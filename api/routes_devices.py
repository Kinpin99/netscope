"""
api/routes_devices.py
------------------------
Device detail view, and the per-device "normal baseline" training endpoint
(must_add_to_project.txt item 6): "on user request, can create a 'normal
baseline' for a particular device; Behavioural Anomalies: Detects
abnormalities in that selected device's traffic behaviour."
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from orchestrator.orchestrator import SystemOrchestrator
from topology.topology_builder import TopologyBuilder
from utils.config_loader import load_config, get_device_by_ip
from api.security import audit_event, require_admin

router = APIRouter()


def _get_builder() -> TopologyBuilder:
    return TopologyBuilder()


def _get_orchestrator() -> SystemOrchestrator:
    return SystemOrchestrator()


@router.get("/{device_ip}")
def device_detail(device_ip: str):
    """
    Single device's current health, open alerts, and whether it has a
    per-device behavioral baseline (data/models/device_profiles/).
    """
    builder = _get_builder()
    detail = builder.device_detail(device_ip)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Device {device_ip} not found in config.yaml")
    return detail


@router.post("/{device_ip}/baseline")
async def train_device_baseline(device_ip: str, request: Request, user=Depends(require_admin)):
    """
    must_add_to_project.txt item 6: on-request per-device "normal baseline".

    Triggers training/train_device_model.py --mode per-device for this
    device, producing data/models/device_profiles/<ip>_model.pkl. This is
    additive (see orchestrator.train_device_baseline's docstring) - it
    doesn't go through the global archive/evaluate/promote pipeline, so it
    can't affect other devices or roll back the global models.

    Runs synchronously in a thread pool - training on one device's data is
    fast (seconds), unlike the full pipeline.

    404 if device_ip isn't in config.yaml. 422 if training fails (e.g. the
    device has no observed traffic yet - see train_device_model.py's
    "Has it been observed yet?" error).
    """
    cfg = load_config()
    if get_device_by_ip(cfg, device_ip) is None:
        audit_event("baseline_train_unknown_device", request=request, user=user, success=False, target=device_ip)
        raise HTTPException(status_code=404, detail=f"Device {device_ip} not found in config.yaml")

    orch = _get_orchestrator()
    ok = await run_in_threadpool(orch.train_device_baseline, device_ip)

    if not ok:
        audit_event("baseline_train_failed", request=request, user=user, success=False, target=device_ip)
        raise HTTPException(
            status_code=422,
            detail=(
                f"Baseline training failed for {device_ip}. This usually means "
                f"the device has no observed traffic yet - check back after "
                f"the system has collected data for this device."
            ),
        )

    audit_event("baseline_trained", request=request, user=user, target=device_ip)
    builder = _get_builder()
    return builder.device_detail(device_ip)


@router.delete("/{device_ip}/baseline")
def delete_device_baseline(device_ip: str, request: Request, user=Depends(require_admin)):
    """
    Remove a device's per-device baseline, reverting it to the global
    device_behavior model. Also cleans up its
    normalization_stats.json["device_behavior_profiles"] entry.
    """
    cfg = load_config()
    if get_device_by_ip(cfg, device_ip) is None:
        audit_event("baseline_delete_unknown_device", request=request, user=user, success=False, target=device_ip)
        raise HTTPException(status_code=404, detail=f"Device {device_ip} not found in config.yaml")

    models_dir = cfg["paths"]["models_dir"]
    safe_name = device_ip.replace(".", "_").replace(":", "_")
    profile_path = models_dir / "device_profiles" / f"{safe_name}_model.pkl"

    if not profile_path.exists():
        audit_event("baseline_delete_missing", request=request, user=user, success=False, target=device_ip)
        raise HTTPException(status_code=404, detail=f"No per-device baseline exists for {device_ip}")

    profile_path.unlink()

    stats_path = models_dir / "normalization_stats.json"
    if stats_path.exists():
        with open(stats_path) as f:
            stats = json.load(f)
        stats.get("device_behavior_profiles", {}).pop(device_ip, None)
        with open(stats_path, "w") as f:
            json.dump(stats, f, indent=2)

    audit_event("baseline_deleted", request=request, user=user, target=device_ip)
    return {"device_ip": device_ip, "baseline_removed": True}
