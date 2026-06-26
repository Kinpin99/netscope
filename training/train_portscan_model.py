import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from preprocessing.unified_preprocessing import PortScanFeatures
from training.common import (
    feature_columns,
    save_model,
    split_train_eval,
    to_matrix,
    train_isolation_forest,
    train_random_forest,
)
from utils.config_loader import load_config

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [train-portscan] %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Train the port scan detector")
    parser.add_argument("--netflow", default=None, help="NetFlow CSV or directory (default: from config.yaml)")
    parser.add_argument("--labelled-csv", default=None,
                        help="Optional separate labelled dataset (same feature schema + 'label' column) "
                             "to train a Random Forest instead of Isolation Forest")
    parser.add_argument("--config", default=None)
    parser.add_argument("--contamination", default="auto")
    args = parser.parse_args()

    cfg = load_config(args.config)
    netflow_path = args.netflow or str(cfg["paths"]["netflow_raw_dir"])
    processed_dir = cfg["paths"]["processed_dir"]
    models_dir = cfg["paths"]["models_dir"]

    log.info("Loading NetFlow from %s", netflow_path)
    feat = PortScanFeatures.from_csv(netflow_path)
    log.info("Computed %d feature rows across %d source IPs",
             len(feat), feat["src_ip"].nunique() if not feat.empty else 0)

    if feat.empty:
        log.error("No portscan features computed - check NetFlow data availability.")
        sys.exit(1)

    processed_dir.mkdir(parents=True, exist_ok=True)
    feat_path = processed_dir / "portscan_features.csv"
    feat.to_csv(feat_path, index=False)
    log.info("Saved processed features -> %s", feat_path)

    labelled_df = None
    if args.labelled_csv:
        labelled_df = pd.read_csv(args.labelled_csv)
        if "label" not in labelled_df.columns:
            log.error("--labelled-csv provided but no 'label' column found")
            sys.exit(1)
        log.info("Loaded %d labelled rows from %s", len(labelled_df), args.labelled_csv)
    elif "label" in feat.columns:
        labelled_df = feat

    contamination = args.contamination
    if contamination != "auto":
        contamination = float(contamination)

    if labelled_df is not None:
        # Supervised: Random Forest
        train_df, eval_df = split_train_eval(labelled_df)
        cols = feature_columns(train_df)
        log.info("Training Random Forest with feature columns (%d): %s", len(cols), cols)

        X_train = to_matrix(train_df, cols)
        y_train = train_df["label"].to_numpy()
        model = train_random_forest(X_train, y_train)

        model_path = models_dir / "portscan_model.pkl"
        save_model(model_path, model, cols, "random_forest", training_rows=len(train_df))
        log.info("Saved model -> %s (trained on %d labelled rows)", model_path, len(train_df))

        if not eval_df.empty:
            X_eval = to_matrix(eval_df, cols)
            y_eval = eval_df["label"].to_numpy()
            acc = model.score(X_eval, y_eval)
            log.info("Eval accuracy: %.4f (%d rows)", acc, len(eval_df))

    else:
        # Unsupervised: Isolation Forest
        train_df, eval_df = split_train_eval(feat)
        cols = feature_columns(train_df)
        log.info("Training Isolation Forest with feature columns (%d): %s", len(cols), cols)

        X_train = to_matrix(train_df, cols)
        model = train_isolation_forest(X_train, contamination=contamination)

        model_path = models_dir / "portscan_model.pkl"
        save_model(
            model_path, model, cols, "isolation_forest",
            training_rows=len(train_df),
            extra_meta={"contamination": contamination},
        )
        log.info("Saved model -> %s (trained on %d rows)", model_path, len(train_df))

        if not eval_df.empty:
            X_eval = to_matrix(eval_df, cols)
            eval_scores = model.decision_function(X_eval)
            log.info(
                "Eval set (%d rows): decision_function mean=%.4f, min=%.4f, max=%.4f",
                len(eval_df), eval_scores.mean(), eval_scores.min(), eval_scores.max(),
            )


if __name__ == "__main__":
    main()
