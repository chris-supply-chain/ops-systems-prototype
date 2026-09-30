"""Inventory · WMS: the multi-echelon position from supplier-held stock to the 3PL
shelf, by owner and status, valued at standard cost, and reconciled against the
3PL's WMS snapshot."""
from ...db import as_of, now, q, q1, val
from ..router import HttpError, get

ECHELONS = [
    ("SUPPLIER", "Supplier-held", "SUPPLIER", "VMI and supplier stock, from supplier reports"),
    ("INBOUND", "Inbound to our sites", "OEM", "Supplier shipments in transit (FCA/DAP)"),
    ("PLANT", "OEM plant · Fremont", "OEM", "Pack-line components, BMS boards, packs in WIP/FG"),
    ("CM_CONSIGNED", "Consigned at the CM · Taichung", "OEM", "OEM-owned modules held at the CM in Taiwan"),
    ("CM_OWNED", "CM-owned parts", "CM", "Parts the CM buys itself (CM weekly report)"),
    ("CM_FG", "CM WIP & finished goods", "CM", "Vehicles on the line or waiting for a container"),
    ("TRANSIT", "Ocean & line-haul in transit", "OEM", "Vehicles on the water, packs on DG trucks"),
    ("3PL", "3PL · Reno", "OEM", "Available, allocated and held stock at the 3PL"),
]
POINT_OF_USE = {"PLANT": "Fremont", "CM_CONSIGNED": "Taichung", "3PL": "Reno"}
SITE_SHORT = {"OEM-FRE": "Fremont", "3PL-RNO": "Reno", "CM-TXG": "Taichung"}


def _positions(conn):
    """Rows of (echelon, item_id, bucket, qty, site_id, detail)."""
    rows = []
    for r in q(conn, """SELECT site_id, item_id, stock_status, SUM(qty) qty, MIN(as_of) as_of, source
                        FROM inventory_balance WHERE owner='SUPPLIER' GROUP BY site_id, item_id, stock_status"""):
        rows.append(("SUPPLIER", r["item_id"], "FG at supplier" if r["stock_status"] == "AVAILABLE" else "In production",
                     r["qty"], r["site_id"], r["as_of"]))
    for r in q(conn, """SELECT pl.item_id, s.dest_site_id, SUM(pl.qty - pl.received_qty) qty, s.mode, s.eta_current
                        FROM shipment s JOIN po_line pl ON s.shipment_id = 'IB-' || pl.po_id || '-' || pl.line_no
                        WHERE s.leg = 'SUPPLIER_TO_PLANT' AND s.status IN ('IN_TRANSIT','BOOKED')
                        GROUP BY pl.item_id, s.dest_site_id, s.mode"""):
        mode = {"OCEAN": "Ocean", "TRUCK_FTL": "Truck", "AIR": "Air", "TRUCK_LTL_DG": "DG truck"}.get(r["mode"], r["mode"])
        rows.append(("INBOUND", r["item_id"], f"{mode} → {SITE_SHORT.get(r['dest_site_id'], r['dest_site_id'])}",
                     r["qty"], r["dest_site_id"], r["eta_current"]))
    for r in q(conn, """SELECT b.item_id, b.stock_status, SUM(b.qty) qty FROM inventory_balance b
                        WHERE b.site_id='OEM-FRE' GROUP BY b.item_id, b.stock_status"""):
        rows.append(("PLANT", r["item_id"], "QC hold" if r["stock_status"] == "QC_HOLD" else "Available", r["qty"],
                     "OEM-FRE", None))
    for r in q(conn, """SELECT u.item_id, u.status, COUNT(*) n FROM unit u
                        WHERE u.location_site_id='OEM-FRE' AND u.status IN ('COMPONENT','WIP','BUILT')
                        GROUP BY u.item_id, u.status"""):
        bucket = {"COMPONENT": "Available", "WIP": "Packs in WIP", "BUILT": "Packs awaiting truck"}[r["status"]]
        rows.append(("PLANT", r["item_id"], bucket, r["n"], "OEM-FRE", None))
    for r in q(conn, """SELECT item_id, COUNT(*) n FROM unit WHERE location_site_id='CM-TXG' AND status='COMPONENT'
                        GROUP BY item_id"""):
        rows.append(("CM_CONSIGNED", r["item_id"], "Available", r["n"], "CM-TXG", None))
    for r in q(conn, """SELECT item_id, SUM(qty) qty FROM inventory_balance WHERE site_id='CM-TXG' AND owner='CM'
                        GROUP BY item_id"""):
        rows.append(("CM_OWNED", r["item_id"], "CM stock", r["qty"], "CM-TXG", None))
    for r in q(conn, """SELECT item_id, status, COUNT(*) n FROM unit WHERE location_site_id='CM-TXG'
                        AND status IN ('WIP','BUILT') GROUP BY item_id, status"""):
        rows.append(("CM_FG", r["item_id"], "On the line" if r["status"] == "WIP" else "Awaiting container",
                     r["n"], "CM-TXG", None))
    for r in q(conn, """SELECT u.item_id, s.leg, s.status, s.received_at IS NOT NULL AS rcv, COUNT(*) n
                        FROM unit u JOIN shipment_unit su ON su.serial = u.serial
                        JOIN shipment s ON s.shipment_id = su.shipment_id AND s.leg IN ('CM_TO_3PL','PLANT_TO_3PL')
                        WHERE u.status = 'IN_TRANSIT' GROUP BY u.item_id, s.leg, s.status, rcv"""):
        if r["rcv"]:
            bucket = "Unaccounted"
        elif r["leg"] == "CM_TO_3PL":
            bucket = {"CUSTOMS_HOLD": "Customs hold", "AT_PORT": "At port", "GATED_IN": "At origin port"}.get(r["status"], "On the water")
        else:
            bucket = "DG truck"
        rows.append(("TRANSIT", r["item_id"], bucket, r["n"], None, None))
    for r in q(conn, """SELECT item_id, CASE WHEN on_hold = 1 THEN 'On hold' WHEN status='ALLOCATED' THEN 'Allocated'
                               ELSE 'Available' END bucket, COUNT(*) n
                        FROM unit WHERE location_site_id='3PL-RNO' AND status IN ('AT_3PL','ALLOCATED')
                        GROUP BY item_id, bucket"""):
        rows.append(("3PL", r["item_id"], r["bucket"], r["n"], "3PL-RNO", None))
    for r in q(conn, "SELECT item_id, SUM(qty) qty FROM inventory_balance WHERE site_id='3PL-RNO' GROUP BY item_id"):
        rows.append(("3PL", r["item_id"], "Available", r["qty"], "3PL-RNO", None))
    return rows


def _daily_use(conn, today):
    """14-day average daily consumption at the point of use."""
    use = {}
    for r in q(conn, """SELECT child_item_id item, SUM(qty) q FROM genealogy
                        WHERE relation='INSTALLED' AND source IN ('OEM_MES','CM_FEED')
                          AND julianday(installed_at) >= julianday(?) - 14
                        GROUP BY child_item_id""", (today,)):
        use[r["item"]] = r["q"] / 14.0
    for r in q(conn, """SELECT u.item_id item, COUNT(*) n FROM customer_order o
                        JOIN order_line ol ON ol.order_id = o.order_id
                        JOIN unit u ON u.serial IN (ol.vehicle_serial, ol.pack_serial)
                        WHERE julianday(o.allocated_at) >= julianday(?) - 14 GROUP BY u.item_id""", (today,)):
        use[r["item"]] = r["n"] / 14.0
    kits = val(conn, """SELECT COUNT(*) FROM customer_order o JOIN order_line ol ON ol.order_id = o.order_id AND ol.line_no = 1
                        WHERE julianday(o.allocated_at) >= julianday(?) - 14""", (today,))
    use["CHG-1"] = kits / 14.0
    return use


def _forward_use(conn, today, days=14):
    """Forward daily requirement from the MPS / CM commit, exploded one BOM level with effectivity."""
    plans = q(conn, """SELECT plan_date, item_id, SUM(qty) qty FROM build_plan
                       WHERE plan_date > ? AND plan_date <= date(?, '+%d days') GROUP BY plan_date, item_id""" % days,
              (today, today))
    bom = q(conn, "SELECT parent_item_id, child_item_id, qty_per, eff_from, eff_to FROM bom_line WHERE bom_level IN ('OEM','CM')")
    by_parent = {}
    for b in bom:
        by_parent.setdefault(b["parent_item_id"], []).append(b)
    need = {}
    for p in plans:
        for b in by_parent.get(p["item_id"], []):
            if b["eff_from"] <= p["plan_date"] and (b["eff_to"] is None or b["eff_to"] > p["plan_date"]):
                need[b["child_item_id"]] = need.get(b["child_item_id"], 0) + p["qty"] * b["qty_per"]
    return {k: v / float(days) for k, v in need.items()}


@get(r"^/api/inventory/overview$")
def overview(req):
    conn = req.conn
    today = as_of(conn)
    items = {r["item_id"]: r for r in q(conn, "SELECT item_id, name, kind, uom, make_buy, std_cost, lifecycle, safety_stock FROM item")}
    pos = _positions(conn)
    use = _daily_use(conn, today)
    fwd = _forward_use(conn, today)

    ech = {e[0]: {"id": e[0], "label": e[1], "owner": e[2], "sub": e[3], "qty_units": 0, "value": 0.0, "items": set(),
                  "buckets": {}} for e in ECHELONS}
    per_item = {}
    for e, item, bucket, qty, site, detail in pos:
        it = items.get(item)
        if not it:
            continue
        value = (qty or 0) * (it["std_cost"] or 0)
        E = ech[e]
        if it["uom"] == "EA":
            E["qty_units"] += qty or 0
        E["value"] += value
        E["items"].add(item)
        E["buckets"][bucket] = E["buckets"].get(bucket, 0) + (qty or 0)
        p = per_item.setdefault(item, {"item_id": item, "name": it["name"], "kind": it["kind"], "uom": it["uom"],
                                       "std_cost": it["std_cost"], "lifecycle": it["lifecycle"],
                                       "safety_stock": it["safety_stock"], "by": {k[0]: 0 for k in ECHELONS},
                                       "available": 0, "allocated": 0, "hold": 0, "value_oem": 0.0, "value_all": 0.0})
        p["by"][e] += qty or 0
        p["value_all"] += value
        if ech[e]["owner"] == "OEM":
            p["value_oem"] += value
        if e == "3PL":
            key = {"Available": "available", "Allocated": "allocated", "On hold": "hold"}[bucket]
            p[key] += qty or 0
    item_rows = []
    for item, p in per_item.items():
        d = use.get(item)
        basis = "trailing 14 days"
        if p["kind"] not in ("VEHICLE", "PACK") and item != "CHG-1" and item in fwd:
            d, basis = fwd[item], "next 14 days of MPS / CM commit"
        elif p["kind"] not in ("VEHICLE", "PACK") and item != "CHG-1" and item not in fwd and p["lifecycle"] == "PHASE_OUT":
            d, basis = 0.0, "phase-out: no forward requirement"
        if p["kind"] in ("VEHICLE", "PACK") or item == "CHG-1":
            usable = p["available"]
            where = "3PL"
        elif p["by"]["CM_CONSIGNED"]:
            usable, where = p["by"]["CM_CONSIGNED"], "CM_CONSIGNED"
        elif p["by"]["PLANT"]:
            usable, where = p["by"]["PLANT"], "PLANT"
        elif p["by"]["CM_OWNED"]:
            usable, where = p["by"]["CM_OWNED"], "CM_OWNED"
        else:
            usable, where = None, None
        p["trailing_use"] = use.get(item)
        p["use_basis"] = basis
        p["daily_use"] = d
        p["point_of_use"] = where
        p["usable"] = usable
        p["dos"] = (usable / d) if (usable is not None and d) else None
        p["in_transit"] = p["by"]["INBOUND"] + p["by"]["TRANSIT"]
        item_rows.append(p)
    order = {"VEHICLE": 0, "PACK": 1, "MODULE": 2, "COMPONENT": 3, "MATERIAL": 4, "KIT": 5}
    item_rows.sort(key=lambda p: (order.get(p["kind"], 9), p["item_id"]))
    echelons = []
    for e in ECHELONS:
        E = ech[e[0]]
        echelons.append({**{k: v for k, v in E.items() if k not in ("items", "buckets")}, "items": len(E["items"]),
                         "buckets": [{"bucket": b, "qty": qv} for b, qv in sorted(E["buckets"].items(), key=lambda x: -x[1])]})

    # --- insights -------------------------------------------------------------------
    insights = {}
    dub = per_item.get("DU-B")
    eco = q1(conn, "SELECT eco_id, effective_date, effective_serial, title FROM eco WHERE eco_id='ECO-0042'")
    cutin = val(conn, "SELECT MIN(parent_serial) FROM genealogy WHERE child_item_id='DU-C' AND source='CM_FEED'")
    if dub and dub["by"]["CM_CONSIGNED"]:
        insights["stranded"] = {"item_id": "DU-B", "qty": dub["by"]["CM_CONSIGNED"],
                                "value": dub["by"]["CM_CONSIGNED"] * (dub["std_cost"] or 0), "eco": eco, "cut_in": cutin}
    dev = q1(conn, "SELECT deviation_id, qty_limit, qty_used, valid_to, status FROM deviation WHERE deviation_id='DEV-0012'")
    bms_b = (per_item.get("BMS-B") or {}).get("by", {}).get("PLANT", 0)
    bms_a = (per_item.get("BMS-A") or {}).get("by", {}).get("PLANT", 0)
    bms_use = (use.get("BMS-B") or 0) + (use.get("BMS-A") or 0)
    allowance = max(0, (dev["qty_limit"] - dev["qty_used"])) if dev else 0
    next_rcpt = q1(conn, """SELECT pl.po_id, pl.line_no, pl.qty, pl.need_date, pl.promise_date FROM po_line pl
                            JOIN purchase_order po ON po.po_id = pl.po_id
                            WHERE pl.item_id='BMS-B' AND pl.status='OPEN' AND po.ship_to_site_id='OEM-FRE'
                            ORDER BY COALESCE(pl.promise_date, pl.need_date) LIMIT 1""")
    insights["bms"] = {"b_on_hand": bms_b, "a_on_hand": bms_a, "a_allowance": allowance, "deviation": dev,
                       "daily_use": bms_use, "cover_days": ((bms_b + min(bms_a, allowance)) / bms_use) if bms_use else None,
                       "next_receipt": next_rcpt}
    dev17 = q1(conn, "SELECT deviation_id, qty_limit, qty_used, valid_to, status FROM deviation WHERE deviation_id='DEV-0017'")
    if dev17:
        after = val(conn, """SELECT COUNT(*) FROM genealogy WHERE child_item_id='GSK-A' AND source='OEM_MES'
                             AND substr(installed_at, 1, 10) > ?""", (dev17["valid_to"],))
        insights["gasket"] = {"deviation": dev17, "installed_after_expiry": after,
                              "gsk_b_trailing": use.get("GSK-B"), "gsk_b_forward": fwd.get("GSK-B")}
    insights["freshness"] = q(conn, """SELECT site_id, item_id, MIN(as_of) as_of, source FROM inventory_balance
                                       WHERE owner='SUPPLIER' GROUP BY site_id, item_id ORDER BY as_of""")
    insights["unaccounted"] = q(conn, """
        SELECT u.serial, u.item_id, s.shipment_id, s.container_no, s.received_at
        FROM unit u JOIN shipment_unit su ON su.serial = u.serial
        JOIN shipment s ON s.shipment_id = su.shipment_id AND s.leg = 'CM_TO_3PL'
        WHERE u.status = 'IN_TRANSIT' AND s.received_at IS NOT NULL""")
    insights["cm_stock"] = q(conn, """
        SELECT r.report_date, r.item_id, r.on_hand excel,
               (SELECT COUNT(*) FROM unit u WHERE u.item_id = r.item_id AND u.status='COMPONENT'
                  AND u.location_site_id='CM-TXG') system
        FROM cm_stock_report r WHERE r.report_date = (SELECT MAX(report_date) FROM cm_stock_report)""")
    return {"as_of": today, "now": now(conn), "echelons": echelons, "items": item_rows, "insights": insights,
            "wms": _wms_recon(conn),
            "fre_lots": q(conn, """
                SELECT b.lot_id, b.item_id, i.name, b.qty, b.stock_status, l.iqc_status, l.supplier_id, l.received_at,
                       l.mfg_date, CAST(julianday(?) - julianday(l.received_at) AS INTEGER) age_days,
                       l.po_id, l.po_line_no
                FROM inventory_balance b JOIN lot l ON l.lot_id = b.lot_id JOIN item i ON i.item_id = b.item_id
                WHERE b.site_id = 'OEM-FRE' ORDER BY b.item_id, l.received_at""", (now(conn),)),
            "customers": q(conn, """SELECT item_id, status, COUNT(*) n FROM unit WHERE status IN ('SHIPPED','DELIVERED')
                                    AND item_id IN (SELECT item_id FROM item WHERE kind IN ('VEHICLE','PACK'))
                                    GROUP BY item_id, status""")}


def _wms_recon(conn):
    snap = val(conn, "SELECT MAX(snapshot_at) FROM wms_snapshot")
    if not snap:
        return None
    wms = {r["item_id"]: r for r in q(conn, "SELECT * FROM wms_snapshot WHERE snapshot_at = ?", (snap,))}
    sysrows = q(conn, """
        SELECT u.item_id,
               SUM(CASE WHEN h.hold_id IS NULL THEN 1 ELSE 0 END) available,
               SUM(CASE WHEN h.hold_id IS NOT NULL THEN 1 ELSE 0 END) hold
        FROM shipment s
        JOIN shipment_unit su ON su.shipment_id = s.shipment_id
        JOIN unit u ON u.serial = su.serial
        LEFT JOIN hold h ON h.serial = u.serial AND julianday(h.placed_at) <= julianday(:S)
             AND (h.released_at IS NULL OR julianday(h.released_at) > julianday(:S))
        WHERE s.leg IN ('CM_TO_3PL','PLANT_TO_3PL') AND s.received_at IS NOT NULL
          AND julianday(s.received_at) <= julianday(:S)
          AND u.status IN ('AT_3PL','ALLOCATED','SHIPPED','DELIVERED','RETURNED')
          AND NOT EXISTS (SELECT 1 FROM order_line ol JOIN customer_order o ON o.order_id = ol.order_id
                          WHERE (ol.vehicle_serial = u.serial OR ol.pack_serial = u.serial)
                            AND julianday(o.allocated_at) <= julianday(:S))
          AND NOT EXISTS (SELECT 1 FROM genealogy g WHERE g.child_serial = u.serial AND g.source = 'SERVICE'
                            AND julianday(g.installed_at) <= julianday(:S))
        GROUP BY u.item_id""", {"S": snap})
    system = {r["item_id"]: r for r in sysrows}
    service = {r["item_id"]: r["n"] for r in q(conn, """
        SELECT u.item_id, COUNT(*) n FROM genealogy g JOIN unit u ON u.serial = g.child_serial
        WHERE g.source = 'SERVICE' AND julianday(g.installed_at) <= julianday(?) GROUP BY u.item_id""", (snap,))}
    rows = []
    for sku in sorted(set(wms) | set(system)):
        w = wms.get(sku)
        s = system.get(sku) or {"available": 0, "hold": 0}
        wa, wh = (w["qty_available"], w["qty_hold"]) if w else (0, 0)
        diff_a, diff_h = wa - s["available"], wh - s["hold"]
        causes = []
        svc = service.get(sku, 0)
        if diff_a and svc and 0 < diff_a <= svc:
            causes.append(f"{diff_a} warranty-swap pull(s) left the shelf without a WMS issue transaction")
        elif diff_a:
            causes.append("cycle-count variance: WMS counts a unit the serial scans don't support" if diff_a > 0
                          else "units scanned in our system but missing from WMS available")
        if diff_h:
            causes.append("hold status differs between WMS and our hold table")
        rows.append({"sku": sku, "wms_available": wa, "wms_hold": wh, "system_available": s["available"],
                     "system_hold": s["hold"], "diff_available": diff_a, "diff_hold": diff_h, "causes": causes})
    return {"snapshot_at": snap, "rows": rows}


@get(r"^/api/inventory/item$")
def item_detail(req):
    conn = req.conn
    item_id = req.arg("id")
    if not item_id:
        raise HttpError(400, "id is required")
    it = q1(conn, """SELECT i.*, s.name supplier_name, s.tier FROM item i LEFT JOIN supplier s ON s.supplier_id = i.primary_supplier_id
                     WHERE i.item_id = ?""", (item_id,))
    if not it:
        raise HttpError(404, f"item {item_id} not found")
    pos = [{"echelon": e, "bucket": b, "qty": qty, "site_id": site, "detail": d} for e, i2, b, qty, site, d in _positions(conn)
           if i2 == item_id]
    lots = q(conn, """SELECT l.lot_id, l.site_id, l.supplier_id, l.qty_received, l.received_at, l.iqc_status, l.mfg_date,
                             (SELECT SUM(b.qty) FROM inventory_balance b WHERE b.lot_id = l.lot_id) on_hand
                      FROM lot l WHERE l.item_id = ? ORDER BY l.received_at DESC LIMIT 12""", (item_id,))
    open_po = q(conn, """SELECT pl.po_id, pl.line_no, po.supplier_id, po.ship_to_site_id, pl.qty, pl.received_qty, pl.need_date,
                                pl.promise_date, pl.confirm_status FROM po_line pl JOIN purchase_order po ON po.po_id = pl.po_id
                         WHERE pl.item_id = ? AND pl.status = 'OPEN' ORDER BY COALESCE(pl.promise_date, pl.need_date) LIMIT 8""",
                  (item_id,))
    use = _daily_use(conn, as_of(conn)).get(item_id)
    units = q(conn, """SELECT serial, status, location_site_id, on_hold, built_at FROM unit WHERE item_id = ?
                       AND status IN ('AT_3PL','ALLOCATED','COMPONENT','BUILT','WIP','IN_TRANSIT')
                       ORDER BY COALESCE(built_at, serial) DESC LIMIT 40""", (item_id,))
    policy = q(conn, "SELECT * FROM replenishment_policy WHERE item_id = ?", (item_id,))
    return {"item": it, "positions": pos, "lots": lots, "open_po": open_po, "daily_use": use, "units": units,
            "policies": policy}
