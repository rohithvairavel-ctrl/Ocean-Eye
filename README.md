# OCEAN-EYE

A working local maritime forensics application with a React dashboard, Python analysis API, reproducible SAR/AIS/environment fixtures, global geographic search, and auditable PDF/ZIP exports.

## Open and present in VS Code

1. Open **OCEAN-EYE.code-workspace** in VS Code (or File > Open Folder and choose this folder).
2. Choose **Terminal > Run Build Task** (`Ctrl+Shift+B`) and select **OCEAN-EYE: Start app**. The default task starts both services.
3. Open **http://127.0.0.1:5173** in your browser.
4. Open **Cases & Data**, choose **New demo case**, then **Run full investigation**.
5. Follow [the presentation guide](docs/DEMO_GUIDE.md). All results are computed from input files.
6. Stop both services with `Ctrl+C` in the launch terminal.

Alternatively, run this in the VS Code PowerShell terminal:

```powershell
.\.venv\Scripts\python.exe scripts/launch.py
```

Dependencies and demo data are already installed/generated on this computer. No API keys, cloud account, GPU, internet connection, or map tile service is needed to present the included demo.

### On another computer

Install **Python 3.12** and **Node.js 22 or newer**, then run:

```powershell
.\setup.ps1
.\start.ps1
```

If PowerShell does not permit scripts, use the individual commands below instead of changing machine policy:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm.cmd ci
.\.venv\Scripts\python.exe scripts/create_demo_case.py
.\.venv\Scripts\python.exe scripts/load_local_geography.py
.\.venv\Scripts\python.exe scripts/launch.py
```

Do not copy the `.venv` or `node_modules` directories to another computer; recreate them there. Keep the downloaded `datasets/geospatial` files for offline maps.

### Presentation build

```powershell
npm.cmd run build
.\.venv\Scripts\python.exe scripts/launch.py --production
```

Open **http://127.0.0.1:8000**. This serves the built dashboard and API together, with no frontend development server. `start.ps1 -Production` is equivalent. This is the easiest mode for a stable presentation after building.

## What works

- Georeferenced, two-band synthetic SAR scene with an approximately 42.6 km² slick; reproducible 20-vessel AIS dataset; time-indexed forcing.
- Calibrated Sigma0 GeoTIFF input, median filtering, dark-region segmentation, multiple connected components, geodesic area/perimeter, georeferenced mask export.
- 500-particle hindcast and 6/12/24/48-hour forecast, seeded uncertainty, configurable windage.
- MarineCadastre CSV normalization, identity/time/coordinate validation, duplicate removal, impossible-jump rejection, track reconstruction.
- Speed and course anomalies, AIS reporting gaps, uncertainty corridors, transparent weighted vessel screening.
- Candidate removal and reranking, supporting/contradicting evidence, alternative hypotheses, bright-return SAR/AIS screening.
- Global local basemap/search, maritime-region containment, nearby receptor overlap and source metadata; importable geographic layers.
- Saved cases and immutable run folders, input snapshots, algorithm hashes, hash-linked audit events, PDF report and verified evidence ZIP.
- Responsive dark command-center layout, map layers, map time toggle, vessel inspection, timeline, evidence view, presentation mode.

## Scientific scope

This is a **functional demonstration and experimental screening prototype**, not a validated operational oil-attribution system. The installed SAR detector is a transparent image-processing baseline, not a trained neural model. Scores are calculated investigative priorities, **not probabilities of responsibility**. All synthetic investigations are visibly labelled.

The release window is an analyst-supplied assumption. Oil type, volume, and calibrated oil/dark-vessel probabilities remain unknown. The uniform environmental adapter does not model spatial current gradients, beaching, evaporation or weathering. Future trained-model, reanalysis, and enterprise adapters are documented in [CAPABILITIES.md](docs/CAPABILITIES.md); unavailable capabilities are not disguised as working models.

## Data and sources

See [DATASETS.md](docs/DATASETS.md) for import formats and source URLs.

- AIS: MarineCadastre historical CSV schema, with newer lowercase/latitude/longitude aliases.
- SAR: Zenodo Sentinel-1 Part I/II/III compatibility for **extracted, calibrated, georeferenced images**. The 40+ GB archive is not bundled or downloaded automatically.
- Basemap: Natural Earth public-domain country, marine-region, port and coastline features, downloaded and stored locally with source checksums.
- No real incident imagery or matching real vessel AIS has been acquired. The provided full investigation uses synthetic inputs.

## Development

```powershell
# Backend only
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
# Frontend only, in a second terminal
npm.cmd run dev
# Verify the pipeline (isolated temporary database)
.\.venv\Scripts\python.exe -m pytest -q
# Build and type-check
npm.cmd run build
# Validate demo inputs
.\.venv\Scripts\python.exe scripts/validate_datasets.py
```

API documentation: **http://127.0.0.1:8000/docs**.

## Project layout

```text
frontend/                 React + TypeScript UI and Leaflet map
backend/                  FastAPI, raster processing, drift, AIS, evidence, persistence
scripts/                  Data generation/import/validation and local launchers
tests/                    API and scientific-invariant integration tests
datasets/satellite/       GeoTIFF and metadata
datasets/ais/             MarineCadastre-compatible CSV
datasets/environment/     Forcing JSON
datasets/geospatial/      Offline Natural Earth and imported references
datasets/demo/            Scenario and segmentation truth (benchmark only)
datasets/cases/           Immutable run artifacts and reports
docs/                     Presentation, data, architecture and capability guides
.vscode/                  Tasks and debugging configurations
```

## Docker

`docker compose up --build` serves the built app at **http://127.0.0.1:8000**. It uses a persistent named data volume. Docker configuration is supplied; the local Windows workflow is the tested deployment. The optional PostGIS schema is in `docker/postgis.sql`; the active local database is SQLite, not PostGIS.

## Local security and custody

Servers bind to loopback. Cross-origin writes are rejected. Uploaded filenames cannot choose artifact paths. No case data is transmitted to a third-party service. Audit hashes make accidental modification detectable; they are not digital signatures or tamper-proof storage. For network deployment, add authentication, authorization, signed evidence storage, durable worker infrastructure and independent scientific validation.
