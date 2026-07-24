from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import threading
import time
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse

from .server import APP, JarvisHandler


def _ensure_local_gateway() -> None:
    """Start OmniRoute when JARVIS is configured for it and it is not already running."""
    if os.environ.get("JARVIS_AUTOSTART_OMNIROUTE", "true").lower() not in {"1", "true", "yes"}:
        return
    base_url = APP.intelligence._configured_base_url()
    if not base_url:
        return
    parsed = urlparse(base_url)
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    port_open = False
    try:
        with socket.create_connection((parsed.hostname, port), timeout=0.25):
            port_open = True
    except OSError:
        pass
    if port_open:
        root_url = base_url.rstrip("/")
        if root_url.endswith("/v1"):
            root_url = root_url[:-3]
        try:
            from urllib.error import HTTPError
            from urllib.request import Request, urlopen

            with urlopen(Request(root_url + "/favicon.ico", method="HEAD"), timeout=1.5):
                APP.intelligence._last_error = None
                return
        except HTTPError as exc:
            exc.close()
            return
        except (OSError, TimeoutError):
            pass
    health_error = "The local intelligence gateway is not responding."
    executable = shutil.which("omniroute.cmd") or shutil.which("omniroute")
    if not executable:
        return
    try:
        if port_open:
            subprocess.run(
                [executable, "restart", "--port", str(port)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            subprocess.Popen(
                [
                    executable,
                    "serve",
                    "--daemon",
                    "--no-open",
                    "--port",
                    str(port),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=(
                    getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    | getattr(subprocess, "DETACHED_PROCESS", 0)
                ),
            )
    except (OSError, subprocess.SubprocessError):
        return
    for _ in range(24):
        time.sleep(0.25)
        if not APP.intelligence._detect_local_endpoint_error():
            APP.intelligence._last_error = None
            return
    APP.intelligence._last_error = health_error


def main() -> None:
    parser = argparse.ArgumentParser(description="Run JARVIS as a native desktop window.")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    try:
        import webview
    except ImportError as exc:
        raise SystemExit(
            "Desktop dependencies are missing. Run Setup-JARVIS-Desktop.ps1 first."
        ) from exc

    _ensure_local_gateway()
    server = ThreadingHTTPServer(("127.0.0.1", 0), JarvisHandler)
    server.daemon_threads = True
    port = server.server_address[1]
    thread = threading.Thread(
        target=server.serve_forever,
        name="jarvis-desktop-server",
        daemon=True,
    )
    thread.start()

    try:
        webview.create_window(
            "JARVIS // Command Center",
            f"http://127.0.0.1:{port}",
            width=1440,
            height=900,
            min_size=(920, 620),
            background_color="#070a0d",
            confirm_close=False,
        )
        webview.start(debug=args.debug)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
