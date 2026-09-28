"""Live-data control plane, acquisition plans, Copernicus Marine provider and the
hardened Copernicus discovery pass.

No test here reaches the internet: every external call goes through an
httpx.MockTransport handler or a fixture shaped like the real payloads
(CDSE STAC items, ESA acquisition-plan KML, Copernicus Marine STAC metadata).
"""

import asyncio
import json
import math
import time as _time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import shape

from backend import acquisition_plan, copernicus, datasets, engine, live_data, marine, storage
from backend.app import app
from backend.drift import validate_environment
from backend.geo import circle

NOW = datetime(2026, 9, 27, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    import backend.app as app_module
    import backend.config as config

    data_dir = tmp_path / "data"
    for name in ("satellite", "ais", "environment", "geospatial", "demo", "cases"):
        (data_dir / name).mkdir(parents=True)
    modules = [config, app_module, storage, datasets, engine, copernicus, acquisition_plan, live_data]
    previous = [m.DATA for m in modules]
    for m in modules:
        m.DATA = data_dir
    storage.init()
    monkeypatch.delenv("CDSE_CLIENT_ID", raising=False)
    monkeypatch.delenv("CDSE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("COPERNICUSMARINE_SERVICE_USERNAME", raising=False)
    monkeypatch.delenv("COPERNICUSMARINE_SERVICE_PASSWORD", raising=False)
    copernicus.reset_token_cache()
    acquisition_plan._next_pass_memo.clear()
    live_data._ais_cache.clear()
    try:
        yield data_dir
    finally:
        for m, value in zip(modules, previous):
            m.DATA = value


def _stac_feature(stac_id, when="2026-09-22T00:31:01.644669Z", platform="sentinel-1d"):
    return {
        "type": "Feature",
        "id": stac_id,
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[79.4, 12.0], [81.9, 12.4], [81.6, 14.1], [79.1, 13.7], [79.4, 12.0]]],
        },
        "properties": {
            "datetime": when,
            "start_datetime": when,
            "end_datetime": when.replace("01.64", "26.64"),
            "platform": platform,
            "constellation": "sentinel-1",
            "sar:instrument_mode": "IW",
            "sar:product_type": "GRD",
            "sar:polarizations": ["VV", "VH"],
            "sat:orbit_state": "descending",
            "sat:relative_orbit": 165,
            "sat:absolute_orbit": 4683,
            "processing:level": "L1",
        },
        "links": [{"rel": "self", "href": f"https://stac.dataspace.copernicus.eu/v1/collections/sentinel-1-grd/items/{stac_id}"}],
        "assets": {"vv": {}, "vh": {}},
    }


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# Public STAC discovery, syncing, dedup, events, rate limits
# ---------------------------------------------------------------------------


def test_public_discovery_records_real_scene_metadata_and_events(isolated):
    area = copernicus.create_watch_area("Bay of Bengal", [80.55, 12.25], 30)
    seen_states = []

    def handler(request):
        # The SYNCING marker is visible while the real query is in flight.
        seen_states.append(copernicus.get_watch_area(area["id"])["last_status"])
        assert "Authorization" not in request.headers
        return httpx.Response(200, json={"features": [_stac_feature("S1D_IW_GRDH_TEST_A")]})

    with _client(handler) as fake:
        first = copernicus.check_watch_area(area, fake)
        second = copernicus.check_watch_area(copernicus.get_watch_area(area["id"]), fake)

    assert seen_states[0] == "SYNCING"
    assert first["outcome"] == "NEW_EARTH_OBSERVATION"
    assert first["new_observations"][0]["platform"] == "Sentinel-1D"
    assert second["outcome"] == "NO_NEW_ACQUISITION" and second["new_observations"] == []

    obs = copernicus.list_observations(area["id"])
    assert len(obs) == 1
    scene = obs[0]["provenance"]["scene"]
    assert scene["platform"] == "Sentinel-1D"
    assert scene["orbit_state"] == "descending" and scene["relative_orbit"] == 165
    assert scene["instrument_mode"] == "IW" and scene["product_type"] == "GRD"
    assert scene["footprint"]["type"] == "Polygon"
    assert scene["source_url"].endswith("/S1D_IW_GRDH_TEST_A")
    assert obs[0]["status"] == "AUTH_REQUIRED"  # imagery, honestly, without OAuth

    refreshed = copernicus.get_watch_area(area["id"])
    assert refreshed["sync_started"] is None
    assert refreshed["last_success"] and refreshed["last_new_at"]
    assert refreshed["last_scene_count"] == 1

    status = live_data.status()
    event = status["events"][0]
    assert event["type"] == "NEW_EARTH_OBSERVATION"
    assert event["platform"] == "Sentinel-1D" and event["acquired"].startswith("2026-09-22")


def test_scene_metadata_never_invents_missing_fields():
    scene = copernicus.scene_metadata({"id": "X", "properties": {"datetime": "2026-09-01T00:00:00Z"}})
    assert scene["platform"] is None and scene["relative_orbit"] is None
    assert scene["acquired"] == "2026-09-01T00:00:00Z"


def test_rate_limited_catalogue_is_reported_and_backs_off(isolated):
    area = copernicus.create_watch_area("Throttled", [80.9, 12.1], 10)
    with _client(lambda r: httpx.Response(429)) as fake:
        result = copernicus.check_watch_area(area, fake)
    assert result["status"] == "RATE_LIMITED"
    refreshed = copernicus.get_watch_area(area["id"])
    assert refreshed["last_status"] == "RATE_LIMITED" and refreshed["consecutive_failures"] == 1
    assert copernicus.effective_interval_minutes(refreshed) == refreshed["interval_minutes"] * 2


def test_imagery_uses_the_scene_own_time_range(isolated, monkeypatch):
    monkeypatch.setenv("CDSE_CLIENT_ID", "id")
    monkeypatch.setenv("CDSE_CLIENT_SECRET", "secret")
    area = copernicus.create_watch_area("Exact scene", [80.55, 12.25], 10)
    bodies = []
    from tests.test_copernicus import _tiff_bytes

    def handler(request):
        url = str(request.url)
        if "openid-connect/token" in url:
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 600})
        if "stac.dataspace" in url:
            return httpx.Response(200, json={"features": [_stac_feature("S1D_EXACT")]})
        if "sh.dataspace" in url:
            bodies.append(json.loads(request.content))
            return httpx.Response(200, content=_tiff_bytes())
        return httpx.Response(404)

    with _client(handler) as fake:
        copernicus.check_watch_area(area, fake)
    time_range = bodies[0]["input"]["data"][0]["dataFilter"]["timeRange"]
    assert time_range == {"from": "2026-09-22T00:29:01.644669Z", "to": "2026-09-22T00:33:26.644669Z"}
    imagery = live_data.sentinel_imagery()
    assert imagery["status"] == "CURRENT" and imagery["last_success"]


def test_pending_imagery_is_retried_once_credentials_exist(isolated, monkeypatch):
    area = copernicus.create_watch_area("Later creds", [80.55, 12.25], 10)
    with _client(lambda r: httpx.Response(200, json={"features": [_stac_feature("S1D_LATER")]})) as fake:
        copernicus.check_watch_area(area, fake)
    assert copernicus.list_observations(area["id"])[0]["status"] == "AUTH_REQUIRED"

    monkeypatch.setenv("CDSE_CLIENT_ID", "id")
    monkeypatch.setenv("CDSE_CLIENT_SECRET", "secret")
    from tests.test_copernicus import _tiff_bytes

    def handler(request):
        url = str(request.url)
        if "openid-connect/token" in url:
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 600})
        if "stac.dataspace" in url:
            return httpx.Response(200, json={"features": [_stac_feature("S1D_LATER")]})
        return httpx.Response(200, content=_tiff_bytes())

    with _client(handler) as fake:
        result = copernicus.check_watch_area(copernicus.get_watch_area(area["id"]), fake)
    assert result["imagery_retried"] == 1
    obs = copernicus.list_observations(area["id"])[0]
    assert obs["status"] == "READY_TO_IMPORT" and obs["artifact"]


def test_auto_analyze_is_blocked_not_faked_without_imagery_credentials(isolated):
    area = copernicus.create_watch_area("Auto", [80.55, 12.25], 10, auto_analyze=True)
    with _client(lambda r: httpx.Response(200, json={"features": [_stac_feature("S1D_BLOCKED")]})) as fake:
        copernicus.check_watch_area(area, fake)
    obs = copernicus.list_observations(area["id"])[0]
    assert obs["status"] == "AUTH_REQUIRED"
    assert obs["provenance"]["auto_analyze"]["status"] == "BLOCKED"
    assert "CDSE OAuth" in obs["provenance"]["auto_analyze"]["missing"][0]
    with storage.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 0


def test_auto_analyze_dependencies_name_what_is_missing(isolated):
    datasets.create_demo()
    cfg = json.loads((storage.DATA / "demo/case.json").read_text())
    # Demo inputs satisfy every dependency at the demo observation time.
    assert copernicus.auto_analyze_dependencies(cfg, cfg["observation_time"]) == []
    # A 2026 acquisition cannot reuse a 2025 release window, forcing, or AIS.
    missing = copernicus.auto_analyze_dependencies(cfg, "2026-09-22T00:31:01Z")
    assert missing and "release-window" in missing[0]
    no_ais = {**cfg, "ais": None}
    assert any("AIS data is required" in m for m in copernicus.auto_analyze_dependencies(no_ais, cfg["observation_time"]))
    shifted = {**cfg, "release_window": ["2025-08-12T02:00:00Z", "2025-08-12T05:00:00Z"]}
    later = "2025-08-13T20:00:00Z"  # forcing ends 2025-08-15T15:00 < later + 48 h
    assert any("Environmental forcing" in m for m in copernicus.auto_analyze_dependencies(shifted, later))


# ---------------------------------------------------------------------------
# Scheduler: next_poll_at is derived from real scheduler state
# ---------------------------------------------------------------------------


def _area(**over):
    base = {"status": "ACTIVE", "created": "2026-09-27T07:00:00+00:00", "last_checked": "2026-09-27T07:50:00+00:00",
            "interval_minutes": 15, "consecutive_failures": 0}
    return {**base, **over}


def test_next_check_aligns_to_scheduler_ticks_and_backoff():
    tick = NOW + timedelta(seconds=10)
    sched = {"running": True, "next_tick_at": tick}
    # due at 08:05 -> first tick at/after it, ticks every 30 s from 08:00:10
    assert copernicus.next_check_at(_area(), NOW, sched) == datetime(2026, 9, 27, 8, 5, 10, tzinfo=timezone.utc)
    # overdue areas run on the very next tick
    assert copernicus.next_check_at(_area(last_checked="2026-09-27T07:00:00+00:00"), NOW, sched) == tick
    # two failures -> 60 min effective interval
    backed = _area(consecutive_failures=2)
    assert copernicus.effective_interval_minutes(backed) == 60
    assert copernicus.next_check_at(backed, NOW, {"running": False}) == datetime(2026, 9, 27, 8, 50, tzinfo=timezone.utc)
    # paused areas are never scheduled; never-checked areas are due immediately
    assert copernicus.next_check_at(_area(status="PAUSED"), NOW, sched) is None
    assert copernicus.next_check_at(_area(last_checked=None), NOW, {"running": False}) == NOW


def test_monitor_tick_completes_poll_and_live_status_shows_countdown(isolated, monkeypatch):
    real = httpx.Client
    monkeypatch.setattr(
        copernicus.httpx, "Client",
        lambda *a, **kw: real(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"features": []}))),
    )
    area = copernicus.create_watch_area("Ticked", [80.55, 12.25], 20)
    results = asyncio.run(copernicus.monitor_tick())
    assert results[0]["status"] == "OK" and results[0]["outcome"] == "NO_NEW_ACQUISITION"
    status = live_data.status()
    cat = next(p for p in status["providers"] if p["provider"] == "sentinel1_catalogue")
    assert cat["status"] in ("NO_NEW_DATA", "WAITING_FOR_PASS")
    assert cat["last_success"] and cat["next_poll_at"]
    nxt = datetime.fromisoformat(cat["next_poll_at"])
    last = datetime.fromisoformat(copernicus.get_watch_area(area["id"])["last_checked"])
    assert abs((nxt - last).total_seconds() - copernicus.DEFAULT_INTERVAL_MINUTES * 60) < 1  # backend is the source of truth
    assert status["next_catalogue_check_at"] == cat["next_poll_at"]


def test_poll_interval_is_configurable(monkeypatch):
    monkeypatch.setenv("OCEANEYE_CATALOGUE_POLL_MINUTES", "7")
    assert copernicus._configured_interval() == 7
    monkeypatch.setenv("OCEANEYE_CATALOGUE_POLL_MINUTES", "1")
    assert copernicus._configured_interval() == copernicus.MIN_INTERVAL_MINUTES


# ---------------------------------------------------------------------------
# Freshness, stale providers and source health
# ---------------------------------------------------------------------------


def test_freshness_is_source_specific():
    hour, day = 3600, 86400
    assert live_data.classify_freshness("sar", 2 * day) == "CURRENT"
    assert live_data.classify_freshness("sar", 10 * day) == "STALE"
    assert live_data.classify_freshness("sar", 20 * day) == "VERY_STALE"
    assert live_data.classify_freshness("ais", 5 * 60) == "LIVE"
    assert live_data.classify_freshness("ais", 2 * hour) == "STALE"  # AIS goes stale within hours
    assert live_data.classify_freshness("ais", 7 * hour) == "VERY_STALE"
    assert live_data.classify_freshness("sar", None) == "UNAVAILABLE"
    # Ocean model: valid-time semantics -- a forecast still covering now is CURRENT.
    assert live_data.classify_model_validity(NOW + timedelta(days=9), NOW) == "CURRENT"
    assert live_data.classify_model_validity(NOW - timedelta(hours=6), NOW) == "STALE"
    assert live_data.classify_model_validity(NOW - timedelta(days=3), NOW) == "VERY_STALE"


def test_stale_catalogue_and_degraded_health(isolated):
    area = copernicus.create_watch_area("Old scenes", [80.55, 12.25], 20)
    with _client(lambda r: httpx.Response(200, json={"features": [_stac_feature("S1D_OLD", "2026-09-10T00:31:01.644669Z")]})) as fake:
        copernicus.check_watch_area(area, fake)
    status = live_data.status(now=NOW)
    cat = next(p for p in status["providers"] if p["provider"] == "sentinel1_catalogue")
    assert cat["status"] == "VERY_STALE" and cat["stale"] is True
    assert cat["data_age_seconds"] > 14 * 86400
    imagery = next(p for p in status["providers"] if p["provider"] == "sentinel1_imagery")
    assert imagery["status"] == "AUTH_REQUIRED"
    assert status["health"]["state"] == "DEGRADED"
    assert any("Calibrated SAR retrieval" in r for r in status["health"]["reasons"])


def test_offline_catalogue_is_reported(isolated):
    area = copernicus.create_watch_area("Unreachable", [80.55, 12.25], 20)

    def handler(request):
        raise httpx.ConnectError("no route", request=request)

    with _client(handler) as fake:
        copernicus.check_watch_area(area, fake)
    status = live_data.status()
    cat = next(p for p in status["providers"] if p["provider"] == "sentinel1_catalogue")
    assert cat["status"] == "OFFLINE" and "Network error" in cat["message"]
    assert status["health"]["state"] in ("OFFLINE", "DEGRADED")


def test_live_status_without_watch_areas_is_not_configured(isolated):
    status = live_data.status()
    cat = next(p for p in status["providers"] if p["provider"] == "sentinel1_catalogue")
    assert cat["status"] == "NOT_CONFIGURED"
    assert status["next_planned_pass"] is None
    plan = next(p for p in status["providers"] if p["provider"] == "acquisition_plan")
    assert plan["status"] == "NOT_CONFIGURED"


# ---------------------------------------------------------------------------
# Acquisition plans
# ---------------------------------------------------------------------------

PLAN_HTML = """
<a href="https://sentinels.copernicus.eu/documents/d/sentinel/s1c_mp_user_20260915t171204_20261005t194000">S1C</a>
<a href="/documents/d/sentinel/s1c_mp_user_20260922t180544_20261012t194000">S1C newer</a>
<a href="/documents/d/sentinel/s1d_mp_user_20260920t174933_20261010t194000">S1D</a>
<a href="/documents/d/sentinel/s1d_mp_user_20260801t174933_20260821t194000">S1D expired</a>
<a href="/documents/d/sentinel/s1d_mp_user_20260920t174933_20261010t194000">S1D duplicate</a>
"""


def _placemark(sat, begin, end, mode, coords, extra=""):
    return f"""
    <Placemark><name>{begin}</name>
      <TimeSpan><begin>{begin}</begin><end>{end}</end></TimeSpan>
      <ExtendedData>
        <Data name="SatelliteId"><value>{sat}</value></Data>
        <Data name="DatatakeId"><value>ABCD</value></Data>
        <Data name="Mode"><value>{mode}</value></Data>
        <Data name="Swath"><value>IW</value></Data>
        <Data name="Polarisation"><value>DV</value></Data>{extra}
      </ExtendedData>
      <Polygon><outerBoundaryIs><LinearRing><coordinates>{coords}</coordinates></LinearRing></outerBoundaryIs></Polygon>
    </Placemark>"""


BAY = "79.5,11.5,0 81.5,11.5,0 81.5,13.5,0 79.5,13.5,0 79.5,11.5,0"
ELSEWHERE = "10,50,0 12,50,0 12,52,0 10,52,0 10,50,0"
ANTIMERIDIAN = "179.5,10,0 -179.5,10,0 -179.5,12,0 179.5,12,0 179.5,10,0"


def _kml(*placemarks):
    return f"""<?xml version="1.0" encoding="iso-8859-1"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Planned</name>
<Folder><name>2026-09-27</name><Folder><name>S1D</name>{''.join(placemarks)}</Folder></Folder></Document></kml>"""


def test_plan_listing_selection_and_parsing():
    files = acquisition_plan.list_plan_files(PLAN_HTML)
    assert len(files) == 4  # duplicate link removed
    assert files[1]["url"] == "https://sentinels.copernicus.eu/documents/d/sentinel/s1c_mp_user_20260922t180544_20261012t194000"
    chosen = acquisition_plan.select_plan_files(files, NOW, per_satellite=1)
    assert {f["satellite"] for f in chosen} == {"Sentinel-1C", "Sentinel-1D"}
    assert all("20260801" not in f["url"] for f in chosen)  # expired plan never used
    assert next(f for f in chosen if f["code"] == "S1C")["start"].startswith("2026-09-22")

    segments = acquisition_plan.parse_kml(
        _kml(
            _placemark("S1D", "2026-09-28T00:31:00", "2026-09-28T00:31:25", "IW", BAY,
                       '<Data name="RelativeOrbit"><value>165</value></Data>'),
            _placemark("S1D", "2026-09-27T12:00:00", "2026-09-27T12:01:00", "WV", BAY),
        ),
        {"url": "u", "start": "s", "code": "S1D"},
    )
    assert len(segments) == 2
    first = segments[0]
    assert first["satellite"] == "Sentinel-1D" and first["mode"] == "IW"
    assert first["orbit"] == {"RelativeOrbit": "165"}
    assert first["begin"] == "2026-09-28T00:31:00+00:00"


def test_next_pass_intersects_aoi_and_is_unknown_otherwise():
    aoi = shape(circle([80.55, 12.25], 30))
    segments = acquisition_plan.parse_kml(
        _kml(
            _placemark("S1C", "2026-09-27T07:00:00", "2026-09-27T07:01:00", "IW", BAY),  # already past
            _placemark("S1C", "2026-09-27T12:00:00", "2026-09-27T12:01:00", "WV", BAY),  # wave mode ignored
            _placemark("S1D", "2026-09-27T20:00:00", "2026-09-27T20:01:00", "IW", ELSEWHERE),
            _placemark("S1D", "2026-09-28T00:31:00", "2026-09-28T00:31:25", "IW", BAY),
        )
    )
    hit = acquisition_plan.next_pass(segments, aoi, NOW)
    assert hit["satellite"] == "Sentinel-1D" and hit["begin"].startswith("2026-09-28T00:31")
    assert "polygon" not in hit
    far = shape(circle([-60, -40], 20))
    assert acquisition_plan.next_pass(segments, far, NOW) is None  # -> UNKNOWN, never guessed


def test_next_pass_handles_antimeridian_footprints():
    segments = acquisition_plan.parse_kml(
        _kml(_placemark("S1C", "2026-09-28T00:00:00", "2026-09-28T00:01:00", "IW", ANTIMERIDIAN))
    )
    assert acquisition_plan.next_pass(segments, shape(circle([-179.8, 11], 10)), NOW)
    assert acquisition_plan.next_pass(segments, shape(circle([179.8, 11], 10)), NOW)
    assert acquisition_plan.next_pass(segments, shape(circle([0, 11], 10)), NOW) is None


def test_later_issued_plan_supersedes_overlap():
    old = {"code": "S1D", "start": "2026-09-20T00:00:00+00:00", "end": "2026-10-10T00:00:00+00:00", "url": "old"}
    new = {"code": "S1D", "start": "2026-09-27T00:00:00+00:00", "end": "2026-10-15T00:00:00+00:00", "url": "new"}
    old_segs = acquisition_plan.parse_kml(_kml(
        _placemark("S1D", "2026-09-26T00:00:00", "2026-09-26T00:01:00", "IW", BAY),
        _placemark("S1D", "2026-09-29T00:00:00", "2026-09-29T00:01:00", "IW", BAY)), old)
    new_segs = acquisition_plan.parse_kml(_kml(
        _placemark("S1D", "2026-09-30T00:00:00", "2026-09-30T00:01:00", "IW", BAY)), new)
    merged = acquisition_plan.merge_plans([(old, old_segs), (new, new_segs)])
    assert [s["begin"][:10] for s in merged] == ["2026-09-26", "2026-09-30"]


def test_plan_refresh_offline_keeps_unknown_and_success_populates_next_pass(isolated):
    copernicus.create_watch_area("Plan AOI", [80.55, 12.25], 30)

    def offline(request):
        raise httpx.ConnectError("blocked", request=request)

    with _client(offline) as fake:
        state = acquisition_plan.refresh(client=fake, force=True, now=NOW)
    assert state["last_status"] == "OFFLINE"
    status = live_data.status(now=NOW)
    plan = next(p for p in status["providers"] if p["provider"] == "acquisition_plan")
    assert plan["status"] == "OFFLINE" and status["next_planned_pass"] is None

    kml = _kml(_placemark("S1D", "2026-09-28T00:31:00", "2026-09-28T00:31:25", "IW", BAY))

    def ok(request):
        if "acquisition-plans" in str(request.url):
            return httpx.Response(200, text=PLAN_HTML)
        return httpx.Response(200, content=kml.encode())

    with _client(ok) as fake:
        state = acquisition_plan.refresh(client=fake, force=True, now=NOW)
    assert state["last_status"] == "OK" and state["segment_count"] >= 1
    # throttled: a second non-forced refresh within 12 h makes no request
    calls = []
    with _client(lambda r: calls.append(r) or httpx.Response(500)) as fake:
        acquisition_plan.refresh(client=fake, now=NOW + timedelta(hours=1))
    assert calls == []
    status = live_data.status(now=NOW)
    assert status["next_planned_pass"]["satellite"] == "Sentinel-1D"
    assert status["next_planned_pass"]["label"] == "PLANNED — NOT GUARANTEED"
    plan = next(p for p in status["providers"] if p["provider"] == "acquisition_plan")
    assert plan["status"] == "CURRENT"


# ---------------------------------------------------------------------------
# Copernicus Marine provider + environmental time series
# ---------------------------------------------------------------------------


def test_marine_metadata_parsing_and_refresh(isolated):
    item = {
        "id": "cmems_mod_glo_phy_anfc_merged-uv_PT1H-i_202211",
        "properties": {
            "title": "hourly mean merged surface currents",
            "start_datetime": "2020-11-01T00:00:00Z",
            "end_datetime": "2026-10-06T23:00:00Z",
            "admp_updated": "2026-09-27",
            "admp_updated_data": "2026-09-26",
            "cube:variables": {"uo": {}, "vo": {}, "utotal": {}, "vtotal": {}},
        },
    }
    meta = marine.parse_dataset_metadata(item)
    assert meta["coverage_end"] == "2026-10-06T23:00:00+00:00"
    assert meta["variables"] == ["uo", "utotal", "vo", "vtotal"]

    def handler(request):
        if request.url.path.endswith("product.stac.json"):
            return httpx.Response(200, json={"links": [
                {"rel": "item", "href": "cmems_mod_glo_phy_anfc_merged-sl_PT1H-i_202411/dataset.stac.json"},
                {"rel": "item", "href": "cmems_mod_glo_phy_anfc_merged-uv_PT1H-i_202211/dataset.stac.json"},
            ]})
        assert "merged-uv" in request.url.path
        return httpx.Response(200, json=item)

    with _client(handler) as fake:
        state = marine.refresh_metadata(client=fake, force=True, now=NOW)
    assert state["last_status"] == "OK"
    ocean = live_data.ocean_provider(NOW)
    assert ocean["status"] in ("NOT_INSTALLED", "AUTH_REQUIRED")  # no data retrieved -> never CURRENT
    assert ocean["model_coverage_end"] == "2026-10-06T23:00:00+00:00"


def test_marine_series_and_environment_contract():
    times = ["2026-09-27T00:00:00Z", "2026-09-27T01:00:00Z", "2026-09-27T02:00:00Z"]
    series = marine.series_from_arrays(
        times, [0.2, float("nan"), 0.3], [-0.1, 0.0, -0.2], [0.02, 0.0, 0.04], [0.02, 0.0, 0.0],
        {"dataset_id": marine.DATASET_PREFIX, "variables": ["utotal", "vtotal"]},
    )
    assert [r["time"][:13] for r in series["records"]] == ["2026-09-27T00", "2026-09-27T02"]  # NaN hour dropped
    assert series["records"][1]["spatial_sigma_ms"] == round(math.sqrt((0.04 ** 2) / 2), 4)
    env = marine.build_environment(series, [79, 11, 82, 14], forecast_reference_time="2026-09-27T00:00:00Z")
    assert env["source_type"] == "REAL" and env["units"] == "m/s"
    assert all(r["wind_east_ms"] == 0 and r["wind_sigma_ms"] == 0 for r in env["records"])
    assert "currents-only" in env["forcing_terms"]["wind"]
    # Satisfies the existing drift forcing validator's record contract.
    env["records"] = env["records"] + [
        {**env["records"][-1], "time": (datetime(2026, 9, 27, 2, tzinfo=timezone.utc) + timedelta(hours=h)).isoformat()}
        for h in range(1, 60)
    ]
    validate_environment(env, "2026-09-27T01:30:00Z", ["2026-09-27T00:10:00Z", "2026-09-27T00:40:00Z"])


def test_marine_fetch_reports_missing_toolbox_or_credentials(isolated, monkeypatch):
    monkeypatch.setattr(marine, "toolbox_available", lambda: False)
    state = marine.refresh_subset([79, 11, 82, 14], NOW, NOW + timedelta(hours=48))
    assert state["last_status"] == "NOT_INSTALLED"
    monkeypatch.setattr(marine, "toolbox_available", lambda: True)
    state = marine.refresh_subset([79, 11, 82, 14], NOW, NOW + timedelta(hours=48))
    assert state["last_status"] == "AUTH_REQUIRED"
    assert live_data.ocean_provider(NOW)["status"] == "AUTH_REQUIRED"


def test_environment_time_series_labels_and_directions():
    speed, direction = marine.current_speed_direction(0.0, 1.0)
    assert (speed, direction) == (1.0, 0.0)  # flowing toward north
    assert marine.current_speed_direction(1.0, 0.0)[1] == 90.0
    env = {
        "source_type": "REAL", "source": "model",
        "forecast_reference_time": "2026-09-27T06:00:00Z",
        "records": [
            {"time": f"2026-09-27T{h:02d}:00:00Z", "current_east_ms": 0.1, "current_north_ms": 0.1}
            for h in range(0, 24)
        ],
    }
    series = marine.time_series(env, "2026-09-27T02:00:00Z")
    by = {p["label"]: p for p in series["points"]}
    assert by["AT OBSERVATION"]["kind"] == "ANALYSIS"
    assert by["+6H"]["kind"] == "FORECAST"  # never labelled as an observation
    assert by["+48H"]["available"] is False and "Outside" in by["+48H"]["reason"]
    assert by["LATEST AVAILABLE"]["valid_time"].startswith("2026-09-27T23")
    synthetic = marine.time_series({**env, "source_type": "SYNTHETIC"}, "2026-09-27T02:00:00Z")
    assert {p["kind"] for p in synthetic["points"] if p["available"]} == {"SYNTHETIC"}


# ---------------------------------------------------------------------------
# HTTP surface: live status, environment series, evidence graph, idempotent watch
# ---------------------------------------------------------------------------


def _run_demo(c):
    datasets.create_demo()
    case = c.post("/api/v1/cases/demo").json()
    run = c.post(f"/api/v1/cases/{case['id']}/run", json={}).json()
    for _ in range(150):
        state = c.get(f"/api/v1/runs/{run['run_id']}").json()
        if state["state"] != "RUNNING":
            break
        _time.sleep(0.2)
    assert state["state"] == "COMPLETE", state
    return case


def test_live_data_http_surface_and_case_sources(isolated, monkeypatch):
    real = httpx.Client
    monkeypatch.setattr(
        copernicus.httpx, "Client",
        lambda *a, **kw: real(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"features": []}))),
    )
    with TestClient(app) as c:
        case = _run_demo(c)
        status = c.get(f"/api/v1/live-data/status?case_id={case['id']}").json()
        providers = {p["provider"]: p for p in status["providers"]}
        assert status["data_mode"] == "DEMO"
        assert providers["ais"]["status"] == "DEMO"
        assert providers["ais"]["latest_data_timestamp"].startswith("2025-08-12")
        assert providers["case_forcing"]["status"] == "DEMO"
        for p in providers.values():
            assert set(p) >= {"provider", "status", "configured", "last_attempt", "last_success",
                              "latest_data_timestamp", "data_age_seconds", "next_poll_at",
                              "next_expected_update_at", "last_error", "stale", "source"}
        assert status["scheduler"]["granularity_seconds"] == copernicus.POLL_GRANULARITY_SECONDS

        series = c.get(f"/api/v1/cases/{case['id']}/environment/series").json()
        labels = [p["label"] for p in series["points"]]
        assert labels == ["AT OBSERVATION", "LATEST AVAILABLE", "+6H", "+12H", "+24H", "+48H"]
        assert all(p["kind"] == "SYNTHETIC" for p in series["points"] if p["available"])

        ocean = c.post("/api/v1/live-data/ocean/metadata/refresh")
        assert ocean.status_code == 200  # network failure is recorded, not raised

        graph = c.get(f"/api/v1/cases/{case['id']}/intelligence").json()["evidence"]
        ids = {n["id"] for n in graph["nodes"]}
        assert {"truthloop", "contradicting", "next_observation", "proof"} <= ids
        stages = {n["id"]: n["stage"] for n in graph["nodes"]}
        assert stages["truthloop"] == "challenge" and stages["sar"] == "observe"
        assert all(n["meta"]["epistemic_state"] for n in graph["nodes"])
        assert ("ranking", "truthloop") in {(e["source"], e["target"]) for e in graph["edges"]}

        first = c.post(f"/api/v1/cases/{case['id']}/next-observations/origin/watch").json()
        again = c.post(f"/api/v1/cases/{case['id']}/next-observations/origin/watch").json()
        assert first["already_watching"] is False and again["already_watching"] is True
        assert first["watch_area"]["id"] == again["watch_area"]["id"]
        status = c.get(f"/api/v1/live-data/status?case_id={case['id']}").json()
        assert len(status["watch_areas"]) == 1
        assert status["watch_areas"][0]["next_check_at"]


def test_auto_analyze_uses_marine_currents_when_access_is_real(isolated, monkeypatch):
    """With (mocked) Marine access, a 2026 acquisition gets acquisition-matched
    forcing instead of the linked case's stale 2025 forcing."""
    datasets.create_demo()
    cfg = json.loads((storage.DATA / "demo/case.json").read_text())
    acquired = "2026-09-22T00:31:01Z"
    cfg["release_window"] = ["2026-09-21T12:00:00Z", "2026-09-21T15:00:00Z"]
    monkeypatch.setattr(marine, "toolbox_available", lambda: True)
    monkeypatch.setattr(marine, "credentials_configured", lambda: True)
    calls = []

    def fake_fetch(bbox, start, end):
        calls.append((start, end))
        hours = int((end - start).total_seconds() // 3600) + 1
        times = [(start + timedelta(hours=h)).isoformat() for h in range(hours)]
        return marine.series_from_arrays(times, [0.2] * hours, [-0.1] * hours, [0.02] * hours, [0.02] * hours,
                                         {"dataset_id": marine.DATASET_PREFIX, "variables": ["utotal", "vtotal"]})

    monkeypatch.setattr(marine, "fetch_surface_currents", fake_fetch)
    area = copernicus.create_watch_area("Marine", [80.55, 12.25], 10)
    path = copernicus._dynamic_forcing(area, cfg, "S1D_MARINE", acquired)
    assert path and calls
    env = json.loads((storage.DATA / path).read_text())
    assert env["source_type"] == "REAL" and env["product_id"] == marine.PRODUCT_ID
    validate_environment(env, acquired, cfg["release_window"])
    assert copernicus.auto_analyze_dependencies({**cfg, "environment": path}, acquired) == [
        "AIS observations overlapping the release window"
    ]  # the demo AIS is 2025 -- still honestly missing


def test_next_observation_and_truthloop_accept_runs_from_older_builds(isolated):
    """Regression: runs stored by earlier builds lack receptor coordinates /
    first_overlap_time. Next-Best-Observation and TruthLoop must still work."""
    from backend import next_observation, truthloop

    with TestClient(app) as c:
        case = _run_demo(c)
        result = c.get(f"/api/v1/cases/{case['id']}/analysis").json()
    for r in result["impact"]["receptors"]:
        r.pop("coordinates", None)
        r.pop("first_overlap_time", None)
    cands = next_observation.candidates(result)
    receptor = next(c for c in cands if c["target_type"] == "NEAREST POTENTIAL EXPOSURE RECEPTOR")
    assert len(receptor["center"]) == 2
    assert truthloop.unchallenged(result)["discriminating_evidence"]
