"""Copernicus integration and Next-Best-Observation tests.

All Copernicus Data Space network calls are replaced with httpx.MockTransport
handlers -- no test in this file reaches the real network. Credentials are
explicitly unset unless a test sets them, matching how the app behaves out of
the box.
"""

import json

import httpx
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from fastapi.testclient import TestClient

from backend import copernicus, datasets, engine, next_observation, storage
from backend.app import app


def _tiff_bytes(value=0.02):
    data = np.full((4, 4), value, dtype="float32")
    with rasterio.io.MemoryFile() as memfile:
        with memfile.open(
            driver="GTiff", height=4, width=4, count=1, dtype="float32",
            crs="EPSG:4326", transform=from_origin(80.0, 13.0, 0.01, 0.01),
        ) as dst:
            dst.write(data, 1)
        return memfile.read()


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    import backend.app as app_module
    import backend.config as config

    data_dir = tmp_path / "data"
    for name in ("satellite", "ais", "environment", "geospatial", "demo", "cases"):
        (data_dir / name).mkdir(parents=True)
    modules = [config, app_module, storage, datasets, engine, copernicus]
    previous = [m.DATA for m in modules]
    for m in modules:
        m.DATA = data_dir
    storage.init()
    monkeypatch.delenv("CDSE_CLIENT_ID", raising=False)
    monkeypatch.delenv("CDSE_CLIENT_SECRET", raising=False)
    copernicus.reset_token_cache()
    try:
        yield data_dir
    finally:
        for m, value in zip(modules, previous):
            m.DATA = value


@pytest.fixture
def client(isolated):
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Watch-area CRUD
# ---------------------------------------------------------------------------


def test_watch_area_crud_and_audit(isolated):
    area = copernicus.create_watch_area("Bay of Bengal watch", [80.9, 12.1], 25, 30)
    assert area["status"] == "ACTIVE"
    assert area["last_status"] == "PENDING"
    listed = copernicus.list_watch_areas()
    assert len(listed) == 1 and listed[0]["id"] == area["id"]
    updated = copernicus.update_watch_area(area["id"], status="PAUSED", interval_minutes=60)
    assert updated["status"] == "PAUSED" and updated["interval_minutes"] == 60
    events = storage.events("global")
    actions = [e["action"] for e in events]
    assert "watch_area_created" in actions and "watch_area_updated" in actions
    copernicus.delete_watch_area(area["id"])
    assert copernicus.list_watch_areas() == []
    with pytest.raises(KeyError):
        copernicus.get_watch_area(area["id"])


def test_watch_area_rejects_invalid_input(isolated):
    with pytest.raises(ValueError):
        copernicus.create_watch_area("Bad radius", [80.9, 12.1], 0)
    with pytest.raises(ValueError):
        copernicus.create_watch_area("Bad radius", [80.9, 12.1], 501)
    with pytest.raises(ValueError):
        copernicus.create_watch_area("Bad center", [200, 12.1], 10)
    with pytest.raises(ValueError):
        copernicus.create_watch_area("Bad interval", [80.9, 12.1], 10, interval_minutes=1)


# ---------------------------------------------------------------------------
# Discovery pass: credentials, network, dedup
# ---------------------------------------------------------------------------


def test_check_watch_area_discovers_via_public_catalogue_without_credentials(isolated):
    """CDSE's STAC catalogue is publicly searchable with no OAuth client at all --
    only imagery retrieval (the Process API) needs credentials. A watch area with
    no CDSE_CLIENT_ID/SECRET configured must still discover real scenes; only the
    per-scene imagery step is AUTH_REQUIRED."""
    area = copernicus.create_watch_area("No creds", [80.9, 12.1], 10)

    def handler(request):
        url = str(request.url)
        assert "Authorization" not in request.headers  # genuinely unauthenticated
        if "stac.dataspace" in url and "/items" in url:
            return httpx.Response(
                200,
                json={
                    "features": [
                        {
                            "id": "S1A_PUBLIC_ONLY",
                            "properties": {"datetime": "2026-09-20T00:00:00Z"},
                            "assets": {"data": {}},
                        }
                    ]
                },
            )
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as fake:
        result = copernicus.check_watch_area(area, fake)

    assert result["status"] == "OK"
    assert len(result["new_observations"]) == 1
    assert result["new_observations"][0]["status"] == "AUTH_REQUIRED"  # imagery, not discovery
    observations = copernicus.list_observations(area["id"])
    assert observations[0]["status"] == "AUTH_REQUIRED"
    assert observations[0]["mode"] == "REAL"  # the discovered scene itself is real, not fabricated
    assert observations[0]["provenance"]["catalogue_source"] == "PUBLIC_CATALOGUE"
    refreshed = copernicus.get_watch_area(area["id"])
    assert refreshed["last_status"] == "OK"
    assert refreshed["consecutive_failures"] == 0


def test_check_watch_area_falls_back_to_authenticated_search_when_public_needs_auth(isolated, monkeypatch):
    """Defensive path: if a deployment/collection ever requires auth even for STAC
    search (401/403), and credentials ARE configured, fall back to the token-based
    search rather than reporting AUTH_REQUIRED outright."""
    monkeypatch.setenv("CDSE_CLIENT_ID", "id")
    monkeypatch.setenv("CDSE_CLIENT_SECRET", "secret")
    area = copernicus.create_watch_area("Auth-gated catalogue", [80.9, 12.1], 10)

    def handler(request):
        url = str(request.url)
        if "openid-connect/token" in url:
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 600})
        if "stac.dataspace" in url and "/items" in url:
            return httpx.Response(401, json={"detail": "auth required for this collection"})
        if "stac.dataspace" in url:  # authenticated POST /v1/search fallback
            assert request.headers.get("Authorization") == "Bearer tok"
            return httpx.Response(
                200,
                json={"features": [{"id": "S1A_AUTH_FALLBACK", "properties": {"datetime": "2026-09-20T00:00:00Z"}, "assets": {}}]},
            )
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as fake:
        result = copernicus.check_watch_area(area, fake, fetch_imagery=False)

    assert result["status"] == "OK"
    assert len(result["new_observations"]) == 1
    observations = copernicus.list_observations(area["id"])
    assert observations[0]["provenance"]["catalogue_source"] == "AUTHENTICATED"


def test_check_watch_area_public_catalogue_needs_auth_and_none_configured(isolated):
    """If the public endpoint itself demands auth and no credentials are
    configured, the watch area correctly reports AUTH_REQUIRED (not a silent
    empty result) -- this is the one case discovery still can't proceed."""
    area = copernicus.create_watch_area("Locked catalogue", [80.9, 12.1], 10)

    def handler(request):
        return httpx.Response(401, json={"detail": "auth required"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as fake:
        result = copernicus.check_watch_area(area, fake)
    assert result["status"] == "AUTH_REQUIRED"
    refreshed = copernicus.get_watch_area(area["id"])
    assert refreshed["last_status"] == "AUTH_REQUIRED"
    assert refreshed["consecutive_failures"] == 1


def test_check_watch_area_offline_on_network_error(isolated, monkeypatch):
    monkeypatch.setenv("CDSE_CLIENT_ID", "id")
    monkeypatch.setenv("CDSE_CLIENT_SECRET", "secret")
    area = copernicus.create_watch_area("Unreachable", [80.9, 12.1], 10)

    def handler(request):
        raise httpx.ConnectError("simulated network failure", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as fake:
        result = copernicus.check_watch_area(area, fake)
    assert result["status"] == "OFFLINE"
    assert copernicus.get_watch_area(area["id"])["last_status"] == "OFFLINE"


def test_check_watch_area_auth_rejected_by_token_endpoint(isolated, monkeypatch):
    monkeypatch.setenv("CDSE_CLIENT_ID", "id")
    monkeypatch.setenv("CDSE_CLIENT_SECRET", "wrong")
    area = copernicus.create_watch_area("Bad creds", [80.9, 12.1], 10)

    def handler(request):
        return httpx.Response(401, json={"error": "invalid_client"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as fake:
        result = copernicus.check_watch_area(area, fake)
    assert result["status"] == "AUTH_REQUIRED"


def test_check_watch_area_discovers_and_dedups(isolated, monkeypatch):
    monkeypatch.setenv("CDSE_CLIENT_ID", "id")
    monkeypatch.setenv("CDSE_CLIENT_SECRET", "secret")
    area = copernicus.create_watch_area("Live watch", [80.9, 12.1], 10)
    tiff = _tiff_bytes()
    calls = {"process": 0}

    def handler(request):
        url = str(request.url)
        if "openid-connect/token" in url:
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 600})
        if "stac.dataspace" in url:
            return httpx.Response(
                200,
                json={
                    "features": [
                        {
                            "id": "S1A_TEST_SCENE_1",
                            "properties": {"datetime": "2026-09-20T00:00:00Z"},
                            "assets": {"data": {}},
                        }
                    ]
                },
            )
        if "sh.dataspace" in url:
            calls["process"] += 1
            return httpx.Response(200, content=tiff, headers={"content-type": "image/tiff"})
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as fake:
        first = copernicus.check_watch_area(area, fake)
        second = copernicus.check_watch_area(area, fake)

    assert first["status"] == "OK"
    assert len(first["new_observations"]) == 1
    assert first["new_observations"][0]["status"] == "READY_TO_IMPORT"
    assert second["new_observations"] == []  # deduped by stac id
    assert calls["process"] == 1  # not re-fetched on the deduped pass

    observations = copernicus.list_observations(area["id"])
    assert len(observations) == 1
    assert observations[0]["mode"] == "REAL"
    assert observations[0]["status"] == "READY_TO_IMPORT"
    artifact = storage.DATA / "satellite" / observations[0]["artifact"]
    assert artifact.is_file()
    with rasterio.open(artifact) as src:
        tags = src.tags()
        assert tags["source_type"] == "REAL"
        assert tags["radiometry"] == "sigma0_db"
        saved = src.read(1)
    # 0.02 linear -> 10*log10(0.02) ≈ -16.99 dB; confirms the unit conversion actually ran.
    assert saved[0, 0] == pytest.approx(10 * np.log10(0.02), abs=0.01)

    refreshed = copernicus.get_watch_area(area["id"])
    assert refreshed["last_status"] == "OK"
    assert refreshed["consecutive_failures"] == 0

    events = [e["action"] for e in storage.events("global")]
    assert events.count("copernicus_observation") == 1


def test_monitor_tick_discovers_via_public_catalogue_without_credentials(isolated, monkeypatch):
    """Scheduled ticks no longer short-circuit to AUTH_REQUIRED just because no
    CDSE client is configured -- they attempt real (mocked, here) public
    catalogue discovery like a manual check-now would."""
    import asyncio

    def handler(request):
        return httpx.Response(200, json={"features": []})

    real_client = httpx.Client
    monkeypatch.setattr(
        copernicus.httpx, "Client", lambda *a, **kw: real_client(transport=httpx.MockTransport(handler))
    )
    copernicus.create_watch_area("Ticked", [80.9, 12.1], 10)
    results = asyncio.run(copernicus.monitor_tick())
    assert results and all(r["status"] == "OK" for r in results)


def test_monitor_tick_still_reports_auth_required_when_catalogue_itself_needs_auth(isolated, monkeypatch):
    import asyncio

    def handler(request):
        return httpx.Response(401, json={"detail": "auth required"})

    real_client = httpx.Client
    monkeypatch.setattr(
        copernicus.httpx, "Client", lambda *a, **kw: real_client(transport=httpx.MockTransport(handler))
    )
    copernicus.create_watch_area("Ticked", [80.9, 12.1], 10)
    results = asyncio.run(copernicus.monitor_tick())
    assert results and all(r["status"] == "AUTH_REQUIRED" for r in results)


def test_auto_analyze_without_linked_case_waits_for_data(isolated, monkeypatch):
    monkeypatch.setenv("CDSE_CLIENT_ID", "id")
    monkeypatch.setenv("CDSE_CLIENT_SECRET", "secret")
    area = copernicus.create_watch_area(
        "Standalone watch", [80.9, 12.1], 10, auto_analyze=True
    )
    assert area["auto_analyze"] == 1
    tiff = _tiff_bytes()

    def handler(request):
        url = str(request.url)
        if "openid-connect/token" in url:
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 600})
        if "stac.dataspace" in url:
            return httpx.Response(
                200,
                json={"features": [{"id": "S1A_NOCASE", "properties": {"datetime": "2026-09-20T00:00:00Z"}, "assets": {}}]},
            )
        if "sh.dataspace" in url:
            return httpx.Response(200, content=tiff, headers={"content-type": "image/tiff"})
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as fake:
        result = copernicus.check_watch_area(area, fake)
    assert result["status"] == "OK"
    assert result["new_observations"][0]["status"] == "WAITING_FOR_DATA"
    observations = copernicus.list_observations(area["id"])
    assert observations[0]["status"] == "WAITING_FOR_DATA"
    assert "reason" in observations[0]["provenance"]["auto_analyze"]
    # No case fabricated.
    with storage.connect() as db:
        count = db.execute("SELECT COUNT(*) AS n FROM cases").fetchone()["n"]
    assert count == 0


def test_auto_analyze_completes_loop_when_case_has_real_inputs(isolated, monkeypatch):
    monkeypatch.setenv("CDSE_CLIENT_ID", "id")
    monkeypatch.setenv("CDSE_CLIENT_SECRET", "secret")
    datasets.create_demo()
    demo_case = {
        "name": "Bay of Bengal · SAR investigation",
        "observation_time": "2025-08-12T14:20:00Z",
        "source_type": "SYNTHETIC",
        "satellite": "satellite/demo_sar.tif",
        "ais": "ais/demo.csv",
        "environment": "environment/demo.json",
        "release_window": ["2025-08-11T20:00:00Z", "2025-08-11T23:00:00Z"],
        "sources": {"satellite": "SYNTHETIC", "ais": "SYNTHETIC", "environment": "SYNTHETIC"},
        "seed": 428,
        "windage": 0.03,
    }
    import uuid as uuid_module

    linked_case_id = str(uuid_module.uuid4())
    with storage.connect() as db:
        db.execute(
            "INSERT INTO cases(id,name,created,config) VALUES(?,?,?,?)",
            (linked_case_id, demo_case["name"], storage.now(), storage.canonical(demo_case)),
        )

    area = copernicus.create_watch_area(
        "Linked watch", [80.55, 12.25], 10, case_id=linked_case_id, auto_analyze=True
    )

    # Reuse the real calibrated demo SAR scene as the "process API response" so the
    # engine pipeline actually has a real candidate to segment, instead of a blank tile.
    # copernicus._save_calibrated_geotiff treats its input as LINEAR POWER and applies
    # 10*log10(...), so the demo scene's dB band is first converted back to linear here
    # to round-trip to the same realistic dB values the demo case was built with.
    with rasterio.open(storage.DATA / "satellite" / "demo_sar.tif") as demo_src:
        db_band = demo_src.read(1).astype("float64")
        linear_band = (10 ** (db_band / 10)).astype("float32")
        demo_profile = demo_src.profile.copy()
    demo_profile.update(dtype="float32", count=1)
    with rasterio.io.MemoryFile() as memfile:
        with memfile.open(**demo_profile) as dst:
            dst.write(linear_band, 1)
        real_tiff = memfile.read()

    def handler(request):
        url = str(request.url)
        if "openid-connect/token" in url:
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 600})
        if "stac.dataspace" in url:
            return httpx.Response(
                200,
                json={"features": [{"id": "S1A_LINKED", "properties": {"datetime": "2025-08-12T14:20:00Z"}, "assets": {}}]},
            )
        if "sh.dataspace" in url:
            return httpx.Response(200, content=real_tiff, headers={"content-type": "image/tiff"})
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as fake:
        result = copernicus.check_watch_area(area, fake)

    assert result["status"] == "OK"
    assert result["new_observations"][0]["status"] == "ANALYZED"
    observations = copernicus.list_observations(area["id"])
    outcome = observations[0]["provenance"]["auto_analyze"]
    assert outcome["status"] == "ANALYZED"
    new_case_id = outcome["case_id"]
    new_case = storage.get_case(new_case_id)
    assert new_case["config"]["source_type"] == "SYNTHETIC"  # reused AIS/env were SYNTHETIC
    assert new_case["config"]["sources"]["satellite"] == "REAL"
    assert new_case["latest_run"] == outcome["run_id"]
    run = storage.get_run(outcome["run_id"])
    assert run["state"] == "COMPLETE"
    assert run["result"]["spill"]["area_km2"] > 0
    actions = [e["action"] for e in storage.events(new_case_id)]
    assert "auto_analyze_alert" in actions


def test_status_reports_configuration(isolated, monkeypatch):
    assert copernicus.status()["credentials_configured"] is False
    assert copernicus.status()["mode"] == "DEMO"
    assert copernicus.status()["public_catalogue_search"] is True  # true regardless of credentials
    monkeypatch.setenv("CDSE_CLIENT_ID", "id")
    monkeypatch.setenv("CDSE_CLIENT_SECRET", "secret")
    assert copernicus.status()["credentials_configured"] is True
    assert copernicus.status()["mode"] == "REAL"


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


def test_copernicus_api_crud(client, monkeypatch):
    # check-now builds its own httpx.Client internally; redirect it to a mock
    # transport so this test never reaches the real network, matching every
    # other test in this file. An empty catalogue response is enough here --
    # public-catalogue discovery itself is covered in detail above.
    def handler(request):
        return httpx.Response(200, json={"features": []})

    import backend.copernicus_api as copernicus_api_module

    real_client = httpx.Client
    monkeypatch.setattr(
        copernicus_api_module.httpx, "Client", lambda *a, **kw: real_client(transport=httpx.MockTransport(handler))
    )

    created = client.post(
        "/api/v1/copernicus/watch-areas",
        json={"name": "API watch", "center": [80.9, 12.1], "radius_km": 15},
    ).json()
    assert created["status"] == "ACTIVE"
    listed = client.get("/api/v1/copernicus/watch-areas").json()
    assert len(listed) == 1
    paused = client.patch(
        f"/api/v1/copernicus/watch-areas/{created['id']}", json={"status": "PAUSED"}
    ).json()
    assert paused["status"] == "PAUSED"
    checked = client.post(f"/api/v1/copernicus/watch-areas/{created['id']}/check-now").json()
    assert checked["status"] == "OK"  # public catalogue discovery needs no credentials
    assert checked["new_observations"] == []  # mocked catalogue returned no scenes
    observations = client.get(f"/api/v1/copernicus/watch-areas/{created['id']}/observations").json()
    assert observations == []
    status = client.get("/api/v1/copernicus/status").json()
    assert status["credentials_configured"] is False
    deleted = client.delete(f"/api/v1/copernicus/watch-areas/{created['id']}")
    assert deleted.status_code == 200
    assert client.get("/api/v1/copernicus/watch-areas").json() == []


def test_copernicus_api_rejects_bad_watch_area(client):
    response = client.post(
        "/api/v1/copernicus/watch-areas",
        json={"name": "Bad", "center": [80.9, 12.1], "radius_km": 9999},
    )
    assert response.status_code == 422


def test_health_reports_copernicus_configuration(client):
    assert client.get("/api/v1/health").json()["copernicus_configured"] is False


# ---------------------------------------------------------------------------
# Next-Best-Observation
# ---------------------------------------------------------------------------


def _fake_result():
    return {
        "run_id": "run-1",
        "case_id": "case-1",
        "analysis_hash": "deadbeef",
        "name": "Test incident",
        "observation_time": "2026-09-20T12:00:00Z",
        "spill": {"centroid": [80.55, 12.25], "area_km2": 42.6},
        "origin": {"centroid": [80.5, 12.2], "radius90_km": 6.4},
        "vessels": [
            {
                "mmsi": "900000001",
                "name": "DEMO Seabreeze",
                "score": 87.5,
                "position": [80.52, 12.22],
                "nearest_time": "2026-09-20T10:00:00Z",
                "gaps": [
                    {
                        "minutes": 95,
                        "overlaps_release": True,
                        "geometry": {"type": "LineString", "coordinates": [[80.5, 12.2], [80.6, 12.3]]},
                    }
                ],
            }
        ],
        "impact": {
            "receptors": [
                {
                    "name": "Demo Marine Reserve",
                    "kind": "protected_area",
                    "first_overlap_h": 12,
                    "coordinates": [80.9, 12.08],
                }
            ]
        },
    }


def test_next_observation_candidates_are_ranked_and_explained():
    result = _fake_result()
    ranked = next_observation.candidates(result)
    ids = [c["id"] for c in ranked]
    assert set(ids) == {"origin", "vessel-900000001", "gap-900000001-0", "receptor-0", "spill"}
    scores = [c["score"] for c in ranked]
    assert scores == sorted(scores, reverse=True)
    for c in ranked:
        assert 0 <= c["score"] <= 100
        assert "rationale" in c and "basis" in c
        total_contribution = sum(x["contribution"] for x in c["components"])
        assert total_contribution == pytest.approx(c["score"], abs=0.2)


def test_next_observation_omits_absent_evidence():
    result = _fake_result()
    result["vessels"] = []
    result["impact"]["receptors"] = []
    ranked = next_observation.candidates(result)
    ids = {c["id"] for c in ranked}
    assert ids == {"origin", "spill"}


def test_next_observation_api_and_watch_action(isolated):
    datasets.create_demo()
    with TestClient(app) as c:
        case = c.post("/api/v1/cases/demo").json()
        run = c.post(f"/api/v1/cases/{case['id']}/run", json={}).json()
        run_id = run["run_id"]
        import time as time_module

        status = None
        for _ in range(120):
            status = c.get(f"/api/v1/runs/{run_id}").json()
            if status["state"] != "RUNNING":
                break
            time_module.sleep(0.2)
        assert status["state"] == "COMPLETE", status

        view = c.get(f"/api/v1/cases/{case['id']}/next-observations").json()
        assert view["candidates"]
        candidate_id = view["candidates"][0]["id"]

        created = c.post(f"/api/v1/cases/{case['id']}/next-observations/{candidate_id}/watch").json()
        assert created["watch_area"]["source"] == "next_best_observation"
        assert created["watch_area"]["case_id"] == case["id"]
        assert created["candidate"]["id"] == candidate_id

        watches = c.get("/api/v1/copernicus/watch-areas").json()
        assert any(w["id"] == created["watch_area"]["id"] for w in watches)

        bad = c.post(f"/api/v1/cases/{case['id']}/next-observations/not-a-real-id/watch")
        assert bad.status_code == 404
