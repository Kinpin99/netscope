import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from training.common import load_model, score_isolation_forest, split_train_eval, to_matrix
from utils.config_loader import load_config

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [evaluate] %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# Detector name - (model filename, processed features filename)
DETECTOR_FILES = {
    "bandwidth":       ("bandwidth_model.pkl", "bandwidth_features.csv"),
    "portscan":        ("portscan_model.pkl", "portscan_features.csv"),
    "device_behavior": ("device_model.pkl", "device_behavior_features.csv"),
    "protocol":        ("protocol_model.pkl", "protocol_features.csv"),
}


MIN_RF_EVAL_ACCURACY = 0.6


class EvalResult:
    def __init__(self, detector: str):
        self.detector = detector
        self.passed = True
        self.messages = []

    def fail(self, msg: str):
        self.passed = False
        self.messages.append(f"FAIL: {msg}")
        log.error("[%s] %s", self.detector, msg)

    def warn(self, msg: str):
        self.messages.append(f"WARN: {msg}")
        log.warning("[%s] %s", self.detector, msg)

    def info(self, msg: str):
        self.messages.append(f"INFO: {msg}")
        log.info("[%s] %s", self.detector, msg)


def evaluate_isolation_forest(detector: str, bundle: dict, feat: pd.DataFrame) -> EvalResult:
    result = EvalResult(detector)

    _, eval_df = split_train_eval(feat)
    if eval_df.empty:
        result.warn("Evaluation split is empty (too little data) - skipping score checks")
        return result

    try:
        scores = score_isolation_forest(bundle, eval_df)
    except Exception as exc:
        result.fail(f"Scoring raised an exception: {exc}")
        return result

    if np.isnan(scores).any():
        result.fail("Evaluation scores contain NaN")
        return result

    if scores.min() < 0 or scores.max() > 1:
        result.fail(f"Evaluation scores out of [0,1] range: min={scores.min():.4f} max={scores.max():.4f}")
        return result

    if np.allclose(scores, scores[0]):
        result.fail(
            f"All evaluation scores are identical ({scores[0]:.4f}) - "
            "model may not have learned a meaningful decision boundary"
        )
        return result

    result.info(
        f"Eval scores OK: n={len(scores)}, mean={scores.mean():.4f}, "
        f"std={scores.std():.4f}, min={scores.min():.4f}, max={scores.max():.4f}"
    )
    return result


def evaluate_random_forest(detector: str, bundle: dict, feat: pd.DataFrame) -> EvalResult:
    result = EvalResult(detector)

    if "label" not in feat.columns:
        result.warn("Random Forest model but no 'label' column in features - cannot compute eval accuracy")
        return result

    _, eval_df = split_train_eval(feat)
    if eval_df.empty:
        result.warn("Evaluation split is empty - skipping accuracy check")
        return result

    X_eval = to_matrix(eval_df, bundle["feature_columns"])
    y_eval = eval_df["label"].to_numpy()

    try:
        acc = bundle["model"].score(X_eval, y_eval)
    except Exception as exc:
        result.fail(f"Scoring raised an exception: {exc}")
        return result

    if acc < MIN_RF_EVAL_ACCURACY:
        result.fail(f"Eval accuracy {acc:.4f} below minimum threshold {MIN_RF_EVAL_ACCURACY}")
        return result

    result.info(f"Eval accuracy OK: {acc:.4f} (n={len(eval_df)})")
    return result


def evaluate_detector(detector: str, models_dir: Path, processed_dir: Path) -> EvalResult:
    model_file, features_file = DETECTOR_FILES[detector]
    model_path = models_dir / model_file
    features_path = processed_dir / features_file

    result = EvalResult(detector)

    if not model_path.exists():
        result.fail(f"Model file not found: {model_path}")
        return result

    if not features_path.exists():
        result.fail(f"Processed features file not found: {features_path}")
        return result

    try:
        bundle = load_model(model_path)
    except Exception as exc:
        result.fail(f"Failed to load model bundle: {exc}")
        return result

    required_keys = {"model", "feature_columns", "model_type"}
    missing = required_keys - set(bundle.keys())
    if missing:
        result.fail(f"Model bundle missing required keys: {missing}")
        return result

    feat = pd.read_csv(features_path)
    if feat.empty:
        result.fail("Processed features file is empty")
        return result


    missing_cols = [c for c in bundle["feature_columns"] if c not in feat.columns]
    if missing_cols:
        result.fail(
            f"Model expects feature columns not present in current "
            f"processed features: {missing_cols}. This usually means "
            f"unified_preprocessing.py changed its output columns since "
            f"this model was trained."
        )
        return result

    if bundle["model_type"] == "isolation_forest":
        sub_result = evaluate_isolation_forest(detector, bundle, feat)
    elif bundle["model_type"] == "random_forest":
        sub_result = evaluate_random_forest(detector, bundle, feat)
    else:
        result.fail(f"Unknown model_type: {bundle['model_type']}")
        return result

    result.passed = result.passed and sub_result.passed
    result.messages.extend(sub_result.messages)
    return result


def main():
    parser = argparse.ArgumentParser(description="Evaluate newly trained models before promotion")
    parser.add_argument("--detectors", nargs="+", default=list(DETECTOR_FILES.keys()),
                        choices=list(DETECTOR_FILES.keys()))
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)
    models_dir = cfg["paths"]["models_dir"]
    processed_dir = cfg["paths"]["processed_dir"]

    all_passed = True
    for detector in args.detectors:
        log.info("=== Evaluating %s ===", detector)
        result = evaluate_detector(detector, models_dir, processed_dir)
        if not result.passed:
            all_passed = False
            log.error("[%s] EVALUATION FAILED", detector)
        else:
            log.info("[%s] EVALUATION PASSED", detector)

    if not all_passed:
        log.error("One or more models failed evaluation. Promotion should be aborted.")
        sys.exit(1)

    log.info("All evaluated models passed.")


if __name__ == "__main__":
    main()
