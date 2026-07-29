import csv
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from alerts.alert_store import AlertStore
from alerts.risk_scoring import severity_rank
from troubleshooting.event_store import SyslogEventStore
from troubleshooting.topology_graph import NetworkTopologyGraph
from utils.config_loader import PROJECT_ROOT, load_config


class RootCauseEngine:
    def __init__(self, config_path: Optional[str] = None):
        self.cfg = load_config(config_path)
        alerts_dir = self.cfg["paths"].get("alerts_dir") or (self.cfg["paths"]["models_dir"].parent / "alerts")
        self.alert_store = AlertStore(Path(alerts_dir))
        syslog_raw = ((self.cfg.get("troubleshooting") or {}).get("syslog_events_file") or "data/syslogs/syslog_events.jsonl")
        syslog_path = Path(syslog_raw)
        if not syslog_path.is_absolute():
            syslog_path = PROJECT_ROOT / syslog_path
        self.syslog_store = SyslogEventStore(path=syslog_path)
        self.health_path = self.cfg["paths"]["models_dir"].parent / "health_scores.json"
        self.prtg_raw_dir = self.cfg["paths"].get("prtg_raw_dir") or (PROJECT_ROOT / "data" / "raw")
        self.topology = None
        try:
            self.topology = NetworkTopologyGraph(config_path)
        except Exception:
            self.topology = None

    def analyze(self, last_hours: float = 24, limit: int = 50) -> Dict[str, Any]:
        now = time.time()
        since = now - float(last_hours) * 3600
        syslogs = self.syslog_store.list_events(since=since, limit=1000)
        alerts = self.alert_store.list_alerts(since=since)
        health = self._load_health_scores()
        prtg = self._latest_prtg_by_ip()

        incidents: List[Dict[str, Any]] = []
        incidents.extend(self._incidents_from_syslogs(syslogs, alerts, health, prtg))
        incidents.extend(self._incidents_from_alert_clusters(alerts, syslogs, health, prtg))

        incidents = self._dedupe_incidents(incidents)
        incidents.sort(key=lambda item: (item.get("confidence", 0), severity_rank(item.get("severity", "info"))), reverse=True)
        return {
            "generated_at": now,
            "window_hours": last_hours,
            "syslog_event_count": len(syslogs),
            "alert_count": len(alerts),
            "incidents": incidents[:limit],
        }

    def get_incident(self, incident_id: str, last_hours: float = 24) -> Optional[Dict[str, Any]]:
        for incident in self.analyze(last_hours=last_hours, limit=200).get("incidents", []):
            if incident.get("id") == incident_id:
                return incident
        return None

    def topology_json(self) -> Dict[str, Any]:
        if not self.topology:
            return {"nodes": [], "edges": [], "warning": "NetworkX topology graph is unavailable or no topology links are configured."}
        return self.topology.to_json()

    # ------------------------------------------------------------------
    # Incident builders
    # ------------------------------------------------------------------
    def _incidents_from_syslogs(self, syslogs: List[Dict[str, Any]], alerts: List[Dict[str, Any]], health: Dict[str, Any], prtg: Dict[str, Any]) -> List[Dict[str, Any]]:
        result = []
        interesting = {
            "interface_down", "high_interface_errors", "port_security", "routing_adjacency_down",
            "dhcp_issue", "auth_failure", "device_reboot", "high_cpu", "high_memory", "wireless_client_issue",
        }
        for event in syslogs:
            event_type = event.get("event_type") or "generic_syslog"
            if event_type not in interesting and severity_rank(event.get("severity", "info")) < severity_rank("medium"):
                continue

            root_id = self._resolve_event_device(event)
            downstream = self._downstream(root_id) if root_id else []
            affected = self._affected_alert_entities(alerts, root_id, downstream)
            if root_id and root_id not in affected:
                affected.insert(0, root_id)

            evidence = [self._event_evidence(event)]
            evidence.extend(self._metric_evidence(root_id, health, prtg))
            evidence.extend(self._alert_evidence(alerts, affected)[:4])

            severity = self._max_severity([event.get("severity", "info")] + [a.get("severity", "info") for a in alerts if a.get("entity_id") in affected])
            confidence = self._confidence_from_evidence(event_type, evidence, affected)
            title, summary = self._title_summary_for_event(event, root_id, affected)

            result.append({
                "id": self._incident_id("syslog", event.get("id"), root_id, event_type),
                "title": title,
                "summary": summary,
                "root_cause_device": self._device_payload(root_id),
                "root_cause_type": event_type,
                "severity": severity,
                "confidence": confidence,
                "affected_devices": [self._device_payload(ip) for ip in affected],
                "blast_radius_count": len(downstream),
                "evidence": evidence,
                "recommendations": self._recommendations(event_type, root_id),
                "created_from": "syslog_correlation",
                "last_seen": event.get("received_at") or event.get("timestamp"),
            })
        return result

    def _incidents_from_alert_clusters(self, alerts: List[Dict[str, Any]], syslogs: List[Dict[str, Any]], health: Dict[str, Any], prtg: Dict[str, Any]) -> List[Dict[str, Any]]:
        result = []
        clusters: Dict[str, List[Dict[str, Any]]] = {}
        for alert in alerts:
            key = alert.get("building") or "Unassigned"
            clusters.setdefault(key, []).append(alert)

        for building, items in clusters.items():
            if len(items) < 2 and not any(severity_rank(a.get("severity", "info")) >= severity_rank("critical") for a in items):
                continue
            affected = sorted({a.get("entity_id") for a in items if a.get("entity_id")})
            common = self._common_upstream(affected)
            root_id = common.get("ip") if common else None
            evidence = self._alert_evidence(items, affected)[:6]
            if root_id:
                evidence.extend(self._metric_evidence(root_id, health, prtg))
            related_syslogs = self._related_syslogs(syslogs, affected + ([root_id] if root_id else []))
            evidence.extend([self._event_evidence(e) for e in related_syslogs[:3]])
            severity = self._max_severity([a.get("severity", "info") for a in items])
            confidence = min(92, 45 + len(items) * 8 + len(related_syslogs) * 6 + (12 if root_id else 0))
            title = "{} network issue cluster".format(building)
            summary = "{} related alert(s) were observed in {}.{}".format(
                len(items), building, " A common upstream device was found." if root_id else ""
            )
            result.append({
                "id": self._incident_id("cluster", building, ",".join(affected), len(items)),
                "title": title,
                "summary": summary,
                "root_cause_device": self._device_payload(root_id),
                "root_cause_type": "alert_cluster",
                "severity": severity,
                "confidence": confidence,
                "affected_devices": [self._device_payload(ip) for ip in affected],
                "blast_radius_count": len(self._downstream(root_id)) if root_id else 0,
                "evidence": evidence,
                "recommendations": self._recommendations("alert_cluster", root_id),
                "created_from": "alert_topology_correlation",
                "last_seen": max([a.get("updated_at") or a.get("created_at") or 0 for a in items] or [time.time()]),
            })
        return result

    # ------------------------------------------------------------------
    # Correlation helpers
    # ------------------------------------------------------------------
    def _resolve_event_device(self, event: Dict[str, Any]) -> Optional[str]:
        for key in ("device_ip", "device_name"):
            value = event.get(key)
            if not value:
                continue
            if self.topology:
                resolved = self.topology.resolve(value)
                if resolved:
                    return resolved
            if self._device_by_ip(value):
                return value
        return event.get("device_ip")

    def _downstream(self, root_id: Optional[str]) -> List[str]:
        if not root_id or not self.topology:
            return []
        try:
            return self.topology.downstream(root_id)
        except Exception:
            return []

    def _common_upstream(self, identifiers: List[str]) -> Optional[Dict[str, Any]]:
        if not self.topology:
            return None
        try:
            return self.topology.common_upstream(identifiers)
        except Exception:
            return None

    def _affected_alert_entities(self, alerts: List[Dict[str, Any]], root_id: Optional[str], downstream: List[str]) -> List[str]:
        candidates = set(downstream)
        if root_id:
            candidates.add(root_id)
        affected = []
        for alert in alerts:
            entity = alert.get("entity_id")
            if not entity:
                continue
            if not candidates or entity in candidates:
                affected.append(entity)
        return sorted(set(affected))

    def _related_syslogs(self, syslogs: List[Dict[str, Any]], identifiers: List[str]) -> List[Dict[str, Any]]:
        ids = {x for x in identifiers if x}
        related = []
        for event in syslogs:
            resolved = self._resolve_event_device(event)
            if resolved in ids or event.get("device_ip") in ids or event.get("device_name") in ids:
                related.append(event)
        return related

    # ------------------------------------------------------------------
    # Evidence / recommendations
    # ------------------------------------------------------------------
    def _event_evidence(self, event: Dict[str, Any]) -> Dict[str, Any]:
        label = "Syslog {} on {}".format(event.get("event_type"), event.get("device_name") or event.get("device_ip") or "unknown device")
        return {
            "type": "syslog",
            "label": label,
            "severity": event.get("severity"),
            "timestamp": event.get("received_at") or event.get("timestamp"),
            "detail": event.get("message"),
            "device_ip": event.get("device_ip"),
            "interface": event.get("interface"),
        }

    def _alert_evidence(self, alerts: List[Dict[str, Any]], affected: List[str]) -> List[Dict[str, Any]]:
        rows = []
        affected_set = set(affected)
        for alert in alerts:
            if affected_set and alert.get("entity_id") not in affected_set:
                continue
            rows.append({
                "type": "alert",
                "label": "{} anomaly on {}".format(alert.get("detector"), alert.get("entity_id")),
                "severity": alert.get("severity"),
                "timestamp": alert.get("updated_at") or alert.get("created_at"),
                "detail": alert.get("issue_type"),
                "score": alert.get("last_score") or alert.get("max_score"),
                "device_ip": alert.get("entity_id"),
            })
        rows.sort(key=lambda x: severity_rank(x.get("severity", "info")), reverse=True)
        return rows

    def _metric_evidence(self, ip: Optional[str], health: Dict[str, Any], prtg: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not ip:
            return []
        evidence = []
        h = health.get(ip)
        if h:
            evidence.append({
                "type": "health_score",
                "label": "Current health score for {}".format(ip),
                "severity": "medium" if h.get("health_score", 100) < 70 else "info",
                "detail": "health_score={}".format(h.get("health_score")),
                "device_ip": ip,
            })
        p = prtg.get(ip)
        if p:
            try:
                errors = int(float(p.get("if_in_errors") or 0))
                cpu = float(p.get("cpu_load_pct") or 0)
                mem = float(p.get("mem_used_pct") or 0)
            except Exception:
                errors, cpu, mem = 0, 0, 0
            if errors > 0:
                evidence.append({"type": "prtg", "label": "Interface errors observed", "severity": "high", "detail": "if_in_errors={}".format(errors), "device_ip": ip})
            if cpu >= 85:
                evidence.append({"type": "prtg", "label": "High CPU load observed", "severity": "medium", "detail": "cpu_load_pct={}".format(cpu), "device_ip": ip})
            if mem >= 90:
                evidence.append({"type": "prtg", "label": "High memory use observed", "severity": "medium", "detail": "mem_used_pct={}".format(mem), "device_ip": ip})
        return evidence

    def _recommendations(self, root_cause_type: str, root_id: Optional[str]) -> List[str]:
        base = []
        if root_id:
            base.append("Open the device page for {} and check its recent anomaly scores and alerts.".format(root_id))
        mapping = {
            "interface_down": ["Check the affected cable/uplink and switch port status.", "Verify power and transceiver status on both ends of the link.", "If this is an access switch/AP uplink, check downstream user impact."],
            "high_interface_errors": ["Inspect the port for CRC/input errors and replace the cable or transceiver if errors continue.", "Check duplex/speed mismatch and interface counters."],
            "port_security": ["Check whether an unauthorized MAC address or loop triggered port security.", "Confirm the connected endpoint is expected before re-enabling the port."],
            "routing_adjacency_down": ["Check routing neighbor status, interface addressing, and upstream reachability.", "Verify recent config changes or link flaps around the same time."],
            "dhcp_issue": ["Check DHCP scope exhaustion, relay/helper-address configuration, and DHCP server reachability."],
            "auth_failure": ["Review failed login source and confirm whether it is expected administration activity.", "Rotate credentials if the failures are suspicious or repeated."],
            "device_reboot": ["Confirm whether the reboot was planned. Check power, logs, and uptime."],
            "high_cpu": ["Check top talkers and control-plane logs; compare with NetFlow traffic spikes."],
            "high_memory": ["Check running processes, memory leaks, and recent firmware/config changes."],
            "wireless_client_issue": ["Check AP radio status, client count, channel utilisation, and upstream switch port health."],
            "alert_cluster": ["Investigate the common upstream device and its uplinks first.", "Compare the affected devices against the topology blast radius.", "Check recent syslogs around the alert window."],
        }
        return base + mapping.get(root_cause_type, ["Review correlated syslogs, SNMP counters, and NetFlow around the incident window."])

    def _title_summary_for_event(self, event: Dict[str, Any], root_id: Optional[str], affected: List[str]) -> (str, str):
        event_type = event.get("event_type") or "syslog"
        device = root_id or event.get("device_name") or event.get("device_ip") or "unknown device"
        title = "{} on {}".format(event_type.replace("_", " ").title(), device)
        summary = "A {} syslog event was observed".format(event_type.replace("_", " "))
        if affected:
            summary += " and {} device(s) may be affected".format(len(affected))
        return title, summary + "."

    def _confidence_from_evidence(self, event_type: str, evidence: List[Dict[str, Any]], affected: List[str]) -> int:
        confidence = 45
        if event_type != "generic_syslog":
            confidence += 18
        confidence += min(18, len(evidence) * 4)
        confidence += min(15, len(affected) * 3)
        return min(95, confidence)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------
    def _load_health_scores(self) -> Dict[str, Any]:
        if not self.health_path.exists():
            return {}
        try:
            with open(self.health_path) as f:
                return json.load(f)
        except Exception:
            return {}

    def _latest_prtg_by_ip(self) -> Dict[str, Any]:
        raw_dir = Path(self.prtg_raw_dir)
        if not raw_dir.is_absolute():
            raw_dir = PROJECT_ROOT / raw_dir
        files = sorted(raw_dir.glob("prtg_raw_*.csv"), reverse=True)
        if not files:
            return {}
        rows = {}
        try:
            with open(files[0], newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    ip = row.get("device_ip")
                    if ip:
                        rows[ip] = row
        except Exception:
            return {}
        return rows

    # ------------------------------------------------------------------
    # Misc helpers
    # ------------------------------------------------------------------
    def _device_by_ip(self, ip: str) -> Optional[Dict[str, Any]]:
        for device in self.cfg.get("devices", []):
            if device.get("ip") == ip:
                return device
        return None

    def _device_payload(self, ip: Optional[str]) -> Optional[Dict[str, Any]]:
        if not ip:
            return None
        device = self._device_by_ip(ip) or {"ip": ip, "name": ip}
        return {
            "ip": device.get("ip") or ip,
            "name": device.get("name") or ip,
            "building": device.get("building"),
            "floor": device.get("floor"),
            "device_type": device.get("device_type") or device.get("type"),
            "role": device.get("role"),
        }

    def _max_severity(self, severities: List[str]) -> str:
        values = [s or "info" for s in severities]
        return sorted(values, key=severity_rank, reverse=True)[0] if values else "info"

    def _incident_id(self, *parts: Any) -> str:
        raw = "|".join(str(p) for p in parts)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def _dedupe_incidents(self, incidents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        by_id = {}
        for item in incidents:
            existing = by_id.get(item["id"])
            if not existing or item.get("confidence", 0) > existing.get("confidence", 0):
                by_id[item["id"]] = item
        return list(by_id.values())
