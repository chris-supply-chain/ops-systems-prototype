"""As-built genealogy: backward trace (what is in this unit), forward trace (where
did this lot or serial go), recall scope (what is still in our control), and a
unit's life story across every system.

Both traces are single recursive CTEs over `genealogy`. The same SQL is shown in
the Data Sandbox, so the page and the database tell the same story.
"""

BACKWARD_SQL = """
WITH RECURSIVE tree(genealogy_id, parent_serial, child_serial, child_lot_id, child_item_id, qty, relation,
                    position, station_id, installed_at, removed_at, removal_reason, source, depth) AS (
  SELECT genealogy_id, parent_serial, child_serial, child_lot_id, child_item_id, qty, relation, position,
         station_id, installed_at, removed_at, removal_reason, source, 1
  FROM genealogy WHERE parent_serial = :root AND (:history = 1 OR removed_at IS NULL)
  UNION ALL
  SELECT g.genealogy_id, g.parent_serial, g.child_serial, g.child_lot_id, g.child_item_id, g.qty, g.relation,
         g.position, g.station_id, g.installed_at, g.removed_at, g.removal_reason, g.source, t.depth + 1
  FROM genealogy g JOIN tree t ON g.parent_serial = t.child_serial
  WHERE (:history = 1 OR g.removed_at IS NULL) AND t.depth < 8
)
SELECT tree.*, i.name AS item_name, i.kind AS item_kind,
       u.status AS unit_status, u.origin AS unit_origin, u.supplier_id AS unit_supplier, u.on_hold,
       l.supplier_id AS lot_supplier, l.iqc_status, l.mfg_date, l.received_at AS lot_received
FROM tree
JOIN item i ON i.item_id = tree.child_item_id
LEFT JOIN unit u ON u.serial = tree.child_serial
LEFT JOIN lot l ON l.lot_id = tree.child_lot_id
ORDER BY depth, position, installed_at
"""

FORWARD_SQL = """
WITH RECURSIVE lots(lot_id) AS (
  SELECT :ref
  UNION
  SELECT ll.child_lot_id FROM lot_link ll JOIN lots ON ll.parent_lot_id = lots.lot_id
),
up(serial, depth, relation) AS (
  SELECT parent_serial, 1, relation FROM genealogy
  WHERE (child_lot_id IN (SELECT lot_id FROM lots) OR child_serial = :ref) AND removed_at IS NULL
  UNION
  SELECT g.parent_serial, up.depth + 1, g.relation FROM genealogy g
  JOIN up ON g.child_serial = up.serial
  WHERE g.removed_at IS NULL AND up.depth < 8
)
SELECT up.serial, MIN(up.depth) AS depth, u.item_id, i.kind, i.name, u.status, u.location_site_id, u.on_hold,
       u.built_at, u.line
FROM up JOIN unit u ON u.serial = up.serial JOIN item i ON i.item_id = u.item_id
GROUP BY up.serial
"""

IN_CONTROL = {"WIP", "BUILT", "IN_TRANSIT", "AT_3PL", "ALLOCATED", "COMPONENT"}
WITH_CUSTOMER = {"SHIPPED", "DELIVERED"}


def resolve(conn, q):
    q = (q or "").strip()
    if not q:
        return None
    for kind, sql in (("unit", "SELECT serial AS id FROM unit WHERE serial=?"),
                      ("lot", "SELECT lot_id AS id FROM lot WHERE lot_id=?"),
                      ("order", "SELECT order_id AS id FROM customer_order WHERE order_id=?")):
        row = conn.execute(sql, (q,)).fetchone()
        if row:
            return {"kind": kind, "id": row["id"]}
    row = conn.execute("SELECT serial AS id FROM unit WHERE serial LIKE ? ORDER BY serial LIMIT 1", (q + "%",)).fetchone()
    if row:
        return {"kind": "unit", "id": row["id"]}
    row = conn.execute("SELECT lot_id AS id FROM lot WHERE lot_id LIKE ? ORDER BY lot_id LIMIT 1", (q + "%",)).fetchone()
    if row:
        return {"kind": "lot", "id": row["id"]}
    return None


def unit_header(conn, serial):
    u = conn.execute("""SELECT u.*, i.name AS item_name, i.kind AS item_kind, s.name AS location_name
                        FROM unit u JOIN item i USING(item_id) LEFT JOIN site s ON s.site_id = u.location_site_id
                        WHERE u.serial=?""", (serial,)).fetchone()
    return u


def backward(conn, serial, history=False):
    """Nested as-built tree of everything inside `serial` (optionally including removed parts)."""
    rows = conn.execute(BACKWARD_SQL, {"root": serial, "history": 1 if history else 0}).fetchall()
    nodes = {}
    root = {"id": serial, "children": []}
    for r in rows:
        key = r["child_serial"] or r["child_lot_id"]
        node = {"id": key, "kind": "serial" if r["child_serial"] else "lot", "item_id": r["child_item_id"],
                "item_name": r["item_name"], "item_kind": r["item_kind"], "qty": r["qty"], "relation": r["relation"],
                "position": r["position"], "station_id": r["station_id"], "installed_at": r["installed_at"],
                "removed_at": r["removed_at"], "removal_reason": r["removal_reason"], "source": r["source"],
                "status": r["unit_status"] or r["iqc_status"], "origin": r["unit_origin"],
                "supplier_id": r["unit_supplier"] or r["lot_supplier"], "on_hold": r["on_hold"],
                "mfg_date": r["mfg_date"], "depth": r["depth"], "children": []}
        parent = nodes.get(r["parent_serial"], root) if r["depth"] > 1 else root
        parent["children"].append(node)
        if r["child_serial"] and not r["removed_at"]:
            nodes[key] = node
        elif r["child_serial"] and r["removed_at"] and key not in nodes:
            nodes[key] = node
    _add_upstream_lots(conn, root)
    return root


def _add_upstream_lots(conn, node, depth=0):
    """Hang each lot's tier-2/3 parent lots (from supplier certificates) under it: as-built down to the mine."""
    for ch in node["children"]:
        if ch["kind"] == "lot" and depth < 4:
            for r in conn.execute("""SELECT ll.parent_lot_id, ll.qty, l.item_id, i.name, l.supplier_id, l.mfg_date, s.tier
                                     FROM lot_link ll JOIN lot l ON l.lot_id = ll.parent_lot_id JOIN item i USING(item_id)
                                     JOIN supplier s ON s.supplier_id = l.supplier_id WHERE ll.child_lot_id=?""", (ch["id"],)):
                ch["children"].append({"id": r["parent_lot_id"], "kind": "lot", "item_id": r["item_id"], "item_name": r["name"],
                                       "item_kind": "MATERIAL", "qty": r["qty"], "relation": "MADE_FROM",
                                       "position": f"TIER {r['tier']}", "station_id": None, "installed_at": None,
                                       "removed_at": None, "removal_reason": None, "source": "SUPPLIER_COA",
                                       "status": None, "origin": None, "supplier_id": r["supplier_id"], "on_hold": 0,
                                       "mfg_date": r["mfg_date"], "depth": ch["depth"] + 1, "children": []})
            _add_upstream_lots(conn, ch, depth + 1)
        elif ch["children"]:
            _add_upstream_lots(conn, ch, depth)


def lots_below(conn, ref):
    """The lot itself plus every lot made from it (cathode batch -> cell lots)."""
    return [r["lot_id"] for r in conn.execute(
        "WITH RECURSIVE lots(lot_id) AS (SELECT ? UNION SELECT ll.child_lot_id FROM lot_link ll JOIN lots"
        " ON ll.parent_lot_id = lots.lot_id) SELECT lot_id FROM lots", (ref,))]


def forward(conn, ref):
    """Every unit that contains `ref` (a lot id or a serial), at any level."""
    return conn.execute(FORWARD_SQL, {"ref": ref}).fetchall()


def recall_scope(conn, ref):
    """What a problem in `ref` touches, split by whether we can still stop it."""
    units = forward(conn, ref)
    vehicles = [u for u in units if u["kind"] == "VEHICLE"]
    packs = [u for u in units if u["kind"] == "PACK"]
    top = list(vehicles)
    married = set()
    if packs:
        pack_ids = [p["serial"] for p in packs]
        for chunk in _chunks(pack_ids, 500):
            q = ",".join("?" * len(chunk))
            for r in conn.execute(f"SELECT child_serial FROM genealogy WHERE relation='SHIPPED_WITH' AND removed_at IS NULL"
                                  f" AND child_serial IN ({q})", chunk):
                married.add(r["child_serial"])
        top += [p for p in packs if p["serial"] not in married]
    buckets = {"IN_CONTROL": [], "WITH_CUSTOMER": [], "OUT_OF_SCOPE": []}
    for u in top:
        st = u["status"]
        b = "IN_CONTROL" if st in IN_CONTROL else ("WITH_CUSTOMER" if st in WITH_CUSTOMER else "OUT_OF_SCOPE")
        buckets[b].append(u)
    by_status = {}
    for u in top:
        k = ("HOLD" if u["on_hold"] else u["status"], u["kind"])
        by_status[k] = by_status.get(k, 0) + 1
    serials = [u["serial"] for u in top]
    bucket_of = {u["serial"]: b for b, us in buckets.items() for u in us}
    orders, regions, orders_by_bucket = [], {}, {b: set() for b in buckets}
    for chunk in _chunks(serials, 500):
        q = ",".join("?" * len(chunk))
        for r in conn.execute(f"""SELECT DISTINCT o.order_id, o.status, o.ship_to_region, o.ship_to_state, o.promised_date,
                                         ol.vehicle_serial, ol.pack_serial
                                  FROM order_line ol JOIN customer_order o USING(order_id)
                                  WHERE ol.vehicle_serial IN ({q}) OR ol.pack_serial IN ({q})""", chunk + chunk):
            orders.append(r)
            regions[r["ship_to_region"]] = regions.get(r["ship_to_region"], 0) + 1
            b = bucket_of.get(r["vehicle_serial"]) or bucket_of.get(r["pack_serial"])
            if b:
                orders_by_bucket[b].add(r["order_id"])
    holds = sum(1 for u in top if u["on_hold"])
    lots = lots_below(conn, ref)
    q = ",".join("?" * len(lots))
    lot_stock = [dict(r) for r in conn.execute(
        f"SELECT lot_id, item_id, site_id, stock_status, qty FROM inventory_balance WHERE lot_id IN ({q}) AND qty > 0", lots)]
    claims = conn.execute(f"""SELECT claim_id, serial, reported_at, symptom, defect_code, status, failed_lot_id, supplier_id,
                                     cost_parts_usd + cost_labor_usd + cost_logistics_usd AS cost_usd, chargeback_id
                              FROM warranty_claim WHERE failed_lot_id IN ({q}) OR failed_serial = ? OR serial = ?
                              ORDER BY reported_at DESC""", lots + [ref, ref]).fetchall()
    return {
        "ref": ref,
        "lots": lots,
        "lot_stock": lot_stock,
        "units_total": len(units),
        "top_level": len(top),
        "vehicles": len(vehicles),
        "packs": len(packs),
        "packs_unmarried": len(packs) - len(married),
        "in_control": len(buckets["IN_CONTROL"]),
        "with_customer": len(buckets["WITH_CUSTOMER"]),
        "out_of_scope": len(buckets["OUT_OF_SCOPE"]),
        "on_hold": holds,
        "by_status": [{"status": k[0], "kind": k[1], "n": v} for k, v in sorted(by_status.items())],
        "regions": regions,
        "orders": [dict(o) for o in orders[:500]],
        "orders_total": len(orders),
        "orders_with_customer": len(orders_by_bucket["WITH_CUSTOMER"]),
        "orders_in_control": len(orders_by_bucket["IN_CONTROL"]),      # waiting on units a hold would stop
        "claims": [dict(c) for c in claims],
        "in_control_units": [dict(u) for u in buckets["IN_CONTROL"]],
        "with_customer_units": [dict(u) for u in buckets["WITH_CUSTOMER"][:500]],
    }


def lot_header(conn, lot_id):
    lot = conn.execute("""SELECT l.*, i.name AS item_name, s.name AS supplier_name, s.tier, site.name AS site_name
                          FROM lot l JOIN item i USING(item_id) JOIN supplier s ON s.supplier_id = l.supplier_id
                          JOIN site ON site.site_id = l.site_id WHERE l.lot_id=?""", (lot_id,)).fetchone()
    if not lot:
        return None
    lot = dict(lot)
    lot["qc"] = [dict(r) for r in conn.execute("SELECT qe_id, kind, result, disposition, qty_inspected, qty_defective,"
                                                " defect_code, detected_at, status FROM quality_event WHERE lot_id=?", (lot_id,))]
    lot["consumed_units"] = conn.execute("SELECT COUNT(DISTINCT parent_serial) n, SUM(qty) q FROM genealogy WHERE child_lot_id=?",
                                         (lot_id,)).fetchone()
    lot["consumed_units"] = dict(lot["consumed_units"])
    lot["on_hand"] = conn.execute("SELECT COALESCE(SUM(qty),0) q FROM inventory_balance WHERE lot_id=?", (lot_id,)).fetchone()["q"]
    lot["holds"] = [dict(r) for r in conn.execute("SELECT * FROM hold WHERE lot_id=?", (lot_id,))]
    return lot


def timeline(conn, serial):
    """One unit's life across MES, carriers, customs, 3PL, CRM and the action layer."""
    ev = []
    for r in conn.execute("""SELECT se.event_ts, se.result, se.defect_code, s.code, s.name, s.site_id, se.source, se.raw_id,
                                    se.measurements
                             FROM station_event se JOIN station s USING(station_id) WHERE se.serial=? ORDER BY se.event_ts""",
                          (serial,)):
        ev.append({"ts": r["event_ts"], "system": "CM MES" if r["source"] == "CM_FEED" else "OEM MES",
                   "kind": "station", "title": f"{r['code']} {r['name']}: {r['result']}",
                   "detail": r["defect_code"], "tone": {"PASS": "good", "FAIL": "critical", "REWORK": "warning",
                                                        "SCRAP": "critical"}[r["result"]],
                   "ref": f"raw_cm_mes_event:{r['raw_id']}" if r["raw_id"] else None})
    for r in conn.execute("""SELECT se.event_ts, se.code, se.location, se.detail, se.source, se.raw_ref, s.shipment_id, s.mode
                             FROM shipment_unit su JOIN shipment s USING(shipment_id)
                             JOIN shipment_event se ON se.shipment_id = s.shipment_id
                             WHERE su.serial=? ORDER BY se.event_ts""", (serial,)):
        ev.append({"ts": r["event_ts"], "system": {"CARRIER": "Carrier", "3PL_FEED": "3PL WMS", "BROKER": "Customs broker",
                                                     "OEM": "TMS"}[r["source"]],
                   "kind": "shipment", "title": f"{r['code'].replace('_', ' ').title()} · {r['shipment_id']}",
                   "detail": " · ".join(x for x in (r["location"], r["detail"]) if x), "tone": "info",
                   "ref": r["raw_ref"], "shipment_id": r["shipment_id"]})
    for r in conn.execute("""SELECT g.installed_at, g.removed_at, g.removal_reason, g.relation, g.source, g.parent_serial,
                                    g.child_serial, g.position
                             FROM genealogy g WHERE (g.child_serial=? OR g.parent_serial=?) AND g.relation='SHIPPED_WITH'""",
                          (serial, serial)):
        other = r["child_serial"] if r["parent_serial"] == serial else r["parent_serial"]
        ev.append({"ts": r["installed_at"], "system": "3PL WMS" if r["source"] == "3PL_FEED" else "Service",
                   "kind": "marriage", "title": f"Kitted with {other}", "detail": r["position"], "tone": "info",
                   "other": other})
        if r["removed_at"]:
            ev.append({"ts": r["removed_at"], "system": "Service", "kind": "swap", "title": f"Removed from kit: {other}",
                       "detail": r["removal_reason"], "tone": "warning", "other": other})
    for r in conn.execute("""SELECT o.order_id, o.allocated_at, o.shipped_at, o.delivered_at, o.ship_to_state
                             FROM order_line ol JOIN customer_order o USING(order_id)
                             WHERE ol.vehicle_serial=? OR ol.pack_serial=?""", (serial, serial)):
        if r["allocated_at"]:
            ev.append({"ts": r["allocated_at"], "system": "3PL WMS", "kind": "order",
                       "title": f"Allocated to {r['order_id']}", "detail": f"Ship to {r['ship_to_state']}", "tone": "info",
                       "order_id": r["order_id"]})
    for r in conn.execute("SELECT * FROM warranty_claim WHERE serial=? OR failed_serial=?", (serial, serial)):
        ev.append({"ts": r["reported_at"], "system": "CRM", "kind": "claim", "title": f"Warranty claim {r['claim_id']}",
                   "detail": r["symptom"], "tone": "serious", "claim_id": r["claim_id"]})
    for r in conn.execute("SELECT * FROM hold WHERE serial=?", (serial,)):
        ev.append({"ts": r["placed_at"], "system": "QMS", "kind": "hold", "title": "Hold placed", "detail": r["reason"],
                   "tone": "critical"})
        if r["released_at"]:
            ev.append({"ts": r["released_at"], "system": "QMS", "kind": "hold", "title": "Hold released", "detail": None,
                       "tone": "good"})
    ev.sort(key=lambda e: e["ts"] or "")
    return ev


def months_in_service(conn):
    """Months each delivered pack has been with its customer, by pack serial."""
    from ..db import now as get_now
    rows = conn.execute("""SELECT ol.pack_serial AS serial, (julianday(?) - julianday(o.delivered_at)) / 30.4375 AS months
                           FROM order_line ol JOIN customer_order o USING(order_id)
                           WHERE ol.pack_serial IS NOT NULL AND o.delivered_at IS NOT NULL""", (get_now(conn),))
    return {r["serial"]: max(0.0, r["months"]) for r in rows}


def field_rate(claims, packs, months):
    """Claims per 1,000 pack-months in service for one group of packs. Time in service is the fair denominator:
    a pack delivered last week has had a week to fail, one delivered in June has had three months."""
    pack_months = sum(months.get(p, 0.0) for p in packs)
    return {"claims": claims, "packs_in_service": sum(1 for p in packs if p in months),
            "pack_months": round(pack_months, 1),
            "rate": round(1000.0 * claims / pack_months, 1) if pack_months else None}


def batch_signal(conn, ref, months=None):
    """Capacity-fade claims on packs built from `ref` (a batch, lot or serial) against every other delivered pack, both
    per 1,000 pack-months in service: (mine, rest)."""
    months = months_in_service(conn) if months is None else months
    exposed = {u["serial"] for u in forward(conn, ref) if u["kind"] == "PACK"}
    failed = [r["failed_serial"] for r in conn.execute(
        "SELECT failed_serial FROM warranty_claim WHERE defect_code='FLD-CAPFADE' AND status != 'REJECTED'")]
    mine = field_rate(sum(1 for f in failed if f in exposed), exposed, months)
    rest = field_rate(sum(1 for f in failed if f not in exposed), set(months) - exposed, months)
    return mine, rest


def _chunks(xs, n):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]
