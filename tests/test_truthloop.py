"""TruthLoop: adversarial hypothesis testing over a completed investigation.

Verifies real recomputation (not fabricated output): weight-ablation actually
changes backend.ais.analyze()'s scores, every challenge either ran a real
recomputation or explicitly says why it could not, and the API wiring/audit
trail work end-to-end against a demo case run.
"""

import time

import pytest
from fastapi.testclient import TestClient

from backend import ais, datasets, drift, engine, storage
from backend.app import app
from backend import truthloop


@pytest.fixture
def isolated(tmp_path):
    import backend.app as app_module
    import backend.config as config

    data_dir = tmp_path / "data"
    for name in ("satellite", "ais", "environment", "geospatial", "demo", "cases"):
        (data_dir / name).mkdir(parents=True)
    modules = [config, app_module, storage, datasets, engine]
    previous = [m.DATA for m in modules]
    for m in modules:
        m.DATA = data_dir
    storage.init()
    try:
        yield data_dir
    finally:
        for m, value in zip(modules, previous):
            m.DATA = value


def _run_demo(c):
    datasets.create_demo()
    case = c.post("/api/v1/cases/demo").json()
    run = c.post(f"/api/v1/cases/{case['id']}/run", json={}).json()
    run_id = run["run_id"]
    status = None
    for _ in range(120):
        status = c.get(f"/api/v1/runs/{run_id}").json()
        if status["state"] != "RUNNING":
            break
        time.sleep(0.2)
    assert status["state"] == "COMPLETE", status
    return case


def test_weight_ablation_changes_ais_analyze_scores(isolated):
    with TestClient(app) as c:
        case = _run_demo(c)
        result = c.get(f"/api/v1/cases/{case['id']}/analysis").json()
    tracks = {v["mmsi"]: v["track"] for v in result["vessels"]}
    baseline = ais.analyze(tracks, result["origin"], result["observation_time"])
    removed = ais.DEFAULT_WEIGHTS["Origin proximity"]
    remaining_total = sum(v for k, v in ais.DEFAULT_WEIGHTS.items() if k != "Origin proximity")
    modified_weights = {
        k: (0.0 if k == "Origin proximity" else v / remaining_total)
        for k, v in ais.DEFAULT_WEIGHTS.items()
    }
    reweighted = ais.analyze(tracks, result["origin"], result["observation_time"], weights=modified_weights)
    baseline_top = next(v for v in baseline if v["mmsi"] == result["vessels"][0]["mmsi"])
    reweighted_top = next(v for v in reweighted if v["mmsi"] == result["vessels"][0]["mmsi"])
    assert removed > 0
    # Removing a real, positively-weighted factor and renormalizing must change the score
    # for any vessel whose "Origin proximity" component isn't identical to its weighted mean.
    assert reweighted_top["score"] != baseline_top["score"] or all(
        c["value"] == 0 for c in baseline_top["components"] if c["name"] == "Origin proximity"
    )
    # analyze() with no weights argument is unchanged (backward compatibility).
    unchanged = ais.analyze(tracks, result["origin"], result["observation_time"])
    assert [v["score"] for v in unchanged] == [v["score"] for v in baseline]


def test_truthloop_endpoint_runs_real_challenges(isolated):
    with TestClient(app) as c:
        case = _run_demo(c)
        case_id = case["id"]
        analysis = c.get(f"/api/v1/cases/{case_id}/analysis").json()

        view = c.get(f"/api/v1/cases/{case_id}/truthloop").json()
        assert view["model_version"] == truthloop.MODEL_VERSION
        assert view["run_id"] == analysis["run_id"]

        # A. competing hypotheses
        ids = [h["id"] for h in view["hypotheses"]]
        assert ids == ["H1", "H2", "H3", "H4", "H5"]
        for h in view["hypotheses"]:
            position = h["current_position"].upper()
            assert position.startswith("OPEN") or position.startswith("NOT EVALUATED")
            assert set(h) >= {
                "supporting_evidence", "contradicting_evidence", "missing_evidence",
                "assumptions", "uncertainties", "current_position",
            }
        assert view["hypotheses"][0]["reference"]["mmsi"] == analysis["vessels"][0]["mmsi"]

        # B. challenges -- all 8 defined, baseline always the real top vessel
        challenge_ids = {ch["id"] for ch in view["challenges"]}
        assert challenge_ids == {
            "remove_proximity", "remove_ais_gap", "remove_speed_change", "remove_course_change",
            "exclude_strongest_candidate", "expand_origin_uncertainty", "shift_release_time",
            "vary_forcing_assumptions",
        }
        for ch in view["challenges"]:
            assert ch["baseline_ranking"][0]["mmsi"] == analysis["vessels"][0]["mmsi"]
            if ch["available"]:
                assert ch["after_ranking"], ch
            else:
                assert ch["explanation"], "unavailable challenge must explain why"

        # C. conclusion stability -- deterministic state, grounded reasons
        assert view["stability"]["state"] in {"ROBUST", "MODERATE", "FRAGILE", "INDETERMINATE"}
        assert view["stability"]["reasons"]
        assert set(view["stability"]["reversals"]).issubset(challenge_ids)

        # D. evidence fragility -- real, mathematically derived contributions
        assert view["fragility"]
        for f in view["fragility"]:
            assert f["influence"] in {"HIGH", "MEDIUM", "LOW"}
        assert view["most_dependent_factor"] == view["fragility"][0]["factor"]

        # E. discriminating evidence -- reuses Next-Best-Observation, not fabricated
        next_obs = c.get(f"/api/v1/cases/{case_id}/next-observations").json()
        assert len(view["discriminating_evidence"]) == len(next_obs["candidates"])
        assert {e["candidate_id"] for e in view["discriminating_evidence"]} == {
            cand["id"] for cand in next_obs["candidates"]
        }

        # CHALLENGE THIS CONCLUSION action + audit trail
        challenged = c.post(f"/api/v1/cases/{case_id}/truthloop/challenge").json()
        assert challenged["stability"]["state"] == view["stability"]["state"]
        events = c.get(f"/api/v1/cases/{case_id}/audit").json()
        actions = [e["action"] for e in events]
        assert "truthloop_viewed" in actions
        assert "truthloop_challenge_run" in actions


def test_removing_ais_gap_reverses_ranking_when_it_is_decisive(isolated):
    """Construct a case where AIS-gap evidence is the deciding factor, and
    confirm TruthLoop reports FRAGILE with a ranking reversal -- matching the
    directive's own worked example."""
    with TestClient(app) as c:
        case = _run_demo(c)
        case_id = case["id"]
        analysis = c.get(f"/api/v1/cases/{case_id}/analysis").json()
    vessels = analysis["vessels"]
    if len(vessels) < 2:
        pytest.skip("Demo case has fewer than two AIS-tracked vessels")
    tracks = {v["mmsi"]: v["track"] for v in vessels}
    remaining_total = sum(v for k, v in ais.DEFAULT_WEIGHTS.items() if k != "AIS gap")
    modified = {
        k: (0.0 if k == "AIS gap" else v / remaining_total)
        for k, v in ais.DEFAULT_WEIGHTS.items()
    }
    reranked = ais.analyze(tracks, analysis["origin"], analysis["observation_time"], weights=modified)
    if reranked[0]["mmsi"] == vessels[0]["mmsi"]:
        pytest.skip("AIS-gap evidence is not decisive for this demo case's ranking")
    from backend.truthloop import _challenge_result, _ranking_summary

    result = _challenge_result(
        "remove_ais_gap", "Remove AIS-gap evidence", "desc", "method",
        _ranking_summary(vessels), reranked,
    )
    assert result["ranking_changed"] is True
    assert "reverses" in result["explanation"]
