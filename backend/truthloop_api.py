"""OCEAN-EYE TruthLoop API -- adversarial hypothesis testing over a completed run.

Every response is a fresh, deterministic recomputation (see backend.truthloop);
nothing here is cached or fabricated. Viewing and explicitly "challenging" the
conclusion are both audited so the investigation record shows when the
adversarial pass was run.
"""

from fastapi import APIRouter

from . import storage
from .response_api import completed
from .truthloop import derive

router = APIRouter(prefix="/api/v1")


@router.get("/cases/{case_id}/truthloop")
def truthloop(case_id: str, run_id: str | None = None):
    result = completed(case_id, run_id)
    payload = derive(result)
    storage.audit(case_id, "truthloop_viewed", {"run_id": payload["run_id"], "stability": payload["stability"]["state"]})
    return payload


@router.post("/cases/{case_id}/truthloop/challenge")
def truthloop_challenge(case_id: str, run_id: str | None = None):
    result = completed(case_id, run_id)
    payload = derive(result)
    storage.audit(
        case_id,
        "truthloop_challenge_run",
        {
            "run_id": payload["run_id"],
            "stability": payload["stability"]["state"],
            "reversals": payload["stability"]["reversals"],
        },
    )
    return payload
