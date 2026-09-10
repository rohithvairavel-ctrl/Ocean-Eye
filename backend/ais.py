"""MarineCadastre normalization, cleaning, trajectory features and transparent ranking."""

import csv
import re
import numpy as np
from collections import defaultdict
from datetime import datetime, timezone
from .drift import timestamp
from .geo import distance, circle


def load_ais(path):
    groups = defaultdict(list)
    seen = set()
    errors = defaultdict(int)
    total = 0
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise ValueError("AIS CSV has no header")
        normalized = {re.sub("[^a-z]", "", k.lower()): k for k in reader.fieldnames}
        aliases = {
            "mmsi": ["mmsi"],
            "time": ["basedatetime", "timestamp", "time"],
            "lat": ["lat", "latitude"],
            "lon": ["lon", "longitude"],
            "sog": ["sog"],
            "cog": ["cog"],
            "name": ["vesselname"],
            "imo": ["imo"],
            "type": ["vesseltype"],
        }
        lookup = {
            key: next((normalized[a] for a in values if a in normalized), None)
            for key, values in aliases.items()
        }
        if any(not lookup[k] for k in ("mmsi", "time", "lat", "lon")):
            raise ValueError(
                "AIS requires MMSI, BaseDateTime, LAT and LON (or normalized equivalents)"
            )
        for row in reader:
            total += 1
            try:
                get = lambda k, default="": (
                    row.get(lookup[k], default) if lookup[k] else default
                )
                mmsi = str(get("mmsi")).strip()
                if not re.fullmatch(r"\d{9}", mmsi):
                    raise ValueError("identity")
                t = datetime.fromisoformat(get("time").replace("Z", "+00:00"))
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                t = t.astimezone(timezone.utc)
                lat = float(get("lat"))
                lon = float(get("lon"))
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    raise ValueError("coordinates")
                key = (mmsi, t.isoformat())
                if key in seen:
                    errors["duplicate"] += 1
                    continue
                seen.add(key)

                def optional(k, maximum):
                    try:
                        v = float(get(k))
                        return v if np.isfinite(v) and 0 <= v < maximum else None
                    except (TypeError, ValueError):
                        return None

                groups[mmsi].append(
                    {
                        "time": t.isoformat(),
                        "coordinates": [lon, lat],
                        "sog": optional("sog", 102.2),
                        "cog": optional("cog", 360),
                        "name": get("name") or mmsi,
                        "imo": get("imo"),
                        "vessel_type": get("type"),
                    }
                )
            except (ValueError, TypeError, KeyError):
                errors["invalid"] += 1
    clean = {}
    for mmsi, track in groups.items():
        track.sort(key=lambda p: timestamp(p["time"]))
        accepted = []
        for p in track:
            if accepted:
                hours = (
                    timestamp(p["time"]) - timestamp(accepted[-1]["time"])
                ).total_seconds() / 3600
                if (
                    hours <= 0
                    or distance(p["coordinates"], accepted[-1]["coordinates"]) / hours
                    > 120
                ):
                    errors["impossible_speed"] += 1
                    continue
            accepted.append(p)
        if len(accepted) >= 2:
            clean[mmsi] = accepted
        else:
            errors["insufficient_track"] += len(accepted)
    valid = sum(map(len, clean.values()))
    if not clean:
        raise ValueError("No usable AIS tracks remain after validation")
    return clean, {
        "input_rows": total,
        "accepted_rows": valid,
        "rejected_rows": total - valid,
        "reasons": dict(errors),
        "score": round(100 * valid / max(total, 1), 1),
        "method": "Valid identity/location/time, deduplication, chronological sort, 120 km/h jump filter",
    }


def analyze(tracks, origin, observation):
    center = origin["centroid"]
    start, end = map(timestamp, origin["release_window"])
    obs = timestamp(observation)
    result = []
    for mmsi, track in tracks.items():
        relevant = [p for p in track if start <= timestamp(p["time"]) <= end]
        nearest = min(
            relevant or track, key=lambda p: distance(center, p["coordinates"])
        )
        dist = distance(center, nearest["coordinates"])
        gaps = []
        for a, b in zip(track, track[1:]):
            gap = (timestamp(b["time"]) - timestamp(a["time"])).total_seconds() / 60
            if gap > 30:
                overlap = timestamp(a["time"]) <= end and timestamp(b["time"]) >= start
                midpoint = [
                    (a["coordinates"][i] + b["coordinates"][i]) / 2 for i in (0, 1)
                ]
                speed_bound = (
                    max([p["sog"] for p in track if p["sog"] is not None] + [10]) * 1.5
                )
                radius = max(
                    distance(a["coordinates"], b["coordinates"]) / 2,
                    speed_bound * 1.852 * gap / 120,
                )
                gaps.append(
                    {
                        "start": a["time"],
                        "end": b["time"],
                        "minutes": gap,
                        "overlaps_release": overlap,
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [a["coordinates"], b["coordinates"]],
                        },
                        "corridor": circle(midpoint, radius),
                        "speed_bound_kn": round(speed_bound, 1),
                        "interpretation": "Unobserved interval; envelope assumes 1.5x maximum observed speed (10 kn minimum baseline), not an observed dark track",
                    }
                )
        speeds = [p["sog"] for p in track if p["sog"] is not None]
        near_speeds = [p["sog"] for p in relevant if p["sog"] is not None]
        baseline = float(np.median(speeds)) if speeds else 0
        minimum = min(near_speeds) if near_speeds else baseline
        slowdown = max(0, 1 - minimum / max(baseline, 0.1)) if speeds else 0
        courses = [p["cog"] for p in relevant if p["cog"] is not None]
        course_delta = max(
            [abs((b - a + 180) % 360 - 180) for a, b in zip(courses, courses[1:])],
            default=0,
        )
        proximity = float(np.exp(-dist / max(origin["radius90_km"], 1)))
        temporal = (
            1.0
            if relevant
            else float(
                np.exp(
                    -min(
                        abs((timestamp(p["time"]) - start).total_seconds())
                        for p in track
                    )
                    / 10800
                )
            )
        )
        gap_score = max(
            [min(g["minutes"] / 90, 1) for g in gaps if g["overlaps_release"]],
            default=0,
        )
        # Trajectory proximity samples all release-window observations, separate from nearest point.
        consistency = (
            float(
                np.mean(
                    [
                        np.exp(
                            -distance(center, p["coordinates"])
                            / max(2 * origin["radius90_km"], 2)
                        )
                        for p in relevant
                    ]
                )
            )
            if relevant
            else 0
        )
        components = {
            "Origin proximity": proximity,
            "Time coverage": temporal,
            "Trajectory overlap": consistency,
            "Speed reduction": slowdown,
            "Course change": min(course_delta / 30, 1),
            "AIS gap": gap_score,
        }
        weights = {
            "Origin proximity": 0.35,
            "Time coverage": 0.1,
            "Trajectory overlap": 0.25,
            "Speed reduction": 0.12,
            "Course change": 0.08,
            "AIS gap": 0.1,
        }
        score = 100 * sum(components[k] * weights[k] for k in weights)
        support = []
        contradictions = []
        if proximity > 0.4:
            support.append(
                f"Observed {dist:.2f} km from model origin during release window"
            )
        else:
            contradictions.append(
                f"Nearest release-window position {dist:.1f} km from modeled origin"
            )
        if slowdown > 0.5:
            support.append(
                f"Speed fell from track median {baseline:.1f} to {minimum:.1f} knots"
            )
        else:
            contradictions.append("No strong speed reduction during release window")
        if course_delta > 10:
            support.append(f"Observed course change of {course_delta:.1f} degrees")
        if gap_score:
            support.append("AIS reporting gap overlaps assumed release window")
        contradictions.append(
            "No observation of discharge; AIS anomalies have benign explanations"
        )
        current = min(
            track, key=lambda p: abs((timestamp(p["time"]) - obs).total_seconds())
        )
        result.append(
            {
                "mmsi": mmsi,
                "name": track[0]["name"],
                "imo": track[0]["imo"],
                "vessel_type": track[0]["vessel_type"],
                "score": round(score, 1),
                "components": [
                    {
                        "name": k,
                        "value": round(components[k] * 100, 2),
                        "weight": weights[k],
                        "contribution": round(components[k] * weights[k] * 100, 2),
                    }
                    for k in weights
                ],
                "supporting": support,
                "contradicting": contradictions,
                "nearest_km": round(dist, 2),
                "baseline_speed_kn": round(baseline, 2),
                "minimum_speed_kn": round(minimum, 2),
                "course_change_deg": round(course_delta, 2),
                "gaps": gaps,
                "track": track,
                "position": current["coordinates"],
                "geometry": {
                    "type": "LineString",
                    "coordinates": [p["coordinates"] for p in track],
                },
                "route_baseline": "Within-case median speed and course changes; historical route model unavailable",
            }
        )
    return sorted(result, key=lambda r: r["score"], reverse=True)


def attribute(vessels, exclude=None):
    ranked = [v for v in vessels if v["mmsi"] != exclude]
    return {
        "ranking": [
            {k: v for k, v in r.items() if k not in ("track", "geometry")}
            for r in ranked
        ],
        "excluded_mmsi": exclude,
        "score_type": "Weighted investigative screening score; NOT probability of responsibility",
        "weights": (
            {x["name"]: x["weight"] for x in vessels[0]["components"]}
            if vessels
            else {}
        ),
        "hypotheses": [
            {"name": "Tracked vessel source", "status": "Ranked by screening evidence"},
            {
                "name": "Untracked vessel source",
                "status": "Unresolved; AIS completeness unknown",
            },
            {
                "name": "Infrastructure source",
                "status": "Unresolved; requires infrastructure observations",
            },
            {
                "name": "Natural look-alike",
                "status": "Unresolved; SAR dark-region screening cannot reject all look-alikes",
            },
        ],
        "robustness": {
            "top_margin": (
                round(ranked[0]["score"] - ranked[1]["score"], 1)
                if len(ranked) > 1
                else None
            ),
            "interpretation": "Candidate removal reranks remaining evidence; does not prove causal attribution",
        },
    }
