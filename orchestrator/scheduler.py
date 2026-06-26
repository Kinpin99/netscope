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
