import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import APIRouter, Query

from detectors.ensemble_detector import ModelBundle, score_window
from preprocessing.unified_preprocessing import (
    _assign_device_ip,
    _assign_window,
    _is_private_ip,
    _load_netflow,
    _load_snmp,
)
from utils.config_loader import load_config

router = APIRouter()


def _get_cfg():
    return load_config()


@router.get("/recent")
def recent_traffic(
    minutes: int = Query(15, ge=1, le=1440, description="How many minutes of recent data to aggregate"),
):

    cfg = _get_cfg()
    nf = _load_netflow(str(cfg["paths"]["netflow_raw_dir"]))
    if nf.empty:
        return {"window_sec": 60, "devices": {}}

    cutoff = time.time() - minutes * 60
    nf = nf[nf["timestamp"] >= cutoff]
    if nf.empty:
        return {"window_sec": 60, "devices": {}}

    nf = _assign_window(nf, 60)
    nf = _assign_device_ip(nf)
    nf["is_inbound"] = nf["dst_ip"].apply(_is_private_ip)

    in_flows = nf[nf["is_inbound"]]
    out_flows = nf[~nf["is_inbound"]]

    in_agg = in_flows.groupby(["device_ip", "window"]).agg(
        bytes_in=("bytes", "sum"), packets_in=("packets", "sum")
    )
    out_agg = out_flows.groupby(["device_ip", "window"]).agg(
        bytes_out=("bytes", "sum"), packets_out=("packets", "sum")
    )
    combined = in_agg.join(out_agg, how="outer").fillna(0).reset_index()

    devices: dict = {}
    for device_ip, group in combined.groupby("device_ip"):
        group = group.sort_values("window")
        devices[device_ip] = [
            {
                "window": float(row["window"]),
                "bytes_in": float(row["bytes_in"]),
                "bytes_out": float(row["bytes_out"]),
                "packets_in": float(row["packets_in"]),
                "packets_out": float(row["packets_out"]),
            }
            for _, row in group.iterrows()
        ]

    return {"window_sec": 60, "devices": devices}


@router.get("/live-scores")
def live_scores(
    minutes: int = Query(1, ge=1, le=10, description="How many minutes of recent data to score"),
):

    cfg = _get_cfg()
    nf = _load_netflow(str(cfg["paths"]["netflow_raw_dir"]))
    snmp = _load_snmp(str(cfg["paths"]["prtg_raw_dir"]))

    if nf.empty:
        return {"scores": []}

    cutoff = time.time() - minutes * 60
    nf = nf[nf["timestamp"] >= cutoff]
    if not snmp.empty:
        snmp = snmp[snmp["timestamp"] >= cutoff]

    if nf.empty:
        return {"scores": []}

    models = ModelBundle(cfg["paths"]["models_dir"], cfg["paths"]["processed_dir"])
    result = score_window(nf, snmp, models)

    records = []
    for _, row in result.iterrows():
        score = row["anomaly_score"]
        is_nan = score is None or (isinstance(score, float) and math.isnan(score))
        records.append({
            "detector": row["detector"],
            "entity_id": row["entity_id"],
            "window": float(row["window"]),
            "anomaly_score": None if is_nan else float(score),
            "profile_used": row["profile_used"],
        })

    return {"scores": records}
