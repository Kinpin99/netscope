"""
api/routes_system.py
-----------------------
System lifecycle status for the dashboard's status banner:
"Collecting baseline data (Day 4 of 14)" / "Training models..." /
"Live detection active".

Also exposes a manual "trigger retrain now" endpoint for admins. The
per-device baseline endpoint (must_add_to_project.txt item 6) lives in
routes_devices.py since it's more naturally a device-scoped action.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from orchestrator.orchestrator import SystemOrchestrator
from orchestrator.system_state import PHASE_OBSERVATION, PHASE_TRAINING
from api.security import audit_event, require_admin

router = APIRouter()


def _get_orchestrator() -> SystemOrchestrator:
    return SystemOrchestrator()


@router.get("/status")
def get_status():
    """
    Current lifecycle phase plus progress info:
        {
          "phase": "observation" | "training" | "inference",
          "notes": "...",
          "models_version": <int>,
          "last_retrain_at": <epoch> | null,
          "observation": {...}   # only present while phase != "training"
        }
    """
    orch = _get_orchestrator()
    state = orch.state.get()

    result = {
        "phase": state["phase"],
        "notes": state["notes"],
        "models_version": state["models_version"],
        "last_retrain_at": state.get("last_retrain_at"),
        "last_training_result": state.get("last_training_result"),
    }

    if state["phase"] == PHASE_OBSERVATION:
        try:
            result["observation"] = orch.observation_status()
        except Exception:
            result["observation"] = None
    else:
        # Preserve the response shape without rescanning telemetry after the
        # system has already entered inference/training.
        result["observation"] = None

    return result


@router.post("/retrain")
async def trigger_retrain(request: Request, user=Depends(require_admin)):
    """
    Manually trigger the training pipeline (archive -> train -> evaluate ->
    promote/rollback). Runs synchronously in a thread pool since training
    takes real time (seconds to minutes depending on data volume) - the
    HTTP request blocks until it completes.

    Returns 409 if training is already in progress (phase == "training"),
    to avoid two concurrent training runs stepping on each other's
    data/models/ files.
    """
    orch = _get_orchestrator()
    if orch.state.phase == PHASE_TRAINING:
        audit_event("retrain_conflict", request=request, user=user, success=False)
        raise HTTPException(status_code=409, detail="Training is already in progress")

    passed = await run_in_threadpool(orch.trigger_training_now)
    audit_event("retrain_triggered", request=request, user=user, success=bool(passed))
    state = orch.state.get()
    return {
        "passed": passed,
        "phase": state["phase"],
        "models_version": state["models_version"],
        "notes": state["notes"],
    }
