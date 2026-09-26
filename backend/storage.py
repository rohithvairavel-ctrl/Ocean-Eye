"""SQLite local persistence with immutable per-run artifacts and hash-linked audit events."""

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from .config import DATA


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def connect():
    db = sqlite3.connect(DATA / "ocean_eye.sqlite", timeout=30)
    db.row_factory = sqlite3.Row
    return db


def init():
    with connect() as db:
        db.executescript(
            """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS cases(id TEXT PRIMARY KEY, name TEXT NOT NULL, created TEXT NOT NULL, config TEXT NOT NULL, latest_run TEXT);
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, case_id TEXT NOT NULL, created TEXT NOT NULL, state TEXT NOT NULL, stage TEXT, result TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL, time TEXT NOT NULL, action TEXT NOT NULL, details TEXT NOT NULL, previous_hash TEXT, hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS geography(id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, source TEXT NOT NULL, version TEXT NOT NULL, source_type TEXT NOT NULL, jurisdiction TEXT, status TEXT, geometry TEXT NOT NULL, properties TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS scenarios(id TEXT PRIMARY KEY, case_id TEXT NOT NULL, run_id TEXT NOT NULL, created TEXT NOT NULL, payload TEXT NOT NULL, hash TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS geography_name ON geography(name COLLATE NOCASE);
        """
        )

    # Archive only the known legacy integration fixture; preserve all runs and evidence.
    archived = []
    with connect() as db:
        for row in db.execute(
            "SELECT id,config FROM cases WHERE name='Upload integration test'"
        ).fetchall():
            cfg = json.loads(row["config"])
            if not cfg.get("archived"):
                cfg["archived"] = True
                cfg["archive_reason"] = (
                    "Legacy integration fixture hidden from operational inventory"
                )
                db.execute(
                    "UPDATE cases SET config=? WHERE id=?", (canonical(cfg), row["id"])
                )
                archived.append(row["id"])
    for case_id in archived:
        audit(
            case_id,
            "legacy_fixture_archived",
            {"reason": "Operational inventory cleanup; evidence retained"},
        )


def audit(case_id, action, details):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        prev = db.execute(
            "SELECT hash FROM audit WHERE case_id=? ORDER BY seq DESC LIMIT 1",
            (case_id,),
        ).fetchone()
        prev = prev[0] if prev else ""
        t = now()
        payload = canonical(details)
        h = hashlib.sha256((prev + t + action + payload).encode()).hexdigest()
        db.execute(
            "INSERT INTO audit(case_id,time,action,details,previous_hash,hash) VALUES(?,?,?,?,?,?)",
            (case_id, t, action, payload, prev, h),
        )
    return h


def get_case(case_id):
    with connect() as db:
        row = db.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
    if not row:
        raise KeyError("Case not found")
    value = dict(row)
    value["config"] = json.loads(value["config"])
    return value


def get_run(run_id):
    with connect() as db:
        row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        raise KeyError("Run not found")
    value = dict(row)
    value["result"] = json.loads(value["result"]) if value["result"] else None
    return value


def events(case_id):
    with connect() as db:
        return [
            dict(row)
            for row in db.execute(
                "SELECT * FROM audit WHERE case_id=? ORDER BY seq", (case_id,)
            )
        ]


def import_geography(
    collection, source, version, source_type="REAL", default_kind="country"
):
    from shapely.geometry import shape, mapping
    from shapely import make_valid

    if collection.get("type") != "FeatureCollection":
        raise ValueError("Expected GeoJSON FeatureCollection in WGS84")
    prepared = []
    for i, f in enumerate(collection.get("features", [])):
        geom = shape(f["geometry"])
        repaired = not geom.is_valid
        if repaired:
            geom = make_valid(geom)
        if geom.is_empty:
            raise ValueError(f"Empty feature geometry at index {i}")
        x1, y1, x2, y2 = geom.bounds
        if not (-180 <= x1 <= x2 <= 180 and -90 <= y1 <= y2 <= 90):
            raise ValueError("GeoJSON coordinates must be WGS84 longitude/latitude")
        p = dict(f.get("properties") or {})
        p["geometry_repaired"] = repaired
        name = p.get("name") or p.get("ADMIN") or p.get("NAME") or f"Feature {i}"
        identifier = hashlib.sha256(
            f"{source}:{version}:{i}:{name}".encode()
        ).hexdigest()[:24]
        prepared.append(
            (
                identifier,
                name,
                p.get("kind", default_kind),
                source,
                version,
                p.get("source_type", source_type),
                p.get("jurisdiction") or p.get("SOVEREIGNT"),
                p.get("boundary_status", "APPROXIMATE"),
                canonical(mapping(geom)),
                canonical(p),
            )
        )
    with connect() as db:
        db.executemany(
            "INSERT OR REPLACE INTO geography VALUES(?,?,?,?,?,?,?,?,?,?)", prepared
        )
    return len(prepared)
