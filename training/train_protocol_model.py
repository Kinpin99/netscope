import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from preprocessing.unified_preprocessing import (
    PROTOCOL_TCP, PROTOCOL_UDP, PROTOCOL_ICMP,
    ProtocolFeatures,
    _assign_device_ip,
    _assign_window,
    _load_netflow,
)
from training.common import (
    feature_columns,
    save_model,
    split_train_eval,
    to_matrix,
    train_isolation_forest,
)
from utils.config_loader import load_config

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [train-protocol] %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def compute_protocol_baseline(nf: pd.DataFrame) -> pd.DataFrame:
    """
    Compute each device's overall protocol distribution (tcp/udp/icmp ratio)
    across the full training period.
    """
    nf = _assign_window(nf, 60)
    nf = _assign_device_ip(nf)

    rows = []
    for device_ip, grp in nf.groupby("device_ip"):
        n = len(grp)
        if n == 0:
            continue
        for proto, label in [(PROTOCOL_TCP, "tcp"), (PROTOCOL_UDP, "udp"), (PROTOCOL_ICMP, "icmp")]:
            ratio = (grp["protocol"] == proto).sum() / n
            rows.append({"device_ip": device_ip, "protocol": proto, "ratio": round(ratio, 6)})

    return pd.DataFrame(rows, columns=["device_ip", "protocol", "ratio"])


def main():
    parser = argparse.ArgumentParser(description="Train the protocol anomaly detector")
    parser.add_argument("--netflow", default=None, help="NetFlow CSV or directory (default: from config.yaml)")
    parser.add_argument("--config", default=None)
    parser.add_argument("--contamination", default="auto")
    args = parser.parse_args()

    cfg = load_config(args.config)
    netflow_path = args.netflow or str(cfg["paths"]["netflow_raw_dir"])
    processed_dir = cfg["paths"]["processed_dir"]
    models_dir = cfg["paths"]["models_dir"]
    baseline_path = processed_dir / "protocol_baseline.csv"

    log.info("Loading NetFlow from %s", netflow_path)
    nf_raw = _load_netflow(netflow_path)
    if nf_raw.empty:
        log.error("No NetFlow data found - check %s", netflow_path)
        sys.exit(1)

    if baseline_path.exists():
        log.info("Existing baseline found at %s - computing features against it", baseline_path)
    else:
        log.info("No existing baseline (first run) - kl_div/num_new_protocols will be 0 for this training pass")

    # Compute features using whatever baseline currently exists
    feat = ProtocolFeatures.from_csv(
        netflow_path,
        baseline_csv=str(baseline_path) if baseline_path.exists() else None,
    )
    log.info("Computed %d feature rows across %d devices",
             len(feat), feat["device_ip"].nunique() if not feat.empty else 0)

    if feat.empty:
        log.error("No protocol features computed - check NetFlow data availability.")
        sys.exit(1)

    processed_dir.mkdir(parents=True, exist_ok=True)
    feat_path = processed_dir / "protocol_features.csv"
    feat.to_csv(feat_path, index=False)
    log.info("Saved processed features -> %s", feat_path)

    # Train model
    contamination = args.contamination
    if contamination != "auto":
        contamination = float(contamination)

    train_df, eval_df = split_train_eval(feat)
    cols = feature_columns(train_df)
    log.info("Feature columns (%d): %s", len(cols), cols)

    X_train = to_matrix(train_df, cols)
    model = train_isolation_forest(X_train, contamination=contamination)

    model_path = models_dir / "protocol_model.pkl"
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


    new_baseline = compute_protocol_baseline(nf_raw)
    new_baseline.to_csv(baseline_path, index=False)
    log.info("Refreshed protocol baseline -> %s (%d device/protocol rows)", baseline_path, len(new_baseline))


if __name__ == "__main__":
    main()
