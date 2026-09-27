from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass  # python-dotenv not installed; CDSE_CLIENT_ID/SECRET must be set in the real environment
DATA = ROOT / "datasets"
for name in ("satellite", "ais", "environment", "geospatial", "demo", "cases"):
    (DATA / name).mkdir(parents=True, exist_ok=True)
VERSION = "ocean-eye-screening-1.0"
