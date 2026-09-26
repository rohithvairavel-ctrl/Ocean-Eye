"""Seeded Lagrangian particle ensemble under explicitly supplied forcing."""

from datetime import datetime, timedelta
import numpy as np
from .geo import ellipse, distance, feature
from shapely.geometry import shape, Point


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Timestamp must be an ISO 8601 string with timezone")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Timestamps must include a timezone")
    return dt


def validate_environment(env, observation, release):
    if not isinstance(env, dict):
        raise ValueError("Environmental input must be a JSON object")
    if env.get("units") != "m/s":
        raise ValueError("Environmental units must be m/s")
    coverage = env.get("spatial_coverage")
    if (
        not isinstance(coverage, list)
        or len(coverage) != 4
        or any(not isinstance(v, (int, float)) or not np.isfinite(v) for v in coverage)
        or not (
            -180 <= coverage[0] < coverage[2] <= 180
            and -90 <= coverage[1] < coverage[3] <= 90
        )
    ):
        raise ValueError(
            "Environmental spatial_coverage must be [west,south,east,north] in WGS84"
        )
    records = env.get("records", [])
    if not isinstance(records, list):
        raise ValueError("Environmental records must be a list")
    if len(records) < 2:
        raise ValueError("At least two time-indexed forcing records are required")
    required = [
        "current_east_ms",
        "current_north_ms",
        "wind_east_ms",
        "wind_north_ms",
        "current_sigma_ms",
        "wind_sigma_ms",
    ]
    for row in records:
        if not isinstance(row, dict):
            raise ValueError("Each environmental record must be an object")
        timestamp(row.get("time"))
        if any(
            k not in row
            or not isinstance(row[k], (int, float))
            or not np.isfinite(row[k])
            for k in required
        ):
            raise ValueError("Invalid environmental forcing values")
        if row["current_sigma_ms"] < 0 or row["wind_sigma_ms"] < 0:
            raise ValueError("Uncertainty must be non-negative")
    if len({timestamp(r["time"]) for r in records}) != len(records):
        raise ValueError("Environmental record timestamps must be unique")
    records.sort(key=lambda r: timestamp(r["time"]))
    first, last = timestamp(records[0]["time"]), timestamp(records[-1]["time"])
    if first > timestamp(release[0]) or last < timestamp(observation) + timedelta(
        hours=48
    ):
        raise ValueError(
            "Environmental forcing must cover release window through observation +48h"
        )
    return records


def simulate(spill, env, observation, release, seed=428, particles=500, windage=0.03):
    records = validate_environment(env, observation, release)
    center = spill["centroid"]
    obs = timestamp(observation)
    coverage = env.get("spatial_coverage")
    if not coverage or not (
        coverage[0] <= center[0] <= coverage[2]
        and coverage[1] <= center[1] <= coverage[3]
    ):
        raise ValueError("Spill falls outside declared environmental coverage")
    rng = np.random.default_rng(seed)
    times = np.array([timestamp(r["time"]).timestamp() for r in records])

    def forcing(t, key):
        return np.interp(t.timestamp(), times, [r[key] for r in records])

    # Initial uncertainty reflects equivalent slick radius, not a precise release point.
    radius = max(np.sqrt(spill["area_km2"] / np.pi) * 1000 / 2, 100)
    start = np.column_stack(
        [rng.normal(0, radius, particles), rng.normal(0, radius, particles)]
    )
    from .geo import projection

    _, back = projection(*center)

    def coords(xy):
        lon, lat = back.transform(xy[:, 0], xy[:, 1])
        return np.column_stack([lon, lat])

    age_min = (obs - timestamp(release[1])).total_seconds() / 3600
    age_max = (obs - timestamp(release[0])).total_seconds() / 3600
    if not 0 < age_min <= age_max <= 96:
        raise ValueError("Release window must precede observation by 0-96 hours")
    release_ages = rng.uniform(age_min, age_max, particles)
    xy = start.copy()
    origin_xy = np.zeros_like(xy)
    assigned = np.zeros(particles, bool)
    systematic = rng.normal(0, 1, (particles, 2))
    snapshots = []
    dt = 0.25
    for step in range(1, int(np.ceil(age_max / dt)) + 1):
        age = step * dt
        t = obs - timedelta(hours=age)
        v = np.array(
            [
                forcing(t, "current_east_ms") + windage * forcing(t, "wind_east_ms"),
                forcing(t, "current_north_ms") + windage * forcing(t, "wind_north_ms"),
            ]
        )
        sigma = forcing(t, "current_sigma_ms") + windage * forcing(t, "wind_sigma_ms")
        xy -= (v + systematic * sigma) * dt * 3600
        xy += rng.normal(0, np.sqrt(2 * 5 * dt * 3600), xy.shape)
        take = (release_ages <= age) & ~assigned
        origin_xy[take] = xy[take]
        assigned[take] = True
        if step % 24 == 0 or step == int(np.ceil(age_max / dt)):
            pts = coords(xy)
            snapshots.append(
                {
                    "hours_before": round(age, 2),
                    "particles": pts[::5].tolist(),
                    "geometry": ellipse(pts),
                    "center": pts.mean(axis=0).tolist(),
                }
            )
    origin_points = coords(origin_xy)
    mean = origin_points.mean(axis=0).tolist()
    spread = float(np.quantile([distance(mean, p) for p in origin_points], 0.9))
    origin = {
        "centroid": mean,
        "geometry": ellipse(origin_points),
        "particles": origin_points[::5].tolist(),
        "radius90_km": round(spread, 2),
        "release_window": release,
        "age_hours": [round(age_min, 2), round(age_max, 2)],
        "snapshots": snapshots,
        "method": "500-particle quarter-hour Euler hindcast",
        "parameters": {
            "particles": particles,
            "seed": seed,
            "windage": windage,
            "diffusivity_m2s": 5,
            "dt_hours": dt,
        },
        "uncertainty": "90% conditional ensemble region; excludes unknown forcing bias",
        "release_window_basis": "Analyst-supplied assumption; not inferred from SAR",
        "confidence": None,
    }
    forecasts = []
    xy = start.copy()
    for step in range(1, 193):
        t = obs + timedelta(hours=step * dt)
        v = np.array(
            [
                forcing(t, "current_east_ms") + windage * forcing(t, "wind_east_ms"),
                forcing(t, "current_north_ms") + windage * forcing(t, "wind_north_ms"),
            ]
        )
        sigma = forcing(t, "current_sigma_ms") + windage * forcing(t, "wind_sigma_ms")
        xy += (v + systematic * sigma) * dt * 3600
        xy += rng.normal(0, np.sqrt(2 * 15 * dt * 3600), xy.shape)
        if step in (24, 48, 96, 192):
            pts = coords(xy)
            forecasts.append(
                {
                    "hours": step * dt,
                    "time": t.isoformat(),
                    "geometry": ellipse(pts),
                    "center": pts.mean(axis=0).tolist(),
                    "particles": pts[::5].tolist(),
                    "spread90_km": round(
                        float(
                            np.quantile(
                                np.linalg.norm(xy - xy.mean(axis=0), axis=1), 0.9
                            )
                        )
                        / 1000,
                        2,
                    ),
                }
            )
    return origin, {
        "steps": forecasts,
        "method": "Ensemble advection + windage + diffusion",
        "uncertainty": "Conditional particle spread, not oil mass or shoreline-arrival probability",
        "parameters": {**origin["parameters"], "diffusivity_m2s": 15},
    }


def impact(forecast, receptors):
    results = []
    for f in receptors.get("features", []):
        g = shape(f["geometry"])
        hits = []
        for step in forecast["steps"]:
            if g.intersects(shape(step["geometry"])):
                hits.append(step["hours"])
        results.append(
            {
                "name": f["properties"].get("name", "Unnamed"),
                "kind": f["properties"].get("kind", "unknown"),
                "source_type": f["properties"].get("source_type", "UNKNOWN"),
                "first_overlap_h": min(hits) if hits else None,
                "first_overlap_time": next(
                    (
                        step["time"]
                        for step in forecast["steps"]
                        if step["hours"] in hits
                    ),
                    None,
                ),
                "source": f["properties"].get("source", "Not recorded"),
                "version": f["properties"].get("version", "Not recorded"),
                "coordinates": list(
                    (
                        g.intersection(
                            shape(
                                next(
                                    step["geometry"]
                                    for step in forecast["steps"]
                                    if step["hours"] == min(hits)
                                )
                            )
                        )
                        if hits
                        else g
                    )
                    .representative_point()
                    .coords[0]
                ),
                "status": "POTENTIAL EXPOSURE" if hits else "NO SAMPLED OVERLAP",
                "uncertainty": "First overlapping forecast sample; onset may occur between samples. Not confirmed damage.",
                "distance48_km": round(
                    distance(
                        forecast["steps"][-1]["center"],
                        g.representative_point().coords[0],
                    ),
                    2,
                ),
            }
        )
    results.sort(
        key=lambda r: (
            r["first_overlap_h"] is None,
            r["first_overlap_h"] or 999,
            r["distance48_km"],
        )
    )
    return {
        "receptors": results,
        "level": (
            "POTENTIAL EXPOSURE"
            if any(r["first_overlap_h"] for r in results)
            else "NO LOADED RECEPTOR OVERLAP"
        ),
        "limitations": "Overlap of conditional 90% envelope with loaded receptors; absence of overlap is not proof of no impact.",
    }
