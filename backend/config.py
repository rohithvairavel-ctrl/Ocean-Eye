import os
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


def _origins():
    configured = os.getenv("OCEANEYE_ALLOWED_ORIGINS", "")
    if configured.strip():
        return frozenset(value.strip().rstrip("/") for value in configured.split(",") if value.strip())
    return frozenset(
        f"http://{host}:{port}"
        for host in ("localhost", "127.0.0.1")
        for port in (5173, 8000, 4173)
    )


ALLOWED_ORIGINS = _origins()
DEPLOYMENT_MODE = os.getenv("OCEANEYE_DEPLOYMENT_MODE", "local").strip() or "local"
