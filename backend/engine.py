"""Connected investigation orchestration. Each stage records real completion."""

import csv
import hashlib
import json
import shutil
import traceback
from pathlib import Path
from . import storage, satellite, ais, drift
from .config import DATA, VERSION, ROOT
from .geo import circle
from shapely.geometry import shape
from .reports import make_report, bundle

STAGES = [
    "Satellite preprocessing & segmentation",
    "Environmental validation",
    "Monte Carlo hindcast & forecast",
    "AIS cleaning & reconstruction",
    "Behaviour & gap analysis",
    "Attribution & counterfactuals",
    "Impact & evidence graph",
    "Forensic report & checksums",
]


def run(case_id, run_id):
    case = storage.get_case(case_id)
    cfg = case["config"]
    out = DATA / "cases" / case_id / run_id
    out.mkdir(parents=True, exist_ok=True)

    def stage(index):
        with storage.connect() as db:
            db.execute("UPDATE runs SET stage=? WHERE id=?", (STAGES[index], run_id))
        storage.audit(
            case_id, "stage_started", {"run_id": run_id, "stage": STAGES[index]}
        )

    def save(name, value):
        (out / name).write_text(
            json.dumps(value, indent=2, allow_nan=False), encoding="utf-8"
        )

    try:
        inputs = []
        for role in ["satellite", "ais", "environment"]:
            source = DATA / cfg[role]
            target = out / (f"input_{role}" + source.suffix)
            shutil.copyfile(source, target)
            inputs.append(
                {
                    "role": role,
                    "name": source.name,
                    "sha256": storage.digest(target),
                    "source_type": cfg.get("sources", {}).get(role, cfg["source_type"]),
                    "artifact": target.name,
                }
            )
        save("case_config.json", cfg)
        inputs.append(
            {
                "role": "configuration",
                "name": "case_config.json",
                "sha256": storage.digest(out / "case_config.json"),
                "source_type": cfg["source_type"],
                "artifact": "case_config.json",
            }
        )
        stage(0)
        spill = satellite.prepare(out / inputs[0]["artifact"], out, cfg)
        if not spill["geometry"]:
            raise ValueError(
                "No dark-region candidates detected. Review the image and preprocessing; no attribution was computed."
            )
        stage(1)
        env = json.loads((out / inputs[2]["artifact"]).read_text(encoding="utf-8-sig"))
        stage(2)
        origin, forecast = drift.simulate(
            spill,
            env,
            cfg["observation_time"],
            cfg["release_window"],
            seed=cfg.get("seed", 428),
            windage=cfg.get("windage", 0.03),
        )
        stage(3)
        tracks, quality = ais.load_ais(out / inputs[1]["artifact"])
        stage(4)
        vessels = ais.analyze(tracks, origin, cfg["observation_time"])
        stage(5)
        attribution = ais.attribute(vessels)
        stage(6)
        receptors = {"type": "FeatureCollection", "features": []}
        nearby = shape(circle(spill["centroid"], 250))
        context = []
        with storage.connect() as db:
            for row in db.execute(
                "SELECT * FROM geography WHERE kind IN ('protected_area','fishery','port','infrastructure','coastline','coral','mangrove','seagrass','habitat','species_range','sensitive_ecosystem','fisheries')"
            ):
                if cfg["source_type"] == "REAL" and row["source_type"] == "SYNTHETIC":
                    continue
                if not shape(json.loads(row["geometry"])).intersects(nearby):
                    continue
                receptors["features"].append(
                    {
                        "type": "Feature",
                        "geometry": json.loads(row["geometry"]),
                        "properties": {
                            "name": row["name"],
                            "kind": row["kind"],
                            "source_type": row["source_type"],
                            "source": row["source"],
                            "version": row["version"],
                        },
                    }
                )
        save("receptors.geojson", receptors)
        inputs.append(
            {
                "role": "receptors",
                "name": "receptors.geojson",
                "sha256": storage.digest(out / "receptors.geojson"),
                "source_type": (
                    "SYNTHETIC"
                    if any(
                        f["properties"]["source_type"] == "SYNTHETIC"
                        for f in receptors["features"]
                    )
                    else "REAL"
                ),
                "artifact": "receptors.geojson",
            }
        )
        impact = drift.impact(forecast, receptors)
        from shapely.geometry import Point

        with storage.connect() as db:
            for row in db.execute(
                "SELECT * FROM geography WHERE kind IN ('country','sea','ocean','eez')"
            ):
                if shape(json.loads(row["geometry"])).covers(Point(*spill["centroid"])):
                    context.append(
                        {
                            "name": row["name"],
                            "kind": row["kind"],
                            "source": row["source"],
                            "version": row["version"],
                            "status": row["status"],
                        }
                    )
        save("geographic_context.json", context)
        inputs.append(
            {
                "role": "geographic_context",
                "name": "geographic_context.json",
                "sha256": storage.digest(out / "geographic_context.json"),
                "source_type": "REAL",
                "artifact": "geographic_context.json",
            }
        )
        dark_matches = []
        for i, d in enumerate(spill["sar_detections"]):
            near = min(
                vessels, key=lambda v: drift.distance(d["coordinates"], v["position"])
            )
            km = drift.distance(d["coordinates"], near["position"])
            observation_offset = (
                min(
                    abs(
                        (
                            drift.timestamp(p["time"])
                            - drift.timestamp(cfg["observation_time"])
                        ).total_seconds()
                    )
                    for p in near["track"]
                )
                / 60
            )
            dark_matches.append(
                {
                    "id": f"sar-{i}",
                    "coordinates": d["coordinates"],
                    "nearest_mmsi": near["mmsi"],
                    "distance_km": round(km, 2),
                    "ais_time_offset_min": round(observation_offset, 1),
                    "matched": km < 2 and observation_offset <= 15,
                }
            )
        dark = {
            "sar_returns": dark_matches,
            "unmatched_returns": sum(not r["matched"] for r in dark_matches),
            "gap_count": sum(len(v["gaps"]) for v in vessels),
            "probability": None,
            "status": "UNRESOLVED",
            "limitations": "Bright-return detector is a screening baseline, not validated vessel detection. Unmatched returns and AIS gaps do not establish a dark vessel.",
        }
        nodes = [
            {"id": "satellite", "label": "SAR observation", "kind": "observation"},
            {
                "id": "spill",
                "label": f"{spill['area_km2']:.1f} km² candidate",
                "kind": "measurement",
            },
            {"id": "origin", "label": "Modeled origin", "kind": "model"},
            {"id": "forecast", "label": "48 h forecast", "kind": "model"},
        ]
        edges = [
            {
                "source": "satellite",
                "target": "spill",
                "label": "dark-region segmentation",
            },
            {
                "source": "spill",
                "target": "origin",
                "label": "hindcast under assumed release window",
            },
            {"source": "spill", "target": "forecast", "label": "ensemble advection"},
        ]
        for v in vessels[:6]:
            nodes.append(
                {
                    "id": v["mmsi"],
                    "label": v["name"],
                    "kind": "vessel",
                    "score": v["score"],
                }
            )
            edges.append(
                {
                    "source": "origin",
                    "target": v["mmsi"],
                    "label": f"{v['nearest_km']} km | score {v['score']}/100",
                }
            )
        evidence = {
            "nodes": nodes,
            "edges": edges,
            "supporting": vessels[0]["supporting"],
            "contradicting": vessels[0]["contradicting"],
        }
        timeline = [
            {
                "time": cfg["release_window"][0],
                "event": "Assumed release window opens",
                "type": "assumption",
            },
            {
                "time": cfg["release_window"][1],
                "event": "Assumed release window closes",
                "type": "assumption",
            },
            {
                "time": cfg["observation_time"],
                "event": "SAR observation acquired",
                "type": "observation",
            },
        ]
        for gap in vessels[0]["gaps"]:
            timeline.append(
                {
                    "time": gap["start"],
                    "event": f"{vessels[0]['name']}: AIS gap ({gap['minutes']:.0f} min)",
                    "type": "gap",
                }
            )
        timeline.sort(key=lambda x: drift.timestamp(x["time"]))
        limitations = spill["limitations"] + [
            "Release time and spill age are conditional on an analyst-supplied window.",
            "Uniform forcing adapter lacks spatial gradients, beaching, evaporation and weathering.",
            "Ensemble spread does not include every model error; no calibrated attribution probability.",
            "AIS gaps can reflect receiver coverage; sparse tracks limit interpolation.",
            "Exposure depends on imported receptor coverage. Global basemap is not a legal EEZ layer.",
            "Historical route learning and trained SAR vessel/oil classifiers are extension interfaces, not installed models.",
        ]
        code_hashes = {
            p.name: storage.digest(p) for p in sorted((ROOT / "backend").glob("*.py"))
        }
        save("algorithm_manifest.json", code_hashes)
        config_hash = hashlib.sha256(
            storage.canonical(
                {
                    "inputs": inputs,
                    "version": VERSION,
                    "code_hashes": code_hashes,
                    "parameters": origin["parameters"],
                }
            ).encode()
        ).hexdigest()
        result = {
            "case_id": case_id,
            "run_id": run_id,
            "name": case["name"],
            "observation_time": cfg["observation_time"],
            "created": storage.now(),
            "source_type": cfg["source_type"],
            "data_label": (
                "DEMO DATA - SYNTHETIC INVESTIGATION"
                if any(i["source_type"] == "SYNTHETIC" for i in inputs)
                else "REAL INPUTS - EXPERIMENTAL SCREENING"
            ),
            "spill": spill,
            "origin": origin,
            "vessels": vessels,
            "attribution": attribution,
            "forecast": forecast,
            "impact": impact,
            "evidence": evidence,
            "timeline": timeline,
            "dark": dark,
            "quality": quality,
            "environment": env,
            "receptors": receptors,
            "geographic_context": context,
            "provenance": {
                "model_version": VERSION,
                "code_hashes": code_hashes,
                "inputs": inputs,
                "parameters": origin["parameters"],
                "forecast_parameters": forecast["parameters"],
            },
            "analysis_hash": config_hash,
            "limitations": limitations,
            "recommendations": [
                "Obtain acquisition-matched wind and current fields to constrain drift bias.",
                "Confirm oil using a second observation or field evidence and a validated look-alike model.",
                "Obtain wider AIS receiver coverage and independent vessel observations for the release window.",
            ],
            "assets": f"/data/{case_id}/{run_id}",
        }
        from .intelligence import derive

        result["intelligence"] = derive(result)
        save("intelligence.json", result["intelligence"])
        save("analysis.json", result)
        save(
            "satellite_metadata.json",
            {
                k: spill[k]
                for k in ["sensor", "crs", "resolution", "polarizations", "radiometry"]
            },
        )
        save(
            "origin_probability.geojson",
            {
                "type": "Feature",
                "geometry": origin["geometry"],
                "properties": {"conditional_coverage": 0.9},
            },
        )
        save(
            "forecast.geojson",
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": s["geometry"],
                        "properties": {"hours": s["hours"]},
                    }
                    for s in forecast["steps"]
                ],
            },
        )
        save(
            "drift_tracks.geojson",
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [spill["centroid"], p],
                        },
                        "properties": {
                            "kind": "endpoint visualization, not resolved particle path"
                        },
                    }
                    for p in origin["particles"]
                ],
            },
        )
        save("evidence_graph.json", evidence)
        save("timeline.json", timeline)
        with (out / "ais_candidates.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=["mmsi", "name", "score", "nearest_km"]
            )
            writer.writeheader()
            writer.writerows([{k: v[k] for k in writer.fieldnames} for v in vessels])
        stage(7)
        make_report(result, out)
        storage.audit(
            case_id,
            "analysis_completed",
            {
                "run_id": run_id,
                "analysis_hash": config_hash,
                "report_sha256": storage.digest(out / "report.pdf"),
            },
        )
        save("audit.json", storage.events(case_id))
        bundle(out)
        with storage.connect() as db:
            db.execute(
                "UPDATE runs SET state='COMPLETE',stage='Investigation complete',result=? WHERE id=?",
                (storage.canonical(result), run_id),
            )
            db.execute("UPDATE cases SET latest_run=? WHERE id=?", (run_id, case_id))
    except Exception as exc:
        traceback.print_exc()
        with storage.connect() as db:
            db.execute(
                "UPDATE runs SET state='FAILED',error=? WHERE id=?", (str(exc), run_id)
            )
        storage.audit(case_id, "analysis_failed", {"run_id": run_id, "error": str(exc)})
