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
    inputs = {i["role"]: i for i in result["provenance"].get("inputs", [])}
    obs_time = result["observation_time"]
    assets = result.get("assets", "")

    def node(id_, label, kind, stage, details, *, epistemic, source, time=None,
             assumptions=(), uncertainty=None, artifact=None, sha256=None, **extra):
        return {
            "id": id_,
            "label": label,
            "kind": kind,
            "stage": stage,
            "details": details,
            "meta": {
                "epistemic_state": epistemic,
                "source": source,
                "timestamp": time,
                "assumptions": list(assumptions),
                "uncertainty": uncertainty,
                "artifact": artifact,
                "sha256": sha256,
            },
            **extra,
        }

    nodes = [
        node("sar", "SAR acquisition", "OBSERVATION", "observe",
             {"time": obs_time, "sensor": spill["sensor"]},
             epistemic="OBSERVED", source=inputs.get("satellite", {}).get("name", spill["sensor"]),
             time=obs_time, artifact=f"{assets}/satellite.png",
             sha256=inputs.get("satellite", {}).get("sha256")),
        node("ais", "AIS observations", "OBSERVATION", "observe", result["quality"],
             epistemic="OBSERVED", source=inputs.get("ais", {}).get("name", "AIS input"),
             uncertainty="AIS gaps can reflect receiver coverage, not vessel behaviour.",
             sha256=inputs.get("ais", {}).get("sha256")),
        node("preprocessing", "SAR preprocessing", "DERIVED MEASUREMENT", "measure",
             {"method": spill["method"], "radiometry": spill["radiometry"],
              "calibration": spill.get("calibration")},
             epistemic="DERIVED", source="SAR preprocessing and dark-region segmentation (this run)", time=obs_time,
             artifact=f"{assets}/processed.png"),
        node("segmentation", "Dark-region segmentation", "DERIVED MEASUREMENT", "measure",
             {"method": spill["method"], "radiometry": spill["radiometry"],
              "area_km2": spill["area_km2"], "pixels": spill["candidate_pixels"]},
             epistemic="DERIVED", source="SAR preprocessing and dark-region segmentation (this run)", time=obs_time,
             uncertainty="Threshold screening; not a validated oil classifier.",
             artifact=f"{assets}/mask.tif"),
        node("candidate", "Oil candidate — classification pending", "SCREENING RESULT", "measure",
             classification, epistemic="SCREENING", source="Classifier contract — no validated oil classifier installed",
             uncertainty="Pollutant identity UNKNOWN; look-alikes not excluded."),
        node("release", "Assumed release window", "ASSUMPTION", "reconstruct",
             {"window": origin["release_window"], "basis": origin.get("release_window_basis")},
             epistemic="ASSUMED", source="Analyst-supplied case configuration",
             assumptions=["Release occurred inside this window"]),
        node("origin", "Modeled origin region", "MODEL OUTPUT", "reconstruct",
             {"radius90_km": origin["radius90_km"], "uncertainty": origin["uncertainty"],
              "parameters": origin["parameters"]},
             epistemic="MODELED", source=origin["method"], time=origin["release_window"][0],
             assumptions=["Forcing inputs are representative", "Release window assumption"],
             uncertainty=origin["uncertainty"], artifact=f"{assets}/origin_probability.geojson"),
        node("ranking", "Investigative relevance ranking", "SCREENING RESULT", "attribute",
             {"weights": result["attribution"]["weights"], "warning": "Not a probability of culpability."},
             epistemic="SCREENING", source="AIS screening and investigative ranking (this run)",
             uncertainty="Weighted screening score; not a probability of culpability.",
             artifact=f"{assets}/ais_candidates.csv"),
        node("truthloop", "TruthLoop challenge", "ADVERSARIAL TEST", "challenge",
             {"top_score_margin": (result.get("attribution") or {}).get("robustness", {}).get("top_margin"),
              "recorded_challenges": "See TruthLoop for the latest recorded challenge"},
             epistemic="TESTED ON DEMAND", source="TruthLoop adversarial recomputation",
             assumptions=["Recomputes this run's own ranking and hindcast only; no new evidence"],
             uncertainty="Stability is a deterministic qualitative class, not statistical confidence."),
        node("contradicting", "Contradicting evidence", "DERIVED MEASUREMENT", "challenge",
             {"contradicting": vessels[0]["contradicting"] if vessels else []},
             epistemic="DERIVED", source="AIS screening (this run)"),
        node("forecast", "Conditional forecast", "MODEL OUTPUT", "consequence",
             {"method": forecast["method"], "uncertainty": forecast["uncertainty"],
              "horizons_h": [s["hours"] for s in forecast["steps"]]},
             epistemic="PREDICTED", source=forecast["method"], time=obs_time,
             assumptions=["Uniform forcing adapter", "No weathering or beaching"],
             uncertainty=forecast["uncertainty"], artifact=f"{assets}/forecast.geojson"),
        node("ecology", "Potential ecological exposure", "MODEL OUTPUT", "consequence", ecology,
             epistemic="PREDICTED", source="Forecast envelope × loaded receptor layers",
             uncertainty="Overlap is potential exposure, not confirmed damage."),
        node("response", "Response planning scenarios", "RESPONSE SCENARIO", "consequence",
             {"status": "Planning scenarios are created by the operator; effectiveness is not modeled"},
             epistemic="ASSUMED", source="Operator-entered planning inputs",
             assumptions=["Asset availability unverified", "Straight-line route"]),
        node("next_observation", "Next-best observation", "RECOMMENDATION", "next",
             {"basis": "Ranked re-observation targets derived from this run's outputs"},
             epistemic="HEURISTIC", source="Next-best-observation heuristic",
             uncertainty="Transparent weighted heuristic; not formal information gain."),
        node("proof", "Evidence package", "EVIDENCE PACKAGE", "prove", result["provenance"],
             epistemic="RECORDED", source="Forensic report + SHA-256 manifest",
             time=result.get("created"), artifact=f"{assets}/evidence.zip",
             sha256=result.get("analysis_hash")),
    ]
    pairs = [
        ("sar", "preprocessing"),
        ("preprocessing", "segmentation"),
        ("segmentation", "candidate"),
        ("candidate", "origin"),
        ("release", "origin"),
        ("origin", "ranking"),
        ("ais", "ranking"),
        ("ranking", "truthloop"),
        ("truthloop", "contradicting"),
        ("candidate", "forecast"),
        ("forecast", "ecology"),
        ("ecology", "response"),
        ("contradicting", "next_observation"),
        ("ecology", "next_observation"),
        ("next_observation", "proof"),
        ("response", "proof"),
        ("ranking", "proof"),
    ]
    for v in vessels[:3]:
        nodes.append(
            node(v["mmsi"], v["name"], "VESSEL EVIDENCE", "attribute",
                 {"score": v["score"], "components": v["components"],
                  "supporting": v["supporting"], "contradicting": v["contradicting"]},
                 epistemic="SCREENING", source="AIS screening (this run)",
                 uncertainty="Investigative relevance score; not a probability of culpability.",
                 mmsi=v["mmsi"])
        )
        pairs.extend([("ais", v["mmsi"]), ("origin", v["mmsi"]), (v["mmsi"], "ranking")])
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
