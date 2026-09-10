"""Start missing OCEAN-EYE services and verify readiness before announcing success."""

import argparse
import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def available(port):
    with socket.socket() as connection:
        try:
            connection.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def healthy(port, backend=False):
    try:
        path = "/api/v1/health" if backend else "/"
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}{path}", timeout=2
        ) as response:
            body = response.read(65536).decode("utf-8")
        if backend:
            data = json.loads(body)
            return data.get("status") == "online" and str(
                data.get("version", "")
            ).startswith("ocean-eye-")
        return "OCEAN-EYE" in body
    except (OSError, ValueError):
        return False


def service_plan(production=False):
    requested = [(8000, True)] if production else [(8000, True), (5173, False)]
    missing = []
    for port, backend in requested:
        if healthy(port, backend):
            print(f"Reusing OCEAN-EYE service on port {port}.", flush=True)
        elif available(port):
            missing.append((port, backend))
        else:
            raise RuntimeError(
                f"Port {port} is occupied by an unresponsive or different service. Stop that service and retry. No existing process was stopped."
            )
    return missing


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--production", action="store_true")
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    children = []
    try:
        missing = service_plan(args.production)
        node = shutil.which("node")
        if any(not backend for _, backend in missing) and (
            not node or not (ROOT / "node_modules/vite/bin/vite.js").exists()
        ):
            raise RuntimeError("Frontend dependencies missing. Run npm install first.")
        if args.production and not (ROOT / "dist/index.html").exists():
            raise RuntimeError(
                "Run npm run build before starting the presentation build."
            )
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
        for port, backend in missing:
            command = (
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "backend.app:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ]
                if backend
                else [
                    node,
                    str(ROOT / "node_modules/vite/bin/vite.js"),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--strictPort",
                ]
            )
            children.append(subprocess.Popen(command, cwd=ROOT))
        deadline = time.monotonic() + 45
        requested = [(8000, True)] if args.production else [(8000, True), (5173, False)]
        while not all(healthy(port, backend) for port, backend in requested):
            if any(child.poll() is not None for child in children):
                raise RuntimeError(
                    "A service exited during startup. See the error above."
                )
            if time.monotonic() > deadline:
                raise RuntimeError(
                    "Services did not become ready within 45 seconds. See startup logs above."
                )
            time.sleep(0.4)
        url = f"http://127.0.0.1:{8000 if args.production else 5173}"
        print(
            f"\nOCEAN-EYE ready: {url}\nBackend verified: http://127.0.0.1:8000/docs",
            flush=True,
        )
        if args.open:
            webbrowser.open(url)
        if not children:
            print(
                "Already running. Refresh the browser; no duplicate services were started."
            )
            return 0
        print(
            "Keep this terminal open. Ctrl+C stops only services started by this launcher.",
            flush=True,
        )
        while all(child.poll() is None for child in children):
            time.sleep(0.4)
        raise RuntimeError("A service stopped unexpectedly. Restart with npm run dev.")
    except KeyboardInterrupt:
        return 0
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(f"OCEAN-EYE startup error: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                child.kill()


if __name__ == "__main__":
    sys.exit(main())
