"""WGS84 IO, local equal-distance analysis, geodesic measurements."""

import numpy as np
from pyproj import CRS, Geod, Transformer
from shapely.geometry import shape, mapping, Point
from shapely.ops import transform

GEOD = Geod(ellps="WGS84")


def projection(lon, lat):
    crs = CRS.from_proj4(f"+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m")
    return (
        Transformer.from_crs(4326, crs, always_xy=True),
        Transformer.from_crs(crs, 4326, always_xy=True),
    )


def distance(a, b):
    return abs(GEOD.inv(a[0], a[1], b[0], b[1])[2]) / 1000


def circle(center, radius_km):
    forward, backward = projection(*center)
    return mapping(
        transform(
            backward.transform, Point(0, 0).buffer(radius_km * 1000, quad_segs=32)
        )
    )


def feature(geometry, **props):
    return {"type": "Feature", "geometry": geometry, "properties": props}


def ellipse(points, quantile=0.9):
    points = np.asarray(points)
    center = points.mean(axis=0)
    forward, back = projection(*center)
    x, y = forward.transform(points[:, 0], points[:, 1])
    xy = np.column_stack([x, y])
    cov = np.cov(xy.T) + np.eye(2)
    values, vectors = np.linalg.eigh(cov)
    # Gaussian two-dimensional radial quantile. This is conditional model spread.
    radius = np.sqrt(-2 * np.log(1 - quantile))
    theta = np.linspace(0, 2 * np.pi, 65)
    ring = (
        np.column_stack([np.cos(theta), np.sin(theta)])
        @ np.diag(np.sqrt(values))
        @ vectors.T
        * radius
    )
    lon, lat = back.transform(ring[:, 0], ring[:, 1])
    return {"type": "Polygon", "coordinates": [np.column_stack([lon, lat]).tolist()]}
