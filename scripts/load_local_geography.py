import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.config import DATA
from backend import storage

storage.init()
manifest = DATA / "geospatial/sources.json"
if manifest.exists():
    for entry in json.loads(manifest.read_text()):
        path = DATA / "geospatial" / (entry["name"] + ".geojson")
        if not path.exists():
            continue
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        for f in value["features"]:
            p = f["properties"]
            p["name"] = (
                p.get("name") or p.get("NAME") or p.get("ADMIN") or entry["name"]
            )
            if entry["kind"] == "sea" and "ocean" in p["name"].lower():
                p["kind"] = "ocean"
        print(
            entry["name"],
            storage.import_geography(
                value,
                "Natural Earth public domain - " + entry["name"],
                "snapshot-" + entry["snapshot"],
                "REAL",
                entry["kind"],
            ),
        )
else:
    print(
        "No downloaded reference manifest. Run scripts/download_reference_data.py when online."
    )
