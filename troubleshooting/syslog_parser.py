import re
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

_SEVERITY_WORDS = {
    "emerg": "critical",
    "emergency": "critical",
    "alert": "critical",
    "crit": "critical",
    "critical": "critical",
    "err": "high",
    "error": "high",
    "warning": "medium",
    "warn": "medium",
    "notice": "low",
    "info": "info",
    "informational": "info",
    "debug": "info",
}

_FACILITY_SEVERITY = {
    "0": "critical",
    "1": "critical",
    "2": "critical",
    "3": "high",
    "4": "medium",
    "5": "low",
    "6": "info",
    "7": "info",
}

_EVENT_PATTERNS = [
    ("interface_down", [r"\binterface\b.*\bdown\b", r"\blink\b.*\bdown\b", r"changed state to down", r"line protocol.*down"]),
    ("interface_up", [r"\binterface\b.*\bup\b", r"\blink\b.*\bup\b", r"changed state to up", r"line protocol.*up"]),
    ("high_interface_errors", [r"\bcrc\b", r"input errors", r"output errors", r"interface errors", r"excessive errors"]),
    ("port_security", [r"port.?security", r"mac violation", r"err.?disable", r"bpduguard", r"storm control"]),
    ("routing_adjacency_down", [r"ospf.*down", r"bgp.*down", r"eigrp.*down", r"neighbor.*down", r"adjacency.*down"]),
    ("routing_adjacency_up", [r"ospf.*full", r"bgp.*established", r"neighbor.*up", r"adjacency.*up"]),
    ("dhcp_issue", [r"dhcp.*fail", r"dhcp.*decline", r"no.*dhcp", r"address conflict"]),
    ("auth_failure", [r"authentication failure", r"login failed", r"invalid password", r"failed login", r"aaa.*fail"]),
    ("device_reboot", [r"reboot", r"reload", r"system restarted", r"boot completed"]),
    ("high_cpu", [r"high cpu", r"cpu.*threshold", r"process.*hog"]),
    ("high_memory", [r"high memory", r"memory.*threshold", r"memory allocation"]),
    ("wireless_client_issue", [r"client.*disassoc", r"client.*deauth", r"radio.*down", r"ssid", r"channel.*change"]),
]

_INTERFACE_RE = re.compile(r"\b(?:interface|if|port)\s+([A-Za-z]+[A-Za-z0-9/._-]*)", re.I)
_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_HOST_AFTER_TIMESTAMP_RE = re.compile(r"^(?:<\d+>)?(?:[A-Z][a-z]{2}\s+\d{1,2}\s+\d\d:\d\d:\d\d|\d{4}-\d\d-\d\dT\S+)\s+(\S+)")


def parse_syslog_line(line: str, default_device_ip: Optional[str] = None) -> Dict[str, Any]:
    raw = (line or "").strip()
    now = time.time()
    device_hint = _extract_hostname(raw)
    device_ip = default_device_ip or _extract_ip(raw)
    severity = _extract_severity(raw)
    event_type = _extract_event_type(raw)
    interface = _extract_interface(raw)

    return {
        "id": str(uuid.uuid4()),
        "received_at": now,
        "timestamp": _extract_timestamp(raw) or now,
        "device_ip": device_ip,
        "device_name": device_hint,
        "severity": severity,
        "event_type": event_type,
        "interface": interface,
        "message": raw,
        "raw": raw,
        "source": "syslog",
    }


def _extract_timestamp(raw: str) -> Optional[float]:
    # ISO-style timestamp at start of line.
    m = re.match(r"^(?:<\d+>)?(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:?\d\d)?)", raw)
    if m:
        value = m.group(1).replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(value).timestamp()
        except Exception:
            return None

    # Classic syslog has no year, e.g. Jun 27 12:03:44. Use current year.
    m = re.match(r"^(?:<\d+>)?([A-Z][a-z]{2}\s+\d{1,2}\s+\d\d:\d\d:\d\d)", raw)
    if m:
        try:
            current_year = datetime.now().year
            return datetime.strptime("{} {}".format(current_year, m.group(1)), "%Y %b %d %H:%M:%S").timestamp()
        except Exception:
            return None
    return None


def _extract_hostname(raw: str) -> Optional[str]:
    m = _HOST_AFTER_TIMESTAMP_RE.match(raw)
    if m:
        host = m.group(1).strip()
        if host and not _IP_RE.fullmatch(host):
            return host
    return None


def _extract_ip(raw: str) -> Optional[str]:
    m = _IP_RE.search(raw)
    return m.group(0) if m else None


def _extract_severity(raw: str) -> str:
    # Cisco-style %LINK-3-UPDOWN / %SYS-5-CONFIG_I is often more useful
    # than the transport PRI value for network troubleshooting.
    m = re.search(r"%[A-Z0-9_-]+-(\d)-", raw)
    if m:
        return _FACILITY_SEVERITY.get(m.group(1), "info")

    pri = re.match(r"^<(\d{1,3})>", raw)
    if pri:
        sev_num = str(int(pri.group(1)) % 8)
        return _FACILITY_SEVERITY.get(sev_num, "info")

    lower = raw.lower()
    for word, sev in _SEVERITY_WORDS.items():
        if re.search(r"\b{}\b".format(re.escape(word)), lower):
            return sev
    return "info"


def _extract_event_type(raw: str) -> str:
    lower = raw.lower()
    for event_type, patterns in _EVENT_PATTERNS:
        for pattern in patterns:
            if re.search(pattern, lower):
                return event_type
    return "generic_syslog"


def _extract_interface(raw: str) -> Optional[str]:
    m = _INTERFACE_RE.search(raw)
    if m:
        return m.group(1)
    # Cisco messages often include Gi0/1 without the word interface.
    m = re.search(r"\b(?:Gi|Fa|Te|Eth|Ethernet|GigabitEthernet|FastEthernet)[A-Za-z0-9/._-]+\b", raw)
    return m.group(0) if m else None
