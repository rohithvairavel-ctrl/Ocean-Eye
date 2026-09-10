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
