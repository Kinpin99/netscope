"""
prtg_collector.py
------------------
Pulls interface traffic, CPU, memory, and error-rate data from PRTG's REST
API and writes it in the exact schema that unified_preprocessing.py's
_load_snmp() expects:

    timestamp, device_ip, if_in_octets, if_out_octets, if_speed,
    if_in_errors, cpu_load_pct, mem_used_pct

This is the contract that lets _load_snmp() (and therefore
BandwidthFeatures / DeviceBehaviorFeatures) stay completely unaware of
whether the data came from raw SNMP polling or PRTG - prtg_collector.py
is the only place that translates between PRTG's world and ours.

Output:
    data/raw/prtg_raw_<YYYY-MM-DD>.csv  (daily-rotated, same convention as
    netflow_collector.py's RotatingCsvWriter)

PRTG sensor model:
    Each device in config.yaml lists PRTG sensor IDs for:
      traffic_in, traffic_out  - interface octet counters (bps channels)
      if_speed_bps             - nominal interface speed (static, from config
                                  - PRTG traffic sensors don't reliably expose
                                  this as a queryable channel)
      if_errors                - error/discard counter sensor
      cpu, memory               - utilization % sensors

    Any sensor a device doesn't have is simply omitted from config.yaml;
    the corresponding output column is filled with 0 for that device.

Two modes:
    poll      - continuous loop, polling every `poll_interval_sec` for the
                most recent data (Phase 1/2/3 of the orchestrator lifecycle)
    backfill  - one-shot pull of a historical date range, useful for
                bootstrapping observation data faster than waiting in real
                time if PRTG already has weeks of history for these sensors

Usage
-----
# Live polling (blocks until Ctrl-C)
python prtg_collector.py --mode poll

# Backfill the last 14 days in one shot
python prtg_collector.py --mode backfill --days 14
"""

import argparse
import csv
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils.config_loader import load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [prtg] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

CSV_FIELDS = [
    "timestamp", "device_ip",
    "if_in_octets", "if_out_octets", "if_speed",
    "if_in_errors", "cpu_load_pct", "mem_used_pct",
]

# PRTG historicdata.json date format
PRTG_DATE_FMT = "%Y-%m-%d-%H-%M-%S"


# ---------------------------------------------------------------------------
# Output writer - daily-rotated CSV, same convention as netflow_collector.py
# ---------------------------------------------------------------------------
class RotatingCsvWriter:
    """Append-safe, daily-rotated CSV writer (mirrors netflow_collector.py)."""

    def __init__(self, output_dir: Path, prefix: str = "prtg_raw"):
        self.output_dir = output_dir
        self.prefix = prefix
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._current_date = None
        self._current_path = None
        self._write_header = False

    def _path_for(self, dt: datetime) -> Path:
        return self.output_dir / f"{self.prefix}_{dt.strftime('%Y-%m-%d')}.csv"

    def _ensure_current_file(self, ts: float) -> Path:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        date_key = dt.date()
        if date_key != self._current_date:
            self._current_date = date_key
            self._current_path = self._path_for(dt)
            self._write_header = (
                not self._current_path.exists()
                or self._current_path.stat().st_size == 0
            )
        return self._current_path

    def write_rows(self, rows: List[dict]) -> None:
        if not rows:
            return
        # Group by day in case a backfill batch spans multiple days
        by_day: Dict[str, List[dict]] = {}
        for row in rows:
            dt = datetime.fromtimestamp(row["timestamp"], tz=timezone.utc)
            by_day.setdefault(dt.strftime("%Y-%m-%d"), []).append(row)

        for _, day_rows in by_day.items():
            path = self._ensure_current_file(day_rows[0]["timestamp"])
            with open(path, "a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
                if self._write_header:
                    writer.writeheader()
                    self._write_header = False
                for row in day_rows:
                    writer.writerow(row)


# ---------------------------------------------------------------------------
# PRTG API client
# ---------------------------------------------------------------------------
class PrtgClient:
    """
    Thin wrapper around PRTG's historicdata.json endpoint.

    PRTG returns one JSON object per averaging interval, with each sensor
    channel as a key (e.g. "Traffic In (speed)", "CPU Load"). The exact
    channel names depend on the sensor type and PRTG's localization, so
    we read channel values positionally where possible and fall back to
    summing/parsing the "value_raw" fields PRTG provides alongside the
    formatted strings.
    """

    def __init__(self, base_url: str, api_token: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.timeout = timeout
        self.session = requests.Session()

    def historic_data(
        self,
        sensor_id: int,
        start: datetime,
        end: datetime,
        avg_interval_sec: int = 60,
    ) -> List[dict]:
        """
        Fetch historic data for a sensor between start/end (UTC datetimes).
        Returns a list of {"datetime": ..., "value_raw": <dict of channel -> float>}.
        """
        url = f"{self.base_url}/api/historicdata.json"
        params = {
            "id": sensor_id,
            "sdate": start.strftime(PRTG_DATE_FMT),
            "edate": end.strftime(PRTG_DATE_FMT),
            "avg": avg_interval_sec,
            "apitoken": self.api_token,
        }
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            log.error("PRTG request failed for sensor %s: %s", sensor_id, exc)
            return []

        data = resp.json()
        histdata = data.get("histdata", [])
        return histdata


def _to_epoch(prtg_datetime_str: str) -> Optional[float]:
    """
    PRTG returns datetimes like "06/13/2026 14:00:00" (locale-dependent) or
    as an ISO-ish string depending on PRTG version/config. We try a couple
    of common formats and fall back to None (row skipped) if none match -
    a parsing failure here should never crash the whole poll cycle.
    """
    for fmt in ("%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(prtg_datetime_str, fmt).replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            continue
    log.warning("Could not parse PRTG datetime: %r", prtg_datetime_str)
    return None


def _extract_channel_value(point: dict, channel_keys: List[str]) -> Optional[float]:
    """
    A single historicdata point looks roughly like:
        {
          "datetime": "06/13/2026 14:00:00",
          "Traffic In": "1.2 Mbit/s",
          "Traffic In_raw": 1234567.0,
          "Traffic Out": "...",
          "Traffic Out_raw": ...,
          ...
        }

    channel_keys is a list of candidate "<Name>_raw" keys to try, in order
    of preference, since PRTG's exact channel naming varies by sensor type
    and PRTG version/localization. Returns the first match found, or None.
    """
    for key in channel_keys:
        raw_key = f"{key}_raw"
        if raw_key in point:
            try:
                return float(point[raw_key])
            except (TypeError, ValueError):
                continue
    return None


# Candidate PRTG channel name prefixes per metric, most common first.
# These cover the standard "SNMP Traffic" and "SNMP CPU Load"/"SNMP Memory"
# sensor types; if a site uses custom sensor names, add them here.
CHANNEL_CANDIDATES = {
    "if_in_octets":  ["Traffic In", "Traffic In (Volume)", "In", "ifInOctets"],
    "if_out_octets": ["Traffic Out", "Traffic Out (Volume)", "Out", "ifOutOctets"],
    "if_in_errors":  ["Errors In", "Error Total", "Discards In", "ifInErrors"],
    "cpu_load_pct":  ["Total", "CPU Load", "Average CPU"],
    "mem_used_pct":  ["Memory Usage", "Used", "Percent Used"],
}


# ---------------------------------------------------------------------------
# Per-device polling
# ---------------------------------------------------------------------------
def poll_device(
    client: PrtgClient,
    device: dict,
    start: datetime,
    end: datetime,
    avg_interval_sec: int,
) -> List[dict]:
    """
    Fetch all configured sensors for one device over [start, end), merge
    them by timestamp, and return a list of rows matching CSV_FIELDS.
    """
    sensors = device.get("sensors", {})
    device_ip = device["ip"]
    if_speed = float(sensors.get("if_speed_bps", 0))

    # timestamp -> partial row
    merged: Dict[float, dict] = {}

    metric_sensor_map = {
        "if_in_octets":  sensors.get("traffic_in"),
        "if_out_octets": sensors.get("traffic_out"),
        "if_in_errors":  sensors.get("if_errors"),
        "cpu_load_pct":  sensors.get("cpu"),
        "mem_used_pct":  sensors.get("memory"),
    }

    for metric, sensor_id in metric_sensor_map.items():
        if sensor_id is None:
            continue  # device doesn't have this sensor; column stays 0

        histdata = client.historic_data(sensor_id, start, end, avg_interval_sec)
        for point in histdata:
            ts = _to_epoch(point.get("datetime", ""))
            if ts is None:
                continue
            value = _extract_channel_value(point, CHANNEL_CANDIDATES[metric])
            if value is None:
                continue

            row = merged.setdefault(ts, {
                "timestamp": ts,
                "device_ip": device_ip,
                "if_in_octets": 0.0,
                "if_out_octets": 0.0,
                "if_speed": if_speed,
                "if_in_errors": 0.0,
                "cpu_load_pct": 0.0,
                "mem_used_pct": 0.0,
            })
            row[metric] = value

    return list(merged.values())


# ---------------------------------------------------------------------------
# Poll mode (continuous)
# ---------------------------------------------------------------------------
def run_poll(cfg: dict, output_dir: Path) -> None:
    prtg_cfg = cfg["prtg"]
    client = PrtgClient(prtg_cfg["base_url"], prtg_cfg["api_token"])
    devices = cfg["devices"]
    interval = prtg_cfg.get("poll_interval_sec", 60)
    avg_interval = prtg_cfg.get("avg_interval_sec", 60)
    lag = prtg_cfg.get("poll_lag_sec", 30)

    writer = RotatingCsvWriter(output_dir)
    log.info(
        "Starting PRTG poll loop: %d devices, every %ds -> %s",
        len(devices), interval, output_dir,
    )

    total_rows = 0
    try:
        while True:
            now = datetime.now(timezone.utc)
            end = now - timedelta(seconds=lag)
            start = end - timedelta(seconds=interval)

            all_rows: List[dict] = []
            for device in devices:
                try:
                    rows = poll_device(client, device, start, end, avg_interval)
                    all_rows.extend(rows)
                except Exception:
                    log.exception("Failed polling device %s (%s)", device.get("name"), device.get("ip"))

            if all_rows:
                writer.write_rows(all_rows)
                total_rows += len(all_rows)
                log.info("Wrote %d rows this cycle (total=%d)", len(all_rows), total_rows)
            else:
                log.warning("No data returned this poll cycle")

            time.sleep(interval)

    except KeyboardInterrupt:
        log.info("Shutting down. Total rows collected: %d", total_rows)


# ---------------------------------------------------------------------------
# Backfill mode (one-shot historical pull)
# ---------------------------------------------------------------------------
def run_backfill(cfg: dict, output_dir: Path, days: int, chunk_hours: int = 24) -> None:
    """
    Pull `days` worth of history for every device, in chunk_hours-sized
    requests (PRTG limits how much historic data can be requested per call
    for finely-averaged data, so we chunk by day by default).

    This is the fast path for bootstrapping observation data: if PRTG
    already has 30 days of 60s-averaged data sitting in its database, you
    don't need to wait 14 real-time days for the orchestrator's observation
    phase to gather enough data - backfill it in one run.
    """
    prtg_cfg = cfg["prtg"]
    client = PrtgClient(prtg_cfg["base_url"], prtg_cfg["api_token"])
    devices = cfg["devices"]
    avg_interval = prtg_cfg.get("avg_interval_sec", 60)

    writer = RotatingCsvWriter(output_dir)
    now = datetime.now(timezone.utc)
    overall_start = now - timedelta(days=days)

    log.info(
        "Backfilling %d days (%s -> %s) for %d devices -> %s",
        days, overall_start.isoformat(), now.isoformat(), len(devices), output_dir,
    )

    total_rows = 0
    chunk_start = overall_start
    while chunk_start < now:
        chunk_end = min(chunk_start + timedelta(hours=chunk_hours), now)
        log.info("Chunk: %s -> %s", chunk_start.isoformat(), chunk_end.isoformat())

        all_rows: List[dict] = []
        for device in devices:
            try:
                rows = poll_device(client, device, chunk_start, chunk_end, avg_interval)
                all_rows.extend(rows)
            except Exception:
                log.exception("Failed backfilling device %s (%s)", device.get("name"), device.get("ip"))

        if all_rows:
            writer.write_rows(all_rows)
            total_rows += len(all_rows)
            log.info("  wrote %d rows (total=%d)", len(all_rows), total_rows)

        chunk_start = chunk_end

    log.info("Backfill complete. Total rows: %d", total_rows)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="PRTG collector (poll or backfill)")
    parser.add_argument("--mode", choices=["poll", "backfill"], required=True)
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument("--days", type=int, default=14, help="Days to backfill (backfill mode)")
    parser.add_argument("--chunk-hours", type=int, default=24, help="Backfill chunk size in hours")
    args = parser.parse_args()

    cfg = load_config(args.config)
    output_dir = cfg["paths"]["prtg_raw_dir"]

    if not cfg["prtg"].get("api_token"):
        log.error(
            "No PRTG API token configured. Set PRTG_API_TOKEN env var or "
            "config.yaml's prtg.api_token."
        )
        sys.exit(1)

    if args.mode == "poll":
        run_poll(cfg, output_dir)
    else:
        run_backfill(cfg, output_dir, args.days, args.chunk_hours)


if __name__ == "__main__":
    main()
