import json
import sys
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from preprocessing.unified_preprocessing import (
    BandwidthFeatures,
    DeviceBehaviorFeatures,
    PortScanFeatures,
    ProtocolFeatures,
    _load_baseline,
)
from training.common import load_model, score_isolation_forest, to_matrix

import logging
log = logging.getLogger(__name__)



# Model bundle
class ModelBundle:

    def __init__(self, models_dir: Path, processed_dir: Path):
        self.models_dir = Path(models_dir)
        self.processed_dir = Path(processed_dir)

        self.models: Dict[str, Optional[dict]] = {}
        for detector, filename in [
            ("bandwidth", "bandwidth_model.pkl"),
            ("portscan", "portscan_model.pkl"),
            ("device_behavior", "device_model.pkl"),
            ("protocol", "protocol_model.pkl"),
        ]:
            path = self.models_dir / filename
            if path.exists():
                self.models[detector] = load_model(path)
            else:
                self.models[detector] = None
                log.warning("%s not found - %s detector will be skipped", path, detector)

        self.normalization_stats = self._load_stats()
        self.protocol_baseline = _load_baseline(str(self.processed_dir / "protocol_baseline.csv"))
        self.device_profiles = self._load_device_profiles()

    def _load_stats(self) -> dict:
        path = self.models_dir / "normalization_stats.json"
        if not path.exists():
            return {}
        with open(path) as f:
            return json.load(f)

    def _load_device_profiles(self) -> Dict[str, dict]:
        """device_ip - model bundle, for devices with a per-device baseline."""
        profiles_dir = self.models_dir / "device_profiles"
        profiles: Dict[str, dict] = {}
        if not profiles_dir.exists():
            return profiles

        for path in profiles_dir.glob("*_model.pkl"):
            try:
                bundle = load_model(path)
            except Exception:
                log.exception("Failed loading per-device profile %s", path)
                continue
            device_ip = bundle.get("device_ip")
            if device_ip:
                profiles[device_ip] = bundle
            else:
                log.warning("Per-device profile %s missing device_ip metadata - skipping", path)

        if profiles:
            log.info("Loaded %d per-device behavior profiles: %s", len(profiles), list(profiles.keys()))
        return profiles

    def reload(self) -> "ModelBundle":
        """Re-read everything from disk - call after a training/promotion cycle."""
        return ModelBundle(self.models_dir, self.processed_dir)



# Scoring
def _score_with_bundle(bundle: Optional[dict], feat: pd.DataFrame) -> pd.Series:
    """Score feat with the given model bundle, or return NaN scores if no model."""
    if bundle is None or feat.empty:
        return pd.Series([float("nan")] * len(feat), index=feat.index)

    if bundle["model_type"] == "isolation_forest":
        return pd.Series(score_isolation_forest(bundle, feat), index=feat.index)

    if bundle["model_type"] == "random_forest":
        X = to_matrix(feat, bundle["feature_columns"])
        # probability of the positive (anomalous) class
        proba = bundle["model"].predict_proba(X)
        if proba.shape[1] == 2:
            return pd.Series(proba[:, 1], index=feat.index)
        return pd.Series(proba.max(axis=1), index=feat.index)

    log.warning("Unknown model_type %r - returning NaN scores", bundle["model_type"])
    return pd.Series([float("nan")] * len(feat), index=feat.index)


# Columns that identify a row rather than describe its behavior - excluded
_ID_COLS = {"device_ip", "src_ip", "window"}


def _row_to_feature_dict(row: pd.Series) -> dict:
    return {k: v for k, v in row.items() if k not in _ID_COLS}


def _score_device_with_profile(
    netflow_df: pd.DataFrame,
    snmp_df: pd.DataFrame,
    models: "ModelBundle",
    device_ip: str,
) -> Dict[tuple, float]:

    profile_bundle = models.device_profiles[device_ip]
    dev_stats = models.normalization_stats.get("device_behavior_profiles", {}).get(device_ip, {})

    mask = (netflow_df["src_ip"] == device_ip) | (netflow_df["dst_ip"] == device_ip)
    dev_netflow = netflow_df[mask]
    if dev_netflow.empty:
        return {}

    row_df = DeviceBehaviorFeatures.from_stream(
        dev_netflow,
        snmp_df,
        normalization_stats={"device_behavior": {device_ip: dev_stats}},
    )
    row_df = row_df[row_df["device_ip"] == device_ip]
    if row_df.empty:
        return {}

    scores = _score_with_bundle(profile_bundle, row_df)
    return {
        (device_ip, window): float(score)
        for window, score in zip(row_df["window"], scores)
    }


def _row_features(row: pd.Series, id_cols: set) -> dict:

    return {k: v for k, v in row.items() if k not in id_cols}


_BANDWIDTH_ID_COLS = {"device_ip", "window"}
_PORTSCAN_ID_COLS = {"src_ip", "window"}
_DEVICE_BEHAVIOR_ID_COLS = {"device_ip", "window"}
_PROTOCOL_ID_COLS = {"device_ip", "window"}


def score_window(
    netflow_df: pd.DataFrame,
    snmp_df: pd.DataFrame,
    models: ModelBundle,
) -> pd.DataFrame:

    results = []

    # --- Bandwidth ---
    bw_feat = BandwidthFeatures.from_stream(
        netflow_df, snmp_df, normalization_stats=models.normalization_stats
    )
    if not bw_feat.empty:
        scores = _score_with_bundle(models.models["bandwidth"], bw_feat)
        for idx, row in bw_feat.iterrows():
            results.append({
                "detector": "bandwidth",
                "entity_id": row["device_ip"],
                "window": row["window"],
                "anomaly_score": scores.loc[idx],
                "profile_used": "global",
                "features": _row_features(row, _BANDWIDTH_ID_COLS),
            })

    # --- Port scan ---
    ps_feat = PortScanFeatures.from_stream(netflow_df)
    if not ps_feat.empty:
        scores = _score_with_bundle(models.models["portscan"], ps_feat)
        for idx, row in ps_feat.iterrows():
            results.append({
                "detector": "portscan",
                "entity_id": row["src_ip"],
                "window": row["window"],
                "anomaly_score": scores.loc[idx],
                "profile_used": "global",
                "features": _row_features(row, _PORTSCAN_ID_COLS),
            })

    # --- Device behavior (with per-device profile overrides) ---
    db_feat = DeviceBehaviorFeatures.from_stream(
        netflow_df, snmp_df, normalization_stats=models.normalization_stats
    )
    if not db_feat.empty:
        global_scores = _score_with_bundle(models.models["device_behavior"], db_feat)

        # Precompute per-device profile scores ONCE per device (not per row)
        profile_devices = set(db_feat["device_ip"]) & set(models.device_profiles.keys())
        per_device_scores: Dict[tuple, float] = {}
        for device_ip in profile_devices:
            per_device_scores.update(
                _score_device_with_profile(netflow_df, snmp_df, models, device_ip)
            )

        for idx, row in db_feat.iterrows():
            device_ip = row["device_ip"]
            window = row["window"]
            score = global_scores.loc[idx]
            profile_used = "global"

            key = (device_ip, window)
            if key in per_device_scores:
                score = per_device_scores[key]
                profile_used = "per_device"

            results.append({
                "detector": "device_behavior",
                "entity_id": device_ip,
                "window": window,
                "anomaly_score": score,
                "profile_used": profile_used,
                "features": _row_features(row, _DEVICE_BEHAVIOR_ID_COLS),
            })

    # --- Protocol ---
    pr_feat = ProtocolFeatures.from_stream(netflow_df, baseline=models.protocol_baseline)
    if not pr_feat.empty:
        scores = _score_with_bundle(models.models["protocol"], pr_feat)
        for idx, row in pr_feat.iterrows():
            results.append({
                "detector": "protocol",
                "entity_id": row["device_ip"],
                "window": row["window"],
                "anomaly_score": scores.loc[idx],
                "profile_used": "global",
                "features": _row_features(row, _PROTOCOL_ID_COLS),
            })

    if not results:
        return pd.DataFrame(columns=["detector", "entity_id", "window", "anomaly_score", "profile_used", "features"])

    return pd.DataFrame(results)
