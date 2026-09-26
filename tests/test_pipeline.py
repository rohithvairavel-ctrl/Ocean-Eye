"""Meaningful pipeline invariants and API integration tests."""

import hashlib, json, time, zipfile
import numpy as np
import pytest
from fastapi.testclient import TestClient
from backend import storage, datasets, engine, ais, drift
from backend.app import app
from backend.config import DATA


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    global DATA
    import backend.config as config
    import backend.app as app_module

    DATA = tmp_path_factory.mktemp("ocean-eye-data")
    for name in ("satellite", "ais", "environment", "geospatial", "demo", "cases"):
        (DATA / name).mkdir()
    modules = [config, app_module, storage, datasets, engine]
    previous = [m.DATA for m in modules]
    for m in modules:
        m.DATA = DATA
    try:
        with TestClient(app) as c:
            yield c
    finally:
        for m, value in zip(modules, previous):
            m.DATA = value


@pytest.fixture(scope="module")
def analysis(client):
    datasets.create_demo()
    response = client.post("/api/v1/cases/demo")
    assert response.status_code == 200
    case = response.json()
    response = client.post(f"/api/v1/cases/{case['id']}/run", json={})
    assert response.status_code == 200
    run_id = response.json()["run_id"]
    for _ in range(120):
        status = client.get("/api/v1/runs/" + run_id).json()
        if status["state"] != "RUNNING":
            break
        time.sleep(0.2)
    assert status["state"] == "COMPLETE", status
    result = client.get(f"/api/v1/cases/{case['id']}/analysis").json()
    return result


def test_connected_pipeline(analysis, client):
    assert 40 < analysis["spill"]["area_km2"] < 45
    assert len(analysis["vessels"]) == 20
    assert analysis["quality"]["rejected_rows"] == 2
    assert analysis["vessels"][0]["name"] == "DEMO Seabreeze"
    assert analysis["spill"]["oil_probability"] is None
    for section in [
        "spill",
        "origin",
        "vessels",
        "attribution",
        "forecast",
        "evidence",
        "timeline",
        "dark",
        "quality",
        "impact",
        "provenance",
    ]:
        assert (
            client.get(f"/api/v1/cases/{analysis['case_id']}/{section}").status_code
            == 200
        )
    assert [s["hours"] for s in analysis["forecast"]["steps"]] == [6, 12, 24, 48]
    assert analysis["evidence"]["nodes"] and analysis["timeline"]


def test_counterfactual_preserves_original(analysis, client):
    top = analysis["vessels"][0]
    response = client.post(
        f"/api/v1/cases/{analysis['case_id']}/counterfactual",
        json={"exclude_mmsi": top["mmsi"]},
    )
    assert response.status_code == 200
    ranking = response.json()["ranking"]
    assert len(ranking) == 19
    assert top["mmsi"] not in [v["mmsi"] for v in ranking]
    assert ranking[0]["score"] == analysis["vessels"][1]["score"]
    assert (
        client.get(f"/api/v1/cases/{analysis['case_id']}/attribution").json()[
            "ranking"
        ][0]["mmsi"]
        == top["mmsi"]
    )


def test_evidence_changes_score(analysis):
    tracks, _ = ais.load_ais(DATA / "ais/demo.csv")
    original = ais.analyze(tracks, analysis["origin"], analysis["observation_time"])
    origin = {**analysis["origin"], "centroid": [83, 16]}
    shifted = ais.analyze(tracks, origin, analysis["observation_time"])
    old = next(v for v in original if v["mmsi"] == "900000001")["score"]
    new = next(v for v in shifted if v["mmsi"] == "900000001")["score"]
    assert old - new > 30


def test_reproducibility_and_sensitivity(analysis):
    env = analysis["environment"]
    args = (
        analysis["spill"],
        env,
        analysis["observation_time"],
        analysis["origin"]["release_window"],
    )
    a, f = drift.simulate(*args)
    b, g = drift.simulate(*args)
    assert a == b and f == g
    changed, _ = drift.simulate(*args, windage=0.08)
    assert drift.distance(a["centroid"], changed["centroid"]) > 3


def test_bundle_and_custody(analysis, client):
    folder = DATA / "cases" / analysis["case_id"] / analysis["run_id"]
    with zipfile.ZipFile(folder / "evidence.zip") as z:
        assert z.testzip() is None
        for line in z.read("checksums.sha256").decode().splitlines():
            expected, name = line.split("  ", 1)
            assert hashlib.sha256(z.read(name)).hexdigest() == expected
        assert z.read("report.pdf").startswith(b"%PDF")
        assert len(z.read("report.pdf")) > 10000
    events = client.get(f"/api/v1/cases/{analysis['case_id']}/audit").json()
    previous = ""
    for event in events:
        assert event["previous_hash"] == previous
        assert (
            hashlib.sha256(
                (previous + event["time"] + event["action"] + event["details"]).encode()
            ).hexdigest()
            == event["hash"]
        )
        previous = event["hash"]


def test_upload_and_process_same_inputs(analysis, client):
    metadata = {
        "name": "Upload integration test",
        "source_type": "SYNTHETIC",
        "observation_time": analysis["observation_time"],
        "release_window": analysis["origin"]["release_window"],
        "radiometry": "sigma0_db",
    }
    files = {
        k: (name, (DATA / path).read_bytes(), mime)
        for k, name, path, mime in [
            ("satellite", "demo.tif", "satellite/demo_sar.tif", "image/tiff"),
            ("ais_file", "demo.csv", "ais/demo.csv", "text/csv"),
            ("environment", "demo.json", "environment/demo.json", "application/json"),
        ]
    }
    response = client.post(
        "/api/v1/cases/import", data={"metadata": json.dumps(metadata)}, files=files
    )
    assert response.status_code == 200, response.text
    case = response.json()
    job = client.post(f"/api/v1/cases/{case['id']}/run", json={}).json()
    for _ in range(120):
        status = client.get("/api/v1/runs/" + job["run_id"]).json()
        if status["state"] != "RUNNING":
            break
        time.sleep(0.2)
    assert status["state"] == "COMPLETE", status
    uploaded = client.get(f"/api/v1/cases/{case['id']}/analysis").json()
    assert uploaded["spill"]["area_km2"] == analysis["spill"]["area_km2"]
    assert uploaded["vessels"][0]["score"] == analysis["vessels"][0]["score"]
    assert uploaded["source_type"] == "SYNTHETIC"


def test_reject_bad_inputs_and_cross_origin(client, tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("MMSI,BaseDateTime,LAT,LON\n900000001,2025-01-01T00:00:00Z,999,80\n")
    with pytest.raises(ValueError):
        ais.load_ais(p)
    assert (
        client.post(
            "/api/v1/cases/demo", headers={"origin": "https://unrelated.example"}
        ).status_code
        == 403
    )
    assert client.get("/api/v1/cases/not-a-case/spill").status_code == 404


def test_geography_import_search(client):
    value = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [80.4, 12.3]},
                "properties": {"name": "Test harbor"},
            }
        ],
    }
    response = client.post(
        "/api/v1/geography/import",
        data={
            "source": "Integration fixture",
            "version": "1",
            "kind": "port",
            "source_type": "SYNTHETIC",
        },
        files={"file": ("test.geojson", json.dumps(value), "application/geo+json")},
    )
    assert response.status_code == 200
    assert (
        client.get("/api/v1/search", params={"q": "Test harbor"}).json()[0]["name"]
        == "Test harbor"
    )
    assert client.get("/api/v1/search", params={"q": "12.3,80.4"}).json()[0][
        "coordinates"
    ] == [80.4, 12.3]


def test_historical_ais_utc_timestamp(tmp_path):
    p = tmp_path / "ais.csv"
    p.write_text(
        "MMSI,BaseDateTime,LAT,LON,SOG,COG\n900000001,2025-01-01T00:00:00,12,80,2,90\n900000001,2025-01-01T00:15:00,12,80.001,2,90\n"
    )
    tracks, quality = ais.load_ais(p)
    assert tracks["900000001"][0]["time"].endswith("+00:00")
    assert quality["accepted_rows"] == 2


# Response intelligence preserves the original physical pipeline and its uncertainty.
def test_intelligence_and_dossier(client, analysis):
    prefix = f"/api/v1/cases/{analysis['case_id']}"
    response = client.get(prefix + "/intelligence")
    assert response.status_code == 200
    view = response.json()
    assert view["classification"]["status"] == "UNAVAILABLE"
    assert view["classification"]["calibrated_confidence"] is None
    assert view["incident"]["pollutant"] == "UNKNOWN"
    assert "UNAVAILABLE" in view["ecology"]["species_assessment"]
    assert view["alerts"]
    for event in view["alerts"]:
        assert event["rule_id"] and event["data_used"] and event["why"]
        assert event["run_id"] == analysis["run_id"]
        assert event["delivery"]["external_notifications"] == "NOT_CONFIGURED"
    nodes = {n["id"] for n in view["evidence"]["nodes"]}
    assert {
        "sar",
        "preprocessing",
        "segmentation",
        "origin",
        "forecast",
        "ecology",
        "response",
        "proof",
    } <= nodes
    assert all(
        e["source"] in nodes and e["target"] in nodes for e in view["evidence"]["edges"]
    )
    vessel = analysis["vessels"][0]
    dossier = client.get(prefix + f"/vessels/{vessel['mmsi']}/dossier").json()
    assert dossier["record"]["components"] == vessel["components"]
    assert dossier["identity"]["mmsi"]["source"]["sha256"]
    assert dossier["external_metadata"]["ownership"] is None
    assert client.get(prefix + "/vessels/000000000/dossier").status_code == 404


def test_response_timing_is_not_removal_physics(client, analysis):
    from backend.response import ScenarioInput, evaluate

    spec = ScenarioInput(run_id=analysis["run_id"], name="Baseline", kind="no_action")
    baseline = evaluate(analysis, spec)
    assert baseline["baseline"]["receptor_exposure"] == analysis["impact"]["receptors"]
    assert baseline["intervention"]["receptor_exposure_after"] is None
    assert baseline["asset"] is None and baseline["travel_km"] is None
    spec = ScenarioInput(
        run_id=analysis["run_id"],
        name="Dispatch",
        kind="dispatch",
        departure=(80, 12),
        speed_kn=10,
    )
    immediate = evaluate(analysis, spec)
    delayed = evaluate(
        analysis, spec.model_copy(update={"delay_h": 12, "kind": "delayed_response"})
    )
    assert delayed["arrival_window_h"][0] == pytest.approx(
        immediate["arrival_window_h"][0] + 12, abs=0.002
    )
    assert delayed["margin_to_target_h"] == pytest.approx(
        immediate["margin_to_target_h"] - 12, abs=0.002
    )
    assert immediate["arrival_window_h"][0] <= immediate["arrival_window_h"][1]
    assert immediate["intervention"]["removal_efficiency"] is None
    assert immediate["asset"]["availability"] == "UNVERIFIED"
    assert immediate["analysis_hash"] == analysis["analysis_hash"]


def test_scenario_persistence_export_and_run_isolation(client, analysis):
    prefix = f"/api/v1/cases/{analysis['case_id']}"
    before = client.get(prefix + "/analysis").json()
    spec = {
        "run_id": analysis["run_id"],
        "name": "Delayed planning",
        "kind": "delayed_response",
        "departure": [80, 12],
        "delay_h": 48,
    }
    response = client.post(prefix + "/scenarios", json=spec)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value in client.get(prefix + "/scenarios").json()
    exported = client.get("/api/v1/scenarios/" + value["id"] + "/export").json()
    assert (
        hashlib.sha256(storage.canonical(exported["scenario"]).encode()).hexdigest()
        == exported["sha256"]
    )
    assert any(
        json.loads(e["details"]).get("scenario_id") == value["id"]
        for e in exported["audit"]
    )
    view = client.get(prefix + "/intelligence").json()
    assert any(e["rule_id"] == "response-window-" + value["id"] for e in view["alerts"])
    assert any(n["id"] == value["id"] for n in view["evidence"]["nodes"])
    assert client.get(prefix + "/analysis").json() == before
    other = client.post("/api/v1/cases/demo").json()
    assert (
        client.post(f"/api/v1/cases/{other['id']}/scenarios", json=spec).status_code
        == 404
    )
    assert client.get(f"/api/v1/cases/{other['id']}/intelligence").status_code == 409


@pytest.mark.parametrize(
    "update",
    [
        {"departure": [200, 0]},
        {"departure": None},
        {"speed_kn": 0},
        {"speed_uncertainty_fraction": 1},
        {"target": [0, 91]},
        {"target_horizon_h": 36},
    ],
)
def test_invalid_response_inputs(client, analysis, update):
    spec = {
        "run_id": analysis["run_id"],
        "name": "Invalid",
        "kind": "dispatch",
        "departure": [80, 12],
        **update,
    }
    assert (
        client.post(
            f"/api/v1/cases/{analysis['case_id']}/scenarios", json=spec
        ).status_code
        == 422
    )


def test_classification_contract_rejects_unfounded_confidence():
    from backend.classification import ClassificationResult
    from pydantic import ValidationError

    for value in (
        {"calibrated_confidence": 0.99},
        {"predicted_class": "oil"},
        {"status": "SCREENING", "probabilities": {"oil": 1}},
        {"metrics": {"accuracy": 0.99}},
    ):
        with pytest.raises(ValidationError):
            ClassificationResult(**value)
    assert ClassificationResult().model_dump()["metrics"] is None


def test_ecology_provenance_and_no_damage_claim(analysis):
    from backend.intelligence import derive

    ecology = derive(analysis)["ecology"]
    for row in ecology["receptors"]:
        assert row["source"] and row["version"]
        assert row["status"] in ("POTENTIAL EXPOSURE", "NO SAMPLED OVERLAP")
        assert bool(row["first_overlap_time"]) == (row["first_overlap_h"] is not None)
    empty = drift.impact(analysis["forecast"], {"features": []})
    assert empty["receptors"] == []
    assert empty["level"] == "NO LOADED RECEPTOR OVERLAP"


@pytest.mark.parametrize(
    "mutate",
    [
        "non_object",
        "string_value",
        "missing_time",
        "duplicate_time",
        "units",
        "coverage",
    ],
)
def test_environment_validation_is_actionable(analysis, mutate):
    import copy

    env = copy.deepcopy(analysis["environment"])
    if mutate == "non_object":
        env = []
    elif mutate == "string_value":
        env["records"][0]["wind_east_ms"] = "unknown"
    elif mutate == "missing_time":
        del env["records"][0]["time"]
    elif mutate == "duplicate_time":
        env["records"][1]["time"] = env["records"][0]["time"]
    elif mutate == "units":
        env["units"] = "knots"
    elif mutate == "coverage":
        env["spatial_coverage"] = [80, 12]
    with pytest.raises(ValueError):
        drift.validate_environment(
            env, analysis["observation_time"], analysis["origin"]["release_window"]
        )


def test_legacy_fixture_is_archived_without_deleting_evidence(client, analysis):
    case = client.post("/api/v1/cases/demo").json()
    with storage.connect() as db:
        db.execute(
            "UPDATE cases SET name=? WHERE id=?",
            ("Upload integration test", case["id"]),
        )
    storage.init()
    assert storage.get_case(case["id"])["config"]["archived"]
    assert all(c["id"] != case["id"] for c in client.get("/api/v1/cases").json())
    assert all(c["id"] != case["id"] for c in client.get("/api/v1/incidents").json())
    assert storage.events(case["id"])[-1]["action"] == "legacy_fixture_archived"
    assert client.get("/api/v1/cases/" + case["id"]).status_code == 200


def test_response_evidence_supplement_keeps_original(client, analysis):
    import io

    response = client.get(
        f"/api/v1/cases/{analysis['case_id']}/response-evidence?run_id={analysis['run_id']}"
    )
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as package:
        for line in package.read("checksums.sha256").decode().splitlines():
            digest, name = line.split("  ", 1)
            assert hashlib.sha256(package.read(name)).hexdigest() == digest
        original = (
            DATA / "cases" / analysis["case_id"] / analysis["run_id"] / "evidence.zip"
        )
        assert package.read("original-evidence.zip") == original.read_bytes()
        for scenario in json.loads(package.read("response-scenarios.json")):
            digest = scenario.pop("sha256")
            assert (
                hashlib.sha256(storage.canonical(scenario).encode()).hexdigest()
                == digest
            )
