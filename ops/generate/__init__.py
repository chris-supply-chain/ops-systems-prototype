"""Build the database: simulate the world, land every system's raw view, run the
normalizers, then run the decision logic the way the live platform would.
"""
import datetime as dt
import os
import time
from pathlib import Path

from .. import config
from ..db import connect, init_schema


def build_database(path, as_of=None, seed=7, verbose=True):
    from .. import ingest
    from ..logic import state
    from . import emit, post
    from .world import World

    t_start = time.time()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".building")
    for p in (tmp, Path(str(tmp) + "-journal")):
        if p.exists():
            p.unlink()
    conn = connect(tmp)
    conn.execute("PRAGMA journal_mode = MEMORY")
    conn.execute("PRAGMA synchronous = OFF")
    init_schema(conn)
    as_of = dt.date.fromisoformat(as_of) if as_of else dt.date.today()

    def log(msg):
        if verbose:
            print(f"  [{time.time() - t_start:5.1f}s] {msg}", flush=True)

    w = World(conn, as_of, seed)
    conn.executemany("INSERT INTO meta(key, value) VALUES (?, ?)", [
        ("as_of", as_of.isoformat()), ("now_utc", w.now_s), ("seed", str(seed)),
        ("generated_at", dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
        ("app", "Ops OS"), ("schema_version", config.SCHEMA_VERSION),
    ])
    w.run()
    log(f"simulated {len(w.vehicles):,} vehicles, {len(w.packs):,} packs, {len(w.orders):,} orders; raw feeds landed")
    now = w.now_s
    summary = {}
    summary["cm_mes+asn"] = ingest.run_cm_and_asn(conn, now)
    log(f"CM MES + ASN normalized: {summary['cm_mes+asn']}")
    emit.oem_mes(w)
    emit.shipments(w)
    summary["3pl"] = ingest.run_3pl(conn, now)
    summary["carrier"] = ingest.run_carrier(conn, now)
    summary["ack"] = ingest.run_confirmations(conn, now)
    summary["email"] = ingest.run_email(conn, now)
    summary["crm"] = ingest.run_warranty(conn, now)
    log(f"3PL / carrier / supplier acks / email / CRM normalized")
    post.post_ingest(w)
    state.derive(conn)
    _run_logic(conn, w, log)
    conn.commit()
    conn.execute("PRAGMA journal_mode = DELETE")
    conn.close()
    os.replace(tmp, path)
    log("done")
    counts = {}
    c2 = connect(path, readonly=True)
    for t in ("unit", "station_event", "genealogy", "customer_order", "shipment", "raw_cm_mes_event", "raw_email"):
        counts[t] = c2.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"]
    c2.close()
    return counts


def _run_logic(conn, w, log):
    """Decision logic, in dependency order. Each module is optional until it exists."""
    import importlib
    for mod, fn in (("promises", "initial_promises"), ("mrp", "run_and_store"), ("scorecard", "compute"),
                    ("exceptions", "detect"), ("decisions", "propose"), ("contracts", "register_and_run"),
                    ("evals", "run_all")):
        try:
            m = importlib.import_module(f"ops.logic.{mod}")
        except ModuleNotFoundError:
            continue
        getattr(m, fn)(conn)
        log(f"logic: {mod}.{fn}")
