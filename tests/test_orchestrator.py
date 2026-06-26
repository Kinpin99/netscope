"""
test_orchestrator.py
----------------------
Covers:
  - system_state.py: phase transitions, mark_training_started/result,
    rollback-vs-stay-in-observation logic on first-ever training failure
  - orchestrator.py:
      - observation_status() reflects elapsed time + record counts
      - tick() triggers training when thresholds are met
      - trigger_training_now(): archive -> train -> evaluate -> promote
      - rollback restores previous models on evaluation failure
      - per-device baseline training (must_add_to_project.txt item 6)
      - archive pruning respects ARCHIVE_RETENTION

These tests build a self-contained project fixture (synthetic netflow/prtg
CSVs + a config.yaml pointing at tmp_path) so they don't touch the real
data/ directory, and run the real train_*.py / evaluate_models.py
subprocesses against that fixture - i.e. these are integration tests of
the full training pipeline glued together by the orchestrator.
"""

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pytest
import yaml

from orchestrator.system_state import (
    SystemState,
    PHASE_OBSERVATION,
    PHASE_INFERENCE,
    PHASE_TRAINING,
)


# ---------------------------------------------------------------------------
# system_state.py
# ---------------------------------------------------------------------------
class TestSystemState:
    def test_default_state_is_observation(self, tmp_path):
        state = SystemState(tmp_path / "state.json")
        s = state.get()
        assert s["phase"] == PHASE_OBSERVATION
        assert s["models_version"] == 0
        assert s["observation_started_at"] is not None

    def test_persists_across_instances(self, tmp_path):
        path = tmp_path / "state.json"
        state1 = SystemState(path)
        state1.set_phase(PHASE_INFERENCE, note="test")

        state2 = SystemState(path)
        assert state2.phase == PHASE_INFERENCE
        assert state2.get()["notes"] == "test"

    def test_invalid_phase_raises(self, tmp_path):
        state = SystemState(tmp_path / "state.json")
        with pytest.raises(ValueError):
            state.set_phase("not_a_real_phase")

    def test_mark_training_started(self, tmp_path):
        state = SystemState(tmp_path / "state.json")
        state.mark_training_started()
        s = state.get()
        assert s["phase"] == PHASE_TRAINING
        assert s["last_training_started_at"] is not None

    def test_mark_training_result_pass_increments_version(self, tmp_path):
        state = SystemState(tmp_path / "state.json")
        state.mark_training_started()
        state.mark_training_result(passed=True, note="ok")
        s = state.get()
        assert s["phase"] == PHASE_INFERENCE
        assert s["models_version"] == 1
        assert s["last_training_result"] == "passed"
        assert s["last_retrain_at"] is not None

    def test_mark_training_result_fail_first_run_falls_back_to_observation(self, tmp_path):
        """If models_version == 0 (no model has ever successfully trained),
        a failed training run should leave the system in OBSERVATION so it
        keeps collecting data and retries - not stuck in TRAINING."""
        state = SystemState(tmp_path / "state.json")
        state.mark_training_started()
        state.mark_training_result(passed=False, note="failed")
        s = state.get()
        assert s["phase"] == PHASE_OBSERVATION
        assert s["models_version"] == 0
        assert s["last_training_result"] == "failed"

    def test_mark_training_result_fail_after_success_stays_in_inference(self, tmp_path):
        """If a later retrain fails, the system should stay in INFERENCE
        on the (rolled-back) previous models, not regress to OBSERVATION."""
        state = SystemState(tmp_path / "state.json")
        state.mark_training_started()
        state.mark_training_result(passed=True, note="first ok")
        assert state.get()["models_version"] == 1

        state.mark_training_started()
        state.mark_training_result(passed=False, note="retrain failed")
        s = state.get()
        assert s["phase"] == PHASE_INFERENCE
        assert s["models_version"] == 1  # unchanged
        assert s["last_training_result"] == "failed"


# ---------------------------------------------------------------------------
# Orchestrator integration fixture
# ---------------------------------------------------------------------------
@pytest.fixture
def orchestrator_project(tmp_path):
    """
    Build a self-contained project directory under tmp_path:
      - data/raw/  with synthetic netflow + prtg CSVs
      - data/processed/, data/models/
      - config.yaml pointing at these paths, with small bootstrap
        thresholds so observation_status() can be made "ready" in tests
        without generating huge datasets.

    Returns the SystemOrchestrator instance.
    """
    from orchestrator.orchestrator import SystemOrchestrator

    project_dir = tmp_path / "project"
    (project_dir / "data" / "raw").mkdir(parents=True)
    (project_dir / "data" / "processed").mkdir(parents=True)
    (project_dir / "data" / "models").mkdir(parents=True)

    # --- synthetic netflow data ---
    random.seed(42)
    base_ts = 1718000000
    rows = []
    for window in range(10):
        ts = base_ts + window * 60
        for dev in ["10.0.0.5", "10.0.0.6"]:
            for _ in range(20):
                rows.append({
                    "timestamp": ts + random.randint(0, 59),
                    "src_ip": dev, "dst_ip": "8.8.8.8",
                    "src_port": random.randint(1024, 65535), "dst_port": 443,
                    "protocol": 6, "tcp_flags": 0x10,
                    "packets": random.randint(1, 20), "bytes": random.randint(100, 1500),
                    "duration_sec": random.uniform(0.1, 2),
                })
    nf_df = pd.DataFrame(rows)
    nf_df.to_csv(project_dir / "data" / "raw" / "netflow_raw_2026-06-13.csv", index=False)

    # --- synthetic prtg data ---
    prtg_rows = []
    for window in range(10):
        ts = base_ts + window * 60
        for dev in ["10.0.0.5", "10.0.0.6"]:
            prtg_rows.append({
                "timestamp": ts, "device_ip": dev,
                "if_in_octets": random.randint(10000, 50000),
                "if_out_octets": random.randint(10000, 50000),
                "if_speed": 1_000_000_000,
                "if_in_errors": 0,
                "cpu_load_pct": random.uniform(10, 30),
                "mem_used_pct": random.uniform(20, 50),
            })
    prtg_df = pd.DataFrame(prtg_rows)
    prtg_df.to_csv(project_dir / "data" / "raw" / "prtg_raw_2026-06-13.csv", index=False)

    n_records = len(nf_df)

    # --- config.yaml ---
    config = {
        "system": {"mode": "observation", "kafka_bootstrap": "localhost:9092"},
        "prtg": {"base_url": "https://prtg.local", "api_token": "test", "poll_interval_sec": 60,
                 "avg_interval_sec": 60, "poll_lag_sec": 30},
        "devices": [
            {"ip": "10.0.0.5", "name": "dev5", "building": "HQ", "sensors": {}},
            {"ip": "10.0.0.6", "name": "dev6", "building": "HQ", "sensors": {}},
        ],
        "bootstrap": {
            "min_collection_days": 0,         # 0 days -> time threshold always met
            "min_netflow_records": n_records, # exactly enough -> ready
            "training_hour_utc": 2,
            "retrain_interval_days": 7,
            "rolling_training_window_days": 90,
        },
        "paths": {
            "netflow_raw_dir": str(project_dir / "data" / "raw"),
            "prtg_raw_dir": str(project_dir / "data" / "raw"),
            "processed_dir": str(project_dir / "data" / "processed"),
            "models_dir": str(project_dir / "data" / "models"),
        },
    }
    config_path = project_dir / "config.yaml"
    with open(config_path, "w") as f:
        yaml.safe_dump(config, f)

    orch = SystemOrchestrator(str(config_path))
    orch._project_dir = project_dir  # stash for test access
    return orch


# ---------------------------------------------------------------------------
# observation_status
# ---------------------------------------------------------------------------
class TestObservationStatus:
    def test_status_ready_when_thresholds_met(self, orchestrator_project):
        status = orchestrator_project.observation_status()
        assert status["ready"] is True
        assert status["netflow_records"] == status["records_required"]
        assert status["days_elapsed"] >= status["days_required"]

    def test_status_not_ready_with_high_record_threshold(self, orchestrator_project):
        orchestrator_project.cfg["bootstrap"]["min_netflow_records"] = 10_000_000
        status = orchestrator_project.observation_status()
        assert status["ready"] is False


# ---------------------------------------------------------------------------
# Full training pipeline via trigger_training_now()
# ---------------------------------------------------------------------------
class TestTriggerTraining:
    def test_successful_training_promotes_and_transitions_to_inference(self, orchestrator_project):
        orch = orchestrator_project
        result = orch.trigger_training_now()

        assert result is True
        state = orch.state.get()
        assert state["phase"] == PHASE_INFERENCE
        assert state["models_version"] == 1
        assert state["last_training_result"] == "passed"

        # All four model files should exist
        models_dir = orch.models_dir
        for f in ["bandwidth_model.pkl", "portscan_model.pkl", "device_model.pkl", "protocol_model.pkl"]:
            assert (models_dir / f).exists(), f"{f} missing after training"

        # normalization_stats.json should have bandwidth + device_behavior slices
        with open(models_dir / "normalization_stats.json") as fh:
            stats = json.load(fh)
        assert "bandwidth" in stats
        assert "device_behavior" in stats

    def test_archive_created_on_training(self, orchestrator_project):
        orch = orchestrator_project
        orch.trigger_training_now()

        archive_root = orch.models_dir / "archive"
        assert archive_root.exists()
        snapshots = list(archive_root.iterdir())
        assert len(snapshots) == 1

    def test_second_training_archives_first_models(self, orchestrator_project):
        orch = orchestrator_project
        orch.trigger_training_now()
        orch.trigger_training_now()

        archive_root = orch.models_dir / "archive"
        snapshots = list(archive_root.iterdir())
        assert len(snapshots) == 2

        state = orch.state.get()
        assert state["models_version"] == 2

    def test_tick_triggers_training_when_observation_ready(self, orchestrator_project):
        orch = orchestrator_project
        assert orch.state.phase == PHASE_OBSERVATION

        orch.tick()

        state = orch.state.get()
        assert state["phase"] == PHASE_INFERENCE
        assert state["models_version"] == 1


# ---------------------------------------------------------------------------
# Rollback on evaluation failure
# ---------------------------------------------------------------------------
class TestRollback:
    def test_rollback_restores_previous_model_on_eval_failure(self, orchestrator_project, monkeypatch):
        orch = orchestrator_project

        # First training: should succeed and promote
        assert orch.trigger_training_now() is True
        first_bundle_bytes = (orch.models_dir / "bandwidth_model.pkl").read_bytes()

        # Force the evaluation step to fail on the second run
        original_run = orch._run_subprocess

        def fake_run(script, extra_args=None):
            if "evaluate_models.py" in script:
                return False
            return original_run(script, extra_args)

        monkeypatch.setattr(orch, "_run_subprocess", fake_run)

        result = orch.trigger_training_now()
        assert result is False

        state = orch.state.get()
        assert state["last_training_result"] == "failed"
        assert state["phase"] == PHASE_INFERENCE  # stayed in inference
        assert state["models_version"] == 1       # not incremented

        # Model file should be restored to the first training's version
        second_bundle_bytes = (orch.models_dir / "bandwidth_model.pkl").read_bytes()
        assert second_bundle_bytes == first_bundle_bytes

    def test_failed_first_training_falls_back_to_observation(self, orchestrator_project, monkeypatch):
        orch = orchestrator_project

        def always_fail(script, extra_args=None):
            return False

        monkeypatch.setattr(orch, "_run_subprocess", always_fail)

        result = orch.trigger_training_now()
        assert result is False

        state = orch.state.get()
        assert state["phase"] == PHASE_OBSERVATION
        assert state["models_version"] == 0


# ---------------------------------------------------------------------------
# Per-device baseline (must_add_to_project.txt item 6)
# ---------------------------------------------------------------------------
class TestPerDeviceBaseline:
    def test_train_device_baseline_creates_profile(self, orchestrator_project):
        orch = orchestrator_project
        ok = orch.train_device_baseline("10.0.0.5")
        assert ok is True

        profile_path = orch.models_dir / "device_profiles" / "10_0_0_5_model.pkl"
        assert profile_path.exists()

        with open(orch.models_dir / "normalization_stats.json") as f:
            stats = json.load(f)
        assert "10.0.0.5" in stats.get("device_behavior_profiles", {})

    def test_per_device_failure_does_not_affect_global_state(self, orchestrator_project):
        orch = orchestrator_project
        # Unknown device -> train_device_model.py exits non-zero (no rows for that IP)
        ok = orch.train_device_baseline("10.99.99.99")
        assert ok is False

        # Global system state should be untouched
        state = orch.state.get()
        assert state["phase"] == PHASE_OBSERVATION
        assert state["models_version"] == 0


# ---------------------------------------------------------------------------
# Archive pruning
# ---------------------------------------------------------------------------
class TestArchivePruning:
    def test_prune_keeps_only_retention_count(self, orchestrator_project, monkeypatch):
        from orchestrator import orchestrator as orch_module
        monkeypatch.setattr(orch_module, "ARCHIVE_RETENTION", 2)

        orch = orchestrator_project
        for _ in range(4):
            orch.trigger_training_now()

        archive_root = orch.models_dir / "archive"
        snapshots = list(archive_root.iterdir())
        assert len(snapshots) == 2


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
