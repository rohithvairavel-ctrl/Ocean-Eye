"""Single-command presentation launcher; no global services or settings changed."""

import argparse, signal, socket, subprocess, sys, time, shutil, webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--production", action="store_true")
parser.add_argument("--open", action="store_true")
args = parser.parse_args()


def available(port):
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


ports = [8000] if args.production else [8000, 5173]
if not all(available(p) for p in ports):
    print(
        "OCEAN-EYE ports are already in use. If the app is running, open http://127.0.0.1:5173 (or :8000 for presentation build). Otherwise stop the earlier server and retry."
    )
    sys.exit(1)
if not (ROOT / "datasets/demo/case.json").exists():
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/create_demo_case.py")],
        cwd=ROOT,
        check=True,
    )
if not (ROOT / "datasets/ocean_eye.sqlite").exists():
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/load_local_geography.py")],
        cwd=ROOT,
        check=True,
    )
node = shutil.which("node")
if not args.production and (
    not node or not (ROOT / "node_modules/vite/bin/vite.js").exists()
):
    print("Run npm install first.")
    sys.exit(1)
if args.production and not (ROOT / "dist/index.html").exists():
    print("Run npm run build first.")
    sys.exit(1)
children = []
try:
    children.append(
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "backend.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
            ],
            cwd=ROOT,
        )
    )
    if not args.production:
        children.append(
            subprocess.Popen(
                [
                    node,
                    str(ROOT / "node_modules/vite/bin/vite.js"),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "5173",
                    "--strictPort",
                ],
                cwd=ROOT,
            )
        )
    url = "http://127.0.0.1:" + ("8000" if args.production else "5173")
    print(
        "\nOCEAN-EYE | "
        + url
        + "\nAPI: http://127.0.0.1:8000/docs\nPress Ctrl+C here to stop both servers.\n",
        flush=True,
    )
    if args.open:
        time.sleep(2)
        webbrowser.open(url)
    while all(p.poll() is None for p in children):
        time.sleep(0.4)
except KeyboardInterrupt:
    pass
finally:
    for child in children:
        if child.poll() is None:
            child.terminate()
    for child in children:
        try:
            child.wait(timeout=8)
        except subprocess.TimeoutExpired:
            child.kill()
