"""
train_device_model.py
-----------------------
Trains the Device Behavior / Unknown Device Detector model.

Pipeline:
    netflow_raw + prtg_raw  --[unified_preprocessing.DeviceBehaviorFeatures]-->
    device_behavior_features.csv  --[IsolationForest]--> device_model.pkl
                                   --[compute_normalization_stats]--> normalization_stats.json["device_behavior"]

Two modes:

  --mode global (default)
      One model trained across all devices. This is the model loaded by
      the live ensemble_detector for devices that don't have their own
      per-device baseline.

  --mode per-device --device-ip <ip>
      Trains a dedicated model for a single device, saved as
      data/models/device_profiles/<ip>_model.pkl, plus a per-device slice
      of normalization_stats.json under
      normalization_stats.json["device_behavior_profiles"][<ip>].

      This implements must_add_to_project.txt item 6: "on user request,
      can create a 'normal baseline' for a particular device". The
      orchestrator/API calls this mode when an admin requests a baseline
      for a specific device (typically after a >=24h observation window
      for that device specifically).

      If a per-device model exists for a device, the live ensemble
      detector should prefer it over the global model for that device's
      rows - see profiling/profile_updater.py.

Usage:
    python training/train_device_model.py
    python training/train_device_model.py --mode per-device --device-ip 10.0.0.5
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from preprocessing.unified_preprocessing import (
    DeviceBehaviorFeatures,
    compute_normalization_stats,
)
from training.common import (
    feature_columns,
    save_model,
    split_train_eval,
    to_matrix,
    train_isolation_forest,
    write_normalization_stats_slice,
)
from utils.config_loader import load_config

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [train-device] %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Train the device behavior detector")
    parser.add_argument("--netflow", default=None, help="NetFlow CSV or directory (default: from config.yaml)")
    parser.add_argument("--prtg", default=None, help="PRTG/SNMP CSV or directory (default: from config.yaml)")
    parser.add_argument("--config", default=None)
    parser.add_argument("--contamination", default="auto")
    parser.add_argument("--mode", choices=["global", "per-device"], default="global")
    parser.add_argument("--device-ip", default=None,
                        help="Required for --mode per-device: build a dedicated baseline for this device")
    args = parser.parse_args()

    if args.mode == "per-device" and not args.device_ip:
        parser.error("--mode per-device requires --device-ip")

    cfg = load_config(args.config)
    netflow_path = args.netflow or str(cfg["paths"]["netflow_raw_dir"])
    prtg_path = args.prtg or str(cfg["paths"]["prtg_raw_dir"])
    processed_dir = cfg["paths"]["processed_dir"]
    models_dir = cfg["paths"]["models_dir"]

    log.info("Loading NetFlow from %s, PRTG from %s", netflow_path, prtg_path)
    feat = DeviceBehaviorFeatures.from_csv(netflow_path, prtg_path)
    log.info("Computed %d feature rows across %d devices",
             len(feat), feat["device_ip"].nunique() if not feat.empty else 0)

    if feat.empty:
        log.error("No device-behavior features computed - check NetFlow/PRTG data availability.")
        sys.exit(1)

    contamination = args.contamination
    if contamination != "auto":
        contamination = float(contamination)

    if args.mode == "global":
        _train_global(feat, processed_dir, models_dir, contamination)
    else:
        _train_per_device(feat, args.device_ip, models_dir, contamination)


def _train_global(feat: pd.DataFrame, processed_dir: Path, models_dir: Path, contamination) -> None:
    processed_dir.mkdir(parents=True, exist_ok=True)
    feat_path = processed_dir / "device_behavior_features.csv"
    feat.to_csv(feat_path, index=False)
    log.info("Saved processed features -> %s", feat_path)

    train_df, eval_df = split_train_eval(feat)
    cols = feature_columns(train_df)
    log.info("Feature columns (%d): %s", len(cols), cols)

    X_train = to_matrix(train_df, cols)
    model = train_isolation_forest(X_train, contamination=contamination)

    model_path = models_dir / "device_model.pkl"
    save_model(
        model_path, model, cols, "isolation_forest",
        training_rows=len(train_df),
        extra_meta={"contamination": contamination, "scope": "global"},
    )
    log.info("Saved global model -> %s (trained on %d rows)", model_path, len(train_df))

    if not eval_df.empty:
        X_eval = to_matrix(eval_df, cols)
        eval_scores = model.decision_function(X_eval)
        log.info(
            "Eval set (%d rows): decision_function mean=%.4f, min=%.4f, max=%.4f",
            len(eval_df), eval_scores.mean(), eval_scores.min(), eval_scores.max(),
        )

    stats = compute_normalization_stats(feat, "device_ip", DeviceBehaviorFeatures.ZSCORE_VALUE_COLS)
    stats_path = models_dir / "normalization_stats.json"
    write_normalization_stats_slice(stats_path, "device_behavior", stats)
    log.info("Updated normalization stats -> %s [device_behavior]", stats_path)


def _train_per_device(feat: pd.DataFrame, device_ip: str, models_dir: Path, contamination) -> None:
    """
    must_add_to_project.txt item 6: on-request "normal baseline" for one device.

    Trains a dedicated Isolation Forest on only this device's historical
    windows. The live ensemble detector (profiling/profile_updater.py)
    checks for data/models/device_profiles/<ip>_model.pkl and, if present,
    scores that device's rows with this model instead of the global one -
    giving a tighter, device-specific notion of "normal".
    """
    dev_feat = feat[feat["device_ip"] == device_ip]
    if dev_feat.empty:
        log.error("No feature rows found for device_ip=%s. Has it been observed yet?", device_ip)
        sys.exit(1)

    log.info("Training per-device baseline for %s on %d rows", device_ip, len(dev_feat))

    if len(dev_feat) < 30:
        log.warning(
            "Only %d windows of history for %s (< 30 minutes). The baseline "
            "will be low-confidence - consider waiting for more observation "
            "time before relying on alerts from this profile.",
            len(dev_feat), device_ip,
        )

    train_df, eval_df = split_train_eval(dev_feat)
    cols = feature_columns(train_df)

    X_train = to_matrix(train_df, cols)
    model = train_isolation_forest(X_train, contamination=contamination)

    profiles_dir = models_dir / "device_profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    safe_name = device_ip.replace(".", "_").replace(":", "_")
    model_path = profiles_dir / f"{safe_name}_model.pkl"

    save_model(
        model_path, model, cols, "isolation_forest",
        training_rows=len(train_df),
        extra_meta={
            "contamination": contamination,
            "scope": "per_device",
            "device_ip": device_ip,
            "low_confidence": len(dev_feat) < 30,
        },
    )
    log.info("Saved per-device model -> %s (trained on %d rows)", model_path, len(train_df))

    # Per-device normalization stats, stored separately from the global
    # device_behavior stats so a per-device profile doesn't get overwritten
    # by the next global retrain.
    dev_stats = compute_normalization_stats(dev_feat, "device_ip", DeviceBehaviorFeatures.ZSCORE_VALUE_COLS)

    stats_path = models_dir / "normalization_stats.json"
    stats = {}
    if stats_path.exists():
        with open(stats_path) as f:
            stats = json.load(f)
    stats.setdefault("device_behavior_profiles", {})[device_ip] = dev_stats.get(device_ip, {})
    with open(stats_path, "w") as f:
        json.dump(stats, f, indent=2)
    log.info("Updated normalization stats -> %s [device_behavior_profiles][%s]", stats_path, device_ip)


if __name__ == "__main__":
    main()
