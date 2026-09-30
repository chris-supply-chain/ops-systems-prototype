"""Supplier Loop: PO confirmations and promise dates (traced to the raw email, Excel row
or EDI segment they came from), forecast released to tiers 1-3 against supplier
commits, RFQs with total cost of ownership, ECOs and their real cut-in, pricing with
effectivity, and a scorecard computed from what actually happened.
"""
import datetime as dt
import json
import re

from ...logic import sourcing as S
from ...xlsx import read_xlsx
from ..router import HttpError, get
from ...db import as_of, now, q, q1, val

TS = S.TS
CHANNEL_DEFAULT = {"KES": "EDI855", "HDS": "EDI855", "CSP": "PORTAL", "TNM": "PORTAL", "FAP": "PORTAL",
                   "PNC": "EXCEL", "SMT": "EMAIL", "NRT": "EMAIL", "BAY": "EMAIL", "VPC": "EMAIL"}
PO_RX = re.compile(r"\b(4[57]\d{5})(?:-(\d{1,2}))?\b")
LINE_RX = re.compile(r"(?:line|partida|item|l[ií]nea)\s*#?\s*(\d{1,2})\b", re.I)

LINE_SQL = f"""
SELECT pl.po_id, pl.line_no, po.supplier_id, s.name AS supplier_name, s.tier, pl.item_id, i.name AS item_name, i.uom,
       pl.qty, pl.received_qty, pl.unit_price, ROUND(pl.qty * pl.unit_price, 2) AS line_value, pl.need_date,
       pl.promise_date, pl.confirm_status, pl.status, {TS.format('pl.confirmed_at')} AS confirmed_at,
       {TS.format('po.created_at')} AS po_created_at, po.ship_to_site_id, po.buyer,
       ROUND((julianday(pl.confirmed_at) - julianday(po.created_at)) * 24, 1) AS hours_to_confirm,
       ROUND((julianday(:now) - julianday(po.created_at)) * 24, 1) AS age_hours,
       (SELECT COUNT(*) FROM po_promise_history h WHERE h.po_id = pl.po_id AND h.line_no = pl.line_no) AS n_hist,
       (SELECT h.channel FROM po_promise_history h WHERE h.po_id = pl.po_id AND h.line_no = pl.line_no
         ORDER BY julianday(h.recorded_at) DESC, h.id DESC LIMIT 1) AS last_channel,
       (SELECT h.note FROM po_promise_history h WHERE h.po_id = pl.po_id AND h.line_no = pl.line_no
         ORDER BY julianday(h.recorded_at) DESC, h.id DESC LIMIT 1) AS last_note,
       (SELECT h.promise_date FROM po_promise_history h WHERE h.po_id = pl.po_id AND h.line_no = pl.line_no
         ORDER BY julianday(h.recorded_at), h.id LIMIT 1) AS first_promise,
       CAST(julianday(COALESCE(pl.promise_date, pl.need_date)) - julianday(pl.need_date) AS INTEGER) AS slip_days
FROM po_line pl
JOIN purchase_order po USING (po_id)
JOIN supplier s ON s.supplier_id = po.supplier_id
JOIN item i ON i.item_id = pl.item_id
WHERE (:scope = 'all' OR pl.status = 'OPEN')
  AND (:sup IS NULL OR po.supplier_id = :sup)
  AND (:item IS NULL OR pl.item_id = :item)
  AND (:po IS NULL OR pl.po_id = :po)
ORDER BY pl.need_date, pl.po_id, pl.line_no
"""


def _quarantined_acks(conn):
    """(po, line) -> what was received but could not be applied (EDI/portal acks, abstained emails)."""
    out = {}
    for r in q(conn, f"SELECT raw_id, supplier_id, {TS.format('received_at')} AS received_at, channel, payload, ingest_note"
                     " FROM raw_supplier_confirmation WHERE ingest_status = 'QUARANTINED'"):
        po = line = None
        if r["channel"] == "EDI855":
            seg = {s.split("*")[0]: s.split("*") for s in r["payload"].strip("~").split("~") if s}
            po = seg.get("BAK", [None] * 4)[3]
            line = int(seg["PO1"][1]) if "PO1" in seg else None
        else:
            try:
                p = json.loads(r["payload"])
                po, line = p.get("po"), p.get("line")
            except ValueError:
                pass
        if po:
            out.setdefault((po, line), []).append({"kind": "ack", "ref": f"raw_supplier_confirmation:{r['raw_id']}",
                                                   "channel": r["channel"], "received_at": r["received_at"],
                                                   "reason": r["ingest_note"], "supplier_id": r["supplier_id"]})
    for r in q(conn, f"SELECT raw_id, {TS.format('received_at')} AS received_at, from_addr, subject, body_text, ingest_note"
                     " FROM raw_email WHERE classified_as = 'PO_CONFIRMATION_TEXT' AND ingest_status = 'QUARANTINED'"):
        text = (r["subject"] or "") + "\n" + (r["body_text"] or "")
        m = PO_RX.search(text)
        if not m:
            continue
        line = int(m.group(2)) if m.group(2) else None
        if line is None:
            lm = LINE_RX.search(text)
            line = int(lm.group(1)) if lm else None
        out.setdefault((m.group(1), line), []).append({"kind": "email", "ref": f"raw_email:{r['raw_id']}", "channel": "EMAIL",
                                                        "received_at": r["received_at"], "reason": r["ingest_note"],
                                                        "from": r["from_addr"], "subject": r["subject"]})
    return out


def _lines(conn, scope="open", supplier=None, item=None, po=None):
    rows = q(conn, LINE_SQL, {"now": now(conn), "scope": scope, "sup": supplier, "item": item, "po": po})
    quar = _quarantined_acks(conn)
    expo = {(e["po_id"], e["line_no"]): e for e in S.price_exposure(conn)}
    for r in rows:
        r["channel"] = r["last_channel"] or CHANNEL_DEFAULT.get(r["supplier_id"], "EMAIL")
        flags = []
        if r["status"] == "OPEN" and r["promise_date"] and r["promise_date"] > r["need_date"]:
            flags.append("SLIPPED")
        if r["confirm_status"] == "UNCONFIRMED" and r["status"] == "OPEN" and (r["age_hours"] or 0) > 72:
            flags.append("UNCONFIRMED_72H")
        qs = quar.get((r["po_id"], r["line_no"]), []) + quar.get((r["po_id"], None), [])
        if any(x["kind"] == "ack" for x in qs):
            flags.append("ACK_QUARANTINED")
        if any(x["kind"] == "email" for x in qs):
            flags.append("EMAIL_ABSTAINED")
        e = expo.get((r["po_id"], r["line_no"]))
        if e:
            flags.append("PRICE_EXPOSURE")
            r["exposure_usd"] = e["exposure_usd"]
            r["effective_price"] = e["effective_price"]
        r["quarantined"] = qs
        r["flags"] = flags
    return rows


@get(r"^/api/suppliers/overview$")
def overview(req):
    c = req.conn
    lines = _lines(c, "open")
    today = as_of(c)
    slips = [l for l in lines if "SLIPPED" in l["flags"]]
    afe = q1(c, f"SELECT h.po_id, h.line_no, h.promise_date, {TS.format('h.recorded_at')} AS recorded_at, h.channel, h.note, h.raw_ref,"
                " pl.need_date, pl.item_id, pl.qty,"
                " (SELECT h2.promise_date FROM po_promise_history h2 WHERE h2.po_id = h.po_id AND h2.line_no = h.line_no"
                "   AND h2.id < h.id ORDER BY h2.id DESC LIMIT 1) AS prev_promise"
                " FROM po_promise_history h JOIN po_line pl USING (po_id, line_no)"
                " WHERE h.note LIKE '%AFE%' ORDER BY julianday(h.recorded_at), h.id LIMIT 1")
    hmi = next((l for l in lines if "ACK_QUARANTINED" in l["flags"]), None)
    expo = S.price_exposure(c)
    latest = q1(c, "SELECT release_id, released_at FROM forecast_release ORDER BY released_at DESC LIMIT 1")
    cov = q1(c, """SELECT SUM(COALESCE(sc.commit_qty, 0)) AS committed, SUM(f.qty) AS forecast,
                          COUNT(DISTINCT f.supplier_id) AS suppliers,
                          COUNT(DISTINCT CASE WHEN sc.commit_qty IS NOT NULL THEN f.supplier_id END) AS responded
                   FROM forecast_line f LEFT JOIN supplier_commit sc USING (release_id, supplier_id, item_id, week_start)
                   WHERE f.release_id = ? AND f.tier = 1""", (latest["release_id"],))
    silent = [r["name"] for r in q(c, """SELECT s.name FROM supplier s WHERE s.supplier_id IN (SELECT supplier_id FROM forecast_line)
                                         AND s.supplier_id NOT IN (SELECT supplier_id FROM supplier_commit) ORDER BY s.tier, s.name""")]
    return {
        "as_of": today,
        "kpis": {
            "open_lines": len(lines),
            "open_value": round(sum((l["qty"] - (l["received_qty"] or 0)) * l["unit_price"] for l in lines), 2),
            "unconfirmed": sum(1 for l in lines if l["confirm_status"] == "UNCONFIRMED"),
            "unconfirmed_72h": sum(1 for l in lines if "UNCONFIRMED_72H" in l["flags"]),
            "slipped": len(slips),
            "slip_days_avg": round(sum(l["slip_days"] for l in slips) / len(slips), 1) if slips else 0,
            "quarantined_inputs": sum(len(l["quarantined"]) for l in lines),
            "price_exposure_usd": round(sum(e["exposure_usd"] for e in expo), 2),
            "price_exposure_lines": len(expo),
            "tier1_commit_coverage": round(cov["committed"] / cov["forecast"], 4) if cov and cov["forecast"] else None,
            "latest_release": latest["release_id"],
            "silent_suppliers": silent,
        },
        "story": {"afe_slip": afe, "hmi_unconfirmed": {k: hmi[k] for k in ("po_id", "line_no", "item_id", "qty", "need_date",
                                                                               "supplier_id", "age_hours", "quarantined")} if hmi else None},
        "suppliers": q(c, "SELECT supplier_id, name, tier FROM supplier WHERE supplier_id IN (SELECT supplier_id FROM purchase_order)"
                          " ORDER BY name"),
        "items": q(c, "SELECT DISTINCT pl.item_id, i.name FROM po_line pl JOIN item i USING (item_id) ORDER BY pl.item_id"),
    }


@get(r"^/api/suppliers/lines$")
def lines(req):
    scope = req.arg("scope", "open")
    rows = _lines(req.conn, "all" if scope == "all" else "open", req.arg("supplier"), req.arg("item"))
    return {"rows": rows}


def _source(conn, ref):
    """Resolve a raw_ref to the document it came from, so every promise date is traceable."""
    if not ref or ":" not in ref:
        return None
    table, rid = ref.split(":", 1)
    try:
        rid = int(rid)
    except ValueError:
        return None
    if table == "raw_email":
        e = q1(conn, f"SELECT raw_id, from_addr, subject, {TS.format('received_at')} AS received_at, body_text, ingest_status,"
                     " ingest_note, classified_as FROM raw_email WHERE raw_id = ?", (rid,))
        return e and {"kind": "email", "ref": ref, "from": e["from_addr"], "subject": e["subject"],
                      "received_at": e["received_at"], "body": (e["body_text"] or "")[:1200], "status": e["ingest_status"],
                      "note": e["ingest_note"]}
    if table == "raw_attachment":
        a = q1(conn, f"SELECT a.attachment_id, a.filename, a.content, a.parse_status, a.parse_note, e.raw_id, e.from_addr,"
                     f" e.subject, {TS.format('e.received_at')} AS received_at FROM raw_attachment a"
                     " JOIN raw_email e USING (raw_id) WHERE a.attachment_id = ?", (rid,))
        if not a:
            return None
        return {"kind": "excel", "ref": ref, "filename": a["filename"], "from": a["from_addr"], "subject": a["subject"],
                "received_at": a["received_at"], "email_ref": f"raw_email:{a['raw_id']}", "status": a["parse_status"],
                "note": a["parse_note"], "_content": a["content"]}
    if table == "raw_supplier_confirmation":
        r = q1(conn, f"SELECT raw_id, supplier_id, channel, payload, ingest_status, ingest_note, {TS.format('received_at')}"
                     " AS received_at FROM raw_supplier_confirmation WHERE raw_id = ?", (rid,))
        return r and {"kind": "edi" if r["channel"] == "EDI855" else "portal", "ref": ref, "channel": r["channel"],
                      "payload": r["payload"], "status": r["ingest_status"], "note": r["ingest_note"],
                      "received_at": r["received_at"]}
    return None


def _excel_row(src, po, line):
    """Find the header row and the row for this PO line inside the supplier's workbook."""
    try:
        sheets = read_xlsx(src.pop("_content"))
    except Exception:
        return
    for name, rows in sheets:
        for i, row in enumerate(rows[:15]):
            cells = [str(c).lower() for c in row if isinstance(c, str)]
            if sum(1 for c in cells if "po" in c or "採購" in c or "line" in c or "項次" in c) >= 2:
                for k, r in enumerate(rows[i + 1:], start=i + 2):
                    if r and str(r[0]) == str(po) and len(r) > 1 and str(r[1]) == str(line):
                        src.update(sheet=name, header=[c for c in row], row=[c for c in r], header_row=i + 1, row_no=k)
                        return


@get(r"^/api/suppliers/po$")
def po_detail(req):
    c = req.conn
    po = req.arg("po")
    if not po:
        raise HttpError(400, "po is required")
    head = q1(c, f"SELECT po.*, {TS.format('po.created_at')} AS created_at_z, s.name AS supplier_name, s.tier, s.country,"
                 " st.name AS ship_to_name FROM purchase_order po JOIN supplier s USING (supplier_id)"
                 " JOIN site st ON st.site_id = po.ship_to_site_id WHERE po.po_id = ?", (po,))
    if not head:
        raise HttpError(404, f"PO {po} not found")
    rows = _lines(c, "all", po=po)
    for r in rows:
        hist = q(c, f"SELECT id, promise_date, promise_qty, {TS.format('recorded_at')} AS recorded_at, channel, raw_ref, note"
                    " FROM po_promise_history WHERE po_id = ? AND line_no = ? ORDER BY julianday(recorded_at), id",
                 (po, r["line_no"]))
        for h in hist:
            src = _source(c, h["raw_ref"])
            if src and src["kind"] == "excel":
                _excel_row(src, po, r["line_no"])
            if src:
                src.pop("_content", None)
            h["source"] = src
        r["history"] = hist
        r["receipts"] = q(c, f"SELECT receipt_id, {TS.format('received_at')} AS received_at, qty, lot_id, asn_no, site_id"
                             " FROM goods_receipt WHERE po_id = ? AND line_no = ? ORDER BY received_at", (po, r["line_no"]))
        r["invoices"] = q(c, "SELECT invoice_id, invoice_date, qty, unit_price, amount_usd, match_status, variance_usd, pay_status"
                             " FROM supplier_invoice WHERE po_id = ? AND line_no = ?", (po, r["line_no"]))
        for x in r["quarantined"]:
            src = _source(c, x["ref"])
            if src:
                src.pop("_content", None)
                x["source"] = src
    head["created_at"] = head.pop("created_at_z")
    return {"po": head, "lines": rows, "focus_line": req.arg("line", None, int)}


# ------------------------------------------------------------------ forecast to tiers 1-3

@get(r"^/api/suppliers/forecast$")
def forecast(req):
    c = req.conn
    releases = q(c, f"""SELECT r.release_id, {TS.format('r.released_at')} AS released_at, r.demand_basis,
                          (SELECT COUNT(*) FROM forecast_line f WHERE f.release_id = r.release_id) AS lines,
                          (SELECT COUNT(*) FROM supplier_commit s WHERE s.release_id = r.release_id) AS commits
                        FROM forecast_release r ORDER BY r.released_at""")
    if not releases:
        return {"releases": []}
    rel = req.arg("release") or releases[-1]["release_id"]
    rel_row = next((r for r in releases if r["release_id"] == rel), releases[-1])
    rel = rel_row["release_id"]
    cells = q(c, """SELECT f.supplier_id, f.tier, f.week_start, SUM(f.qty) AS qty, SUM(sc.commit_qty) AS commit_qty,
                           COUNT(sc.commit_qty) AS n_commit, COUNT(*) AS n
                    FROM forecast_line f LEFT JOIN supplier_commit sc USING (release_id, supplier_id, item_id, week_start)
                    WHERE f.release_id = ? GROUP BY f.supplier_id, f.tier, f.week_start ORDER BY f.week_start""", (rel,))
    sups = {r["supplier_id"]: r for r in q(c, """SELECT s.supplier_id, s.name, s.tier, s.country, s.commodity, s.parent_supplier_id,
                                                        p.name AS parent_name FROM supplier s
                                                 LEFT JOIN supplier p ON p.supplier_id = s.parent_supplier_id""")}
    items = {}
    for r in q(c, "SELECT DISTINCT f.supplier_id, f.item_id, i.uom FROM forecast_line f JOIN item i USING (item_id)"
                  " WHERE f.release_id = ?", (rel,)):
        items.setdefault(r["supplier_id"], []).append({"item_id": r["item_id"], "uom": r["uom"]})
    weeks = sorted({r["week_start"] for r in cells})
    grid = {}
    for r in cells:
        g = grid.setdefault(r["supplier_id"], {"supplier_id": r["supplier_id"], "tier": r["tier"], "cells": {}, "qty": 0.0,
                                               "commit": 0.0, "responded": False})
        g["cells"][r["week_start"]] = {"qty": r["qty"], "commit": r["commit_qty"],
                                       "cover": (r["commit_qty"] / r["qty"]) if r["commit_qty"] is not None and r["qty"] else None}
        g["qty"] += r["qty"] or 0
        if r["commit_qty"] is not None:
            g["commit"] += r["commit_qty"]
            g["responded"] = True
    ever = {r["supplier_id"] for r in q(c, "SELECT DISTINCT supplier_id FROM supplier_commit")}
    rows = []
    for sid, g in grid.items():
        s = sups[sid]
        g["ever_responded"] = sid in ever
        g.update(name=s["name"], country=s["country"], commodity=s["commodity"], parent=s["parent_supplier_id"],
                 parent_name=s["parent_name"], items=items.get(sid, []),
                 cover=(g["commit"] / g["qty"]) if g["responded"] and g["qty"] else None)
        near = [v for wk, v in sorted(g["cells"].items())[:8] if v["cover"] is not None]
        g["near_cover"] = min((v["cover"] for v in near), default=None)
        rows.append(g)
    rows.sort(key=lambda g: (g["tier"], g["parent"] or "", g["name"]))

    # early warning: near-term (8-week) commit coverage by release, tier-2 Microvolt vs tier-1 Pinecrest
    warn = q(c, """SELECT f.release_id, f.supplier_id, SUM(f.qty) AS qty, SUM(sc.commit_qty) AS commit_qty, COUNT(sc.commit_qty) AS n
                   FROM forecast_line f JOIN forecast_release r USING (release_id)
                   LEFT JOIN supplier_commit sc USING (release_id, supplier_id, item_id, week_start)
                   WHERE f.supplier_id IN ('MVS', 'PNC', 'KSM', 'NWC')
                     AND julianday(f.week_start) - julianday(date(r.released_at)) < 56
                   GROUP BY 1, 2 ORDER BY 1""")
    series = {}
    for r in warn:
        series.setdefault(r["supplier_id"], []).append(
            {"release_id": r["release_id"], "cover": (r["commit_qty"] / r["qty"]) if r["n"] and r["qty"] else None})
    rel_at = {r["release_id"]: r["released_at"] for r in releases}
    first_drop = {}
    for sid, pts in series.items():
        for p in pts:
            p["released_at"] = rel_at[p["release_id"]]
            if sid not in first_drop and p["cover"] is not None and p["cover"] < 0.9:
                first_drop[sid] = p
    slip = q1(c, f"SELECT h.po_id, h.line_no, {TS.format('h.recorded_at')} AS recorded_at, h.promise_date, h.note FROM po_promise_history h"
                 " WHERE h.note LIKE '%AFE%' ORDER BY julianday(h.recorded_at), h.id LIMIT 1")
    lead = None
    if slip and first_drop.get("MVS"):
        lead = round((S.to_date(slip["recorded_at"]) - S.to_date(first_drop["MVS"]["released_at"])).days / 7.0, 1)

    # forecast waterfall for one item across releases
    item = req.arg("item") or "BMS-B"
    wf = q(c, """SELECT f.release_id, f.week_start, SUM(f.qty) AS qty, SUM(sc.commit_qty) AS commit_qty
                 FROM forecast_line f LEFT JOIN supplier_commit sc USING (release_id, supplier_id, item_id, week_start)
                 WHERE f.item_id = ? GROUP BY 1, 2 ORDER BY 1, 2""", (item,))
    wf_weeks = sorted({r["week_start"] for r in wf})
    wf_rows = {}
    for r in wf:
        wf_rows.setdefault(r["release_id"], {})[r["week_start"]] = {"qty": r["qty"], "commit": r["commit_qty"]}
    item_row = q1(c, "SELECT item_id, name, uom, primary_supplier_id FROM item WHERE item_id = ?", (item,))
    return {
        "releases": releases, "release": rel_row, "weeks": weeks, "grid": rows,
        "warning": {"series": series, "first_drop": first_drop, "slip": slip, "lead_weeks": lead},
        "waterfall": {"item": item_row, "weeks": wf_weeks,
                      "rows": [{"release_id": k, "released_at": rel_at.get(k), "cells": v} for k, v in sorted(wf_rows.items())]},
        "forecast_items": q(c, "SELECT DISTINCT f.item_id, i.name, f.tier FROM forecast_line f JOIN item i USING (item_id)"
                              " ORDER BY f.tier, f.item_id"),
    }


# ------------------------------------------------------------------ RFQs, ECOs, pricing, scorecard

@get(r"^/api/suppliers/rfqs$")
def rfqs(req):
    return {"rfqs": S.rfq_analysis(req.conn)}


@get(r"^/api/suppliers/ecos$")
def ecos(req):
    c = req.conn
    today = as_of(c)
    rows = q(c, "SELECT e.*, oi.name AS old_name, ni.name AS new_name, pi.name AS parent_name FROM eco e"
                " LEFT JOIN item oi ON oi.item_id = e.old_item_id LEFT JOIN item ni ON ni.item_id = e.new_item_id"
                " LEFT JOIN item pi ON pi.item_id = e.parent_item_id ORDER BY e.created_at DESC")
    swap_items = [x for e in rows for x in (e["old_item_id"], e["new_item_id"]) if x]
    usage = {}
    if swap_items:
        marks = ",".join("?" * len(swap_items))
        for r in q(c, f"SELECT child_item_id, COUNT(*) AS edges, SUM(qty) AS qty, MIN(installed_at) AS first_use,"
                      f" MAX(installed_at) AS last_use FROM genealogy WHERE relation = 'INSTALLED' AND child_item_id IN ({marks})"
                      " GROUP BY child_item_id", swap_items):
            usage[r["child_item_id"]] = r
    for e in rows:
        e["bom_lines"] = q(c, "SELECT b.*, p.name AS parent_name, ch.name AS child_name FROM bom_line b"
                              " JOIN item p ON p.item_id = b.parent_item_id JOIN item ch ON ch.item_id = b.child_item_id"
                              " WHERE b.eco_id = ? ORDER BY b.parent_item_id", (e["eco_id"],))
        e["deviations"] = q(c, "SELECT deviation_id, title, qty_limit, qty_used, valid_from, valid_to, status FROM deviation"
                               " WHERE eco_id = ? OR item_id = ?", (e["eco_id"], e["old_item_id"] or "~"))
        eff = e["effective_date"]
        real = {}
        if e["old_item_id"] and e["new_item_id"] and eff:
            old, new = e["old_item_id"], e["new_item_id"]
            after = q1(c, "SELECT COUNT(*) AS n, SUM(qty) AS qty, MAX(installed_at) AS last FROM genealogy"
                          " WHERE relation = 'INSTALLED' AND child_item_id = ? AND installed_at >= ?", (old, eff))
            first_new = q1(c, f"SELECT parent_serial, {TS.format('installed_at')} AS installed_at FROM genealogy"
                              " WHERE relation = 'INSTALLED' AND child_item_id = ? ORDER BY julianday(installed_at), parent_serial LIMIT 1", (new,))
            it_old = q1(c, "SELECT serialized, lot_controlled, primary_supplier_id, std_cost, uom FROM item WHERE item_id = ?", (old,))
            if it_old["serialized"]:
                res = q1(c, "SELECT COUNT(*) AS n, GROUP_CONCAT(DISTINCT location_site_id) AS sites FROM unit"
                            " WHERE item_id = ? AND status = 'COMPONENT'", (old,))
                residual, where = res["n"], res["sites"]
            else:
                res = q1(c, "SELECT COALESCE(SUM(qty), 0) AS n, GROUP_CONCAT(DISTINCT site_id) AS sites FROM inventory_balance"
                            " WHERE item_id = ? AND owner = 'OEM'", (old,))
                residual, where = res["n"], res["sites"]
            p = S.price_on(c, old, it_old["primary_supplier_id"], eff)
            unit = p["unit_price"] if p else (it_old["std_cost"] or 0)
            real = {"old_uses_after": after["n"], "old_qty_after": after["qty"], "old_last_use": S.z(after["last"]),
                    "old_total_uses": (usage.get(old) or {}).get("edges", 0), "new_total_uses": (usage.get(new) or {}).get("edges", 0),
                    "cut_in_unit": first_new["parent_serial"] if first_new else None,
                    "cut_in_at": first_new["installed_at"] if first_new else None, "residual_old": residual,
                    "residual_where": where, "residual_value": round(residual * unit, 2), "old_unit_price": unit,
                    "serial_effectivity": e["effectivity_type"] == "SERIAL"}
        elif e["status"] in ("IN_REVIEW", "DRAFT", "APPROVED"):
            start = max(today, eff or today)
            end = (dt.date.fromisoformat(start) + dt.timedelta(weeks=13)).isoformat()
            parent = e["parent_item_id"]
            if parent and q1(c, "SELECT 1 FROM build_plan WHERE item_id = ? LIMIT 1", (parent,)):
                planned = val(c, "SELECT COALESCE(SUM(qty), 0) FROM build_plan WHERE item_id = ? AND plan_date >= ? AND plan_date < ?",
                              (parent, start, end))
                basis = f"{parent} builds in the 13 weeks from {start}"
            else:
                planned = val(c, "SELECT COALESCE(SUM(qty), 0) FROM build_plan WHERE plan_type = 'CM_COMMIT' AND plan_date >= ?"
                                 " AND plan_date < ?", (start, end))
                basis = f"vehicle builds in the 13 weeks from {start} (CM commit)"
            touched = q(c, "SELECT pl.po_id, pl.line_no, pl.item_id, pl.qty, pl.need_date FROM po_line pl"
                           " WHERE pl.status = 'OPEN' AND pl.item_id IN (SELECT child_item_id FROM bom_line WHERE parent_item_id = ?"
                           "   AND position = 'ENCLOSURE') AND pl.need_date >= ?", (parent, start)) if parent else []
            real = {"planned_units_13w": planned, "impact_13w": round(planned * (e["cost_delta"] or 0), 2),
                    "impact_annual": round(planned * 4 * (e["cost_delta"] or 0), 2), "basis": basis, "open_po_lines": touched}
        e["reality"] = real
    return {"ecos": rows}


@get(r"^/api/suppliers/pricing$")
def pricing(req):
    c = req.conn
    prices = q(c, "SELECT p.*, i.name AS item_name, i.uom, s.name AS supplier_name FROM price p JOIN item i USING (item_id)"
                  " JOIN supplier s USING (supplier_id) ORDER BY p.item_id, p.supplier_id, p.min_qty, p.eff_from")
    expo = S.price_exposure(c)
    return {"prices": prices, "exposure": expo, "exposure_total": round(sum(e["exposure_usd"] for e in expo), 2),
            "as_of": as_of(c)}


@get(r"^/api/suppliers/price-on$")
def price_on(req):
    c = req.conn
    item, sup = req.arg("item"), req.arg("supplier")
    day = req.arg("date") or as_of(c)
    qty = req.arg("qty", 0, float)
    if not item or not sup:
        raise HttpError(400, "item and supplier are required")
    hit = S.price_on(c, item, sup, day, qty)
    cands = q(c, "SELECT price_id, min_qty, unit_price, eff_from, eff_to, basis, source_ref,"
                 " (eff_from <= :d AND (eff_to IS NULL OR eff_to > :d)) AS in_effect, (min_qty <= :q) AS qty_ok"
                 " FROM price WHERE item_id = :i AND supplier_id = :s ORDER BY eff_from, min_qty",
              {"d": day, "q": qty, "i": item, "s": sup})
    return {"item": item, "supplier": sup, "date": day, "qty": qty, "price": hit, "candidates": cands}


@get(r"^/api/suppliers/scorecard$")
def scorecard(req):
    return {"rows": S.scorecard(req.conn), "as_of": as_of(req.conn)}


@get(r"^/api/suppliers/supplier$")
def supplier(req):
    c = req.conn
    sid = req.arg("id")
    s = q1(c, "SELECT s.*, st.name AS site_name, st.city, p.name AS parent_name FROM supplier s"
              " LEFT JOIN site st ON st.site_id = s.site_id LEFT JOIN supplier p ON p.supplier_id = s.parent_supplier_id"
              " WHERE s.supplier_id = ?", (sid,))
    if not s:
        raise HttpError(404, f"supplier {sid} not found")
    s["recovery_terms"] = S.recovery_terms(s["recovery_terms"])
    subs = q(c, """WITH RECURSIVE sub(supplier_id, lvl) AS (
                      SELECT supplier_id, 1 FROM supplier WHERE parent_supplier_id = ?
                      UNION ALL SELECT s.supplier_id, sub.lvl + 1 FROM supplier s JOIN sub ON s.parent_supplier_id = sub.supplier_id)
                   SELECT s.supplier_id, s.name, s.tier, s.commodity, s.country, sub.lvl FROM sub JOIN supplier s USING (supplier_id)
                   ORDER BY sub.lvl, s.name""", (sid,))
    sc = next((r for r in S.scorecard(c) if r["supplier_id"] == sid), None)
    return {
        "supplier": s, "sub_tier": subs,
        "avl": q(c, "SELECT si.*, i.name FROM supplier_item si JOIN item i USING (item_id) WHERE si.supplier_id = ?", (sid,)),
        "open_lines": [l for l in _lines(c, "open", supplier=sid)],
        "scorecard": sc,
        "chargebacks": q(c, "SELECT chargeback_id, title, basis, amount_usd, status, created_at FROM chargeback WHERE supplier_id = ?"
                            " ORDER BY created_at DESC", (sid,)),
        "stock": q(c, "SELECT item_id, stock_status, SUM(qty) AS qty, MAX(as_of) AS as_of, source FROM inventory_balance"
                      " WHERE owner = 'SUPPLIER' AND site_id = ? GROUP BY 1, 2, 5", (s["site_id"] or "~",)),
        "latest_commit": q1(c, """SELECT f.release_id, SUM(f.qty) AS qty, SUM(sc.commit_qty) AS commit_qty, COUNT(sc.commit_qty) AS n
                                  FROM forecast_line f LEFT JOIN supplier_commit sc USING (release_id, supplier_id, item_id, week_start)
                                  WHERE f.supplier_id = ? AND f.release_id = (SELECT MAX(release_id) FROM forecast_release)
                                  GROUP BY 1""", (sid,)),
    }
