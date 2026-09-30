"""Genealogy: resolve anything (serial, lot, order), backward as-built tree, forward
trace / recall scope, and a unit's life across every system."""
from ops.api.router import HttpError, get
from ops.db import q, q1
from ops.logic import genealogy as g


@get(r"^/api/genealogy/resolve$")
def resolve(req):
    ref = req.arg("q")
    hit = g.resolve(req.conn, ref)
    if hit and hit["kind"] == "order":
        line = q1(req.conn, "SELECT vehicle_serial FROM order_line WHERE order_id=? AND line_no=1", (hit["id"],))
        if line and line["vehicle_serial"]:
            return {"kind": "unit", "id": line["vehicle_serial"], "via_order": hit["id"]}
    return hit or {"kind": None}


@get(r"^/api/genealogy/unit/(.+)$")
def unit(req):
    c = req.conn
    serial = req.params[0]
    head = g.unit_header(c, serial)
    if not head:
        raise HttpError(404, f"no unit {serial}")
    history = req.arg("history", "0") == "1"
    tree = g.backward(c, serial, history=history)
    parents = q(c, """WITH RECURSIVE up(serial, depth, relation, position) AS (
                        SELECT parent_serial, 1, relation, position FROM genealogy WHERE child_serial=? AND removed_at IS NULL
                        UNION SELECT g.parent_serial, up.depth + 1, g.relation, g.position FROM genealogy g
                        JOIN up ON g.child_serial = up.serial WHERE g.removed_at IS NULL AND up.depth < 6)
                      SELECT up.*, u.item_id, i.kind, u.status FROM up JOIN unit u USING(serial) JOIN item i USING(item_id)
                      ORDER BY depth""", (serial,))
    orders = q(c, """SELECT o.order_id, o.status, o.channel, o.ship_to_state, o.promised_date, o.first_promised_date,
                            o.delivered_at, ol.line_no, ol.item_id AS kit
                     FROM order_line ol JOIN customer_order o USING(order_id)
                     WHERE ol.vehicle_serial=? OR ol.pack_serial=?""", (serial,) * 2)
    removed = q(c, """SELECT child_serial, child_item_id, removed_at, removal_reason, position FROM genealogy
                      WHERE parent_serial=? AND removed_at IS NOT NULL""", (serial,))
    holds = q(c, "SELECT * FROM hold WHERE serial=? ORDER BY placed_at DESC", (serial,))
    wo = q1(c, "SELECT * FROM work_order WHERE wo_id=?", (head["wo_id"],)) if head["wo_id"] else None
    eol = q1(c, """SELECT se.measurements FROM station_event se JOIN station s USING(station_id)
                   WHERE se.serial=? AND s.code='S60' AND se.result='PASS' ORDER BY se.event_ts DESC LIMIT 1""", (serial,))
    return {"unit": head, "tree": tree, "parents": parents, "orders": orders, "removed": removed, "holds": holds,
            "work_order": wo, "eol": eol["measurements"] if eol else None, "timeline": g.timeline(c, serial),
            "sql": g.BACKWARD_SQL.strip()}


@get(r"^/api/genealogy/lot/(.+)$")
def lot(req):
    c = req.conn
    lot_id = req.params[0]
    head = g.lot_header(c, lot_id)
    if not head:
        raise HttpError(404, f"no lot {lot_id}")
    upstream = q(c, """WITH RECURSIVE up(lot_id, depth) AS (SELECT parent_lot_id, 1 FROM lot_link WHERE child_lot_id=?
                         UNION SELECT ll.parent_lot_id, up.depth + 1 FROM lot_link ll JOIN up ON ll.child_lot_id = up.lot_id)
                       SELECT up.depth, l.lot_id, l.item_id, i.name, l.supplier_id, s.name AS supplier_name, s.tier, l.qty_received,
                              i.uom, l.mfg_date FROM up JOIN lot l USING(lot_id) JOIN item i USING(item_id)
                       JOIN supplier s ON s.supplier_id = l.supplier_id ORDER BY up.depth""", (lot_id,))
    downstream = q(c, """SELECT ll.child_lot_id AS lot_id, l.item_id, l.supplier_id, l.qty_received, l.received_at, l.iqc_status,
                                (SELECT COUNT(DISTINCT parent_serial) FROM genealogy WHERE child_lot_id = ll.child_lot_id) packs,
                                (SELECT COUNT(*) FROM warranty_claim WHERE failed_lot_id = ll.child_lot_id
                                   AND defect_code = 'FLD-CAPFADE' AND status != 'REJECTED') claims
                         FROM lot_link ll JOIN lot l ON l.lot_id = ll.child_lot_id WHERE ll.parent_lot_id=?
                         ORDER BY l.received_at""", (lot_id,))
    if downstream:
        # per month in service, so the youngest lots are judged on the time they have actually had to fail
        months = g.months_in_service(c)
        for d in downstream:
            packs = {r["parent_serial"] for r in c.execute(
                "SELECT DISTINCT parent_serial FROM genealogy WHERE child_lot_id=?", (d["lot_id"],))}
            d.update(g.field_rate(d["claims"], packs, months))
    scope = g.recall_scope(c, lot_id)
    decision = q1(c, """SELECT decision_id, status, title FROM decision_log WHERE rule_id='QUALITY-LOT-CLUSTER'
                        AND (trigger_ref=? OR trigger_ref IN (SELECT parent_lot_id FROM lot_link WHERE child_lot_id=?))
                        ORDER BY decision_id DESC LIMIT 1""", (lot_id, lot_id))
    return {"lot": head, "upstream": upstream, "downstream": downstream, "scope": scope, "decision": decision,
            "sql": g.FORWARD_SQL.strip()}


@get(r"^/api/genealogy/examples$")
def examples(req):
    c = req.conn
    ex = []
    batch = q1(c, "SELECT ref_id FROM ops_exception WHERE rule_id='QUALITY-LOT-CLUSTER' ORDER BY detected_at LIMIT 1")
    if batch:
        ex.append({"q": batch["ref_id"], "label": "The field issue", "why": "Cathode batch behind the capacity-fade claims"})
    swap = q1(c, "SELECT parent_serial FROM genealogy WHERE source='SERVICE' LIMIT 1")
    if swap:
        ex.append({"q": swap["parent_serial"], "label": "Pack swapped in service", "why": "As-built vs as-maintained"})
    wrong = q1(c, """SELECT se.serial FROM station_event se JOIN station s USING(station_id)
                     JOIN genealogy gg ON gg.parent_serial=se.serial AND gg.position='DRIVE_UNIT' AND gg.removed_at IS NULL
                     WHERE s.code='S60' AND se.result='PASS' AND json_extract(se.measurements,'$.du_sn') <> gg.child_serial
                     LIMIT 1""")
    if wrong:
        ex.append({"q": wrong["serial"], "label": "Wrong drive unit on record", "why": "Rework at the unmapped S65 bay"})
    prov = q1(c, "SELECT serial FROM unit WHERE origin='PROVISIONAL' LIMIT 1")
    if prov:
        ex.append({"q": prov["serial"], "label": "Drive unit without an ASN", "why": "Hand-carried to the CM"})
    ocean = q1(c, """SELECT su.serial FROM shipment s JOIN shipment_unit su USING(shipment_id) WHERE s.leg='CM_TO_3PL'
                     AND s.ata IS NULL AND s.eta_current > s.eta_planned LIMIT 1""")
    if ocean:
        ex.append({"q": ocean["serial"], "label": "On the delayed vessel", "why": "Pegged to at-risk promises"})
    du_rework = q1(c, """SELECT parent_serial FROM genealogy WHERE position='DRIVE_UNIT' AND removed_at IS NOT NULL
                         AND source='CM_FEED' LIMIT 1""")
    if du_rework:
        ex.append({"q": du_rework["parent_serial"], "label": "Drive unit swapped at EOL", "why": "Removed parts kept in history"})
    return {"examples": ex}
