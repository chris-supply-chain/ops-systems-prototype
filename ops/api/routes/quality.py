"""Quality Loop (QMS): incoming inspection, inline SPC and capability, deviations,
NCRs and CAPA, and holds, with the one write this page owns (releasing a hold).

Timestamps in the core arrive in more than one ISO shape ('Z', '+00:00', local
offsets); every query normalizes them to UTC 'Z' with strftime so ordering and
day bucketing are right.
"""
import datetime as dt
import json
import re

from ops.api.router import HttpError, get, post
from ops.db import as_of, now, q, q1, val
from ops.logic import spc
from ops.logic.contracts import after_action


def ts(col):
    return f"strftime('%Y-%m-%dT%H:%M:%SZ', {col})"


def _d(s):
    return dt.date.fromisoformat(s[:10])


# ---------------------------------------------------------------------------- shared helpers

def next_decision_id(conn):
    nums = [int(m.group(1)) for r in conn.execute("SELECT decision_id FROM decision_log").fetchall()
            for m in [re.match(r"D-(\d+)$", r["decision_id"])] if m]
    return f"D-{(max(nums) if nums else 100) + 1:04d}"


def record_decision(conn, *, loop, rule_id, trigger_ref, title, rationale, action, decided_by, outcome,
                    inputs=None, impact=None):
    """Every write this page makes is a decision in the audit trail (status EXECUTED, with its writes)."""
    did = next_decision_id(conn)
    t = now(conn)
    conn.execute("INSERT INTO decision_log VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (did, loop, rule_id, trigger_ref, title, rationale, json.dumps(inputs) if inputs else None, action,
                  json.dumps(impact) if impact else None, "EXECUTED", t, decided_by, t, json.dumps(outcome)))
    return did


def outbound(conn, target, mtype, ref, payload, decision_id, status="SENT"):
    conn.execute("INSERT INTO outbound_message(target_system, message_type, ref, payload, created_at, status, decision_id)"
                 " VALUES (?,?,?,?,?,?,?)", (target, mtype, ref, json.dumps(payload), now(conn), status, decision_id))


def first_pass(conn, site, code, offset_hours, since, lines=None):
    """First test of each unit at a station, bucketed by plant-local day: [{day, line, n, fails, codes}]."""
    rows = q(conn, f"""
        WITH firsts AS (
          SELECT se.serial, MIN(se.event_ts) first_ts
          FROM station_event se JOIN station s ON s.station_id = se.station_id
          WHERE s.site_id = ? AND s.code = ?
          GROUP BY se.serial)
        SELECT date(datetime(f.first_ts, '{offset_hours:+d} hours')) AS day, s.line, se.result, se.defect_code
        FROM firsts f
        JOIN station_event se ON se.serial = f.serial AND se.event_ts = f.first_ts
        JOIN station s ON s.station_id = se.station_id AND s.code = ? AND s.site_id = ?
        WHERE date(datetime(f.first_ts, '{offset_hours:+d} hours')) >= ?""", (site, code, code, site, since))
    out = {}
    for r in rows:
        if lines and r["line"] not in lines:
            continue
        k = (r["day"], r["line"])
        b = out.setdefault(k, {"day": r["day"], "line": r["line"], "n": 0, "fails": 0, "codes": {}})
        b["n"] += 1
        if r["result"] == "FAIL":
            b["fails"] += 1
            b["codes"][r["defect_code"] or "?"] = b["codes"].get(r["defect_code"] or "?", 0) + 1
    return sorted(out.values(), key=lambda b: (b["day"], b["line"]))


def _merge_days(buckets):
    out = {}
    for b in buckets:
        m = out.setdefault(b["day"], {"day": b["day"], "n": 0, "fails": 0, "codes": {}})
        m["n"] += b["n"]
        m["fails"] += b["fails"]
        for k, v in b["codes"].items():
            m["codes"][k] = m["codes"].get(k, 0) + v
    return [out[k] for k in sorted(out)]


# ---------------------------------------------------------------------------- summary

@get(r"^/api/quality/summary$")
def summary(req):
    c = req.conn
    today = _d(as_of(c))
    since90 = (today - dt.timedelta(days=90)).isoformat()
    iqc = q1(c, f"SELECT COUNT(*) n, SUM(result='ACCEPT') acc, SUM(result='CONDITIONAL') cond, SUM(result='REJECT') rej"
                f" FROM quality_event WHERE qe_id LIKE 'IQC-%' AND {ts('detected_at')} >= ?", (since90,))
    pending = val(c, "SELECT COUNT(*) FROM lot WHERE iqc_status='PENDING'")
    since21 = (today - dt.timedelta(days=21)).isoformat()
    s60 = _merge_days(first_pass(c, "CM-TXG", "S60", 8, since21))
    p50 = _merge_days(first_pass(c, "OEM-FRE", "P50", -7, since21))

    def fpy_window(days, k):
        cut = (today - dt.timedelta(days=k)).isoformat()
        n = sum(d["n"] for d in days if d["day"] > cut)
        f = sum(d["fails"] for d in days if d["day"] > cut)
        prev_n = sum(d["n"] for d in days if (today - dt.timedelta(days=2 * k)).isoformat() < d["day"] <= cut)
        prev_f = sum(d["fails"] for d in days if (today - dt.timedelta(days=2 * k)).isoformat() < d["day"] <= cut)
        return ((n - f) / n if n else None), ((prev_n - prev_f) / prev_n if prev_n else None)

    s60_fpy, s60_prev = fpy_window(s60, 7)
    p50_fpy, p50_prev = fpy_window(p50, 7)
    ncr_open = val(c, "SELECT COUNT(*) FROM quality_event WHERE qe_id NOT LIKE 'IQC-%' AND status != 'CLOSED'")
    capa_open = val(c, "SELECT COUNT(*) FROM capa WHERE status != 'CLOSED'")
    devs = q(c, "SELECT deviation_id, valid_to, qty_limit, qty_used FROM deviation WHERE status='APPROVED' AND valid_to >= ?"
                " ORDER BY valid_to", (today.isoformat(),))
    holds = val(c, "SELECT COUNT(*) FROM hold WHERE released_at IS NULL")
    holds_units = val(c, "SELECT COUNT(DISTINCT serial) FROM hold WHERE released_at IS NULL AND serial IS NOT NULL")
    return {
        "as_of": today.isoformat(),
        "iqc": {"inspected": iqc["n"] or 0, "accepted": iqc["acc"] or 0, "conditional": iqc["cond"] or 0,
                "rejected": iqc["rej"] or 0, "pending": pending},
        "s60": {"fpy": s60_fpy, "prev": s60_prev, "spark": [((d["n"] - d["fails"]) / d["n"]) for d in s60 if d["n"]]},
        "p50": {"fpy": p50_fpy, "prev": p50_prev, "spark": [((d["n"] - d["fails"]) / d["n"]) for d in p50 if d["n"]]},
        "ncr_open": ncr_open, "capa_open": capa_open,
        "deviations_active": len(devs),
        "next_expiry": ({"id": devs[0]["deviation_id"], "valid_to": devs[0]["valid_to"],
                         "days": (_d(devs[0]["valid_to"]) - today).days,
                         "left": devs[0]["qty_limit"] - devs[0]["qty_used"]} if devs else None),
        "holds_active": holds, "holds_units": holds_units,
        "counts": {
            "iqc": val(c, "SELECT COUNT(*) FROM quality_event WHERE qe_id LIKE 'IQC-%'"),
            "deviations": val(c, "SELECT COUNT(*) FROM deviation"),
            "ncr": val(c, "SELECT COUNT(*) FROM quality_event WHERE qe_id NOT LIKE 'IQC-%'"),
            "holds": holds,
        },
    }


# ---------------------------------------------------------------------------- incoming inspection

@get(r"^/api/quality/iqc$")
def iqc(req):
    c = req.conn
    rows = q(c, f"""
        SELECT qe.qe_id, qe.item_id, i.name AS item_name, qe.lot_id, qe.supplier_id, s.name AS supplier_name,
               l.qty_received AS lot_qty, {ts('l.received_at')} AS received_at, {ts('qe.detected_at')} AS detected_at,
               {ts('qe.closed_at')} AS closed_at, qe.qty_inspected AS n, qe.qty_defective AS bad, qe.defect_code,
               dc.description AS defect_desc, qe.result, qe.disposition, qe.status, qe.root_cause, qe.ncr_no,
               qe.cost_usd, l.iqc_status
        FROM quality_event qe
        LEFT JOIN lot l ON l.lot_id = qe.lot_id
        LEFT JOIN item i ON i.item_id = qe.item_id
        LEFT JOIN supplier s ON s.supplier_id = qe.supplier_id
        LEFT JOIN defect_code dc ON dc.defect_code = qe.defect_code
        WHERE qe.qe_id LIKE 'IQC-%'
        ORDER BY received_at DESC""")
    by = {}
    for r in rows:
        b = by.setdefault(r["supplier_id"], {"supplier_id": r["supplier_id"], "supplier_name": r["supplier_name"],
                                             "lots": 0, "accepted": 0, "conditional": 0, "rejected": 0, "pending": 0,
                                             "items": set(), "last_received": None, "cost_usd": 0.0})
        b["lots"] += 1
        b["items"].add(r["item_id"])
        b["cost_usd"] += r["cost_usd"] or 0
        if r["status"] == "OPEN":
            b["pending"] += 1
        elif r["result"] == "ACCEPT":
            b["accepted"] += 1
        elif r["result"] == "CONDITIONAL":
            b["conditional"] += 1
        else:
            b["rejected"] += 1
        b["last_received"] = max(b["last_received"] or "", r["received_at"] or "")
    suppliers = []
    for b in sorted(by.values(), key=lambda b: -b["lots"]):
        judged = b["accepted"] + b["conditional"] + b["rejected"]
        b["accept_rate"] = b["accepted"] / judged if judged else None
        b["items"] = sorted(b["items"])
        suppliers.append(b)
    # the sampling plan actually applied: c = 0 (accept on zero defects), n by lot size
    plan = q(c, """SELECT qe.qty_inspected AS n, MIN(l.qty_received) AS lot_min, MAX(l.qty_received) AS lot_max,
                          COUNT(*) AS lots
                   FROM quality_event qe JOIN lot l ON l.lot_id = qe.lot_id
                   WHERE qe.qe_id LIKE 'IQC-%' GROUP BY qe.qty_inspected ORDER BY n""")
    # what IQC could not see: lots that passed and are now failing in the field
    latent = q(c, f"""
        SELECT w.failed_lot_id AS lot_id, l.item_id, l.supplier_id, l.iqc_status, COUNT(*) AS claims,
               MIN({ts('w.reported_at')}) AS first_claim, MAX({ts('w.reported_at')}) AS last_claim,
               (SELECT qty_inspected FROM quality_event WHERE lot_id = w.failed_lot_id AND qe_id LIKE 'IQC-%') AS n,
               (SELECT qty_defective FROM quality_event WHERE lot_id = w.failed_lot_id AND qe_id LIKE 'IQC-%') AS bad
        FROM warranty_claim w JOIN lot l ON l.lot_id = w.failed_lot_id
        WHERE w.status != 'REJECTED' AND l.origin = 'OEM_RECEIPT'
        GROUP BY w.failed_lot_id ORDER BY claims DESC""")
    highlights = []
    for r in rows:
        if r["result"] == "ACCEPT" or r["status"] == "OPEN":
            continue
        capa = q1(c, "SELECT capa_id, d_stage, status FROM capa WHERE qe_id = ?", (r["ncr_no"],)) if r["ncr_no"] else None
        cbs = q(c, """SELECT DISTINCT cb.chargeback_id, cb.status, cb.amount_usd FROM chargeback cb
                      LEFT JOIN chargeback_line cl ON cl.chargeback_id = cb.chargeback_id
                      WHERE cl.ref_id IN (?, ?) OR cb.title LIKE ?""",
                (r["qe_id"], r["ncr_no"] or "", f"%{r['lot_id']}%"))
        highlights.append({**r, "capa": capa, "chargebacks": cbs})
    pending = q(c, f"""SELECT l.lot_id, l.item_id, i.name AS item_name, l.supplier_id, l.qty_received,
                              {ts('l.received_at')} AS received_at
                       FROM lot l JOIN item i ON i.item_id = l.item_id WHERE l.iqc_status = 'PENDING'
                       ORDER BY received_at""")
    return {"suppliers": suppliers, "inspections": rows, "plan": plan, "latent": latent, "highlights": highlights,
            "pending": pending}


# ---------------------------------------------------------------------------- inline + SPC

EOL_CAUSE_CODES = ("EOL-FW", "EOL-MOTOR", "EOL-BRK")


@get(r"^/api/quality/inline$")
def inline(req):
    c = req.conn
    line = (req.arg("line", "ALL") or "ALL").upper()
    days = max(14, min(req.arg("days", 60, int), 150))
    today = _d(as_of(c))
    since = (today - dt.timedelta(days=days)).isoformat()
    lines = None if line == "ALL" else [line]
    buckets = first_pass(c, "CM-TXG", "S60", 8, since, lines)
    daily = _merge_days(buckets)
    chart = spc.pchart([{"key": d["day"], "n": d["n"], "fails": d["fails"], "codes": d["codes"]} for d in daily])
    # assignable causes on record: NCRs at the CM for an end-of-line defect, by plant-local day
    ncrs = q(c, f"""SELECT qe_id, defect_code, root_cause, status,
                           date(datetime({ts('detected_at')}, '+8 hours')) AS day
                    FROM quality_event WHERE qe_id LIKE 'NCR-%' AND site_id = 'CM-TXG'""")
    by_day = {}
    for n in ncrs:
        by_day.setdefault(n["day"], []).append(n)
    for p in chart["points"]:
        p["day"] = p.pop("key")
        cause = [n for n in by_day.get(p["day"], []) if n["defect_code"] in p["codes"]]
        p["cause"] = cause[0] if cause else None
    per_line = {}
    for b in buckets:
        per_line.setdefault(b["line"], {"n": 0, "fails": 0})
        per_line[b["line"]]["n"] += b["n"]
        per_line[b["line"]]["fails"] += b["fails"]

    # weekly defects per unit by line (CM L1, CM L2, pack line)
    weeks_since = (today - dt.timedelta(days=7 * 16)).isoformat()
    trend_rows = q(c, f"""
        SELECT s.site_id, s.line,
               date(datetime(se.event_ts, CASE WHEN s.site_id = 'CM-TXG' THEN '+8 hours' ELSE '-7 hours' END),
                    '-6 days', 'weekday 1') AS wk,
               SUM(se.result = 'FAIL') AS fails,
               COUNT(DISTINCT CASE WHEN s.code IN ('S10', 'P10') THEN se.serial END) AS started
        FROM station_event se JOIN station s ON s.station_id = se.station_id
        WHERE {ts('se.event_ts')} >= ?
        GROUP BY s.site_id, s.line, wk ORDER BY wk""", (weeks_since,))
    series = {"L1": [], "L2": [], "P1": []}
    for r in trend_rows:
        if r["started"] and r["line"] in series and r["wk"] < today.isoformat():
            series[r["line"]].append({"x": r["wk"], "y": r["fails"] / r["started"], "fails": r["fails"], "units": r["started"]})
    for k in series:
        series[k] = series[k][:-1] if series[k] and series[k][-1]["x"] >= (today - dt.timedelta(days=6)).isoformat() else series[k]

    since30 = (today - dt.timedelta(days=30)).isoformat()
    pareto = q(c, f"""SELECT se.defect_code, dc.description, dc.category, dc.default_responsible AS responsible,
                             MIN(s.site_id) AS site_id, COUNT(*) AS fails
                      FROM station_event se JOIN station s ON s.station_id = se.station_id
                      LEFT JOIN defect_code dc ON dc.defect_code = se.defect_code
                      WHERE se.result = 'FAIL' AND {ts('se.event_ts')} >= ?
                      GROUP BY se.defect_code ORDER BY fails DESC""", (since30,))
    total = sum(p["fails"] for p in pareto) or 1
    cum = 0
    for p in pareto:
        cum += p["fails"]
        p["cum_pct"] = cum / total

    # first-pass yield by station over the last 14 days, and rolled throughput yield per line
    since14 = (today - dt.timedelta(days=14)).isoformat()
    st = q(c, f"""
        WITH firsts AS (SELECT se.serial, se.station_id, MIN(se.event_ts) AS t FROM station_event se
                        WHERE {ts('se.event_ts')} >= ? GROUP BY se.serial, se.station_id)
        SELECT s.site_id, s.line, s.code, s.seq, s.name, COUNT(*) AS units, SUM(se.result = 'PASS') AS passed
        FROM firsts f JOIN station_event se ON se.serial = f.serial AND se.station_id = f.station_id AND se.event_ts = f.t
        JOIN station s ON s.station_id = f.station_id
        GROUP BY s.station_id ORDER BY s.site_id, s.line, s.seq""", (since14,))
    rty = {}
    for r in st:
        r["fpy"] = r["passed"] / r["units"] if r["units"] else None
        if r["fpy"] is not None:
            rty[r["line"]] = rty.get(r["line"], 1.0) * r["fpy"]
    return {"line": line, "days": days, "pchart": chart, "per_line": per_line, "trend": series, "pareto": pareto,
            "stations": st, "rty": rty}


def _plan_values(c, cp, since=None):
    """Measurements for one control-plan characteristic, oldest first (all lines of that station code)."""
    st = q1(c, "SELECT code, site_id FROM station WHERE station_id = ?", (cp["station_id"],))
    path = f"$.{cp['characteristic']}"
    where, params = "", []
    if cp["item_id"] in ("PK-STD", "PK-LRG"):
        where += " AND u.item_id = ?"
        params.append(cp["item_id"])
    if since:
        where += f" AND {ts('se.event_ts')} >= ?"
        params.append(since)
    return q(c, f"""SELECT {ts('se.event_ts')} AS t, se.serial, s.line, se.result,
                           CAST(json_extract(se.measurements, ?) AS REAL) AS v
                    FROM station_event se JOIN station s ON s.station_id = se.station_id
                    JOIN unit u ON u.serial = se.serial
                    WHERE s.code = ? AND s.site_id = ? AND se.measurements IS NOT NULL
                      AND json_extract(se.measurements, ?) IS NOT NULL {where}
                    ORDER BY t""", [path, st["code"], st["site_id"], path] + params)


@get(r"^/api/quality/capability$")
def capability(req):
    c = req.conn
    today = _d(as_of(c))
    since = (today - dt.timedelta(days=90)).isoformat()
    plans = q(c, """SELECT cp.*, s.code AS station_code, s.name AS station_name, i.name AS item_name
                    FROM control_plan cp LEFT JOIN station s ON s.station_id = cp.station_id
                    LEFT JOIN item i ON i.item_id = cp.item_id ORDER BY cp.site_id, cp.cp_id""")
    out = []
    for cp in plans:
        base = {k: cp[k] for k in ("cp_id", "site_id", "station_id", "station_code", "station_name", "item_id",
                                   "item_name", "characteristic", "unit", "lsl", "usl", "target", "method",
                                   "frequency", "reaction_plan")}
        if cp["station_id"] is None or (cp["lsl"] is None and cp["usl"] is None):
            out.append({**base, "kind": "ATTRIBUTE", "stats": None, "hist": [], "status": "NONE"})
            continue
        vals = _plan_values(c, cp, since)
        xs = [r["v"] for r in vals]
        st = spc.capability(xs, cp["lsl"], cp["usl"])
        lo = min([x for x in (st["min"], cp["lsl"], cp["target"]) if x is not None])
        hi = max([x for x in (st["max"], cp["usl"], cp["target"]) if x is not None])
        pad = (hi - lo) * 0.06 or 1.0
        out.append({**base, "kind": "VARIABLE", "stats": st, "status": spc.capability_status(st["cpk"]),
                    "hist": spc.histogram(xs, lo - pad, hi + pad, 26), "window_days": 90})
    return {"plans": out}


@get(r"^/api/quality/capability/([A-Z0-9-]+)$")
def capability_detail(req):
    c = req.conn
    cp = q1(c, """SELECT cp.*, s.code AS station_code, s.name AS station_name, i.name AS item_name
                  FROM control_plan cp LEFT JOIN station s ON s.station_id = cp.station_id
                  LEFT JOIN item i ON i.item_id = cp.item_id WHERE cp.cp_id = ?""", (req.params[0],))
    if cp is None:
        raise HttpError(404, "no such control plan row")
    if cp["station_id"] is None:
        return {"plan": cp, "points": [], "limits": None, "stats": None, "by_line": []}
    today = _d(as_of(c))
    vals = _plan_values(c, cp, (today - dt.timedelta(days=90)).isoformat())
    recent = vals[-160:]
    lim = spc.imr_limits([r["v"] for r in vals])
    pts = []
    for r in recent:
        flag = None
        if lim and (r["v"] > lim["ucl"] or r["v"] < lim["lcl"]):
            flag = "critical"
        pts.append({**r, "flag": flag,
                    "out_of_spec": (cp["lsl"] is not None and r["v"] < cp["lsl"]) or (cp["usl"] is not None and r["v"] > cp["usl"])})
    by_line = []
    for ln in sorted({r["line"] for r in vals}):
        xs = [r["v"] for r in vals if r["line"] == ln]
        s = spc.capability(xs, cp["lsl"], cp["usl"])
        by_line.append({"line": ln, **s, "status": spc.capability_status(s["cpk"])})
    st = spc.capability([r["v"] for r in vals], cp["lsl"], cp["usl"])
    return {"plan": cp, "points": pts, "limits": lim, "stats": st, "status": spc.capability_status(st["cpk"]),
            "by_line": by_line, "n_total": len(vals)}


# ---------------------------------------------------------------------------- deviations

@get(r"^/api/quality/deviations$")
def deviations(req):
    c = req.conn
    today = _d(as_of(c))
    rows = q(c, """SELECT d.*, i.name AS item_name, s.name AS supplier_name, e.title AS eco_title, st.name AS site_name
                   FROM deviation d JOIN item i ON i.item_id = d.item_id
                   LEFT JOIN supplier s ON s.supplier_id = d.supplier_id
                   LEFT JOIN eco e ON e.eco_id = d.eco_id
                   LEFT JOIN site st ON st.site_id = d.site_id
                   ORDER BY d.valid_to DESC""")
    for d in rows:
        vf, vt = _d(d["valid_from"]), _d(d["valid_to"])
        d["days_left"] = (vt - today).days
        d["remaining"] = d["qty_limit"] - d["qty_used"]
        d["effective_status"] = "EXPIRED" if d["status"] == "APPROVED" and vt < today else d["status"]
        d["quality_events"] = q(c, f"SELECT qe_id, kind, lot_id, result, disposition, {ts('detected_at')} AS detected_at"
                                   " FROM quality_event WHERE deviation_id = ?", (d["deviation_id"],))
        lot_ids = [e["lot_id"] for e in d["quality_events"] if e["lot_id"]]
        tz = "+8 hours" if d["site_id"] == "CM-TXG" else "-7 hours"
        if lot_ids:
            usage = q(c, f"""SELECT date(datetime(installed_at, '{tz}')) AS day, SUM(qty) AS qty, COUNT(*) AS units
                             FROM genealogy WHERE child_lot_id = ? GROUP BY day ORDER BY day""", (lot_ids[0],))
            basis = f"installs of lot {lot_ids[0]}"
        else:
            col = "child_serial" if val(c, "SELECT serialized FROM item WHERE item_id = ?", (d["item_id"],)) else "child_lot_id"
            usage = q(c, f"""SELECT date(datetime(installed_at, '{tz}')) AS day, SUM(qty) AS qty, COUNT(*) AS units
                             FROM genealogy WHERE child_item_id = ? AND {col} IS NOT NULL
                               AND date(datetime(installed_at, '{tz}')) >= ?
                               AND date(datetime(installed_at, '{tz}')) <= ?
                             GROUP BY day ORDER BY day""", (d["item_id"], d["valid_from"], min(d["valid_to"], today.isoformat())))
            basis = f"{d['item_id']} installed from {d['valid_from']}"
        d["usage"] = usage
        d["usage_basis"] = basis if usage else "usage reported by the site (not traceable to installs)"
        d["usage_traced"] = sum(u["units"] for u in usage)
    return {"as_of": today.isoformat(), "deviations": rows}


# ---------------------------------------------------------------------------- NCRs + CAPA

@get(r"^/api/quality/ncr$")
def ncr(req):
    c = req.conn
    today = _d(as_of(c))
    ncrs = q(c, f"""SELECT qe.qe_id, qe.kind, qe.site_id, qe.item_id, i.name AS item_name, qe.lot_id, qe.serial,
                           qe.supplier_id, s.name AS supplier_name, qe.defect_code, dc.description AS defect_desc,
                           qe.qty_inspected AS n, qe.qty_defective AS bad, qe.result, qe.disposition, qe.status,
                           {ts('qe.detected_at')} AS detected_at, {ts('qe.closed_at')} AS closed_at, qe.root_cause,
                           qe.ncr_no, qe.deviation_id, qe.cost_usd
                    FROM quality_event qe LEFT JOIN item i ON i.item_id = qe.item_id
                    LEFT JOIN supplier s ON s.supplier_id = qe.supplier_id
                    LEFT JOIN defect_code dc ON dc.defect_code = qe.defect_code
                    WHERE qe.qe_id NOT LIKE 'IQC-%' ORDER BY detected_at DESC""")
    for r in ncrs:
        r["ecos"] = sorted(set(re.findall(r"ECO-\d{4}", r["root_cause"] or "")))
    capas = q(c, f"""SELECT ca.*, {ts('ca.opened_at')} AS opened_utc, {ts('ca.closed_at')} AS closed_utc,
                            s.name AS supplier_name, qe.defect_code, qe.item_id, qe.lot_id
                     FROM capa ca LEFT JOIN supplier s ON s.supplier_id = ca.supplier_id
                     LEFT JOIN quality_event qe ON qe.qe_id = ca.qe_id ORDER BY ca.opened_at DESC""")
    for ca in capas:
        ca["opened_at"], ca["closed_at"] = ca.pop("opened_utc"), ca.pop("closed_utc")
        ca["overdue"] = ca["status"] != "CLOSED" and _d(ca["due_date"]) < today
        ca["days_to_due"] = (_d(ca["due_date"]) - today).days
    return {"ncrs": ncrs, "capas": capas}


# ---------------------------------------------------------------------------- holds

@get(r"^/api/quality/holds$")
def holds(req):
    c = req.conn
    rows = q(c, f"""SELECT h.hold_id, h.scope_type, h.serial, h.lot_id, h.site_id, st.name AS site_name, h.reason,
                           {ts('h.placed_at')} AS placed_at, {ts('h.released_at')} AS released_at, h.decision_id,
                           COALESCE(u.item_id, l.item_id) AS item_id, i.name AS item_name, u.status AS unit_status,
                           d.title AS decision_title
                    FROM hold h LEFT JOIN unit u ON u.serial = h.serial LEFT JOIN lot l ON l.lot_id = h.lot_id
                    LEFT JOIN item i ON i.item_id = COALESCE(u.item_id, l.item_id)
                    LEFT JOIN site st ON st.site_id = h.site_id
                    LEFT JOIN decision_log d ON d.decision_id = h.decision_id
                    ORDER BY h.released_at IS NOT NULL, placed_at DESC""")
    active = [r for r in rows if not r["released_at"]]
    by_reason = {}
    for r in active:
        key = r["decision_id"] or r["reason"].split(":")[0]
        g = by_reason.setdefault(key, {"key": key, "reason": r["reason"], "decision_id": r["decision_id"],
                                       "decision_title": r["decision_title"], "count": 0})
        g["count"] += 1
    return {"active": active, "released": [r for r in rows if r["released_at"]][:200],
            "groups": sorted(by_reason.values(), key=lambda g: -g["count"])}


@post(r"^/api/quality/holds/([A-Za-z0-9_.:-]+)/release$")
def release_hold(req):
    c = req.conn
    h = q1(c, "SELECT * FROM hold WHERE hold_id = ?", (req.params[0],))
    if h is None:
        raise HttpError(404, "no such hold")
    if h["released_at"]:
        raise HttpError(409, "hold already released")
    note = (req.body.get("note") or "").strip() or "Disposition complete"
    t = now(c)
    ref = h["serial"] or h["lot_id"]
    target = {"3PL-RNO": "WMS_3PL", "CM-TXG": "CM_MES", "OEM-FRE": "OEM_MES"}.get(h["site_id"], "WMS_3PL")
    writes = {"hold": 1, "outbound_message": 1}
    c.execute("UPDATE hold SET released_at = ? WHERE hold_id = ?", (t, h["hold_id"]))
    if h["serial"]:
        still = val(c, "SELECT COUNT(*) FROM hold WHERE serial = ? AND released_at IS NULL", (h["serial"],))
        if not still:
            c.execute("UPDATE unit SET on_hold = 0 WHERE serial = ?", (h["serial"],))
            writes["unit"] = 1
    did = record_decision(
        c, loop="QUALITY", rule_id="QUALITY-HOLD-RELEASE", trigger_ref=h["hold_id"],
        title=f"Release hold on {ref}", rationale=f"{note}. Original reason: {h['reason']}",
        action=f"Release {h['scope_type'].lower()} hold and instruct {target} to make it available",
        decided_by=req.body.get("actor") or "Quality engineer", outcome={"writes": writes, "note": note})
    outbound(c, target, "RELEASE_HOLD", ref, {"hold_id": h["hold_id"], "scope": h["scope_type"], "ref": ref, "note": note}, did)
    c.execute("UPDATE decision_log SET outcome_json = ? WHERE decision_id = ?",
              (json.dumps({"writes": {**writes, "decision_log": 1}, "note": note}), did))
    after_action(c)
    return {"ok": True, "hold_id": h["hold_id"], "released_at": t, "decision_id": did, "target_system": target}
