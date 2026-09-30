"""Process Lab: process mining over process_event (variants, DFG, glue, projection)."""
from ...db import q
from ...logic import process as pm
from ..router import HttpError, get


def _categories(conn, proc):
    return {r["activity"]: r["category"] for r in q(conn, "SELECT activity, category FROM process_activity WHERE process=?", (proc,))}


@get(r"^/api/process/list$")
def list_processes(req):
    rows = q(req.conn, "SELECT process, COUNT(DISTINCT case_id) AS cases, COUNT(*) AS events, MIN(ts) AS first, MAX(ts) AS last"
                       " FROM process_event GROUP BY process ORDER BY cases DESC")
    for r in rows:
        r["title"] = pm.TITLES.get(r["process"], r["process"])
    return {"processes": rows}


@get(r"^/api/process/([A-Z_]+)$")
def analyze(req):
    proc = req.params[0]
    rows = q(req.conn, "SELECT case_id, activity, ts, actor_role FROM process_event WHERE process=?", (proc,))
    if not rows:
        raise HttpError(404, f"no events for process {proc}")
    cats = _categories(req.conn, proc)
    res = pm.analyze(rows, cats, proc)
    acts = {r["activity"]: r for r in q(req.conn, "SELECT activity, category, system, note FROM process_activity WHERE process=?",
                                        (proc,))}
    res["activities"] = acts
    # the projection that matters: only cases that actually touch a glue or wait step
    cases = pm.build_cases(rows)
    touched = {cid: evs for cid, evs in cases.items()
               if any(cats.get(e["activity"]) in ("GLUE", "WAIT") for e in evs)}
    res["projection_touched"] = {**pm.projection(touched, cats), "cases": len(touched)} if touched else None
    for v in res["variants"]:
        v["case_ids"] = v["case_ids"][:40]
    return res


@get(r"^/api/process/([A-Z_]+)/case/([A-Za-z0-9_\-]+)$")
def case(req):
    proc, cid = req.params
    rows = q(req.conn, "SELECT e.activity, e.ts, e.actor_role, a.category, a.system, a.note FROM process_event e"
                       " JOIN process_activity a ON a.process = e.process AND a.activity = e.activity"
                       " WHERE e.process=? AND e.case_id=? ORDER BY e.ts, e.id", (proc, cid))
    if not rows:
        raise HttpError(404, f"no case {cid}")
    prev = None
    for r in rows:
        t = pm.parse_ts(r["ts"])
        r["gap_h"] = pm.hours(prev, t) if prev else 0.0
        r["ts"] = t.strftime("%Y-%m-%dT%H:%M:%SZ")
        prev = t
    return {"process": proc, "case_id": cid, "events": rows,
            "cycle_h": pm.hours(pm.parse_ts(rows[0]["ts"]), pm.parse_ts(rows[-1]["ts"]))}
