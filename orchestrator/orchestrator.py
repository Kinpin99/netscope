import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.system_state import SystemState, PHASE_OBSERVATION, PHASE_INFERENCE, PHASE_TRAINING
from preprocessing.unified_preprocessing import _load_netflow
from utils.config_loader import load_config

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [orchestrator] %(levelname)s %(message)s")
log = logging.getLogger(__name__)


PROJECT_ROOT = Path(__file__).resolve().parent.parent

TRAIN_SCRIPTS = [
    "training/train_bandwidth_model.py",
    "training/train_portscan_model.py",
    "training/train_device_model.py",
    "training/train_protocol_model.py",
]
EVALUATE_SCRIPT = "training/evaluate_models.py"

# Model files that get archived/restored as a unit on each training cycle
MODEL_ARTIFACTS = [
    "bandwidth_model.pkl",
    "portscan_model.pkl",
    "device_model.pkl",
    "protocol_model.pkl",
    "normalization_stats.json",
]

ARCHIVE_RETENTION = 10  # training snapshots


class SystemOrchestrator:
    def __init__(self, config_path: Optional[str] = None):
        self.cfg = load_config(config_path)
        self.config_path = self.cfg["_config_path"]
        self.models_dir: Path = self.cfg["paths"]["models_dir"]
        self.netflow_dir: Path = self.cfg["paths"]["netflow_raw_dir"]
        self.state = SystemState(self.models_dir / "system_state.json")

    
    # Observation phase
    def observation_status(self) -> dict:
        bootstrap = self.cfg["bootstrap"]
        state = self.state.get()

        started = state.get("observation_started_at") or time.time()
        days_elapsed = (time.time() - started) / 86400.0
        days_required = bootstrap["min_collection_days"]

        nf = _load_netflow(str(self.netflow_dir))
        records = len(nf)
        records_required = bootstrap["min_netflow_records"]

        ready = (days_elapsed >= days_required) and (records >= records_required)

        return {
            "ready": ready,
            "days_elapsed": round(days_elapsed, 2),
            "days_required": days_required,
            "netflow_records": records,
            "records_required": records_required,
        }

    
    # Main tick - called periodically by scheduler.py
    def tick(self) -> None:
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

    
    # Training trigger ,also callable directly for manual/admin retrain
    def trigger_training_now(self) -> bool:
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

    
    # Archive / promote / rollback
    def _archive_current_models(self) -> Path:
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
        log.info("Promoting newly trained models (previous version archived at %s)", archive_dir)

    def _rollback(self, archive_dir: Path) -> None:
        """
        Restore the previous model artifacts from archive_dir over the
        freshly trained ones in data/models/.
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

    
    # Per-device baseline (must_add_to_project.txt number 6)
    def train_device_baseline(self, device_ip: str) -> bool:
        """
        On-request per-device baseline training. Does NOT go through the
        archive/evaluate/promote machinery used for the four global
        models
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

    
    # Subprocess helper
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



# CLI / cron entry point
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
