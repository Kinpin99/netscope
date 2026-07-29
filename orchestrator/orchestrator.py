"""
orchestrator.py
-----------------
The lifecycle manager described in the project design:

    OBSERVATION -> TRAINING -> INFERENCE
                       ^             |
                       |__retraining_|

Responsibilities:
  1. On startup, determine the current phase from system_state.json
     (or initialize it if this is a fresh deployment).
  2. While in OBSERVATION: periodically check whether enough data has been
     collected (config.yaml's bootstrap.* thresholds) to start training.
  3. Trigger the training pipeline (TRAINING phase):
       a. Run the four train_*.py scripts as subprocesses (CSV-only, no
          Kafka - matches the training/inference separation throughout
          this project)
       b. Run evaluate_models.py as a gate
       c. On pass: archive the previously-deployed models, promote the
          new ones (they're already in data/models/ - "promotion" here
          means the archive step + bumping models_version), transition
          to INFERENCE
       d. On fail: leave the previous models in place (do NOT overwrite
          them - see _archive_and_train), transition back to INFERENCE
          (if models existed before) or OBSERVATION (first-ever run),
          and record the failure for admin review
  4. While in INFERENCE: periodically check whether a retrain is due
     (config.yaml's bootstrap.retrain_interval_days), and if so repeat
     step 3.

This module exposes both:
  - A class-based API (SystemOrchestrator) for use by scheduler.py / the
    FastAPI app (routes_system.py can call trigger_training_now() for an
    admin-requested manual retrain, or train_device_baseline() for
    must_add_to_project.txt item 6).
  - A `run_once()` convenience function and CLI entry point for use from
    cron/systemd timers instead of an in-process scheduler, if preferred.

IMPORTANT ON MODEL PROMOTION:
The train_*.py scripts write directly into data/models/*.pkl. This means
by the time evaluate_models.py runs, the "new" models have already
overwritten the "old" ones on disk. To make rollback possible on
evaluation failure, _archive_current_models() copies the CURRENT models to
data/models/archive/<timestamp>/ BEFORE running training. If evaluation
fails, the archived copies are copied back over the freshly-trained
(failing) ones - this is the rollback. If evaluation passes, the archived
copies are just left in data/models/archive/ as history (and old archives
beyond a retention count are pruned).
"""

import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.system_state import SystemState, PHASE_OBSERVATION, PHASE_INFERENCE, PHASE_TRAINING
from utils.telemetry_cache import count_rotating_csv_rows
from utils.config_loader import load_config

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [orchestrator] %(levelname)s %(message)s")
log = logging.getLogger(__name__)


PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Training scripts run in this order. device_model and protocol_model are
# independent of each other and of bandwidth/portscan, but running them
# sequentially keeps subprocess output easy to follow and avoids four
# processes hammering the same CSV files concurrently during read.
TRAIN_SCRIPTS = [
    "training/train_bandwidth_model.py",
    "training/train_portscan_model.py",
    "training/train_device_model.py",
    "training/train_protocol_model.py",
]
EVALUATE_SCRIPT = "training/evaluate_models.py"

# Model files that get archived/restored as a unit on each training cycle.
# device_profiles/ (per-device baselines) and normalization_stats.json are
# included so a rollback restores the complete picture, not just the four
# global models.
MODEL_ARTIFACTS = [
    "bandwidth_model.pkl",
    "portscan_model.pkl",
    "device_model.pkl",
    "protocol_model.pkl",
    "normalization_stats.json",
]

ARCHIVE_RETENTION = 10  # keep at most this many archived training snapshots


class SystemOrchestrator:
    def __init__(self, config_path: Optional[str] = None):
        self.cfg = load_config(config_path)
        # Use the resolved path (not the possibly-None argument) so
        # subprocesses spawned by _run_subprocess always get an explicit
        # --config pointing at the same config.yaml this process read -
        # even if config_path was None and DEFAULT_CONFIG_PATH was
        # monkeypatched (e.g. in tests).
        self.config_path = self.cfg["_config_path"]
        self.models_dir: Path = self.cfg["paths"]["models_dir"]
        self.netflow_dir: Path = self.cfg["paths"]["netflow_raw_dir"]
        self.state = SystemState(self.models_dir / "system_state.json")

    # -----------------------------------------------------------------
    # Observation phase
    # -----------------------------------------------------------------
    def observation_status(self) -> dict:
        """
        Returns a dict describing progress towards leaving observation:
            {
              "ready": bool,
              "days_elapsed": float,
              "days_required": int,
              "netflow_records": int,
              "records_required": int,
            }
        """
        bootstrap = self.cfg["bootstrap"]
        state = self.state.get()

        started = state.get("observation_started_at") or time.time()
        days_elapsed = (time.time() - started) / 86400.0
        days_required = bootstrap["min_collection_days"]

        records = count_rotating_csv_rows(self.netflow_dir, "netflow_raw_*.csv")
        records_required = bootstrap["min_netflow_records"]

        ready = (days_elapsed >= days_required) and (records >= records_required)

        return {
            "ready": ready,
            "days_elapsed": round(days_elapsed, 2),
            "days_required": days_required,
            "netflow_records": records,
            "records_required": records_required,
        }

    # -----------------------------------------------------------------
    # Main tick - called periodically by scheduler.py
    # -----------------------------------------------------------------
    def tick(self) -> None:
        """
        One scheduler iteration. Decides whether any state transition is
        needed and acts on it. Safe to call repeatedly (e.g. every few
        minutes) - it's a no-op most of the time.
        """
        phase = self.state.phase

        if phase == PHASE_OBSERVATION:
            status = self.observation_status()
            log.info(
                "Observation status: ready=%s days=%.2f/%d records=%d/%d",
                status["ready"], status["days_elapsed"], status["days_required"],
                status["netflow_records"], status["records_required"],
            )
            if status["ready"]:
                log.info("Observation thresholds met - triggering initial training")
                self.trigger_training_now()
            else:
                self.state.update_note(
                    f"Collecting baseline data - day {status['days_elapsed']:.1f} of "
                    f"{status['days_required']} ({status['netflow_records']}/{status['records_required']} flows)"
                )

        elif phase == PHASE_INFERENCE:
            if self._retrain_due():
                log.info("Retrain interval elapsed - triggering retraining")
                self.trigger_training_now()

        elif phase == PHASE_TRAINING:
            # Training is a transient state. If we observe it here, a
            # previous run likely crashed mid-training without updating
            # state. Don't get stuck - re-run.
            log.warning("Found system in TRAINING phase on tick - a previous "
                        "run may have crashed. Re-running training.")
            self.trigger_training_now()

    def _retrain_due(self) -> bool:
        bootstrap = self.cfg["bootstrap"]
        state = self.state.get()
        last_retrain = state.get("last_retrain_at")
        if last_retrain is None:
            return False  # shouldn't happen in INFERENCE, but be safe
        interval_sec = bootstrap["retrain_interval_days"] * 86400
        return (time.time() - last_retrain) >= interval_sec

    # -----------------------------------------------------------------
    # Training trigger (also callable directly for manual/admin retrain)
    # -----------------------------------------------------------------
    def trigger_training_now(self) -> bool:
        """
        Runs the full training pipeline: archive current models, run all
        four train_*.py scripts, evaluate, and either promote or roll back.

        Returns True if the new models were promoted, False if rolled back.
        """
        self.state.mark_training_started()
        archive_dir = self._archive_current_models()

        log.info("=== Running training pipeline ===")
        all_ok = True
        for script in TRAIN_SCRIPTS:
            ok = self._run_subprocess(script)
            if not ok:
                all_ok = False
                log.error("Training script failed: %s", script)
                break  # stop early - no point evaluating partial results

        if all_ok:
            log.info("=== Running evaluation gate ===")
            eval_ok = self._run_subprocess(EVALUATE_SCRIPT)
        else:
            eval_ok = False

        if all_ok and eval_ok:
            self._promote(archive_dir)
            self.state.mark_training_result(
                passed=True,
                note=f"Training completed and promoted (snapshot: {archive_dir.name}).",
            )
            log.info("Training PASSED - new models promoted.")
            return True
        else:
            self._rollback(archive_dir)
            reason = "training script failure" if not all_ok else "evaluation failure"
            self.state.mark_training_result(
                passed=False,
                note=f"Training FAILED ({reason}). Rolled back to previous models "
                     f"(snapshot: {archive_dir.name}). Admin review recommended.",
            )
            log.error("Training FAILED (%s) - rolled back to previous models.", reason)
            return False

    # -----------------------------------------------------------------
    # Archive / promote / rollback
    # -----------------------------------------------------------------
    def _archive_current_models(self) -> Path:
        """
        Copy the current model artifacts to data/models/archive/<timestamp>/
        BEFORE training overwrites them. Returns the archive directory
        (created even if some/all artifacts don't exist yet - e.g. the very
        first training run, where archive_dir will just be empty).
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        archive_dir = self.models_dir / "archive" / timestamp
        archive_dir.mkdir(parents=True, exist_ok=True)

        for artifact in MODEL_ARTIFACTS:
            src = self.models_dir / artifact
            if src.exists():
                shutil.copy2(src, archive_dir / artifact)

        # Also archive per-device profiles directory if present
        profiles_src = self.models_dir / "device_profiles"
        if profiles_src.exists():
            shutil.copytree(profiles_src, archive_dir / "device_profiles", dirs_exist_ok=True)

        log.info("Archived current models -> %s", archive_dir)
        self._prune_archives()
        return archive_dir

    def _prune_archives(self) -> None:
        archive_root = self.models_dir / "archive"
        if not archive_root.exists():
            return
        snapshots = sorted(
            (p for p in archive_root.iterdir() if p.is_dir()),
            key=lambda p: p.name,
        )
        excess = len(snapshots) - ARCHIVE_RETENTION
        for old in snapshots[:max(0, excess)]:
            log.info("Pruning old archive snapshot: %s", old)
            shutil.rmtree(old, ignore_errors=True)

    def _promote(self, archive_dir: Path) -> None:
        """
        New models are already in data/models/ (written there by train_*.py).
        "Promotion" is just: nothing further to copy - the archive_dir
        snapshot of the PREVIOUS models remains as history/rollback point.
        This method exists as an explicit step for clarity and as a hook
        for future promotion-time actions (e.g. notifying the dashboard,
        reloading live inference workers).
        """
        log.info("Promoting newly trained models (previous version archived at %s)", archive_dir)
        # Hook point: e.g. signal the live inference process to reload
        # model bundles from disk. Left as a no-op here since the live
        # inference loader (detectors/ensemble_detector.py) re-reads
        # model files on each retrain cycle / restart.

    def _rollback(self, archive_dir: Path) -> None:
        """
        Restore the previous model artifacts from archive_dir over the
        freshly (and failingly) trained ones in data/models/.
        """
        log.info("Rolling back to archived models from %s", archive_dir)
        for artifact in MODEL_ARTIFACTS:
            src = archive_dir / artifact
            if src.exists():
                shutil.copy2(src, self.models_dir / artifact)

        profiles_src = archive_dir / "device_profiles"
        if profiles_src.exists():
            profiles_dst = self.models_dir / "device_profiles"
            shutil.copytree(profiles_src, profiles_dst, dirs_exist_ok=True)

    # -----------------------------------------------------------------
    # Per-device baseline (must_add_to_project.txt item 6)
    # -----------------------------------------------------------------
    def train_device_baseline(self, device_ip: str) -> bool:
        """
        On-request per-device baseline training. Does NOT go through the
        archive/evaluate/promote machinery used for the four global
        models - a per-device model is additive (stored under
        data/models/device_profiles/) and doesn't replace anything that
        live inference depends on by default, so a bad per-device profile
        can't break the global system. Returns True on success.
        """
        log.info("Training per-device baseline for %s", device_ip)
        ok = self._run_subprocess(
            "training/train_device_model.py",
            extra_args=["--mode", "per-device", "--device-ip", device_ip],
        )
        if ok:
            log.info("Per-device baseline for %s trained successfully", device_ip)
        else:
            log.error("Per-device baseline training failed for %s", device_ip)
        return ok

    # -----------------------------------------------------------------
    # Subprocess helper
    # -----------------------------------------------------------------
    def _run_subprocess(self, script: str, extra_args: Optional[List[str]] = None) -> bool:
        cmd = [sys.executable, str(PROJECT_ROOT / script)]
        if self.config_path:
            cmd.extend(["--config", str(self.config_path)])
        if extra_args:
            cmd.extend(extra_args)

        log.info("Running: %s", " ".join(cmd))
        proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True)

        if proc.stdout:
            for line in proc.stdout.splitlines():
                log.info("  %s", line)
        if proc.stderr:
            for line in proc.stderr.splitlines():
                log.info("  %s", line)

        if proc.returncode != 0:
            log.error("%s exited with code %d", script, proc.returncode)
            return False
        return True


# ---------------------------------------------------------------------------
# CLI / cron entry point
# ---------------------------------------------------------------------------
def run_once(config_path: Optional[str] = None) -> None:
    orchestrator = SystemOrchestrator(config_path)
    orchestrator.tick()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Orchestrator tick (run once, for cron/systemd timers)")
    parser.add_argument("--config", default=None)
    parser.add_argument("--force-train", action="store_true",
                        help="Force a training run regardless of phase/thresholds")
    parser.add_argument("--device-baseline", default=None,
                        help="Train a per-device baseline for this device IP and exit")
    args = parser.parse_args()

    orchestrator = SystemOrchestrator(args.config)

    if args.device_baseline:
        ok = orchestrator.train_device_baseline(args.device_baseline)
        sys.exit(0 if ok else 1)

    if args.force_train:
        ok = orchestrator.trigger_training_now()
        sys.exit(0 if ok else 1)

    orchestrator.tick()
