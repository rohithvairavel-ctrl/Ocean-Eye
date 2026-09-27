"""Next-Best-Observation priority engine, and the action that closes the loop
by turning a candidate target into a Copernicus watch area."""

from fastapi import APIRouter

from . import copernicus
from .next_observation import candidates, derive
from .response_api import completed

router = APIRouter(prefix="/api/v1")


@router.get("/cases/{case_id}/next-observations")
def next_observations(case_id: str, run_id: str | None = None):
    result = completed(case_id, run_id)
    return derive(result)


@router.post("/cases/{case_id}/next-observations/{candidate_id}/watch")
def watch_candidate(
    case_id: str, candidate_id: str, run_id: str | None = None, interval_minutes: int | None = None,
    auto_analyze: bool = False,
):
    result = completed(case_id, run_id)
    match = next((c for c in candidates(result) if c["id"] == candidate_id), None)
    if match is None:
        raise KeyError("Unknown next-best-observation candidate for this run")
    origin_ref = f"nbo:{case_id}:{candidate_id}"
    existing = copernicus.find_watch_by_origin(origin_ref)
    if existing:
        return {"watch_area": existing, "candidate": match, "already_watching": True}
    watch = copernicus.create_watch_area(
        name=(f"{match['target_type'].title()} · {result['name']}")[:160],
        center=match["center"],
        radius_km=match["suggested_radius_km"],
        interval_minutes=interval_minutes,
        case_id=case_id,
        run_id=result["run_id"],
        note=match["rationale"],
        source="next_best_observation",
        auto_analyze=auto_analyze,
        origin_ref=origin_ref,
    )
    return {"watch_area": watch, "candidate": match, "already_watching": False}
