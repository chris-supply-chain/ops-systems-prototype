"""Closed Loop: each loop's sense -> decide -> act -> learn stages with live counts,
the decisions waiting on a human, execution, and the audit trail of every write."""
import json

from ops.api.router import HttpError, get, post
from ops.db import q, q1, val
from ops.logic import decisions as dec

LOOPS = {
    "QUALITY": {
        "name": "Quality loop", "question": "A defect shows up in the field. How fast is it contained and paid for?",
        "stages": [
            ("Sense", "CRM cases land raw, the classifier codes the symptom, genealogy traces it to a lot",
             ["raw_warranty_case", "warranty_claim", "genealogy", "lot_link"]),
            ("Decide", "Claims cluster on one batch above baseline: the rule proposes containment with the scope",
             ["ops_exception", "decision_log"]),
            ("Act", "Holds at the 3PL and pack line, an 8D to the supplier, CAPA, a chargeback, and a debit memo in the ERP",
             ["hold", "outbound_message", "capa", "chargeback", "erp_journal_entry"]),
            ("Learn", "Recoveries and PPM roll into the supplier scorecard that sourcing reads",
             ["supplier_scorecard", "rfq_quote"]),
        ]},
    "SUPPLY": {
        "name": "Supply loop", "question": "A supplier slips. Does the line stop, or does the system move first?",
        "stages": [
            ("Sense", "Promise dates arrive by EDI, portal, email and Excel; MRP nets them against the MPS",
             ["raw_supplier_confirmation", "raw_email", "po_promise_history", "mrp_run"]),
            ("Decide", "A shortage inside lead time is a line-stop risk: expedite, split, or use an approved alternate",
             ["mrp_message", "ops_exception", "decision_log"]),
            ("Act", "PO line split and pulled in, air booked, the deviation extended, MRP re-run",
             ["po_line", "deviation", "outbound_message", "planned_order"]),
            ("Learn", "Tier-2 commits and promise slips feed the scorecard and the next forecast release",
             ["supplier_commit", "supplier_scorecard", "forecast_release"]),
        ]},
    "PROMISE": {
        "name": "Promise loop", "question": "The ocean leg slips. Do customers hear it from us first?",
        "stages": [
            ("Sense", "Carrier EDI 315 milestones and ETA updates land raw and move the shipment",
             ["raw_carrier_event", "shipment_event", "shipment"]),
            ("Decide", "ATP re-run against current ETAs finds promises it can no longer keep",
             ["order_promise", "ops_exception", "decision_log"]),
            ("Act", "New promise dates with the reason recorded, and a message queued per customer",
             ["customer_order", "order_promise", "outbound_message"]),
            ("Learn", "The first promise stays on record; the ATP backtest eval scores promise accuracy",
             ["eval_run", "eval_case"]),
        ]},
    "DATA": {
        "name": "Data loop", "question": "The CM changes something without telling us. What catches it, and how is it fixed?",
        "stages": [
            ("Sense", "Every CM MES message lands raw; anything the mapping cannot read is quarantined, not dropped",
             ["raw_cm_mes_event", "ingest_run", "ingest_step"]),
            ("Decide", "Contracts show the damage (wrong as-built drive units); the rule proposes a mapping change",
             ["contract_run", "ops_exception", "decision_log"]),
            ("Act", "New station + mapping version, quarantine replayed, gated by tests, evals and contracts",
             ["station", "mapping_version", "station_event", "genealogy", "change_review"]),
            ("Learn", "The eval and contract history show when the feed drifted and how fast it recovered",
             ["eval_run", "contract_run"]),
        ]},
}


def _count(c, table):
    try:
        return val(c, f"SELECT COUNT(*) FROM {table}") or 0
    except Exception:
        return None


@get(r"^/api/loop$")
def loop_overview(req):
    c = req.conn
    loops = []
    for key, spec in LOOPS.items():
        stages = []
        for name, what, tables in spec["stages"]:
            stages.append({"name": name, "what": what, "tables": [{"table": t, "rows": _count(c, t)} for t in tables]})
        pending = q(c, "SELECT * FROM decision_log WHERE loop=? AND status='PROPOSED' ORDER BY decision_id", (key,))
        history = q(c, "SELECT decision_id, title, status, decided_by, executed_at, impact_json, outcome_json, achieved_json"
                       " FROM decision_log"
                       " WHERE loop=? AND status!='PROPOSED' ORDER BY COALESCE(executed_at, proposed_at) DESC", (key,))
        loops.append({"key": key, "name": spec["name"], "question": spec["question"], "stages": stages,
                      "pending": [_decision(d) for d in pending], "history": [_decision(d) for d in history]})
    outbound = q(c, """SELECT o.*, d.loop FROM outbound_message o LEFT JOIN decision_log d USING(decision_id)
                       ORDER BY o.msg_id DESC LIMIT 60""")
    by_target = q(c, "SELECT target_system, COUNT(*) n FROM outbound_message GROUP BY 1 ORDER BY n DESC")
    return {"loops": loops, "outbound": outbound, "by_target": by_target,
            "counts": {"proposed": val(c, "SELECT COUNT(*) FROM decision_log WHERE status='PROPOSED'"),
                       "executed": val(c, "SELECT COUNT(*) FROM decision_log WHERE status='EXECUTED'"),
                       "outbound": val(c, "SELECT COUNT(*) FROM outbound_message")}}


def _decision(d):
    d = dict(d)
    for k in ("inputs_json", "impact_json", "outcome_json", "achieved_json"):
        if d.get(k):
            try:
                d[k.replace("_json", "")] = json.loads(d[k])
            except (TypeError, ValueError):
                pass
    return d


@get(r"^/api/loop/decision/([A-Z0-9-]+)$")
def decision_detail(req):
    c = req.conn
    did = req.params[0]
    d = q1(c, "SELECT * FROM decision_log WHERE decision_id=?", (did,))
    if not d:
        raise HttpError(404, f"no decision {did}")
    writes = {
        "hold": q(c, "SELECT hold_id, scope_type, serial, lot_id, site_id, placed_at FROM hold WHERE decision_id=? LIMIT 200", (did,)),
        "hold_count": val(c, "SELECT COUNT(*) FROM hold WHERE decision_id=?", (did,)),
        "outbound_message": q(c, "SELECT * FROM outbound_message WHERE decision_id=? ORDER BY msg_id LIMIT 100", (did,)),
        "outbound_count": val(c, "SELECT COUNT(*) FROM outbound_message WHERE decision_id=?", (did,)),
        "order_promise": q(c, "SELECT * FROM order_promise WHERE decision_id=? LIMIT 100", (did,)),
        "order_promise_count": val(c, "SELECT COUNT(*) FROM order_promise WHERE decision_id=?", (did,)),
        "chargeback": q(c, "SELECT * FROM chargeback WHERE decision_id=?", (did,)),
        "capa": q(c, "SELECT * FROM capa WHERE decision_id=?", (did,)),
        "change_review": q(c, "SELECT * FROM change_review WHERE decision_id=?", (did,)),
        "exceptions": q(c, "SELECT exception_id, title, status FROM ops_exception WHERE decision_id=?", (did,)),
    }
    audit_sql = (f"-- every row this decision wrote\nSELECT 'hold' AS tbl, COUNT(*) AS n FROM hold WHERE decision_id = '{did}'\n"
                 f"UNION ALL SELECT 'outbound_message', COUNT(*) FROM outbound_message WHERE decision_id = '{did}'\n"
                 f"UNION ALL SELECT 'order_promise', COUNT(*) FROM order_promise WHERE decision_id = '{did}'\n"
                 f"UNION ALL SELECT 'chargeback', COUNT(*) FROM chargeback WHERE decision_id = '{did}'\n"
                 f"UNION ALL SELECT 'capa', COUNT(*) FROM capa WHERE decision_id = '{did}'\n"
                 f"UNION ALL SELECT 'change_review', COUNT(*) FROM change_review WHERE decision_id = '{did}';")
    return {"decision": _decision(d), "writes": writes, "audit_sql": audit_sql}


@post(r"^/api/loop/execute$")
def execute(req):
    did = req.body.get("decision_id")
    actor = (req.body.get("actor") or "Ops lead").strip()[:60]
    if not did:
        raise HttpError(400, "decision_id is required")
    try:
        return dec.execute(req.conn, did, actor)
    except ValueError as e:
        raise HttpError(409, str(e))


@post(r"^/api/loop/reject$")
def reject(req):
    c = req.conn
    did = req.body.get("decision_id")
    reason = (req.body.get("reason") or "Rejected by reviewer").strip()[:300]
    d = q1(c, "SELECT status FROM decision_log WHERE decision_id=?", (did,))
    if not d:
        raise HttpError(404, f"no decision {did}")
    if d["status"] != "PROPOSED":
        raise HttpError(409, f"{did} is {d['status']}")
    from ops.db import now
    c.execute("UPDATE decision_log SET status='REJECTED', decided_by=?, executed_at=?, outcome_json=? WHERE decision_id=?",
              (req.body.get("actor") or "Ops lead", now(c), json.dumps({"reason": reason}), did))
    return {"decision_id": did, "status": "REJECTED"}


@post(r"^/api/loop/propose$")
def propose(req):
    """Re-run detection and let the rules propose anything new (e.g. after a reset or a data change)."""
    from ops.logic import exceptions
    exceptions.detect(req.conn)
    made = dec.propose(req.conn)
    return {"proposed": made}
