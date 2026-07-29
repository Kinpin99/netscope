"""
stream_router.py
-------------------
The Phase 3 (live inference) main loop. Consumes from the Kafka topics
that netflow_collector.py and prtg_collector.py publish to
(--publish-kafka mode), buckets records into 60-second windows via
SlidingWindowBuffer, and for each completed window:

    netflow_df, snmp_df  --[ensemble_detector.score_window]--> scores_df
                         --[AlertEngine.process_window]--> alerts/health updated

This is the piece that "closes the loop" in the live system: the same
unified_preprocessing feature code, the same trained models, and the same
alert engine used elsewhere, now driven by a continuous Kafka stream
instead of CSVs.

Model reloading:
    ModelBundle is loaded once at startup. Each iteration, this module
    checks data/models/system_state.json's "models_version" - if it has
    increased since the last check (the orchestrator promoted new models
    via a retrain), ModelBundle.reload() is called to pick up the new
    model files. This means stream_router does NOT need to be restarted
    after a retrain.

Kafka is an optional dependency (kafka-python) - if it's not installed,
this module can still be imported (e.g. for testing process_one_window in
isolation) but main()/run() will raise a clear error if invoked.

Usage:
    python ingestion/stream_router.py
    python ingestion/stream_router.py --config path/to/config.yaml
"""

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


# NetFlow record schema (matches collectors.packet_utils.NetFlowRecord.to_csv_row
# and netflow_collector.py's CSV_FIELDS / KafkaFlowPublisher payloads).
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
    """
    Holds the buffers, model bundle, and alert engine for one running
    instance. process_one_window() is the unit of work, separated from the
    Kafka consume loop so it can be tested without Kafka.
    """

    def __init__(self, config_path: Optional[str] = None):
        self.cfg = load_config(config_path)
        self.config_path = self.cfg["_config_path"]

        self.netflow_buffer = SlidingWindowBuffer(window_sec=60, grace_period_sec=10)
        self.prtg_buffer = SlidingWindowBuffer(window_sec=60, grace_period_sec=10)

        self.models = ModelBundle(self.cfg["paths"]["models_dir"], self.cfg["paths"]["processed_dir"])
        self.alert_engine = AlertEngine(self.config_path)
        self.system_state = SystemState(self.cfg["paths"]["models_dir"] / "system_state.json")
        self._last_models_version = self.system_state.get().get("models_version", 0)

    # -----------------------------------------------------------------
    # Model hot-reload
    # -----------------------------------------------------------------
    def _maybe_reload_models(self) -> None:
        current_version = self.system_state.get().get("models_version", 0)
        if current_version != self._last_models_version:
            log.info(
                "Detected models_version change (%d -> %d) - reloading model bundle",
                self._last_models_version, current_version,
            )
            self.models = self.models.reload()
            self._last_models_version = current_version

    # -----------------------------------------------------------------
    # Per-window processing (testable without Kafka)
    # -----------------------------------------------------------------
    def process_one_window(self, window: int, netflow_records: list, snmp_records: list) -> pd.DataFrame:
        """
        Process one completed window's worth of records:
          1. Build netflow_df / snmp_df
          2. score_window via the (possibly just-reloaded) model bundle
          3. AlertEngine.process_window - updates alerts + health scores

        Returns the scores_df for logging/inspection.
        """
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

    # -----------------------------------------------------------------
    # Buffer intake
    # -----------------------------------------------------------------
    def ingest_netflow(self, record: dict) -> None:
        self.netflow_buffer.add(record)

    def ingest_prtg(self, record: dict) -> None:
        self.prtg_buffer.add(record)

    # -----------------------------------------------------------------
    # Tick: flush any ready windows from both buffers
    # -----------------------------------------------------------------
    def tick(self) -> int:
        """
        Flush ready windows from both buffers and process each. NetFlow and
        PRTG windows are matched by window_start; if one stream has a ready
        window the other doesn't yet, the other's records for that window
        (if any arrive later) are effectively dropped for that window - in
        practice both streams should be on the same 60s cadence so this is
        rare, and an empty snmp_df for a window degrades gracefully (see
        unified_preprocessing's "no SNMP data" fallback paths).

        Returns the number of windows processed.
        """
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


# ---------------------------------------------------------------------------
# Kafka consume loop (optional dependency)
# ---------------------------------------------------------------------------
def run(config_path: Optional[str] = None, poll_timeout_ms: int = 1000) -> None:
    try:
        from kafka import KafkaConsumer
    except ImportError:
        raise RuntimeError(
            "kafka-python is required to run stream_router's live consume loop. "
            "Install with: pip install kafka-python --break-system-packages"
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
