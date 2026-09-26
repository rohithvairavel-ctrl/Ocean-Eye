"""HTTP surface for Copernicus watch areas and the live-monitor status card."""

from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import copernicus

router = APIRouter(prefix="/api/v1/copernicus")


class WatchAreaInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    center: tuple[float, float]
    radius_km: float = Field(gt=0, le=500)
    interval_minutes: int = Field(
        default=copernicus.DEFAULT_INTERVAL_MINUTES,
        ge=copernicus.MIN_INTERVAL_MINUTES,
        le=copernicus.MAX_INTERVAL_MINUTES,
    )
    case_id: str | None = None
    run_id: str | None = None
    note: str = ""
    source: str = "operator"


class WatchAreaUpdate(BaseModel):
    status: Literal["ACTIVE", "PAUSED"] | None = None
    interval_minutes: int | None = Field(
        default=None, ge=copernicus.MIN_INTERVAL_MINUTES, le=copernicus.MAX_INTERVAL_MINUTES
    )


@router.get("/status")
def status():
    return copernicus.status()


@router.get("/watch-areas")
def watch_areas():
    return copernicus.list_watch_areas()


@router.post("/watch-areas")
def create_watch_area(spec: WatchAreaInput):
    return copernicus.create_watch_area(
        spec.name, list(spec.center), spec.radius_km, spec.interval_minutes,
        spec.case_id, spec.run_id, spec.note, spec.source,
    )


@router.patch("/watch-areas/{watch_id}")
def update_watch_area(watch_id: str, spec: WatchAreaUpdate):
    return copernicus.update_watch_area(watch_id, spec.status, spec.interval_minutes)


@router.delete("/watch-areas/{watch_id}")
def delete_watch_area(watch_id: str):
    copernicus.delete_watch_area(watch_id)
    return {"deleted": watch_id}


@router.get("/watch-areas/{watch_id}/observations")
def watch_area_observations(watch_id: str):
    copernicus.get_watch_area(watch_id)
    return copernicus.list_observations(watch_id)


@router.post("/watch-areas/{watch_id}/check-now")
def check_now(watch_id: str):
    area = copernicus.get_watch_area(watch_id)
    with httpx.Client() as client:
        return copernicus.check_watch_area(area, client)
