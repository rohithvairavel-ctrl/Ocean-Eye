"""Run-bound intelligence views and immutable, audited planning scenarios."""

import hashlib
import json
import uuid
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from . import storage
from .intelligence import derive, dossier, alert
from .classification import INCIDENT_TYPES
from .response import ScenarioInput, evaluate

router = APIRouter(prefix="/api/v1")


def completed(case_id, run_id=None):
    case = storage.get_case(case_id)
    identifier = run_id or case["latest_run"]
    if not identifier:
        raise HTTPException(
            409,
            "Complete an investigation before opening intelligence or response planning",
        )
    run = storage.get_run(identifier)
    if run["case_id"] != case_id:
        raise HTTPException(404, "Run is not part of this case")
    if run["state"] != "COMPLETE" or not run["result"]:
        raise HTTPException(409, "This investigation has no completed analysis")
    return run["result"]


@router.get("/incident-types")
def incident_types():
    return INCIDENT_TYPES


@router.get("/incidents")
def incidents():
    with storage.connect() as db:
        rows = db.execute("SELECT * FROM cases ORDER BY created DESC").fetchall()
        output = []
        for row in rows:
            cfg = json.loads(row["config"])
            if cfg.get("archived"):
                continue
            run = db.execute(
                "SELECT * FROM runs WHERE id=?", (row["latest_run"],)
            ).fetchone()
            result = json.loads(run["result"]) if run and run["result"] else None
            output.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "source_type": cfg["source_type"],
                    "observation_time": cfg["observation_time"],
                    "status": run["state"] if run else "NOT_ANALYZED",
                    "coordinates": result["spill"]["centroid"] if result else None,
                    "run_id": row["latest_run"],
                }
            )
    return output


@router.get("/cases/{case_id}/scenarios")
def scenarios(case_id: str, run_id: str | None = None):
    result = completed(case_id, run_id)
    with storage.connect() as db:
        rows = db.execute(
            "SELECT payload,hash FROM scenarios WHERE case_id=? AND run_id=? ORDER BY created DESC",
            (case_id, result["run_id"]),
        ).fetchall()
    return [{**json.loads(r["payload"]), "sha256": r["hash"]} for r in rows]


@router.get("/cases/{case_id}/intelligence")
def intelligence(case_id: str):
    result = completed(case_id)
    view = derive(result)
    for scenario in scenarios(case_id, result["run_id"]):
        view["evidence"]["nodes"].append(
            {
                "id": scenario["id"],
                "label": scenario["input"]["name"],
                "kind": "RESPONSE SCENARIO",
                "details": scenario,
            }
        )
        view["evidence"]["edges"].extend(
            [
                {"source": "forecast", "target": scenario["id"]},
                {"source": scenario["id"], "target": "proof"},
            ]
        )
        if (
            scenario["margin_to_target_h"] is not None
            and scenario["margin_to_target_h"] <= 2
        ):
            event = alert(
                "response-window-" + scenario["id"],
                "HIGH",
                "Response planning window is narrow",
                "Conservative ready time is within two hours of, or after, the selected forecast horizon.",
                {
                    "scenario_id": scenario["id"],
                    "ready_window_h": scenario["ready_window_h"],
                    "margin_h": scenario["margin_to_target_h"],
                    "operator_inputs": scenario["input"],
                },
                "Review departure delay, asset assumptions and an earlier feasible target; no interception is guaranteed.",
                result,
                scenario["target"],
                scenario["input"]["target_horizon_h"],
            )
            view["alerts"].insert(0, event)
            view["recommendations"].insert(
                0,
                {
                    "id": event["id"],
                    "action": event["next_action"],
                    **{
                        k: event[k]
                        for k in (
                            "why",
                            "data_used",
                            "assumptions",
                            "uncertainty",
                            "rule_id",
                        )
                    },
                },
            )
    from pathlib import Path

    view["derivation_provenance"] = {
        p.name: storage.digest(p)
        for p in [
            Path(__file__),
            Path(__file__).with_name("intelligence.py"),
            Path(__file__).with_name("drift.py"),
            Path(__file__).with_name("classification.py"),
        ]
    }
    view["view_basis"] = (
        "Derived from immutable run outputs; scenario records are separately hashed and audited."
    )
    return view


@router.get("/cases/{case_id}/vessels/{mmsi}/dossier")
def vessel_dossier(case_id: str, mmsi: str):
    return dossier(completed(case_id), mmsi)


@router.post("/cases/{case_id}/scenarios")
def create_scenario(case_id: str, spec: ScenarioInput):
    result = completed(case_id, spec.run_id)
    value = {
        **evaluate(result, spec),
        "id": str(uuid.uuid4()),
        "created": storage.now(),
    }
    payload = storage.canonical(value)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    with storage.connect() as db:
        db.execute(
            "INSERT INTO scenarios VALUES(?,?,?,?,?,?)",
            (value["id"], case_id, spec.run_id, value["created"], payload, digest),
        )
    storage.audit(
        case_id,
        "response_scenario_created",
        {
            "scenario_id": value["id"],
            "run_id": spec.run_id,
            "sha256": digest,
            "input": spec.model_dump(mode="json"),
        },
    )
    return {**value, "sha256": digest}


@router.get("/scenarios/{scenario_id}/export")
def export_scenario(scenario_id: str):
    with storage.connect() as db:
        row = db.execute(
            "SELECT * FROM scenarios WHERE id=?", (scenario_id,)
        ).fetchone()
    if not row:
        raise KeyError("Scenario not found")
    payload = {
        "scenario": json.loads(row["payload"]),
        "sha256": row["hash"],
        "hash_scope": "Canonical scenario JSON (sorted keys, compact separators)",
        "audit": storage.events(row["case_id"]),
    }
    return Response(
        storage.canonical(payload),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="response-{scenario_id}.json"'
        },
    )


@router.get("/cases/{case_id}/response-evidence")
def response_evidence(case_id: str, run_id: str):
    """Add a planning supplement without altering the original analysis package."""
    import io
    import zipfile

    result = completed(case_id, run_id)
    original = storage.DATA / "cases" / case_id / result["run_id"] / "evidence.zip"
    if not original.is_file():
        raise HTTPException(409, "Original evidence package is not ready")
    records = scenarios(case_id, run_id)
    files = {
        "original-evidence.zip": original.read_bytes(),
        "response-scenarios.json": storage.canonical(records).encode(),
        "audit-at-export.json": storage.canonical(storage.events(case_id)).encode(),
        "README.txt": b"Original evidence is preserved inside original-evidence.zip. Response scenarios are immutable planning records bound to the named run and analysis hash. Their sha256 fields hash canonical JSON excluding that sha256 field. Post-intervention exposure and removal are NOT MODELED. Audit is a snapshot at export time. Hashes are integrity checks, not digital signatures.\n",
    }
    manifest = (
        "\n".join(
            hashlib.sha256(data).hexdigest() + "  " + name
            for name, data in files.items()
        )
        + "\n"
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as package:
        for name, data in files.items():
            package.writestr(name, data)
        package.writestr("checksums.sha256", manifest)
    return Response(
        output.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="response-evidence-{run_id}.zip"'
        },
    )
