"""SAR screening: calibrated Sigma0 dB input, robust dark-region segmentation."""

import json
import numpy as np
import rasterio
from rasterio.features import shapes
from rasterio.warp import transform_bounds
from scipy import ndimage
from shapely.geometry import shape, mapping
from shapely.ops import transform, unary_union
from pyproj import Transformer
from PIL import Image
from .geo import GEOD


def prepare(path, out, metadata):
    with rasterio.open(path) as src:
        if not src.crs:
            raise ValueError(
                "A georeferenced GeoTIFF with a CRS is required. PNG/JPEG needs a georeferencing sidecar."
            )
        if src.width * src.height > 25000000:
            raise ValueError(
                "Scene exceeds 25 million pixels; tile the scene before import."
            )
        tags = src.tags()
        radiometry = metadata.get("radiometry", tags.get("radiometry"))
        if radiometry not in ("sigma0_db", "sigma0_linear"):
            raise ValueError(
                "Declare calibrated sigma0_db or sigma0_linear radiometry. Raw Sentinel-1 SAFE requires external calibration/terrain correction first."
            )
        a = src.read(1, masked=True).filled(np.nan).astype("float64")
        if radiometry == "sigma0_linear":
            a = 10 * np.log10(np.maximum(a, 1e-10))
        valid = np.isfinite(a)
        if valid.sum() < 100:
            raise ValueError("Too few valid pixels in scene")
        median = float(np.nanmedian(a))
        a[~valid] = median
        smooth = ndimage.median_filter(a, size=3)
        # Conservative adaptive threshold: strong local damping relative to scene median.
        threshold = median - max(6.0, float(np.median(np.abs(a - median))) * 3.5)
        mask = ndimage.binary_opening((smooth < threshold) & valid, iterations=1)
        mask = ndimage.binary_closing(mask, iterations=2)
        labels, count = ndimage.label(mask)
        sizes = np.bincount(labels.ravel())
        keep = sizes >= 12
        keep[0] = False
        mask = keep[labels]
        relaxed = ndimage.binary_opening((smooth < median - 3) & valid, iterations=1)
        relaxed_labels, relaxed_count = ndimage.label(relaxed)
        rejected_regions = []
        relaxed_sizes = np.bincount(relaxed_labels.ravel())
        accepted_ids = set(np.unique(relaxed_labels[mask]))
        for candidate in range(1, relaxed_count + 1):
            if relaxed_sizes[candidate] < 12 or candidate in accepted_ids:
                continue
            region = relaxed_labels == candidate
            rejected_regions.append(
                {
                    "pixels": int(region.sum()),
                    "damping_db": round(float(median - np.median(smooth[region])), 2),
                    "reason": "Insufficient backscatter damping for this screening threshold; possible look-alike, unconfirmed",
                }
            )
        transform_to_wgs = Transformer.from_crs(src.crs, 4326, always_xy=True)
        polys = [
            transform(transform_to_wgs.transform, shape(g))
            for g, v in shapes(mask.astype("uint8"), mask=mask, transform=src.transform)
            if v == 1
        ]
        bounds = transform_bounds(src.crs, 4326, *src.bounds)
        profile = src.profile.copy()
        profile.update(count=1, dtype="uint8", nodata=0)
        with rasterio.open(out / "mask.tif", "w", **profile) as dst:
            dst.write(mask.astype("uint8"), 1)
        Image.fromarray(np.clip((a + 30) / 30 * 255, 0, 255).astype("uint8")).save(
            out / "satellite.png"
        )
        Image.fromarray(np.clip((smooth + 30) / 30 * 255, 0, 255).astype("uint8")).save(
            out / "processed.png"
        )
        overlay = np.zeros((*mask.shape, 4), dtype="uint8")
        overlay[mask] = [255, 74, 87, 110]
        Image.fromarray(overlay).save(out / "mask.png")
        geo = unary_union(polys) if polys else None
        area, perimeter = GEOD.geometry_area_perimeter(geo) if geo else (0, 0)
        contrast = (
            float(np.median(smooth[~mask]) - np.median(smooth[mask]))
            if mask.any() and (~mask).any()
            else 0
        )
        slicks = []
        for p in polys:
            ar, pe = GEOD.geometry_area_perimeter(p)
            slicks.append(
                {
                    "type": "Feature",
                    "geometry": mapping(p),
                    "properties": {
                        "area_km2": abs(ar) / 1e6,
                        "perimeter_km": pe / 1000,
                    },
                }
            )
        bright, _ = ndimage.label((smooth > -5) & valid)
        detections = []
        for label in range(1, int(bright.max()) + 1):
            yy, xx = np.where(bright == label)
            if 2 <= len(xx) <= 100:
                x, y = src.transform * (float(xx.mean()), float(yy.mean()))
                detections.append(
                    {
                        "coordinates": list(transform_to_wgs.transform(x, y)),
                        "pixels": len(xx),
                        "classification": "bright return; unverified vessel",
                    }
                )
        result = {
            "area_km2": round(abs(area) / 1e6, 3),
            "perimeter_km": round(perimeter / 1000, 3),
            "centroid": (
                list(geo.centroid.coords)[0]
                if geo
                else [(bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2]
            ),
            "geometry": mapping(geo) if geo else None,
            "slicks": {"type": "FeatureCollection", "features": slicks},
            "component_count": len(polys),
            "bounds": list(bounds),
            "contrast_db": round(contrast, 2),
            "threshold_db": round(threshold, 2),
            "candidate_pixels": int(mask.sum()),
            "valid_fraction": float(valid.mean()),
            "sensor": metadata.get("sensor", tags.get("sensor", "Imported SAR")),
            "method": "Median filter + adaptive dark-region screening v1",
            "calibration": "Input declared calibrated; calibration not inferred",
            "crs": str(src.crs),
            "resolution": list(src.res),
            "polarizations": metadata.get(
                "polarizations", tags.get("polarizations", "unknown")
            ),
            "radiometry": radiometry,
            "oil_probability": None,
            "look_alike_screening": {
                "rejected_regions": rejected_regions,
                "method": "Relaxed candidate threshold followed by minimum damping screen",
                "validated_classifier": False,
            },
            "oil_type": "Unknown from SAR alone",
            "volume_m3": None,
            "sar_detections": detections,
            "status": "CANDIDATE" if polys else "NO_CANDIDATE",
            "limitations": [
                "Dark pixels are oil candidates, not confirmed oil.",
                "No trained oil/look-alike classifier has been installed.",
                "Land masking and weather context are required for real-scene interpretation.",
            ],
            "uncertainty": {
                "boundary": "Resolution and threshold dependent; no calibrated confidence interval",
                "probability_calibrated": False,
            },
        }
        (out / "spill.geojson").write_text(
            json.dumps(result["slicks"]), encoding="utf-8"
        )
        return result
