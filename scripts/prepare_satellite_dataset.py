"""Inspect extracted Zenodo GeoTIFF and align matching unreferenced mask explicitly."""

import argparse
import json
from pathlib import Path
import rasterio
import numpy as np
from PIL import Image

parser = argparse.ArgumentParser()
parser.add_argument("image", type=Path)
parser.add_argument("--mask", type=Path)
parser.add_argument("--output", type=Path, default=Path("datasets/satellite"))
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
with rasterio.open(args.image) as src:
    if not src.crs:
        raise ValueError("Source image must have georeferencing")
    info = {
        "path": str(args.image),
        "crs": str(src.crs),
        "width": src.width,
        "height": src.height,
        "bands": src.count,
        "bounds": list(src.bounds),
        "transform": list(src.transform),
        "tags": src.tags(),
        "source_type": "REAL",
        "note": "Confirm Sigma0 units and acquisition timestamp from original scene metadata",
    }
    if args.mask:
        mask = np.asarray(Image.open(args.mask))
        if mask.ndim != 2 or mask.shape != (src.height, src.width):
            raise ValueError(
                "Mask must be a matching single-channel matrix with identical dimensions; no silent resizing"
            )
        profile = src.profile.copy()
        profile.update(count=1, dtype=mask.dtype)
        with rasterio.open(
            args.output / (args.mask.stem + "_aligned.tif"), "w", **profile
        ) as dst:
            dst.write(mask, 1)
    (args.output / (args.image.stem + "_metadata.json")).write_text(
        json.dumps(info, indent=2)
    )
print(json.dumps(info, indent=2))
