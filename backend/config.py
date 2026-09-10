from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "datasets"
for name in ("satellite", "ais", "environment", "geospatial", "demo", "cases"):
    (DATA / name).mkdir(parents=True, exist_ok=True)
VERSION = "ocean-eye-screening-1.0"
