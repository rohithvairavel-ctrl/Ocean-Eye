# Architecture and capability matrix

## Connected local runtime

React/TypeScript + Leaflet -> FastAPI -> immutable run folder + SQLite -> SAR/AIS/drift processing -> evidence/report -> API-backed dashboard.

`POST /api/v1/cases/demo` creates a reproducible case. `POST /api/v1/cases/import` validates uploaded inputs. `POST /api/v1/cases/{id}/run` queues a thread worker and returns a run ID. `GET /api/v1/runs/{run_id}` reports actual stage/state. Completed per-section endpoints expose spill, origin, vessels, attribution, forecast, evidence, timeline, dark, quality, impact and provenance.

Original files are copied into the run before analysis. The analysis identity hashes input checksums, parameters, backend code hashes and model version. PDF and evidence ZIP are generated before a run becomes COMPLETE. A failed run does not replace the last successful analysis. Server restart marks interrupted jobs failed so they can be explicitly rerun.

## Installed functionality vs extension interfaces

| Capability                    | Current implementation                                                                                 |
| ----------------------------- | ------------------------------------------------------------------------------------------------------ |
| SAR ingestion                 | Calibrated georeferenced GeoTIFF, two declared radiometric conventions                                 |
| Preprocessing                 | Metadata preservation, invalid-pixel handling, median filtering                                        |
| Segmentation                  | Adaptive dark-region threshold, morphology and connected components                                    |
| Oil/look-alike classification | Contrast screening only; no trained or validated classifier installed                                  |
| Area/perimeter                | WGS84 geodesic geometry from raster mask                                                               |
| Oil type/volume               | Unknown, not fabricated                                                                                |
| Spill age                     | Conditional on supplied release window                                                                 |
| Hindcast/forecast             | 500 seeded Lagrangian particles, temporal forcing interpolation, windage and diffusion                 |
| Drift uncertainty             | Conditional 90% Gaussian ellipse and radial quantile; excludes structural model error                  |
| AIS                           | CSV schema normalization, validation, reconstruction, speed/course/gap features                        |
| Historical routes             | Within-case baseline only; long-term learned route model not installed                                 |
| Attribution                   | Transparent weighted investigative score, no calibrated guilt probability                              |
| Counterfactual                | Exclude candidate and rerank preserved evidence; change windage and rerun                              |
| Dark vessel                   | Compact bright-return screening and nearest AIS comparison; unresolved probability                     |
| Multi-hypothesis              | Explicit vessel, untracked, infrastructure and look-alike hypotheses; unresolved alternatives retained |
| Geographic intelligence       | Offline countries, marine regions, ports, coastline; import schema for additional layers               |
| EEZ/protected areas/fisheries | User-importable; no authoritative global EEZ/protected/fisheries dataset bundled                       |
| Impact                        | Nearby loaded receptor intersections with forecast envelopes; no toxicity/mass model                   |
| Evidence/timeline             | Derived supporting/contradicting features and observed/assumed event types                             |
| Provenance/custody            | Input/code checksums, immutable run directories, linked audit hashes, PDF/ZIP                          |
| Database                      | SQLite local runtime; optional PostGIS schema provided but not wired into this runtime                 |
| Jobs                          | Local thread executor; restart recovery. Durable distributed queue is a production extension           |
| User access                   | Local single-user; roles/SSO/multi-tenant authorization not implemented                                |
| Deployment                    | Tested local VS Code workflow; Docker files provided, Docker not verified here                         |
| ML training/evaluation        | No learned weights or real-data training run included; synthetic benchmark is not field validation     |

## Method details

Drift velocity is current + windage × wind. Uncertain velocity perturbations remain correlated through a particle's trajectory. Independent diffusion is added each quarter-hour. Release time is sampled within the supplied window. Forward horizons are 6, 12, 24 and 48 hours. There is no claim that time reversal perfectly reconstructs weathered oil.

Attribution weights sum to one: origin proximity 0.35, temporal coverage 0.10, mean trajectory overlap 0.25, speed reduction 0.12, course change 0.08, and release-window AIS gap 0.10. Proximity decays with the modeled uncertainty radius. These are explicit heuristic weights, not fitted likelihoods. Nationality/name do not influence the score.

SAR vessel screening identifies small bright connected components. A spatial match requires less than 2 km separation and at most 15 minutes between AIS and acquisition. This baseline cannot distinguish every structure, speckle return, ship and wake; unmatched returns remain unconfirmed.

## Production integration points

Replace the SAR screening entry point in `backend/satellite.py` with a validated segmentation/look-alike model that returns the same geometry/metadata contract. Replace the uniform forcing adapter in `backend/drift.py` with a gridded space/time interpolator and physics modules. Keep raw artifacts and algorithm versions in every result. Move the storage interface to PostGIS and add durable workers, authentication, signed evidence storage and independent benchmark validation before network/operational deployment.

## Environmental response intelligence (implemented)

- `backend/classification.py`: typed classifier adapter contract; unavailable status prevents fabricated prediction/confidence/metrics. No trained classifier is installed.
- `backend/intelligence.py`: deterministic event rules, recommendation traces, ecological source/version metadata, typed reasoning nodes and vessel dossier contract. Notification adapter interface only; no external sending.
- `backend/response.py`: run-bound geodesic timing planner. Kind distinguishes operational intent; containment/removal physics is deliberately absent for every intervention kind.
- `backend/response_api.py`: saved-case inventory, intelligence/dossiers, immutable SQLite scenarios, SHA-256 export and supplemental evidence ZIP. Derived views of old runs identify their current derivation code hashes and preserve original run artifacts.
- Ecology supports imported coral, mangrove, seagrass, habitats, species ranges, protected areas, fisheries, ports and coastline. Importing a dataset is not validation of its authority or species presence. Re-run after importing layers to include them in the immutable receptor snapshot.
- Scenarios are referenced to satellite observation time, not the live clock. Arrival windows reflect operator speed bounds; they are not calibrated confidence intervals. Forecast samples at 6/12/24/48 h cannot establish exact exposure onset.
- Existing PDFs remain the original analysis snapshot. Later scenarios are supplied as separately hashed JSON and a supplemental ZIP; they do not rewrite that report.
- Known legacy integration fixtures are archived from the operational case list without deleting their case, runs or audit history.
