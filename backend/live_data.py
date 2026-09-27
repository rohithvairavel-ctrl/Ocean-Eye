"""Live-data control plane: one lightweight, honest status view of every
external data source OCEAN-EYE depends on.

Every state is derived from real backend records (watch-area check history,
stored observations, cached plan/metadata fetches, the active case's inputs).
The endpoint never calls external services itself -- the background scheduler
does that -- so the frontend can poll it every few seconds cheaply.

States
    LIVE            continuously updating source, data within its live tolerance
    CURRENT         data within the source-specific freshness tolerance
    SYNCING         a real query is in flight right now
    WAITING_FOR_PASS  catalogue healthy; no acquisition yet (a planned pass is known)
    NO_NEW_DATA     catalogue healthy; nothing acquired over the AOI in the search window
    STALE / VERY_STALE  data older than the source's tolerance
    AUTH_REQUIRED   credentials missing or rejected
    OFFLINE         service unreachable
    RATE_LIMITED    service answered HTTP 429
    ERROR           unexpected failure (see last_error)
    DEMO            synthetic demonstration input
    UPLOADED        static archive supplied by the operator (no live feed)
    NOT_CONFIGURED  nothing set up yet (e.g. no area under watch)
    NOT_INSTALLED   optional client library missing
    UNAVAILABLE     no reliable information exists
"""

import json
from datetime import datetime, timedelta, timezone

from . import acquisition_plan, copernicus, marine, storage
from .config import DATA
from .drift import timestamp

# Source-specific freshness tolerances (seconds). SAR revisit over a given AOI
# is days, not minutes; AIS goes stale within hours.
FRESHNESS = {
    "sar": [("CURRENT", 7 * 86400), ("STALE", 14 * 86400)],
    "ais": [("LIVE", 15 * 60), ("CURRENT", 3600), ("STALE", 6 * 3600)],
    "plan": [("CURRENT", 24 * 3600), ("STALE", 72 * 3600)],
    "metadata": [("CURRENT", 24 * 3600), ("STALE", 72 * 3600)],
}


def classify_freshness(kind, age_seconds):
    if age_seconds is None:
        return "UNAVAILABLE"
    for state, limit in FRESHNESS[kind]:
        if age_seconds <= limit:
            return state
    return "VERY_STALE"


def classify_model_validity(coverage_end, now, hourly=True):
    """Ocean model freshness follows valid-time semantics: a forecast whose
    valid window still reaches the present is CURRENT, however old its run."""
    if coverage_end is None:
        return "UNAVAILABLE"
    end = timestamp(coverage_end) if isinstance(coverage_end, str) else coverage_end
    lag = (now - end).total_seconds()
    if lag <= (3600 if hourly else 86400):
        return "CURRENT"
    if lag <= 24 * 3600:
        return "STALE"
    return "VERY_STALE"


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


def _age(now, stamp):
    if not stamp:
        return None
    return max(0, int((now - timestamp(stamp)).total_seconds()))


def _provider(key, label, status, **fields):
    base = {
        "provider": key,
        "label": label,
        "status": status,
        "configured": None,
        "last_attempt": None,
        "last_success": None,
        "latest_data_timestamp": None,
        "data_age_seconds": None,
        "next_poll_at": None,
        "next_expected_update_at": None,
        "last_error": None,
        "stale": status in ("STALE", "VERY_STALE"),
        "source": None,
        "product": None,
        "message": None,
    }
    base.update(fields)
    base["stale"] = base["status"] in ("STALE", "VERY_STALE")
    return base


# ---------------------------------------------------------------------------
# Sentinel-1 catalogue (public) + imagery (OAuth)
# ---------------------------------------------------------------------------


def _latest_observations(limit=6):
    with storage.connect() as db:
        rows = db.execute(
            "SELECT o.*, w.name AS watch_name FROM observations o JOIN watch_areas w ON w.id=o.watch_id "
            "ORDER BY o.created DESC LIMIT ?",
            (limit,),
        ).fetchall()
    events = []
    for r in rows:
        prov = json.loads(r["provenance"])
        scene = prov.get("scene") or {}
        events.append(
            {
                "id": r["id"],
                "type": "NEW_EARTH_OBSERVATION",
                "watch_id": r["watch_id"],
                "watch_name": r["watch_name"],
                "stac_id": r["stac_id"],
                "platform": scene.get("platform"),
                "acquired": r["acquired"],
                "discovered": r["created"],
                "status": r["status"],
                "instrument_mode": scene.get("instrument_mode"),
                "orbit_state": scene.get("orbit_state"),
                "auto_analyze": prov.get("auto_analyze"),
                "source_url": scene.get("source_url"),
            }
        )
    return events


def sentinel_catalogue(areas, now, passes):
    active = [a for a in areas if a["status"] == "ACTIVE"]
    if not areas:
        return _provider(
            "sentinel1_catalogue", "Sentinel-1 catalogue", "NOT_CONFIGURED",
            configured=True, source=copernicus.STAC_ITEMS_URL.format(collection=copernicus.COLLECTION),
            product="Sentinel-1 GRD (public CDSE STAC)",
            message="No area is under watch. Add an AOI to Copernicus Watch to start catalogue monitoring.",
        ), []
    with storage.connect() as db:
        latest = db.execute(
            "SELECT MAX(acquired) AS acquired FROM observations WHERE watch_id IN (%s)"
            % ",".join("?" * len(areas)),
            [a["id"] for a in areas],
        ).fetchone()["acquired"]
    syncing = [
        a for a in active
        if a.get("sync_started") and (now - timestamp(a["sync_started"])).total_seconds() < copernicus.SYNC_STALE_SECONDS
    ]
    checks = [a for a in areas if a.get("last_checked")]
    last_attempt = max((a["last_checked"] for a in checks), default=None)
    last_success = max((a["last_success"] for a in areas if a.get("last_success")), default=None)
    recent = max(checks, key=lambda a: a["last_checked"]) if checks else None
    next_times = [t for t in (copernicus.next_check_at(a, now) for a in active) if t]
    next_poll = min(next_times) if next_times else None
    age = _age(now, latest)
    freshness = classify_freshness("sar", age) if latest else None
    upcoming = [p for p in passes.values() if p]
    next_expected = min((p["begin"] for p in upcoming), default=None)
    failing = {a.get("last_status") for a in checks} & {"OFFLINE", "RATE_LIMITED", "ERROR", "AUTH_REQUIRED"}

    if syncing:
        status, message = "SYNCING", f"Querying the public CDSE catalogue for {len(syncing)} area(s)."
    elif not active:
        status, message = "NOT_CONFIGURED", "All watch areas are paused."
    elif recent and recent.get("last_status") in ("OFFLINE", "RATE_LIMITED", "ERROR", "AUTH_REQUIRED") and not last_success:
        status, message = recent["last_status"], recent.get("last_message")
    elif not checks:
        status, message = "WAITING_FOR_PASS", "First catalogue check is scheduled."
    elif failing and recent and recent.get("last_status") in failing:
        status, message = recent["last_status"], recent.get("last_message")
    elif latest:
        status = freshness
        message = (
            "New Sentinel-1 acquisition recorded."
            if recent and recent.get("last_new_at") == recent.get("last_checked")
            else "No new acquisition since the last catalogue check."
        )
    else:
        status = "WAITING_FOR_PASS" if next_expected else "NO_NEW_DATA"
        message = f"No Sentinel-1 acquisition over the watched AOI in the last {copernicus.SEARCH_WINDOW_DAYS} days."
    return _provider(
        "sentinel1_catalogue", "Sentinel-1 catalogue", status,
        configured=True,
        last_attempt=last_attempt,
        last_success=last_success,
        latest_data_timestamp=latest,
        data_age_seconds=age,
        freshness=freshness,
        next_poll_at=_iso(next_poll),
        next_expected_update_at=next_expected,
        last_error=next((a.get("last_error") for a in checks if a.get("last_status") != "OK" and a.get("last_error")), None),
        source=copernicus.STAC_ITEMS_URL.format(collection=copernicus.COLLECTION),
        product="Sentinel-1 GRD (public CDSE STAC)",
        message=message,
        watch_areas=len(areas),
        active_watch_areas=len(active),
        poll_interval_minutes=min((copernicus.effective_interval_minutes(a) for a in active), default=None),
        last_check_outcome=(
            None if not recent or recent.get("last_status") != "OK"
            else ("NEW_EARTH_OBSERVATION" if recent.get("last_new_at") == recent.get("last_checked") else "NO_NEW_ACQUISITION")
        ),
    ), next_times


def sentinel_imagery(watching=False):
    configured = copernicus.credentials_configured()
    state = storage.get_provider_state("sentinel1_imagery") or {}
    if not configured:
        return _provider(
            "sentinel1_imagery", "Calibrated SAR retrieval", "AUTH_REQUIRED",
            configured=False, last_attempt=state.get("last_attempt"), last_success=state.get("last_success"),
            last_error=state.get("last_error"),
            source=copernicus.PROCESS_URL, product="SIGMA0_ELLIPSOID (Sentinel Hub Process API)",
            message=(
                "Catalogue monitoring is active. " if watching else "Catalogue discovery needs no credentials. "
            ) + "Calibrated imagery retrieval requires CDSE OAuth client credentials.",
        )
    last = state.get("last_status")
    status = {"OK": "CURRENT", None: "WAITING_FOR_PASS"}.get(last, last)
    return _provider(
        "sentinel1_imagery", "Calibrated SAR retrieval", status,
        configured=True, last_attempt=state.get("last_attempt"), last_success=state.get("last_success"),
        last_error=state.get("last_error"),
        source=copernicus.PROCESS_URL, product="SIGMA0_ELLIPSOID (Sentinel Hub Process API)",
        message="Credentials configured; imagery is retrieved when a new scene is discovered."
        if not last else None,
    )


def plan_provider(areas, now):
    passes, cache = acquisition_plan.next_passes(areas, now)
    state = storage.get_provider_state("acquisition_plan") or {}
    freshness = acquisition_plan.plan_freshness(cache, now)
    if not areas:
        status = "NOT_CONFIGURED"
        message = "Planned passes are computed for watched AOIs."
    elif cache is None:
        status = state.get("last_status") if state.get("last_status") not in (None, "OK") else "UNAVAILABLE"
        message = "No reliable planned acquisition found." if status == "UNAVAILABLE" else state.get("last_error")
    else:
        status = freshness
        message = acquisition_plan.LABEL
    last_attempt = state.get("last_attempt")
    next_refresh = (
        (timestamp(last_attempt) + timedelta(hours=acquisition_plan.REFRESH_HOURS)).isoformat()
        if last_attempt else None
    )
    return passes, _provider(
        "acquisition_plan", "Sentinel-1 acquisition plan", status,
        configured=True,
        last_attempt=last_attempt,
        last_success=state.get("last_success"),
        latest_data_timestamp=cache["fetched_at"] if cache else None,
        data_age_seconds=_age(now, cache["fetched_at"]) if cache else None,
        next_poll_at=next_refresh,
        last_error=state.get("last_error"),
        source=acquisition_plan.PLAN_PAGE_URL,
        product="ESA Sentinel-1 mission plan (KML)",
        message=message,
        plan_files=[f["url"].rsplit("/", 1)[-1] for f in (cache or {}).get("files", [])],
        coverage_end=(cache or {}).get("coverage_end"),
    )


# ---------------------------------------------------------------------------
# Ocean model + case inputs
# ---------------------------------------------------------------------------


def ocean_provider(now):
    meta_state = storage.get_provider_state("marine_metadata") or {}
    meta = meta_state.get("metadata") or {}
    subset = storage.get_provider_state("marine_subset") or {}
    series = subset.get("series") or {}
    records = series.get("records") or []
    installed = marine.toolbox_available()
    configured = installed and marine.credentials_configured()
    fields = dict(
        configured=configured,
        source=f"Copernicus Marine {marine.PRODUCT_ID}",
        product=meta.get("dataset_id") or marine.DATASET_PREFIX,
        last_attempt=subset.get("last_attempt") or meta_state.get("last_attempt"),
        last_success=subset.get("last_success") or meta_state.get("last_success"),
        model_coverage_end=meta.get("coverage_end"),
        model_updated=meta.get("product_updated"),
        catalogue_status=meta_state.get("last_status"),
    )
    if records:
        latest_valid = records[-1]["time"]
        status = classify_model_validity(latest_valid, now)
        return _provider(
            "ocean_model", "Ocean currents (model)", status,
            latest_data_timestamp=latest_valid,
            data_age_seconds=_age(now, subset.get("last_success")),
            last_error=subset.get("last_error"),
            message=f"AOI-averaged hourly currents, valid to {latest_valid}.",
            **fields,
        )
    if not installed:
        status = "NOT_INSTALLED"
        message = (
            "Copernicus Marine subsetting needs the official copernicusmarine toolbox and an account. "
            + ("Product catalogue is reachable; " if meta else "")
            + "no currents have been retrieved."
        )
    elif not marine.credentials_configured():
        status, message = "AUTH_REQUIRED", "Set COPERNICUSMARINE_SERVICE_USERNAME / _PASSWORD to retrieve currents."
    else:
        status = subset.get("last_status") or "WAITING_FOR_PASS"
        message = subset.get("last_error") or "Currents are retrieved for watched AOIs."
    return _provider(
        "ocean_model", "Ocean currents (model)", status,
        latest_data_timestamp=meta.get("coverage_end"),
        data_age_seconds=None,
        last_error=subset.get("last_error") or meta_state.get("last_error"),
        message=message,
        **fields,
    )


_ais_cache = {}


def _case_inputs(case_id):
    case = storage.get_case(case_id)
    cfg = case["config"]
    run_id = case.get("latest_run")
    key = (case_id, run_id)
    if key in _ais_cache:
        return case, cfg, _ais_cache[key]
    info = {"latest_message": None, "vessels": 0, "environment": None}
    if run_id:
        run = storage.get_run(run_id)
        result = run.get("result") or {}
        stamps = [p["time"] for v in result.get("vessels", []) for p in v.get("track", [])]
        info["latest_message"] = max(stamps) if stamps else None
        info["vessels"] = len(result.get("vessels", []))
        env = result.get("environment") or {}
        recs = env.get("records") or []
        info["environment"] = {
            "source": env.get("source"),
            "source_type": env.get("source_type"),
            "coverage": [recs[0]["time"], recs[-1]["time"]] if recs else None,
            "forecast_reference_time": env.get("forecast_reference_time"),
        }
    _ais_cache[key] = info
    return case, cfg, info


def ais_provider(case_id, now):
    if not case_id:
        return _provider("ais", "AIS", "UNAVAILABLE", configured=False,
                         message="Open a case to see its AIS source.")
    try:
        case, cfg, info = _case_inputs(case_id)
    except KeyError:
        return _provider("ais", "AIS", "UNAVAILABLE", configured=False, message="Case not found.")
    source_type = cfg.get("sources", {}).get("ais", cfg.get("source_type"))
    latest = info["latest_message"]
    if not cfg.get("ais"):
        status, message = "UNAVAILABLE", "AIS data is required before vessel attribution can run."
    elif source_type == "SYNTHETIC":
        status, message = "DEMO", "Synthetic AIS archive for the demonstration case; not a live feed."
    else:
        status, message = "UPLOADED", "Operator-supplied AIS archive; no live AIS feed is connected."
    return _provider(
        "ais", "AIS", status,
        configured=bool(cfg.get("ais")),
        latest_data_timestamp=latest,
        data_age_seconds=_age(now, latest),
        freshness=classify_freshness("ais", _age(now, latest)) if latest else None,
        source=cfg.get("ais"),
        product=f"{info['vessels']} tracked vessel(s)" if info["vessels"] else None,
        message=message,
    )


def case_forcing_provider(case_id, now):
    if not case_id:
        return None
    try:
        case, cfg, info = _case_inputs(case_id)
    except KeyError:
        return None
    env = info.get("environment") or {}
    coverage = env.get("coverage")
    source_type = env.get("source_type") or cfg.get("sources", {}).get("environment")
    if source_type == "SYNTHETIC":
        status = "DEMO"
    elif coverage:
        status = "UPLOADED"
    else:
        status = "UNAVAILABLE"
    return _provider(
        "case_forcing", "Case environmental forcing", status,
        configured=bool(cfg.get("environment")),
        latest_data_timestamp=coverage[1] if coverage else None,
        source=env.get("source"),
        product="Currents + wind forcing used by this run",
        coverage=coverage,
        forecast_reference_time=env.get("forecast_reference_time"),
        message="Forcing is an analysis input, not an observation.",
    )


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


def health(providers, data_mode):
    by = {p["provider"]: p for p in providers}
    catalogue = by["sentinel1_catalogue"]["status"]
    reasons = []
    if catalogue in ("OFFLINE", "ERROR", "RATE_LIMITED"):
        reasons.append(f"Sentinel-1 catalogue {catalogue}")
    for key in ("sentinel1_imagery", "ocean_model"):
        if by[key]["status"] in ("AUTH_REQUIRED", "NOT_INSTALLED"):
            reasons.append(f"{by[key]['label']} {by[key]['status'].replace('_', ' ').lower()}")
        elif by[key]["status"] in ("OFFLINE", "ERROR", "RATE_LIMITED"):
            reasons.append(f"{by[key]['label']} {by[key]['status']}")
    if by["acquisition_plan"]["status"] in ("OFFLINE", "ERROR", "VERY_STALE"):
        reasons.append(f"Acquisition plan {by['acquisition_plan']['status']}")
    if catalogue in ("OFFLINE",) and by["acquisition_plan"]["status"] in ("OFFLINE", "UNAVAILABLE"):
        state = "OFFLINE"
    elif reasons:
        state = "DEGRADED"
    elif data_mode == "DEMO":
        state = "DEMO"
    else:
        state = "OPERATIONAL"
    return {"state": state, "reasons": reasons}


def status(case_id=None, now=None):
    now = now or datetime.now(timezone.utc)
    areas = copernicus.list_watch_areas()
    passes, plan = plan_provider(areas, now)
    catalogue, _ = sentinel_catalogue(areas, now, passes)
    watching = any(a["status"] == "ACTIVE" for a in areas)
    providers = [catalogue, sentinel_imagery(watching), plan, ocean_provider(now), ais_provider(case_id, now)]
    forcing = case_forcing_provider(case_id, now)
    if forcing:
        providers.append(forcing)
    data_mode = None
    if case_id:
        try:
            data_mode = "DEMO" if storage.get_case(case_id)["config"].get("source_type") == "SYNTHETIC" else "REAL"
        except KeyError:
            data_mode = None
    area_views = []
    for a in areas:
        nxt = copernicus.next_check_at(a, now)
        area_views.append(
            {
                "id": a["id"],
                "name": a["name"],
                "status": a["status"],
                "center": [a["center_lon"], a["center_lat"]],
                "radius_km": a["radius_km"],
                "interval_minutes": a["interval_minutes"],
                "effective_interval_minutes": copernicus.effective_interval_minutes(a),
                "last_status": a.get("last_status"),
                "last_checked": a.get("last_checked"),
                "last_message": a.get("last_message"),
                "next_check_at": _iso(nxt),
                "auto_analyze": bool(a.get("auto_analyze")),
                "case_id": a.get("case_id"),
                "origin_ref": a.get("origin_ref"),
                "source": a.get("source"),
                "last_scene_count": a.get("last_scene_count"),
                "last_new_at": a.get("last_new_at"),
                "next_planned_pass": passes.get(a["id"]),
            }
        )
    upcoming = sorted([p for p in passes.values() if p], key=lambda p: p["begin"])
    return {
        "generated_at": now.isoformat(),
        "server_time": now.isoformat(),
        "case_id": case_id,
        "data_mode": data_mode,
        "health": health(providers, data_mode),
        "scheduler": copernicus.scheduler_state(),
        "providers": providers,
        "watch_areas": area_views,
        "next_catalogue_check_at": catalogue["next_poll_at"],
        "next_planned_pass": (
            {**upcoming[0], "label": acquisition_plan.LABEL} if upcoming else None
        ),
        "events": _latest_observations(),
    }
