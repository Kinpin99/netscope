import argparse
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from alerts.alert_engine import AlertEngine
from detectors.ensemble_detector import ModelBundle, score_window
from ingestion.sliding_window import SlidingWindowBuffer
from orchestrator.system_state import SystemState
from utils.config_loader import load_config

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [stream-router] %(levelname)s %(message)s")
log = logging.getLogger(__name__)



NETFLOW_COLUMNS = [
    "timestamp", "src_ip", "dst_ip", "src_port", "dst_port",
    "protocol", "tcp_flags", "packets", "bytes", "duration_sec",
]

# PRTG record schema (matches prtg_collector.py's CSV_FIELDS).
PRTG_COLUMNS = [
    "timestamp", "device_ip", "if_in_octets", "if_out_octets",
    "if_speed", "if_in_errors", "cpu_load_pct", "mem_used_pct",
]


def _records_to_df(records: list, columns: list) -> pd.DataFrame:
    if not records:
        return pd.DataFrame(columns=columns)
    df = pd.DataFrame(records)
    for col in columns:
        if col not in df.columns:
            df[col] = 0
    return df[columns]


class StreamRouter:


    def __init__(self, config_path: Optional[str] = None):
        self.cfg = load_config(config_path)
        self.config_path = self.cfg["_config_path"]

        self.netflow_buffer = SlidingWindowBuffer(window_sec=60, grace_period_sec=10)
        self.prtg_buffer = SlidingWindowBuffer(window_sec=60, grace_period_sec=10)

        self.models = ModelBundle(self.cfg["paths"]["models_dir"], self.cfg["paths"]["processed_dir"])
        self.alert_engine = AlertEngine(self.config_path)
        self.system_state = SystemState(self.cfg["paths"]["models_dir"] / "system_state.json")
        self._last_models_version = self.system_state.get().get("models_version", 0)

    
    # Model hot reload
    def _maybe_reload_models(self) -> None:
        current_version = self.system_state.get().get("models_version", 0)
        if current_version != self._last_models_version:
            log.info(
                "Detected models_version change (%d -> %d) - reloading model bundle",
                self._last_models_version, current_version,
            )
            self.models = self.models.reload()
            self._last_models_version = current_version

    
    # Per-window processing (testable without Kafka)
    def process_one_window(self, window: int, netflow_records: list, snmp_records: list) -> pd.DataFrame:

        self._maybe_reload_models()

        netflow_df = _records_to_df(netflow_records, NETFLOW_COLUMNS)
        snmp_df = _records_to_df(snmp_records, PRTG_COLUMNS)

        scores_df = score_window(netflow_df, snmp_df, self.models)
        if scores_df.empty:
            log.debug("Window %d: no scores produced (empty input?)", window)
            return scores_df

        touched = self.alert_engine.process_window(scores_df)
        n_alerts = sum(1 for a in touched if a["status"] == "open")
        n_closed = sum(1 for a in touched if a["status"] == "closed")
        log.info(
            "Window %d: %d flows, %d snmp rows -> %d score rows, %d alerts open/extended, %d closed",
            window, len(netflow_records), len(snmp_records), len(scores_df), n_alerts, n_closed,
        )
        return scores_df

    
    # Buffer intake
    def ingest_netflow(self, record: dict) -> None:
        self.netflow_buffer.add(record)

    def ingest_prtg(self, record: dict) -> None:
        self.prtg_buffer.add(record)

    
    # Tick: flush any ready windows from both buffers
    def tick(self) -> int:

        netflow_ready = dict(self.netflow_buffer.flush_ready())
        prtg_ready = dict(self.prtg_buffer.flush_ready())

        all_windows = sorted(set(netflow_ready) | set(prtg_ready))
        for window in all_windows:
            self.process_one_window(
                window,
                netflow_ready.get(window, []),
                prtg_ready.get(window, []),
            )
        return len(all_windows)



# Kafka consume loop (optional dependency)
def run(config_path: Optional[str] = None, poll_timeout_ms: int = 1000) -> None:
    try:
        from kafka import KafkaConsumer
    except ImportError:
        raise RuntimeError(
            "kafka-python is required to run stream_router's live consume loop. "
        )

    router = StreamRouter(config_path)
    bootstrap = router.cfg["system"]["kafka_bootstrap"]

    consumer = KafkaConsumer(
        "netflow-raw", "prtg-metrics",
        bootstrap_servers=bootstrap,
        value_deserializer=lambda v: __import__("json").loads(v.decode("utf-8")),
        auto_offset_reset="latest",
        enable_auto_commit=True,
        consumer_timeout_ms=poll_timeout_ms,
    )

    log.info("StreamRouter started. Consuming netflow-raw, prtg-metrics from %s", bootstrap)

    try:
        while True:
            for message in consumer:
                if message.topic == "netflow-raw":
                    router.ingest_netflow(message.value)
                elif message.topic == "prtg-metrics":
                    router.ingest_prtg(message.value)

            n = router.tick()
            if n:
                log.info("Processed %d window(s)", n)
    except KeyboardInterrupt:
        log.info("StreamRouter shutting down.")


def main():
    parser = argparse.ArgumentParser(description="Live inference stream router (Phase 3)")
    parser.add_argument("--config", default=None)
    parser.add_argument("--poll-timeout-ms", type=int, default=1000)
    args = parser.parse_args()
    run(args.config, args.poll_timeout_ms)


if __name__ == "__main__":
    main()
