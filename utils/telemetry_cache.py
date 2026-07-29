"""
Incremental caches for daily-rotated telemetry CSV files.

Dashboard endpoints should not reread every historical CSV on every poll.
This module tails only newly appended bytes, retains a bounded recent
window in memory, and exposes a cheap incremental row counter for the
observation progress banner.
"""

import csv
import io
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

import pandas as pd


_COUNT_LOCK = threading.RLock()
_COUNT_STATE: Dict[str, dict] = {}


def count_rotating_csv_rows(directory: Path, pattern: str) -> int:
    """Count data rows across rotated CSVs without loading them into pandas.

    Counts are updated incrementally when a file grows. If a file is replaced
    or truncated, it is recounted safely.
    """
    directory = Path(directory)
    total = 0
    current_paths = {str(p.resolve()): p for p in directory.glob(pattern)}

    with _COUNT_LOCK:
        for key in list(_COUNT_STATE):
            if key not in current_paths:
                _COUNT_STATE.pop(key, None)

        for key, path in current_paths.items():
            try:
                stat = path.stat()
            except OSError:
                continue

            state = _COUNT_STATE.get(key)
            reset = (
                state is None
                or stat.st_size < state["offset"]
                or (stat.st_size == state["offset"] and stat.st_mtime_ns != state["mtime_ns"])
            )
            if reset:
                state = {"offset": 0, "line_count": 0, "mtime_ns": 0}

            if stat.st_size > state["offset"]:
                try:
                    with open(path, "rb") as handle:
                        handle.seek(state["offset"])
                        chunk = handle.read()
                    state["line_count"] += chunk.count(b"\n")
                    state["offset"] = stat.st_size
                except OSError:
                    continue

            state["mtime_ns"] = stat.st_mtime_ns
            _COUNT_STATE[key] = state
            # One line is the CSV header. Collectors always terminate rows
            # with a newline, so this remains exact while the file is open.
            total += max(0, state["line_count"] - 1)

    return total


class RotatingCsvTailCache:
    """Incrementally tail recent daily CSV files into a bounded DataFrame."""

    def __init__(
        self,
        directory: Path,
        patterns: Sequence[str],
        columns: Sequence[str],
        numeric_columns: Sequence[str],
        retention_sec: int = 8 * 3600,
        min_sync_interval_sec: float = 1.0,
        max_files: int = 3,
    ):
        self.directory = Path(directory)
        self.patterns = tuple(patterns)
        self.columns = list(columns)
        self.numeric_columns = tuple(numeric_columns)
        self.retention_sec = int(retention_sec)
        self.min_sync_interval_sec = float(min_sync_interval_sec)
        self.max_files = int(max_files)

        self._lock = threading.RLock()
        self._states: Dict[str, dict] = {}
        self._combined = pd.DataFrame(columns=self.columns)
        self._last_sync = 0.0
        self._version = 0

    @property
    def version(self) -> int:
        return self._version

    def _paths(self) -> List[Path]:
        paths = set()
        for pattern in self.patterns:
            paths.update(self.directory.glob(pattern))
        return sorted(paths)[-self.max_files :]

    def _empty(self) -> pd.DataFrame:
        return pd.DataFrame(columns=self.columns)

    def _parse_rows(self, header: List[str], lines: List[str]) -> pd.DataFrame:
        if not lines:
            return self._empty()
        reader = csv.DictReader(io.StringIO("".join(lines)), fieldnames=header)
        rows = [row for row in reader if row]
        if not rows:
            return self._empty()
        frame = pd.DataFrame(rows)
        for col in self.columns:
            if col not in frame.columns:
                frame[col] = None
        frame = frame[self.columns]
        for col in self.numeric_columns:
            if col in frame.columns:
                frame[col] = pd.to_numeric(frame[col], errors="coerce")
        if "timestamp" in frame.columns:
            frame = frame.dropna(subset=["timestamp"])
        return frame

    def _sync_file(self, path: Path, now: float) -> bool:
        key = str(path.resolve())
        try:
            stat = path.stat()
        except OSError:
            return False

        state = self._states.get(key)
        reset = (
            state is None
            or stat.st_size < state["offset"]
            or (stat.st_size == state["offset"] and stat.st_mtime_ns != state["mtime_ns"])
        )
        if reset:
            state = {
                "offset": 0,
                "mtime_ns": 0,
                "header": None,
                "partial": b"",
                "df": self._empty(),
            }

        changed = False
        if stat.st_size > state["offset"]:
            try:
                with open(path, "rb") as handle:
                    handle.seek(state["offset"])
                    chunk = handle.read()
            except OSError:
                return False

            payload = state["partial"] + chunk
            parts = payload.splitlines(True)
            if parts and not parts[-1].endswith((b"\n", b"\r")):
                state["partial"] = parts.pop()
            else:
                state["partial"] = b""

            decoded = [part.decode("utf-8", errors="replace") for part in parts]
            if state["header"] is None and decoded:
                state["header"] = next(csv.reader([decoded.pop(0)]))

            if state["header"] and decoded:
                new_df = self._parse_rows(state["header"], decoded)
                if not new_df.empty:
                    state["df"] = new_df.reset_index(drop=True) if state["df"].empty else pd.concat([state["df"], new_df], ignore_index=True)
                    changed = True

            state["offset"] = stat.st_size

        state["mtime_ns"] = stat.st_mtime_ns
        if "timestamp" in state["df"].columns and not state["df"].empty:
            cutoff = now - self.retention_sec
            state["df"] = state["df"][state["df"]["timestamp"] >= cutoff].reset_index(drop=True)
        self._states[key] = state
        return changed or reset

    def sync(self, force: bool = False) -> None:
        now = time.time()
        with self._lock:
            if not force and now - self._last_sync < self.min_sync_interval_sec:
                return

            paths = self._paths()
            valid = {str(path.resolve()) for path in paths}
            removed = False
            for key in list(self._states):
                if key not in valid:
                    self._states.pop(key, None)
                    removed = True

            changed = removed
            for path in paths:
                changed = self._sync_file(path, now) or changed

            if changed or self._combined.empty:
                frames = [state["df"] for state in self._states.values() if not state["df"].empty]
                self._combined = pd.concat(frames, ignore_index=True) if frames else self._empty()
                if not self._combined.empty and "timestamp" in self._combined.columns:
                    self._combined = self._combined.sort_values("timestamp").reset_index(drop=True)
                self._version += 1

            self._last_sync = now

    def get(self, cutoff: Optional[float] = None) -> pd.DataFrame:
        self.sync()
        with self._lock:
            frame = self._combined
            if cutoff is not None and not frame.empty:
                frame = frame[frame["timestamp"] >= cutoff]
            return frame.copy()
