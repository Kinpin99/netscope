"""
alert_store.py
-----------------
Persistence for alerts. Stores alerts as JSON, one file per UTC day
(data/alerts/alerts_<YYYY-MM-DD>.json), mirroring the daily-rotation
convention used for raw NetFlow/PRTG data elsewhere in this project.

An "alert" represents an anomaly that alert_engine has decided is worth
surfacing (passed severity threshold). Alerts have a lifecycle:

    OPEN    - the anomaly condition is currently active (most recent window
              that contributed to this alert was above threshold)
    CLOSED  - the anomaly condition cleared (a subsequent window scored
              below threshold for this detector+entity)

This lets the dashboard show "currently active issues" (OPEN) separately
from history, and lets must_add_to_project.txt item 5 (issue distribution
view) query alerts over a time range regardless of current status.

Schema (one alert):
    {
      "id": "<uuid>",
      "detector": "bandwidth" | "portscan" | "device_behavior" | "protocol",
      "entity_id": "<device_ip or src_ip>",
      "issue_type": "<risk_scoring.classify_issue_type output>",
      "severity": "info"|"low"|"medium"|"high"|"critical",
      "status": "open" | "closed",
      "first_window": <epoch>,
      "last_window": <epoch>,
      "window_count": <int>,         # how many consecutive windows triggered this
      "max_score": <float>,
      "last_score": <float>,
      "building": "<from config.yaml device.building, or null>",
      "device_name": "<from config.yaml device.name, or null>",
      "profile_used": "global" | "per_device",
      "created_at": <epoch>,
      "updated_at": <epoch>,
      "closed_at": <epoch> | null
    }

This is intentionally simple (JSON files, full-file rewrite on update) -
appropriate for the alert volumes expected from a handful of detectors on
a 1-minute cadence. If volume grows, swap this module's internals for a
real DB (storage/timeseries_db.py / device_db.py) without changing
alert_engine.py's interface.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from alerts.risk_scoring import severity_rank

STATUS_OPEN = "open"
STATUS_CLOSED = "closed"


class AlertStore:
    def __init__(self, alerts_dir: Path):
        self.alerts_dir = Path(alerts_dir)
        self.alerts_dir.mkdir(parents=True, exist_ok=True)

    # -----------------------------------------------------------------
    # File layout: one JSON file per UTC day, keyed by the alert's
    # created_at date. An OPEN alert that spans multiple days stays in
    # the file for the day it was created; updates to it rewrite that
    # same file (looked up via find_open_alert, which scans recent days -
    # bounded by _RECENT_DAYS_TO_SCAN).
    # -----------------------------------------------------------------
    _RECENT_DAYS_TO_SCAN = 14  # an alert shouldn't stay open longer than this in practice

    def _path_for_date(self, date_str: str) -> Path:
        return self.alerts_dir / f"alerts_{date_str}.json"

    def _date_str(self, epoch: float) -> str:
        return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d")

    def _read_file(self, path: Path) -> List[dict]:
        if not path.exists():
            return []
        with open(path) as f:
            return json.load(f)

    def _write_file(self, path: Path, alerts: List[dict]) -> None:
        tmp_path = path.with_suffix(".tmp")
        with open(tmp_path, "w") as f:
            json.dump(alerts, f, indent=2)
        tmp_path.replace(path)

    def _recent_files(self) -> List[Path]:
        """All alert files for the last _RECENT_DAYS_TO_SCAN days, newest first."""
        files = sorted(self.alerts_dir.glob("alerts_*.json"), reverse=True)
        return files[: self._RECENT_DAYS_TO_SCAN]

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------
    def find_open_alert(self, detector: str, entity_id: str) -> Optional[dict]:
        """
        Find the currently-OPEN alert for this (detector, entity_id), if any.
        Scans recent days' files since an open alert may have been created
        on a previous day and is still active.
        """
        for path in self._recent_files():
            alerts = self._read_file(path)
            for alert in alerts:
                if (
                    alert["detector"] == detector
                    and alert["entity_id"] == entity_id
                    and alert["status"] == STATUS_OPEN
                ):
                    alert["_source_file"] = str(path)  # internal hint for save_alert
                    return alert
        return None

    def save_alert(self, alert: dict) -> None:
        """
        Insert or update an alert. If alert has a "_source_file" hint (set
        by find_open_alert), update it in place there. Otherwise it's a new
        alert - file determined by created_at's date.
        """
        source_file = alert.pop("_source_file", None)
        if source_file:
            path = Path(source_file)
        else:
            path = self._path_for_date(self._date_str(alert["created_at"]))

        alerts = self._read_file(path)
        for i, existing in enumerate(alerts):
            if existing["id"] == alert["id"]:
                alerts[i] = alert
                self._write_file(path, alerts)
                return

        alerts.append(alert)
        self._write_file(path, alerts)

    def create_alert(
        self,
        detector: str,
        entity_id: str,
        issue_type: str,
        severity: str,
        window: float,
        score: float,
        building: Optional[str] = None,
        device_name: Optional[str] = None,
        profile_used: str = "global",
    ) -> dict:
        now = datetime.now(timezone.utc).timestamp()
        alert = {
            "id": str(uuid.uuid4()),
            "detector": detector,
            "entity_id": entity_id,
            "issue_type": issue_type,
            "severity": severity,
            "status": STATUS_OPEN,
            "first_window": window,
            "last_window": window,
            "window_count": 1,
            "max_score": score,
            "last_score": score,
            "building": building,
            "device_name": device_name,
            "profile_used": profile_used,
            "created_at": now,
            "updated_at": now,
            "closed_at": None,
        }
        self.save_alert(alert)
        return alert

    def update_alert(
        self,
        alert: dict,
        window: float,
        score: float,
        severity: str,
        profile_used: str = "global",
    ) -> dict:
        """Extend an existing OPEN alert with a new triggering window."""
        alert["last_window"] = window
        alert["window_count"] += 1
        alert["max_score"] = max(alert["max_score"], score)
        alert["last_score"] = score
        # Severity can escalate (but not de-escalate while still open -
        # de-escalation below threshold means the alert should be closed,
        # not downgraded).
        if severity_rank(severity) > severity_rank(alert["severity"]):
            alert["severity"] = severity
        alert["profile_used"] = profile_used
        alert["updated_at"] = datetime.now(timezone.utc).timestamp()
        self.save_alert(alert)
        return alert

    def close_alert(self, alert: dict) -> dict:
        alert["status"] = STATUS_CLOSED
        now = datetime.now(timezone.utc).timestamp()
        alert["updated_at"] = now
        alert["closed_at"] = now
        self.save_alert(alert)
        return alert

    def list_open_alerts(self) -> List[dict]:
        """All currently-OPEN alerts, across recent files."""
        seen_ids = set()
        result = []
        for path in self._recent_files():
            for alert in self._read_file(path):
                if alert["status"] == STATUS_OPEN and alert["id"] not in seen_ids:
                    seen_ids.add(alert["id"])
                    result.append(alert)
        return result

    def list_alerts(
        self,
        since: Optional[float] = None,
        until: Optional[float] = None,
        device_ip: Optional[str] = None,
        building: Optional[str] = None,
        severity: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[dict]:
        """
        Query alerts with optional filters - backs the issue distribution
        view (must_add_to_project.txt item 5) and the building-perspective
        view (item 1).

        since/until filter on created_at. device_ip filters on entity_id
        (note: for portscan, entity_id is the SOURCE ip of the suspected
        scanner, which may not be one of our managed devices).
        """
        result = []
        for path in sorted(self.alerts_dir.glob("alerts_*.json")):
            for alert in self._read_file(path):
                if since is not None and alert["created_at"] < since:
                    continue
                if until is not None and alert["created_at"] > until:
                    continue
                if device_ip is not None and alert["entity_id"] != device_ip:
                    continue
                if building is not None and alert.get("building") != building:
                    continue
                if severity is not None and alert["severity"] != severity:
                    continue
                if status is not None and alert["status"] != status:
                    continue
                result.append(alert)
        return result
