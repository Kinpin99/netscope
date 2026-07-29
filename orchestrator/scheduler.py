"""
scheduler.py
--------------
Thin APScheduler wrapper around SystemOrchestrator.tick(). This is the
long-running process that ties the lifecycle together: while collectors
(netflow_collector.py, prtg_collector.py) run as their own processes
writing to data/raw/, this scheduler periodically calls tick() to check
observation progress, trigger training, and trigger retraining.

One scheduled job:
  - orchestrator_tick: runs every `tick_interval_minutes` (default 60).
    This is deliberately coarse - training is an expensive, multi-minute
    operation, and observation/retrain thresholds are measured in days,
    so checking once an hour is more than sufficient. tick() itself is
    cheap and a no-op most of the time. Training runs as a blocking
    subprocess call from within tick(); max_instances=1 ensures ticks
    don't overlap if a tick is still running training when the next
    interval fires.

This is an alternative to running orchestrator.py via cron/systemd timers
(run_once() / `python orchestrator.py` does the same single tick). Use
whichever fits the deployment - this module is for environments where a
single long-running Python process is preferred.

Usage:
    python orchestrator/scheduler.py
    python orchestrator/scheduler.py --interval-minutes 30
    python orchestrator/scheduler.py --run-immediately
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.executors.pool import ThreadPoolExecutor

from orchestrator.orchestrator import SystemOrchestrator

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [scheduler] %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Run the orchestrator on a recurring schedule")
    parser.add_argument("--config", default=None)
    parser.add_argument("--interval-minutes", type=int, default=60,
                        help="How often to run orchestrator.tick() (default: 60)")
    parser.add_argument("--run-immediately", action="store_true",
                        help="Run one tick immediately on startup, before the first scheduled interval")
    args = parser.parse_args()

    orchestrator = SystemOrchestrator(args.config)

    scheduler = BlockingScheduler(
        executors={"default": ThreadPoolExecutor(max_workers=1)},
        job_defaults={"coalesce": True, "max_instances": 1},
    )

    job_kwargs = {}
    if args.run_immediately:
        job_kwargs["next_run_time"] = datetime.now()

    scheduler.add_job(
        orchestrator.tick,
        "interval",
        minutes=args.interval_minutes,
        id="orchestrator_tick",
        **job_kwargs,
    )

    log.info(
        "Scheduler starting. Current phase: %s. Tick interval: %d minutes.",
        orchestrator.state.phase, args.interval_minutes,
    )

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler shutting down.")


if __name__ == "__main__":
    main()
