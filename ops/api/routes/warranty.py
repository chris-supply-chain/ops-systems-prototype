"""Warranty & Chargebacks: field claims traced to the failed lot and supplier, the
lot-level signal, and the chargeback lifecycle (draft, send, accept, post to ERP),
where every step is a decision_log entry with its writes.
"""
import datetime as dt
import json
import re

from ops.api.router import HttpError, get, post
from ops.api.routes.quality import outbound, record_decision, ts
from ops.dates import to_date
from ops.db import as_of, now, q, q1, val
from ops.logic.chargeback import BILLABLE, claim_lines, conflict, post_to_erp, terms_for, write_chargeback
from ops.logic.contracts import after_action
from ops.logic.exceptions import BASELINE_FLOOR, SIGNAL_RATIO
from ops.logic.genealogy import field_rate, months_in_service

OPEN_CB = ("DRAFT", "SENT", "ACCEPTED", "DISPUTED")


def _claims(c, where="1=1", params=()):
    rows = q(c, f"""
        SELECT w.claim_id, {ts('w.reported_at')} AS reported_at, w.serial, w.order_id, w.symptom, w.defect_code,
               dc.description AS defect_desc, w.failed_item_id, i.name AS failed_item_name, w.failed_serial,
               w.failed_lot_id, w.supplier_id, s.name AS supplier_name, w.cost_parts_usd, w.cost_labor_usd,
               w.cost_logistics_usd, (w.cost_parts_usd + w.cost_labor_usd + w.cost_logistics_usd) AS cost_usd,
               w.status, w.chargeback_id, cb.status AS cb_status, w.raw_id,
               json_extract(r.payload, '$.category') AS crm_category, json_extract(r.payload, '$.case_no') AS case_no,
               json_extract(r.payload, '$.parts_replaced') AS parts_json, o.ship_to_state, o.ship_to_region,
               {ts('o.delivered_at')} AS delivered_at
        FROM warranty_claim w
        LEFT JOIN defect_code dc ON dc.defect_code = w.defect_code
        LEFT JOIN item i ON i.item_id = w.failed_item_id
        LEFT JOIN supplier s ON s.supplier_id = w.supplier_id
        LEFT JOIN chargeback cb ON cb.chargeback_id = w.chargeback_id
        LEFT JOIN raw_warranty_case r ON r.raw_id = w.raw_id
        LEFT JOIN customer_order o ON o.order_id = w.order_id
        WHERE {where}
        ORDER BY reported_at DESC""", params)
    for r in rows:
        parts = json.loads(r.pop("parts_json") or "[]")
        r["parts_replaced"] = parts
        r["conflict"] = conflict(r["defect_code"], r["crm_category"], parts)
        r["billable"] = bool(r["supplier_id"] and r["status"] in BILLABLE and not r["chargeback_id"] and not r["conflict"])
        r["days_in_service"] = ((to_date(r["reported_at"]) - to_date(r["delivered_at"])).days
                                if r["delivered_at"] and r["reported_at"] else None)
    return rows


def _next_cb(c):
    nums = [int(m.group(1)) for r in c.execute("SELECT chargeback_id FROM chargeback").fetchall()
            for m in [re.match(r"CB-(\d+)$", r["chargeback_id"])] if m]
    return f"CB-{(max(nums) if nums else 0) + 1:04d}"


def _recovery(c, claim):
    if not claim["supplier_id"]:
        return 0.0
    return claim_lines(c, claim["supplier_id"], [claim["claim_id"]])[0][3]


# ---------------------------------------------------------------------------- summary

@get(r"^/api/warranty/summary$")
def summary(req):
    c = req.conn
    today = to_date(as_of(c))
    claims = _claims(c)
    valid = [x for x in claims if x["status"] != "REJECTED"]
    d30 = (today - dt.timedelta(days=30)).isoformat()
    d60 = (today - dt.timedelta(days=60)).isoformat()
    last30 = sum(1 for x in valid if x["reported_at"] >= d30)
    prev30 = sum(1 for x in valid if d60 <= x["reported_at"] < d30)
    delivered = val(c, "SELECT COUNT(*) FROM unit u JOIN item i ON i.item_id = u.item_id WHERE i.kind = 'VEHICLE'"
                       " AND u.status = 'DELIVERED'")
    unbilled = {}
    review = {}
    for x in valid:
        if x["billable"]:
            g = unbilled.setdefault(x["supplier_id"], {"supplier_id": x["supplier_id"], "supplier_name": x["supplier_name"],
                                                       "claims": 0, "recoverable_usd": 0.0, "oldest": x["reported_at"],
                                                       "codes": {}, "lots": {}})
            g["claims"] += 1
            g["recoverable_usd"] += _recovery(c, x)
            g["oldest"] = min(g["oldest"], x["reported_at"])
            g["codes"][x["defect_code"]] = g["codes"].get(x["defect_code"], 0) + 1
            if x["failed_lot_id"]:
                g["lots"][x["failed_lot_id"]] = g["lots"].get(x["failed_lot_id"], 0) + 1
        elif x["supplier_id"] and not x["chargeback_id"] and x["conflict"]:
            review.setdefault(x["supplier_id"], []).append(x["claim_id"])
    for g in unbilled.values():
        g["recoverable_usd"] = round(g["recoverable_usd"], 2)
        g["needs_review"] = review.get(g["supplier_id"], [])
    pending_dx = sum(1 for x in valid if x["supplier_id"] and x["status"] == "OPEN" and not x["chargeback_id"])
    cbs = q(c, "SELECT chargeback_id, status, amount_usd FROM chargeback")
    pipeline = {}
    for cb in cbs:
        p = pipeline.setdefault(cb["status"], {"status": cb["status"], "count": 0, "amount_usd": 0.0})
        p["count"] += 1
        p["amount_usd"] += cb["amount_usd"]
    gross = sum(x["cost_usd"] for x in valid)
    recovered = sum(cb["amount_usd"] for cb in cbs if cb["status"] == "POSTED")

    # the signal: capacity-fade claims per 1,000 pack-months in service, by the cell lot inside the pack. Per pack-month,
    # not per pack: a lot delivered last week has had a week to fail, one delivered in June has had three months.
    months = months_in_service(c)
    packs_by_lot = {}
    for r in c.execute("SELECT DISTINCT child_lot_id AS lot_id, parent_serial FROM genealogy WHERE child_item_id = 'CEL-21700'"):
        packs_by_lot.setdefault(r["lot_id"], set()).add(r["parent_serial"])
    lots = []
    for lot in q(c, f"""SELECT l.lot_id, l.supplier_id, {ts('l.received_at')} AS received_at, l.iqc_status,
                               (SELECT COUNT(*) FROM warranty_claim w WHERE w.failed_lot_id = l.lot_id
                                  AND w.defect_code = 'FLD-CAPFADE' AND w.status != 'REJECTED') AS claims
                        FROM lot l WHERE l.item_id = 'CEL-21700' ORDER BY l.received_at"""):
        fr = field_rate(lot["claims"], packs_by_lot.get(lot["lot_id"], ()), months)
        if fr["packs_in_service"]:
            lots.append(dict(lot, packs=fr["packs_in_service"], pack_months=fr["pack_months"], rate=fr["rate"] or 0.0))

    def per_1000(group):
        pm = sum(o["pack_months"] for o in group)
        return 1000.0 * sum(o["claims"] for o in group) / pm if pm else 0.0

    flagged = []
    for lot in lots:
        lot["baseline"] = per_1000([o for o in lots if o["lot_id"] != lot["lot_id"]])
        lot["flag"] = lot["claims"] >= 3 and lot["rate"] >= SIGNAL_RATIO * max(lot["baseline"], BASELINE_FLOOR)
        if lot["flag"]:
            flagged.append(lot)
    baseline = per_1000([lot for lot in lots if not lot["flag"]])

    # weekly claims: capacity fade against everything else
    weeks = {}
    for x in valid:
        wd = to_date(x["reported_at"])
        wk = (wd - dt.timedelta(days=wd.weekday())).isoformat()
        b = weeks.setdefault(wk, {"week": wk, "capfade": 0, "other": 0})
        b["capfade" if x["defect_code"] == "FLD-CAPFADE" else "other"] += 1
    wk_list = []
    if weeks:
        start = min(to_date(k) for k in weeks)
        d = start
        while d <= today:
            k = d.isoformat()
            wk_list.append(weeks.get(k, {"week": k, "capfade": 0, "other": 0}))
            d += dt.timedelta(days=7)

    # claims per 1,000 delivered vehicles by build week (encoded in the serial)
    bw = q(c, """SELECT substr(u.serial, 5, 5) AS build_week, COUNT(*) AS delivered FROM unit u JOIN item i ON i.item_id = u.item_id
                 WHERE i.kind = 'VEHICLE' AND u.status = 'DELIVERED' GROUP BY build_week ORDER BY build_week""")
    by_week_claims = {}
    for x in valid:
        k = x["serial"][4:9]
        by_week_claims[k] = by_week_claims.get(k, 0) + 1
    build = [{"build_week": r["build_week"], "delivered": r["delivered"], "claims": by_week_claims.get(r["build_week"], 0),
              "rate": 1000.0 * by_week_claims.get(r["build_week"], 0) / r["delivered"]}
             for r in bw if r["delivered"] >= 20]
    modes = {}
    for x in valid:
        m = modes.setdefault(x["defect_code"] or "UNCLASSIFIED", {"code": x["defect_code"], "desc": x["defect_desc"],
                                                                   "claims": 0, "cost_usd": 0.0, "supplier_id": x["supplier_id"]})
        m["claims"] += 1
        m["cost_usd"] += x["cost_usd"]
    return {
        "as_of": today.isoformat(),
        "kpis": {"claims_30d": last30, "claims_prev_30d": prev30, "claims_total": len(valid),
                 "rejected": len(claims) - len(valid), "delivered": delivered,
                 "per_1000": 1000.0 * len(valid) / delivered if delivered else None,
                 "recoverable_usd": round(sum(g["recoverable_usd"] for g in unbilled.values()), 2),
                 "recoverable_claims": sum(g["claims"] for g in unbilled.values()),
                 "open_cb_usd": round(sum(cb["amount_usd"] for cb in cbs if cb["status"] in OPEN_CB), 2),
                 "open_cb": sum(1 for cb in cbs if cb["status"] in OPEN_CB),
                 "recovered_usd": round(recovered, 2), "gross_cost_usd": round(gross, 2),
                 "pending_diagnosis": pending_dx},
        "unbilled": sorted(unbilled.values(), key=lambda g: -g["recoverable_usd"]),
        "pipeline": pipeline,
        "lots": lots, "flagged_lots": [f["lot_id"] for f in flagged], "baseline_rate": baseline,
        "weekly": wk_list, "build_weeks": build,
        "modes": sorted(modes.values(), key=lambda m: -m["claims"]),
    }


# ---------------------------------------------------------------------------- claims

@get(r"^/api/warranty/claims$")
def claims(req):
    rows = _claims(req.conn)
    return {"claims": rows}


@get(r"^/api/warranty/claims/([A-Za-z0-9-]+)$")
def claim(req):
    c = req.conn
    rows = _claims(c, "w.claim_id = ?", (req.params[0],))
    if not rows:
        raise HttpError(404, "no such claim")
    x = rows[0]
    raw = q1(c, f"SELECT raw_id, {ts('received_at')} AS received_at, payload, ingest_status, ingest_note"
                " FROM raw_warranty_case WHERE raw_id = ?", (x["raw_id"],))
    steps = q(c, f"SELECT seq, step, status, {ts('at')} AS at, detail FROM ingest_step WHERE source_ref = ? ORDER BY seq",
              (f"raw_warranty_case:{x['raw_id']}",))
    packs = q(c, f"""SELECT g.child_serial AS serial, g.child_item_id AS item_id, g.position, g.source,
                            {ts('g.installed_at')} AS installed_at, {ts('g.removed_at')} AS removed_at, g.removal_reason
                     FROM genealogy g WHERE g.parent_serial = ? AND g.relation = 'SHIPPED_WITH' ORDER BY g.installed_at""",
              (x["serial"],))
    lot = None
    if x["failed_lot_id"]:
        lot = q1(c, f"""SELECT l.lot_id, l.item_id, l.supplier_id, l.iqc_status, {ts('l.received_at')} AS received_at,
                               (SELECT COUNT(*) FROM warranty_claim w WHERE w.failed_lot_id = l.lot_id AND w.status != 'REJECTED') AS claims,
                               (SELECT COUNT(DISTINCT parent_serial) FROM genealogy WHERE child_lot_id = l.lot_id) AS packs,
                               (SELECT qty_inspected FROM quality_event WHERE lot_id = l.lot_id AND qe_id LIKE 'IQC-%') AS iqc_n,
                               (SELECT qty_defective FROM quality_event WHERE lot_id = l.lot_id AND qe_id LIKE 'IQC-%') AS iqc_bad
                        FROM lot l WHERE l.lot_id = ?""", (x["failed_lot_id"],))
    preview = None
    if x["supplier_id"]:
        line = claim_lines(c, x["supplier_id"], [x["claim_id"]])[0]
        preview = {"amount_usd": line[3], "math": line[2], "terms": terms_for(c, x["supplier_id"])}
    return {"claim": x, "raw": raw, "steps": steps, "packs": packs, "lot": lot, "recovery": preview}


# ---------------------------------------------------------------------------- chargebacks

@get(r"^/api/warranty/chargebacks$")
def chargebacks(req):
    c = req.conn
    today = to_date(as_of(c))
    rows = q(c, f"""SELECT cb.chargeback_id, cb.supplier_id, s.name AS supplier_name, cb.basis, cb.title, cb.amount_usd,
                           cb.status, {ts('cb.created_at')} AS created_at, {ts('cb.sent_at')} AS sent_at,
                           {ts('cb.responded_at')} AS responded_at, {ts('cb.posted_at')} AS posted_at, cb.debit_memo_no,
                           cb.je_id, cb.decision_id, cb.notes,
                           (SELECT COUNT(*) FROM chargeback_line l WHERE l.chargeback_id = cb.chargeback_id) AS lines
                    FROM chargeback cb JOIN supplier s ON s.supplier_id = cb.supplier_id
                    ORDER BY cb.created_at DESC""")
    for r in rows:
        terms = terms_for(c, r["supplier_id"])
        r["response_days"] = terms.get("response_days", 21)
        r["age_days"] = (today - to_date(r["sent_at"])).days if r["sent_at"] else None
        r["overdue"] = r["status"] == "SENT" and r["age_days"] is not None and r["age_days"] > r["response_days"]
    return {"chargebacks": rows}


@get(r"^/api/warranty/chargebacks/([A-Za-z0-9-]+)$")
def chargeback(req):
    c = req.conn
    cb_id = req.params[0]
    rows = [r for r in chargebacks(req)["chargebacks"] if r["chargeback_id"] == cb_id]
    if not rows:
        raise HttpError(404, "no such chargeback")
    cb = rows[0]
    lines = q(c, "SELECT line_no, ref_type, ref_id, description, amount_usd FROM chargeback_line WHERE chargeback_id = ?"
                 " ORDER BY line_no", (cb_id,))
    je, je_lines = None, []
    if cb["je_id"]:
        je = q1(c, f"SELECT je_id, doc_type, {ts('posted_at')} AS posted_at, supplier_id, memo, source_ref, status"
                   " FROM erp_journal_entry WHERE je_id = ?", (cb["je_id"],))
        je_lines = q(c, """SELECT l.line_no, l.gl_account, g.name AS gl_name, g.kind AS gl_kind, l.debit_usd, l.credit_usd,
                                  l.cost_center, l.memo FROM erp_journal_line l JOIN gl_account g ON g.gl_account = l.gl_account
                           WHERE l.je_id = ? ORDER BY l.line_no""", (cb["je_id"],))
    refs = [cb_id] + ([cb["debit_memo_no"]] if cb["debit_memo_no"] else [])
    msgs = q(c, f"""SELECT msg_id, target_system, message_type, ref, payload, {ts('created_at')} AS created_at, status, decision_id
                    FROM outbound_message WHERE ref IN ({','.join('?' * len(refs))}) ORDER BY created_at""", refs)
    decisions = q(c, f"""SELECT decision_id, loop, rule_id, title, status, decided_by, {ts('executed_at')} AS executed_at,
                                outcome_json FROM decision_log WHERE trigger_ref = ? OR decision_id = ? ORDER BY proposed_at""",
                  (cb_id, cb["decision_id"] or ""))
    claim_ids = [l["ref_id"] for l in lines if l["ref_type"] == "CLAIM"]
    cl = _claims(c, f"w.claim_id IN ({','.join('?' * len(claim_ids))})", claim_ids) if claim_ids else []
    return {"chargeback": cb, "lines": lines, "terms": terms_for(c, cb["supplier_id"]), "je": je, "je_lines": je_lines,
            "balanced": round(sum(l["debit_usd"] for l in je_lines), 2) == round(sum(l["credit_usd"] for l in je_lines), 2),
            "lines_total": round(sum(l["amount_usd"] for l in lines), 2), "messages": msgs, "decisions": decisions,
            "claims": cl}


def _bump(c, cb_id, allowed):
    cb = q1(c, """SELECT cb.*, s.name AS supplier_name FROM chargeback cb JOIN supplier s USING(supplier_id)
                  WHERE cb.chargeback_id = ?""", (cb_id,))
    if cb is None:
        raise HttpError(404, "no such chargeback")
    if cb["status"] not in allowed:
        raise HttpError(409, f"{cb_id} is {cb['status']}; this step needs {' or '.join(allowed)}")
    return cb


@post(r"^/api/warranty/chargebacks/draft$")
def draft(req):
    c = req.conn
    sup = req.body.get("supplier_id")
    if not sup:
        raise HttpError(400, "supplier_id is required")
    eligible = [x for x in _claims(c, "w.supplier_id = ?", (sup,)) if x["billable"]]
    wanted = req.body.get("claim_ids")
    if wanted:
        eligible = [x for x in eligible if x["claim_id"] in set(wanted)]
    if not eligible:
        raise HttpError(400, f"no billable claims for {sup} (diagnosed, not yet charged back, no classification conflict)")
    lines = claim_lines(c, sup, [x["claim_id"] for x in eligible])
    total = round(sum(l[3] for l in lines), 2)
    codes = {}
    lots = {}
    for x in eligible:
        codes[x["defect_desc"] or x["defect_code"]] = codes.get(x["defect_desc"] or x["defect_code"], 0) + 1
        if x["failed_lot_id"]:
            lots[x["failed_lot_id"]] = lots.get(x["failed_lot_id"], 0) + 1
    main = max(codes, key=codes.get)
    lot_txt = f" (lot {max(lots, key=lots.get)})" if lots else ""
    title = f"{main}: {len(eligible)} field claim{'s' if len(eligible) != 1 else ''}{lot_txt}"
    cb_id = _next_cb(c)
    did = record_decision(
        c, loop="QUALITY", rule_id="QUALITY-RECOVERY", trigger_ref=cb_id,
        title=f"Draft chargeback {cb_id} to {sup}: {len(eligible)} claims, ${total:,.2f}",
        rationale="Claims are diagnosed, traced to the supplier's part or lot, and priced by the supplier's recovery terms.",
        action=f"Write chargeback {cb_id} with one line per claim; hold for review before sending",
        decided_by=req.body.get("actor") or "SQE", inputs={"claims": [x["claim_id"] for x in eligible]},
        impact={"amount_usd": total}, outcome={"writes": {"chargeback": 1, "chargeback_line": len(lines),
                                                          "warranty_claim": len(eligible), "decision_log": 1}})
    write_chargeback(c, cb_id, sup, "WARRANTY", title, lines, "DRAFT", now(c), decision_id=did)
    after_action(c)
    return {"ok": True, "chargeback_id": cb_id, "amount_usd": total, "claims": len(eligible), "decision_id": did}


@post(r"^/api/warranty/chargebacks/([A-Za-z0-9-]+)/send$")
def send(req):
    c = req.conn
    cb = _bump(c, req.params[0], ("DRAFT",))
    t = now(c)
    terms = terms_for(c, cb["supplier_id"])
    due = (to_date(t) + dt.timedelta(days=terms.get("response_days", 21))).isoformat()
    c.execute("UPDATE chargeback SET status = 'SENT', sent_at = ? WHERE chargeback_id = ?", (t, cb["chargeback_id"]))
    did = record_decision(
        c, loop="QUALITY", rule_id="QUALITY-RECOVERY-SEND", trigger_ref=cb["chargeback_id"],
        title=f"Send {cb['chargeback_id']} to {cb['supplier_name']} (${cb['amount_usd']:,.2f})",
        rationale=f"Contract gives the supplier {terms.get('response_days', 21)} days to accept or dispute.",
        action="Publish the chargeback with its evidence to the supplier portal", decided_by=req.body.get("actor") or "SQE",
        outcome={"writes": {"chargeback": 1, "outbound_message": 1, "decision_log": 1}, "response_due": due})
    outbound(c, "SUPPLIER_PORTAL", "CHARGEBACK_NOTICE", cb["chargeback_id"],
             {"supplier": cb["supplier_id"], "amount_usd": cb["amount_usd"], "response_due": due}, did)
    after_action(c)
    return {"ok": True, "status": "SENT", "response_due": due, "decision_id": did}


@post(r"^/api/warranty/chargebacks/([A-Za-z0-9-]+)/accept$")
def accept(req):
    c = req.conn
    cb = _bump(c, req.params[0], ("SENT", "DISPUTED"))
    t = now(c)
    note = (cb["notes"] + " | " if cb["notes"] else "") + "Accepted in the supplier portal (simulated response)"
    c.execute("UPDATE chargeback SET status = 'ACCEPTED', responded_at = ?, notes = ? WHERE chargeback_id = ?",
              (t, note, cb["chargeback_id"]))
    did = record_decision(
        c, loop="QUALITY", rule_id="QUALITY-RECOVERY-ACCEPTED", trigger_ref=cb["chargeback_id"],
        title=f"{cb['supplier_name']} accepted {cb['chargeback_id']}",
        rationale="Supplier response recorded from the portal (simulated for the demo).",
        action="Mark the chargeback accepted so finance can post the debit memo",
        decided_by="Supplier portal (simulated)", outcome={"writes": {"chargeback": 1, "decision_log": 1}})
    after_action(c)
    return {"ok": True, "status": "ACCEPTED", "decision_id": did}


@post(r"^/api/warranty/chargebacks/([A-Za-z0-9-]+)/post$")
def post_erp(req):
    c = req.conn
    cb = _bump(c, req.params[0], ("ACCEPTED",))
    t = now(c)
    je_id = post_to_erp(c, cb["chargeback_id"], t)
    memo = q1(c, "SELECT debit_memo_no FROM chargeback WHERE chargeback_id = ?", (cb["chargeback_id"],))["debit_memo_no"]
    did = record_decision(
        c, loop="QUALITY", rule_id="QUALITY-RECOVERY-POST", trigger_ref=cb["chargeback_id"],
        title=f"Post debit memo {memo} for {cb['chargeback_id']} to the ERP",
        rationale="Accepted recovery reduces what we owe the supplier and offsets the cost it recovers.",
        action=f"Journal entry {je_id}: debit 2000 accounts payable, credit the recovery account",
        decided_by=req.body.get("actor") or "Finance", outcome={"writes": {"erp_journal_entry": 1, "erp_journal_line": 2,
                                                                           "chargeback": 1, "outbound_message": 1,
                                                                           "decision_log": 1}, "je_id": je_id})
    outbound(c, "ERP", "DEBIT_MEMO", cb["chargeback_id"], {"debit_memo": memo, "je_id": je_id,
                                                          "amount_usd": cb["amount_usd"]}, did, status="ACKED")
    after_action(c)
    return {"ok": True, "status": "POSTED", "je_id": je_id, "debit_memo_no": memo, "decision_id": did}
