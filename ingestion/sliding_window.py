import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import logging
log = logging.getLogger(__name__)


class SlidingWindowBuffer:

    def __init__(self, window_sec: int = 60, grace_period_sec: int = 10):
        self.window_sec = window_sec
        self.grace_period_sec = grace_period_sec
        self._buckets: Dict[int, List[dict]] = defaultdict(list)
        self._max_window_seen: Optional[int] = None
        self._max_window_seen_at: Optional[float] = None

    def _window_for(self, ts: float) -> int:
        return int(ts // self.window_sec) * self.window_sec

    def add(self, record: dict) -> None:
        ts = record.get("timestamp")
        if ts is None:
            log.warning("Record missing 'timestamp' - dropping: %r", record)
            return

        window = self._window_for(float(ts))
        self._buckets[window].append(record)

        if self._max_window_seen is None or window > self._max_window_seen:
            self._max_window_seen = window
            self._max_window_seen_at = time.time()

    def flush_ready(self) -> List[Tuple[int, List[dict]]]:

        if not self._buckets:
            return []

        ready_windows = []
        now = time.time()

        for window in sorted(self._buckets.keys()):
            is_strictly_older = self._max_window_seen is not None and window < self._max_window_seen
            grace_elapsed = (
                self._max_window_seen_at is not None
                and (now - self._max_window_seen_at) >= self.grace_period_sec
            )
            if is_strictly_older or grace_elapsed:
                ready_windows.append(window)

        result = []
        for window in ready_windows:
            result.append((window, self._buckets.pop(window)))
        return result

    def pending_window_count(self) -> int:
        return len(self._buckets)
