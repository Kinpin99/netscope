"""Fast traffic and live-score endpoints backed by api.live_runtime."""

from fastapi import APIRouter, Query

from api.live_runtime import get_live_runtime

router = APIRouter()


@router.get("/recent")
def recent_traffic(
    minutes: int = Query(15, ge=1, le=1440),
    max_devices: int = Query(100, ge=1, le=500),
):
    return get_live_runtime().recent_traffic(minutes=minutes, max_devices=max_devices)


@router.get("/device/{device_ip}")
def recent_device_traffic(
    device_ip: str,
    minutes: int = Query(60, ge=1, le=1440),
):
    return get_live_runtime().recent_device_traffic(device_ip=device_ip, minutes=minutes)


@router.get("/live-scores")
def live_scores(
    minutes: int = Query(3, ge=1, le=10),
):
    """Return cached live scores.

    A background file-inference loop now persists completed windows through
    AlertEngine, so the Alerts and Devices pages stay consistent with this
    live preview even when Kafka/stream_router is not being used.
    """
    runtime = get_live_runtime()
    runtime.refresh_scores_if_data_changed()
    return {"scores": runtime.get_live_scores(minutes=minutes).get("scores", [])}
