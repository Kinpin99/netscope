"""
test_stream_router.py
------------------------
Covers:
  - sliding_window.SlidingWindowBuffer: window bucketing, flush-on-later-
    window, flush-on-grace-period, ordering
  - stream_router.StreamRouter:
      - process_one_window with real trained models (full pipeline:
        score_window -> AlertEngine.process_window)
      - process_one_window with empty input (regression for the
        empty-DataFrame groupby bug)
      - tick() flushes ready windows from both buffers and processes them
      - model hot-reload when models_version changes in system_state.json
"""

import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pytest
import yaml

from ingestion.sliding_window import SlidingWindowBuffer


# ---------------------------------------------------------------------------
# SlidingWindowBuffer
# ---------------------------------------------------------------------------
class TestSlidingWindowBuffer:
    def test_bucketing(self):
        buf = SlidingWindowBuffer(window_sec=60, grace_period_sec=100)
        buf.add({"timestamp": 5})
        buf.add({"timestamp": 59})
        buf.add({"timestamp": 60})
        buf.add({"timestamp": 119})

        assert buf.pending_window_count() == 2

    def test_no_flush_without_later_window_or_grace(self):
        buf = SlidingWindowBuffer(window_sec=60, grace_period_sec=100)
        buf.add({"timestamp": 5})
        assert buf.flush_ready() == []

    def test_flush_on_later_window(self):
        buf = SlidingWindowBuffer(window_sec=60, grace_period_sec=100)
        buf.add({"timestamp": 5, "v": "a"})
        buf.add({"timestamp": 65, "v": "b"})

        ready = buf.flush_ready()
        assert len(ready) == 1
        window, records = ready[0]
        assert window == 0
        assert records == [{"timestamp": 5, "v": "a"}]
        assert buf.pending_window_count() == 1

    def test_flush_on_grace_period(self):
        buf = SlidingWindowBuffer(window_sec=60, grace_period_sec=0.2)
        buf.add({"timestamp": 5})
        assert buf.flush_ready() == []

        time.sleep(0.3)
        ready = buf.flush_ready()
        assert len(ready) == 1
        assert ready[0][0] == 0

    def test_flush_returns_ascending_order(self):
        buf = SlidingWindowBuffer(window_sec=60, grace_period_sec=100)
        buf.add({"timestamp": 125})  # window 120
        buf.add({"timestamp": 5})    # window 0
        buf.add({"timestamp": 65})   # window 60
        buf.add({"timestamp": 185})  # window 180 - makes 0,60,120 all "older"

        ready = buf.flush_ready()
        windows = [w for w, _ in ready]
        assert windows == [0, 60, 120]

    def test_missing_timestamp_dropped(self):
        buf = SlidingWindowBuffer(window_sec=60, grace_period_sec=100)
        buf.add({"no_timestamp": True})
        assert buf.pending_window_count() == 0

    def test_emptied_after_flush(self):
        buf = SlidingWindowBuffer(window_sec=60, grace_period_sec=100)
        buf.add({"timestamp": 5})
        buf.add({"timestamp": 65})
        buf.flush_ready()
        assert buf.flush_ready() == []  # window 0 already flushed, nothing new ready


# ---------------------------------------------------------------------------
# StreamRouter integration
# ---------------------------------------------------------------------------
@pytest.fixture
def router_project(tmp_path):
    """
    Self-contained project: synthetic netflow/prtg CSVs, trained models,
    config.yaml. Returns (StreamRouter, project_dir, config_path, base_ts).
    """
    from orchestrator.orchestrator import SystemOrchestrator
    from ingestion.stream_router import StreamRouter

    project_dir = tmp_path / "project"
    for sub in ["data/raw", "data/processed", "data/models", "data/alerts"]:
        (project_dir / sub).mkdir(parents=True)

    random.seed(99)
    base_ts = 1718000000
    rows = []
    for window in range(15):
        ts = base_ts + window * 60
        for dev in ["10.0.0.5", "10.0.0.6"]:
            for _ in range(15):
                rows.append({
                    "timestamp": ts + random.randint(0, 59),
                    "src_ip": dev, "dst_ip": "8.8.8.8",
                    "src_port": random.randint(1024, 65535), "dst_port": 443,
                    "protocol": 6, "tcp_flags": 0x10,
                    "packets": random.randint(1, 20), "bytes": random.randint(100, 1500),
                    "duration_sec": random.uniform(0.1, 2),
                })
            for _ in range(5):
                rows.append({
                    "timestamp": ts + random.randint(0, 59),
                    "src_ip": "8.8.8.8", "dst_ip": dev,
                    "src_port": 443, "dst_port": random.randint(1024, 65535),
                    "protocol": 6, "tcp_flags": 0x10,
                    "packets": random.randint(1, 20), "bytes": random.randint(100, 1500),
                    "duration_sec": random.uniform(0.1, 2),
                })
    nf_df = pd.DataFrame(rows)
    nf_df.to_csv(project_dir / "data" / "raw" / "netflow_raw_2026-06-13.csv", index=False)

    prtg_rows = []
    for window in range(15):
        ts = base_ts + window * 60
        for dev in ["10.0.0.5", "10.0.0.6"]:
            prtg_rows.append({
                "timestamp": ts, "device_ip": dev,
                "if_in_octets": random.randint(10000, 50000),
                "if_out_octets": random.randint(10000, 50000),
                "if_speed": 1_000_000_000,
                "if_in_errors": 0,
                "cpu_load_pct": random.uniform(10, 30),
                "mem_used_pct": random.uniform(20, 50),
            })
    prtg_df = pd.DataFrame(prtg_rows)
    prtg_df.to_csv(project_dir / "data" / "raw" / "prtg_raw_2026-06-13.csv", index=False)

    config = {
        "system": {"mode": "inference", "kafka_bootstrap": "localhost:9092"},
        "prtg": {"base_url": "https://prtg.local", "api_token": "test", "poll_interval_sec": 60,
                 "avg_interval_sec": 60, "poll_lag_sec": 30},
        "devices": [
            {"ip": "10.0.0.5", "name": "core-router-01", "building": "HQ", "sensors": {}},
            {"ip": "10.0.0.6", "name": "edge-switch-floor2", "building": "HQ", "sensors": {}},
        ],
        "bootstrap": {
            "min_collection_days": 0, "min_netflow_records": len(nf_df),
            "training_hour_utc": 2, "retrain_interval_days": 7,
            "rolling_training_window_days": 90,
        },
        "paths": {
            "netflow_raw_dir": str(project_dir / "data" / "raw"),
            "prtg_raw_dir": str(project_dir / "data" / "raw"),
            "processed_dir": str(project_dir / "data" / "processed"),
            "models_dir": str(project_dir / "data" / "models"),
            "alerts_dir": str(project_dir / "data" / "alerts"),
        },
    }
    config_path = project_dir / "config.yaml"
    with open(config_path, "w") as f:
        yaml.safe_dump(config, f)

    orch = SystemOrchestrator(str(config_path))
    assert orch.trigger_training_now() is True

    router = StreamRouter(str(config_path))
    return router, project_dir, config_path, base_ts, nf_df, prtg_df


class TestProcessOneWindow:
    def test_empty_window_returns_empty_df(self, router_project):
        router, *_ = router_project
        result = router.process_one_window(1000, [], [])
        assert result.empty

    def test_real_window_produces_scores_and_alerts(self, router_project):
        router, project_dir, config_path, base_ts, nf_df, prtg_df = router_project

        window_start = base_ts
        wnf = nf_df[(nf_df["timestamp"] >= window_start) & (nf_df["timestamp"] < window_start + 60)]
        wsnmp = prtg_df[(prtg_df["timestamp"] >= window_start) & (prtg_df["timestamp"] < window_start + 60)]

        result = router.process_one_window(window_start, wnf.to_dict("records"), wsnmp.to_dict("records"))
        assert not result.empty
        assert set(result["detector"]) == {"bandwidth", "portscan", "device_behavior", "protocol"}

        # Scores should be valid numbers (models exist) or NaN, never crash
        scores = result["anomaly_score"].dropna()
        assert (scores >= 0).all() and (scores <= 1).all()

    def test_netflow_only_window_no_snmp(self, router_project):
        """A window with flows but no PRTG data for that minute should
        still process without error (degrades to if_util_*=0 etc.)."""
        router, project_dir, config_path, base_ts, nf_df, prtg_df = router_project

        window_start = base_ts
        wnf = nf_df[(nf_df["timestamp"] >= window_start) & (nf_df["timestamp"] < window_start + 60)]

        result = router.process_one_window(window_start, wnf.to_dict("records"), [])
        assert not result.empty


class TestTick:
    def test_tick_processes_ready_windows(self, router_project):
        router, project_dir, config_path, base_ts, nf_df, prtg_df = router_project

        # Feed two windows' worth of records plus one record from a third
        # window to trigger flushing of the first two.
        for w in range(2):
            window_start = base_ts + w * 60
            wnf = nf_df[(nf_df["timestamp"] >= window_start) & (nf_df["timestamp"] < window_start + 60)]
            wsnmp = prtg_df[(prtg_df["timestamp"] >= window_start) & (prtg_df["timestamp"] < window_start + 60)]
            for rec in wnf.to_dict("records"):
                router.ingest_netflow(rec)
            for rec in wsnmp.to_dict("records"):
                router.ingest_prtg(rec)

        # trigger window
        router.ingest_netflow({
            "timestamp": base_ts + 3 * 60, "src_ip": "0.0.0.0", "dst_ip": "0.0.0.0",
            "src_port": 0, "dst_port": 0, "protocol": 0, "tcp_flags": 0,
            "packets": 0, "bytes": 0, "duration_sec": 0,
        })

        n = router.tick()
        # Timestamp jitter (random.randint(0, 59)) near window boundaries can
        # place some of "window 0"'s records into the preceding bucket, so
        # 3 windows (not exactly 2) become ready once the trigger record
        # (window 3) marks everything before window 3 as "older". The
        # important invariant is that the buffer drains down to just the
        # trigger's own window.
        assert n >= 2
        assert router.netflow_buffer.pending_window_count() == 1

    def test_tick_with_nothing_buffered(self, router_project):
        router, *_ = router_project
        assert router.tick() == 0


class TestModelHotReload:
    def test_reload_triggered_by_version_change(self, router_project):
        router, project_dir, config_path, *_ = router_project

        from orchestrator.orchestrator import SystemOrchestrator
        initial_version = router._last_models_version
        assert initial_version == 1

        old_bundle = router.models.models["bandwidth"]

        orch = SystemOrchestrator(str(config_path))
        assert orch.trigger_training_now() is True

        router.process_one_window(999999, [], [])  # empty window still triggers the version check

        assert router._last_models_version == 2
        assert router.models.models["bandwidth"] is not old_bundle

    def test_no_reload_when_version_unchanged(self, router_project):
        router, *_ = router_project
        bundle_before = router.models

        router.process_one_window(999999, [], [])

        assert router.models is bundle_before  # same ModelBundle instance - no reload happened


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
