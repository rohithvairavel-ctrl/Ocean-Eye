"""HTTP surface for the live-data control plane."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

from . import acquisition_plan, copernicus, live_data, marine
from .response_api import completed

router = APIRouter(prefix="/api/v1")


@router.get("/live-data/status")
def live_status(case_id: str | None = None):
    """Lightweight: reads backend state only; never calls external services."""
    return live_data.status(case_id)


@router.post("/live-data/acquisition-plan/refresh")
def refresh_plan():
    """Operator-requested plan refresh (bypasses the 12 h throttle)."""
    return acquisition_plan.refresh(force=True)


@router.post("/live-data/ocean/metadata/refresh")
def refresh_ocean_metadata():
    return marine.refresh_metadata(force=True)


@router.post("/live-data/ocean/refresh")
def refresh_ocean(watch_id: str):
    """Retrieve AOI-averaged currents (now-6 h .. now+48 h) for one watch area.
    Returns AUTH_REQUIRED / NOT_INSTALLED honestly when access is unavailable."""
    area = copernicus.get_watch_area(watch_id)
    bbox = copernicus.bbox_for([area["center_lon"], area["center_lat"]], area["radius_km"])
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    state = marine.refresh_subset(bbox, now - timedelta(hours=6), now + timedelta(hours=48))
    return {k: v for k, v in state.items() if k != "series"} | {
        "records": len((state.get("series") or {}).get("records", []))
    }


@router.get("/cases/{case_id}/environment/series")
def environment_series(case_id: str, run_id: str | None = None):
    """Current at observation, latest available, and +6/+12/+24/+48 h, taken
    from the exact forcing the run used. Forecast values are never labelled as
    observations."""
    result = completed(case_id, run_id)
    return {
        "run_id": result["run_id"],
        **marine.time_series(result.get("environment") or {}, result["observation_time"]),
    }
