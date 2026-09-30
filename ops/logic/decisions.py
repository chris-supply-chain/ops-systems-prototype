"""The closed loop: sense -> decide -> act -> learn, inside the system.

`propose` turns open exceptions into decisions with a rationale, the evidence
used and the expected impact. `execute` carries a decision out as real writes:
holds, PO changes, promise updates, a new mapping version, chargebacks, journal
entries, and an outbound message for every other system that has to hear about
it. Each execution records exactly which rows it wrote, so the audit trail is
queryable in the Data Sandbox. A change to logic or mappings goes through the
same gate as a code change: tests, evals, contracts, then review.
"""
import datetime as dt
import json

from ..db import as_of as get_as_of, now as get_now


def _next_id(conn):
    n = conn.execute("SELECT COUNT(*) n FROM decision_log").fetchone()["n"]
    return f"D-{101 + n:04d}"


def _exists(conn, rule, ref):
    return conn.execute("SELECT decision_id FROM decision_log WHERE rule_id=? AND trigger_ref=? AND status IN ('PROPOSED','EXECUTED')",
                        (rule, ref)).fetchone()


def _insert(conn, loop, rule, ref, title, rationale, inputs, action, impact):
    did = _next_id(conn)
    conn.execute("INSERT INTO decision_log(decision_id, loop, rule_id, trigger_ref, title, rationale, inputs_json,"
                 " proposed_action, impact_json, status, proposed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 (did, loop, rule, ref, title, rationale, json.dumps(inputs), action, json.dumps(impact), "PROPOSED",
                  get_now(conn)))
    conn.execute("UPDATE ops_exception SET decision_id=? WHERE rule_id=? AND ref_id=? AND status!='RESOLVED'",
                 (did, _exc_rule(rule), ref))
    return did


def _exc_rule(rule):
    return {"QUALITY-LOT-CLUSTER": "QUALITY-LOT-CLUSTER", "PROMISE-SUPPLY-DELAY": "PROMISE-AT-RISK",
            "SUPPLY-LINE-STOP": "PLAN-LINE-STOP", "DATA-UNMAPPED-STATION": "DATA-QUARANTINE"}.get(rule, rule)


# ============================================================================ propose

def propose(conn):
    made = []
    from .chargeback import claim_lines
    from .genealogy import batch_signal, field_rate, months_in_service, recall_scope
    from .promises import at_risk
    for e in conn.execute("SELECT * FROM ops_exception WHERE status='OPEN' ORDER BY detected_at").fetchall():
        rule = e["rule_id"]
        if rule == "QUALITY-LOT-CLUSTER" and not _exists(conn, rule, e["ref_id"]):
            scope_ref = e["ref_id"]
            lot = conn.execute("""SELECT failed_lot_id, COUNT(*) n FROM warranty_claim WHERE defect_code='FLD-CAPFADE'
                                  AND (failed_lot_id=? OR failed_lot_id IN (SELECT child_lot_id FROM lot_link WHERE parent_lot_id=?))
                                  GROUP BY failed_lot_id ORDER BY n DESC LIMIT 1""", (scope_ref, scope_ref)).fetchone()["failed_lot_id"]
            sc = recall_scope(conn, scope_ref)
            siblings = [x for x in sc["lots"] if x != scope_ref]
            # the signal, fairly measured: packs built from the batch vs every other delivered pack, per month in service
            months = months_in_service(conn)
            batch, rest = batch_signal(conn, scope_ref, months)
            versus = (f"against {rest['rate']:.1f} for packs from every other batch ({rest['claims']} claims in "
                      f"{rest['pack_months']:,.0f} pack-months)" + (f", {batch['rate'] / rest['rate']:.0f}× the rate"
                                                                     if batch["rate"] else "")
                      if rest["rate"] else
                      f"against none in {rest['pack_months']:,.0f} pack-months for packs from every other batch")
            stock = sum(r["qty"] for r in sc["lot_stock"] if r["site_id"] == "OEM-FRE")
            affected = {u["serial"] for u in sc["in_control_units"]}
            kinds = {k: sum(1 for u in sc["in_control_units"] if u["kind"] == k) for k in ("PACK", "VEHICLE")}
            companions = {c for u in sc["in_control_units"] if u["kind"] == "VEHICLE"
                          for c in _kit_companions(conn, u["serial"])} - affected
            quiet = {}                  # sibling lots with no claims yet, and how long their packs have been in service
            for x in siblings:
                if x != lot:
                    packs = {r["parent_serial"] for r in conn.execute(
                        "SELECT DISTINCT parent_serial FROM genealogy WHERE child_lot_id=?", (x,))}
                    n = conn.execute("SELECT COUNT(*) n FROM warranty_claim WHERE failed_lot_id=? AND defect_code='FLD-CAPFADE'"
                                     " AND status != 'REJECTED'", (x,)).fetchone()["n"]
                    if not n:
                        quiet[x] = field_rate(0, packs, months)["pack_months"]
            young = (f" The lots with no claims yet ({', '.join(quiet)}) have {sum(quiet.values()):,.0f} pack-months in "
                     f"service between them, too few to show the defect," if quiet else " Its sibling lots fail too,")
            upstream = (f" Kestrel's certificates show {lot} was made from Northwind cathode batch {scope_ref}, which also went "
                        f"into {len(siblings) - 1} other cell lots ({', '.join(x for x in siblings if x != lot)}).{young} so the "
                        f"scope is the batch, not the lot." if scope_ref != lot else "")
            made.append(_insert(
                conn, "QUALITY", rule, scope_ref,
                (f"Contain cathode batch {scope_ref}: hold {sc['in_control']:,} units"
                 + (f" and {stock:,.0f} cells" if stock else "") + ", 8D with Kestrel, recover costs"
                 if scope_ref != lot else
                 f"Contain cell lot {lot}: hold {sc['in_control']:,} units, open an 8D with Kestrel, draft recovery")
                if sc["in_control"] or stock else
                f"Recover costs on {'cathode batch' if scope_ref != lot else 'cell lot'} {scope_ref}: 8D with Kestrel "
                f"(nothing left in our control to hold)",
                f"{batch['claims']} capacity-fade claims on packs built from this batch: {batch['rate'] or 0:.1f} per 1,000 "
                f"pack-months in service, {versus}. The cells passed incoming inspection, so the defect is latent (capacity "
                f"fade shows only in service).{upstream} Genealogy puts {sc['in_control']:,} affected units still in our "
                f"control and {sc['with_customer']:,} with customers.",
                {"lot": lot, "scope_ref": scope_ref, "sibling_lots": siblings, "claims": batch["claims"],
                 "per_1000_pack_months": batch["rate"], "baseline_per_1000_pack_months": rest["rate"],
                 "baseline_claims": rest["claims"], "packs_built_from_scope": sc["packs"],
                 "packs_in_service": batch["packs_in_service"], "cells_in_stock": stock},
                f"Hold every affected unit in our control ({kinds['PACK']} packs, {kinds['VEHICLE']} vehicles at the 3PL, "
                f"Fremont or in transit) plus the {len(companions)} packs kitted with those vehicles, so no kit ships split: "
                f"{len(affected) + len(companions)} serial holds. Put the {len(sc['lots'])} lots on hold and block their cells "
                "still in stock; send hold instructions to the 3PL WMS and the pack-line MES; request an 8D from Kestrel; "
                "open CAPA; draft a chargeback for claims + containment; queue a proactive service notice for customers with "
                "affected packs; re-run MRP so the blocked cells are replanned.",
                {"hold_units": sc["in_control"], "kit_companions": len(companions),
                 "serial_holds": len(affected) + len(companions), "lot_holds": len(sc["lots"]),
                 "customers_exposed": sc["with_customer"],
                 "recovery_estimate_usd": round(sum(x[3] for x in claim_lines(conn, "KES", _recoverable(conn, sc)))
                                                + 12.0 * sc["in_control"], 2)}))
        elif rule == "PROMISE-AT-RISK" and not _exists(conn, "PROMISE-SUPPLY-DELAY", "at_risk"):
            risk = at_risk(conn)
            if not risk:
                continue
            by_src = {}
            for r in risk:
                key = r["pegged_to"].split(" + ")[0] if r["pegged_to"] else "unpegged"
                by_src[key] = by_src.get(key, 0) + 1
            top = max(by_src.items(), key=lambda kv: kv[1])[0]
            avg = sum(r["slip_days"] for r in risk) / len(risk)
            made.append(_insert(
                conn, "PROMISE", "PROMISE-SUPPLY-DELAY", "at_risk",
                f"Re-promise {len(risk)} orders (+{avg:.1f} days on average) and tell customers first",
                f"ATP re-run against current carrier ETAs moves {len(risk)} open orders past their promise. The largest "
                f"cause is supply pegged to {top}. Telling customers before the date passes is cheaper than missing it quietly.",
                {"orders": len(risk), "avg_slip_days": round(avg, 1), "by_source": by_src},
                "Update promised dates to the new ATP dates (order_promise, reason SUPPLY_DELAY); queue a customer "
                "notification per order; leave the original promise on record for on-time reporting.",
                {"orders": len(risk), "max_slip_days": max(r["slip_days"] for r in risk)}))
        elif rule == "PLAN-LINE-STOP" and e["ref_id"] == "BMS-B" and not _exists(conn, "SUPPLY-LINE-STOP", "BMS-B"):
            plan = expedite_plan(conn)
            if not plan:
                continue
            made.append(_insert(
                conn, "SUPPLY", "SUPPLY-LINE-STOP", "BMS-B",
                f"Avoid the pack-line stop on {plan['stockout_day']}: air-expedite {plan['qty']} BMS boards and extend DEV-0012",
                f"BMS-B stock plus the rev-A boards still allowed under DEV-0012 run out on {plan['stockout_day']}. "
                f"Pinecrest pushed PO {plan['po_ref']} to {plan['promise']} (AFE IC allocation; tier-2 Microvolt committed "
                f"only ~70% for weeks before). The pack line would stop for {plan['gap_days']} workdays "
                f"(~{plan['packs_lost']} packs), which in turn delays about {plan['orders_hit']} customer promises.",
                plan, f"Split {plan['po_ref']}: {plan['qty']} boards by air arriving {plan['expedite_day']} "
                f"(${plan['air_usd']:,.0f} freight), balance on the current promise; extend DEV-0012 by 7 days for the "
                f"remaining rev-A allowance; re-run MRP to confirm the gap closes.",
                {"packs_protected": plan["packs_lost"], "air_freight_usd": plan["air_usd"],
                 "orders_protected": plan["orders_hit"]}))
        elif rule == "DATA-QUARANTINE" and not _exists(conn, "DATA-UNMAPPED-STATION", "S65"):
            q = conn.execute("SELECT COUNT(*) n FROM raw_cm_mes_event WHERE ingest_status='QUARANTINED'"
                             " AND ingest_note LIKE 'unknown station S65%'").fetchone()["n"]
            if not q:
                continue
            from .contracts import run_one
            wrong = run_one(conn, "C-GEN-05")["violations"]
            made.append(_insert(
                conn, "DATA", "DATA-UNMAPPED-STATION", "S65",
                f"Map the CM's new rework bay S65 and replay {q} quarantined messages",
                f"Since {conn.execute('SELECT MIN(received_at) t FROM raw_cm_mes_event WHERE ingest_note LIKE ' + chr(39) + 'unknown station S65%' + chr(39)).fetchone()['t'][:10]} "
                f"the CM has routed end-of-line reworks through a station it never told us about. Quarantine kept bad data "
                f"out, but {wrong} vehicles now carry the wrong drive unit in their as-built record (the EOL tester read a "
                f"different serial than genealogy holds). A recall on drive units would miss them.",
                {"quarantined": q, "vehicles_with_wrong_du": wrong},
                "Add stations TXG-L1-S65 and TXG-L2-S65 (REWORK), publish CM_MES mapping v2.1, replay the quarantine; "
                "gate: unit tests, evals EV-CM-MES and EV-GENEALOGY and contract C-GEN-05 must pass before the change is "
                "marked deployed.",
                {"messages": q, "as_built_corrections": wrong}))
    return made


def expedite_plan(conn):
    from .atp import promise_all
    from .mrp import run
    res = run(conn)
    bms = res["items"]["BMS-B"]
    if bms["first_short"] is None:
        return None
    as_of = dt.date.fromisoformat(res["as_of"])
    stock_out = as_of + dt.timedelta(days=bms["first_short"])
    line = conn.execute("""SELECT pl.po_id, pl.line_no, pl.qty, pl.promise_date, pl.need_date FROM po_line pl
                           WHERE pl.item_id='BMS-B' AND pl.status='OPEN' AND pl.promise_date IS NOT NULL
                           AND pl.promise_date > ? ORDER BY pl.promise_date LIMIT 1""", (stock_out.isoformat(),)).fetchone()
    if not line:
        return None
    promise = dt.date.fromisoformat(line["promise_date"])
    gap = [as_of + dt.timedelta(days=i) for i in range(bms["first_short"], (promise - as_of).days)]
    workdays = [d for d in gap if d.weekday() < 5]
    lost = int(sum(bms["gross"][(d - as_of).days] for d in gap if (d - as_of).days < len(bms["gross"])))
    qty = int(min(line["qty"], max(250, -(-lost // 250) * 250)))
    expedite_day = stock_out - dt.timedelta(days=1)
    orders_hit = 0
    try:
        from .mrp import constrained_pack_plan
        cons = constrained_pack_plan(conn, res)
        full = {k: dict(v) for k, v in res["mps"].items() if k in ("PK-STD", "PK-LRG")}
        with_short, _, _ = promise_all(conn, constrained=cons)
        without, _, _ = promise_all(conn, constrained={"plan": full})
        orders_hit = sum(1 for oid, r in with_short.items() if r["promise"] and without.get(oid, {}).get("promise")
                         and r["promise"] > without[oid]["promise"])
    except Exception:
        pass
    return {"item": "BMS-B", "stockout_day": stock_out.isoformat(), "po_ref": f"{line['po_id']}-{line['line_no']}",
            "po_id": line["po_id"], "line_no": line["line_no"], "promise": line["promise_date"], "qty": qty,
            "expedite_day": expedite_day.isoformat(), "gap_days": len(workdays), "packs_lost": lost,
            "air_usd": round(qty * 0.35 * 18 + 1250, 0), "orders_hit": orders_hit}


# ============================================================================ execute

def execute(conn, decision_id, actor="Ops lead"):
    d = conn.execute("SELECT * FROM decision_log WHERE decision_id=?", (decision_id,)).fetchone()
    if d is None:
        raise ValueError(f"no decision {decision_id}")
    if d["status"] != "PROPOSED":
        raise ValueError(f"{decision_id} is {d['status']}, not PROPOSED")
    fn = {"QUALITY-LOT-CLUSTER": _contain_lot, "PROMISE-SUPPLY-DELAY": _repromise, "SUPPLY-LINE-STOP": _expedite,
          "DATA-UNMAPPED-STATION": _map_station}[d["rule_id"]]
    conn.execute("SAVEPOINT exec_decision")
    try:
        outcome = fn(conn, d, actor)
    except Exception:
        conn.execute("ROLLBACK TO exec_decision")
        conn.execute("RELEASE exec_decision")
        raise
    status = "EXECUTED" if outcome.get("ok", True) else "REJECTED"
    if not outcome.get("ok", True):
        conn.execute("ROLLBACK TO exec_decision")
    conn.execute("RELEASE exec_decision")
    conn.execute("UPDATE decision_log SET status=?, decided_by=?, executed_at=?, outcome_json=? WHERE decision_id=?",
                 (status, actor, get_now(conn), json.dumps(outcome), decision_id))
    if status == "EXECUTED":
        conn.execute("UPDATE ops_exception SET status='ACKNOWLEDGED' WHERE decision_id=? AND status='OPEN'", (decision_id,))
    from . import contracts, exceptions
    exceptions.detect(conn)
    contracts.after_action(conn)
    return {"decision_id": decision_id, "status": status, **outcome}


def _out(conn, target, mtype, ref, payload, decision_id, status="SENT"):
    conn.execute("INSERT INTO outbound_message(target_system, message_type, ref, payload, created_at, status, decision_id)"
                 " VALUES (?,?,?,?,?,?,?)", (target, mtype, ref, json.dumps(payload), get_now(conn), status, decision_id))


def _scope_name(ref, capital=False):
    """'cathode batch CA2605-103' or 'cell lot CL2606-105': containment can be scoped to either."""
    kind = "cathode batch" if ref.startswith("CA") else "cell lot"
    return f"{kind.capitalize() if capital else kind} {ref}"


def _recoverable(conn, sc, supplier_id="KES"):
    """Claims in a recall scope the supplier can be billed for now, by the same rule as the Warranty page."""
    from .chargeback import billable_claim_ids
    return billable_claim_ids(conn, supplier_id, [c["claim_id"] for c in sc["claims"]])


def _kit_companions(conn, serial):
    """Units that ship in the same kit as a vehicle (its packs): held with it so a kit never ships split."""
    return [r["child_serial"] for r in conn.execute(
        "SELECT child_serial FROM genealogy WHERE parent_serial=? AND relation='SHIPPED_WITH' AND removed_at IS NULL",
        (serial,))]


def _contain_lot(conn, d, actor):
    from .chargeback import claim_lines, write_chargeback
    from .genealogy import recall_scope
    inputs = json.loads(d["inputs_json"] or "{}")
    lot = inputs.get("scope_ref") or d["trigger_ref"]
    did = d["decision_id"]
    now = get_now(conn)
    sc = recall_scope(conn, lot)
    writes = {"hold": 0, "unit": 0, "outbound_message": 0, "quality_event": 0, "capa": 0, "chargeback": 0,
              "chargeback_line": 0}
    held = {"3PL-RNO": [], "OEM-FRE": [], "IN_TRANSIT": []}
    for u in sc["in_control_units"]:
        serials = [u["serial"]] + (_kit_companions(conn, u["serial"]) if u["kind"] == "VEHICLE" else [])
        for s in serials:
            hid = f"HOLD-{did}-{s}"
            if conn.execute("SELECT 1 FROM hold WHERE hold_id=?", (hid,)).fetchone():
                continue
            site = u["location_site_id"]
            conn.execute("INSERT INTO hold(hold_id, scope_type, serial, site_id, reason, placed_at, decision_id)"
                         " VALUES (?,?,?,?,?,?,?)", (hid, "SERIAL", s, site, f"Containment: {_scope_name(lot)} capacity fade",
                                                     now, did))
            conn.execute("UPDATE unit SET on_hold=1 WHERE serial=?", (s,))
            writes["hold"] += 1
            writes["unit"] += 1
            held["IN_TRANSIT" if site is None else site if site in held else "IN_TRANSIT"].append(s)
    for lid in sc["lots"]:
        conn.execute("INSERT INTO hold(hold_id, scope_type, lot_id, reason, placed_at, decision_id) VALUES (?,?,?,?,?,?)",
                     (f"HOLD-{did}-{lid}", "LOT", lid, f"Containment: no further use (batch {lot})", now, did))
        writes["hold"] += 1
    blocked = conn.execute(f"UPDATE inventory_balance SET stock_status='BLOCKED' WHERE stock_status='AVAILABLE' AND lot_id IN"
                           f" ({','.join('?' * len(sc['lots']))})", sc["lots"]).rowcount
    writes["inventory_balance"] = blocked
    if held["3PL-RNO"]:
        _out(conn, "WMS_3PL", "HOLD_INSTRUCTION", lot, {"site": "RNO", "serials": held["3PL-RNO"],
                                                         "instruction": "Quarantine; do not kit or ship; re-kit allocated orders"}, did)
        writes["outbound_message"] += 1
    if held["OEM-FRE"]:
        _out(conn, "OEM_MES", "HOLD", lot, {"serials": held["OEM-FRE"]}, did)
        writes["outbound_message"] += 1
    if held["IN_TRANSIT"]:
        _out(conn, "WMS_3PL", "HOLD_ON_RECEIPT", lot, {"serials": held["IN_TRANSIT"]}, did)
        writes["outbound_message"] += 1
    qe_id = f"FLD-{lot}"
    conn.execute("INSERT INTO quality_event(qe_id, kind, site_id, item_id, lot_id, supplier_id, defect_code, qty_inspected,"
                 " qty_defective, result, disposition, status, detected_at, root_cause, ncr_no, cost_usd)"
                 " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (qe_id, "FIELD", "OEM-FRE", "CEL-21700", lot, "KES", "FLD-CAPFADE", sc["packs"], len(sc["claims"]),
                  "REJECT", "SORT", "CONTAINED", now, "Under investigation (8D requested)", f"NCR-{lot}",
                  12.0 * sc["in_control"]))
    writes["quality_event"] += 1
    conn.execute("INSERT INTO capa(capa_id, qe_id, supplier_id, title, d_stage, containment, owner, opened_at, due_date,"
                 " status, decision_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 (f"CAPA-{lot}", qe_id, "KES", f"Capacity fade on {_scope_name(lot)}", "D3",
                  f"{writes['hold']} holds placed; no further use of lot", "SQE", now,
                  (dt.date.fromisoformat(get_as_of(conn)) + dt.timedelta(days=30)).isoformat(), "CONTAINED", did))
    writes["capa"] += 1
    _out(conn, "SUPPLIER_PORTAL", "8D_REQUEST", lot, {"supplier": "KES", "lot": lot, "claims": len(sc["claims"]),
                                                      "due": "D3 containment in 48h, D4 root cause in 10 days"}, did)
    writes["outbound_message"] += 1
    from .mrp import run_and_store
    mrp_run = run_and_store(conn, triggered_by=f"decision {did}")
    writes["mrp_run"] = 1
    claim_ids = _recoverable(conn, sc)
    if claim_ids:
        lines = claim_lines(conn, "KES", claim_ids)
        lines.append(("COST", None, f"Containment: inspect/sort {sc['in_control']} held units at $12", 12.0 * sc["in_control"]))
        n = conn.execute("SELECT COUNT(*) n FROM chargeback").fetchone()["n"]
        cb_id = f"CB-{n + 1:04d}"
        write_chargeback(conn, cb_id, "KES", "WARRANTY", f"{_scope_name(lot, capital=True)}: capacity-fade claims + containment", lines,
                         "DRAFT", now, decision_id=did)
        writes["chargeback"] += 1
        writes["chargeback_line"] += len(lines)
    if sc["with_customer"]:
        _out(conn, "CUSTOMER_COMMS", "SERVICE_CAMPAIGN_DRAFT", lot,
             {"customers": sc["with_customer"], "message": "Proactive battery health check; replacement if below 80% capacity"},
             did, status="QUEUED")
        writes["outbound_message"] += 1
    short = conn.execute("SELECT item_id, bucket_date FROM mrp_message WHERE run_id=? AND message='SHORTAGE'", (mrp_run,)).fetchall()
    return {"writes": writes, "held_by_location": {k: len(v) for k, v in held.items()}, "scope": lot, "lots": sc["lots"],
            "mrp_run_id": mrp_run, "shortages_after": [dict(r) for r in short]}


def _repromise(conn, d, actor):
    from .promises import at_risk
    did = d["decision_id"]
    now = get_now(conn)
    risk = at_risk(conn)
    for r in risk:
        conn.execute("UPDATE customer_order SET promised_date=? WHERE order_id=?", (r["new_promise"], r["order_id"]))
        conn.execute("INSERT INTO order_promise(order_id, promised_date, reason, decided_at, pegged_to, decision_id)"
                     " VALUES (?,?,?,?,?,?)", (r["order_id"], r["new_promise"], "SUPPLY_DELAY", now, r["pegged_to"], did))
        _out(conn, "CUSTOMER_COMMS", "PROMISE_UPDATE", r["order_id"],
             {"old": r["promised"], "new": r["new_promise"], "reason": "Shipping delay on the ocean leg"}, did, status="QUEUED")
    proposed = json.loads(d["inputs_json"] or "{}").get("orders")
    note = None
    if proposed is not None and proposed != len(risk):
        note = (f"Proposed for {proposed} orders; {len(risk)} were at risk at execution "
                f"(supply changed in between, e.g. containment holds)")
    return {"writes": {"customer_order": len(risk), "order_promise": len(risk), "outbound_message": len(risk)},
            "orders": len(risk), "proposed_orders": proposed, "note": note}


def _expedite(conn, d, actor):
    from .mrp import run_and_store
    plan = json.loads(d["inputs_json"])
    did = d["decision_id"]
    now = get_now(conn)
    line = conn.execute("SELECT * FROM po_line WHERE po_id=? AND line_no=?", (plan["po_id"], plan["line_no"])).fetchone()
    qty = min(plan["qty"], line["qty"])
    new_no = conn.execute("SELECT MAX(line_no) m FROM po_line WHERE po_id=?", (plan["po_id"],)).fetchone()["m"] + 1
    conn.execute("UPDATE po_line SET qty=? WHERE po_id=? AND line_no=?", (line["qty"] - qty, plan["po_id"], plan["line_no"]))
    conn.execute("INSERT INTO po_line(po_id, line_no, item_id, qty, unit_price, price_id, need_date, promise_date,"
                 " confirm_status, confirmed_at, received_qty, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                 (plan["po_id"], new_no, line["item_id"], qty, line["unit_price"], line["price_id"], line["need_date"],
                  plan["expedite_day"], "CONFIRMED", now, 0, "OPEN"))
    conn.execute("INSERT INTO po_promise_history(po_id, line_no, promise_date, promise_qty, recorded_at, channel, note)"
                 " VALUES (?,?,?,?,?,?,?)", (plan["po_id"], new_no, plan["expedite_day"], qty, now, "EXPEDITE",
                                             f"Split from line {plan['line_no']}; air via SkyAxis (simulated supplier acceptance)"))
    conn.execute("INSERT INTO po_promise_history(po_id, line_no, promise_date, promise_qty, recorded_at, channel, note)"
                 " VALUES (?,?,?,?,?,?,?)", (plan["po_id"], plan["line_no"], line["promise_date"], line["qty"] - qty, now,
                                             "EXPEDITE", f"Balance after split ({qty} moved to line {new_no})"))
    dev = conn.execute("SELECT * FROM deviation WHERE deviation_id='DEV-0012'").fetchone()
    new_to = (dt.date.fromisoformat(dev["valid_to"]) + dt.timedelta(days=7)).isoformat()
    conn.execute("UPDATE deviation SET valid_to=? WHERE deviation_id='DEV-0012'", (new_to,))
    _out(conn, "SUPPLIER_PORTAL", "EXPEDITE_REQUEST", plan["po_ref"],
         {"split_qty": qty, "ship": "air", "deliver_by": plan["expedite_day"]}, did)
    _out(conn, "TMS", "AIR_BOOKING", plan["po_ref"], {"carrier": "SKA", "lane": "HSZ-SFO", "est_usd": plan["air_usd"]}, did)
    _out(conn, "OEM_MES", "DEVIATION_EXTENDED", "DEV-0012", {"valid_to": new_to}, did)
    run_id = run_and_store(conn, triggered_by=f"decision {did}")
    short = conn.execute("SELECT bucket_date, qty FROM mrp_message WHERE run_id=? AND item_id='BMS-B' AND message='SHORTAGE'",
                         (run_id,)).fetchone()
    return {"writes": {"po_line": 2, "po_promise_history": 2, "deviation": 1, "outbound_message": 3, "mrp_run": 1},
            "mrp_run_id": run_id, "shortage_after": dict(short) if short else None,
            "result": "gap closed" if not short else f"remaining gap from {short['bucket_date']}"}


def _map_station(conn, d, actor):
    from ..ingest.cm_mes import CmMes
    from ..ingest.common import Run
    from .contracts import run_one
    from .evals import run_suite
    did = d["decision_id"]
    now = get_now(conn)
    before = run_one(conn, "C-GEN-05")["violations"]
    for line in ("L1", "L2"):
        conn.execute("INSERT OR IGNORE INTO station VALUES (?,?,?,?,?,?,?,?,?)",
                     (f"TXG-{line}-S65", "CM-TXG", line, "S65", 65, "Rework bay (EOL returns)", "REWORK", 30.0, 2))
    v2 = conn.execute("SELECT spec_json FROM mapping_version WHERE source='CM_MES' AND version='v2'").fetchone()
    spec = json.loads(v2["spec_json"])
    spec["stations"]["S65"] = "S65"
    conn.execute("INSERT INTO mapping_version VALUES (?,?,?,?,?)",
                 ("CM_MES", "v2.1", now, json.dumps(spec, ensure_ascii=False), "Adds S65 rework bay (decision " + did + ")"))
    cr_id = f"CR-{15 + conn.execute('SELECT COUNT(*) n FROM change_review WHERE change_id >= ' + chr(39) + 'CR-0015' + chr(39)).fetchone()['n']:04d}"
    conn.execute("INSERT INTO change_review(change_id, title, component, kind, author, proposed_at, diff_summary,"
                 " checklist_json, status, decision_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (cr_id, "CM MES mapping v2.1: map rework bay S65", "ops.ingest.cm_mes", "MAPPING",
                  "Closed-loop proposal (reviewed by ops)", now,
                  "station master +2 rows (TXG-L1/L2-S65, REWORK); mapping_version v2.1 adds S65 -> S65; replay quarantine",
                  json.dumps(["unit tests", "EV-CM-MES >= 99.5%", "EV-GENEALOGY = 100%", "C-GEN-05 clean",
                              "reviewer approval"]), "OPEN", did))
    run = Run(conn, "CM_MES", now, "v2.1")
    mes = CmMes(conn, run)
    replayed = 0
    for r in conn.execute("SELECT raw_id, received_at, payload FROM raw_cm_mes_event WHERE ingest_status='QUARANTINED'"
                          " AND ingest_note LIKE 'unknown station S65%' ORDER BY received_at").fetchall():
        run.counts["in"] += 1
        if mes.process(r["raw_id"], r["received_at"], r["payload"], replay=True) == "REPLAYED":
            replayed += 1
    run.finish(now, f"replay after mapping v2.1 ({did})")
    from .state import derive
    derive(conn)
    after = run_one(conn, "C-GEN-05")["violations"]
    ev = run_suite(conn, "EV-CM-MES")
    ev_gen = run_suite(conn, "EV-GENEALOGY")          # the replay heals the as-built record the recall trace reads
    tests = conn.execute("SELECT run_id, failures, errors FROM test_run ORDER BY run_id DESC LIMIT 1").fetchone()
    gates = {"contract_C-GEN-05": after == 0, "eval_EV-CM-MES": ev["gate"] == "PASS",
             "eval_EV-GENEALOGY": ev_gen["gate"] == "PASS",
             "tests": bool(tests) and tests["failures"] == 0 and tests["errors"] == 0}
    ok = all(gates.values())
    conn.execute("UPDATE change_review SET eval_run_id=?, test_run_id=?, contracts_ok=?, reviewer=?, status=?, decided_at=?,"
                 " notes=? WHERE change_id=?",
                 (ev["run_id"], tests["run_id"] if tests else None, 1 if after == 0 else 0, actor,
                  "DEPLOYED" if ok else "CHANGES_REQUESTED", now,
                  f"Replayed {replayed}; C-GEN-05 {before} -> {after}; EV-CM-MES {ev['score']:.2%}; "
                  f"EV-GENEALOGY {ev_gen['score']:.2%}", cr_id))
    if ok:
        _out(conn, "CM_MES", "MAPPING_DEPLOYED", "CM_MES v2.1", {"version": "v2.1", "stations": ["S65"]}, did)
    return {"ok": ok, "gates": gates, "replayed": replayed, "c_gen_05_before": before, "c_gen_05_after": after,
            "eval_score": ev["score"], "change_review": cr_id,
            "writes": {"station": 2, "mapping_version": 1, "station_event": replayed, "genealogy": "as-built corrected",
                       "change_review": 1, "outbound_message": 1 if ok else 0}}
