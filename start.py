#!/usr/bin/env python3
"""maimai-planner launcher: start server and open the default browser."""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
SERVER = PROJECT_DIR / "server" / "server.py"
READY_TIMEOUT = 20.0
PORT_START = 20001
PORT_TRIES = 20
BAR = "-" * 62


def port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def pick_port(host: str, start: int, tries: int) -> int | None:
    for port in range(start, start + tries):
        if port_free(host, port):
            return port
    return None


def wait_ready(port: int, proc: subprocess.Popen) -> bool:
    url = f"http://127.0.0.1:{port}/api/config"
    deadline = time.time() + READY_TIMEOUT
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=1.5) as response:
                if response.status == 200 and proc.poll() is None:
                    return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="start.py", description="maimai-planner launcher")
    ap.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=PORT_START, help="start port (default 20001)")
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser")
    args = ap.parse_args(argv)

    if not SERVER.is_file():
        print(f"[ERROR] missing server: {SERVER}", file=sys.stderr)
        return 2

    port = pick_port(args.host, args.port, PORT_TRIES)
    if port is None:
        print(f"[ERROR] no free port in {args.port}..{args.port + PORT_TRIES - 1}", file=sys.stderr)
        return 2

    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    print(BAR)
    print("maimai-planner launcher")
    print(f"  project: {PROJECT_DIR}")
    print(f"  python:  {sys.executable}")
    print(f"  server:  {SERVER.name} --host {args.host} --port {port}")
    print(BAR)

    proc = subprocess.Popen(
        [sys.executable, str(SERVER), "--host", args.host, "--port", str(port)],
        cwd=str(PROJECT_DIR),
        env=env,
    )

    try:
        if not wait_ready(port, proc):
            print("[ERROR] server did not become ready.", file=sys.stderr)
            return 2

        url = f"http://127.0.0.1:{port}/"
        print(f"[OK] server ready: {url}")
        if args.no_browser:
            print("browser disabled; open the URL manually.")
        else:
            try:
                webbrowser.open(url)
                print("[OK] browser opened.")
            except Exception as exc:  # noqa: BLE001
                print(f"[WARN] browser did not open: {exc}", file=sys.stderr)
                print(f"open manually: {url}")

        print(BAR)
        print("Ctrl+C stops the server.")
        while proc.poll() is None:
            time.sleep(0.5)
        print(f"[server exited: {proc.returncode}]")
        return proc.returncode or 0
    except KeyboardInterrupt:
        print("\n[stopping server]")
        return 0
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
