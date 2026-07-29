"""
training/common.py
--------------------
Shared utilities for the four train_*.py scripts.

Key design decisions:

  - Models are Isolation Forest by default (unsupervised - matches the
    NOTE in anomaly_features.txt that only Random Forest and Isolation
    Forest will be used, and IF doesn't require labelled anomalies).
    A Random Forest path is also provided for detectors where labelled
    data becomes available later (e.g. from CICIDS/UNSW-NB15 or synthetic
    attacks), selectable via --model-type.

  - Every saved model is a dict: {"model": <sklearn estimator>,
    "feature_columns": [...], "model_type": "isolation_forest"|"random_forest",
    "trained_at": <epoch>, "training_rows": N}
    The feature_columns list is saved alongside the model because live
    inference MUST build its feature vector in this exact column order -
    sklearn estimators have no awareness of column names, only column
    position. Storing this list here (rather than relying on
    unified_preprocessing's output column order matching forever) is the
    safeguard against silent training/inference feature misalignment.

  - Identifier columns (device_ip, src_ip, window, etc.) are excluded from
    the feature matrix automatically via FEATURE_EXCLUDE_COLS / the
    *_output_columns() ID prefixes.
"""

import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.model_selection import train_test_split

# Columns that identify a row (device/window/source) rather than describe
# its behavior - never fed to the model.
ID_COLUMNS = {"device_ip", "src_ip", "window", "label"}


def feature_columns(df: pd.DataFrame) -> List[str]:
    """All numeric columns except identifier columns, in DataFrame order."""
    cols = []
    for c in df.columns:
        if c in ID_COLUMNS:
            continue
        if pd.api.types.is_numeric_dtype(df[c]):
            cols.append(c)
    return cols


def to_matrix(df: pd.DataFrame, columns: List[str]) -> np.ndarray:
    """
    Build a numeric feature matrix from df using exactly `columns`, in that
    order. Missing columns are filled with 0 (e.g. a live inference batch
    that happens to have no flows of a particular protocol, producing a
    feature set with fewer columns than training saw).
    """
    out = pd.DataFrame(index=df.index)
    for c in columns:
        out[c] = df[c] if c in df.columns else 0.0
    return out.fillna(0).to_numpy(dtype=float)


def train_isolation_forest(
    X: np.ndarray,
    contamination: float = "auto",
    n_estimators: int = 200,
    random_state: int = 42,
) -> IsolationForest:
    model = IsolationForest(
        n_estimators=n_estimators,
        contamination=contamination,
        random_state=random_state,
        n_jobs=-1,
    )
    model.fit(X)
    return model


def train_random_forest(
    X: np.ndarray,
    y: np.ndarray,
    n_estimators: int = 200,
    random_state: int = 42,
) -> RandomForestClassifier:
    model = RandomForestClassifier(
        n_estimators=n_estimators,
        random_state=random_state,
        n_jobs=-1,
        class_weight="balanced",
    )
    model.fit(X, y)
    return model


def save_model(
    path: Path,
    model,
    feature_cols: List[str],
    model_type: str,
    training_rows: int,
    extra_meta: Optional[dict] = None,
) -> None:
    """
    Save model + the metadata live inference needs to use it correctly.
    `feature_cols` is the single most important field here - it pins the
    exact column order the model expects.
    """
    bundle = {
        "model": model,
        "feature_columns": feature_cols,
        "model_type": model_type,
        "trained_at": time.time(),
        "training_rows": training_rows,
    }
    if extra_meta:
        bundle.update(extra_meta)

    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, path)


def load_model(path: Path) -> dict:
    """Load a model bundle saved by save_model()."""
    return joblib.load(path)


def score_isolation_forest(bundle: dict, df: pd.DataFrame) -> np.ndarray:
    """
    Return anomaly scores in [0, 1], where higher = more anomalous.

    sklearn's IsolationForest.score_samples returns higher values for
    *normal* points (less negative) and lower (more negative) for
    anomalies. We flip and rescale via decision_function so that the
    output is intuitive for the alert engine: 0 = normal, 1 = anomalous.
    """
    X = to_matrix(df, bundle["feature_columns"])
    model: IsolationForest = bundle["model"]
    # decision_function: positive = normal (inlier), negative = anomaly
    raw = model.decision_function(X)
    # Map roughly [-0.5, 0.5] -> [1, 0] via a clipped linear transform.
    scores = np.clip(0.5 - raw, 0, 1)
    return scores


def split_train_eval(
    df: pd.DataFrame,
    eval_fraction: float = 0.2,
    random_state: int = 42,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Time-aware split: the last eval_fraction of rows (by `window`, if
    present) become the evaluation set, rest is training. This is more
    realistic than a random split for time-series anomaly data - it
    evaluates the model on the period that most resembles "the future"
    relative to training, similar to how live inference will behave.

    Falls back to a random split if `window` isn't present.
    """
    if "window" in df.columns:
        df_sorted = df.sort_values("window")
        n_eval = max(1, int(len(df_sorted) * eval_fraction))
        train_df = df_sorted.iloc[:-n_eval]
        eval_df = df_sorted.iloc[-n_eval:]
        return train_df, eval_df

    return train_test_split(df, test_size=eval_fraction, random_state=random_state)


def write_normalization_stats_slice(
    stats_path: Path,
    key: str,
    slice_stats: Dict[str, Dict[str, float]],
) -> None:
    """
    Merge `slice_stats` under `stats[key]` into normalization_stats.json,
    preserving other detectors' entries. Each train_*.py script calls this
    for its own detector slice ("bandwidth", "device_behavior", etc.)
    rather than overwriting the whole file, since multiple training scripts
    run in the same pipeline and each only knows its own slice.
    """
    stats = {}
    if stats_path.exists():
        with open(stats_path) as f:
            stats = json.load(f)

    stats[key] = slice_stats

    stats_path.parent.mkdir(parents=True, exist_ok=True)
    with open(stats_path, "w") as f:
        json.dump(stats, f, indent=2)
