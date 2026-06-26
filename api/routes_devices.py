import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from orchestrator.orchestrator import SystemOrchestrator
from topology.topology_builder import TopologyBuilder
from utils.config_loader import load_config, get_device_by_ip

router = APIRouter()


def _get_builder() -> TopologyBuilder:
    return TopologyBuilder()


def _get_orchestrator() -> SystemOrchestrator:
    return SystemOrchestrator()


@router.get("/{device_ip}")
def device_detail(device_ip: str):

    builder = _get_builder()
    detail = builder.device_detail(device_ip)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Device {device_ip} not found in config.yaml")
    return detail


@router.post("/{device_ip}/baseline")
async def train_device_baseline(device_ip: str):
    """
    must_add_to_project.txt number 6: on-request per-device "normal baseline".
    """
    cfg = load_config()
    if get_device_by_ip(cfg, device_ip) is None:
        raise HTTPException(status_code=404, detail=f"Device {device_ip} not found in config.yaml")

    orch = _get_orchestrator()
    ok = await run_in_threadpool(orch.train_device_baseline, device_ip)

    if not ok:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Baseline training failed for {device_ip}. This usually means "
                f"the device has no observed traffic yet - check back after "
                f"the system has collected data for this device."
            ),
        )

    builder = _get_builder()
    return builder.device_detail(device_ip)


@router.delete("/{device_ip}/baseline")
def delete_device_baseline(device_ip: str):
    """
    Remove a device's per-device baseline, reverting it to the global
    device_behavior model
    """
    cfg = load_config()
    if get_device_by_ip(cfg, device_ip) is None:
        raise HTTPException(status_code=404, detail=f"Device {device_ip} not found in config.yaml")

    models_dir = cfg["paths"]["models_dir"]
    safe_name = device_ip.replace(".", "_").replace(":", "_")
    profile_path = models_dir / "device_profiles" / f"{safe_name}_model.pkl"

    if not profile_path.exists():
        raise HTTPException(status_code=404, detail=f"No per-device baseline exists for {device_ip}")

    profile_path.unlink()

    stats_path = models_dir / "normalization_stats.json"
    if stats_path.exists():
        with open(stats_path) as f:
            stats = json.load(f)
        stats.get("device_behavior_profiles", {}).pop(device_ip, None)
        with open(stats_path, "w") as f:
            json.dump(stats, f, indent=2)

    return {"device_ip": device_ip, "baseline_removed": True}
