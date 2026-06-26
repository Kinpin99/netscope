import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alerts.alert_store import AlertStore
from alerts.risk_scoring import severity_rank
from utils.config_loader import load_config, get_device_by_ip

import logging
log = logging.getLogger(__name__)


class TopologyBuilder:
    def __init__(self, config_path: Optional[str] = None):
        self.cfg = load_config(config_path)
        alerts_dir = self.cfg["paths"].get("alerts_dir") or (
            self.cfg["paths"]["models_dir"].parent / "alerts"
        )
        self.alert_store = AlertStore(Path(alerts_dir))
        self.health_path = self.cfg["paths"]["models_dir"].parent / "health_scores.json"


    # Health scores helper
    def _load_health_scores(self) -> Dict[str, dict]:
        if not self.health_path.exists():
            return {}
        with open(self.health_path) as f:
            return json.load(f)


    # Per-device node
    def _device_node(self, device: dict, health_scores: dict, open_alerts_by_entity: Dict[str, list]) -> dict:
        ip = device["ip"]
        health = health_scores.get(ip)
        alerts = open_alerts_by_entity.get(ip, [])

        max_severity = "info"
        for alert in alerts:
            if severity_rank(alert["severity"]) > severity_rank(max_severity):
                max_severity = alert["severity"]

        return {
            "ip": ip,
            "name": device.get("name"),
            "building": device.get("building"),
            "health_score": health["health_score"] if health else None,
            "detector_scores": health["detector_scores"] if health else {},
            "open_issue_count": len(alerts),
            "max_severity": max_severity,
            "status": _status_from_health(health["health_score"] if health else None, len(alerts)),
        }


    # Building-grouped view (item 1)
    def building_view(self) -> List[dict]:
        health_scores = self._load_health_scores()
        open_alerts = self.alert_store.list_open_alerts()

        open_alerts_by_entity: Dict[str, list] = {}
        for alert in open_alerts:
            open_alerts_by_entity.setdefault(alert["entity_id"], []).append(alert)

        buildings: Dict[str, dict] = {}
        for device in self.cfg.get("devices", []):
            building_name = device.get("building") or "Unassigned"
            node = self._device_node(device, health_scores, open_alerts_by_entity)

            entry = buildings.setdefault(building_name, {
                "building": building_name,
                "device_count": 0,
                "open_issue_count": 0,
                "max_severity": "info",
                "_health_scores": [],
                "devices": [],
            })
            entry["device_count"] += 1
            entry["open_issue_count"] += node["open_issue_count"]
            if severity_rank(node["max_severity"]) > severity_rank(entry["max_severity"]):
                entry["max_severity"] = node["max_severity"]
            if node["health_score"] is not None:
                entry["_health_scores"].append(node["health_score"])
            entry["devices"].append(node)

        result = []
        for entry in buildings.values():
            scores = entry.pop("_health_scores")
            entry["avg_health_score"] = round(sum(scores) / len(scores), 2) if scores else None
            result.append(entry)

        return sorted(result, key=lambda b: b["building"])


    # Flat device list with status for a simple device-list dashboard page
    def device_list(self) -> List[dict]:
        health_scores = self._load_health_scores()
        open_alerts = self.alert_store.list_open_alerts()

        open_alerts_by_entity: Dict[str, list] = {}
        for alert in open_alerts:
            open_alerts_by_entity.setdefault(alert["entity_id"], []).append(alert)

        return [
            self._device_node(device, health_scores, open_alerts_by_entity)
            for device in self.cfg.get("devices", [])
        ]


    # Single device detail
    def device_detail(self, ip: str) -> Optional[dict]:
        device = get_device_by_ip(self.cfg, ip)
        if device is None:
            return None

        health_scores = self._load_health_scores()
        open_alerts = [a for a in self.alert_store.list_open_alerts() if a["entity_id"] == ip]
        open_alerts_by_entity = {ip: open_alerts}

        node = self._device_node(device, health_scores, open_alerts_by_entity)
        node["open_alerts"] = open_alerts
        node["has_per_device_profile"] = self._has_per_device_profile(ip)
        return node

    def _has_per_device_profile(self, ip: str) -> bool:
        profiles_dir = self.cfg["paths"]["models_dir"] / "device_profiles"
        safe_name = ip.replace(".", "_").replace(":", "_")
        return (profiles_dir / f"{safe_name}_model.pkl").exists()


def _status_from_health(health_score: Optional[float], open_issue_count: int) -> str:
    if health_score is None:
        return "unknown"
    if open_issue_count == 0 and health_score >= 90:
        return "healthy"
    if health_score >= 50:
        return "degraded"
    return "critical"
