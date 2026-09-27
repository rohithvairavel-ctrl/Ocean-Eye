"""Sentinel-1 planned acquisitions from ESA's published acquisition-plan KML files.

ESA publishes rolling Sentinel-1 mission plans (one KML per satellite and
planning window) on the Copernicus Sentinel website. This module:

* discovers the plan files currently listed on that page,
* downloads the most recent plan(s) per satellite that still cover the future,
* parses each planned datatake (satellite, begin/end, mode, swath,
  polarisation, datatake id, and any orbit fields the KML carries),
* intersects future datatakes with watch-area AOIs to find the next PLANNED
  pass over each AOI.

A plan is a plan: acquisitions can be re-planned, cancelled or lost. Every
result is labelled PLANNED — NOT GUARANTEED. When no current plan can be
retrieved, the next pass is reported as UNKNOWN; it is never extrapolated
from a nominal repeat cycle.

Network access happens only in refresh(), which the background scheduler
calls at most every REFRESH_HOURS. Status requests read the local cache.
"""

import json
import re
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import httpx

from . import storage
from .config import DATA

PLAN_PAGE_URL = "https://sentinels.copernicus.eu/web/sentinel/copernicus/sentinel-1/acquisition-plans"
BASE_URL = "https://sentinels.copernicus.eu"
REFRESH_HOURS = 12
FILES_PER_SATELLITE = 2
RELEVANT_MODES = ("IW", "EW", "SM")  # WV wave-mode vignettes are not usable imagery for slicks
LABEL = "PLANNED — NOT GUARANTEED"

_FILE_RE = re.compile(
    r"(?:https?://sentinels\.copernicus\.eu)?(/documents/[^\"'\s<>]*?"
    r"(s1[a-d])_mp_user_(\d{8}t\d{6})_(\d{8}t\d{6})[^\"'\s<>]*)",
    re.IGNORECASE,
)
_next_pass_memo = {}


def _utc(stamp):
    return datetime.strptime(stamp.upper(), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)


def _parse_time(text):
    value = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def satellite_name(code):
    code = code.upper()
    return f"Sentinel-{code[1:]}" if code.startswith("S1") else code


# ---------------------------------------------------------------------------
# Plan discovery and selection
# ---------------------------------------------------------------------------


def list_plan_files(html):
    """All acquisition-plan file links on the ESA page, de-duplicated."""
    seen, files = set(), []
    for match in _FILE_RE.finditer(html):
        path, code, start, end = match.groups()
        url = BASE_URL + path
        if url in seen:
            continue
        seen.add(url)
        files.append(
            {
                "satellite": satellite_name(code),
                "code": code.upper(),
                "start": _utc(start).isoformat(),
                "end": _utc(end).isoformat(),
                "url": url,
            }
        )
    return files


def select_plan_files(files, now, per_satellite=FILES_PER_SATELLITE):
    """Per satellite, the most recently issued plans that still reach the future.
    Later-issued plans supersede earlier ones where they overlap (ESA re-issues
    plans when planning changes)."""
    chosen = []
    for code in sorted({f["code"] for f in files}):
        live = [f for f in files if f["code"] == code and _parse_time(f["end"]) > now]
        live.sort(key=lambda f: f["start"], reverse=True)
        chosen.extend(live[:per_satellite])
    return chosen


# ---------------------------------------------------------------------------
# KML parsing
# ---------------------------------------------------------------------------


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _polygon(text):
    points = []
    for token in text.split():
        parts = token.split(",")
        if len(parts) >= 2:
            points.append([round(float(parts[0]), 4), round(float(parts[1]), 4)])
    if len(points) >= 3 and points[0] != points[-1]:
        points.append(points[0])
    return points


def parse_kml(data, plan=None):
    """Planned datatakes from one acquisition-plan KML (bytes or str)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    import io

    segments = []
    for _event, element in ET.iterparse(io.BytesIO(data), events=("end",)):
        if _local(element.tag) != "Placemark":
            continue
        fields, begin, end, coords = {}, None, None, None
        for child in element.iter():
            name = _local(child.tag)
            if name == "begin" and child.text:
                begin = _parse_time(child.text)
            elif name == "end" and child.text:
                end = _parse_time(child.text)
            elif name == "Data":
                value = next((c.text for c in child if _local(c.tag) == "value"), None)
                if child.get("name") and value is not None:
                    fields[child.get("name")] = value.strip()
            elif name == "coordinates" and child.text and coords is None:
                coords = _polygon(child.text)
        element.clear()
        if not begin or not end or not coords or len(coords) < 4:
            continue
        code = (fields.get("SatelliteId") or (plan or {}).get("code") or "").upper()
        orbit = {
            k: fields[k]
            for k in fields
            if "orbit" in k.lower() or k.lower() in ("pass", "orbitdirection")
        }
        segments.append(
            {
                "satellite": satellite_name(code) if code else None,
                "begin": begin.isoformat(),
                "end": end.isoformat(),
                "mode": fields.get("Mode"),
                "swath": fields.get("Swath"),
                "polarisation": fields.get("Polarisation"),
                "datatake_id": fields.get("DatatakeId"),
                "orbit": orbit or None,
                "polygon": coords,
                "plan_url": (plan or {}).get("url"),
                "plan_start": (plan or {}).get("start"),
            }
        )
    return segments


def merge_plans(parsed):
    """Combine segments from several plans; where a later-issued plan covers a
    time, its datatakes replace the earlier plan's for that satellite."""
    by_sat = {}
    for plan, segments in parsed:
        by_sat.setdefault(plan["code"], []).append((plan, segments))
    merged = []
    for code, plans in by_sat.items():
        plans.sort(key=lambda item: item[0]["start"])
        for index, (plan, segments) in enumerate(plans):
            later = [p for p, _ in plans[index + 1:]]
            for seg in segments:
                t = _parse_time(seg["begin"])
                if any(_parse_time(p["start"]) <= t <= _parse_time(p["end"]) for p in later):
                    continue
                merged.append(seg)
    merged.sort(key=lambda s: s["begin"])
    return merged


# ---------------------------------------------------------------------------
# AOI intersection
# ---------------------------------------------------------------------------


def _shape(points):
    from shapely.geometry import Polygon

    lons = [p[0] for p in points]
    if max(lons) - min(lons) > 180:  # crosses the antimeridian: unwrap to 0..360
        points = [[p[0] + 360 if p[0] < 0 else p[0], p[1]] for p in points]
    polygon = Polygon(points)
    return polygon if polygon.is_valid else polygon.buffer(0)


def next_pass(segments, aoi, now, modes=RELEVANT_MODES):
    """Earliest planned datatake beginning after `now` whose footprint
    intersects the AOI (a shapely geometry in lon/lat). None if the loaded plan
    contains no such datatake -- the caller reports UNKNOWN, never a guess."""
    from shapely.affinity import translate

    minx, miny, maxx, maxy = aoi.bounds
    shifted = translate(aoi, xoff=360)
    for seg in segments:
        if modes and seg.get("mode") not in modes:
            continue
        if _parse_time(seg["begin"]) <= now:
            continue
        lats = [p[1] for p in seg["polygon"]]
        if max(lats) < miny or min(lats) > maxy:
            continue
        footprint = _shape(seg["polygon"])
        if footprint.intersects(aoi) or footprint.intersects(shifted):
            return {k: v for k, v in seg.items() if k != "polygon"}
    return None


# ---------------------------------------------------------------------------
# Cache + refresh
# ---------------------------------------------------------------------------


def _folder():
    folder = DATA / "acquisition_plans"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def load_segments():
    path = _folder() / "segments.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def refresh(client=None, force=False, now=None):
    """Fetch the ESA plan listing and current plan files. Throttled to one real
    attempt every REFRESH_HOURS unless force=True. Failures keep the previous
    cache and are recorded honestly (OFFLINE / ERROR)."""
    now = now or datetime.now(timezone.utc)
    state = storage.get_provider_state("acquisition_plan") or {}
    last = state.get("last_attempt")
    if not force and last and now - _parse_time(last) < timedelta(hours=REFRESH_HOURS):
        return state
    owns = client is None
    client = client or httpx.Client(follow_redirects=True)
    update = {k: v for k, v in state.items() if k != "updated"}
    update["last_attempt"] = now.isoformat()
    try:
        response = client.get(PLAN_PAGE_URL, timeout=30)
        if response.status_code == 429:
            raise RuntimeError("RATE_LIMITED: ESA acquisition-plan page answered HTTP 429")
        response.raise_for_status()
        listed = list_plan_files(response.text)
        chosen = select_plan_files(listed, now)
        if not chosen:
            raise ValueError("No acquisition-plan file covering the future is listed on the ESA page")
        parsed = []
        for plan in chosen:
            kml = client.get(plan["url"], timeout=90)
            kml.raise_for_status()
            (_folder() / (plan["url"].rsplit("/", 1)[-1] + ".kml")).write_bytes(kml.content)
            parsed.append((plan, parse_kml(kml.content, plan)))
        segments = merge_plans(parsed)
        payload = {
            "fetched_at": now.isoformat(),
            "source": PLAN_PAGE_URL,
            "files": chosen,
            "segment_count": len(segments),
            "coverage_end": max((s["end"] for s in segments), default=None),
            "segments": segments,
        }
        (_folder() / "segments.json").write_text(json.dumps(payload), encoding="utf-8")
        _next_pass_memo.clear()
        update.update(
            {
                "last_success": now.isoformat(),
                "last_error": None,
                "last_status": "OK",
                "files": chosen,
                "segment_count": len(segments),
                "coverage_end": payload["coverage_end"],
            }
        )
    except httpx.HTTPError as exc:
        update.update({"last_error": f"Could not reach ESA acquisition plans: {exc}", "last_status": "OFFLINE"})
    except Exception as exc:
        traceback.print_exc()
        text = str(exc)
        update.update(
            {
                "last_error": text,
                "last_status": "RATE_LIMITED" if text.startswith("RATE_LIMITED") else "ERROR",
            }
        )
    finally:
        if owns:
            client.close()
    storage.set_provider_state("acquisition_plan", update)
    return update


def scheduled_refresh():
    """Scheduler hook: refresh only when something is actually being watched."""
    from .copernicus import list_watch_areas

    if any(a["status"] == "ACTIVE" for a in list_watch_areas()):
        refresh()


def next_passes(areas, now=None):
    """Next planned pass per watch area from the cached plan."""
    from shapely.geometry import shape

    from .geo import circle

    now = now or datetime.now(timezone.utc)
    cache = load_segments()
    out = {}
    for area in areas:
        if cache is None:
            out[area["id"]] = None
            continue
        key = (cache["fetched_at"], area["id"], area["center_lon"], area["center_lat"], area["radius_km"])
        hit = _next_pass_memo.get(key)
        if hit is not None and (hit == "NONE" or _parse_time(hit["begin"]) > now):
            out[area["id"]] = None if hit == "NONE" else hit
            continue
        aoi = shape(circle([area["center_lon"], area["center_lat"]], area["radius_km"]))
        found = next_pass(cache["segments"], aoi, now)
        _next_pass_memo[key] = found or "NONE"
        out[area["id"]] = found
    return out, cache


def plan_freshness(cache, now):
    if not cache:
        return "UNAVAILABLE"
    age = (now - _parse_time(cache["fetched_at"])).total_seconds()
    if cache.get("coverage_end") and _parse_time(cache["coverage_end"]) <= now:
        return "VERY_STALE"
    if age <= 24 * 3600:
        return "CURRENT"
    if age <= 72 * 3600:
        return "STALE"
    return "VERY_STALE"
