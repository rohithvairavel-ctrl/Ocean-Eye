import os, shutil, sys, subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from backend import storage, datasets

storage.init()
if not (root / "datasets/demo/case.json").exists():
    datasets.create_demo()
shutil.copytree(root / "reference", root / "datasets/geospatial", dirs_exist_ok=True)
subprocess.run(
    [sys.executable, str(root / "scripts/load_local_geography.py")], check=True
)
os.execv(
    sys.executable,
    [
        sys.executable,
        "-m",
        "uvicorn",
        "backend.app:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
    ],
)
