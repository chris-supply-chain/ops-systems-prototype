"""A tiny regex router. Route modules register handlers with @get / @post.

    @get(r"^/api/genealogy/trace$")
    def trace(req):
        return {...}          # anything json.dumps can handle (default=str)

Handlers receive a Request with .conn (sqlite3, dict rows), .query (dict of
str), .params (regex groups), .body (parsed JSON for POST) and .arg().
Raise HttpError(404, "...") for client errors.
"""
import re

ROUTES = []


class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class Raw:
    """Return this from a handler to send bytes instead of JSON (e.g. an original .xlsx attachment)."""

    def __init__(self, body, content_type="application/octet-stream", filename=None):
        self.body = body
        self.content_type = content_type
        self.filename = filename


class Request:
    def __init__(self, method, path, query, body, conn, params):
        self.method = method
        self.path = path
        self.query = query
        self.body = body or {}
        self.conn = conn
        self.params = params

    def arg(self, name, default=None, cast=str):
        raw = self.query.get(name)
        if raw is None or raw == "":
            return default
        try:
            return cast(raw)
        except (TypeError, ValueError):
            raise HttpError(400, f"bad value for '{name}': {raw!r}")


def route(method, pattern):
    rx = re.compile(pattern)

    def deco(fn):
        ROUTES.append((method, rx, fn))
        return fn
    return deco


def get(pattern):
    return route("GET", pattern)


def post(pattern):
    return route("POST", pattern)


def match(method, path):
    for m, rx, fn in ROUTES:
        if m != method:
            continue
        found = rx.match(path)
        if found:
            return fn, found.groups()
    return None, None
