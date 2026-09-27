"""OCEAN-EYE TruthLoop API -- adversarial hypothesis testing over a completed run.

GET returns the latest recorded challenge for the run (or the unchallenged
hypothesis view if the conclusion has never been challenged). POST runs a
fresh, deterministic recomputation of every challenge, stores it with a
SHA-256 of its canonical JSON, and audits it -- so "CHALLENGE THIS
CONCLUSION" is a real, recorded investigative action, never a cached display.
"""

import hashlib
import json

from fastapi import APIRouter

from . import storage
from .response_api import completed
from .truthloop import derive, unchallenged

router = APIRouter(prefix="/api/v1")


def latest_challenge(run_id):
    with storage.connect() as db:
        row = db.execute(
            "SELECT created,payload,hash FROM truthloop_runs WHERE run_id=?", (run_id,)
        ).fetchone()
    if not row:
        return None
    return {**json.loads(row["payload"]), "challenged_at": row["created"], "sha256": row["hash"]}


@router.get("/cases/{case_id}/truthloop")
def truthloop(case_id: str, run_id: str | None = None):
    result = completed(case_id, run_id)
    stored = latest_challenge(result["run_id"])
    return stored or unchallenged(result)


@router.post("/cases/{case_id}/truthloop/challenge")
def truthloop_challenge(case_id: str, run_id: str | None = None):
    result = completed(case_id, run_id)
    payload = derive(result)
    canonical = storage.canonical(payload)
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    created = storage.now()
    with storage.connect() as db:
        db.execute(
            "INSERT OR REPLACE INTO truthloop_runs(run_id,case_id,created,payload,hash) VALUES(?,?,?,?,?)",
            (result["run_id"], case_id, created, canonical, digest),
        )
    storage.audit(
        case_id,
        "truthloop_challenge_run",
        {
            "run_id": payload["run_id"],
            "stability": payload["stability"]["state"],
            "reversals": payload["stability"]["reversals"],
            "sha256": digest,
        },
    )
    return {**payload, "challenged_at": created, "sha256": digest}
