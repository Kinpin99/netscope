"""
topology_builder.py
----------------------
Builds two views of the network from config.yaml's device list plus live
health/alert data:

  1. Building-grouped view (must_add_to_project.txt item 1):
     "Allows administrators to view issues about network access, network
     congestion, device status, and error packets from the perspective of
     buildings."

  2. Flat device list / single-device detail, for simpler dashboard pages.

Edges (topology graph) are not inferred from NetFlow here - that would
require topology discovery via LLDP/CDP or routing tables, which is out of
scope for this pass. This returns device nodes grouped by building, which
is sufficient for the dashboard's building/device list views. A future
iteration can add edges once topology discovery data is available.

This module reads (not writes) config.yaml, health_scores.json (from
AlertEngine), and AlertStore - it has no persistence of its own.
"""

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

    # -----------------------------------------------------------------
    # Health scores helper
    # -----------------------------------------------------------------
    def _load_health_scores(self) -> Dict[str, dict]:
        if not self.health_path.exists():
            return {}
        with open(self.health_path) as f:
            return json.load(f)

    # -----------------------------------------------------------------
    # Per-device node
    # -----------------------------------------------------------------
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
            "floor": device.get("floor"),
            "device_type": device.get("device_type") or device.get("type"),
            "role": device.get("role"),
            "source": device.get("source"),
            "serial_number": device.get("serial_number"),
            "mac_address": device.get("mac_address"),
            "health_score": health["health_score"] if health else None,
            "detector_scores": health["detector_scores"] if health else {},
            "open_issue_count": len(alerts),
            "max_severity": max_severity,
            "status": _status_from_health(health["health_score"] if health else None, len(alerts), max_severity),
        }

    # -----------------------------------------------------------------
    # Building-grouped view (item 1)
    # -----------------------------------------------------------------
    def building_view(self) -> List[dict]:
        """
        Returns one entry per building:
            {
              "building": "<name>",
              "device_count": <int>,
              "open_issue_count": <int>,
              "max_severity": "info".."critical",
              "avg_health_score": <float> | None,
              "devices": [<device node dicts>],
            }

        Devices with no "building" set in config.yaml are grouped under
        "Unassigned".
        """
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

    # -----------------------------------------------------------------
    # Flat device list with status (for a simple device-list dashboard page)
    # -----------------------------------------------------------------
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

    # -----------------------------------------------------------------
    # Single device detail
    # -----------------------------------------------------------------
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


def _status_from_health(health_score: Optional[float], open_issue_count: int, max_severity: str = "info") -> str:
    """Coarse status label for dashboard device lists/icons.

    Persisted alerts take precedence over a missing/stale health score. This
    matters for source-keyed detectors such as port scans: the scanner can
    have a real alert even when no device health score has been calculated.
    """
    if open_issue_count > 0:
        if max_severity in {"critical", "high"}:
            return "critical"
        return "degraded"
    if health_score is None:
        return "unknown"
    if health_score >= 90:
        return "healthy"
    if health_score >= 50:
        return "degraded"
    return "critical"
