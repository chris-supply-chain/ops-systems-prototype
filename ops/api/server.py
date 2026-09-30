"""Stdlib HTTP server: JSON API under /api, static files from web/."""
import importlib
import json
import mimetypes
import pkgutil
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlparse

from .. import config
from ..db import connect
from . import routes as routes_pkg
from .router import HttpError, Raw, Request, match

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")


def load_routes():
    for info in pkgutil.iter_modules(routes_pkg.__path__):
        importlib.import_module(f"{routes_pkg.__name__}.{info.name}")


class Handler(BaseHTTPRequestHandler):
    server_version = "OpsOS/0.1"

    def log_message(self, fmt, *args):  # keep the console quiet; errors print below
        pass

    def _send(self, status, body, ctype):
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass            # the browser navigated away mid-response

    def _json(self, status, payload):
        body = json.dumps(payload, default=str, separators=(",", ":")).encode()
        self._send(status, body, "application/json; charset=utf-8")

    def _api(self, method):
        url = urlparse(self.path)
        fn, params = match(method, url.path)
        if fn is None:
            return self._json(404, {"error": f"no route for {method} {url.path}"})
        body = None
        if method == "POST":
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return self._json(400, {"error": "body must be JSON"})
        conn = connect()
        started = time.perf_counter()
        try:
            req = Request(method, url.path, dict(parse_qsl(url.query)), body, conn, params)
            result = fn(req)
            if method == "POST":
                conn.commit()
            if isinstance(result, Raw):
                self.send_response(200)
                self.send_header("Content-Type", result.content_type)
                self.send_header("Content-Length", str(len(result.body)))
                if result.filename:
                    from urllib.parse import quote
                    self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(result.filename)}")
                self.end_headers()
                try:
                    self.wfile.write(result.body)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                self._json(200, result)
        except HttpError as e:
            conn.rollback()
            self._json(e.status, {"error": e.message})
        except Exception as e:  # surface the traceback in the console, message to the UI
            conn.rollback()
            traceback.print_exc()
            self._json(500, {"error": f"{type(e).__name__}: {e}"})
        finally:
            conn.close()
            ms = (time.perf_counter() - started) * 1000
            if ms > 800:
                print(f"slow {method} {url.path} {ms:.0f}ms", file=sys.stderr)

    def _static(self):
        path = urlparse(self.path).path
        if path in ("", "/"):
            path = "/index.html"
        target = (config.WEB_DIR / path.lstrip("/")).resolve()
        if config.WEB_DIR.resolve() not in target.parents or not target.is_file():
            target = config.WEB_DIR / "index.html"
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript") or ctype.endswith("json"):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self._api("GET")
        return self._static()

    def do_POST(self):
        if self.path.startswith("/api/"):
            return self._api("POST")
        self._json(405, {"error": "POST only under /api"})


def serve(port=8000, host="127.0.0.1"):
    load_routes()
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    print(f"Ops OS running at http://localhost:{port}/  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
