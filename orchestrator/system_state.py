import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

PHASE_OBSERVATION = "observation"
PHASE_TRAINING = "training"
PHASE_INFERENCE = "inference"

VALID_PHASES = {PHASE_OBSERVATION, PHASE_TRAINING, PHASE_INFERENCE}


class SystemState:

    def __init__(self, path: Path):
        self.path = path
        if not self.path.exists():
            self._write(self._default_state())

    @staticmethod
    def _default_state() -> Dict[str, Any]:
        return {
            "phase": PHASE_OBSERVATION,
            "observation_started_at": time.time(),
            "last_training_started_at": None,
            "last_training_completed_at": None,
            "last_training_result": None,
            "last_retrain_at": None,
            "models_version": 0,
            "notes": "System started. Collecting baseline data.",
        }

    def _read(self) -> Dict[str, Any]:
        with open(self.path) as f:
            return json.load(f)

    def _write(self, state: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(".tmp")
        with open(tmp_path, "w") as f:
            json.dump(state, f, indent=2)
        tmp_path.replace(self.path)

    # Public API
    def get(self) -> Dict[str, Any]:
        return self._read()

    @property
    def phase(self) -> str:
        return self._read()["phase"]

    def set_phase(self, phase: str, note: Optional[str] = None) -> None:
        if phase not in VALID_PHASES:
            raise ValueError(f"Invalid phase: {phase!r} (must be one of {VALID_PHASES})")
        state = self._read()
        state["phase"] = phase
        if note:
            state["notes"] = note
        self._write(state)

    def mark_training_started(self) -> None:
        state = self._read()
        state["phase"] = PHASE_TRAINING
        state["last_training_started_at"] = time.time()
        state["notes"] = "Training pipeline running."
        self._write(state)

    def mark_training_result(self, passed: bool, note: str) -> None:
        state = self._read()
        state["last_training_completed_at"] = time.time()
        state["last_training_result"] = "passed" if passed else "failed"
        if passed:
            state["phase"] = PHASE_INFERENCE
            state["last_retrain_at"] = time.time()
            state["models_version"] = state.get("models_version", 0) + 1
        else:
            # Roll back to whatever phase we were in before training
            # if models already existed, stay in INFERENCE on the old
            # models; if this was the first-ever training, fall back to
            # OBSERVATION so the system keeps collecting data and retries.
            state["phase"] = PHASE_INFERENCE if state.get("models_version", 0) > 0 else PHASE_OBSERVATION
        state["notes"] = note
        self._write(state)

    def update_note(self, note: str) -> None:
        state = self._read()
        state["notes"] = note
        self._write(state)
