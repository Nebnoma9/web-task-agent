"""Tiny static file server for the bundled demo sites (runs in a background thread)."""

from __future__ import annotations

import functools
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

DEFAULT_SITES_DIR = Path(__file__).resolve().parents[2] / "sites"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return None

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


@contextmanager
def serve_sites(
    sites_dir: Path = DEFAULT_SITES_DIR, host: str = "127.0.0.1", port: int = 0
) -> Iterator[str]:
    """Serve ``sites_dir`` and yield the base URL, e.g. ``http://127.0.0.1:53817``."""
    if not sites_dir.is_dir():
        raise FileNotFoundError(f"sites directory not found: {sites_dir}")
    handler = functools.partial(_QuietHandler, directory=str(sites_dir))
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, name="wta-sites", daemon=True)
    thread.start()
    try:
        yield f"http://{host}:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
