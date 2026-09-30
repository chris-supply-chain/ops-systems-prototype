"""Contracts & Recon: data contracts over the live tables, and reconciliations where
two systems describe the same physical thing.
"""
import json
import time

from ...db import now as get_now
from ...logic import contracts, reconcile
from ..router import HttpError, get, post

SEV_ORDER = {"CRITICAL": 0, "SERIOUS": 1, "WARNING": 2}
MAX_ROWS = 500


def _definitions(conn):
    rows = [dict(r) for r in conn.execute("SELECT * FROM data_contract ORDER BY contract_id")]
    if rows:
        return rows
    keys = ["contract_id", "name", "domain", "layer", "severity", "description", "check_sql", "owner"]
    return [dict(zip(keys, c)) for c in contracts.CONTRACTS]


def _history(conn, limit=20):
    hist = {}
    for r in conn.execute("SELECT run_id, contract_id, ran_at, violations, duration_ms, sample_json FROM contract_run"
                          " ORDER BY run_id"):
        hist.setdefault(r["contract_id"], []).append(r)
    return {k: v[-limit:] for k, v in hist.items()}


def _shape(defn, runs):
    last = runs[-1] if runs else None
    sample = []
    if last and last["sample_json"]:
        try:
            sample = json.loads(last["sample_json"])
        except ValueError:
            sample = []
    return {**{k: defn[k] for k in ("contract_id", "name", "domain", "layer", "severity", "description", "check_sql", "owner")},
            "violations": last["violations"] if last else None, "ran_at": last["ran_at"] if last else None,
            "duration_ms": last["duration_ms"] if last else None, "sample": sample[:10],
            "history": [{"run_id": r["run_id"], "ran_at": r["ran_at"], "violations": r["violations"]} for r in runs],
            "status": None if not last else ("PASS" if last["violations"] == 0 else "FAIL")}


def _summary(items):
    failing = [c for c in items if c["status"] == "FAIL"]
    by_sev = {}
    for c in failing:
        by_sev[c["severity"]] = by_sev.get(c["severity"], 0) + 1
    runs = [c["ran_at"] for c in items if c["ran_at"]]
    return {"total": len(items), "passing": sum(1 for c in items if c["status"] == "PASS"), "failing": len(failing),
            "never_run": sum(1 for c in items if c["status"] is None), "failing_by_severity": by_sev,
            "violations": sum(c["violations"] or 0 for c in items), "last_run": max(runs) if runs else None}


@get(r"^/api/contracts/list$")
def list_contracts(req):
    conn = req.conn
    hist = _history(conn)
    items = [_shape(d, hist.get(d["contract_id"], [])) for d in _definitions(conn)]
    items.sort(key=lambda c: (0 if c["status"] == "FAIL" else 1, SEV_ORDER.get(c["severity"], 9), c["contract_id"]))
    return {"now": get_now(conn), "summary": _summary(items), "contracts": items}


@post(r"^/api/contracts/run$")
def run_now(req):
    conn = req.conn
    only = (req.body or {}).get("contract_id")
    t0 = time.perf_counter()
    if only:
        if not any(d["contract_id"] == only for d in _definitions(conn)):
            raise HttpError(404, f"no contract {only}")
        contracts.register(conn)
        results = [contracts.run_one(conn, only, store=True)]
    else:
        results = contracts.run_all(conn, store=True)
    ms = (time.perf_counter() - t0) * 1000
    hist = _history(conn)
    items = [_shape(d, hist.get(d["contract_id"], [])) for d in _definitions(conn)]
    items.sort(key=lambda c: (0 if c["status"] == "FAIL" else 1, SEV_ORDER.get(c["severity"], 9), c["contract_id"]))
    return {"ran": len(results), "violations": sum(r["violations"] for r in results), "ms": round(ms, 1),
            "summary": _summary(items), "contracts": items}


@get(r"^/api/contracts/violations/([A-Z0-9-]+)$")
def violations(req):
    conn = req.conn
    cid = req.params[0]
    defn = next((d for d in _definitions(conn) if d["contract_id"] == cid), None)
    if defn is None:
        raise HttpError(404, f"no contract {cid}")
    t0 = time.perf_counter()
    cur = conn.execute(f"SELECT * FROM ({defn['check_sql']}) LIMIT {MAX_ROWS + 1}")
    cols = [c[0] for c in cur.description]
    rows = cur.fetchall()
    ms = (time.perf_counter() - t0) * 1000
    return {"contract": defn, "columns": cols, "rows": rows[:MAX_ROWS], "truncated": len(rows) > MAX_ROWS,
            "count": len(rows[:MAX_ROWS]), "ms": round(ms, 1)}


@get(r"^/api/contracts/recon$")
def recon(req):
    t0 = time.perf_counter()
    out = reconcile.all_recons(req.conn)
    return {"recons": out, "ms": round((time.perf_counter() - t0) * 1000, 1),
            "summary": {s: sum(1 for r in out if r["status"] == s) for s in ("MATCH", "EXPLAINED", "OPEN")}}
