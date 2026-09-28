"""Copernicus Data Space Ecosystem integration.

Sentinel-1 GRD discovery via the CDSE STAC catalogue, calibrated backscatter
retrieval via the Sentinel Hub Process API, and a small non-blocking watch-area
monitor that persists discovered scenes. Network access is isolated behind a
handful of functions that take an explicit httpx client, so tests can supply a
fake transport rather than reaching the real network.

Nothing here fabricates data. If credentials are missing, calls raise
CredentialsMissing and the caller records an AUTH_REQUIRED state. If the
network is unreachable, calls raise httpx.HTTPError and the caller records
OFFLINE. Downloaded rasters are calibrated to sigma0_db and saved as REAL
inputs ready for an operator to import through the existing case-import flow;
nothing is auto-classified as oil and no case is auto-created.
"""

import asyncio
import json
import os
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone

import httpx

from . import storage
from .config import DATA
from .drift import timestamp as parse_timestamp

MODEL_VERSION = "copernicus-monitor-1.0"

TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
STAC_SEARCH_URL = "https://stac.dataspace.copernicus.eu/v1/search"
STAC_ITEMS_URL = "https://stac.dataspace.copernicus.eu/v1/collections/{collection}/items"
PROCESS_URL = "https://sh.dataspace.copernicus.eu/process/v1"
COLLECTION = "sentinel-1-grd"

MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 1440


def _configured_interval():
    """Catalogue polling interval for new watch areas (minutes). Default 15,
    overridable with OCEANEYE_CATALOGUE_POLL_MINUTES; clamped to the valid range."""
    try:
        value = int(os.environ.get("OCEANEYE_CATALOGUE_POLL_MINUTES", "15"))
    except ValueError:
        value = 15
    return max(MIN_INTERVAL_MINUTES, min(MAX_INTERVAL_MINUTES, value))


DEFAULT_INTERVAL_MINUTES = _configured_interval()
MAX_BACKOFF_MINUTES = 120
POLL_GRANULARITY_SECONDS = 30
SEARCH_WINDOW_DAYS = 10
SYNC_STALE_SECONDS = 180  # a SYNCING marker older than this is treated as interrupted
IMAGERY_RETRY_LIMIT = 3  # previously AUTH_REQUIRED scenes retried per pass once credentials exist
CATALOGUE_RETRY_DELAYS_SECONDS = (0.25, 0.75)

_token_cache = {"value": None, "expires_at": 0.0}
_monitor_task = None
_monitor_stop = False
_scheduler = {"running": False, "last_tick_at": None, "next_tick_at": None}
_background_hooks = []  # callables run (in a thread) once per scheduler tick; see register_tick_hook


class CredentialsMissing(RuntimeError):
    """Raised when CDSE_CLIENT_ID / CDSE_CLIENT_SECRET are absent or rejected."""


class RateLimited(RuntimeError):
    """Raised when a Copernicus endpoint answers HTTP 429."""


class InvalidImagery(RuntimeError):
    """Raised when the Process API response is a readable but unusable raster."""


def credentials_configured():
    return bool(os.environ.get("CDSE_CLIENT_ID")) and bool(os.environ.get("CDSE_CLIENT_SECRET"))


def _credentials():
    client_id = os.environ.get("CDSE_CLIENT_ID")
    client_secret = os.environ.get("CDSE_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise CredentialsMissing(
            "CDSE_CLIENT_ID / CDSE_CLIENT_SECRET are not set. Configure Copernicus Data "
            "Space Ecosystem OAuth2 client credentials as environment variables to enable "
            "live Sentinel-1 monitoring."
        )
    return client_id, client_secret


def reset_token_cache():
    _token_cache["value"] = None
    _token_cache["expires_at"] = 0.0


def get_token(client: httpx.Client, force=False):
    now = time.time()
    if not force and _token_cache["value"] and _token_cache["expires_at"] > now + 30:
        return _token_cache["value"]
    client_id, client_secret = _credentials()
    try:
        response = client.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=20,
        )
    except httpx.HTTPError:
        raise
    if response.status_code in (400, 401):
        raise CredentialsMissing(
            "Copernicus Data Space rejected the configured client credentials "
            f"(HTTP {response.status_code})."
        )
    response.raise_for_status()
    payload = response.json()
    _token_cache["value"] = payload["access_token"]
    _token_cache["expires_at"] = now + float(payload.get("expires_in", 600))
    return _token_cache["value"]


def stac_search(client: httpx.Client, token, bbox, start, end, limit=10):
    response = client.post(
        STAC_SEARCH_URL,
        json={"collections": [COLLECTION], "bbox": bbox, "datetime": f"{start}/{end}", "limit": limit},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if response.status_code == 401:
        raise CredentialsMissing("Copernicus STAC search returned 401; token expired or invalid.")
    if response.status_code == 429:
        raise RateLimited("Copernicus STAC search is rate limiting requests (HTTP 429).")
    response.raise_for_status()
    return response.json().get("features", [])


def stac_search_public(client: httpx.Client, bbox, start, end, limit=10):
    """Unauthenticated STAC catalogue browse.

    The Copernicus Data Space Ecosystem's STAC catalogue is publicly queryable
    for metadata discovery -- only the Sentinel Hub Process API (actual pixel
    retrieval, see process_backscatter) requires an OAuth2 client. This lets
    watch areas discover real Sentinel-1 scenes with no credentials at all.

    Returns None (not a real "no results") if this deployment/collection
    responds 401/403, signalling the caller should fall back to the
    authenticated stac_search() when credentials are available. Any other
    network/server failure raises httpx.HTTPError like the rest of this module.
    """
    request = {
        "params": {"bbox": ",".join(str(v) for v in bbox), "datetime": f"{start}/{end}", "limit": limit},
        "timeout": 30,
    }
    for attempt in range(len(CATALOGUE_RETRY_DELAYS_SECONDS) + 1):
        try:
            response = client.get(STAC_ITEMS_URL.format(collection=COLLECTION), **request)
            if response.status_code not in (502, 503, 504) or attempt == len(CATALOGUE_RETRY_DELAYS_SECONDS):
                break
        except httpx.TransportError:
            if attempt == len(CATALOGUE_RETRY_DELAYS_SECONDS):
                raise
        time.sleep(CATALOGUE_RETRY_DELAYS_SECONDS[attempt])
    if response.status_code in (401, 403):
        return None
    if response.status_code == 429:
        raise RateLimited("The public Copernicus STAC catalogue is rate limiting requests (HTTP 429).")
    response.raise_for_status()
    return response.json().get("features", [])


def _first(props, *keys):
    for key in keys:
        value = props.get(key)
        if value not in (None, "", []):
            return value
    return None


def scene_metadata(feature):
    """Normalize the metadata a STAC item actually carries. Missing fields stay
    None -- nothing here is inferred or invented."""
    props = feature.get("properties") or {}
    platform = _first(props, "platform", "platformSerialIdentifier")
    self_link = next(
        (link.get("href") for link in feature.get("links", []) if link.get("rel") == "self"),
        None,
    )
    return {
        "stac_id": feature.get("id"),
        "platform": platform.upper().replace("SENTINEL-", "Sentinel-") if isinstance(platform, str) else None,
        "constellation": props.get("constellation"),
        "acquired": _first(props, "datetime", "start_datetime"),
        "start": props.get("start_datetime"),
        "end": props.get("end_datetime"),
        "instrument_mode": _first(props, "sar:instrument_mode", "sensorMode"),
        "product_type": _first(props, "sar:product_type", "product:type", "productType"),
        "polarizations": _first(props, "sar:polarizations", "polarisation"),
        "orbit_state": _first(props, "sat:orbit_state", "orbitDirection"),
        "relative_orbit": _first(props, "sat:relative_orbit", "relativeOrbitNumber"),
        "absolute_orbit": _first(props, "sat:absolute_orbit", "orbitNumber"),
        "processing_level": _first(props, "processing:level", "processingLevel"),
        "timeliness": _first(props, "product:timeliness_category", "timeliness"),
        "published": _first(props, "published", "created"),
        "footprint": feature.get("geometry"),
        "source_url": self_link or STAC_ITEMS_URL.format(collection=COLLECTION) + f"/{feature.get('id')}",
    }


def sigma0_db(linear, epsilon=1e-10):
    import numpy as np

    return 10 * np.log10(np.maximum(linear, epsilon))


PROCESS_EVALSCRIPT = """//VERSION=3
function setup() {
  return {
    input: [{ bands: ["VV"], units: "LINEAR_POWER" }],
    output: { bands: 1, sampleType: "FLOAT32" }
  };
}
function evaluatePixel(sample) {
  return [sample.VV];
}
"""


def process_backscatter(client: httpx.Client, token, bbox, time_range, width=512, height=512):
    """Request calibrated SIGMA0_ELLIPSOID VV backscatter as a linear-power GeoTIFF.

    Returns raw response bytes (linear power). Callers convert to sigma0_db with
    sigma0_db() before persisting, so unit handling is explicit end-to-end.
    """
    body = {
        "input": {
            "bounds": {"bbox": bbox},
            "data": [
                {
                    "type": "sentinel-1-grd",
                    "dataFilter": {
                        "timeRange": {"from": time_range[0], "to": time_range[1]},
                        "acquisitionMode": "IW",
                    },
                    "processing": {"backCoeff": "SIGMA0_ELLIPSOID", "orthorectify": True},
                }
            ],
        },
        "output": {
            "width": width,
            "height": height,
            "responses": [{"identifier": "default", "format": {"type": "image/tiff"}}],
        },
        "evalscript": PROCESS_EVALSCRIPT,
    }
    response = client.post(
        PROCESS_URL,
        json=body,
        headers={"Authorization": f"Bearer {token}"},
        timeout=90,
    )
    if response.status_code == 401:
        raise CredentialsMissing("Sentinel Hub Process API returned 401; token expired or invalid.")
    if response.status_code == 429:
        raise RateLimited("Sentinel Hub Process API is rate limiting requests (HTTP 429).")
    response.raise_for_status()
    return response.content


def scene_time_range(feature, fallback_end):
    """Time range that selects exactly this STAC scene in the Process API
    (its own start/end), rather than whatever was most recent in the window."""
    props = feature.get("properties") or {}
    start, end = props.get("start_datetime"), props.get("end_datetime")
    if start and end:
        # The Process API treats temporal bounds as selection filters. Padding
        # the catalogue's exact sensing interval avoids an edge-exclusive
        # query returning a valid GeoTIFF containing only NoData.
        return (
            (parse_timestamp(start) - timedelta(minutes=2)).isoformat().replace("+00:00", "Z"),
            (parse_timestamp(end) + timedelta(minutes=2)).isoformat().replace("+00:00", "Z"),
        )
    acquired = props.get("datetime")
    if acquired:
        t = parse_timestamp(acquired)
        return (
            (t - timedelta(seconds=30)).isoformat().replace("+00:00", "Z"),
            (t + timedelta(seconds=30)).isoformat().replace("+00:00", "Z"),
        )
    return fallback_end, fallback_end


def _imagery_state(**update):
    state = storage.get_provider_state("sentinel1_imagery") or {}
    state.pop("updated", None)
    state.update(update)
    storage.set_provider_state("sentinel1_imagery", state)
    return state


def bbox_for(center, radius_km):
    from shapely.geometry import shape

    from .geo import circle

    return list(shape(circle(center, radius_km)).bounds)


# ---------------------------------------------------------------------------
# Watch-area persistence
# ---------------------------------------------------------------------------


def find_watch_by_origin(origin_ref):
    """An ACTIVE or PAUSED watch area already created from the same source
    (e.g. the same Next-Best-Observation candidate of the same run)."""
    if not origin_ref:
        return None
    with storage.connect() as db:
        row = db.execute(
            "SELECT id FROM watch_areas WHERE origin_ref=? ORDER BY created DESC LIMIT 1",
            (origin_ref,),
        ).fetchone()
    return get_watch_area(row["id"]) if row else None


def create_watch_area(name, center, radius_km, interval_minutes=None,
                       case_id=None, run_id=None, note="", source="operator", auto_analyze=False,
                       origin_ref=None):
    if interval_minutes is None:
        interval_minutes = DEFAULT_INTERVAL_MINUTES
    if not (-180 <= center[0] <= 180 and -90 <= center[1] <= 90):
        raise ValueError("center must be [longitude, latitude] in WGS84")
    if not (0 < radius_km <= 500):
        raise ValueError("radius_km must be greater than 0 and at most 500")
    if not (MIN_INTERVAL_MINUTES <= interval_minutes <= MAX_INTERVAL_MINUTES):
        raise ValueError(
            f"interval_minutes must be between {MIN_INTERVAL_MINUTES} and {MAX_INTERVAL_MINUTES}"
        )
    watch_id = str(uuid.uuid4())
    now = storage.now()
    with storage.connect() as db:
        db.execute(
            """INSERT INTO watch_areas(
                id,name,created,center_lon,center_lat,radius_km,interval_minutes,status,source,
                case_id,run_id,note,last_checked,last_status,last_message,consecutive_failures,
                auto_analyze,origin_ref
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)""",
            (watch_id, name, now, center[0], center[1], radius_km, interval_minutes, "ACTIVE",
             source, case_id, run_id, note, None, "PENDING", None, int(bool(auto_analyze)),
             origin_ref),
        )
    storage.audit(
        case_id or "global",
        "watch_area_created",
        {
            "watch_id": watch_id, "name": name, "center": list(center), "radius_km": radius_km,
            "source": source, "auto_analyze": bool(auto_analyze),
        },
    )
    return get_watch_area(watch_id)


def get_watch_area(watch_id):
    with storage.connect() as db:
        row = db.execute("SELECT * FROM watch_areas WHERE id=?", (watch_id,)).fetchone()
    if not row:
        raise KeyError("Watch area not found")
    return dict(row)


def list_watch_areas():
    with storage.connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM watch_areas ORDER BY created DESC")]


def update_watch_area(watch_id, status=None, interval_minutes=None, auto_analyze=None):
    get_watch_area(watch_id)
    fields, values = [], []
    if status is not None:
        if status not in ("ACTIVE", "PAUSED"):
            raise ValueError("status must be ACTIVE or PAUSED")
        fields.append("status=?")
        values.append(status)
    if interval_minutes is not None:
        if not (MIN_INTERVAL_MINUTES <= interval_minutes <= MAX_INTERVAL_MINUTES):
            raise ValueError(
                f"interval_minutes must be between {MIN_INTERVAL_MINUTES} and {MAX_INTERVAL_MINUTES}"
            )
        fields.append("interval_minutes=?")
        values.append(interval_minutes)
    if auto_analyze is not None:
        fields.append("auto_analyze=?")
        values.append(int(bool(auto_analyze)))
    if fields:
        values.append(watch_id)
        with storage.connect() as db:
            db.execute(f"UPDATE watch_areas SET {','.join(fields)} WHERE id=?", values)
        storage.audit(
            "global", "watch_area_updated",
            {
                "watch_id": watch_id, "status": status, "interval_minutes": interval_minutes,
                "auto_analyze": auto_analyze,
            },
        )
    return get_watch_area(watch_id)


def delete_watch_area(watch_id):
    get_watch_area(watch_id)
    with storage.connect() as db:
        db.execute("DELETE FROM watch_areas WHERE id=?", (watch_id,))
        db.execute("DELETE FROM observations WHERE watch_id=?", (watch_id,))
    storage.audit("global", "watch_area_deleted", {"watch_id": watch_id})


def list_observations(watch_id):
    with storage.connect() as db:
        rows = db.execute(
            "SELECT * FROM observations WHERE watch_id=? ORDER BY created DESC", (watch_id,)
        ).fetchall()
    return [{**{k: r[k] for k in r.keys() if k != "provenance"}, "provenance": json.loads(r["provenance"])} for r in rows]


# ---------------------------------------------------------------------------
# Discovery + retrieval
# ---------------------------------------------------------------------------


def _mark_syncing(watch_id):
    """Record that a real catalogue query is in flight. Does not touch
    last_checked or the failure counter -- only a completed pass does."""
    with storage.connect() as db:
        db.execute(
            "UPDATE watch_areas SET sync_started=?, last_status='SYNCING' WHERE id=?",
            (storage.now(), watch_id),
        )


def _record_status(watch_id, status, message, scene_count=None, new_count=0):
    now = storage.now()
    with storage.connect() as db:
        row = db.execute("SELECT consecutive_failures FROM watch_areas WHERE id=?", (watch_id,)).fetchone()
        prior_failures = row["consecutive_failures"] if row else 0
        ok = status == "OK"
        failures = 0 if ok else prior_failures + 1
        db.execute(
            """UPDATE watch_areas SET last_checked=?, last_status=?, last_message=?,
                consecutive_failures=?, sync_started=NULL,
                last_success=CASE WHEN ? THEN ? ELSE last_success END,
                last_error=CASE WHEN ? THEN NULL ELSE ? END,
                last_scene_count=COALESCE(?, last_scene_count),
                last_new_at=CASE WHEN ? > 0 THEN ? ELSE last_new_at END
            WHERE id=?""",
            (now, status, message, failures, ok, now, ok, message, scene_count,
             new_count, now, watch_id),
        )
    if ok and prior_failures:
        storage.audit(
            "global",
            "catalogue_recovered",
            {"watch_id": watch_id, "failed_attempts": prior_failures, "recovered_at": now},
        )
    return failures


def _save_calibrated_geotiff(stac_id, raw_linear_tiff_bytes, feature):
    import numpy as np
    import rasterio

    safe_id = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in stac_id)
    folder = DATA / "satellite"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"copernicus_{safe_id}.tif"
    acquired = feature.get("properties", {}).get("datetime", "")
    with rasterio.io.MemoryFile(raw_linear_tiff_bytes) as memfile:
        with memfile.open() as src:
            linear = src.read(1).astype("float64")
            valid = np.isfinite(linear) & (linear > 0)
            minimum_valid = min(100, max(1, linear.size // 100))
            if valid.sum() < minimum_valid:
                raise InvalidImagery(
                    "Sentinel Hub returned a readable GeoTIFF with insufficient valid backscatter pixels."
                )
            db_values = sigma0_db(linear).astype("float32")
            profile = src.profile.copy()
            profile.update(dtype="float32", count=1)
            with rasterio.open(path, "w", **profile) as dst:
                dst.write(db_values, 1)
                dst.update_tags(
                    source_type="REAL",
                    radiometry="sigma0_db",
                    sensor="Sentinel-1 SAR (Copernicus Data Space, SIGMA0_ELLIPSOID)",
                    polarizations="VV",
                    acquisition_time=acquired,
                    stac_id=stac_id,
                    collection=COLLECTION,
                    processing="Linear power to sigma0_db: 10*log10(max(linear, 1e-10))",
                )
    return path.name, storage.digest(path)


def _dynamic_forcing(area, cfg, stac_id, acquired):
    """When Copernicus Marine access is genuinely available, retrieve currents
    for exactly this acquisition's hindcast/forecast window over the watch AOI
    and store them in the existing forcing format. Returns the relative path of
    the forcing file, or None (the linked case's forcing is then reused and the
    dependency check decides whether that is valid). Never simulated."""
    from . import marine

    if not (marine.toolbox_available() and marine.credentials_configured()):
        return None
    window = cfg.get("release_window")
    if not window:
        return None
    try:
        obs = parse_timestamp(acquired)
        start = parse_timestamp(window[0]) - timedelta(hours=1)
        if not timedelta(0) < obs - start <= timedelta(hours=98):
            return None
        bbox = bbox_for([area["center_lon"], area["center_lat"]], max(area["radius_km"], 25))
        series = marine.fetch_surface_currents(bbox, start, obs + timedelta(hours=49))
        env = marine.build_environment(series, [round(v, 4) for v in bbox])
        safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in stac_id)
        path = DATA / "environment" / f"marine_{safe}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(env, indent=2), encoding="utf-8")
        return str(path.relative_to(DATA))
    except Exception:  # recorded by the dependency check that follows
        traceback.print_exc()
        return None


def auto_analyze_dependencies(cfg, acquired):
    """What a linked case is missing before a newly acquired scene can be run
    through the existing pipeline. Returns human-readable missing inputs; an
    empty list means every dependency is genuinely satisfied. Nothing is
    shifted or synthesised to make a check pass."""
    import json as _json

    from .drift import validate_environment

    missing = []
    if not cfg.get("ais"):
        missing.append("AIS data is required before vessel attribution can run")
    if not cfg.get("environment"):
        missing.append("Environmental forcing (currents/wind) is required for hindcast and forecast")
    window = cfg.get("release_window")
    obs = parse_timestamp(acquired)
    if not window:
        missing.append("An analyst-supplied release-window assumption")
        return missing
    start, end = parse_timestamp(window[0]), parse_timestamp(window[1])
    age_min = (obs - end).total_seconds() / 3600
    age_max = (obs - start).total_seconds() / 3600
    if not (0 < age_min <= age_max <= 96):
        missing.append(
            "A release-window assumption within 96 h before this acquisition "
            f"(linked case window {window[0]} – {window[1]} does not precede it)"
        )
        return missing
    if cfg.get("environment"):
        try:
            env = _json.loads((DATA / cfg["environment"]).read_text(encoding="utf-8-sig"))
            validate_environment(env, acquired, window)
        except (OSError, ValueError) as exc:
            missing.append(f"Environmental forcing covering the release window through +48 h ({exc})")
    if cfg.get("ais"):
        try:
            from . import ais as _ais

            tracks, _quality = _ais.load_ais(DATA / cfg["ais"])
            overlaps = any(
                start <= parse_timestamp(p["time"]) <= obs
                for points in tracks.values()
                for p in points
            )
            if not overlaps:
                missing.append("AIS observations overlapping the release window")
        except (OSError, ValueError, KeyError) as exc:
            missing.append(f"Readable AIS input ({exc})")
    return missing


def _attempt_auto_analyze(area, stac_id, artifact_name, feature):
    """Try to close the loop: new calibrated scene -> real case -> engine run.

    This never fabricates AIS or environmental forcing. It only proceeds when the
    watch area is linked to an originating case (case_id) that already carries
    real AIS and environment inputs, and reuses those inputs verbatim alongside
    the newly retrieved REAL satellite scene -- the same honesty computation the
    manual import endpoint uses (a case is only labeled REAL if every input is).
    When there is nothing real to reuse, this returns WAITING_FOR_DATA and
    creates nothing.
    """
    case_id = area.get("case_id")
    if not case_id:
        return {
            "status": "WAITING_FOR_DATA",
            "missing": ["A linked case supplying AIS and environmental inputs"],
            "reason": (
                "No case is linked to this watch area, so there is no AIS or environment "
                "input to run the pipeline against. Link a watch area to a case (e.g. via "
                "Next-Best-Observation's \"Add to Copernicus Watch\") to enable auto-analyze."
            ),
        }
    try:
        source_case = storage.get_case(case_id)
    except KeyError:
        return {
            "status": "WAITING_FOR_DATA",
            "missing": ["The linked case"],
            "reason": f"Linked case {case_id} no longer exists.",
        }
    cfg = dict(source_case["config"])
    acquired_value = feature.get("properties", {}).get("datetime") or storage.now()
    dynamic_forcing = _dynamic_forcing(area, cfg, stac_id, acquired_value)
    if dynamic_forcing:
        cfg["environment"] = dynamic_forcing
        cfg.setdefault("sources", {})
        cfg["sources"] = {**cfg["sources"], "environment": "REAL"}
    missing = auto_analyze_dependencies(cfg, acquired_value)
    if missing:
        return {
            "status": "WAITING_FOR_DATA",
            "missing": missing,
            "reason": "Auto-analyze did not start: " + "; ".join(missing) + ".",
        }
    import uuid as uuid_module

    from . import engine

    acquired = feature.get("properties", {}).get("datetime") or storage.now()
    sources = {
        "satellite": "REAL",
        "ais": cfg.get("sources", {}).get("ais", cfg.get("source_type", "SYNTHETIC")),
        "environment": cfg.get("sources", {}).get("environment", cfg.get("source_type", "SYNTHETIC")),
    }
    new_config = {
        "name": f"Copernicus auto-analysis · {stac_id}",
        "observation_time": acquired,
        "release_window": cfg.get("release_window", [acquired, acquired]),
        "source_type": "SYNTHETIC" if "SYNTHETIC" in sources.values() else "REAL",
        "radiometry": "sigma0_db",
        "sensor": "Sentinel-1 SAR (Copernicus Data Space, SIGMA0_ELLIPSOID)",
        "windage": cfg.get("windage", 0.03),
        "seed": cfg.get("seed", 428),
        "satellite": str((DATA / "satellite" / artifact_name).relative_to(DATA)),
        "ais": cfg["ais"],
        "environment": cfg["environment"],
        "sources": sources,
        "release_window_basis": (
            "Reused from linked case's most recent assumption; not re-derived from this "
            "acquisition alone."
        ),
        "auto_analysis": {
            "watch_id": area["id"],
            "source_case_id": case_id,
            "stac_id": stac_id,
            "triggered": storage.now(),
        },
    }
    new_case_id = str(uuid_module.uuid4())
    now = storage.now()
    with storage.connect() as db:
        db.execute(
            "INSERT INTO cases(id,name,created,config) VALUES(?,?,?,?)",
            (new_case_id, new_config["name"], now, storage.canonical(new_config)),
        )
    storage.audit(
        new_case_id, "case_created", {**new_config, "trigger": "copernicus_auto_analyze"}
    )
    storage.audit(
        area.get("case_id") or "global", "auto_analyze_triggered",
        {"watch_id": area["id"], "new_case_id": new_case_id, "stac_id": stac_id},
    )
    run_id = str(uuid_module.uuid4())
    with storage.connect() as db:
        db.execute(
            "INSERT INTO runs(id,case_id,created,state,stage) VALUES(?,?,?,?,?)",
            (run_id, new_case_id, storage.now(), "RUNNING", "Queued"),
        )
    storage.audit(
        new_case_id, "analysis_requested", {"run_id": run_id, "trigger": "copernicus_auto_analyze"}
    )
    engine.run(new_case_id, run_id)  # synchronous: caller already runs off the event loop
    run = storage.get_run(run_id)
    if run["state"] == "COMPLETE":
        storage.audit(
            new_case_id, "auto_analyze_alert",
            {"run_id": run_id, "watch_id": area["id"], "title": "New Copernicus-triggered investigation ready"},
        )
        return {"status": "ANALYZED", "case_id": new_case_id, "run_id": run_id}
    return {
        "status": "ANALYSIS_FAILED", "case_id": new_case_id, "run_id": run_id,
        "error": run.get("error"),
    }


def _fetch_imagery(client, area, feature, bbox, window_end, token, provenance):
    """Calibrated SIGMA0 retrieval + optional auto-analyze for one discovered
    scene. Returns (status, artifact_name, sha256, token). Never simulates:
    without real OAuth2 credentials the status is AUTH_REQUIRED."""
    stac_id = feature.get("id")
    artifact_name, sha256 = None, None
    _imagery_state(last_attempt=storage.now())
    try:
        imagery_token = token or get_token(client)
        token = imagery_token
        raw = process_backscatter(client, imagery_token, bbox, scene_time_range(feature, window_end))
        artifact_name, sha256 = _save_calibrated_geotiff(stac_id, raw, feature)
        _imagery_state(last_success=storage.now(), last_error=None, last_status="OK")
        status = "READY_TO_IMPORT"
        if area.get("auto_analyze"):
            outcome = _attempt_auto_analyze(area, stac_id, artifact_name, feature)
            provenance["auto_analyze"] = outcome
            status = outcome["status"]
    except CredentialsMissing as exc:
        status = "AUTH_REQUIRED"
        provenance["fetch_error"] = str(exc)
        _imagery_state(last_error=str(exc), last_status="AUTH_REQUIRED")
    except RateLimited as exc:
        status = "FETCH_FAILED"
        provenance["fetch_error"] = str(exc)
        _imagery_state(last_error=str(exc), last_status="RATE_LIMITED")
    except InvalidImagery as exc:
        status = "FETCH_FAILED"
        provenance["fetch_error"] = str(exc)
        _imagery_state(last_error=str(exc), last_status="ERROR")
    except httpx.HTTPError as exc:
        status = "FETCH_FAILED"
        provenance["fetch_error"] = str(exc)
        _imagery_state(last_error=str(exc), last_status="OFFLINE")
    except Exception as exc:  # pragma: no cover - defensive
        traceback.print_exc()
        status = "FETCH_FAILED"
        provenance["fetch_error"] = str(exc)
        _imagery_state(last_error=str(exc), last_status="ERROR")
    if area.get("auto_analyze") and status == "AUTH_REQUIRED":
        provenance["auto_analyze"] = {
            "status": "BLOCKED",
            "missing": ["Calibrated Sentinel-1 imagery (requires CDSE OAuth client credentials)"],
            "reason": "Auto-analyze cannot start without calibrated SAR imagery; catalogue discovery alone is not analysable.",
        }
    return status, artifact_name, sha256, token


def _retry_pending_imagery(client, area, bbox, window_end, token):
    """Scenes discovered while credentials were missing are retried (a few per
    pass) once credentials exist, instead of being deduplicated forever."""
    if not credentials_configured():
        return token, 0
    with storage.connect() as db:
        rows = db.execute(
            "SELECT * FROM observations WHERE watch_id=? AND status IN ('AUTH_REQUIRED','FETCH_FAILED') "
            "ORDER BY acquired DESC LIMIT ?",
            (area["id"], IMAGERY_RETRY_LIMIT),
        ).fetchall()
    retried = 0
    for row in rows:
        provenance = json.loads(row["provenance"])
        feature = {
            "id": row["stac_id"],
            "properties": provenance.get("properties", {}),
            "geometry": (provenance.get("scene") or {}).get("footprint"),
        }
        provenance.pop("fetch_error", None)
        status, artifact_name, sha256, token = _fetch_imagery(
            client, area, feature, bbox, window_end, token, provenance
        )
        provenance.setdefault("imagery_retries", []).append({"time": storage.now(), "status": status})
        with storage.connect() as db:
            db.execute(
                "UPDATE observations SET status=?, artifact=?, sha256=?, provenance=? WHERE id=?",
                (status, artifact_name, sha256, storage.canonical(provenance), row["id"]),
            )
        retried += 1
    return token, retried


def check_watch_area(area, client: "httpx.Client | None" = None, fetch_imagery=True):
    """Run one discovery pass for a single watch area. Never raises; returns a status dict."""
    owns_client = client is None
    client = client or httpx.Client()
    watch_id = area["id"]
    _mark_syncing(watch_id)
    try:
        bbox = bbox_for([area["center_lon"], area["center_lat"]], area["radius_km"])
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=SEARCH_WINDOW_DAYS)
        window = (
            start.isoformat().replace("+00:00", "Z"),
            end.isoformat().replace("+00:00", "Z"),
        )
        token = None
        catalogue_source = "PUBLIC_CATALOGUE"
        try:
            features = stac_search_public(client, bbox, window[0], window[1])
            if features is None:
                # This deployment/collection needs auth even for search -- fall
                # back to the authenticated path if credentials are configured.
                token = get_token(client)
                features = stac_search(client, token, bbox, window[0], window[1])
                catalogue_source = "AUTHENTICATED"
        except CredentialsMissing as exc:
            _record_status(watch_id, "AUTH_REQUIRED", str(exc))
            return {"watch_id": watch_id, "status": "AUTH_REQUIRED", "message": str(exc)}
        except RateLimited as exc:
            _record_status(watch_id, "RATE_LIMITED", str(exc))
            return {"watch_id": watch_id, "status": "RATE_LIMITED", "message": str(exc)}
        except httpx.HTTPError as exc:
            message = f"Network error reaching Copernicus Data Space: {exc}"
            _record_status(watch_id, "OFFLINE", message)
            return {"watch_id": watch_id, "status": "OFFLINE", "message": message}
        except Exception as exc:  # pragma: no cover - defensive
            traceback.print_exc()
            _record_status(watch_id, "ERROR", str(exc))
            return {"watch_id": watch_id, "status": "ERROR", "message": str(exc)}

        with storage.connect() as db:
            known = {
                r["stac_id"]
                for r in db.execute("SELECT stac_id FROM observations WHERE watch_id=?", (watch_id,))
            }
        new_records = []
        for feature in features:
            stac_id = feature.get("id")
            if not stac_id or stac_id in known:
                continue
            known.add(stac_id)
            scene = scene_metadata(feature)
            provenance = {
                "stac_id": stac_id,
                "collection": COLLECTION,
                "bbox": bbox,
                "properties": feature.get("properties", {}),
                "assets": list(feature.get("assets", {}).keys()),
                "discovered": storage.now(),
                "watch_center": [area["center_lon"], area["center_lat"]],
                "watch_radius_km": area["radius_km"],
                "catalogue_source": catalogue_source,
                "scene": scene,
            }
            status, artifact_name, sha256 = "DISCOVERED", None, None
            if fetch_imagery:
                status, artifact_name, sha256, token = _fetch_imagery(
                    client, area, feature, bbox, window[1], token, provenance
                )
            obs_id = str(uuid.uuid4())
            with storage.connect() as db:
                db.execute(
                    """INSERT OR IGNORE INTO observations(
                        id,watch_id,stac_id,collection,acquired,created,mode,status,artifact,sha256,provenance
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (obs_id, watch_id, stac_id, COLLECTION,
                     scene["acquired"], storage.now(), "REAL",
                     status, artifact_name, sha256, storage.canonical(provenance)),
                )
            storage.audit(
                area.get("case_id") or "global", "copernicus_observation",
                {
                    "watch_id": watch_id, "stac_id": stac_id, "status": status,
                    "platform": scene["platform"], "acquired": scene["acquired"],
                },
            )
            new_records.append(
                {"stac_id": stac_id, "status": status, "platform": scene["platform"],
                 "acquired": scene["acquired"]}
            )
        retried = 0
        if fetch_imagery:
            token, retried = _retry_pending_imagery(client, area, bbox, window[1], token)
        outcome = "NEW_EARTH_OBSERVATION" if new_records else "NO_NEW_ACQUISITION"
        _record_status(
            watch_id, "OK",
            f"{len(new_records)} new observation(s) of {len(features)} scene(s) in a {SEARCH_WINDOW_DAYS}-day window",
            scene_count=len(features), new_count=len(new_records),
        )
        return {
            "watch_id": watch_id, "status": "OK", "outcome": outcome,
            "new_observations": new_records, "scenes_in_window": len(features),
            "imagery_retried": retried, "catalogue_source": catalogue_source,
        }
    finally:
        if owns_client:
            client.close()


# ---------------------------------------------------------------------------
# Background monitor
# ---------------------------------------------------------------------------


def effective_interval_minutes(area):
    """Configured interval, backed off exponentially after consecutive failures."""
    failures = area.get("consecutive_failures") or 0
    return min(area["interval_minutes"] * (2 ** min(failures, 4)), MAX_BACKOFF_MINUTES)


def due_at(area):
    """Earliest moment this watch area becomes due (None if paused)."""
    if area["status"] != "ACTIVE":
        return None
    if not area["last_checked"]:
        return parse_timestamp(area["created"])
    return parse_timestamp(area["last_checked"]) + timedelta(minutes=effective_interval_minutes(area))


def _due(area, now=None):
    moment = due_at(area)
    if moment is None:
        return False
    return (now or datetime.now(timezone.utc)) >= moment


def next_check_at(area, now=None, scheduler=None):
    """When the scheduler will actually run the next catalogue query for this
    area: the first scheduler tick at or after the area becomes due. Derived
    from the real scheduler state -- the frontend counts down to this value and
    never pretends a check happened."""
    now = now or datetime.now(timezone.utc)
    moment = due_at(area)
    if moment is None:
        return None
    scheduler = scheduler or _scheduler
    tick = scheduler.get("next_tick_at")
    if not scheduler.get("running") or tick is None:
        return max(moment, now)
    if moment <= tick:
        return tick
    periods = -(-(moment - tick).total_seconds() // POLL_GRANULARITY_SECONDS)
    return tick + timedelta(seconds=periods * POLL_GRANULARITY_SECONDS)


def scheduler_state():
    return {
        "running": _scheduler["running"],
        "last_tick_at": _scheduler["last_tick_at"].isoformat() if _scheduler["last_tick_at"] else None,
        "next_tick_at": _scheduler["next_tick_at"].isoformat() if _scheduler["next_tick_at"] else None,
        "granularity_seconds": POLL_GRANULARITY_SECONDS,
    }


def register_tick_hook(fn):
    """Lightweight periodic work (e.g. refreshing cached acquisition plans) that
    should ride on the same scheduler instead of spawning its own loop."""
    if fn not in _background_hooks:
        _background_hooks.append(fn)


async def monitor_tick():
    """One scheduling pass over all watch areas. Exposed separately so tests can
    drive a single tick deterministically instead of racing a sleep loop."""
    areas = await asyncio.to_thread(list_watch_areas)
    due = [a for a in areas if _due(a)]
    if not due:
        return []
    # Catalogue discovery works against the public CDSE STAC endpoint with no
    # credentials at all; only imagery retrieval (inside check_watch_area)
    # needs CDSE_CLIENT_ID/CDSE_CLIENT_SECRET, so scheduled ticks always
    # attempt a real discovery pass rather than short-circuiting here.
    results = []
    with httpx.Client() as client:
        for a in due:
            results.append(await asyncio.to_thread(check_watch_area, a, client))
    return results


async def monitor_loop():
    global _monitor_stop
    _monitor_stop = False
    _scheduler["running"] = True
    try:
        while not _monitor_stop:
            _scheduler["last_tick_at"] = datetime.now(timezone.utc)
            _scheduler["next_tick_at"] = None
            try:
                await monitor_tick()
            except Exception:  # pragma: no cover - defensive; loop must never die silently
                traceback.print_exc()
            for hook in list(_background_hooks):
                try:
                    await asyncio.to_thread(hook)
                except Exception:  # pragma: no cover - defensive
                    traceback.print_exc()
            _scheduler["next_tick_at"] = datetime.now(timezone.utc) + timedelta(
                seconds=POLL_GRANULARITY_SECONDS
            )
            for _ in range(POLL_GRANULARITY_SECONDS):
                if _monitor_stop:
                    break
                await asyncio.sleep(1)
    finally:
        _scheduler["running"] = False
        _scheduler["next_tick_at"] = None


def start_monitor():
    global _monitor_task
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return None
    if _monitor_task is None or _monitor_task.done():
        _monitor_task = asyncio.create_task(monitor_loop())
    return _monitor_task


async def stop_monitor():
    global _monitor_stop, _monitor_task
    _monitor_stop = True
    if _monitor_task is not None:
        try:
            await asyncio.wait_for(_monitor_task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            _monitor_task.cancel()
        _monitor_task = None


def status():
    configured = credentials_configured()
    return {
        "model_version": MODEL_VERSION,
        "credentials_configured": configured,
        "mode": "REAL" if configured else "DEMO",
        "public_catalogue_search": True,
        "collection": COLLECTION,
        "stac_endpoint": STAC_SEARCH_URL,
        "stac_items_endpoint": STAC_ITEMS_URL.format(collection=COLLECTION),
        "process_endpoint": PROCESS_URL,
        "search_window_days": SEARCH_WINDOW_DAYS,
        "poll_granularity_seconds": POLL_GRANULARITY_SECONDS,
        "default_interval_minutes": DEFAULT_INTERVAL_MINUTES,
        "max_backoff_minutes": MAX_BACKOFF_MINUTES,
        "scheduler": scheduler_state(),
        "watch_areas": [
            {**a, "next_check_at": (lambda t: t.isoformat() if t else None)(next_check_at(a))}
            for a in list_watch_areas()
        ],
        "note": (
            "Sentinel-1 GRD scene discovery uses the Copernicus Data Space Ecosystem's public "
            "STAC catalogue and needs no credentials. Retrieving calibrated SIGMA0_ELLIPSOID "
            "backscatter imagery for auto-analyze needs CDSE_CLIENT_ID/CDSE_CLIENT_SECRET "
            "(Sentinel Hub Process API); discovered scenes are recorded as AUTH_REQUIRED for "
            "imagery until credentials are supplied. Discovered scenes are calibrated to sigma0_db "
            "and saved as REAL inputs ready for manual import into a case; nothing is "
            "auto-classified as oil and no case is created automatically."
        ),
    }
