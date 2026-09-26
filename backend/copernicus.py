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
PROCESS_URL = "https://sh.dataspace.copernicus.eu/process/v1"
COLLECTION = "sentinel-1-grd"

DEFAULT_INTERVAL_MINUTES = 15
MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 1440
MAX_BACKOFF_MINUTES = 120
POLL_GRANULARITY_SECONDS = 30
SEARCH_WINDOW_DAYS = 10

_token_cache = {"value": None, "expires_at": 0.0}
_monitor_task = None
_monitor_stop = False


class CredentialsMissing(RuntimeError):
    """Raised when CDSE_CLIENT_ID / CDSE_CLIENT_SECRET are absent or rejected."""


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
    response.raise_for_status()
    return response.json().get("features", [])


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
    response.raise_for_status()
    return response.content


def bbox_for(center, radius_km):
    from shapely.geometry import shape

    from .geo import circle

    return list(shape(circle(center, radius_km)).bounds)


# ---------------------------------------------------------------------------
# Watch-area persistence
# ---------------------------------------------------------------------------


def create_watch_area(name, center, radius_km, interval_minutes=DEFAULT_INTERVAL_MINUTES,
                       case_id=None, run_id=None, note="", source="operator"):
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
                case_id,run_id,note,last_checked,last_status,last_message,consecutive_failures
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
            (watch_id, name, now, center[0], center[1], radius_km, interval_minutes, "ACTIVE",
             source, case_id, run_id, note, None, "PENDING", None),
        )
    storage.audit(
        case_id or "global",
        "watch_area_created",
        {"watch_id": watch_id, "name": name, "center": list(center), "radius_km": radius_km, "source": source},
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


def update_watch_area(watch_id, status=None, interval_minutes=None):
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
    if fields:
        values.append(watch_id)
        with storage.connect() as db:
            db.execute(f"UPDATE watch_areas SET {','.join(fields)} WHERE id=?", values)
        storage.audit(
            "global", "watch_area_updated",
            {"watch_id": watch_id, "status": status, "interval_minutes": interval_minutes},
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


def _record_status(watch_id, status, message):
    with storage.connect() as db:
        row = db.execute("SELECT consecutive_failures FROM watch_areas WHERE id=?", (watch_id,)).fetchone()
        failures = row["consecutive_failures"] if row else 0
        failures = 0 if status == "OK" else failures + 1
        db.execute(
            "UPDATE watch_areas SET last_checked=?, last_status=?, last_message=?, consecutive_failures=? WHERE id=?",
            (storage.now(), status, message, failures, watch_id),
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


def check_watch_area(area, client: "httpx.Client | None" = None, fetch_imagery=True):
    """Run one discovery pass for a single watch area. Never raises; returns a status dict."""
    owns_client = client is None
    client = client or httpx.Client()
    watch_id = area["id"]
    try:
        bbox = bbox_for([area["center_lon"], area["center_lat"]], area["radius_km"])
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=SEARCH_WINDOW_DAYS)
        window = (
            start.isoformat().replace("+00:00", "Z"),
            end.isoformat().replace("+00:00", "Z"),
        )
        try:
            token = get_token(client)
            features = stac_search(client, token, bbox, window[0], window[1])
        except CredentialsMissing as exc:
            _record_status(watch_id, "AUTH_REQUIRED", str(exc))
            return {"watch_id": watch_id, "status": "AUTH_REQUIRED", "message": str(exc)}
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
            provenance = {
                "stac_id": stac_id,
                "collection": COLLECTION,
                "bbox": bbox,
                "properties": feature.get("properties", {}),
                "assets": list(feature.get("assets", {}).keys()),
                "discovered": storage.now(),
                "watch_center": [area["center_lon"], area["center_lat"]],
                "watch_radius_km": area["radius_km"],
            }
            status, artifact_name, sha256 = "DISCOVERED", None, None
            if fetch_imagery:
                acquired = feature.get("properties", {}).get("datetime") or window[1]
                try:
                    raw = process_backscatter(client, token, bbox, (window[0], acquired))
                    artifact_name, sha256 = _save_calibrated_geotiff(stac_id, raw, feature)
                    status = "READY_TO_IMPORT"
                except CredentialsMissing as exc:
                    status = "AUTH_REQUIRED"
                    provenance["fetch_error"] = str(exc)
                except httpx.HTTPError as exc:
                    status = "FETCH_FAILED"
                    provenance["fetch_error"] = str(exc)
                except Exception as exc:  # pragma: no cover - defensive
                    traceback.print_exc()
                    status = "FETCH_FAILED"
                    provenance["fetch_error"] = str(exc)
            obs_id = str(uuid.uuid4())
            with storage.connect() as db:
                db.execute(
                    """INSERT OR IGNORE INTO observations(
                        id,watch_id,stac_id,collection,acquired,created,mode,status,artifact,sha256,provenance
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (obs_id, watch_id, stac_id, COLLECTION,
                     feature.get("properties", {}).get("datetime"), storage.now(), "REAL",
                     status, artifact_name, sha256, storage.canonical(provenance)),
                )
            storage.audit(
                area.get("case_id") or "global", "copernicus_observation",
                {"watch_id": watch_id, "stac_id": stac_id, "status": status},
            )
            new_records.append({"stac_id": stac_id, "status": status})
        _record_status(
            watch_id, "OK",
            f"{len(new_records)} new observation(s) of {len(features)} scene(s) in a {SEARCH_WINDOW_DAYS}-day window",
        )
        return {
            "watch_id": watch_id, "status": "OK",
            "new_observations": new_records, "scenes_in_window": len(features),
        }
    finally:
        if owns_client:
            client.close()


# ---------------------------------------------------------------------------
# Background monitor
# ---------------------------------------------------------------------------


def _due(area):
    if area["status"] != "ACTIVE":
        return False
    if not area["last_checked"]:
        return True
    failures = area["consecutive_failures"] or 0
    effective_minutes = min(area["interval_minutes"] * (2 ** min(failures, 4)), MAX_BACKOFF_MINUTES)
    elapsed = (datetime.now(timezone.utc) - parse_timestamp(area["last_checked"])).total_seconds()
    return elapsed >= effective_minutes * 60


async def monitor_tick():
    """One scheduling pass over all watch areas. Exposed separately so tests can
    drive a single tick deterministically instead of racing a sleep loop."""
    areas = await asyncio.to_thread(list_watch_areas)
    due = [a for a in areas if _due(a)]
    if not due:
        return []
    if not credentials_configured():
        for a in due:
            await asyncio.to_thread(
                _record_status, a["id"], "AUTH_REQUIRED",
                "CDSE_CLIENT_ID / CDSE_CLIENT_SECRET are not set.",
            )
        return [{"watch_id": a["id"], "status": "AUTH_REQUIRED"} for a in due]
    results = []
    with httpx.Client() as client:
        for a in due:
            results.append(await asyncio.to_thread(check_watch_area, a, client))
    return results


async def monitor_loop():
    global _monitor_stop
    _monitor_stop = False
    while not _monitor_stop:
        try:
            await monitor_tick()
        except Exception:  # pragma: no cover - defensive; loop must never die silently
            traceback.print_exc()
        for _ in range(POLL_GRANULARITY_SECONDS):
            if _monitor_stop:
                break
            await asyncio.sleep(1)


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
    return {
        "model_version": MODEL_VERSION,
        "credentials_configured": credentials_configured(),
        "mode": "REAL" if credentials_configured() else "DEMO",
        "collection": COLLECTION,
        "stac_endpoint": STAC_SEARCH_URL,
        "process_endpoint": PROCESS_URL,
        "search_window_days": SEARCH_WINDOW_DAYS,
        "poll_granularity_seconds": POLL_GRANULARITY_SECONDS,
        "max_backoff_minutes": MAX_BACKOFF_MINUTES,
        "watch_areas": list_watch_areas(),
        "note": (
            "Sentinel-1 GRD discovery and SIGMA0_ELLIPSOID backscatter retrieval from the "
            "Copernicus Data Space Ecosystem. Discovered scenes are calibrated to sigma0_db and "
            "saved as REAL inputs ready for manual import into a case; nothing is auto-classified "
            "as oil and no case is created automatically."
        ),
    }
