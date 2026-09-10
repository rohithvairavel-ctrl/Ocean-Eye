import sys, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.config import DATA
from backend.ais import load_ais
from backend.drift import validate_environment
import rasterio

cfg = json.loads((DATA / "demo/case.json").read_text())
with rasterio.open(DATA / cfg["satellite"]) as src:
    assert src.crs and src.count == 2
tracks, quality = load_ais(DATA / cfg["ais"])
validate_environment(
    json.loads((DATA / cfg["environment"]).read_text()),
    cfg["observation_time"],
    cfg["release_window"],
)
print(
    json.dumps(
        {
            "satellite": "valid",
            "vessels": len(tracks),
            "ais": quality,
            "environment": "valid",
        },
        indent=2,
    )
)
