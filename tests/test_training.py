"""
test_training.py
------------------
Covers:
  - training/common.py: feature_columns selection excludes ID columns,
    to_matrix handles missing columns, save_model/load_model round-trip
    preserves feature_columns (the critical train/inference contract),
    split_train_eval's time-aware ordering.
  - evaluate_models.py: pass/fail gating logic for isolation forest and
    random forest models, missing files, feature-column drift detection.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import IsolationForest

from training.common import (
    feature_columns,
    load_model,
    save_model,
    score_isolation_forest,
    split_train_eval,
    to_matrix,
    train_isolation_forest,
    write_normalization_stats_slice,
)
from training.evaluate_models import evaluate_detector, evaluate_isolation_forest, EvalResult


class ConstantModel:
    """Fake model whose decision_function always returns 0 - used to test
    that evaluate_models rejects degenerate (zero-variance) score
    distributions. Defined at module level so joblib can pickle it."""
    def decision_function(self, X):
        return np.zeros(len(X))


# ---------------------------------------------------------------------------
# feature_columns / to_matrix
# ---------------------------------------------------------------------------
class TestFeatureColumns:
    def test_excludes_id_columns(self):
        df = pd.DataFrame({
            "device_ip": ["10.0.0.1"], "window": [123], "label": [0],
            "bw_in_bytes": [100.0], "bw_out_bytes": [200.0],
        })
        cols = feature_columns(df)
        assert "device_ip" not in cols
        assert "window" not in cols
        assert "label" not in cols
        assert set(cols) == {"bw_in_bytes", "bw_out_bytes"}

    def test_excludes_non_numeric(self):
        df = pd.DataFrame({
            "bw_in_bytes": [100.0],
            "some_string_col": ["hello"],
        })
        cols = feature_columns(df)
        assert cols == ["bw_in_bytes"]


class TestToMatrix:
    def test_basic(self):
        df = pd.DataFrame({"a": [1.0, 2.0], "b": [3.0, 4.0]})
        X = to_matrix(df, ["a", "b"])
        assert X.shape == (2, 2)
        np.testing.assert_array_equal(X, [[1.0, 3.0], [2.0, 4.0]])

    def test_missing_column_filled_with_zero(self):
        """A live inference batch missing a column the model expects
        (e.g. no UDP flows this window -> no avg_pkt_size_udp variance)
        gets 0, not a crash."""
        df = pd.DataFrame({"a": [1.0, 2.0]})
        X = to_matrix(df, ["a", "b"])
        assert X.shape == (2, 2)
        np.testing.assert_array_equal(X[:, 1], [0.0, 0.0])

    def test_nan_filled_with_zero(self):
        df = pd.DataFrame({"a": [1.0, np.nan]})
        X = to_matrix(df, ["a"])
        assert X[1, 0] == 0.0

    def test_column_order_preserved(self):
        df = pd.DataFrame({"a": [1.0], "b": [2.0], "c": [3.0]})
        X = to_matrix(df, ["c", "a", "b"])
        np.testing.assert_array_equal(X, [[3.0, 1.0, 2.0]])


# ---------------------------------------------------------------------------
# save_model / load_model round trip - the train/inference contract
# ---------------------------------------------------------------------------
class TestModelPersistence:
    def test_round_trip_preserves_feature_columns(self, tmp_path):
        X = np.random.RandomState(0).rand(50, 3)
        model = train_isolation_forest(X)
        cols = ["f1", "f2", "f3"]

        model_path = tmp_path / "test_model.pkl"
        save_model(model_path, model, cols, "isolation_forest", training_rows=50)

        bundle = load_model(model_path)
        assert bundle["feature_columns"] == cols
        assert bundle["model_type"] == "isolation_forest"
        assert bundle["training_rows"] == 50
        assert "trained_at" in bundle

    def test_extra_meta_preserved(self, tmp_path):
        X = np.random.RandomState(0).rand(20, 2)
        model = train_isolation_forest(X)
        model_path = tmp_path / "test_model.pkl"
        save_model(model_path, model, ["a", "b"], "isolation_forest", 20,
                   extra_meta={"scope": "per_device", "device_ip": "10.0.0.5"})

        bundle = load_model(model_path)
        assert bundle["scope"] == "per_device"
        assert bundle["device_ip"] == "10.0.0.5"

    def test_loaded_model_can_score(self, tmp_path):
        rng = np.random.RandomState(0)
        X = rng.rand(100, 2)
        model = train_isolation_forest(X)
        cols = ["a", "b"]
        model_path = tmp_path / "test_model.pkl"
        save_model(model_path, model, cols, "isolation_forest", 100)

        bundle = load_model(model_path)
        df = pd.DataFrame({"a": [0.5, 0.5], "b": [0.5, 0.5]})
        scores = score_isolation_forest(bundle, df)
        assert scores.shape == (2,)
        assert (scores >= 0).all() and (scores <= 1).all()

    def test_score_with_missing_feature_column(self, tmp_path):
        """If live data is missing a column the model was trained on,
        scoring should still work (filled with 0), not crash."""
        rng = np.random.RandomState(0)
        X = rng.rand(50, 3)
        model = train_isolation_forest(X)
        cols = ["a", "b", "c"]
        model_path = tmp_path / "test_model.pkl"
        save_model(model_path, model, cols, "isolation_forest", 50)

        bundle = load_model(model_path)
        df = pd.DataFrame({"a": [0.5], "b": [0.5]})  # missing "c"
        scores = score_isolation_forest(bundle, df)
        assert scores.shape == (1,)


# ---------------------------------------------------------------------------
# split_train_eval
# ---------------------------------------------------------------------------
class TestSplitTrainEval:
    def test_time_aware_split_with_window(self):
        df = pd.DataFrame({
            "window": list(range(100)),
            "value": list(range(100)),
        })
        train_df, eval_df = split_train_eval(df, eval_fraction=0.2)
        assert len(eval_df) == 20
        assert len(train_df) == 80
        # eval set should be the LAST 20 windows (most "future-like")
        assert eval_df["window"].min() == 80
        assert train_df["window"].max() == 79

    def test_fallback_random_split_without_window(self):
        df = pd.DataFrame({"value": list(range(50))})
        train_df, eval_df = split_train_eval(df, eval_fraction=0.2)
        assert len(eval_df) == 10
        assert len(train_df) == 40


# ---------------------------------------------------------------------------
# write_normalization_stats_slice
# ---------------------------------------------------------------------------
class TestNormalizationStatsSlice:
    def test_creates_new_file(self, tmp_path):
        stats_path = tmp_path / "normalization_stats.json"
        write_normalization_stats_slice(stats_path, "bandwidth", {"10.0.0.5": {"mean": 1.0}})

        import json
        with open(stats_path) as f:
            data = json.load(f)
        assert data == {"bandwidth": {"10.0.0.5": {"mean": 1.0}}}

    def test_preserves_other_keys(self, tmp_path):
        stats_path = tmp_path / "normalization_stats.json"
        write_normalization_stats_slice(stats_path, "bandwidth", {"10.0.0.5": {"mean": 1.0}})
        write_normalization_stats_slice(stats_path, "device_behavior", {"10.0.0.6": {"mean": 2.0}})

        import json
        with open(stats_path) as f:
            data = json.load(f)
        assert "bandwidth" in data
        assert "device_behavior" in data

    def test_overwrites_only_its_own_key(self, tmp_path):
        stats_path = tmp_path / "normalization_stats.json"
        write_normalization_stats_slice(stats_path, "bandwidth", {"10.0.0.5": {"mean": 1.0}})
        write_normalization_stats_slice(stats_path, "bandwidth", {"10.0.0.5": {"mean": 2.0}})

        import json
        with open(stats_path) as f:
            data = json.load(f)
        assert data["bandwidth"]["10.0.0.5"]["mean"] == 2.0


# ---------------------------------------------------------------------------
# evaluate_models gating
# ---------------------------------------------------------------------------
class TestEvaluateDetector:
    @pytest.fixture
    def models_dir(self, tmp_path):
        d = tmp_path / "models"
        d.mkdir()
        return d

    @pytest.fixture
    def processed_dir(self, tmp_path):
        d = tmp_path / "processed"
        d.mkdir()
        return d

    def _make_feat_df(self, n=100, seed=0):
        rng = np.random.RandomState(seed)
        return pd.DataFrame({
            "device_ip": ["10.0.0.1"] * n,
            "window": list(range(n)),
            "bw_in_bytes": rng.rand(n) * 1000,
            "bw_out_bytes": rng.rand(n) * 1000,
        })

    def test_missing_model_file_fails(self, models_dir, processed_dir):
        result = evaluate_detector("bandwidth", models_dir, processed_dir)
        assert not result.passed
        assert any("not found" in m for m in result.messages)

    def test_missing_features_file_fails(self, models_dir, processed_dir):
        feat = self._make_feat_df()
        X = to_matrix(feat, ["bw_in_bytes", "bw_out_bytes"])
        model = train_isolation_forest(X)
        save_model(models_dir / "bandwidth_model.pkl", model,
                   ["bw_in_bytes", "bw_out_bytes"], "isolation_forest", len(feat))

        result = evaluate_detector("bandwidth", models_dir, processed_dir)
        assert not result.passed
        assert any("features file not found" in m for m in result.messages)

    def test_healthy_model_passes(self, models_dir, processed_dir):
        feat = self._make_feat_df(n=200)
        cols = ["bw_in_bytes", "bw_out_bytes"]
        X = to_matrix(feat, cols)
        model = train_isolation_forest(X)
        save_model(models_dir / "bandwidth_model.pkl", model, cols, "isolation_forest", len(feat))
        feat.to_csv(processed_dir / "bandwidth_features.csv", index=False)

        result = evaluate_detector("bandwidth", models_dir, processed_dir)
        assert result.passed

    def test_degenerate_scores_fail(self, models_dir, processed_dir):
        """A model whose decision_function is constant across all eval rows
        should fail evaluation."""
        feat = self._make_feat_df(n=50)
        cols = ["bw_in_bytes", "bw_out_bytes"]

        # Build a fake bundle whose model always returns the same score
        bundle = {
            "model": ConstantModel(),
            "feature_columns": cols,
            "model_type": "isolation_forest",
            "trained_at": 0,
            "training_rows": len(feat),
        }
        import joblib
        joblib.dump(bundle, models_dir / "bandwidth_model.pkl")
        feat.to_csv(processed_dir / "bandwidth_features.csv", index=False)

        result = evaluate_detector("bandwidth", models_dir, processed_dir)
        assert not result.passed
        assert any("identical" in m for m in result.messages)

    def test_unknown_model_type_fails(self, models_dir, processed_dir):
        feat = self._make_feat_df(n=50)
        cols = ["bw_in_bytes", "bw_out_bytes"]
        X = to_matrix(feat, cols)
        model = train_isolation_forest(X)
        bundle_path = models_dir / "bandwidth_model.pkl"
        save_model(bundle_path, model, cols, "some_unknown_type", len(feat))
        feat.to_csv(processed_dir / "bandwidth_features.csv", index=False)

        result = evaluate_detector("bandwidth", models_dir, processed_dir)
        assert not result.passed
        assert any("Unknown model_type" in m for m in result.messages)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
