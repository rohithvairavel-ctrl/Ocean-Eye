"""Evaluate segmentation against matching demo truth, never used in inference."""

import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from PIL import Image
import rasterio
from backend.config import DATA
from backend import storage

parser = argparse.ArgumentParser()
parser.add_argument("--run")
args = parser.parse_args()
if args.run:
    r = storage.get_run(args.run)
else:
    with storage.connect() as db:
        row = db.execute(
            "SELECT id FROM runs WHERE state='COMPLETE' ORDER BY created DESC LIMIT 1"
        ).fetchone()
    if not row:
        raise SystemExit("Run a demo investigation first")
    r = storage.get_run(row["id"])
result = r["result"]
folder = DATA / "cases" / r["case_id"] / r["id"]
if result["source_type"] != "SYNTHETIC":
    raise SystemExit("This benchmark is for the generated synthetic scene only")
truth = np.asarray(Image.open(DATA / "demo/ground_truth.png")) > 0
with rasterio.open(folder / "mask.tif") as src:
    pred = src.read(1) > 0
if truth.shape != pred.shape:
    raise SystemExit("Ground truth shape mismatch")
intersection = int((truth & pred).sum())
union = int((truth | pred).sum())
metrics = {
    "scope": "Synthetic fixture only; not real-world validation",
    "iou": intersection / max(union, 1),
    "dice": 2 * intersection / max(int(truth.sum() + pred.sum()), 1),
    "predicted_pixels": int(pred.sum()),
    "truth_pixels": int(truth.sum()),
    "run_id": r["id"],
}
print(json.dumps(metrics, indent=2))
