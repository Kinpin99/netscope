"""
sliding_window.py
--------------------
Buffers incoming NetFlow/PRTG records (from Kafka, keyed by their own
`timestamp` field - NOT arrival time) and groups them into fixed-size
time windows, emitting a completed window's records once the router is
confident no more records for that window will arrive.

Why timestamp-based windowing instead of arrival-time:
  Records can arrive slightly out of order (different exporters, network
  jitter, Kafka partition skew), so grouping strictly by arrival order
  would split a single 60s window's flows across two emitted batches.
  Instead, each record is bucketed by floor(record.timestamp / window_sec),
  and a window is only emitted once a record from a LATER window has been
  seen (or the router's flush timeout elapses) - i.e. "the window is closed
  once time has clearly moved on".

This is a standard watermark-style approach, simplified for a single
in-process consumer (no distributed state).
"""

import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import logging
log = logging.getLogger(__name__)


class SlidingWindowBuffer:
    """
    Generic buffer for one record stream (netflow or prtg). Call add() for
    each incoming record dict (must have a numeric "timestamp" key), and
    poll flush_ready() periodically to retrieve completed windows.

    grace_period_sec: how many seconds past a window's end we wait before
    considering it "ready" even if no later-window record has arrived yet.
    This bounds worst-case latency if traffic stops entirely (e.g. quiet
    overnight periods) - without a grace period, the last window of data
    would never flush because nothing "later" ever arrives.
    """

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
        """
        Return [(window_start, records)] for all windows that are ready to
        be processed, removing them from the buffer. A window is ready if:
          - a record from a strictly later window has been seen, OR
          - grace_period_sec has elapsed (wall-clock) since we last saw a
            new max window, and this window is the current max (handles
            the "traffic stopped" case so the last window still flushes)

        Returned in ascending window order.
        """
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
