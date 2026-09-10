"""Reproducible test inputs. Generator truth never enters the ranking engine."""

import csv
import json
from datetime import datetime, timedelta, timezone
import numpy as np
import rasterio
from rasterio.transform import from_origin
from PIL import Image
from .config import DATA
from .geo import projection, circle, feature

OBSERVATION = "2025-08-12T14:20:00Z"
FIELDS = [
    "MMSI",
    "BaseDateTime",
    "LAT",
    "LON",
    "SOG",
    "COG",
    "Heading",
    "VesselName",
    "IMO",
    "CallSign",
    "VesselType",
    "Status",
    "Length",
    "Width",
    "Draft",
    "Cargo",
    "TransceiverClass",
]


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def generate_environment():
    rows = []
    start = datetime(2025, 8, 11, tzinfo=timezone.utc)
    for h in range(112):
        rows.append(
            {
                "time": (start + timedelta(hours=h)).isoformat(),
                "current_east_ms": 0.22 + 0.015 * np.sin(h / 8),
                "current_north_ms": -0.11 + 0.01 * np.cos(h / 7),
                "wind_east_ms": 2.0,
                "wind_north_ms": -1.0,
                "current_sigma_ms": 0.045,
                "wind_sigma_ms": 0.5,
            }
        )
    result = {
        "source_type": "SYNTHETIC",
        "source": "Seeded uniform demo forcing; not an ocean reanalysis",
        "units": "m/s",
        "spatial_coverage": [77, 10, 82, 15],
        "records": rows,
    }
    write_json(DATA / "environment/demo.json", result)
    return result


def generate_satellite():
    rng = np.random.default_rng(428)
    n, pixel = 640, 150
    xx, yy = np.meshgrid((np.arange(n) - n / 2) * pixel, (n / 2 - np.arange(n)) * pixel)
    # ~42.6 km2 curved elongated slick, plus low-backscatter look-alike patch.
    angle = -0.75
    u, v = xx * np.cos(angle) + yy * np.sin(angle), -xx * np.sin(angle) + yy * np.cos(
        angle
    )
    mask = (u / 7300) ** 2 + ((v - 450 * np.sin(u / 1700)) / 1858) ** 2 < 1
    base = -12 + rng.normal(0, 1.6, (n, n)) + 0.7 * np.sin(xx / 6000)
    base[mask] -= 11
    low_wind = (xx + 24000) ** 2 + (yy - 26000) ** 2 < 3500**2
    base[low_wind] -= 5
    # Bright compact returns for demonstration of the separate SAR detector.
    for x, y in [(345, 265), (278, 335), (405, 210)]:
        base[y - 1 : y + 2, x - 1 : x + 2] = -1
    lon, lat = 80.55, 12.25
    epsg = "EPSG:32644"
    from pyproj import Transformer

    cx, cy = Transformer.from_crs(4326, epsg, always_xy=True).transform(lon, lat)
    path = DATA / "satellite/demo_sar.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=n,
        width=n,
        count=2,
        dtype="float32",
        crs=epsg,
        transform=from_origin(cx - n / 2 * pixel, cy + n / 2 * pixel, pixel, pixel),
        compress="deflate",
    ) as dst:
        dst.write(base.astype("float32"), 1)
        dst.write((base - 5 + rng.normal(0, 0.5, (n, n))).astype("float32"), 2)
        dst.update_tags(
            source_type="SYNTHETIC",
            acquisition_time=OBSERVATION,
            sensor="Synthetic SAR / Sentinel-1 compatible",
            radiometry="sigma0_db",
            polarizations="VV,VH",
        )
    Image.fromarray(mask.astype("uint8") * 255).save(DATA / "demo/ground_truth.png")
    write_json(
        DATA / "satellite/demo_sar.json",
        {
            "source_type": "SYNTHETIC",
            "acquisition_time": OBSERVATION,
            "radiometry": "sigma0_db",
            "sensor": "Synthetic SAR",
            "polarizations": ["VV", "VH"],
            "crs": epsg,
            "seed": 428,
        },
    )
    return path


def generate_ais():
    rng = np.random.default_rng(721)
    obs = datetime.fromisoformat(OBSERVATION.replace("Z", "+00:00"))
    origin = [
        80.55 - 0.28 * 16.8 * 3600 / (111320 * np.cos(np.radians(12.25))),
        12.25 + 0.14 * 16.8 * 3600 / 110574,
    ]
    fwd, back = projection(*origin)
    rows = []
    for vessel in range(20):
        course = rng.uniform(0, 360)
        speed = rng.uniform(6, 15)
        offset = rng.uniform(-43000, 43000, 2)
        for step in range(145):
            t = obs - timedelta(hours=30) + timedelta(minutes=15 * step)
            h = (t - (obs - timedelta(hours=16.8))).total_seconds() / 3600
            if vessel == 0:
                if -0.6 < h < 0.65:
                    continue
                # Continuous slow passage, followed by a gradual return to transit speed.
                x = h * 650 if abs(h) < 2 else np.sign(h) * (1300 + (abs(h) - 2) * 9500)
                y = 600 * np.sin(h / 1.5)
                sog = 1.2 if abs(h) < 2 else 18.5
                cog = 65 + 22 * np.sin(h) if abs(h) < 3 else 90
            else:
                x = offset[0] + h * speed * 1852 * np.sin(np.radians(course))
                y = offset[1] + h * speed * 1852 * np.cos(np.radians(course))
                sog = speed + rng.normal(0, 0.15)
                cog = course + rng.normal(0, 1.2)
            lon, lat = back.transform(x, y)
            rows.append(
                dict(
                    zip(
                        FIELDS,
                        [
                            str(900000001 + vessel),
                            t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            round(lat, 6),
                            round(lon, 6),
                            round(sog, 2),
                            round(cog % 360, 2),
                            round(cog % 360),
                            (
                                "DEMO Seabreeze"
                                if vessel == 0
                                else f"DEMO Vessel {vessel:02}"
                            ),
                            "",
                            f"DEMO{vessel:02}",
                            80 if vessel % 3 == 0 else 70,
                            0,
                            183,
                            28,
                            9,
                            80,
                            "A",
                        ],
                    )
                )
            )
    # Known input defects exercise the same cleaning code as imports.
    rows += [dict(rows[20]), {**rows[40], "LAT": 999}]
    path = DATA / "ais/demo.csv"
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    write_json(
        DATA / "ais/demo.json",
        {
            "source_type": "SYNTHETIC",
            "seed": 721,
            "source": "Synthetic MarineCadastre-compatible CSV; identities are fictional",
            "rows": len(rows),
        },
    )
    return path


def generate_geography():
    # Explicit synthetic exposure features; world basemap is imported independently.
    features = [
        feature(
            circle([80.9, 12.08], 3),
            id="demo-mpa",
            name="Demo Marine Reserve",
            kind="protected_area",
            source_type="SYNTHETIC",
        ),
        feature(
            circle([81.02, 12.02], 5),
            id="demo-fish",
            name="Demo Fishing Zone",
            kind="fishery",
            source_type="SYNTHETIC",
        ),
        feature(
            {"type": "Point", "coordinates": [80.32, 13.08]},
            id="demo-port",
            name="Demo port receptor",
            kind="port",
            source_type="SYNTHETIC",
        ),
    ]
    value = {
        "type": "FeatureCollection",
        "features": features,
        "source": "Synthetic exposure fixtures",
        "version": "1",
        "source_type": "SYNTHETIC",
    }
    write_json(DATA / "geospatial/demo_receptors.geojson", value)
    return value


def create_demo():
    generate_satellite()
    generate_ais()
    generate_environment()
    generate_geography()
    result = {
        "name": "Bay of Bengal · SAR investigation",
        "observation_time": OBSERVATION,
        "source_type": "SYNTHETIC",
        "satellite": "satellite/demo_sar.tif",
        "ais": "ais/demo.csv",
        "environment": "environment/demo.json",
        "release_window": ["2025-08-11T20:00:00Z", "2025-08-11T23:00:00Z"],
        "release_window_basis": "Analyst-supplied synthetic scenario assumption; not inferred from one SAR image",
        "seed": 428,
    }
    write_json(DATA / "demo/case.json", result)
    return result
