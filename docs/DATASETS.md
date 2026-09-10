# Dataset setup and import contracts

## Included datasets

The scenario observes a synthetic slick on **12 August 2025, 14:20 UTC**, in the Bay of Bengal near **80.55 E, 12.25 N**. The supplied release window is **11 August 2025, 20:00–23:00 UTC**. It is an input assumption, not an estimate extracted from one SAR scene.

The screenshot's sample coordinates were not treated as validated incident geography. The demo uses an offshore scene, visibly labelled synthetic.

- `satellite/demo_sar.tif`: 640 × 640 pixels, 150 m spacing, UTM 44N, VV/VH-like Sigma0 dB, seeded speckle, one elongated slick plus a low-contrast look-alike region.
- `ais/demo.csv`: 20 fictional MMSIs, 15-minute nominal sampling, one generated gap, slowdown and course change; two deliberately defective rows exercise validation.
- `environment/demo.json`: hourly time series covering hindcast and 48-hour forecast, with current/wind components and uncertainty.
- `demo/ground_truth.png`: generated segmentation truth for separate benchmarking; never used for detection or vessel ranking.
- `geospatial/demo_receptors.geojson`: explicitly synthetic reserve/fishing/port fixtures.
- Natural Earth country/marine-region/port/coastline layers: actual public-domain geographic reference data. Snapshot URLs, dates and SHA-256 checksums are in `geospatial/sources.json`.

Regenerate and validate:

```powershell
.\.venv\Scripts\python.exe scripts/create_demo_case.py
.\.venv\Scripts\python.exe scripts/validate_datasets.py
```

## AIS

Source: https://marinecadastre.gov/accessais/

Official historical dictionary: https://coast.noaa.gov/data/marinecadastre/ais/data-dictionary.pdf

The historical CSV convention is:

```csv
MMSI,BaseDateTime,LAT,LON,SOG,COG,Heading,VesselName,IMO,CallSign,VesselType,Status,Length,Width,Draft,Cargo,TransceiverClass
```

Required fields are MMSI, UTC time, latitude and longitude. Timezone-free MarineCadastre timestamps are interpreted as UTC, as specified by its data dictionary. SOG is knots, COG is degrees, coordinates are WGS84. MMSI is handled as a nine-character identifier. Missing speed/course is represented as missing, never silently treated as measured zero. Normalized aliases include `timestamp`, `latitude`, `longitude`, and lowercase field names. Newer MarineCadastre releases may use different compression and headers; decompress/subset before upload and check the dictionary for that year.

The application reads CSV, not raw NMEA radio messages. It rejects invalid coordinates/identities, duplicate identity/time records and jumps faster than 120 km/h, then sorts each track chronologically. The data-quality number measures accepted records, not AIS geographic completeness.

Real AIS must overlap the satellite scene and release time. MarineCadastre does not provide guaranteed global AIS coverage. Do not spatially relocate real tracks and call them real incident evidence.

## Sentinel-1 / Zenodo

- Part I: https://zenodo.org/records/8346860
- Part II: https://zenodo.org/records/8253899
- Part III: https://zenodo.org/records/13761290

Part I lists approximately 40.7 GB of imagery. This archive is not automatically downloaded. Obtain and extract the desired scenes locally. The georeferenced Sigma0 images contain VV/VH bands; matrix masks are not inherently georeferenced.

```powershell
.\.venv\Scripts\python.exe scripts/prepare_satellite_dataset.py path/to/scene.tif --mask path/to/matching_mask.png
```

This checks the CRS and dimensions and copies the matching source transform to a mask only when dimensions match. The caller must verify the mask belongs to that exact image; dimensions alone cannot prove pairing.

Supported analysis input: calibrated GeoTIFF with a valid CRS and a declared `sigma0_db` or `sigma0_linear` radiometry. First band must be the intended VV/analysis band. Raw SAFE calibration/terrain correction must be performed beforehand, for example in the official ESA SNAP workflow. The app does not pretend a median filter performs radiometric calibration. PNG/JPEG preview screenshots cannot support area/origin computation without verified georeferencing.

Current local limits: 150 MB per upload, 25 million pixels per image. Subset/tile larger scenes first.

## Environmental JSON

```json
{
  "source_type": "REAL",
  "source": "Publisher and dataset identifier",
  "units": "m/s",
  "spatial_coverage": [79.0, 11.0, 82.0, 14.0],
  "records": [
    {
      "time": "2025-08-11T00:00:00Z",
      "current_east_ms": 0.22,
      "current_north_ms": -0.11,
      "wind_east_ms": 2.0,
      "wind_north_ms": -1.0,
      "current_sigma_ms": 0.045,
      "wind_sigma_ms": 0.5
    }
  ]
}
```

The example shows the schema, not a complete dataset. Supply at least two ordered records spanning the earliest release time through observation plus 48 hours. Values use m/s. The current adapter interpolates in time and applies the series uniformly over the declared region. It does not ingest native gridded NetCDF or Zarr fields yet. Use **SYNTHETIC** for simulated forcing, even if satellite/AIS inputs are real; the combined run is then labelled demo/mixed synthetic.

## Geographic imports

Use the UI's **Cases & Data > Import geographic layer**, or:

```powershell
.\.venv\Scripts\python.exe scripts/import_geospatial.py my_layer.geojson --kind eez --source "Publisher" --version "2025"
```

Input is WGS84 GeoJSON FeatureCollection. Supported kinds include country, sea, ocean, port, eez, coastline, protected_area, fishery, infrastructure and shipping_corridor. Preserve source/version/jurisdiction and `boundary_status` in properties. Geometry repairs are recorded in metadata. Natural Earth is an approximate cartographic layer, not authoritative legal EEZ delimitation.

Download the included reference package again with `scripts/download_reference_data.py`. Load already-downloaded files offline with `scripts/load_local_geography.py`. No tile API keys are used.
