
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from alerts.alert_store import AlertStore
from alerts.risk_scoring import (
    classify_issue_type,
    compute_health_score,
    score_to_severity,
    severity_rank,
    DEFAULT_SEVERITY,
)
from utils.config_loader import load_config, get_device_by_ip

import logging
log = logging.getLogger(__name__)



MIN_ALERTABLE_SEVERITY = "low"


def _severity_below(severity: str, floor: str) -> bool:
    return severity_rank(severity) < severity_rank(floor)


class AlertEngine:
    def __init__(self, config_path: Optional[str] = None):
        self.cfg = load_config(config_path)
        alerts_dir = self.cfg["paths"].get("alerts_dir") or (
            self.cfg["paths"]["models_dir"].parent / "alerts"
        )
        self.store = AlertStore(Path(alerts_dir))
        self.health_path = self.cfg["paths"]["models_dir"].parent / "health_scores.json"

    
    # Device metadata enrichment
    def _device_meta(self, entity_id: str) -> Dict[str, Optional[str]]:
        device = get_device_by_ip(self.cfg, entity_id)
        if device:
            return {"building": device.get("building"), "device_name": device.get("name")}
        return {"building": None, "device_name": None}

    
    # Main entry point
    def process_window(self, scores_df: pd.DataFrame) -> list:
        touched_alerts = []

        for _, row in scores_df.iterrows():
            score = row["anomaly_score"]
            if score is None or score != score:  # NaN
                continue

            detector = row["detector"]
            entity_id = row["entity_id"]
            window = row["window"]
            profile_used = row.get("profile_used", "global")
            features = row.get("features") or {}

            severity = score_to_severity(score)
            existing = self.store.find_open_alert(detector, entity_id)

            if severity == DEFAULT_SEVERITY or _severity_below(severity, MIN_ALERTABLE_SEVERITY):
                # Back to normal (or never was anomalous) - close any open alert.
                if existing is not None:
                    closed = self.store.close_alert(existing)
                    log.info(
                        "Closed alert %s (%s/%s) - score back to %.4f (severity=%s)",
                        closed["id"], detector, entity_id, score, severity,
                    )
                    touched_alerts.append(closed)
                continue

            issue_type = classify_issue_type(detector, features)
            meta = self._device_meta(entity_id)

            if existing is not None:
                updated = self.store.update_alert(
                    existing, window=window, score=score, severity=severity,
                    profile_used=profile_used,
                )
                touched_alerts.append(updated)
            else:
                created = self.store.create_alert(
                    detector=detector, entity_id=entity_id, issue_type=issue_type,
                    severity=severity, window=window, score=score,
                    building=meta["building"], device_name=meta["device_name"],
                    profile_used=profile_used,
                )
                log.info(
                    "New alert %s: %s/%s severity=%s issue_type=%s score=%.4f",
                    created["id"], detector, entity_id, severity, issue_type, score,
                )
                touched_alerts.append(created)

        self._update_health_scores(scores_df)
        return touched_alerts

    
    # Health scores (must_add_to_project.txt - number 3)
    def _update_health_scores(self, scores_df: pd.DataFrame) -> None:
        device_keyed = scores_df[scores_df["detector"] != "portscan"]
        if device_keyed.empty:
            return

        latest_window = device_keyed["window"].max()
        latest = device_keyed[device_keyed["window"] == latest_window]

        health: Dict[str, dict] = {}
        if self.health_path.exists():
            with open(self.health_path) as f:
                health = json.load(f)

        for entity_id, group in latest.groupby("entity_id"):
            detector_scores = dict(zip(group["detector"], group["anomaly_score"]))
            score = compute_health_score(detector_scores)
            meta = self._device_meta(entity_id)
            health[entity_id] = {
                "health_score": round(score, 2),
                "window": float(latest_window),
                "detector_scores": {
                    k: (None if v != v else round(float(v), 4))
                    for k, v in detector_scores.items()
                },
                "building": meta["building"],
                "device_name": meta["device_name"],
                "updated_at": datetime.now(timezone.utc).timestamp(),
            }

        tmp_path = self.health_path.with_suffix(".tmp")
        with open(tmp_path, "w") as f:
            json.dump(health, f, indent=2)
        tmp_path.replace(self.health_path)

    def get_health_scores(self) -> Dict[str, dict]:
        if not self.health_path.exists():
            return {}
        with open(self.health_path) as f:
            return json.load(f)

    def compute_health_scores(self, scores_df: pd.DataFrame) -> Dict[str, float]:
        device_keyed = scores_df[scores_df["detector"] != "portscan"]
        if device_keyed.empty:
            return {}

        result = {}
        for entity_id, group in device_keyed.groupby("entity_id"):
            detector_scores = dict(zip(group["detector"], group["anomaly_score"]))
            result[entity_id] = compute_health_score(detector_scores)
        return result

    
    # Issue distribution view (must_add_to_project.txt - number 5)
    def issue_distribution(
        self,
        since: Optional[float] = None,
        until: Optional[float] = None,
    ) -> list:
        alerts = self.store.list_alerts(since=since, until=until)
        if not alerts:
            return []

        by_entity: Dict[str, dict] = {}
        for alert in alerts:
            entity_id = alert["entity_id"]
            entry = by_entity.setdefault(entity_id, {
                "entity_id": entity_id,
                "building": alert.get("building"),
                "device_name": alert.get("device_name"),
                "issue_count": 0,
                "max_severity": "info",
                "issue_types": set(),
            })
            entry["issue_count"] += 1
            entry["issue_types"].add(alert["issue_type"])
            if severity_rank(alert["severity"]) > severity_rank(entry["max_severity"]):
                entry["max_severity"] = alert["severity"]

        result = []
        for entry in by_entity.values():
            entry["issue_types"] = sorted(entry["issue_types"])
            result.append(entry)
        return result
