"""Import any WGS84 reference layer; no country/sea catalogue in application code."""

import argparse, json, sys, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import storage
from backend.config import DATA

parser = argparse.ArgumentParser()
parser.add_argument("path", nargs="?", type=Path)
parser.add_argument("--natural-earth", action="store_true")
parser.add_argument("--source", default="User-supplied dataset")
parser.add_argument("--version", default="unspecified")
parser.add_argument("--kind", default="country")
parser.add_argument("--source-type", choices=["REAL", "SYNTHETIC"], default="REAL")
args = parser.parse_args()
if args.natural_earth:
    args.path = DATA / "geospatial/countries.geojson"
    urllib.request.urlretrieve(
        "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_110m_admin_0_countries.geojson",
        args.path,
    )
    args.source = "Natural Earth 1:110m public domain"
    args.version = "5.1.1"
if not args.path:
    parser.error("Supply GeoJSON path or --natural-earth")
storage.init()
print(
    "Imported",
    storage.import_geography(
        json.loads(args.path.read_text(encoding="utf-8-sig")),
        args.source,
        args.version,
        args.source_type,
        args.kind,
    ),
    "features",
)
