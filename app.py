#!/usr/bin/env python3
"""Ops OS: concept prototype for the OEM's factory-to-door operating system.

    python3 app.py              # build the mock database on first run, then serve
    python3 app.py --reset      # rebuild the database from scratch (same seed = same data)
    python3 app.py --as-of 2026-09-26 --port 8000 --no-browser

Standard library only. No pip install needed.
"""
import argparse
import threading
import time
import webbrowser

from ops import config


def _stale(path):
    """(as_of, seed) of a database stamped with another schema version, or None when it is current."""
    import sqlite3
    from ops.db import connect
    try:
        conn = connect(path, readonly=True)
        try:
            meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
        finally:
            conn.close()
    except sqlite3.Error:
        return (None, None)
    if meta.get("schema_version") == config.SCHEMA_VERSION:
        return None
    seed = meta.get("seed")
    return (meta.get("as_of"), int(seed) if seed and seed.isdigit() else None)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reset", action="store_true", help="rebuild the database")
    ap.add_argument("--as-of", help="dataset 'today' as YYYY-MM-DD (default: today)")
    ap.add_argument("--seed", type=int, default=config.DEFAULT_SEED)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--generate-only", action="store_true", help="build the database and exit")
    args = ap.parse_args()

    as_of, seed = args.as_of, args.seed
    stale = None if args.reset or not config.DB_PATH.exists() else _stale(config.DB_PATH)
    if stale is not None:
        # built by an older schema: rebuild the same world (same date and seed) rather than fail on a missing column
        as_of, seed = args.as_of or stale[0], stale[1] if stale[1] is not None else seed
        print(f"{config.DB_PATH.name} was built by an older version of the schema; rebuilding it as of {as_of}")
    if args.reset or stale is not None or not config.DB_PATH.exists():
        from ops.generate import build_database
        t0 = time.time()
        print(f"building mock database at {config.DB_PATH} ...")
        summary = build_database(config.DB_PATH, as_of=as_of, seed=seed)
        print(f"done in {time.time() - t0:.1f}s  {summary}")
    if args.generate_only:
        return

    from ops.api.server import serve
    if not args.no_browser:
        url = f"http://localhost:{args.port}/"
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    serve(args.port)


if __name__ == "__main__":
    main()
