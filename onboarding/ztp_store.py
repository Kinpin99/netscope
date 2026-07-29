"""
onboarding/ztp_store.py
-----------------------
File-backed ZTP/PnP-style onboarding store. It keeps pending and provisioned
records in JSON, generates simple role-based device configuration, and enrolls
approved devices into config.yaml so the existing topology/device views can
monitor them.
"""

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from utils.config_loader import PROJECT_ROOT, load_config

_MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}([:\-]?[0-9A-Fa-f]{2}){5}$")


def _now() -> float:
    return time.time()


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return cleaned.strip("-") or "device"


def _normalize_mac(mac: str) -> str:
    raw = re.sub(r"[^0-9A-Fa-f]", "", mac or "")
    if len(raw) != 12:
        return mac.strip().lower()
    return ":".join(raw[i:i + 2] for i in range(0, 12, 2)).lower()


def _identity_id(serial: str, mac: str) -> str:
    identity = "{}|{}".format(serial.strip().lower(), _normalize_mac(mac))
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12]
    return "ztp-{}".format(digest)


class ZTPStore:
    def __init__(self, config_path: Optional[str] = None):
        self.config_path = Path(config_path) if config_path else PROJECT_ROOT / "config.yaml"
        self.cfg = load_config(self.config_path)
        onboarding_cfg = self.cfg.get("onboarding", {}) or {}
        self.store_path = self._resolve_path(onboarding_cfg.get("store_file", "data/onboarding/devices.json"))
        self.templates_dir = self._resolve_path(onboarding_cfg.get("templates_dir", "data/onboarding/generated_configs"))
        self.enrollment_secret = os.environ.get("NETSCOPE_ONBOARDING_SECRET") or onboarding_cfg.get("enrollment_secret", "")
        self.auto_register = bool(onboarding_cfg.get("auto_register_on_approval", True))
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.templates_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, raw: str) -> Path:
        p = Path(raw)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p

    def _load(self) -> Dict[str, Any]:
        if not self.store_path.exists():
            return {"devices": [], "version": 1}
        with open(self.store_path) as f:
            data = json.load(f)
        data.setdefault("devices", [])
        return data

    def _save(self, data: Dict[str, Any]) -> None:
        tmp = self.store_path.with_suffix(self.store_path.suffix + ".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        tmp.replace(self.store_path)

    def _find(self, data: Dict[str, Any], device_id: str) -> Optional[Dict[str, Any]]:
        for device in data.get("devices", []):
            if device.get("id") == device_id:
                return device
        return None

    def _verify_enrollment_token(self, payload: Dict[str, Any]) -> None:
        if not self.enrollment_secret:
            return
        token = payload.get("enrollment_token") or ""
        if token != self.enrollment_secret:
            raise ValueError("Invalid onboarding enrollment token")

    def phone_home(self, payload: Dict[str, Any], request=None) -> Dict[str, Any]:
        self._verify_enrollment_token(payload)
        serial = (payload.get("serial_number") or "").strip()
        mac = _normalize_mac(payload.get("mac_address") or "")
        if not serial:
            raise ValueError("serial_number is required")
        if not _MAC_RE.match(mac):
            raise ValueError("mac_address is invalid")
        if not payload.get("management_ip"):
            raise ValueError("management_ip is required")

        device_id = _identity_id(serial, mac)
        data = self._load()
        device = self._find(data, device_id)
        now = _now()
        source_ip = request.client.host if request and request.client else None

        if not device:
            device = {
                "id": device_id,
                "serial_number": serial,
                "mac_address": mac,
                "device_type": (payload.get("device_type") or "unknown").strip().lower(),
                "model": payload.get("model") or "unknown",
                "firmware_version": payload.get("firmware_version") or "unknown",
                "management_ip": payload.get("management_ip"),
                "data_ip": payload.get("data_ip"),
                "hostname": payload.get("hostname"),
                "capabilities": payload.get("capabilities") or {},
                "status": "pending",
                "created_at": now,
                "last_seen": now,
                "phone_home_count": 1,
                "source_ip": source_ip,
                "approval": None,
                "config_path": None,
                "provisioned_at": None,
                "completion": None,
            }
            data["devices"].append(device)
        else:
            # Preserve admin approval/config fields, but update live identity data.
            device.update({
                "serial_number": serial,
                "mac_address": mac,
                "device_type": (payload.get("device_type") or device.get("device_type") or "unknown").strip().lower(),
                "model": payload.get("model") or device.get("model"),
                "firmware_version": payload.get("firmware_version") or device.get("firmware_version"),
                "management_ip": payload.get("management_ip") or device.get("management_ip"),
                "data_ip": payload.get("data_ip") or device.get("data_ip"),
                "hostname": payload.get("hostname") or device.get("hostname"),
                "capabilities": payload.get("capabilities") or device.get("capabilities") or {},
                "last_seen": now,
                "phone_home_count": int(device.get("phone_home_count", 0)) + 1,
                "source_ip": source_ip,
            })
            if device.get("status") == "rejected":
                # A rejected device stays rejected until an admin reverses it manually.
                pass

        self._save(data)
        response = {
            "device": device,
            "action": "wait_for_approval",
            "message": "Device is pending admin approval.",
        }
        if device.get("status") in {"approved", "config_ready", "provisioned"}:
            response.update({
                "action": "download_config",
                "config_url": "/onboarding/devices/{}/config".format(device_id),
                "config": self.get_generated_config(device_id),
            })
        elif device.get("status") == "rejected":
            response.update({"action": "rejected", "message": "Device onboarding was rejected by an administrator."})
        return response

    def list_devices(self, status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        data = self._load()
        devices = data.get("devices", [])
        if status_filter:
            devices = [d for d in devices if d.get("status") == status_filter]
        return sorted(devices, key=lambda d: d.get("last_seen") or d.get("created_at") or 0, reverse=True)

    def get_device(self, device_id: str) -> Optional[Dict[str, Any]]:
        data = self._load()
        return self._find(data, device_id)

    def approve_device(self, device_id: str, approval: Dict[str, Any], user=None, request=None) -> Dict[str, Any]:
        data = self._load()
        device = self._find(data, device_id)
        if not device:
            raise KeyError(device_id)

        name = (approval.get("name") or "").strip()
        if not name:
            raise ValueError("Device name is required")
        management_ip = approval.get("management_ip") or device.get("management_ip")
        data_ip = approval.get("data_ip") or device.get("data_ip") or management_ip
        if not management_ip:
            raise ValueError("management_ip is required")
        if not data_ip:
            raise ValueError("data_ip is required")

        normalized = {
            "name": name,
            "device_type": (approval.get("device_type") or device.get("device_type") or "unknown").strip().lower(),
            "role": approval.get("role") or "Network Device",
            "building": approval.get("building") or "Unassigned",
            "floor": approval.get("floor"),
            "data_ip": data_ip,
            "management_ip": management_ip,
            "snmp_community": approval.get("snmp_community") or "public",
            "snmp_if_index": int(approval.get("snmp_if_index") or 1),
            "if_speed_bps": int(approval.get("if_speed_bps") or 100000000),
            "monitoring_enabled": bool(approval.get("monitoring_enabled", True)),
            "notes": approval.get("notes"),
        }

        config_text, config_obj = self._render_config(device, normalized)
        config_filename = "{}-{}.conf".format(_slug(normalized["name"]), device_id)
        config_path = self.templates_dir / config_filename
        with open(config_path, "w") as f:
            f.write(config_text)

        now = _now()
        device["status"] = "config_ready"
        device["approval"] = {
            **normalized,
            "approved_at": now,
            "approved_by": (user or {}).get("username"),
        }
        device["config_path"] = str(config_path.relative_to(PROJECT_ROOT))
        device["generated_config"] = config_obj
        device["last_seen"] = now

        if self.auto_register and normalized["monitoring_enabled"]:
            self._enroll_in_config(device, normalized)
            device["monitoring_registered"] = True
        else:
            device["monitoring_registered"] = False

        self._save(data)
        return device

    def reject_device(self, device_id: str, user=None, request=None) -> Dict[str, Any]:
        data = self._load()
        device = self._find(data, device_id)
        if not device:
            raise KeyError(device_id)
        device["status"] = "rejected"
        device["rejected_at"] = _now()
        device["rejected_by"] = (user or {}).get("username")
        self._save(data)
        return device

    def mark_complete(self, device_id: str, completion_status: str, message: Optional[str], request=None) -> Dict[str, Any]:
        data = self._load()
        device = self._find(data, device_id)
        if not device:
            raise KeyError(device_id)
        now = _now()
        if completion_status.lower() in {"success", "ok", "complete", "completed"}:
            device["status"] = "provisioned"
            device["provisioned_at"] = now
        else:
            device["status"] = "config_failed"
        device["completion"] = {
            "status": completion_status,
            "message": message,
            "completed_at": now,
            "source_ip": request.client.host if request and request.client else None,
        }
        device["last_seen"] = now
        self._save(data)
        return device

    def get_generated_config(self, device_id: str) -> Dict[str, Any]:
        device = self.get_device(device_id)
        if not device:
            raise KeyError(device_id)
        rel = device.get("config_path")
        config_text = ""
        if rel:
            path = PROJECT_ROOT / rel
            if path.exists():
                config_text = path.read_text()
        return {
            "device_id": device_id,
            "status": device.get("status"),
            "config_text": config_text,
            "config": device.get("generated_config") or {},
        }

    def _render_config(self, device: Dict[str, Any], approval: Dict[str, Any]):
        controller_ip = os.environ.get("NETSCOPE_CONTROLLER_IP", "controller.local")
        config_obj = {
            "hostname": approval["name"],
            "device_type": approval["device_type"],
            "role": approval["role"],
            "building": approval["building"],
            "floor": approval.get("floor"),
            "management_ip": approval["management_ip"],
            "data_ip": approval["data_ip"],
            "snmp": {
                "enabled": True,
                "community": approval["snmp_community"],
                "if_index": approval["snmp_if_index"],
            },
            "monitoring": {
                "enabled": approval["monitoring_enabled"],
                "controller": controller_ip,
            },
            "identity": {
                "serial_number": device.get("serial_number"),
                "mac_address": device.get("mac_address"),
                "model": device.get("model"),
            },
        }
        lines = [
            "# NetScope ZTP-lite generated configuration",
            "# This is a simulation-friendly config, not vendor CLI syntax.",
            "hostname={}".format(config_obj["hostname"]),
            "device_type={}".format(config_obj["device_type"]),
            "role={}".format(config_obj["role"]),
            "building={}".format(config_obj["building"]),
            "floor={}".format(config_obj.get("floor") or ""),
            "management_ip={}".format(config_obj["management_ip"]),
            "data_ip={}".format(config_obj["data_ip"]),
            "snmp_enabled=true",
            "snmp_community={}".format(config_obj["snmp"]["community"]),
            "snmp_if_index={}".format(config_obj["snmp"]["if_index"]),
            "monitoring_enabled={}".format(str(config_obj["monitoring"]["enabled"]).lower()),
            "controller={}".format(controller_ip),
            "serial_number={}".format(device.get("serial_number")),
            "mac_address={}".format(device.get("mac_address")),
            "model={}".format(device.get("model")),
            "",
        ]
        return "\n".join(lines), config_obj

    def _enroll_in_config(self, device: Dict[str, Any], approval: Dict[str, Any]) -> None:
        """Append or update the approved device in config.yaml devices."""
        with open(self.config_path) as f:
            raw_cfg = yaml.safe_load(f) or {}
        raw_cfg.setdefault("devices", [])

        ip = approval["data_ip"]
        new_entry = {
            "ip": ip,
            "name": approval["name"],
            "building": approval["building"],
            "floor": approval.get("floor"),
            "device_type": approval["device_type"],
            "role": approval["role"],
            "source": "ztp_onboarding",
            "serial_number": device.get("serial_number"),
            "mac_address": device.get("mac_address"),
            "sensors": {
                "snmp": {
                    "enabled": True,
                    "host": approval["management_ip"],
                    "community": approval["snmp_community"],
                    "port": 161,
                    "if_index": approval["snmp_if_index"],
                    "if_speed_bps": approval["if_speed_bps"],
                    "output_ip": ip,
                }
            },
        }
        # Remove None fields for cleaner YAML.
        new_entry = {k: v for k, v in new_entry.items() if v is not None}

        updated = False
        for idx, existing in enumerate(raw_cfg["devices"]):
            if existing.get("ip") == ip or existing.get("serial_number") == device.get("serial_number"):
                raw_cfg["devices"][idx] = new_entry
                updated = True
                break
        if not updated:
            raw_cfg["devices"].append(new_entry)

        backup = self.config_path.with_suffix(self.config_path.suffix + ".bak")
        if self.config_path.exists() and not backup.exists():
            backup.write_text(self.config_path.read_text())
        with open(self.config_path, "w") as f:
            yaml.safe_dump(raw_cfg, f, default_flow_style=False)
