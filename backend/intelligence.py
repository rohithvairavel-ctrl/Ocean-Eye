"""Result-traceable decision support. No LLM-generated operational facts."""

from datetime import timedelta
from typing import Protocol
from .classification import pending_classification, INCIDENT_TYPES, CANDIDATE_LABEL
from .drift import timestamp, impact

VERSION = "response-intelligence-1.0"
RECEPTOR_KINDS = (
    "protected_area",
    "fishery",
    "fisheries",
    "port",
    "infrastructure",
    "coastline",
    "coral",
    "mangrove",
    "seagrass",
    "habitat",
    "species_range",
    "sensitive_ecosystem",
)


class NotificationAdapter(Protocol):
    """Future connector contract; no external delivery adapter is installed."""

    def deliver(self, event: dict, destination: str) -> dict: ...


def alert(rule, severity, title, why, data, action, result, location=None, hours=0):
    return {
        "id": f"{result['run_id']}:{rule}",
        "rule_id": rule,
        "rule_version": VERSION,
        "severity": severity,
        "title": title,
        "why": why,
        "data_used": data,
        "next_action": action,
        "location": location or result["spill"]["centroid"],
        "event_time": (
            timestamp(result["observation_time"]) + timedelta(hours=hours)
        ).isoformat(),
        "time_basis": "Scenario time anchored to satellite observation; not a live warning",
        "uncertainty": "Screening and conditional forecast evidence; no calibrated event probability.",
        "assumptions": [
            "Release window and forcing are analysis inputs.",
            "Forecast overlap is potential exposure, not confirmed damage.",
        ],
        "run_id": result["run_id"],
        "analysis_hash": result["analysis_hash"],
        "source_type": result["source_type"],
        "delivery": {
            "channel": "IN_APP",
            "status": "AVAILABLE",
            "external_notifications": "NOT_CONFIGURED",
        },
    }


def derive(result):
    spill = result["spill"]
    origin = result["origin"]
    forecast = result["forecast"]
    # Recompute only receptor intersection metadata for older runs; physical forecast stays unchanged.
    ecology = impact(forecast, result.get("receptors", {"features": []}))
    features = result.get("receptors", {}).get("features", [])
    biodiversity = [
        f
        for f in features
        if f["properties"].get("kind")
        in ("coral", "mangrove", "seagrass", "habitat", "species_range")
        and f["properties"].get("source_type") == "REAL"
    ]
    ecology["species_assessment"] = (
        "Imported species-range overlap only; no species presence or damage confirmed"
        if any(f["properties"].get("kind") == "species_range" for f in biodiversity)
        else "UNAVAILABLE — no real species-range dataset loaded"
    )
    ecology["biodiversity_coverage"] = (
        "Imported habitat layers available; check publisher authority and scope"
        if biodiversity
        else "No real biodiversity layers loaded; species-level assessment unavailable"
    )
    classification = pending_classification()
    events = []
    if spill.get("candidate_pixels", 0) > 0:
        events.append(
            alert(
                "candidate-detected",
                "WATCH",
                CANDIDATE_LABEL,
                f"{spill['area_km2']:.3f} km² dark-region candidate detected.",
                {
                    "candidate_pixels": spill["candidate_pixels"],
                    "method": spill["method"],
                    "area_km2": spill["area_km2"],
                },
                "Obtain independent oil/look-alike confirmation before treating this as a confirmed oil spill.",
                result,
            )
        )
    for index, receptor in enumerate(ecology["receptors"]):
        h = receptor["first_overlap_h"]
        if h is None:
            continue
        sensitive = receptor["kind"] in (
            "protected_area",
            "coastline",
            "coral",
            "mangrove",
            "seagrass",
            "habitat",
            "species_range",
        )
        severity = "HIGH" if sensitive else "WATCH"
        events.append(
            alert(
                f"receptor-overlap-{index}",
                severity,
                f"Potential exposure: {receptor['name']}",
                f"Conditional forecast envelope overlaps this {receptor['kind']} at the +{h:g} h sample; entry may occur between samples.",
                receptor,
                "Verify receptor data and evaluate response arrival before the sampled exposure horizon.",
                result,
                receptor.get("coordinates"),
                h,
            )
        )
    vessels = result.get("vessels", [])
    near = [
        v
        for v in vessels
        if v["nearest_km"] <= origin["radius90_km"]
        and any(
            timestamp(origin["release_window"][0])
            <= timestamp(p["time"])
            <= timestamp(origin["release_window"][1])
            for p in v["track"]
        )
    ]
    if near:
        v = near[0]
        events.append(
            alert(
                "vessel-review",
                "WATCH",
                f"Review vessel evidence: {v['name']}",
                "Observed track enters the radial origin uncertainty screen during the assumed release window. This does not prove discharge.",
                {
                    "mmsi": v["mmsi"],
                    "nearest_km": v["nearest_km"],
                    "origin_radius90_km": origin["radius90_km"],
                    "score": v["score"],
                    "components": v["components"],
                },
                "Inspect the vessel dossier, contrary evidence and receiver coverage.",
                result,
                v["position"],
            )
        )
    else:
        events.append(
            alert(
                "no-nearby-vessel",
                "INFORMATIONAL",
                "No vessel meets the origin-region review rule",
                "No loaded vessel has an observed release-window position within the origin radial uncertainty screen.",
                {
                    "vessel_count": len(vessels),
                    "origin_radius90_km": origin["radius90_km"],
                },
                "Seek wider AIS coverage or independent observations; do not force an attribution.",
                result,
            )
        )
    maxspread = max([s["spread90_km"] for s in forecast["steps"]], default=0)
    if maxspread > 10:
        events.append(
            alert(
                "forecast-spread-over-10km",
                "WATCH",
                "Wide forecast uncertainty",
                f"Maximum sampled 90% spread is {maxspread:g} km; the review threshold is 10 km.",
                {"spread90_km": maxspread, "threshold_km": 10},
                "Obtain updated currents/wind and rerun a sensitivity scenario.",
                result,
            )
        )
    if not any(f["properties"].get("kind") == "species_range" for f in biodiversity):
        events.append(
            alert(
                "biodiversity-unavailable",
                "INFORMATIONAL",
                "Species-level exposure unavailable",
                "No real species-range reference layer is loaded for this assessment.",
                {
                    "real_biodiversity_layers": len(biodiversity),
                    "real_species_range_layers": 0,
                },
                "Import a documented habitat/species dataset; do not infer species presence from the basemap.",
                result,
            )
        )
    ordering = {"CRITICAL": 0, "HIGH": 1, "WATCH": 2, "INFORMATIONAL": 3}
    events.sort(key=lambda a: ordering[a["severity"]])
    nodes = [
        {
            "id": "sar",
            "label": "SAR acquisition",
            "kind": "OBSERVATION",
            "details": {"time": result["observation_time"], "sensor": spill["sensor"]},
        },
        {
            "id": "preprocessing",
            "label": "SAR preprocessing",
            "kind": "DERIVED MEASUREMENT",
            "details": {"method": spill["method"], "radiometry": spill["radiometry"]},
        },
        {
            "id": "segmentation",
            "label": "Dark-region mask",
            "kind": "DERIVED MEASUREMENT",
            "details": {
                "area_km2": spill["area_km2"],
                "pixels": spill["candidate_pixels"],
            },
        },
        {
            "id": "candidate",
            "label": "Oil candidate · pending",
            "kind": "SCREENING RESULT",
            "details": classification,
        },
        {
            "id": "release",
            "label": "Assumed release window",
            "kind": "ASSUMPTION",
            "details": {"window": origin["release_window"]},
        },
        {
            "id": "origin",
            "label": "Modeled origin REGION",
            "kind": "MODEL OUTPUT",
            "details": {
                "radius90_km": origin["radius90_km"],
                "uncertainty": origin["uncertainty"],
            },
        },
        {
            "id": "ais",
            "label": "AIS observations",
            "kind": "OBSERVATION",
            "details": result["quality"],
        },
        {
            "id": "ranking",
            "label": "Investigative relevance",
            "kind": "SCREENING RESULT",
            "details": {
                "weights": result["attribution"]["weights"],
                "warning": "Not a probability of culpability.",
            },
        },
        {
            "id": "forecast",
            "label": "Conditional forecast",
            "kind": "MODEL OUTPUT",
            "details": {
                "method": forecast["method"],
                "uncertainty": forecast["uncertainty"],
            },
        },
        {
            "id": "ecology",
            "label": "Potential ecological exposure",
            "kind": "MODEL OUTPUT",
            "details": ecology,
        },
        {
            "id": "response",
            "label": "Response scenario foundation",
            "kind": "RESPONSE SCENARIO",
            "details": {
                "status": "Create a planning scenario; effectiveness is not modeled"
            },
        },
        {
            "id": "proof",
            "label": "Evidence & report",
            "kind": "DERIVED MEASUREMENT",
            "details": result["provenance"],
        },
    ]
    pairs = [
        ("sar", "preprocessing"),
        ("preprocessing", "segmentation"),
        ("segmentation", "candidate"),
        ("candidate", "origin"),
        ("release", "origin"),
        ("origin", "ranking"),
        ("ais", "ranking"),
        ("candidate", "forecast"),
        ("forecast", "ecology"),
        ("ecology", "response"),
        ("response", "proof"),
        ("ranking", "proof"),
    ]
    for v in vessels[:6]:
        nodes.append(
            {
                "id": v["mmsi"],
                "label": v["name"],
                "kind": "SCREENING RESULT",
                "mmsi": v["mmsi"],
                "details": {
                    "components": v["components"],
                    "supporting": v["supporting"],
                    "contradicting": v["contradicting"],
                },
            }
        )
        pairs.extend([("ais", v["mmsi"]), (v["mmsi"], "ranking")])
    return {
        "version": VERSION,
        "run_id": result["run_id"],
        "analysis_hash": result["analysis_hash"],
        "classification": classification,
        "incident": {
            "workflow": "oil",
            "pollutant": "UNKNOWN",
            "label": CANDIDATE_LABEL,
            "basis": "SAR dark-region screening only",
            "types": INCIDENT_TYPES,
        },
        "ecology": ecology,
        "alerts": events,
        "recommendations": [
            {
                "id": e["id"],
                "action": e["next_action"],
                "why": e["why"],
                "data_used": e["data_used"],
                "assumptions": e["assumptions"],
                "uncertainty": e["uncertainty"],
                "rule_id": e["rule_id"],
            }
            for e in events
        ],
        "evidence": {
            "nodes": nodes,
            "edges": [
                {"source": a, "target": b, "label": "derived from / informs"}
                for a, b in pairs
            ],
        },
        "relevance": {
            "label": "INVESTIGATIVE RELEVANCE SCORE",
            "disclaimer": "Not a probability of culpability.",
            "nearby_observed_candidates": len(near),
        },
        "notifications": {
            "in_app": True,
            "email": False,
            "sms": False,
            "webhook": False,
        },
    }


def dossier(result, mmsi):
    vessel = next((v for v in result["vessels"] if v["mmsi"] == mmsi), None)
    if vessel is None:
        raise KeyError("Vessel is not in this investigation")
    provenance = next(
        (i for i in result["provenance"]["inputs"] if i["role"] == "ais"), {}
    )
    return {
        "run_id": result["run_id"],
        "case_id": result["case_id"],
        "analysis_hash": result["analysis_hash"],
        "identity": {
            k: {"value": vessel.get(k) or None, "source": provenance}
            for k in ("mmsi", "imo", "name", "vessel_type")
        },
        "record": vessel,
        "score_label": "INVESTIGATIVE RELEVANCE SCORE",
        "disclaimer": "Not a probability of culpability.",
        "external_metadata": {
            "status": "UNAVAILABLE",
            "ownership": None,
            "violations": None,
            "history": None,
        },
        "provenance": result["provenance"],
        "observed_period": [vessel["track"][0]["time"], vessel["track"][-1]["time"]],
    }
