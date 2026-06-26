"""
test_ensemble_detector.py
----------------------------
Covers:
  - ModelBundle loading: handles missing model files, missing
    normalization_stats.json, missing protocol_baseline.csv, and
    per-device profiles directory gracefully (all expected during early
    observation phase)
  - score_window: produces NaN scores (not crashes, not 0) when no models
    are loaded
  - score_window: produces real [0,1] scores when models are present
  - Per-device profile override: a device with a trained per-device model
    gets profile_used="per_device" and a score from that model, while other
    devices get "global"
  - Multi-window correctness: a device with a per-device profile spanning
    multiple windows gets a distinct score per window (regression test for
    the bug where all windows collapsed to the first row's score)
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pytest

from detectors.ensemble_detector import ModelBundle, score_window
from training.common import save_model, train_isolation_forest, to_matrix, feature_columns
from preprocessing.unified_preprocessing import (
    DeviceBehaviorFeatures,
    build_all_features,
    build_all_normalization_stats,
    compute_normalization_stats,
)


# ---------------------------------------------------------------------------
# Synthetic data fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def synthetic_netflow():
    import random
    random.seed(7)
    base_ts = 1718000000
    rows = []
    for window in range(20):
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
    return pd.DataFrame(rows)


@pytest.fixture
def synthetic_snmp(synthetic_netflow):
    import random
    random.seed(8)
    base_ts = synthetic_netflow["timestamp"].min()
    rows = []
    for window in range(20):
        ts = base_ts + window * 60
        for dev in ["10.0.0.5", "10.0.0.6"]:
            rows.append({
                "timestamp": ts, "device_ip": dev,
                "if_in_octets": random.randint(10000, 50000),
                "if_out_octets": random.randint(10000, 50000),
                "if_speed": 1_000_000_000,
                "if_in_errors": 0,
                "cpu_load_pct": random.uniform(10, 30),
                "mem_used_pct": random.uniform(20, 50),
            })
    return pd.DataFrame(rows)


@pytest.fixture
def trained_models_dir(tmp_path, synthetic_netflow, synthetic_snmp):
    """
    Train real IsolationForest models on synthetic data and write them +
    normalization_stats.json + protocol_baseline.csv to tmp_path, mimicking
    what the train_*.py scripts produce.
    """
    models_dir = tmp_path / "models"
    processed_dir = tmp_path / "processed"
    models_dir.mkdir()
    processed_dir.mkdir()

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    synthetic_netflow.to_csv(raw_dir / "netflow_raw_2026-06-13.csv", index=False)
    synthetic_snmp.to_csv(raw_dir / "prtg_raw_2026-06-13.csv", index=False)

    features = build_all_features(str(raw_dir), str(raw_dir))

    model_files = {
        "bandwidth": "bandwidth_model.pkl",
        "portscan": "portscan_model.pkl",
        "device_behavior": "device_model.pkl",
        "protocol": "protocol_model.pkl",
    }
    for detector, filename in model_files.items():
        feat = features[detector]
        cols = feature_columns(feat)
        X = to_matrix(feat, cols)
        model = train_isolation_forest(X)
        save_model(models_dir / filename, model, cols, "isolation_forest", len(feat))

    stats = build_all_normalization_stats(features)
    with open(models_dir / "normalization_stats.json", "w") as f:
        json.dump(stats, f, indent=2)

    # Empty protocol baseline (first-run scenario)
    pd.DataFrame(columns=["device_ip", "protocol", "ratio"]).to_csv(
        processed_dir / "protocol_baseline.csv", index=False
    )

    return models_dir, processed_dir, raw_dir


# ---------------------------------------------------------------------------
# ModelBundle loading - graceful degradation
# ---------------------------------------------------------------------------
class TestModelBundleLoading:
    def test_missing_everything(self, tmp_path):
        models = ModelBundle(tmp_path / "models", tmp_path / "processed")
        assert all(v is None for v in models.models.values())
        assert models.normalization_stats == {}
        assert models.protocol_baseline == {}
        assert models.device_profiles == {}

    def test_loads_trained_models(self, trained_models_dir):
        models_dir, processed_dir, _ = trained_models_dir
        models = ModelBundle(models_dir, processed_dir)
        for detector in ["bandwidth", "portscan", "device_behavior", "protocol"]:
            assert models.models[detector] is not None
            assert models.models[detector]["model_type"] == "isolation_forest"
        assert "bandwidth" in models.normalization_stats
        assert "device_behavior" in models.normalization_stats

    def test_reload_picks_up_new_files(self, tmp_path, trained_models_dir):
        models_dir, processed_dir, _ = trained_models_dir
        models = ModelBundle(models_dir, processed_dir)
        assert models.models["bandwidth"] is not None

        (models_dir / "bandwidth_model.pkl").unlink()
        reloaded = models.reload()
        assert reloaded.models["bandwidth"] is None
        # original instance unaffected
        assert models.models["bandwidth"] is not None


# ---------------------------------------------------------------------------
# score_window - no models (observation phase)
# ---------------------------------------------------------------------------
class TestScoreWindowNoModels:
    def test_returns_nan_scores_not_zero(self, tmp_path, synthetic_netflow, synthetic_snmp):
        models = ModelBundle(tmp_path / "models", tmp_path / "processed")
        result = score_window(synthetic_netflow, synthetic_snmp, models)

        assert not result.empty
        assert result["anomaly_score"].isna().all(), (
            "With no trained models, scores must be NaN (no opinion), "
            "not 0 (which would mean 'definitely normal')"
        )

    def test_still_includes_all_detectors_and_entities(self, tmp_path, synthetic_netflow, synthetic_snmp):
        models = ModelBundle(tmp_path / "models", tmp_path / "processed")
        result = score_window(synthetic_netflow, synthetic_snmp, models)

        assert set(result["detector"]) == {"bandwidth", "portscan", "device_behavior", "protocol"}
        assert "10.0.0.5" in result["entity_id"].values
        assert "10.0.0.6" in result["entity_id"].values


# ---------------------------------------------------------------------------
# score_window - with trained models
# ---------------------------------------------------------------------------
class TestScoreWindowWithModels:
    def test_scores_in_valid_range(self, trained_models_dir, synthetic_netflow, synthetic_snmp):
        models_dir, processed_dir, _ = trained_models_dir
        models = ModelBundle(models_dir, processed_dir)

        result = score_window(synthetic_netflow, synthetic_snmp, models)
        scores = result["anomaly_score"].dropna()
        assert len(scores) > 0
        assert (scores >= 0).all() and (scores <= 1).all()

    def test_all_global_without_device_profiles(self, trained_models_dir, synthetic_netflow, synthetic_snmp):
        models_dir, processed_dir, _ = trained_models_dir
        models = ModelBundle(models_dir, processed_dir)

        result = score_window(synthetic_netflow, synthetic_snmp, models)
        db = result[result["detector"] == "device_behavior"]
        assert (db["profile_used"] == "global").all()


# ---------------------------------------------------------------------------
# Per-device profile override
# ---------------------------------------------------------------------------
class TestPerDeviceProfile:
    @pytest.fixture
    def models_with_device_profile(self, trained_models_dir, synthetic_netflow, synthetic_snmp):
        """Add a per-device profile for 10.0.0.5 to the trained_models_dir fixture."""
        models_dir, processed_dir, raw_dir = trained_models_dir

        dev_feat = DeviceBehaviorFeatures.from_csv(str(raw_dir), str(raw_dir))
        dev_feat = dev_feat[dev_feat["device_ip"] == "10.0.0.5"]

        cols = feature_columns(dev_feat)
        X = to_matrix(dev_feat, cols)
        model = train_isolation_forest(X)

        profiles_dir = models_dir / "device_profiles"
        profiles_dir.mkdir()
        save_model(
            profiles_dir / "10_0_0_5_model.pkl", model, cols, "isolation_forest",
            len(dev_feat), extra_meta={"device_ip": "10.0.0.5", "scope": "per_device"},
        )

        # Per-device normalization stats
        dev_stats = compute_normalization_stats(dev_feat, "device_ip", DeviceBehaviorFeatures.ZSCORE_VALUE_COLS)
        with open(models_dir / "normalization_stats.json") as f:
            stats = json.load(f)
        stats.setdefault("device_behavior_profiles", {})["10.0.0.5"] = dev_stats.get("10.0.0.5", {})
        with open(models_dir / "normalization_stats.json", "w") as f:
            json.dump(stats, f, indent=2)

        return ModelBundle(models_dir, processed_dir)

    def test_device_with_profile_uses_per_device(self, models_with_device_profile, synthetic_netflow, synthetic_snmp):
        models = models_with_device_profile
        assert "10.0.0.5" in models.device_profiles

        result = score_window(synthetic_netflow, synthetic_snmp, models)
        db = result[result["detector"] == "device_behavior"]

        dev5 = db[db["entity_id"] == "10.0.0.5"]
        dev6 = db[db["entity_id"] == "10.0.0.6"]

        assert (dev5["profile_used"] == "per_device").all()
        assert (dev6["profile_used"] == "global").all()

    def test_multi_window_device_gets_distinct_scores(self, models_with_device_profile, synthetic_netflow, synthetic_snmp):
        """Regression test: a device with a per-device profile spanning
        multiple windows must get a distinct score per window, not the
        same score repeated for every window."""
        models = models_with_device_profile
        result = score_window(synthetic_netflow, synthetic_snmp, models)

        dev5 = result[(result["detector"] == "device_behavior") & (result["entity_id"] == "10.0.0.5")]
        assert len(dev5) > 1, "Expected multiple windows for 10.0.0.5"

        scores = dev5["anomaly_score"].tolist()
        assert len(set(scores)) > 1, (
            f"All windows for 10.0.0.5 got the same score {scores[0]!r} - "
            "per-device scoring may be collapsing to a single row"
        )

    def test_other_detectors_unaffected_by_device_profile(self, models_with_device_profile, synthetic_netflow, synthetic_snmp):
        models = models_with_device_profile
        result = score_window(synthetic_netflow, synthetic_snmp, models)

        for detector in ["bandwidth", "portscan", "protocol"]:
            rows = result[result["detector"] == detector]
            assert (rows["profile_used"] == "global").all()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
