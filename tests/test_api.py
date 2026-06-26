"""
test_api.py
-------------
Covers all API routers using FastAPI's TestClient against a self-contained
fixture project (synthetic netflow/prtg data, trained models, config.yaml,
populated alerts/health scores) - similar fixture pattern to
test_orchestrator.py and test_ensemble_detector.py.

Routers covered:
  - /system  : status, retrain trigger (conflict on concurrent training)
  - /devices : detail, baseline create/delete, 404s for unknown devices
  - /alerts  : open, list with filters, distribution, health-scores
  - /topology: buildings, devices
  - /traffic : recent, live-scores (incl. empty-data graceful handling)

Each test monkeypatches utils.config_loader.DEFAULT_CONFIG_PATH so route
modules (which construct AlertEngine()/SystemOrchestrator()/etc. with no
config_path, relying on load_config()'s default) pick up the fixture's
config.yaml.
"""

import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pytest
import yaml
from fastapi.testclient import TestClient


@pytest.fixture
def api_project(tmp_path, monkeypatch):
    """
    Build a full fixture project: synthetic netflow/prtg data, trained
    models, config.yaml, and pre-populate alerts + health scores by running
    the real pipeline. Returns (TestClient, project_dir, config_path).
    """
    project_dir = tmp_path / "project"
    for sub in ["data/raw", "data/processed", "data/models", "data/alerts"]:
        (project_dir / sub).mkdir(parents=True)

    # --- synthetic netflow data (with inbound traffic for variety) ---
    random.seed(123)
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

    # --- synthetic prtg data ---
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

    n_records = len(nf_df)

    config = {
        "system": {"mode": "observation", "kafka_bootstrap": "localhost:9092"},
        "prtg": {"base_url": "https://prtg.local", "api_token": "test", "poll_interval_sec": 60,
                 "avg_interval_sec": 60, "poll_lag_sec": 30},
        "devices": [
            {"ip": "10.0.0.5", "name": "core-router-01", "building": "HQ", "sensors": {}},
            {"ip": "10.0.0.6", "name": "edge-switch-floor2", "building": "HQ", "sensors": {}},
            {"ip": "10.0.0.7", "name": "branch-router-01", "building": "Branch-A", "sensors": {}},
        ],
        "bootstrap": {
            "min_collection_days": 0,
            "min_netflow_records": n_records,
            "training_hour_utc": 2,
            "retrain_interval_days": 7,
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

    # Monkeypatch the default config path so route modules' load_config()
    # (called with no args) resolves to this fixture's config.
    import utils.config_loader as config_loader
    monkeypatch.setattr(config_loader, "DEFAULT_CONFIG_PATH", config_path)

    # --- train models via the real orchestrator ---
    from orchestrator.orchestrator import SystemOrchestrator
    orch = SystemOrchestrator(str(config_path))
    assert orch.trigger_training_now() is True

    # --- populate alerts + health scores via the real pipeline ---
    from detectors.ensemble_detector import ModelBundle, score_window
    from alerts.alert_engine import AlertEngine

    models = ModelBundle(Path(config["paths"]["models_dir"]), Path(config["paths"]["processed_dir"]))
    engine = AlertEngine(str(config_path))

    for w in range(15):
        window_start = base_ts + w * 60
        wnf = nf_df[(nf_df["timestamp"] >= window_start) & (nf_df["timestamp"] < window_start + 60)]
        wsnmp = prtg_df[(prtg_df["timestamp"] >= window_start) & (prtg_df["timestamp"] < window_start + 60)]
        if wnf.empty:
            continue
        scores = score_window(wnf, wsnmp, models)
        engine.process_window(scores)

    from api.main import app
    return TestClient(app), project_dir, config_path


# ---------------------------------------------------------------------------
# /system
# ---------------------------------------------------------------------------
class TestSystemRoutes:
    def test_status(self, api_project):
        client, _, _ = api_project
        r = client.get("/system/status")
        assert r.status_code == 200
        body = r.json()
        assert body["phase"] == "inference"
        assert body["models_version"] == 1
        assert "observation" in body

    def test_retrain_conflict_when_training(self, api_project):
        client, _, config_path = api_project

        from orchestrator.system_state import PHASE_TRAINING
        from orchestrator.orchestrator import SystemOrchestrator
        orch = SystemOrchestrator(str(config_path))
        orch.state.set_phase(PHASE_TRAINING)

        r = client.post("/system/retrain")
        assert r.status_code == 409

    def test_retrain_runs_when_not_training(self, api_project):
        client, _, _ = api_project
        r = client.post("/system/retrain")
        assert r.status_code == 200
        body = r.json()
        assert body["phase"] == "inference"
        assert body["models_version"] == 2  # incremented from the fixture's initial training


# ---------------------------------------------------------------------------
# /devices
# ---------------------------------------------------------------------------
class TestDeviceRoutes:
    def test_device_detail(self, api_project):
        client, _, _ = api_project
        r = client.get("/devices/10.0.0.5")
        assert r.status_code == 200
        body = r.json()
        assert body["ip"] == "10.0.0.5"
        assert body["building"] == "HQ"
        assert body["name"] == "core-router-01"
        assert "health_score" in body
        assert "open_alerts" in body
        assert body["has_per_device_profile"] is False

    def test_device_not_found(self, api_project):
        client, _, _ = api_project
        r = client.get("/devices/10.0.0.99")
        assert r.status_code == 404

    def test_create_and_delete_baseline(self, api_project):
        client, _, _ = api_project

        r = client.post("/devices/10.0.0.5/baseline")
        assert r.status_code == 200
        assert r.json()["has_per_device_profile"] is True

        r = client.get("/devices/10.0.0.5")
        assert r.json()["has_per_device_profile"] is True

        r = client.delete("/devices/10.0.0.5/baseline")
        assert r.status_code == 200
        assert r.json()["baseline_removed"] is True

        r = client.get("/devices/10.0.0.5")
        assert r.json()["has_per_device_profile"] is False

    def test_baseline_unknown_device_404(self, api_project):
        client, _, _ = api_project
        r = client.post("/devices/10.0.0.99/baseline")
        assert r.status_code == 404

    def test_delete_nonexistent_baseline_404(self, api_project):
        client, _, _ = api_project
        r = client.delete("/devices/10.0.0.5/baseline")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# /alerts
# ---------------------------------------------------------------------------
class TestAlertRoutes:
    def test_open_alerts(self, api_project):
        client, _, _ = api_project
        r = client.get("/alerts/open")
        assert r.status_code == 200
        body = r.json()
        assert "alerts" in body
        for alert in body["alerts"]:
            assert alert["status"] == "open"

    def test_list_alerts_no_filters(self, api_project):
        client, _, _ = api_project
        r = client.get("/alerts")
        assert r.status_code == 200
        body = r.json()
        assert "count" in body
        assert body["count"] == len(body["alerts"])

    def test_list_alerts_filter_by_severity(self, api_project):
        client, _, _ = api_project
        r = client.get("/alerts")
        all_severities = {a["severity"] for a in r.json()["alerts"]}
        if not all_severities:
            pytest.skip("no alerts generated by fixture")
        target = sorted(all_severities)[0]

        r2 = client.get(f"/alerts?severity={target}")
        for alert in r2.json()["alerts"]:
            assert alert["severity"] == target

    def test_list_alerts_filter_by_device(self, api_project):
        client, _, _ = api_project
        r = client.get("/alerts?device_ip=10.0.0.5")
        for alert in r.json()["alerts"]:
            assert alert["entity_id"] == "10.0.0.5"

    def test_list_alerts_since_future_excludes_all(self, api_project):
        client, _, _ = api_project
        far_future = time.time() + 3600 * 24 * 365
        r = client.get(f"/alerts?since={far_future}")
        assert r.json()["count"] == 0

    def test_distribution(self, api_project):
        client, _, _ = api_project
        r = client.get("/alerts/distribution?last_hours=999999")
        assert r.status_code == 200
        body = r.json()
        assert "distribution" in body
        for entry in body["distribution"]:
            assert "issue_count" in entry
            assert "max_severity" in entry
            assert "issue_types" in entry

    def test_health_scores(self, api_project):
        client, _, _ = api_project
        r = client.get("/alerts/health-scores")
        assert r.status_code == 200
        body = r.json()["health_scores"]
        assert "10.0.0.5" in body
        assert 0 <= body["10.0.0.5"]["health_score"] <= 100
        assert body["10.0.0.5"]["building"] == "HQ"


# ---------------------------------------------------------------------------
# /topology
# ---------------------------------------------------------------------------
class TestTopologyRoutes:
    def test_buildings(self, api_project):
        client, _, _ = api_project
        r = client.get("/topology/buildings")
        assert r.status_code == 200
        buildings = r.json()["buildings"]
        names = {b["building"] for b in buildings}
        assert "HQ" in names
        assert "Branch-A" in names

        hq = next(b for b in buildings if b["building"] == "HQ")
        assert hq["device_count"] == 2
        assert len(hq["devices"]) == 2

    def test_buildings_branch_a_has_no_data(self, api_project):
        client, _, _ = api_project
        r = client.get("/topology/buildings")
        buildings = {b["building"]: b for b in r.json()["buildings"]}
        branch_a = buildings["Branch-A"]
        # 10.0.0.7 never appears in our synthetic data - health unknown
        assert branch_a["devices"][0]["status"] == "unknown"
        assert branch_a["devices"][0]["health_score"] is None

    def test_device_list(self, api_project):
        client, _, _ = api_project
        r = client.get("/topology/devices")
        assert r.status_code == 200
        devices = r.json()["devices"]
        assert len(devices) == 3
        ips = {d["ip"] for d in devices}
        assert ips == {"10.0.0.5", "10.0.0.6", "10.0.0.7"}


# ---------------------------------------------------------------------------
# /traffic
# ---------------------------------------------------------------------------
class TestTrafficRoutes:
    def test_recent_with_old_data_is_empty(self, api_project):
        """Fixture data has fixed historical timestamps, so 'recent'
        (relative to wall-clock now) should be empty but not error."""
        client, _, _ = api_project
        r = client.get("/traffic/recent?minutes=15")
        assert r.status_code == 200
        assert r.json() == {"window_sec": 60, "devices": {}}

    def test_recent_with_fresh_data(self, api_project):
        client, project_dir, _ = api_project

        now = time.time()
        rows = []
        for i in range(10):
            rows.append({
                "timestamp": now - 60 + i, "src_ip": "10.0.0.5", "dst_ip": "8.8.8.8",
                "src_port": 50000 + i, "dst_port": 443, "protocol": 6, "tcp_flags": 16,
                "packets": 5, "bytes": 1000, "duration_sec": 1.0,
            })
        pd.DataFrame(rows).to_csv(project_dir / "data" / "raw" / "netflow_raw_live.csv", index=False)

        r = client.get("/traffic/recent?minutes=5")
        assert r.status_code == 200
        body = r.json()
        assert "10.0.0.5" in body["devices"]
        assert len(body["devices"]["10.0.0.5"]) >= 1
        assert body["devices"]["10.0.0.5"][0]["bytes_out"] > 0

    def test_live_scores_with_old_data_is_empty(self, api_project):
        client, _, _ = api_project
        r = client.get("/traffic/live-scores")
        assert r.status_code == 200
        assert r.json() == {"scores": []}

    def test_live_scores_with_fresh_data(self, api_project):
        client, project_dir, _ = api_project

        now = time.time()
        rows = []
        for i in range(20):
            rows.append({
                "timestamp": now - 30 + i, "src_ip": "10.0.0.5", "dst_ip": "8.8.8.8",
                "src_port": 50000 + i, "dst_port": 443, "protocol": 6, "tcp_flags": 16,
                "packets": 5, "bytes": 1000, "duration_sec": 1.0,
            })
        pd.DataFrame(rows).to_csv(project_dir / "data" / "raw" / "netflow_raw_live.csv", index=False)

        r = client.get("/traffic/live-scores")
        assert r.status_code == 200
        body = r.json()["scores"]
        assert len(body) > 0
        for row in body:
            assert "detector" in row
            assert "entity_id" in row
            assert row["anomaly_score"] is None or isinstance(row["anomaly_score"], float)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
