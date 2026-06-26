import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from preprocessing.unified_preprocessing import (
    BandwidthFeatures,
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
logging.basicConfig(level=logging.INFO, format="%(asctime)s [train-bandwidth] %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Train the bandwidth spike detector")
    parser.add_argument("--netflow", default=None, help="NetFlow CSV or directory (default: from config.yaml)")
    parser.add_argument("--prtg", default=None, help="PRTG/SNMP CSV or directory (default: from config.yaml)")
    parser.add_argument("--config", default=None)
    parser.add_argument("--contamination", default="auto",
                        help="IsolationForest contamination ('auto' or a float like 0.01)")
    args = parser.parse_args()

    cfg = load_config(args.config)
    netflow_path = args.netflow or str(cfg["paths"]["netflow_raw_dir"])
    prtg_path = args.prtg or str(cfg["paths"]["prtg_raw_dir"])
    processed_dir = cfg["paths"]["processed_dir"]
    models_dir = cfg["paths"]["models_dir"]

    log.info("Loading NetFlow from %s, PRTG from %s", netflow_path, prtg_path)
    feat = BandwidthFeatures.from_csv(netflow_path, prtg_path)
    log.info("Computed %d feature rows across %d devices",
             len(feat), feat["device_ip"].nunique() if not feat.empty else 0)

    if feat.empty:
        log.error("No bandwidth features computed - check NetFlow/PRTG data availability.")
        sys.exit(1)

    # Save processed features for inspection / reuse
    processed_dir.mkdir(parents=True, exist_ok=True)
    feat_path = processed_dir / "bandwidth_features.csv"
    feat.to_csv(feat_path, index=False)
    log.info("Saved processed features -> %s", feat_path)

    # Train / eval split
    train_df, eval_df = split_train_eval(feat)
    cols = feature_columns(train_df)
    log.info("Feature columns (%d): %s", len(cols), cols)

    contamination = args.contamination
    if contamination != "auto":
        contamination = float(contamination)

    X_train = to_matrix(train_df, cols)
    model = train_isolation_forest(X_train, contamination=contamination)

    model_path = models_dir / "bandwidth_model.pkl"
    save_model(
        model_path, model, cols, "isolation_forest",
        training_rows=len(train_df),
        extra_meta={"contamination": contamination},
    )
    log.info("Saved model -> %s (trained on %d rows)", model_path, len(train_df))

    # Held-out eval summary - evaluate_models.py does the gating
    if not eval_df.empty:
        X_eval = to_matrix(eval_df, cols)
        eval_scores = model.decision_function(X_eval)
        log.info(
            "Eval set (%d rows): decision_function mean=%.4f, min=%.4f, max=%.4f",
            len(eval_df), eval_scores.mean(), eval_scores.min(), eval_scores.max(),
        )

    # Normalization stats, used by from_stream's live z-score columns
    stats = compute_normalization_stats(feat, "device_ip", BandwidthFeatures.ZSCORE_VALUE_COLS)
    stats_path = models_dir / "normalization_stats.json"
    write_normalization_stats_slice(stats_path, "bandwidth", stats)
    log.info("Updated normalization stats -> %s [bandwidth]", stats_path)


if __name__ == "__main__":
    main()
