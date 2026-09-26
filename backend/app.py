"""OCEAN-EYE local API. OpenAPI documentation at /docs."""

import json
import shutil
import uuid
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .config import DATA, ROOT, VERSION
from . import storage, datasets, engine, ais, copernicus
from .drift import timestamp

pool = ThreadPoolExecutor(max_workers=2)


@asynccontextmanager
async def lifespan(app):
    storage.init()
    with storage.connect() as db:
        db.execute(
            "UPDATE runs SET state='FAILED',error='Server stopped before completion; rerun this case' WHERE state='RUNNING'"
        )
    copernicus.start_monitor()
    try:
        yield
    finally:
        await copernicus.stop_monitor()


app = FastAPI(title="OCEAN-EYE", version="1.0.0", lifespan=lifespan)
from .response_api import router as response_router
from .copernicus_api import router as copernicus_router
from .next_observation_api import router as next_observation_router

app.include_router(response_router)
app.include_router(copernicus_router)
app.include_router(next_observation_router)


@app.middleware("http")
async def local_guard(request: Request, call_next):
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin")
        if origin and origin not in (
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            "http://localhost:4173",
            "http://127.0.0.1:4173",
        ):
            return JSONResponse(
                {"detail": "Cross-origin writes are disabled"}, status_code=403
            )
    return await call_next(request)


@app.exception_handler(KeyError)
async def not_found(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=404)


@app.exception_handler(ValueError)
async def invalid(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=422)


@app.get("/api/v1/health")
def health():
    return {
        "status": "online",
        "version": VERSION,
        "mode": "local",
        "trained_oil_model": False,
        "database": "SQLite",
        "geospatial_engine": "Rasterio / PyProj / Shapely",
        "copernicus_configured": copernicus.credentials_configured(),
    }


@app.get("/api/v1/cases")
def cases():
    with storage.connect() as db:
        rows = db.execute("SELECT * FROM cases ORDER BY created DESC").fetchall()
    return [
        {
            **{k: r[k] for k in ["id", "name", "created", "latest_run"]},
            "source_type": json.loads(r["config"])["source_type"],
        }
        for r in rows
        if not json.loads(r["config"]).get("archived")
    ]


def insert_case(config):
    case_id = str(uuid.uuid4())
    now = storage.now()
    with storage.connect() as db:
        db.execute(
            "INSERT INTO cases(id,name,created,config) VALUES(?,?,?,?)",
            (case_id, config["name"], now, storage.canonical(config)),
        )
    storage.audit(case_id, "case_created", config)
    return storage.get_case(case_id)


@app.post("/api/v1/cases/demo")
def demo():
    # Reuse generated fixtures unless explicitly regenerated via scripts.
    path = DATA / "demo/case.json"
    config = json.loads(path.read_text()) if path.exists() else datasets.create_demo()
    receptors = json.loads((DATA / "geospatial/demo_receptors.geojson").read_text())
    storage.import_geography(
        receptors, "OCEAN-EYE demo receptors", "1", "SYNTHETIC", "protected_area"
    )
    return insert_case(
        {
            **config,
            "radiometry": "sigma0_db",
            "sensor": "Synthetic SAR / Sentinel-1 compatible",
            "polarizations": ["VV", "VH"],
        }
    )


class CaseMetadata(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    observation_time: str
    release_window: list[str] = Field(min_length=2, max_length=2)
    source_type: Literal["REAL", "SYNTHETIC"]
    radiometry: Literal["sigma0_db", "sigma0_linear"] = "sigma0_db"
    sensor: str = "Sentinel-1 SAR"
    windage: float = Field(default=0.03, ge=0, le=0.1)
    seed: int = 428


async def receive(upload: UploadFile, path: Path, max_bytes=150 * 1024 * 1024):
    size = 0
    with path.open("wb") as dst:
        while data := await upload.read(1024 * 1024):
            size += len(data)
            if size > max_bytes:
                dst.close()
                path.unlink(missing_ok=True)
                raise HTTPException(
                    413, "Upload exceeds 150 MB; subset or tile the data"
                )
            dst.write(data)
    if not size:
        path.unlink(missing_ok=True)
        raise ValueError("Uploaded file is empty")


@app.post("/api/v1/cases/import")
async def import_case(
    metadata: str = Form(...),
    satellite: UploadFile = File(...),
    ais_file: UploadFile = File(...),
    environment: UploadFile = File(...),
):
    parsed = CaseMetadata.model_validate_json(metadata)
    config = parsed.model_dump()
    obs = timestamp(config["observation_time"])
    start, end = map(timestamp, config["release_window"])
    if not start < end < obs:
        raise ValueError("Release window must be ordered and before observation")
    if Path(satellite.filename or "").suffix.lower() not in (".tif", ".tiff"):
        raise ValueError("Satellite input must be a calibrated georeferenced GeoTIFF")
    folder = DATA / "cases" / str(uuid.uuid4())
    folder.mkdir()
    await receive(satellite, folder / "satellite.tif")
    await receive(ais_file, folder / "ais.csv")
    await receive(environment, folder / "environment.json")
    from .drift import validate_environment

    env = json.loads((folder / "environment.json").read_text(encoding="utf-8-sig"))
    validate_environment(env, config["observation_time"], config["release_window"])
    ais.load_ais(folder / "ais.csv")
    if env.get("source_type") not in ("REAL", "SYNTHETIC"):
        raise ValueError("Environment must declare source_type")
    import rasterio

    try:
        raster = rasterio.open(folder / "satellite.tif")
    except rasterio.errors.RasterioIOError as exc:
        raise ValueError("Satellite file is not a readable GeoTIFF") from exc
    with raster as src:
        if not src.crs:
            raise ValueError("Satellite must contain a valid CRS")
        declared = src.tags().get("radiometry")
        if declared and declared != parsed.radiometry:
            raise ValueError("Declared radiometry conflicts with GeoTIFF metadata")
        if src.width * src.height > 25000000:
            raise ValueError(
                "Scene exceeds 25 million pixels; tile the scene before import"
            )
        satellite_source = (
            "SYNTHETIC"
            if src.tags().get("source_type") == "SYNTHETIC"
            else parsed.source_type
        )
    config["sources"] = {
        "satellite": satellite_source,
        "ais": parsed.source_type,
        "environment": env["source_type"],
    }
    if "SYNTHETIC" in config["sources"].values():
        config["source_type"] = "SYNTHETIC"
    config.update(
        {
            key: str((folder / file).relative_to(DATA))
            for key, file in [
                ("satellite", "satellite.tif"),
                ("ais", "ais.csv"),
                ("environment", "environment.json"),
            ]
        }
    )
    return insert_case(config)


@app.get("/api/v1/cases/{case_id}")
def case(case_id: str):
    return storage.get_case(case_id)


class RunOptions(BaseModel):
    windage: float | None = Field(default=None, ge=0, le=0.1)


@app.post("/api/v1/cases/{case_id}/run")
def start(case_id: str, options: RunOptions = RunOptions()):
    cfg = storage.get_case(case_id)
    run_id = str(uuid.uuid4())
    with storage.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        active = db.execute(
            "SELECT id FROM runs WHERE case_id=? AND state='RUNNING'", (case_id,)
        ).fetchone()
        if active:
            raise HTTPException(409, "Investigation already running")
        if options.windage is not None:
            cfg["config"]["windage"] = options.windage
            db.execute(
                "UPDATE cases SET config=? WHERE id=?",
                (storage.canonical(cfg["config"]), case_id),
            )
        db.execute(
            "INSERT INTO runs(id,case_id,created,state,stage) VALUES(?,?,?,?,?)",
            (run_id, case_id, storage.now(), "RUNNING", "Queued"),
        )
    storage.audit(
        case_id,
        "analysis_requested",
        {"run_id": run_id, "options": options.model_dump()},
    )
    pool.submit(engine.run, case_id, run_id)
    return {"run_id": run_id, "state": "RUNNING"}


@app.get("/api/v1/runs/{run_id}")
def run_status(run_id: str):
    r = storage.get_run(run_id)
    r.pop("result")
    return r


@app.get("/api/v1/cases/{case_id}/runs")
def runs(case_id: str):
    storage.get_case(case_id)
    with storage.connect() as db:
        return [
            dict(r)
            for r in db.execute(
                "SELECT id,created,state,stage,error FROM runs WHERE case_id=? ORDER BY created DESC",
                (case_id,),
            )
        ]


def result_for(case_id):
    case = storage.get_case(case_id)
    if not case["latest_run"]:
        raise HTTPException(409, "Run an investigation first")
    return storage.get_run(case["latest_run"])["result"]


@app.get("/api/v1/cases/{case_id}/analysis")
def analysis(case_id: str):
    return result_for(case_id)


@app.get("/api/v1/cases/{case_id}/audit")
def audit(case_id: str):
    storage.get_case(case_id)
    return storage.events(case_id)


@app.get("/api/v1/cases/{case_id}/{section}")
def section(case_id: str, section: str):
    if section not in (
        "spill",
        "origin",
        "vessels",
        "attribution",
        "forecast",
        "evidence",
        "timeline",
        "dark",
        "quality",
        "impact",
        "provenance",
    ):
        raise HTTPException(404, "Unknown result section")
    return result_for(case_id)[section]


class Counterfactual(BaseModel):
    exclude_mmsi: str = Field(pattern=r"^\d{9}$")


@app.post("/api/v1/cases/{case_id}/counterfactual")
def counterfactual(case_id: str, options: Counterfactual):
    result = result_for(case_id)
    if not any(v["mmsi"] == options.exclude_mmsi for v in result["vessels"]):
        raise ValueError("Vessel not in this investigation")
    response = ais.attribute(result["vessels"], options.exclude_mmsi)
    storage.audit(
        case_id,
        "counterfactual",
        {"run_id": result["run_id"], "excluded_mmsi": options.exclude_mmsi},
    )
    return response


@app.get("/data/{case_id}/{run_id}/{filename}")
def artifact(case_id: str, run_id: str, filename: str):
    run = storage.get_run(run_id)
    if run["case_id"] != case_id:
        raise HTTPException(404, "Run does not belong to case")
    allowed = {
        "satellite.png",
        "processed.png",
        "mask.png",
        "mask.tif",
        "spill.geojson",
        "origin_probability.geojson",
        "forecast.geojson",
        "report.pdf",
        "evidence.zip",
        "checksums.sha256",
        "analysis.json",
        "ais_candidates.csv",
    }
    if filename not in allowed:
        raise HTTPException(404, "Unknown artifact")
    path = DATA / "cases" / case_id / run_id / filename
    if not path.is_file():
        raise HTTPException(404, "Artifact not ready")
    return FileResponse(
        path,
        filename=(
            filename if filename.endswith((".pdf", ".zip", ".csv", ".tif")) else None
        ),
    )


@app.post("/api/v1/geography/import")
async def geography_import(
    file: UploadFile = File(...),
    source: str = Form(...),
    version: str = Form(...),
    kind: str = Form("country"),
    source_type: Literal["REAL", "SYNTHETIC"] = Form("REAL"),
):
    raw = await file.read(40 * 1024 * 1024 + 1)
    if len(raw) > 40 * 1024 * 1024:
        raise HTTPException(413, "GeoJSON exceeds 40 MB")
    n = storage.import_geography(json.loads(raw), source, version, source_type, kind)
    return {"imported": n}


@app.get("/api/v1/geography")
def geography():
    with storage.connect() as db:
        rows = db.execute("SELECT * FROM geography").fetchall()
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": json.loads(r["geometry"]),
                "properties": {
                    "id": r["id"],
                    "name": r["name"],
                    "kind": r["kind"],
                    "source": r["source"],
                    "source_type": r["source_type"],
                    "version": r["version"],
                    "status": r["status"],
                    **{
                        k: v
                        for k, v in json.loads(r["properties"]).items()
                        if k in ("CONTINENT", "REGION_UN")
                    },
                },
            }
            for r in rows
        ],
    }


@app.get("/api/v1/search")
def search(q: str, case_id: str | None = None):
    found = []
    with storage.connect() as db:
        rows = db.execute(
            "SELECT * FROM geography WHERE name LIKE ? OR kind LIKE ? OR properties LIKE ? LIMIT 30",
            ("%" + q + "%", "%" + q + "%", "%" + q + "%"),
        ).fetchall()
    from shapely.geometry import shape

    for r in rows:
        found.append(
            {
                "id": r["id"],
                "name": r["name"],
                "kind": r["kind"],
                "coordinates": list(
                    shape(json.loads(r["geometry"])).representative_point().coords
                )[0],
                "source_type": r["source_type"],
            }
        )
    if case_id:
        case = storage.get_case(case_id)
        if case["latest_run"]:
            for v in result_for(case_id)["vessels"]:
                if q.lower() in (v["name"] + " " + v["mmsi"] + " " + v["imo"]).lower():
                    found.append(
                        {
                            "id": v["mmsi"],
                            "name": v["name"],
                            "kind": "vessel",
                            "coordinates": v["position"],
                        }
                    )
    try:
        lat, lon = map(float, q.split(","))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            found.insert(
                0,
                {
                    "name": f"{lat}, {lon}",
                    "kind": "coordinates",
                    "coordinates": [lon, lat],
                },
            )
    except ValueError:
        pass
    return found


@app.get("/api/v1/datasets")
def dataset_inventory():
    return {
        "sources": [
            {
                "name": "MarineCadastre AIS",
                "url": "https://marinecadastre.gov/accessais/",
                "schema": "MMSI, BaseDateTime, LAT, LON, SOG, COG; version-aware aliases supported",
            },
            {
                "name": "Zenodo Sentinel-1 oil spill Part I",
                "url": "https://zenodo.org/records/8346860",
                "note": "Download and extract locally; georeferenced Sigma0 VV/VH images; masks require matching source transform",
            },
            {
                "name": "Natural Earth countries",
                "url": "https://www.naturalearthdata.com/",
                "note": "Public-domain cartographic basemap; not legal maritime boundaries",
            },
        ],
        "local": [
            {
                "folder": folder,
                "files": [p.name for p in (DATA / folder).glob("*") if p.is_file()],
            }
            for folder in ["satellite", "ais", "environment", "geospatial", "demo"]
        ],
    }


if (ROOT / "dist").exists():
    app.mount("/", StaticFiles(directory=ROOT / "dist", html=True), name="frontend")
