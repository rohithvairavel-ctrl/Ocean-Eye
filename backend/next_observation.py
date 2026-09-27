"""Next-Best-Observation: a transparent heuristic scheduling aid.

This is NOT a trained model and computes no probability of anything. It ranks
a small set of physically-motivated re-observation targets that already exist
in a completed run's own outputs -- the modeled origin region, the top-ranked
vessel, the widest AIS gap corridor overlapping the release window, the
nearest receptor with sampled forecast overlap, and the original candidate
itself -- using a documented weighted heuristic. Every score is a starting
point for an analyst's judgment about where to point the next Sentinel-1
acquisition, never a directive and never a probability.
"""

from .drift import timestamp

MODEL_VERSION = "next-best-observation-heuristic-1.0"

WEIGHTS = {
    "time_criticality": 0.35,
    "uncertainty_value": 0.30,
    "receptor_sensitivity": 0.20,
    "evidence_value": 0.15,
}

SENSITIVE_KINDS = (
    "protected_area", "coastline", "coral", "mangrove", "seagrass", "habitat", "species_range",
)


def _score(partial, evidence_value):
    components = {**partial, "evidence_value": evidence_value}
    value = 100 * sum(components[k] * WEIGHTS[k] for k in WEIGHTS)
    breakdown = [
        {
            "name": k,
            "value": round(components[k] * 100, 1),
            "weight": WEIGHTS[k],
            "contribution": round(components[k] * WEIGHTS[k] * 100, 1),
        }
        for k in WEIGHTS
    ]
    return round(value, 1), breakdown


def _impact(result):
    """Runs created by older builds stored receptor overlaps without their
    representative coordinates / overlap times. Recompute only that intersection
    metadata from the run's own forecast and receptors (the physical forecast is
    unchanged), exactly as backend.intelligence does for older runs."""
    impact = result["impact"]
    receptors = impact.get("receptors", [])
    if any("coordinates" not in r or "first_overlap_time" not in r for r in receptors) and result.get("receptors"):
        from .drift import impact as recompute

        return recompute(result["forecast"], result["receptors"])
    return impact


def candidates(result):
    spill = result["spill"]
    origin = result["origin"]
    vessels = result.get("vessels", [])
    impact = _impact(result)
    out = []

    radius = origin["radius90_km"]
    score, components = _score(
        {"time_criticality": 0.6, "uncertainty_value": min(radius / 20, 1.0), "receptor_sensitivity": 0.2},
        evidence_value=0.95,
    )
    out.append({
        "id": "origin",
        "target_type": "MODELED ORIGIN REGION",
        "center": origin["centroid"],
        "suggested_radius_km": max(round(radius * 1.3, 1), 5),
        "score": score,
        "components": components,
        "rationale": (
            f"A conditional 90% origin region spans {radius:.1f} km; a new acquisition centered "
            "here can directly test or narrow the modeled origin -- the highest-value "
            "re-observation for attribution work."
        ),
        "basis": "Derived from this run's origin hindcast. MODELED ORIGIN REGION, not an exact source.",
    })

    if vessels:
        v = vessels[0]
        try:
            recency_h = abs(
                (timestamp(result["observation_time"]) - timestamp(v["nearest_time"])).total_seconds()
            ) / 3600
            time_crit = max(0.0, 1 - min(recency_h, 48) / 48)
        except Exception:
            time_crit = 0.5
        score, components = _score(
            {"time_criticality": time_crit, "uncertainty_value": 0.4, "receptor_sensitivity": 0.1},
            evidence_value=0.7,
        )
        out.append({
            "id": f"vessel-{v['mmsi']}",
            "target_type": "TOP-RANKED VESSEL POSITION",
            "center": v["position"],
            "suggested_radius_km": 8,
            "score": score,
            "components": components,
            "rationale": (
                f"{v['name']} (MMSI {v['mmsi']}) carries the highest investigative relevance score "
                f"({v['score']}/100). A follow-up acquisition over its current position may capture "
                "corroborating or exculpatory SAR evidence."
            ),
            "basis": "Investigative relevance score -- not a probability of culpability.",
        })

    gap_hits = [
        (v, i, g) for v in vessels for i, g in enumerate(v.get("gaps", [])) if g["overlaps_release"]
    ]
    if gap_hits:
        v, i, g = max(gap_hits, key=lambda item: item[2]["minutes"])
        coords = g["geometry"]["coordinates"]
        midpoint = [(coords[0][k] + coords[1][k]) / 2 for k in (0, 1)]
        score, components = _score(
            {
                "time_criticality": 0.5,
                "uncertainty_value": min(g["minutes"] / 180, 1.0),
                "receptor_sensitivity": 0.15,
            },
            evidence_value=0.6,
        )
        out.append({
            "id": f"gap-{v['mmsi']}-{i}",
            "target_type": "AIS REPORTING GAP CORRIDOR",
            "center": midpoint,
            "suggested_radius_km": max(5, min(round(g["minutes"] / 6, 1), 40)),
            "score": score,
            "components": components,
            "rationale": (
                f"{v['name']} has a {g['minutes']:.0f}-minute AIS reporting gap overlapping the "
                "assumed release window. A new SAR pass over this unobserved corridor may capture "
                "a vessel not currently tracked; it does not confirm a dark vessel."
            ),
            "basis": "Unobserved-interval envelope, not an observed dark track.",
        })

    receptor_hits = [r for r in impact["receptors"] if r["first_overlap_h"] is not None]
    if receptor_hits:
        r = receptor_hits[0]
        sensitive = r["kind"] in SENSITIVE_KINDS
        time_crit = max(0.0, 1 - min(r["first_overlap_h"], 48) / 48)
        score, components = _score(
            {
                "time_criticality": time_crit,
                "uncertainty_value": 0.3,
                "receptor_sensitivity": 1.0 if sensitive else 0.3,
            },
            evidence_value=0.5,
        )
        out.append({
            "id": f"receptor-{receptor_hits.index(r)}",
            "target_type": "NEAREST POTENTIAL EXPOSURE RECEPTOR",
            "center": r["coordinates"],
            "suggested_radius_km": 10,
            "score": score,
            "components": components,
            "rationale": (
                f"{r['name']} ({r['kind']}) has the earliest sampled forecast overlap at "
                f"+{r['first_overlap_h']:g} h. A pre-arrival acquisition can verify whether the "
                "candidate slick is actually approaching before the modeled window."
            ),
            "basis": "POTENTIAL EXPOSURE from the conditional forecast envelope; not confirmed ecological damage.",
        })

    score, components = _score(
        {"time_criticality": 0.7, "uncertainty_value": 0.25, "receptor_sensitivity": 0.1},
        evidence_value=0.4,
    )
    out.append({
        "id": "spill",
        "target_type": "SPILL CANDIDATE RE-OBSERVATION",
        "center": spill["centroid"],
        "suggested_radius_km": max(round((spill["area_km2"] ** 0.5) * 2, 1), 5),
        "score": score,
        "components": components,
        "rationale": (
            "A repeat acquisition over the original candidate area establishes persistence, growth "
            "or dissipation -- evidence a single SAR image cannot provide."
        ),
        "basis": "OIL CANDIDATE -- CLASSIFICATION PENDING; a second observation does not by itself confirm oil.",
    })

    out.sort(key=lambda c: c["score"], reverse=True)
    return out


def derive(result):
    return {
        "model_version": MODEL_VERSION,
        "run_id": result["run_id"],
        "case_id": result["case_id"],
        "analysis_hash": result["analysis_hash"],
        "method": "Weighted heuristic over run-derived candidate targets; not a trained model, not a probability.",
        "weights": WEIGHTS,
        "candidates": candidates(result),
    }
