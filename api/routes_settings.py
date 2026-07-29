"""Admin-facing configuration endpoints used by the dashboard Settings page."""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from api.security import audit_event, require_admin
from utils.device_registry import DeviceRegistry

router = APIRouter()


class SnmpPayload(BaseModel):
    enabled: bool = False
    host: Optional[str] = None
    community: str = "public"
    port: int = Field(161, ge=1, le=65535)
    if_index: int = Field(1, ge=1)
    if_speed_bps: int = Field(100000000, ge=1)
    output_ip: Optional[str] = None


class DevicePayload(BaseModel):
    ip: str
    name: str = Field(..., min_length=1, max_length=120)
    building: str = "Unassigned"
    floor: Optional[str] = None
    device_type: str = "network_device"
    role: Optional[str] = None
    source: str = "settings_page"
    serial_number: Optional[str] = None
    mac_address: Optional[str] = None
    snmp: SnmpPayload = Field(default_factory=SnmpPayload)


def _registry() -> DeviceRegistry:
    return DeviceRegistry()


def _payload_dict(payload: BaseModel) -> Dict[str, Any]:
    return payload.model_dump() if hasattr(payload, "model_dump") else _payload_dict(payload)


@router.get("/devices")
def list_configured_devices(user=Depends(require_admin)):
    return {"devices": _registry().list_devices()}


@router.post("/devices", status_code=status.HTTP_201_CREATED)
def create_configured_device(payload: DevicePayload, request: Request, user=Depends(require_admin)):
    try:
        device = _registry().create(_payload_dict(payload))
    except ValueError as exc:
        audit_event("settings_device_create_failed", request=request, user=user, success=False, detail=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))
    audit_event("settings_device_created", request=request, user=user, target=device["ip"], detail=device["name"])
    return {"device": device, "restart_recommended": True}


@router.put("/devices/{device_ip}")
def update_configured_device(device_ip: str, payload: DevicePayload, request: Request, user=Depends(require_admin)):
    try:
        device = _registry().update(device_ip, _payload_dict(payload))
    except KeyError:
        audit_event("settings_device_update_missing", request=request, user=user, success=False, target=device_ip)
        raise HTTPException(status_code=404, detail="Configured device not found")
    except ValueError as exc:
        audit_event("settings_device_update_failed", request=request, user=user, success=False, target=device_ip, detail=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))
    audit_event("settings_device_updated", request=request, user=user, target=device["ip"], detail=device["name"])
    return {"device": device, "restart_recommended": True}


@router.delete("/devices/{device_ip}")
def delete_configured_device(device_ip: str, request: Request, user=Depends(require_admin)):
    try:
        device = _registry().delete(device_ip)
    except KeyError:
        audit_event("settings_device_delete_missing", request=request, user=user, success=False, target=device_ip)
        raise HTTPException(status_code=404, detail="Configured device not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit_event("settings_device_deleted", request=request, user=user, target=device_ip, detail=device.get("name"))
    return {"deleted": device, "restart_recommended": True}
