"""Copernicus Marine Service surface-current provider.

Product  GLOBAL_ANALYSISFORECAST_PHY_001_024 (Mercator global analysis & forecast)
Dataset  cmems_mod_glo_phy_anfc_merged-uv_PT1H-i (hourly merged surface currents)

Two independent capabilities, each reported honestly:

1. Product metadata (public, no account): the Copernicus Marine STAC metadata
   catalogue publishes the dataset's temporal coverage and last update, which
   tells us how fresh the model product is. Cached; refreshed at most every
   METADATA_REFRESH_HOURS by the background scheduler.

2. Data subsetting (requires a Copernicus Marine account + the official
   `copernicusmarine` toolbox): only the AOI x time window actually needed is
   opened lazily and averaged -- never a global download. Without the toolbox
   or credentials the provider reports NOT_INSTALLED / AUTH_REQUIRED; nothing
   is simulated.

Retrieved currents are converted into the EXISTING environmental-forcing
contract consumed by backend.drift (records of current_east_ms /
current_north_ms / *_sigma_ms). The Marine product carries no wind; wind terms
are therefore set to zero and this is declared explicitly as a currents-only
forcing assumption in the output, never hidden.
"""

import math
import os
import traceback
from datetime import datetime, timedelta, timezone

import httpx
import numpy as np

from . import storage

PRODUCT_ID = "GLOBAL_ANALYSISFORECAST_PHY_001_024"
DATASET_PREFIX = "cmems_mod_glo_phy_anfc_merged-uv_PT1H-i"
PRODUCT_STAC_URL = f"https://stac.marine.copernicus.eu/metadata/{PRODUCT_ID}/product.stac.json"
METADATA_REFRESH_HOURS = 6
UNITS = "m/s"
WIND_ASSUMPTION = (
    "Copernicus Marine surface currents carry no wind. Wind terms are set to 0 m/s "
    "(currents-only drift): the windage contribution is absent, not estimated."
)


def toolbox_available():
    try:
        import copernicusmarine  # noqa: F401

        return True
    except ImportError:
        return False


def credentials_configured():
    return bool(os.environ.get("COPERNICUSMARINE_SERVICE_USERNAME")) and bool(
        os.environ.get("COPERNICUSMARINE_SERVICE_PASSWORD")
    )


def _parse(value):
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    if len(text) == 10:
        text += "T00:00:00+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 1. Public product metadata
# ---------------------------------------------------------------------------


def parse_dataset_metadata(item):
    """Temporal coverage and update stamps from a Copernicus Marine dataset
    STAC item. Only fields the item actually carries are returned."""
    props = item.get("properties") or {}
    start = props.get("start_datetime")
    end = props.get("end_datetime")
    if not (start and end):
        interval = (((item.get("extent") or {}).get("temporal") or {}).get("interval") or [[None, None]])[0]
        start, end = start or interval[0], end or interval[1]
    if not (start and end):
        time_dim = ((props.get("cube:dimensions") or {}).get("time") or {}).get("extent") or [None, None]
        start, end = start or time_dim[0], end or time_dim[1]
    variables = sorted((props.get("cube:variables") or {}).keys())
    return {
        "dataset_id": item.get("id"),
        "title": props.get("title"),
        "coverage_start": _parse(start).isoformat() if start else None,
        "coverage_end": _parse(end).isoformat() if end else None,
        "product_updated": props.get("admp_updated"),
        "data_updated": props.get("admp_updated_data"),
        "variables": variables,
    }


def refresh_metadata(client=None, force=False, now=None):
    now = now or datetime.now(timezone.utc)
    state = storage.get_provider_state("marine_metadata") or {}
    last = state.get("last_attempt")
    if not force and last and now - _parse(last) < timedelta(hours=METADATA_REFRESH_HOURS):
        return state
    owns = client is None
    client = client or httpx.Client(follow_redirects=True)
    update = {k: v for k, v in state.items() if k != "updated"}
    update["last_attempt"] = now.isoformat()
    try:
        product = client.get(PRODUCT_STAC_URL, timeout=30)
        product.raise_for_status()
        link = next(
            (
                l["href"]
                for l in product.json().get("links", [])
                if DATASET_PREFIX in l.get("href", "") and l.get("rel") in ("item", "child")
            ),
            None,
        )
        if not link:
            raise ValueError(f"{DATASET_PREFIX} is not listed in the product catalogue")
        url = link if link.startswith("http") else PRODUCT_STAC_URL.rsplit("/", 1)[0] + "/" + link
        item = client.get(url, timeout=30)
        item.raise_for_status()
        meta = parse_dataset_metadata(item.json())
        update.update({"last_success": now.isoformat(), "last_error": None, "last_status": "OK",
                       "metadata": meta, "metadata_url": url})
    except httpx.HTTPError as exc:
        update.update({"last_error": f"Could not reach the Copernicus Marine catalogue: {exc}",
                       "last_status": "OFFLINE"})
    except Exception as exc:
        traceback.print_exc()
        update.update({"last_error": str(exc), "last_status": "ERROR"})
    finally:
        if owns:
            client.close()
    storage.set_provider_state("marine_metadata", update)
    return update


def scheduled_refresh():
    from .copernicus import list_watch_areas

    if any(a["status"] == "ACTIVE" for a in list_watch_areas()):
        refresh_metadata()


# ---------------------------------------------------------------------------
# 2. AOI x time subset -> existing forcing contract
# ---------------------------------------------------------------------------


def series_from_arrays(times, u_mean, v_mean, u_std=None, v_std=None, meta=None):
    """Hourly AOI-averaged current series. NaN hours (land / no data) are dropped,
    not filled."""
    rows = []
    for i, t in enumerate(times):
        u, v = float(u_mean[i]), float(v_mean[i])
        if not (math.isfinite(u) and math.isfinite(v)):
            continue
        su = float(u_std[i]) if u_std is not None and math.isfinite(float(u_std[i])) else 0.0
        sv = float(v_std[i]) if v_std is not None and math.isfinite(float(v_std[i])) else 0.0
        rows.append(
            {
                "time": _parse(str(t)).isoformat(),
                "current_east_ms": round(u, 4),
                "current_north_ms": round(v, 4),
                "spatial_sigma_ms": round(math.sqrt((su * su + sv * sv) / 2), 4),
            }
        )
    rows.sort(key=lambda r: r["time"])
    return {"records": rows, **(meta or {})}


def build_environment(series, coverage, forecast_reference_time=None):
    """Existing environmental-forcing contract (backend.drift.validate_environment)."""
    records = [
        {
            "time": r["time"],
            "current_east_ms": r["current_east_ms"],
            "current_north_ms": r["current_north_ms"],
            "wind_east_ms": 0.0,
            "wind_north_ms": 0.0,
            "current_sigma_ms": r["spatial_sigma_ms"],
            "wind_sigma_ms": 0.0,
        }
        for r in series["records"]
    ]
    return {
        "source_type": "REAL",
        "source": f"Copernicus Marine {PRODUCT_ID} / {series.get('dataset_id', DATASET_PREFIX)} "
        f"({series.get('variables', ['uo', 'vo'])}), AOI mean",
        "units": UNITS,
        "spatial_coverage": coverage,
        "records": records,
        "forecast_reference_time": forecast_reference_time,
        "retrieved": series.get("retrieved"),
        "product_id": PRODUCT_ID,
        "dataset_id": series.get("dataset_id", DATASET_PREFIX),
        "forcing_terms": {"currents": "Copernicus Marine surface currents", "wind": WIND_ASSUMPTION},
        "uncertainty_basis": "current_sigma_ms = spatial standard deviation of hourly currents across the AOI; model error not included.",
    }


def fetch_surface_currents(bbox, start, end):
    """Open only the AOI x time subset through the official toolbox and average it."""
    if not toolbox_available():
        raise RuntimeError("NOT_INSTALLED: the copernicusmarine toolbox is not installed")
    if not credentials_configured():
        raise PermissionError(
            "AUTH_REQUIRED: set COPERNICUSMARINE_SERVICE_USERNAME / COPERNICUSMARINE_SERVICE_PASSWORD"
        )
    import copernicusmarine

    dataset = copernicusmarine.open_dataset(
        dataset_id=DATASET_PREFIX,
        variables=["utotal", "vtotal", "uo", "vo"],
        minimum_longitude=bbox[0],
        minimum_latitude=bbox[1],
        maximum_longitude=bbox[2],
        maximum_latitude=bbox[3],
        start_datetime=start.isoformat(),
        end_datetime=end.isoformat(),
        username=os.environ.get("COPERNICUSMARINE_SERVICE_USERNAME"),
        password=os.environ.get("COPERNICUSMARINE_SERVICE_PASSWORD"),
    )
    u_name, v_name = ("utotal", "vtotal") if "utotal" in dataset else ("uo", "vo")
    u, v = dataset[u_name], dataset[v_name]
    for name in ("depth", "elevation"):
        if name in u.dims:
            u, v = u.isel({name: 0}), v.isel({name: 0})
    spatial = [d for d in u.dims if d != "time"]
    times = [np.datetime_as_string(t, unit="s") + "Z" for t in u["time"].values]
    return series_from_arrays(
        times,
        u.mean(dim=spatial, skipna=True).values,
        v.mean(dim=spatial, skipna=True).values,
        u.std(dim=spatial, skipna=True).values,
        v.std(dim=spatial, skipna=True).values,
        {
            "dataset_id": DATASET_PREFIX,
            "variables": [u_name, v_name],
            "units": u.attrs.get("units", "m s-1"),
            "bbox": bbox,
            "retrieved": datetime.now(timezone.utc).isoformat(),
        },
    )


def refresh_subset(bbox, start, end):
    """Retrieve and cache the latest AOI subset for the live data strip."""
    state = {"last_attempt": datetime.now(timezone.utc).isoformat(), "bbox": bbox}
    try:
        series = fetch_surface_currents(bbox, start, end)
        state.update({"last_status": "OK", "last_error": None, "series": series,
                      "last_success": state["last_attempt"]})
    except PermissionError as exc:
        state.update({"last_status": "AUTH_REQUIRED", "last_error": str(exc)})
    except RuntimeError as exc:
        state.update({"last_status": "NOT_INSTALLED" if str(exc).startswith("NOT_INSTALLED") else "ERROR",
                      "last_error": str(exc)})
    except Exception as exc:  # network / service errors from the toolbox
        traceback.print_exc()
        state.update({"last_status": "OFFLINE", "last_error": str(exc)})
    storage.set_provider_state("marine_subset", state)
    return state


# ---------------------------------------------------------------------------
# Environmental time series (observation / latest / +6 .. +48 h)
# ---------------------------------------------------------------------------


def _kind(env, valid):
    if env.get("source_type") == "SYNTHETIC":
        return "SYNTHETIC"
    ref = env.get("forecast_reference_time")
    if ref:
        return "ANALYSIS" if valid <= _parse(ref) else "FORECAST"
    return "UNSPECIFIED"


def _sample(env, times, values, t):
    if t < times[0] or t > times[-1]:
        return None
    return float(np.interp(t.timestamp(), [x.timestamp() for x in times], values))


def current_speed_direction(east, north):
    """Speed (m/s) and direction the current flows TOWARD, degrees clockwise from north."""
    speed = math.hypot(east, north)
    direction = (math.degrees(math.atan2(east, north)) + 360) % 360
    return round(speed, 3), round(direction, 1)


def time_series(env, observation_time, offsets=(6, 12, 24, 48)):
    records = sorted(env.get("records") or [], key=lambda r: r["time"])
    if not records:
        return {"available": False, "reason": "No environmental records loaded for this case.", "points": []}
    times = [_parse(r["time"]) for r in records]
    ue = [r["current_east_ms"] for r in records]
    un = [r["current_north_ms"] for r in records]
    obs = _parse(observation_time)
    wanted = [("AT OBSERVATION", obs, 0)] + [(f"+{h}H", obs + timedelta(hours=h), h) for h in offsets]
    wanted.insert(1, ("LATEST AVAILABLE", times[-1], (times[-1] - obs).total_seconds() / 3600))
    points = []
    for label, t, hours in wanted:
        east, north = _sample(env, times, ue, t), _sample(env, times, un, t)
        if east is None:
            points.append({"label": label, "valid_time": t.isoformat(), "available": False,
                           "reason": "Outside the loaded forcing window"})
            continue
        speed, direction = current_speed_direction(east, north)
        points.append(
            {
                "label": label,
                "valid_time": t.isoformat(),
                "hours_from_observation": round(hours, 2),
                "available": True,
                "current_east_ms": round(east, 4),
                "current_north_ms": round(north, 4),
                "speed_ms": speed,
                "direction_to_deg": direction,
                "kind": _kind(env, t),
            }
        )
    return {
        "available": True,
        "source": env.get("source"),
        "source_type": env.get("source_type"),
        "forecast_reference_time": env.get("forecast_reference_time"),
        "coverage": [times[0].isoformat(), times[-1].isoformat()],
        "direction_convention": "Direction the current flows toward, degrees clockwise from true north.",
        "note": "Forcing values are model or supplied inputs, never observations of the slick.",
        "points": points,
    }
