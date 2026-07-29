"""
utils/device_registry.py
------------------------
Safe, file-backed device inventory management for the Settings page.

The dashboard no longer needs an administrator to edit config.yaml by hand.
All writes are validated, protected by a process lock, written atomically,
and preserve the rest of the project configuration.
"""

import ipaddress
import re
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from utils import config_loader

_LOCK = threading.Lock()
_MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}([:\-]?[0-9A-Fa-f]{2}){5}$")


class DeviceRegistry:
    def __init__(self, config_path: Optional[str] = None):
        self.config_path = Path(config_path) if config_path else Path(config_loader.DEFAULT_CONFIG_PATH)

    def _load_raw(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            raise FileNotFoundError("Configuration file not found: {}".format(self.config_path))
        with open(self.config_path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        data.setdefault("devices", [])
        return data

    def _save_raw(self, data: Dict[str, Any]) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            yaml.safe_dump(data, handle, default_flow_style=False, sort_keys=False)
        tmp.replace(self.config_path)

    @staticmethod
    def _clean_text(value: Any, default: Optional[str] = None) -> Optional[str]:
        if value is None:
            return default
        cleaned = str(value).strip()
        return cleaned or default

    @staticmethod
    def _validate_ip(value: str, field: str = "ip") -> str:
        try:
            return str(ipaddress.ip_address(str(value).strip()))
        except ValueError:
            raise ValueError("{} must be a valid IPv4 or IPv6 address".format(field))

    @staticmethod
    def _validate_mac(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        value = str(value).strip().lower()
        if not _MAC_RE.match(value):
            raise ValueError("mac_address is invalid")
        raw = re.sub(r"[^0-9A-Fa-f]", "", value)
        return ":".join(raw[i:i + 2] for i in range(0, 12, 2)).lower()

    def _normalise(self, payload: Dict[str, Any], existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        base = deepcopy(existing or {})
        ip = self._validate_ip(payload.get("ip") or base.get("ip"), "ip")
        name = self._clean_text(payload.get("name"), base.get("name"))
        if not name:
            raise ValueError("name is required")

        device_type = self._clean_text(
            payload.get("device_type") or payload.get("type"),
            base.get("device_type") or base.get("type") or "network_device",
        )
        building = self._clean_text(payload.get("building"), base.get("building") or "Unassigned")
        floor = self._clean_text(payload.get("floor"), None) if "floor" in payload else base.get("floor")
        role = self._clean_text(payload.get("role"), None) if "role" in payload else base.get("role")
        source = self._clean_text(payload.get("source"), base.get("source") or "settings_page")
        serial_number = self._clean_text(payload.get("serial_number"), None) if "serial_number" in payload else base.get("serial_number")
        mac_address = self._validate_mac(payload.get("mac_address")) if "mac_address" in payload else base.get("mac_address")

        sensors = deepcopy(base.get("sensors") or {})
        if "snmp" in payload:
            snmp = payload.get("snmp") or {}
            if snmp.get("enabled", False):
                host = self._validate_ip(snmp.get("host") or ip, "snmp.host")
                community = self._clean_text(snmp.get("community"), "public")
                port = int(snmp.get("port") or 161)
                if_index = int(snmp.get("if_index") or 1)
                if_speed_bps = int(snmp.get("if_speed_bps") or 100000000)
                output_ip = self._validate_ip(snmp.get("output_ip") or ip, "snmp.output_ip")
                if not 1 <= port <= 65535:
                    raise ValueError("snmp.port must be between 1 and 65535")
                if if_index < 1:
                    raise ValueError("snmp.if_index must be at least 1")
                if if_speed_bps < 1:
                    raise ValueError("snmp.if_speed_bps must be greater than zero")
                sensors["snmp"] = {
                    "enabled": True,
                    "host": host,
                    "community": community,
                    "port": port,
                    "if_index": if_index,
                    "if_speed_bps": if_speed_bps,
                    "output_ip": output_ip,
                }
            else:
                sensors.pop("snmp", None)

        device = {
            "ip": ip,
            "name": name,
            "building": building,
            "device_type": device_type,
            "source": source,
            "sensors": sensors,
        }
        optional = {
            "floor": floor,
            "role": role,
            "serial_number": serial_number,
            "mac_address": mac_address,
        }
        for key, value in optional.items():
            if value is not None:
                device[key] = value

        # Preserve custom keys that other modules may have added.
        known = set(device.keys()) | {"type"}
        for key, value in base.items():
            if key not in known and key not in {"floor", "role", "serial_number", "mac_address"}:
                device[key] = value
        return device

    @staticmethod
    def _public_device(device: Dict[str, Any]) -> Dict[str, Any]:
        result = deepcopy(device)
        result["device_type"] = result.get("device_type") or result.get("type") or "network_device"
        snmp = (result.get("sensors") or {}).get("snmp") or {}
        result["snmp"] = {
            "enabled": bool(snmp.get("enabled", False)),
            "host": snmp.get("host") or result.get("ip"),
            "community": snmp.get("community") or "public",
            "port": int(snmp.get("port") or 161),
            "if_index": int(snmp.get("if_index") or 1),
            "if_speed_bps": int(snmp.get("if_speed_bps") or 100000000),
            "output_ip": snmp.get("output_ip") or result.get("ip"),
        }
        return result

    def list_devices(self) -> List[Dict[str, Any]]:
        data = self._load_raw()
        devices = [self._public_device(item) for item in data.get("devices", [])]
        return sorted(devices, key=lambda item: ((item.get("building") or ""), item.get("name") or ""))

    def create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with _LOCK:
            data = self._load_raw()
            device = self._normalise(payload)
            self._ensure_unique(data.get("devices", []), device)
            data["devices"].append(device)
            self._save_raw(data)
            return self._public_device(device)

    def update(self, original_ip: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        original_ip = self._validate_ip(original_ip, "original_ip")
        with _LOCK:
            data = self._load_raw()
            devices = data.get("devices", [])
            index = next((i for i, item in enumerate(devices) if item.get("ip") == original_ip), None)
            if index is None:
                raise KeyError(original_ip)
            old = devices[index]
            device = self._normalise(payload, existing=old)
            self._ensure_unique(devices, device, ignore_ip=original_ip)
            devices[index] = device

            old_name = old.get("name")
            new_name = device.get("name")
            if old_name and new_name and old_name != new_name:
                for link in (data.get("topology", {}) or {}).get("links", []) or []:
                    if link.get("source") == old_name:
                        link["source"] = new_name
                    if link.get("target") == old_name:
                        link["target"] = new_name
            self._save_raw(data)
            return self._public_device(device)

    def delete(self, device_ip: str) -> Dict[str, Any]:
        device_ip = self._validate_ip(device_ip, "device_ip")
        with _LOCK:
            data = self._load_raw()
            devices = data.get("devices", [])
            device = next((item for item in devices if item.get("ip") == device_ip), None)
            if device is None:
                raise KeyError(device_ip)
            data["devices"] = [item for item in devices if item.get("ip") != device_ip]
            name = device.get("name")
            topology = data.get("topology", {}) or {}
            if name and topology.get("links"):
                topology["links"] = [
                    link for link in topology.get("links", [])
                    if link.get("source") != name and link.get("target") != name
                ]
                data["topology"] = topology
            self._save_raw(data)
            return self._public_device(device)

    @staticmethod
    def _ensure_unique(devices: List[Dict[str, Any]], candidate: Dict[str, Any], ignore_ip: Optional[str] = None) -> None:
        for item in devices:
            if ignore_ip and item.get("ip") == ignore_ip:
                continue
            if item.get("ip") == candidate.get("ip"):
                raise ValueError("A device with this IP address already exists")
            if (item.get("name") or "").strip().lower() == (candidate.get("name") or "").strip().lower():
                raise ValueError("A device with this name already exists")
