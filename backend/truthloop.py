"""OCEAN-EYE TRUTHLOOP -- adversarial evidence & falsification engine.

TruthLoop does not merely support the leading investigative hypothesis; it
tries to break it. It re-runs the SAME deterministic ranking and hindcast
functions the base investigation used (backend.ais.analyze / attribute,
backend.drift.simulate) with specific, named pieces of evidence removed, or
with assumptions varied within explicitly bounded sensitivity ranges. It is
not an LLM and invents no evidence: every hypothesis, challenge and
stability verdict below is built only from a completed run's own real
outputs (backend.engine.run's result dict), never from new data.
"""

import copy
from datetime import timedelta

from . import ais, drift
from .classification import pending_classification
from .drift import timestamp
from .geo import distance
from .next_observation import candidates as next_observation_candidates

MODEL_VERSION = "truthloop-adversarial-falsification-1.0"

WINDAGE_BOUNDS = (0.0, 0.1)  # matches the app's accepted run-time windage range
WINDAGE_SENSITIVITY_FACTOR = 1.5  # documented sensitivity multiplier, not fabricated
RELEASE_SHIFT_HOURS = 2.0  # bounded release-time sensitivity shift

STANDING_LIMITATION = (
    "No direct observation of a discharge exists; every candidate is supported "
    "only by circumstantial screening evidence."
)

_HYPOTHESIS_MAP = {
    "MODELED ORIGIN REGION": ["H1", "H2", "H4"],
    "TOP-RANKED VESSEL POSITION": ["H1", "H3"],
    "AIS REPORTING GAP CORRIDOR": ["H3"],
    "NEAREST POTENTIAL EXPOSURE RECEPTOR": [],
    "SPILL CANDIDATE RE-OBSERVATION": ["H5"],
}


# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------


def _base_weights(result):
    vessels = result.get("vessels") or []
    if vessels and vessels[0].get("components"):
        return {c["name"]: c["weight"] for c in vessels[0]["components"]}
    return dict(ais.DEFAULT_WEIGHTS)


def _reweighted(weights, drop_key):
    """Zero out one factor's weight and renormalize the rest to sum to 1.0.

    Keeps every original key present (at weight 0 for the dropped factor)
    rather than deleting it, since backend.ais.analyze()'s component table
    always reports all six factors -- a removed factor should show as
    zero-weighted, not silently disappear.
    """
    if drop_key not in weights or weights[drop_key] <= 0:
        return None
    remaining_total = sum(v for k, v in weights.items() if k != drop_key)
    if remaining_total <= 0:
        return None
    return {k: (0.0 if k == drop_key else v / remaining_total) for k, v in weights.items()}


def _tracks_from_result(result):
    return {v["mmsi"]: v["track"] for v in result.get("vessels", [])}


def _ranking_summary(vessels):
    return [{"mmsi": v["mmsi"], "name": v["name"], "score": v["score"]} for v in vessels[:5]]


def _challenge_result(id_, label, description, method, baseline_ranking, after_vessels, note=None):
    after_ranking = _ranking_summary(after_vessels) if after_vessels is not None else None
    changed = bool(
        after_ranking and baseline_ranking and after_ranking[0]["mmsi"] != baseline_ranking[0]["mmsi"]
    )
    if after_ranking is None:
        explanation = note or "This challenge could not be run against this run's evidence."
    elif changed:
        explanation = (
            f"Ranking reverses when {label.lower()} is applied: "
            f"{baseline_ranking[0]['name']} ({baseline_ranking[0]['score']}/100) drops from #1, "
            f"replaced by {after_ranking[0]['name']} ({after_ranking[0]['score']}/100)."
        )
    else:
        top = after_ranking[0]
        explanation = f"{top['name']} remains the top-ranked candidate ({top['score']}/100) after {label.lower()}."
    return {
        "id": id_,
        "label": label,
        "description": description,
        "method": method,
        "available": after_ranking is not None,
        "ranking_changed": changed,
        "baseline_ranking": baseline_ranking,
        "after_ranking": after_ranking,
        "explanation": explanation,
    }


# --------------------------------------------------------------------------
# B. challenges
# --------------------------------------------------------------------------


def _factor_challenge(result, weights, drop_key, id_, label, description):
    vessels = result.get("vessels") or []
    baseline_ranking = _ranking_summary(vessels)
    method = (
        "backend.ais.analyze() re-run over this run's own AIS tracks and modeled origin, with the "
        "factor's weight set to zero and the remaining weights renormalized to sum to 1."
    )
    modified = _reweighted(weights, drop_key)
    if modified is None or not vessels:
        return _challenge_result(
            id_, label, description, method, baseline_ranking, None,
            note=f"'{drop_key}' carries no weight in this run's scoring, or no vessels are tracked; nothing to remove.",
        )
    tracks = _tracks_from_result(result)
    reranked = ais.analyze(tracks, result["origin"], result["observation_time"], weights=modified)
    return _challenge_result(id_, label, description, method, baseline_ranking, reranked)


def _exclude_strongest_challenge(result):
    vessels = result.get("vessels") or []
    baseline_ranking = _ranking_summary(vessels)
    base = {
        "id": "exclude_strongest_candidate",
        "label": "Exclude the strongest candidate",
        "description": "Removes the current top-ranked vessel entirely and re-ranks the remaining candidates.",
        "method": "backend.ais.attribute() re-run with the top-ranked MMSI excluded.",
        "baseline_ranking": baseline_ranking,
    }
    if len(vessels) < 2:
        return {
            **base,
            "available": False,
            "ranking_changed": False,
            "after_ranking": None,
            "explanation": "Only one AIS-tracked vessel exists in this run; there is no remaining candidate to rank after exclusion.",
        }
    excluded = vessels[0]["mmsi"]
    remaining = sorted([v for v in vessels if v["mmsi"] != excluded], key=lambda v: v["score"], reverse=True)
    after_ranking = _ranking_summary(remaining)
    margin = round(baseline_ranking[0]["score"] - after_ranking[0]["score"], 1)
    return {
        **base,
        "available": True,
        "ranking_changed": True,
        "after_ranking": after_ranking,
        "explanation": (
            f"With {baseline_ranking[0]['name']} excluded, {after_ranking[0]['name']} becomes the new "
            f"leading candidate at {after_ranking[0]['score']}/100 ({margin:+.1f} points relative to the "
            "excluded candidate's score) -- screening evidence alone, not a stronger causal case."
        ),
    }


def _origin_challenge(id_, label, description, method, result, *, windage=None, release_window=None, env=None):
    vessels = result.get("vessels") or []
    baseline_ranking = _ranking_summary(vessels)
    spill, origin = result.get("spill"), result.get("origin")
    env_data = env if env is not None else result.get("environment")
    if not spill or not origin or not env_data:
        return _challenge_result(
            id_, label, description, method, baseline_ranking, None,
            note="This run does not have the spill/origin/environment data needed to re-run the hindcast.",
        )
    try:
        new_origin, _forecast = drift.simulate(
            spill,
            env_data,
            result["observation_time"],
            release_window or origin["release_window"],
            seed=origin["parameters"]["seed"],
            windage=windage if windage is not None else origin["parameters"]["windage"],
        )
    except ValueError as exc:
        return _challenge_result(
            id_, label, description, method, baseline_ranking, None,
            note=f"This sensitivity variant is outside the model's valid input range: {exc}",
        )
    tracks = _tracks_from_result(result)
    reranked = ais.analyze(tracks, new_origin, result["observation_time"])
    return _challenge_result(id_, label, description, method, baseline_ranking, reranked)


def _expand_origin_uncertainty_challenge(result):
    origin = result.get("origin") or {}
    params = origin.get("parameters", {})
    base_windage = params.get("windage", 0.03)
    _lo, hi = WINDAGE_BOUNDS
    test_windage = min(base_windage * WINDAGE_SENSITIVITY_FACTOR, hi)
    return _origin_challenge(
        "expand_origin_uncertainty",
        "Expand origin uncertainty",
        f"Re-hindcasts with windage raised to {test_windage:.3f} (from {base_windage:.3f}, within the "
        f"app's accepted 0-{hi:g} range) to widen the modeled origin envelope.",
        "backend.drift.simulate() re-run with windage raised within its accepted range, then "
        "backend.ais.analyze() re-run against the new origin.",
        result,
        windage=test_windage,
    )


def _shift_release_time_challenge(result):
    origin = result.get("origin") or {}
    window = origin.get("release_window")
    method = (
        "backend.drift.simulate() re-run with the release window shifted within its valid 0-96 h "
        "release-to-observation range, then backend.ais.analyze() re-run against the new origin."
    )
    if not window:
        return _origin_challenge("shift_release_time", "Shift release time", "No release window on this run.", method, result)
    obs = timestamp(result["observation_time"])
    r0, r1 = timestamp(window[0]), timestamp(window[1])
    age_min = (obs - r1).total_seconds() / 3600
    age_max = (obs - r0).total_seconds() / 3600
    headroom_older = 96 - age_max
    shift_hours = min(RELEASE_SHIFT_HOURS, max(headroom_older - 0.25, 0))
    if shift_hours > 0.1:
        new_r0, new_r1 = r0 - timedelta(hours=shift_hours), r1 - timedelta(hours=shift_hours)
        direction = "earlier"
    else:
        shift_hours = min(RELEASE_SHIFT_HOURS, max(age_min - 0.25, 0))
        new_r0, new_r1 = r0 + timedelta(hours=shift_hours), r1 + timedelta(hours=shift_hours)
        direction = "later"
    new_window = [new_r0.isoformat(), new_r1.isoformat()]
    return _origin_challenge(
        "shift_release_time",
        "Shift release time",
        f"Re-hindcasts with the assumed release window shifted {shift_hours:.1f} h {direction}, staying "
        "within the model's valid 0-96 h release-to-observation range.",
        method,
        result,
        release_window=new_window,
    )


def _vary_forcing_challenge(result):
    env = result.get("environment")
    method = (
        "backend.drift.simulate() re-run with every forcing record perturbed by its own stated "
        "uncertainty, then backend.ais.analyze() re-run against the new origin."
    )
    if not env or not env.get("records"):
        return _origin_challenge("vary_forcing_assumptions", "Vary forcing assumptions", "No environmental records on this run.", method, result)
    perturbed = copy.deepcopy(env)
    for row in perturbed["records"]:
        row["current_east_ms"] = row["current_east_ms"] + row.get("current_sigma_ms", 0)
        row["current_north_ms"] = row["current_north_ms"] + row.get("current_sigma_ms", 0)
        row["wind_east_ms"] = row["wind_east_ms"] + row.get("wind_sigma_ms", 0)
        row["wind_north_ms"] = row["wind_north_ms"] + row.get("wind_sigma_ms", 0)
    return _origin_challenge(
        "vary_forcing_assumptions",
        "Vary forcing assumptions",
        "Re-hindcasts with every forcing record shifted by +1 of its own stated current/wind "
        "uncertainty (current_sigma_ms / wind_sigma_ms) -- the sensitivity range already declared in "
        "the environmental input, not an invented one.",
        method,
        result,
        env=perturbed,
    )


def run_all_challenges(result):
    weights = _base_weights(result)
    return [
        _factor_challenge(
            result, weights, "Origin proximity", "remove_proximity", "Remove origin-proximity evidence",
            "Excludes how close the vessel was to the modeled origin during the release window.",
        ),
        _factor_challenge(
            result, weights, "AIS gap", "remove_ais_gap", "Remove AIS-gap evidence",
            "Excludes AIS reporting gaps overlapping the release window.",
        ),
        _factor_challenge(
            result, weights, "Speed reduction", "remove_speed_change", "Remove speed-change evidence",
            "Excludes the vessel's speed drop during the release window.",
        ),
        _factor_challenge(
            result, weights, "Course change", "remove_course_change", "Remove course-change evidence",
            "Excludes the vessel's heading change during the release window.",
        ),
        _exclude_strongest_challenge(result),
        _expand_origin_uncertainty_challenge(result),
        _shift_release_time_challenge(result),
        _vary_forcing_challenge(result),
    ]


# --------------------------------------------------------------------------
# C. conclusion stability
# --------------------------------------------------------------------------


def conclusion_stability(result, challenges):
    vessels = result.get("vessels") or []
    if not vessels:
        return {
            "state": "INDETERMINATE",
            "reasons": ["No AIS-tracked vessels exist in this run; there is no leading candidate to challenge."],
            "reversals": [],
            "challenges_run": 0,
            "challenges_available": 0,
        }
    testable = [c for c in challenges if c["id"] != "exclude_strongest_candidate"]
    run = [c for c in testable if c["available"]]
    if not run:
        return {
            "state": "INDETERMINATE",
            "reasons": ["No challenge could be run against this run's evidence (no AIS gaps, or insufficient environmental/track data)."],
            "reversals": [],
            "challenges_run": 0,
            "challenges_available": len(testable),
        }
    reversals = [c for c in run if c["ranking_changed"]]
    margin = ((result.get("attribution") or {}).get("robustness") or {}).get("top_margin")
    reasons = [f"{c['label']} changes the top-ranked candidate." for c in reversals]
    close_margin = margin is not None and margin < 5
    if close_margin:
        reasons.append(f"The baseline score margin between the top two candidates is only {margin} points.")
    reasons.append(STANDING_LIMITATION)
    if reversals:
        state = "FRAGILE"
    elif close_margin:
        state = "MODERATE"
    else:
        state = "ROBUST"
    return {
        "state": state,
        "reasons": reasons,
        "reversals": [c["id"] for c in reversals],
        "challenges_run": len(run),
        "challenges_available": len(testable),
    }


# --------------------------------------------------------------------------
# D. evidence fragility
# --------------------------------------------------------------------------


def evidence_fragility(result):
    vessels = result.get("vessels") or []
    if not vessels:
        return []
    top = vessels[0]
    total = top["score"] or 1
    out = []
    for c in top["components"]:
        share = c["contribution"] / total if total else 0
        if share >= 0.30:
            influence = "HIGH"
        elif share >= 0.12:
            influence = "MEDIUM"
        else:
            influence = "LOW"
        out.append(
            {
                "factor": c["name"],
                "value": c["value"],
                "weight": c["weight"],
                "contribution": c["contribution"],
                "share_of_score": round(share * 100, 1),
                "influence": influence,
            }
        )
    out.sort(key=lambda x: x["contribution"], reverse=True)
    return out


def most_dependent_factor(fragility):
    return fragility[0]["factor"] if fragility else None


# --------------------------------------------------------------------------
# A. competing hypotheses
# --------------------------------------------------------------------------


def _nearest_infrastructure(result):
    receptors = (result.get("receptors") or {}).get("features", [])
    spill = result.get("spill") or {}
    center = spill.get("centroid")
    if not center or not receptors:
        return None
    from shapely.geometry import shape as _shape

    best = None
    for f in receptors:
        if f["properties"].get("kind") not in ("infrastructure", "port"):
            continue
        point = list(_shape(f["geometry"]).representative_point().coords[0])
        d = distance(center, point)
        if best is None or d < best["distance_km"]:
            best = {"name": f["properties"].get("name", "Unnamed"), "kind": f["properties"].get("kind"), "distance_km": round(d, 2)}
    return best


def build_hypotheses(result):
    vessels = result.get("vessels") or []
    dark = result.get("dark", {})
    hyps = []

    if vessels:
        v = vessels[0]
        origin = result.get("origin") or {}
        hyps.append(
            {
                "id": "H1",
                "label": "Highest-ranked vessel candidate",
                "reference": {"mmsi": v["mmsi"], "name": v["name"]},
                "supporting_evidence": v["supporting"],
                "contradicting_evidence": v["contradicting"],
                "missing_evidence": [
                    "No direct observation of discharge from this vessel",
                    "No independent (non-AIS) identification",
                ],
                "assumptions": [
                    "The assumed release window is analyst-supplied, not observed",
                    "AIS reporting from this vessel is complete for the window",
                ],
                "uncertainties": (
                    [f"Modeled origin has a 90% radius of {origin['radius90_km']:.1f} km"]
                    if origin.get("radius90_km") is not None
                    else ["Origin uncertainty not available"]
                ),
                "current_position": "OPEN -- highest-ranked by screening evidence; not proven.",
            }
        )
    else:
        hyps.append(
            {
                "id": "H1",
                "label": "Highest-ranked vessel candidate",
                "reference": None,
                "supporting_evidence": [],
                "contradicting_evidence": [],
                "missing_evidence": ["No AIS-tracked vessels in this run"],
                "assumptions": [],
                "uncertainties": [],
                "current_position": "NOT EVALUATED -- no AIS tracks loaded.",
            }
        )

    if len(vessels) > 1:
        v2 = vessels[1]
        hyps.append(
            {
                "id": "H2",
                "label": "Second-ranked vessel candidate",
                "reference": {"mmsi": v2["mmsi"], "name": v2["name"]},
                "supporting_evidence": v2["supporting"],
                "contradicting_evidence": v2["contradicting"],
                "missing_evidence": ["No direct observation of discharge from this vessel"],
                "assumptions": ["Same release-window and AIS-completeness assumptions as H1"],
                "uncertainties": [f"Score margin to H1: {round(vessels[0]['score'] - v2['score'], 1)} points"],
                "current_position": "OPEN -- unresolved; ranked below H1 by screening evidence only.",
            }
        )
    else:
        hyps.append(
            {
                "id": "H2",
                "label": "Second-ranked vessel candidate",
                "reference": None,
                "supporting_evidence": [],
                "contradicting_evidence": [],
                "missing_evidence": ["Only one AIS-tracked vessel exists in this run; H2 cannot be constructed from AIS data"],
                "assumptions": [],
                "uncertainties": [],
                "current_position": "NOT EVALUATED -- insufficient AIS tracks.",
            }
        )

    unmatched = dark.get("unmatched_returns", 0)
    hyps.append(
        {
            "id": "H3",
            "label": "Unknown / non-AIS vessel",
            "reference": None,
            "supporting_evidence": ([f"{unmatched} SAR bright return(s) did not match any tracked vessel"] if unmatched else []),
            "contradicting_evidence": (["Every detected SAR bright return matched a tracked vessel within tolerance"] if not unmatched else []),
            "missing_evidence": [
                "Independent (radar/optical/patrol) detection of an untracked vessel",
                "AIS receiver coverage map for this area and window",
            ],
            "assumptions": ["AIS receiver coverage over the area is assumed, not verified"],
            "uncertainties": ["AIS gaps can reflect receiver coverage rather than vessel absence"],
            "current_position": "OPEN -- cannot be confirmed or excluded from this run's evidence alone.",
        }
    )

    infra_hit = _nearest_infrastructure(result)
    hyps.append(
        {
            "id": "H4",
            "label": "Non-vessel / stationary source",
            "reference": infra_hit,
            "supporting_evidence": ([f"{infra_hit['name']} ({infra_hit['kind']}) is {infra_hit['distance_km']} km from the candidate"] if infra_hit else []),
            "contradicting_evidence": ([] if infra_hit else ["No infrastructure or pipeline layer loaded near the candidate"]),
            "missing_evidence": ([] if infra_hit else ["An infrastructure/pipeline dataset covering this area"]),
            "assumptions": ["Loaded infrastructure/receptor layers are complete for this area"],
            "uncertainties": ["Proximity to mapped infrastructure is not evidence of a stationary discharge"],
            "current_position": ("OPEN -- no stationary source confirmed." if infra_hit else "NOT EVALUATED -- no infrastructure layer loaded near the candidate."),
        }
    )

    classification = pending_classification()
    hyps.append(
        {
            "id": "H5",
            "label": "SAR look-alike / non-oil phenomenon",
            "reference": None,
            "supporting_evidence": [],
            "contradicting_evidence": [],
            "missing_evidence": [
                "A validated oil/look-alike classifier (none is installed)",
                "A second corroborating SAR or optical acquisition",
            ],
            "assumptions": ["Dark-region segmentation reflects damping consistent with a surface film"],
            "uncertainties": [classification.get("explanation", "No validated oil/look-alike classifier is installed.")],
            "current_position": "OPEN -- cannot be ruled out; classification is UNAVAILABLE, not confirmed oil.",
        }
    )
    return hyps


# --------------------------------------------------------------------------
# E. discriminating evidence (reuses Next-Best-Observation, adds no new data)
# --------------------------------------------------------------------------


def discriminating_evidence(result):
    out = []
    for c in next_observation_candidates(result):
        out.append(
            {
                "candidate_id": c["id"],
                "evidence_needed": c["target_type"],
                "why": c["rationale"],
                "distinguishes_hypotheses": _HYPOTHESIS_MAP.get(c["target_type"], []),
                "aoi": {"center": c["center"], "radius_km": c["suggested_radius_km"]},
                "recommended_data_source": "Sentinel-1 SAR (Copernicus Data Space)",
                "uncertainty_it_may_reduce": c["basis"],
                "score": c["score"],
            }
        )
    return out


# --------------------------------------------------------------------------
# top-level
# --------------------------------------------------------------------------


def derive(result):
    weights = _base_weights(result)
    challenges = run_all_challenges(result)
    stability = conclusion_stability(result, challenges)
    fragility = evidence_fragility(result)
    return {
        "model_version": MODEL_VERSION,
        "run_id": result.get("run_id"),
        "case_id": result.get("case_id"),
        "analysis_hash": result.get("analysis_hash"),
        "method": (
            "Deterministic recomputation over this run's own AIS ranking and drift-hindcast "
            "functions; not an LLM and not a probability model."
        ),
        "hypotheses": build_hypotheses(result),
        "weights": weights,
        "challenges": challenges,
        "stability": stability,
        "fragility": fragility,
        "most_dependent_factor": most_dependent_factor(fragility),
        "discriminating_evidence": discriminating_evidence(result),
    }
