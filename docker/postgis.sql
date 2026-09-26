-- Optional PostgreSQL/PostGIS deployment schema. The verified local runtime uses SQLite.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE TABLE IF NOT EXISTS maritime_feature (
 id uuid PRIMARY KEY,
 name text NOT NULL,
 kind text NOT NULL CHECK (kind IN ('country','territory','continent','ocean','sea','gulf','bay','strait','channel','eez','territorial_sea','port','anchorage','shipping_corridor','protected_area','fishery','infrastructure','coastline')),
 source_type text NOT NULL CHECK (source_type IN ('REAL','SYNTHETIC')),
 source text NOT NULL,
 dataset_version text NOT NULL,
 imported_at timestamptz NOT NULL DEFAULT now(),
 jurisdiction text,
 boundary_status text NOT NULL CHECK (boundary_status IN ('CONFIRMED','DISPUTED','APPROXIMATE','UNKNOWN')),
 geom geometry(Geometry,4326) NOT NULL,
 metadata jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS maritime_feature_geom_idx ON maritime_feature USING gist (geom);
CREATE INDEX IF NOT EXISTS maritime_feature_name_idx ON maritime_feature(lower(name));
CREATE TABLE IF NOT EXISTS investigation (
 id uuid PRIMARY KEY, name text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), config jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS analysis_run (
 id uuid PRIMARY KEY, case_id uuid NOT NULL REFERENCES investigation(id), created_at timestamptz NOT NULL DEFAULT now(),
 state text NOT NULL, analysis_hash text, model_version text, result jsonb, error text
);
CREATE TABLE IF NOT EXISTS artifact (
 id uuid PRIMARY KEY, run_id uuid NOT NULL REFERENCES analysis_run(id), role text NOT NULL, uri text NOT NULL,
 sha256 char(64) NOT NULL, source_type text NOT NULL CHECK(source_type IN ('REAL','SYNTHETIC')), metadata jsonb NOT NULL
);
