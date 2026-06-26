"""
test_alerts.py
----------------
Covers:
  - risk_scoring.py: severity thresholds, severity ordering, issue type
    classification (including bandwidth congestion-vs-capacity), health
    score computation including NaN handling
  - alert_store.py: create/update/close lifecycle, find_open_alert,
    daily file rotation, list_alerts filtering
  - alert_engine.py:
      - process_window: new alert creation, severity escalation while
        extending an existing alert, closing alerts when score drops
        below threshold, NaN scores neither create nor close alerts
      - compute_health_scores
      - issue_distribution: must_add_to_project.txt item 5
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pytest
import yaml

from alerts.risk_scoring import (
    classify_issue_type,
    compute_health_score,
    score_to_severity,
    severity_rank,
    ISSUE_TYPE_BANDWIDTH_CONGESTION,
    ISSUE_TYPE_CAPACITY,
    ISSUE_TYPE_PORTSCAN,
    ISSUE_TYPE_DEVICE_BEHAVIOR,
    ISSUE_TYPE_PROTOCOL,
    ISSUE_TYPE_UNKNOWN,
)
from alerts.alert_store import AlertStore, STATUS_OPEN, STATUS_CLOSED
from alerts.alert_engine import AlertEngine


# ---------------------------------------------------------------------------
# risk_scoring.score_to_severity / severity_rank
# ---------------------------------------------------------------------------
class TestScoreToSeverity:
    @pytest.mark.parametrize("score,expected", [
        (0.95, "critical"),
        (0.85, "critical"),
        (0.80, "high"),
        (0.75, "high"),
        (0.70, "medium"),
        (0.65, "medium"),
        (0.60, "low"),
        (0.55, "low"),
        (0.50, "info"),
        (0.0, "info"),
    ])
    def test_thresholds(self, score, expected):
        assert score_to_severity(score) == expected

    def test_nan_maps_to_info(self):
        assert score_to_severity(float("nan")) == "info"

    def test_none_maps_to_info(self):
        assert score_to_severity(None) == "info"

    def test_severity_rank_ordering(self):
        assert severity_rank("info") < severity_rank("low")
        assert severity_rank("low") < severity_rank("medium")
        assert severity_rank("medium") < severity_rank("high")
        assert severity_rank("high") < severity_rank("critical")

    def test_unknown_severity_rank_defaults_low(self):
        assert severity_rank("not_a_severity") == 0


# ---------------------------------------------------------------------------
# risk_scoring.classify_issue_type
# ---------------------------------------------------------------------------
class TestClassifyIssueType:
    def test_bandwidth_low_utilization_is_congestion(self):
        result = classify_issue_type("bandwidth", {"if_util_in": 0.3, "if_util_out": 0.2})
        assert result == ISSUE_TYPE_BANDWIDTH_CONGESTION

    def test_bandwidth_high_utilization_is_capacity(self):
        result = classify_issue_type("bandwidth", {"if_util_in": 0.9, "if_util_out": 0.2})
        assert result == ISSUE_TYPE_CAPACITY

    def test_bandwidth_high_outbound_utilization_is_capacity(self):
        result = classify_issue_type("bandwidth", {"if_util_in": 0.1, "if_util_out": 0.95})
        assert result == ISSUE_TYPE_CAPACITY

    def test_bandwidth_no_features_falls_back_to_congestion(self):
        assert classify_issue_type("bandwidth", None) == ISSUE_TYPE_BANDWIDTH_CONGESTION
        assert classify_issue_type("bandwidth", {}) == ISSUE_TYPE_BANDWIDTH_CONGESTION

    def test_portscan(self):
        assert classify_issue_type("portscan") == ISSUE_TYPE_PORTSCAN

    def test_device_behavior(self):
        assert classify_issue_type("device_behavior") == ISSUE_TYPE_DEVICE_BEHAVIOR

    def test_protocol(self):
        assert classify_issue_type("protocol") == ISSUE_TYPE_PROTOCOL

    def test_unknown_detector(self):
        assert classify_issue_type("something_else") == ISSUE_TYPE_UNKNOWN


# ---------------------------------------------------------------------------
# risk_scoring.compute_health_score
# ---------------------------------------------------------------------------
class TestComputeHealthScore:
    def test_typical_scores_give_full_health(self):
        assert compute_health_score({"bandwidth": 0.5, "portscan": 0.5,
                                       "device_behavior": 0.5, "protocol": 0.5}) == 100.0

    def test_below_typical_clamped_to_full_health(self):
        assert compute_health_score({"bandwidth": 0.2, "portscan": 0.3,
                                       "device_behavior": 0.1, "protocol": 0.0}) == 100.0

    def test_max_anomaly_gives_zero_health(self):
        assert compute_health_score({"bandwidth": 1.0, "portscan": 1.0,
                                       "device_behavior": 1.0, "protocol": 1.0}) == 0.0

    def test_partial_anomaly_midrange(self):
        score = compute_health_score({"bandwidth": 0.75, "portscan": 0.75,
                                        "device_behavior": 0.75, "protocol": 0.75})
        assert score == pytest.approx(50.0)

    def test_nan_entries_ignored_and_weights_redistributed(self):
        score = compute_health_score({
            "bandwidth": float("nan"), "portscan": float("nan"),
            "device_behavior": 0.9, "protocol": float("nan"),
        })
        assert 0 <= score < 100
        assert score == pytest.approx(20.0)

    def test_all_nan_returns_full_health(self):
        score = compute_health_score({
            "bandwidth": float("nan"), "portscan": float("nan"),
            "device_behavior": float("nan"), "protocol": float("nan"),
        })
        assert score == 100.0

    def test_empty_dict_returns_full_health(self):
        assert compute_health_score({}) == 100.0

    def test_unweighted_detector_equal_split(self):
        score = compute_health_score({"unknown_detector_a": 1.0, "unknown_detector_b": 0.5})
        assert score == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# AlertStore
# ---------------------------------------------------------------------------
class TestAlertStore:
    @pytest.fixture
    def store(self, tmp_path):
        return AlertStore(tmp_path / "alerts")

    def test_create_alert(self, store):
        alert = store.create_alert(
            detector="bandwidth", entity_id="10.0.0.5", issue_type="network_congestion",
            severity="high", window=1000, score=0.8, building="HQ", device_name="sw1",
        )
        assert alert["status"] == STATUS_OPEN
        assert alert["window_count"] == 1
        assert alert["max_score"] == 0.8
        assert "id" in alert

    def test_find_open_alert(self, store):
        store.create_alert("bandwidth", "10.0.0.5", "network_congestion", "high", 1000, 0.8)
        found = store.find_open_alert("bandwidth", "10.0.0.5")
        assert found is not None
        assert found["entity_id"] == "10.0.0.5"

    def test_find_open_alert_returns_none_for_unknown(self, store):
        assert store.find_open_alert("bandwidth", "10.0.0.99") is None

    def test_update_alert_increments_window_count(self, store):
        alert = store.create_alert("bandwidth", "10.0.0.5", "network_congestion", "high", 1000, 0.8)
        updated = store.update_alert(alert, window=1060, score=0.85, severity="high")
        assert updated["window_count"] == 2
        assert updated["max_score"] == 0.85
        assert updated["last_window"] == 1060

    def test_update_alert_escalates_severity(self, store):
        alert = store.create_alert("bandwidth", "10.0.0.5", "network_congestion", "high", 1000, 0.8)
        updated = store.update_alert(alert, window=1060, score=0.95, severity="critical")
        assert updated["severity"] == "critical"

    def test_update_alert_does_not_deescalate_severity(self, store):
        alert = store.create_alert("bandwidth", "10.0.0.5", "network_congestion", "critical", 1000, 0.95)
        updated = store.update_alert(alert, window=1060, score=0.6, severity="low")
        assert updated["severity"] == "critical"

    def test_close_alert(self, store):
        alert = store.create_alert("bandwidth", "10.0.0.5", "network_congestion", "high", 1000, 0.8)
        closed = store.close_alert(alert)
        assert closed["status"] == STATUS_CLOSED
        assert closed["closed_at"] is not None
        assert store.find_open_alert("bandwidth", "10.0.0.5") is None

    def test_list_open_alerts(self, store):
        store.create_alert("bandwidth", "10.0.0.5", "network_congestion", "high", 1000, 0.8)
        alert2 = store.create_alert("portscan", "203.0.113.50", "connectivity_security", "critical", 1000, 0.95)
        store.close_alert(alert2)

        open_alerts = store.list_open_alerts()
        assert len(open_alerts) == 1
        assert open_alerts[0]["detector"] == "bandwidth"

    def test_list_alerts_filters(self, store):
        store.create_alert("bandwidth", "10.0.0.5", "network_congestion", "high", 1000, 0.8,
                           building="HQ", device_name="sw1")
        store.create_alert("portscan", "203.0.113.50", "connectivity_security", "critical", 1000, 0.95)

        hq_alerts = store.list_alerts(building="HQ")
        assert len(hq_alerts) == 1
        assert hq_alerts[0]["entity_id"] == "10.0.0.5"

        critical_alerts = store.list_alerts(severity="critical")
        assert len(critical_alerts) == 1
        assert critical_alerts[0]["entity_id"] == "203.0.113.50"

    def test_list_alerts_time_range(self, store):
        store.create_alert("bandwidth", "10.0.0.5", "network_congestion", "high", 1000, 0.8)
        now = time.time()

        future_alerts = store.list_alerts(since=now + 100)
        assert future_alerts == []

        past_alerts = store.list_alerts(since=now - 100)
        assert len(past_alerts) == 1

    def test_persistence_across_instances(self, tmp_path):
        store1 = AlertStore(tmp_path / "alerts")
        store1.create_alert("bandwidth", "10.0.0.5", "network_congestion", "high", 1000, 0.8)

        store2 = AlertStore(tmp_path / "alerts")
        alerts = store2.list_alerts()
        assert len(alerts) == 1


# ---------------------------------------------------------------------------
# AlertEngine.process_window
# ---------------------------------------------------------------------------
class TestProcessWindow:
    @pytest.fixture
    def engine(self, tmp_path):
        project_dir = tmp_path / "project"
        (project_dir / "data" / "alerts").mkdir(parents=True)
        (project_dir / "data" / "models").mkdir(parents=True)
        (project_dir / "data" / "processed").mkdir(parents=True)
        (project_dir / "data" / "raw").mkdir(parents=True)

        config = {
            "system": {"mode": "inference", "kafka_bootstrap": "localhost:9092"},
            "prtg": {"base_url": "https://prtg.local", "api_token": "test", "poll_interval_sec": 60,
                     "avg_interval_sec": 60, "poll_lag_sec": 30},
            "devices": [{"ip": "10.0.0.5", "name": "sw1", "building": "HQ", "sensors": {}}],
            "bootstrap": {"min_collection_days": 14, "min_netflow_records": 100000,
                          "training_hour_utc": 2, "retrain_interval_days": 7,
                          "rolling_training_window_days": 90},
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

        return AlertEngine(str(config_path))

    def _scores_df(self, rows):
        return pd.DataFrame(rows)

    def test_high_score_creates_alert(self, engine):
        scores = self._scores_df([
            {"detector": "bandwidth", "entity_id": "10.0.0.5", "window": 1000,
             "anomaly_score": 0.8, "profile_used": "global", "features": {}},
        ])
        changed = engine.process_window(scores)
        assert len(changed) == 1
        assert changed[0]["severity"] == "high"
        assert changed[0]["building"] == "HQ"
        assert changed[0]["device_name"] == "sw1"

    def test_low_score_does_not_create_alert(self, engine):
        scores = self._scores_df([
            {"detector": "bandwidth", "entity_id": "10.0.0.5", "window": 1000,
             "anomaly_score": 0.3, "profile_used": "global", "features": {}},
        ])
        changed = engine.process_window(scores)
        assert changed == []
        assert engine.store.list_open_alerts() == []

    def test_nan_score_does_not_create_alert(self, engine):
        scores = self._scores_df([
            {"detector": "bandwidth", "entity_id": "10.0.0.5", "window": 1000,
             "anomaly_score": float("nan"), "profile_used": "global", "features": {}},
        ])
        changed = engine.process_window(scores)
        assert changed == []

    def test_repeated_high_scores_extend_same_alert(self, engine):
        scores1 = self._scores_df([
            {"detector": "bandwidth", "entity_id": "10.0.0.5", "window": 1000,
             "anomaly_score": 0.78, "profile_used": "global", "features": {}},
        ])
        scores2 = self._scores_df([
            {"detector": "bandwidth", "entity_id": "10.0.0.5", "window": 1060,
             "anomaly_score": 0.90, "profile_used": "global", "features": {}},
        ])
        engine.process_window(scores1)
        changed = engine.process_window(scores2)

        assert len(changed) == 1
        assert changed[0]["window_count"] == 2
        assert changed[0]["max_score"] == 0.90
        assert changed[0]["severity"] == "critical"

        open_alerts = engine.store.list_open_alerts()
        assert len(open_alerts) == 1

    def test_score_drop_closes_alert(self, engine):
        scores1 = self._scores_df([
            {"detector": "bandwidth", "entity_id": "10.0.0.5", "window": 1000,
             "anomaly_score": 0.8, "profile_used": "global", "features": {}},
        ])
        scores2 = self._scores_df([
            {"detector": "bandwidth", "entity_id": "10.0.0.5", "window": 1060,
             "anomaly_score": 0.3, "profile_used": "global", "features": {}},
        ])
        engine.process_window(scores1)
        changed = engine.process_window(scores2)

        # process_window returns ALL touched alerts, including closures, so
        # the caller (and dashboard) can be notified an issue resolved.
        assert len(changed) == 1
        assert changed[0]["status"] == STATUS_CLOSED
        assert engine.store.list_open_alerts() == []

        all_alerts = engine.store.list_alerts()
        assert len(all_alerts) == 1
        assert all_alerts[0]["status"] == STATUS_CLOSED

    def test_unknown_entity_gets_null_metadata(self, engine):
        scores = self._scores_df([
            {"detector": "portscan", "entity_id": "203.0.113.50", "window": 1000,
             "anomaly_score": 0.9, "profile_used": "global", "features": {}},
        ])
        changed = engine.process_window(scores)
        assert changed[0]["building"] is None
        assert changed[0]["device_name"] is None

    def test_bandwidth_issue_type_from_features(self, engine):
        scores = self._scores_df([
            {"detector": "bandwidth", "entity_id": "10.0.0.5", "window": 1000,
             "anomaly_score": 0.8, "profile_used": "global",
             "features": {"if_util_in": 0.95, "if_util_out": 0.1}},
        ])
        changed = engine.process_window(scores)
        assert changed[0]["issue_type"] == ISSUE_TYPE_CAPACITY

    def test_per_device_profile_used_recorded(self, engine):
        scores = self._scores_df([
            {"detector": "device_behavior", "entity_id": "10.0.0.5", "window": 1000,
             "anomaly_score": 0.8, "profile_used": "per_device", "features": {}},
        ])
        changed = engine.process_window(scores)
        assert changed[0]["profile_used"] == "per_device"


# ---------------------------------------------------------------------------
# AlertEngine.compute_health_scores
# ---------------------------------------------------------------------------
class TestComputeHealthScores:
    @pytest.fixture
    def engine(self, tmp_path):
        project_dir = tmp_path / "project"
        (project_dir / "data" / "alerts").mkdir(parents=True)
        (project_dir / "data" / "models").mkdir(parents=True)
        (project_dir / "data" / "processed").mkdir(parents=True)
        (project_dir / "data" / "raw").mkdir(parents=True)

        config = {
            "system": {"mode": "inference", "kafka_bootstrap": "localhost:9092"},
            "prtg": {"base_url": "https://prtg.local", "api_token": "test", "poll_interval_sec": 60,
                     "avg_interval_sec": 60, "poll_lag_sec": 30},
            "devices": [],
            "bootstrap": {"min_collection_days": 14, "min_netflow_records": 100000,
                          "training_hour_utc": 2, "retrain_interval_days": 7,
                          "rolling_training_window_days": 90},
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

        return AlertEngine(str(config_path))

    def test_per_entity_health_scores(self, engine):
        scores = pd.DataFrame([
            {"detector": "bandwidth", "entity_id": "10.0.0.5", "window": 1000, "anomaly_score": 0.5, "profile_used": "global", "features": {}},
            {"detector": "device_behavior", "entity_id": "10.0.0.5", "window": 1000, "anomaly_score": 0.5, "profile_used": "global", "features": {}},
            {"detector": "bandwidth", "entity_id": "10.0.0.6", "window": 1000, "anomaly_score": 1.0, "profile_used": "global", "features": {}},
        ])
        health = engine.compute_health_scores(scores)
        assert health["10.0.0.5"] == 100.0
        assert health["10.0.0.6"] < 100.0


# ---------------------------------------------------------------------------
# AlertEngine.issue_distribution
# ---------------------------------------------------------------------------
class TestIssueDistribution:
    @pytest.fixture
    def engine(self, tmp_path):
        project_dir = tmp_path / "project"
        (project_dir / "data" / "alerts").mkdir(parents=True)
        (project_dir / "data" / "models").mkdir(parents=True)
        (project_dir / "data" / "processed").mkdir(parents=True)
        (project_dir / "data" / "raw").mkdir(parents=True)

        config = {
            "system": {"mode": "inference", "kafka_bootstrap": "localhost:9092"},
            "prtg": {"base_url": "https://prtg.local", "api_token": "test", "poll_interval_sec": 60,
                     "avg_interval_sec": 60, "poll_lag_sec": 30},
            "devices": [
                {"ip": "10.0.0.5", "name": "sw1", "building": "HQ", "sensors": {}},
                {"ip": "10.0.0.6", "name": "sw2", "building": "Branch-A", "sensors": {}},
            ],
            "bootstrap": {"min_collection_days": 14, "min_netflow_records": 100000,
                          "training_hour_utc": 2, "retrain_interval_days": 7,
                          "rolling_training_window_days": 90},
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

        return AlertEngine(str(config_path))

    def test_distribution_groups_by_entity(self, engine):
        scores = pd.DataFrame([
            {"detector": "bandwidth", "entity_id": "10.0.0.5", "window": 1000, "anomaly_score": 0.78, "profile_used": "global", "features": {"if_util_in":0.3,"if_util_out":0.3}},
            {"detector": "device_behavior", "entity_id": "10.0.0.5", "window": 1000, "anomaly_score": 0.95, "profile_used": "global", "features": {}},
            {"detector": "protocol", "entity_id": "10.0.0.6", "window": 1000, "anomaly_score": 0.7, "profile_used": "global", "features": {}},
        ])
        engine.process_window(scores)

        dist = engine.issue_distribution()
        by_entity = {d["entity_id"]: d for d in dist}

        assert by_entity["10.0.0.5"]["issue_count"] == 2
        assert by_entity["10.0.0.5"]["max_severity"] == "critical"
        assert set(by_entity["10.0.0.5"]["issue_types"]) == {"network_congestion", "device_environment"}

        assert by_entity["10.0.0.6"]["issue_count"] == 1
        assert by_entity["10.0.0.6"]["building"] == "Branch-A"

    def test_distribution_respects_time_range(self, engine):
        scores = pd.DataFrame([
            {"detector": "bandwidth", "entity_id": "10.0.0.5", "window": 1000, "anomaly_score": 0.8, "profile_used": "global", "features": {}},
        ])
        engine.process_window(scores)

        future = engine.issue_distribution(since=time.time() + 1000)
        assert future == []

        past = engine.issue_distribution(since=time.time() - 1000)
        assert len(past) == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
