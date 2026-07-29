import json
import time
from pathlib import Path

import pandas as pd
import yaml


def _config(tmp_path, now):
    raw = tmp_path / "raw"
    models = tmp_path / "models"
    processed = tmp_path / "processed"
    alerts = tmp_path / "alerts"
    for path in (raw, models, processed, alerts):
        path.mkdir(parents=True, exist_ok=True)

    (models / "system_state.json").write_text(json.dumps({
        "phase": "inference",
        "models_version": 1,
        "notes": "Live",
        "observation_started_at": now - 3600,
    }))

    config = {
        "system": {"mode": "inference", "kafka_bootstrap": "localhost:9092"},
        "prtg": {},
        "devices": [{"ip": "10.0.1.21", "name": "ap-test", "building": "HQ", "sensors": {}}],
        "bootstrap": {
            "min_collection_days": 0,
            "min_netflow_records": 1,
            "training_hour_utc": 2,
            "retrain_interval_days": 7,
            "rolling_training_window_days": 90,
        },
        "paths": {
            "netflow_raw_dir": str(raw),
            "prtg_raw_dir": str(raw),
            "processed_dir": str(processed),
            "models_dir": str(models),
            "alerts_dir": str(alerts),
        },
        "live_runtime": {
            "enabled": True,
            "poll_interval_sec": 10,
            "preview_minutes": 3,
            "telemetry_cache_hours": 8,
            "stale_alert_windows": 2,
        },
        "security": {},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config))
    return config_path, raw, alerts


def test_tail_cache_reads_appends_without_duplicates(tmp_path):
    from utils.telemetry_cache import RotatingCsvTailCache

    path = tmp_path / "netflow_raw_2026-07-16.csv"
    path.write_text("timestamp,src_ip,dst_ip,src_port,dst_port,protocol,tcp_flags,packets,bytes,duration_sec\n")
    cache = RotatingCsvTailCache(
        tmp_path,
        ("netflow_raw_*.csv",),
        ("timestamp", "src_ip", "dst_ip", "src_port", "dst_port", "protocol", "tcp_flags", "packets", "bytes", "duration_sec"),
        ("timestamp", "src_port", "dst_port", "protocol", "tcp_flags", "packets", "bytes", "duration_sec"),
        retention_sec=3600,
        min_sync_interval_sec=0,
    )
    now = time.time()
    with path.open("a") as handle:
        handle.write("{0},10.0.0.1,8.8.8.8,50000,443,6,16,1,100,0.1\n".format(now))
    assert len(cache.get(now - 60)) == 1
    assert len(cache.get(now - 60)) == 1

    with path.open("a") as handle:
        handle.write("{0},10.0.0.1,8.8.4.4,50001,443,6,16,1,200,0.1\n".format(now + 1))
    assert len(cache.get(now - 60)) == 2


def test_file_runtime_persists_completed_window_alert(tmp_path, monkeypatch):
    now = time.time()
    config_path, raw, alerts_dir = _config(tmp_path, now)
    completed = int(now // 60) * 60 - 60
    pd.DataFrame([{
        "timestamp": completed + 5,
        "src_ip": "10.0.1.21",
        "dst_ip": "10.0.3.10",
        "src_port": 40000,
        "dst_port": 22,
        "protocol": 6,
        "tcp_flags": 2,
        "packets": 1,
        "bytes": 60,
        "duration_sec": 0.1,
    }]).to_csv(raw / "netflow_raw_2026-07-16.csv", index=False)

    import utils.config_loader as config_loader
    monkeypatch.setattr(config_loader, "DEFAULT_CONFIG_PATH", config_path)

    import api.live_runtime as live_runtime
    monkeypatch.setattr(live_runtime, "score_window", lambda nf, snmp, models: pd.DataFrame([{
        "detector": "portscan",
        "entity_id": "10.0.1.21",
        "window": completed,
        "anomaly_score": 0.82,
        "profile_used": "global",
        "features": {},
    }]))

    runtime = live_runtime.LiveRuntime()
    monkeypatch.setattr(runtime, "_ensure_models", lambda: object())
    runtime.run_cycle()

    from alerts.alert_store import AlertStore
    alerts = AlertStore(alerts_dir).list_open_alerts()
    assert len(alerts) == 1
    assert alerts[0]["entity_id"] == "10.0.1.21"
    assert alerts[0]["severity"] == "high"


def test_device_with_portscan_alert_is_not_unknown(tmp_path, monkeypatch):
    now = time.time()
    config_path, _, alerts_dir = _config(tmp_path, now)

    import utils.config_loader as config_loader
    monkeypatch.setattr(config_loader, "DEFAULT_CONFIG_PATH", config_path)

    from alerts.alert_store import AlertStore
    store = AlertStore(alerts_dir)
    store.create_alert(
        detector="portscan",
        entity_id="10.0.1.21",
        issue_type="connectivity_security",
        severity="high",
        window=now,
        score=0.82,
    )

    from topology.topology_builder import TopologyBuilder
    node = TopologyBuilder(str(config_path)).device_detail("10.0.1.21")
    assert node["open_issue_count"] == 1
    assert node["status"] == "critical"
