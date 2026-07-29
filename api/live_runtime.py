"""Fast, file-backed live inference runtime for the dashboard.

The original dashboard endpoints loaded every telemetry CSV and every ML
model for each browser poll. Apart from being slow, the live-score endpoint
was deliberately read-only, so CSV-only simulations displayed detector
scores without ever creating alerts or device health records.

This singleton runtime fixes both issues:
  * telemetry CSVs are tailed incrementally and retained in memory;
  * model bundles are loaded once and hot-reloaded when models_version changes;
  * completed one-minute windows are processed exactly once by AlertEngine;
  * live preview scores and traffic aggregates are cached for cheap API reads.
"""

import json
import math
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from alerts.alert_engine import AlertEngine
from detectors.ensemble_detector import ModelBundle, score_window
from orchestrator.system_state import SystemState
from preprocessing.unified_preprocessing import _assign_device_ip, _assign_window, _is_private_ip
from utils.config_loader import load_config
from utils.telemetry_cache import RotatingCsvTailCache


NETFLOW_COLUMNS = [
    "timestamp", "src_ip", "dst_ip", "src_port", "dst_port",
    "protocol", "tcp_flags", "packets", "bytes", "duration_sec",
]
NETFLOW_NUMERIC = [
    "timestamp", "src_port", "dst_port", "protocol", "tcp_flags",
    "packets", "bytes", "duration_sec",
]
PRTG_COLUMNS = [
    "timestamp", "device_ip", "if_in_octets", "if_out_octets",
    "if_speed", "if_in_errors", "cpu_load_pct", "mem_used_pct",
]
PRTG_NUMERIC = [
    "timestamp", "if_in_octets", "if_out_octets", "if_speed",
    "if_in_errors", "cpu_load_pct", "mem_used_pct",
]


def _score_records(result: pd.DataFrame) -> List[dict]:
    records = []
    if result is None or result.empty:
        return records
    for _, row in result.iterrows():
        score = row.get("anomaly_score")
        is_nan = score is None or (isinstance(score, float) and math.isnan(score))
        records.append({
            "detector": row.get("detector"),
            "entity_id": str(row.get("entity_id")),
            "window": float(row.get("window", 0)),
            "anomaly_score": None if is_nan else float(score),
            "profile_used": row.get("profile_used", "global"),
        })
    return records


class LiveRuntime:
    def __init__(self):
        self.cfg = load_config()
        runtime_cfg = self.cfg.get("live_runtime", {})
        retention_hours = int(runtime_cfg.get("telemetry_cache_hours", 8))
        self.poll_interval_sec = max(3, int(runtime_cfg.get("poll_interval_sec", 10)))
        self.preview_minutes = max(1, int(runtime_cfg.get("preview_minutes", 3)))
        self.stale_alert_windows = max(1, int(runtime_cfg.get("stale_alert_windows", 2)))
        self.enabled = bool(runtime_cfg.get("enabled", True))

        retention_sec = retention_hours * 3600
        raw_netflow_dir = self.cfg["paths"]["netflow_raw_dir"]
        raw_prtg_dir = self.cfg["paths"]["prtg_raw_dir"]
        self.netflow_cache = RotatingCsvTailCache(
            raw_netflow_dir,
            patterns=("netflow_raw_*.csv", "netflow_raw*.csv"),
            columns=NETFLOW_COLUMNS,
            numeric_columns=NETFLOW_NUMERIC,
            retention_sec=retention_sec,
        )
        self.snmp_cache = RotatingCsvTailCache(
            raw_prtg_dir,
            patterns=("prtg_raw_*.csv", "snmp_raw_*.csv"),
            columns=PRTG_COLUMNS,
            numeric_columns=PRTG_NUMERIC,
            retention_sec=retention_sec,
        )

        self._lock = threading.RLock()
        self._models: Optional[ModelBundle] = None
        self._models_version: Optional[int] = None
        self._alert_engine = AlertEngine(self.cfg["_config_path"])
        self._system_state = SystemState(self.cfg["paths"]["models_dir"] / "system_state.json")
        self._runtime_state_path = self.cfg["paths"]["models_dir"] / "live_runtime_state.json"
        self._last_processed_window = self._load_last_processed_window()
        self._live_scores: List[dict] = []
        self._last_cycle_at: Optional[float] = None
        self._last_error: Optional[str] = None
        self._traffic_cache: Dict[Tuple[int, int, int], dict] = {}

    def _load_last_processed_window(self) -> Optional[int]:
        if not self._runtime_state_path.exists():
            return None
        try:
            with open(self._runtime_state_path) as handle:
                value = json.load(handle).get("last_processed_window")
            return int(value) if value is not None else None
        except Exception:
            return None

    def _save_last_processed_window(self, window: int) -> None:
        tmp = self._runtime_state_path.with_suffix(".tmp")
        with open(tmp, "w") as handle:
            json.dump({"last_processed_window": int(window), "updated_at": time.time()}, handle, indent=2)
        tmp.replace(self._runtime_state_path)

    def _ensure_models(self) -> ModelBundle:
        state = self._system_state.get()
        version = int(state.get("models_version", 0))
        if self._models is None or self._models_version != version:
            self._models = ModelBundle(self.cfg["paths"]["models_dir"], self.cfg["paths"]["processed_dir"])
            self._models_version = version
        return self._models

    @staticmethod
    def _window_slice(frame: pd.DataFrame, window: int) -> pd.DataFrame:
        if frame.empty:
            return frame
        windows = (frame["timestamp"] // 60).astype(int) * 60
        return frame[windows == window]

    def run_cycle(self) -> None:
        """Tail new telemetry, persist completed windows, and refresh preview scores."""
        if not self.enabled:
            return
        if not self._lock.acquire(blocking=False):
            return
        try:
            now = time.time()
            models = self._ensure_models()
            preview_cutoff = now - max(self.preview_minutes + 2, 5) * 60
            nf = self.netflow_cache.get(preview_cutoff)
            snmp = self.snmp_cache.get(preview_cutoff)

            if nf.empty:
                self._live_scores = []
                self._last_cycle_at = now
                self._last_error = None
                return

            current_window = int(now // 60) * 60
            raw_windows = sorted(set(((nf["timestamp"] // 60).astype(int) * 60).tolist()))
            completed = [window for window in raw_windows if window < current_window]

            if completed:
                if self._last_processed_window is None:
                    # First startup should not replay hours of old data, but a
                    # small backfill catches a scan that finished just before API startup.
                    pending = completed[-5:]
                else:
                    pending = [window for window in completed if window > self._last_processed_window]

                for window in pending:
                    nf_window = self._window_slice(nf, window)
                    snmp_window = self._window_slice(snmp, window)
                    result = score_window(nf_window, snmp_window, models)
                    if not result.empty:
                        self._alert_engine.process_window(result)
                    self._alert_engine.close_stale_alerts(
                        current_window=window,
                        stale_after_windows=self.stale_alert_windows,
                    )
                    self._last_processed_window = int(window)
                    self._save_last_processed_window(int(window))

            # Preview includes the current incomplete minute. It is read-only;
            # alert persistence above only uses completed windows exactly once.
            preview_cutoff = now - self.preview_minutes * 60
            preview_nf = nf[nf["timestamp"] >= preview_cutoff]
            preview_snmp = snmp[snmp["timestamp"] >= preview_cutoff] if not snmp.empty else snmp
            preview_result = score_window(preview_nf, preview_snmp, models)
            self._live_scores = _score_records(preview_result)
            self._last_cycle_at = now
            self._last_error = None
        except Exception as exc:
            self._last_error = str(exc)
        finally:
            self._lock.release()

    def refresh_scores_if_data_changed(self) -> None:
        """Synchronously refresh only when new NetFlow bytes have arrived.

        The background worker normally keeps scores current. This small guard
        covers the startup/test race where data is written immediately before
        the first live-score request.
        """
        before = self.netflow_cache.version
        self.netflow_cache.sync(force=True)
        if self._last_cycle_at is None or self.netflow_cache.version != before:
            self.run_cycle()

    def get_live_scores(self, minutes: int = 3) -> dict:
        cutoff = time.time() - max(1, minutes) * 60
        with self._lock:
            scores = [row for row in self._live_scores if row.get("window", 0) >= cutoff - 60]
            return {
                "scores": scores,
                "updated_at": self._last_cycle_at,
                "error": self._last_error,
                "last_processed_window": self._last_processed_window,
            }

    def recent_device_traffic(self, device_ip: str, minutes: int) -> dict:
        cutoff = time.time() - minutes * 60
        nf = self.netflow_cache.get(cutoff)
        if nf.empty:
            return {"window_sec": 60, "device_ip": device_ip, "series": []}

        related = nf[(nf["src_ip"] == device_ip) | (nf["dst_ip"] == device_ip)].copy()
        if related.empty:
            return {"window_sec": 60, "device_ip": device_ip, "series": []}

        related = _assign_window(related, 60)
        inbound = related[related["dst_ip"] == device_ip].groupby("window").agg(
            bytes_in=("bytes", "sum"), packets_in=("packets", "sum")
        )
        outbound = related[related["src_ip"] == device_ip].groupby("window").agg(
            bytes_out=("bytes", "sum"), packets_out=("packets", "sum")
        )
        combined = inbound.join(outbound, how="outer").fillna(0).reset_index().sort_values("window")
        series = [
            {
                "window": float(row["window"]),
                "bytes_in": float(row["bytes_in"]),
                "bytes_out": float(row["bytes_out"]),
                "packets_in": float(row["packets_in"]),
                "packets_out": float(row["packets_out"]),
            }
            for _, row in combined.iterrows()
        ]
        return {"window_sec": 60, "device_ip": device_ip, "series": series}

    def recent_traffic(self, minutes: int, max_devices: int) -> dict:
        cutoff = time.time() - minutes * 60
        nf = self.netflow_cache.get(cutoff)
        version = self.netflow_cache.version
        key = (int(minutes), int(max_devices), int(version))
        with self._lock:
            cached = self._traffic_cache.get(key)
            if cached is not None:
                return cached

        if nf.empty:
            response = {"window_sec": 60, "devices": {}}
            with self._lock:
                self._traffic_cache = {key: response}
            return response

        nf = _assign_window(nf, 60)
        nf = _assign_device_ip(nf)
        nf["is_inbound"] = nf["dst_ip"].apply(_is_private_ip)

        in_flows = nf[nf["is_inbound"]]
        out_flows = nf[~nf["is_inbound"]]
        in_agg = in_flows.groupby(["device_ip", "window"]).agg(
            bytes_in=("bytes", "sum"), packets_in=("packets", "sum")
        )
        out_agg = out_flows.groupby(["device_ip", "window"]).agg(
            bytes_out=("bytes", "sum"), packets_out=("packets", "sum")
        )
        combined = in_agg.join(out_agg, how="outer").fillna(0).reset_index()

        network_group = combined.groupby("window", as_index=False).agg(
            bytes_in=("bytes_in", "sum"),
            bytes_out=("bytes_out", "sum"),
            packets_in=("packets_in", "sum"),
            packets_out=("packets_out", "sum"),
        ).sort_values("window")
        network = [
            {
                "window": float(row["window"]),
                "bytes_in": float(row["bytes_in"]),
                "bytes_out": float(row["bytes_out"]),
                "packets_in": float(row["packets_in"]),
                "packets_out": float(row["packets_out"]),
            }
            for _, row in network_group.iterrows()
        ]

        totals = combined.assign(total_bytes=combined["bytes_in"] + combined["bytes_out"]).groupby("device_ip")["total_bytes"].sum()
        top_device_ips = set(totals.nlargest(max_devices).index.tolist())
        devices: Dict[str, List[dict]] = {}
        for device_ip, group in combined[combined["device_ip"].isin(top_device_ips)].groupby("device_ip"):
            devices[str(device_ip)] = [
                {
                    "window": float(row["window"]),
                    "bytes_in": float(row["bytes_in"]),
                    "bytes_out": float(row["bytes_out"]),
                    "packets_in": float(row["packets_in"]),
                    "packets_out": float(row["packets_out"]),
                }
                for _, row in group.sort_values("window").iterrows()
            ]

        response = {
            "window_sec": 60,
            "network": network,
            "device_count": int(combined["device_ip"].nunique()),
            "devices": devices,
        }
        with self._lock:
            # Keep only responses for the latest data version.
            self._traffic_cache = {k: v for k, v in self._traffic_cache.items() if k[2] == version}
            self._traffic_cache[key] = response
        return response


_RUNTIME: Optional[LiveRuntime] = None
_RUNTIME_LOCK = threading.Lock()


def get_live_runtime() -> LiveRuntime:
    global _RUNTIME
    current_config_path = load_config().get("_config_path")
    if _RUNTIME is None or _RUNTIME.cfg.get("_config_path") != current_config_path:
        with _RUNTIME_LOCK:
            if _RUNTIME is None or _RUNTIME.cfg.get("_config_path") != current_config_path:
                _RUNTIME = LiveRuntime()
    return _RUNTIME
