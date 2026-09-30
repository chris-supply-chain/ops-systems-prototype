"""Exception detection: rules over the core that raise (and later auto-resolve)
ops_exception rows. Each exception says what is wrong, what it touches and where
to act, so the Control Tower is a queue rather than a wall of charts.
"""
import datetime as dt

from ..db import as_of as get_as_of, now as get_now


# what each rule's impact_units counts, so "7" reads as 7 messages, not 7 units
IMPACT_UNIT = {
    "QUALITY-LOT-CLUSTER": "units in our control", "QUALITY-DEVIATION": "parts covered",
    "QUALITY-DEVIATION-OVERRUN": "installs", "PLAN-LINE-STOP": "short", "MOVE-ETA-SLIP": "vehicles",
    "MOVE-SHORT-RECEIPT": "vehicles", "MOVE-CUSTOMS-EXAM": "vehicles", "DATA-QUARANTINE": "messages",
    "DATA-PROVISIONAL": "drive units", "INV-STRANDED": "drive units", "PROMISE-AT-RISK": "orders",
    "SOURCE-UNCONFIRMED": "PO lines", "SOURCE-PRICE-MISMATCH": "PO lines",
}


# a batch is a signal, not noise, when its claim rate per pack-month in service is at least SIGNAL_RATIO times the rest
# of the fleet's; the floor keeps a fleet with almost no claims from making any three claims look like a cluster
SIGNAL_RATIO, BASELINE_FLOOR = 5.0, 2.0          # so never flag below 10 claims per 1,000 pack-months


def _rules(conn):
    as_of = get_as_of(conn)
    now = get_now(conn)
    ex = []

    def add(rule, domain, sev, title, detail, ref_type, ref_id, units=None, usd=None, owner=None, route=None):
        ex.append({"exception_id": f"{rule}:{ref_id}", "rule_id": rule, "domain": domain, "severity": sev, "title": title,
                   "detail": detail, "ref_type": ref_type, "ref_id": ref_id, "impact_units": units,
                   "impact_unit": IMPACT_UNIT.get(rule, "units") if units is not None else None, "impact_usd": usd,
                   "owner": owner, "route": route})

    # QUALITY: field-claim cluster, grouped by the tier-2 batch the cell lots were made from
    clusters = {}
    for r in conn.execute("""SELECT w.failed_lot_id AS lot, COALESCE(ll.parent_lot_id, w.failed_lot_id) AS batch,
                                    w.cost_parts_usd + w.cost_labor_usd + w.cost_logistics_usd AS cost
                             FROM warranty_claim w
                             LEFT JOIN lot_link ll ON ll.child_lot_id = w.failed_lot_id
                               AND ll.parent_lot_id IN (SELECT lot_id FROM lot WHERE item_id='CAM-NMC')
                             WHERE w.failed_lot_id IS NOT NULL AND w.defect_code='FLD-CAPFADE' AND w.status != 'REJECTED'
                             AND w.reported_at >= strftime('%Y-%m-%dT%H:%M:%SZ', ?, '-30 days')""", (now,)):
        c = clusters.setdefault(r["batch"], {"n": 0, "cost": 0.0, "lots": {}})
        c["n"] += 1
        c["cost"] += r["cost"]
        c["lots"][r["lot"]] = c["lots"].get(r["lot"], 0) + 1
    months = None
    for batch, c in clusters.items():
        if c["n"] < 3:
            continue
        from .genealogy import batch_signal, months_in_service, recall_scope
        months = months_in_service(conn) if months is None else months
        mine, rest = batch_signal(conn, batch, months)
        if not mine["rate"] or mine["rate"] < SIGNAL_RATIO * max(rest["rate"] or 0.0, BASELINE_FLOOR):
            continue
        sc = recall_scope(conn, batch)
        lots = ", ".join(f"{k} ({v})" for k, v in sorted(c["lots"].items(), key=lambda kv: -kv[1]))
        add("QUALITY-LOT-CLUSTER", "QUALITY", "CRITICAL",
            f"Capacity-fade claims clustering on {'cathode batch ' if batch.startswith('CA') else 'cell lot '}{batch}",
            f"{c['n']} claims in 30 days (${c['cost']:,.0f}) on cell lots {lots}: {mine['rate']:.1f} per 1,000 pack-months "
            f"in service vs {rest['rate'] or 0:.1f} for the rest of the fleet; {sc['in_control']:,} units in our control, "
            f"{sc['with_customer']:,} with customers", "lot", batch, sc["in_control"], c["cost"], "SQE",
            f"#/genealogy?q={batch}")
    # MOVE: vessel delay against the plan
    for r in conn.execute("""SELECT vessel, voyage, COUNT(*) containers, SUM(asn_qty) units,
                                    MIN(eta_planned) planned, MAX(eta_current) current
                             FROM shipment WHERE leg='CM_TO_3PL' AND ata IS NULL AND eta_current > eta_planned
                             AND julianday(eta_current) - julianday(eta_planned) >= 2 GROUP BY vessel, voyage"""):
        days = (dt.datetime.fromisoformat(r["current"][:10]) - dt.datetime.fromisoformat(r["planned"][:10])).days
        add("MOVE-ETA-SLIP", "MOVE", "SERIOUS", f"{r['vessel']} {r['voyage']} now arriving {days} days late",
            f"{r['containers']} containers, {r['units']} vehicles; ETA {r['planned'][:10]} → {r['current'][:10]}",
            "sailing", f"{r['vessel']} {r['voyage']}", r["units"], None, "Logistics", "#/shipments")
    for r in conn.execute("""SELECT ce.entry_no, s.shipment_id, s.asn_qty FROM customs_entry ce JOIN shipment s USING(shipment_id)
                             WHERE ce.status='EXAM'"""):
        add("MOVE-CUSTOMS-EXAM", "MOVE", "WARNING", f"CBP exam holding {r['shipment_id']}",
            f"Entry {r['entry_no']}: CET intensive exam; {r['asn_qty']} vehicles waiting at Oakland", "shipment",
            r["shipment_id"], r["asn_qty"], None, "Logistics", f"#/shipments?id={r['shipment_id']}")
    for r in conn.execute("""SELECT s.shipment_id, COUNT(*) n FROM shipment s JOIN shipment_unit su USING(shipment_id)
                             JOIN unit u ON u.serial=su.serial
                             WHERE s.leg='CM_TO_3PL' AND s.received_at IS NOT NULL AND u.status='IN_TRANSIT'
                             GROUP BY s.shipment_id"""):
        add("MOVE-SHORT-RECEIPT", "MOVE", "SERIOUS", f"{r['n']} vehicles on {r['shipment_id']}'s ASN never arrived",
            "The CM's ASN listed them; the 3PL scanned the container without them", "shipment", r["shipment_id"],
            r["n"], r["n"] * 1552.0, "Logistics", f"#/shipments?id={r['shipment_id']}")
    # PLAN: MRP shortage (line-stop risk)
    for r in conn.execute("""SELECT m.item_id, m.bucket_date, m.qty, m.detail FROM mrp_message m
                             WHERE m.run_id=(SELECT MAX(run_id) FROM mrp_run) AND m.message='SHORTAGE'"""):
        where = {"CHG-1": "kitting", "DU-C": "CM line", "PU-1": "CM line", "HMI-1": "CM line"}.get(r["item_id"], "pack line")
        add("PLAN-LINE-STOP", "PLAN", "CRITICAL", f"{r['item_id']} runs out on {r['bucket_date']}: {where} stop risk",
            r["detail"], "item", r["item_id"], int(r["qty"] or 0), None, "Planning", f"#/mrp?item={r['item_id']}")
    # SOURCE
    n = conn.execute("""SELECT COUNT(*) n FROM po_line pl JOIN purchase_order po USING(po_id)
                        WHERE pl.status='OPEN' AND pl.confirm_status='UNCONFIRMED'
                        AND po.created_at < strftime('%Y-%m-%dT%H:%M:%SZ', ?, '-72 hours')""", (now,)).fetchone()["n"]
    if n:
        add("SOURCE-UNCONFIRMED", "SOURCE", "WARNING", f"{n} PO lines unconfirmed after 72 hours",
            "Planning on our need date, not a supplier promise", "po_lines", "unconfirmed", n, None, "Procurement",
            "#/suppliers")
    r = conn.execute("""SELECT COUNT(*) n, SUM((pl.unit_price - p.unit_price) * pl.qty) usd
                        FROM po_line pl JOIN purchase_order po USING(po_id)
                        JOIN price p ON p.item_id=pl.item_id AND p.supplier_id=po.supplier_id AND p.min_qty=0 AND p.basis<>'QUOTE'
                         AND p.eff_from <= substr(po.created_at,1,10) AND (p.eff_to IS NULL OR p.eff_to > substr(po.created_at,1,10))
                        WHERE pl.unit_price - p.unit_price > 0.001""").fetchone()
    if r["n"]:
        add("SOURCE-PRICE-MISMATCH", "SOURCE", "WARNING", f"{r['n']} PO lines priced above the effective contract",
            f"Overpayment ${r['usd']:,.0f} (superseded price copied from an older PO)", "po_lines", "price", r["n"],
            r["usd"], "Procurement", "#/contracts")
    # DATA
    r = conn.execute("""SELECT COUNT(*) n, MIN(received_at) first FROM raw_cm_mes_event WHERE ingest_status='QUARANTINED'""").fetchone()
    if r["n"]:
        add("DATA-QUARANTINE", "DATA", "SERIOUS", f"{r['n']} CM MES messages quarantined",
            "Unknown station S65 (the CM's new rework bay): as-built records may be wrong", "feed", "CM_MES", r["n"],
            None, "Data eng.", "#/cm-feed")
    r = conn.execute("SELECT COUNT(*) n FROM unit WHERE origin='PROVISIONAL'").fetchone()
    if r["n"]:
        add("DATA-PROVISIONAL", "DATA", "WARNING", f"{r['n']} installed drive units have no supplier ASN",
            "Hand-carried to the CM without an ASN: motor, controller and magnet lot unknown", "units", "provisional",
            r["n"], None, "Data eng.", "#/cm-feed")
    # QUALITY: deviations near limit or expiry; chargebacks aging
    for d in conn.execute("SELECT * FROM deviation WHERE status='APPROVED'"):
        days = (dt.date.fromisoformat(d["valid_to"]) - dt.date.fromisoformat(as_of)).days
        if days <= 5 or d["qty_used"] >= 0.9 * d["qty_limit"]:
            add("QUALITY-DEVIATION", "QUALITY", "WARNING", f"{d['deviation_id']} expires in {days} days",
                f"{d['title']}: {d['qty_used']} of {d['qty_limit']} used", "deviation", d["deviation_id"],
                d["qty_limit"] - d["qty_used"], None, "SQE", "#/quality")
    for r in conn.execute("""SELECT d.deviation_id, d.item_id, d.valid_to, COUNT(*) n, MAX(g.installed_at) last
                             FROM deviation d JOIN item i ON i.item_id = d.item_id AND i.lifecycle = 'PHASE_OUT'
                             JOIN genealogy g ON g.child_item_id = d.item_id AND substr(g.installed_at, 1, 10) > d.valid_to
                             GROUP BY d.deviation_id"""):
        add("QUALITY-DEVIATION-OVERRUN", "QUALITY", "WARNING",
            f"{r['n']} {r['item_id']} installed after {r['deviation_id']} expired",
            f"The use-up deviation ended {r['valid_to']}, but the line kept drawing phase-out stock until "
            f"{r['last'][:10]}. Needs a retroactive disposition (extend, or MRB review of affected packs).",
            "deviation", r["deviation_id"], r["n"], None, "SQE", "#/quality")
    for c in conn.execute("""SELECT chargeback_id, supplier_id, amount_usd, sent_at FROM chargeback
                             WHERE status='SENT' AND sent_at < strftime('%Y-%m-%dT%H:%M:%SZ', ?, '-21 days')""", (now,)):
        add("QUALITY-CB-AGING", "QUALITY", "WARNING", f"{c['chargeback_id']} unanswered by {c['supplier_id']} for 21+ days",
            f"${c['amount_usd']:,.0f} recovery outstanding", "chargeback", c["chargeback_id"], None, c["amount_usd"],
            "SQE", f"#/warranty?cb={c['chargeback_id']}")
    # PROMISE: orders whose promise ATP can no longer keep
    from .promises import at_risk
    risk = at_risk(conn)
    if risk:
        add("PROMISE-AT-RISK", "PLAN", "SERIOUS", f"{len(risk)} customer promises now at risk",
            f"Average slip {sum(r['slip_days'] for r in risk) / len(risk):.1f} days; mostly pegged to the delayed sailing",
            "orders", "at_risk", len(risk), None, "Customer ops", "#/atp")
    # INVENTORY: stranded phase-out stock after an ECO cut-in
    r = conn.execute("SELECT COUNT(*) n FROM unit WHERE item_id='DU-B' AND status='COMPONENT'").fetchone()
    if r["n"]:
        add("INV-STRANDED", "PLAN", "INFO", f"{r['n']} rev-B drive units stranded at the CM after ECO-0042",
            "OEM-owned consigned stock; disposition: service spares or rework to rev C", "item", "DU-B", r["n"],
            r["n"] * 182.0, "Planning", "#/inventory?item=DU-B")
    return ex


def detect(conn):
    now = get_now(conn)
    found = _rules(conn)
    ids = set()
    for e in found:
        ids.add(e["exception_id"])
        row = conn.execute("SELECT status FROM ops_exception WHERE exception_id=?", (e["exception_id"],)).fetchone()
        if row:
            conn.execute("UPDATE ops_exception SET title=?, detail=?, impact_units=?, impact_unit=?, impact_usd=?, severity=?,"
                         " status=CASE WHEN status='RESOLVED' THEN 'OPEN' ELSE status END, resolved_at=NULL"
                         " WHERE exception_id=?", (e["title"], e["detail"], e["impact_units"], e["impact_unit"],
                                                   e["impact_usd"], e["severity"], e["exception_id"]))
        else:
            conn.execute("INSERT INTO ops_exception(exception_id, rule_id, domain, severity, title, detail, ref_type, ref_id,"
                         " impact_units, impact_unit, impact_usd, detected_at, owner, status, route)"
                         " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (e["exception_id"], e["rule_id"], e["domain"], e["severity"], e["title"], e["detail"],
                          e["ref_type"], e["ref_id"], e["impact_units"], e["impact_unit"], e["impact_usd"], now,
                          e["owner"], "OPEN", e["route"]))
    for row in conn.execute("SELECT exception_id FROM ops_exception WHERE status != 'RESOLVED'").fetchall():
        if row["exception_id"] not in ids:
            conn.execute("UPDATE ops_exception SET status='RESOLVED', resolved_at=? WHERE exception_id=?",
                         (now, row["exception_id"]))
    return len(found)
