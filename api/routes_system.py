import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from orchestrator.orchestrator import SystemOrchestrator
from orchestrator.system_state import PHASE_TRAINING

router = APIRouter()


def _get_orchestrator() -> SystemOrchestrator:
    return SystemOrchestrator()


@router.get("/status")
def get_status():

    orch = _get_orchestrator()
    state = orch.state.get()

    result = {
        "phase": state["phase"],
        "notes": state["notes"],
        "models_version": state["models_version"],
        "last_retrain_at": state.get("last_retrain_at"),
        "last_training_result": state.get("last_training_result"),
    }

    if state["phase"] != PHASE_TRAINING:
        try:
            result["observation"] = orch.observation_status()
        except Exception:
            # observation_status reads NetFlow data, if data/raw is empty
            # or unreadable this shouldn't break the status endpoint.
            result["observation"] = None

    return result


@router.post("/retrain")
async def trigger_retrain():

    orch = _get_orchestrator()
    if orch.state.phase == PHASE_TRAINING:
        raise HTTPException(status_code=409, detail="Training is already in progress")

    passed = await run_in_threadpool(orch.trigger_training_now)
    state = orch.state.get()
    return {
        "passed": passed,
        "phase": state["phase"],
        "models_version": state["models_version"],
        "notes": state["notes"],
    }
