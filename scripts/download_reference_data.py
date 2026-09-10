"""Download a small public-domain global reference package from Natural Earth's repository."""

import json, sys, urllib.request, hashlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import storage
from backend.config import DATA

storage.init()
layers = [
    ("ne_110m_admin_0_countries", "country"),
    ("ne_10m_geography_marine_polys", "sea"),
    ("ne_10m_ports", "port"),
    ("ne_110m_coastline", "coastline"),
]
manifest = []
for name, kind in layers:
    path = DATA / "geospatial" / (name + ".geojson")
    url = f"https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/{name}.geojson"
    urllib.request.urlretrieve(url, path)
    collection = json.loads(path.read_text(encoding="utf-8"))
    for f in collection["features"]:
        p = f["properties"]
        p["name"] = p.get("name") or p.get("NAME") or p.get("ADMIN") or name
        if kind == "sea" and "ocean" in p["name"].lower():
            p["kind"] = "ocean"
    count = storage.import_geography(
        collection,
        "Natural Earth public domain - " + name,
        "snapshot-2026-09-10",
        "REAL",
        kind,
    )
    manifest.append(
        {
            "name": name,
            "url": url,
            "kind": kind,
            "features": count,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "license": "Public domain",
            "snapshot": "2026-09-10",
        }
    )
    print(name, count, flush=True)
(DATA / "geospatial/sources.json").write_text(json.dumps(manifest, indent=2))
