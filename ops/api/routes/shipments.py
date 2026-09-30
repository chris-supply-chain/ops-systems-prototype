"""Shipments & Customs (TMS): the physical pipeline from the CM in Taichung to the
customer's door, with ETA risk, customs entries (ISF timing, assists, duty), DG trucks
from the pack line, inbound supplier legs, last-mile carriers and freight spend.
"""
import datetime as dt
import json

from ...db import as_of, now, q, q1, val
from ...logic import sourcing as S
from ..router import HttpError, get

TS = S.TS
SLA_DAYS = {"WEST": 3, "MOUNTAIN": 4, "CENTRAL": 5, "EAST": 6}
LM_RATE = {("CWF", "WEST"): 165.0, ("CWF", "FLEET"): 140.0, ("PPG", "MOUNTAIN"): 129.0, ("PPG", "CENTRAL"): 149.0,
           ("PPG", "EAST"): 169.0}

OCEAN_SQL = f"""
SELECT s.shipment_id, s.container_no, s.vessel, s.voyage, s.booking_ref, s.bol_no, s.status, s.asn_qty, s.freight_usd,
       {TS.format('s.etd_planned')} AS etd_planned, {TS.format('s.eta_planned')} AS eta_planned,
       {TS.format('s.eta_current')} AS eta_current, {TS.format('s.atd')} AS atd, {TS.format('s.ata')} AS ata,
       {TS.format('s.received_at')} AS received_at,
       ROUND(julianday(s.eta_current) - julianday(s.eta_planned), 1) AS eta_slip_days,
       ROUND(julianday(s.ata) - julianday(s.atd), 1) AS transit_days,
       (SELECT COUNT(*) FROM shipment_unit su WHERE su.shipment_id = s.shipment_id) AS units,
       (SELECT COUNT(*) FROM shipment_unit su JOIN unit u USING (serial)
         WHERE su.shipment_id = s.shipment_id AND u.status = 'IN_TRANSIT') AS units_in_transit,
       (SELECT COUNT(*) FROM shipment_unit su JOIN unit u USING (serial)
         WHERE su.shipment_id = s.shipment_id AND u.on_hold = 1) AS units_on_hold,
       ce.entry_no, ce.status AS customs_status, ce.exam_type,
       {TS.format('ce.isf_filed_at')} AS isf_filed_at,
       (SELECT {TS.format('MIN(e.event_ts)')} FROM shipment_event e WHERE e.shipment_id = s.shipment_id AND e.code = 'LOADED') AS loaded_at,
       (SELECT COUNT(*) FROM shipment_event e WHERE e.shipment_id = s.shipment_id AND e.code = 'ETA_UPDATE') AS eta_updates
FROM shipment s LEFT JOIN customs_entry ce ON ce.shipment_id = s.shipment_id
WHERE s.leg = 'CM_TO_3PL'
ORDER BY s.etd_planned DESC, s.shipment_id
"""


def _ocean(conn):
    rows = q(conn, OCEAN_SQL)
    for r in rows:
        flags = []
        if r["status"] in ("ON_WATER", "BOOKED", "GATED_IN") and (r["eta_slip_days"] or 0) >= 1:
            flags.append("DELAYED")
        if r["status"] == "CUSTOMS_HOLD" or r["customs_status"] == "EXAM":
            flags.append("CUSTOMS_HOLD")
        if r["received_at"] and r["units_in_transit"]:
            flags.append("SHORT")
        isf_h = None
        if r["isf_filed_at"] and r["loaded_at"]:
            isf_h = _hours_between(r["isf_filed_at"], r["loaded_at"])
            if isf_h < 24:
                flags.append("LATE_ISF")
        r["isf_hours_before_loading"] = isf_h
        r["units_received"] = (r["units"] - r["units_in_transit"]) if r["received_at"] else 0
        r["flags"] = flags
    return rows


def _hours_between(a, b):
    t0 = dt.datetime.strptime(a, "%Y-%m-%dT%H:%M:%SZ")
    t1 = dt.datetime.strptime(b, "%Y-%m-%dT%H:%M:%SZ")
    return round((t1 - t0).total_seconds() / 3600.0, 1)


def _biz_days(d0, d1):
    """Carrier transit days: count days after d0 up to d1, skipping Sundays (carriers deliver Mon-Sat)."""
    n, d = 0, d0
    while d < d1:
        d += dt.timedelta(days=1)
        if d.weekday() != 6:
            n += 1
    return n


@get(r"^/api/shipments/overview$")
def overview(req):
    c = req.conn
    today = as_of(c)
    ocean = _ocean(c)
    unit_count = lambda sql, args=(): val(c, sql, args) or 0
    by_vessel = {}
    for r in ocean:
        if r["status"] in ("ON_WATER",):
            v = by_vessel.setdefault((r["vessel"], r["voyage"]), {"vessel": r["vessel"], "voyage": r["voyage"], "containers": 0,
                                                                  "units": 0, "eta_planned": r["eta_planned"],
                                                                  "eta_current": r["eta_current"], "delay_days": r["eta_slip_days"],
                                                                  "etd": r["atd"] or r["etd_planned"]})
            v["containers"] += 1
            v["units"] += r["units"]
    lanes = {
        "cm_wip": unit_count("SELECT COUNT(*) FROM unit WHERE item_id LIKE 'LV1-%' AND status = 'WIP'"),
        "cm_dock": unit_count("""SELECT COUNT(*) FROM unit u WHERE u.item_id LIKE 'LV1-%' AND u.status = 'BUILT'
                                 AND u.serial NOT IN (SELECT su.serial FROM shipment_unit su JOIN shipment s USING (shipment_id)
                                                      WHERE s.leg = 'CM_TO_3PL')"""),
        "port_origin": sum(r["units"] for r in ocean if r["status"] in ("BOOKED", "GATED_IN")),
        "port_origin_ctr": sum(1 for r in ocean if r["status"] in ("BOOKED", "GATED_IN")),
        "ocean": sum(r["units"] for r in ocean if r["status"] == "ON_WATER"),
        "ocean_ctr": sum(1 for r in ocean if r["status"] == "ON_WATER"),
        "port_dest": sum(r["units"] for r in ocean if r["status"] in ("AT_PORT", "CUSTOMS_HOLD")),
        "port_dest_ctr": sum(1 for r in ocean if r["status"] in ("AT_PORT", "CUSTOMS_HOLD")),
        "customs_hold_ctr": sum(1 for r in ocean if "CUSTOMS_HOLD" in r["flags"]),
        "drayage": sum(r["units"] for r in ocean if r["status"] == "IN_TRANSIT"),
        "drayage_ctr": sum(1 for r in ocean if r["status"] == "IN_TRANSIT"),
        "reno_vehicles": unit_count("SELECT COUNT(*) FROM unit WHERE item_id LIKE 'LV1-%' AND status IN ('AT_3PL','ALLOCATED')"),
        "reno_packs": unit_count("SELECT COUNT(*) FROM unit WHERE item_id LIKE 'PK-%' AND status IN ('AT_3PL','ALLOCATED')"),
        "reno_hold": unit_count("SELECT COUNT(*) FROM unit WHERE location_site_id = '3PL-RNO' AND on_hold = 1"),
        "last_mile": unit_count("SELECT COUNT(*) FROM shipment WHERE leg = '3PL_TO_CUSTOMER'"
                                " AND status IN ('IN_TRANSIT','OUT_FOR_DELIVERY','EXCEPTION')"),
        "delivered_7d": unit_count("SELECT COUNT(*) FROM customer_order WHERE delivered_at IS NOT NULL"
                                   " AND julianday(?) - julianday(delivered_at) <= 7", (now(c),)),
        "fremont_packs": unit_count("""SELECT COUNT(*) FROM unit u WHERE u.item_id LIKE 'PK-%' AND u.status = 'BUILT'"""),
        "fremont_wip": unit_count("SELECT COUNT(*) FROM unit WHERE item_id LIKE 'PK-%' AND status = 'WIP'"),
        "dg_truck": unit_count("""SELECT COUNT(*) FROM shipment_unit su JOIN shipment s USING (shipment_id)
                                  WHERE s.leg = 'PLANT_TO_3PL' AND s.status != 'RECEIVED'"""),
        "dg_truck_ctr": unit_count("SELECT COUNT(*) FROM shipment WHERE leg = 'PLANT_TO_3PL' AND status != 'RECEIVED'"),
        "short_units": sum(r["units_in_transit"] for r in ocean if "SHORT" in r["flags"]),
    }
    inbound = _inbound(c)
    delayed = [r for r in ocean if "DELAYED" in r["flags"]]
    held = [r for r in ocean if "CUSTOMS_HOLD" in r["flags"]]
    short = [r for r in ocean if "SHORT" in r["flags"]]
    late_isf = [r for r in ocean if "LATE_ISF" in r["flags"]]
    lm = _last_mile_stats(c, 30)
    spend30 = _spend(c, 30)
    open_orders = val(c, "SELECT COUNT(*) FROM customer_order WHERE status = 'OPEN'")
    return {
        "as_of": today,
        "kpis": {
            "containers_on_water": lanes["ocean_ctr"], "units_on_water": lanes["ocean"],
            "delayed_units": sum(r["units"] for r in delayed), "delay_days": max((r["eta_slip_days"] or 0 for r in delayed), default=0),
            "customs_holds": len(held), "short_units": lanes["short_units"],
            "last_mile_in_flight": lanes["last_mile"], "delivered_7d": lanes["delivered_7d"],
            "last_mile_sla": lm["sla_rate"], "freight_30d": spend30["total"],
            "freight_per_vehicle": spend30["per_vehicle"], "open_orders": open_orders,
        },
        "lanes": lanes, "vessels": sorted(by_vessel.values(), key=lambda v: v["eta_current"] or ""),
        "inbound": inbound,
        "stories": {"delayed": delayed, "held": held, "short": short, "late_isf": late_isf},
    }


def _inbound(conn):
    rows = q(conn, f"""SELECT s.shipment_id, s.mode, s.carrier, s.origin_site_id, os.name AS origin_name, s.dest_site_id,
                             ds.name AS dest_name, {TS.format('s.etd_planned')} AS etd, {TS.format('s.eta_current')} AS eta,
                             s.status, s.asn_qty, s.dg_class
                      FROM shipment s JOIN site os ON os.site_id = s.origin_site_id LEFT JOIN site ds ON ds.site_id = s.dest_site_id
                      WHERE s.leg = 'SUPPLIER_TO_PLANT' ORDER BY s.eta_current""")
    for r in rows:
        parts = r["shipment_id"].split("-")
        if len(parts) == 3:
            pl = q1(conn, "SELECT pl.po_id, pl.line_no, pl.item_id, i.name, pl.promise_date, pl.need_date FROM po_line pl"
                          " JOIN item i USING (item_id) WHERE pl.po_id = ? AND pl.line_no = ?", (parts[1], int(parts[2])))
            r["po_line"] = pl
    return rows


@get(r"^/api/shipments/ocean$")
def ocean(req):
    rows = _ocean(req.conn)
    return {"rows": rows, "planned_transit_days": 15}


@get(r"^/api/shipments/customs$")
def customs(req):
    c = req.conn
    rows = q(c, f"""SELECT ce.entry_no, ce.shipment_id, s.container_no, s.vessel, s.voyage, s.asn_qty, ce.broker, ce.hts_code,
                          ce.entered_value, ce.duty_rate, ce.duty_usd, ce.mpf_usd, ce.hmf_usd, ce.status, ce.exam_type,
                          {TS.format('ce.isf_filed_at')} AS isf_filed_at, {TS.format('ce.entry_filed_at')} AS entry_filed_at,
                          {TS.format('ce.released_at')} AS released_at, {TS.format('s.ata')} AS ata,
                          (SELECT {TS.format('MIN(e.event_ts)')} FROM shipment_event e WHERE e.shipment_id = s.shipment_id
                             AND e.code = 'LOADED') AS loaded_at
                   FROM customs_entry ce JOIN shipment s USING (shipment_id) ORDER BY ce.isf_filed_at DESC""")
    for r in rows:
        r["isf_hours_before_loading"] = (_hours_between(r["isf_filed_at"], r["loaded_at"])
                                         if r["isf_filed_at"] and r["loaded_at"] else None)
        r["value_per_unit"] = round(r["entered_value"] / r["asn_qty"], 2) if r["asn_qty"] else None
    # assists: consigned parts the OEM supplies to the CM free of charge are dutiable and belong in entered value
    assists = []
    for item in ("DU-C", "HMI-1", "PU-1"):
        p = S.price_on(c, item, q1(c, "SELECT primary_supplier_id FROM item WHERE item_id = ?", (item,))["primary_supplier_id"],
                       as_of(c))
        assists.append({"item_id": item, "name": q1(c, "SELECT name FROM item WHERE item_id = ?", (item,))["name"],
                        "unit_price": p["unit_price"] if p else None})
    cm = S.price_on(c, "LV1-SLATE", "FAP", as_of(c))
    totals = {k: round(sum((r[k] or 0) for r in rows), 2) for k in ("entered_value", "duty_usd", "mpf_usd", "hmf_usd")}
    return {"rows": rows, "totals": totals, "assists": assists, "cm_price": cm["unit_price"] if cm else None,
            "late_isf": sum(1 for r in rows if r["isf_hours_before_loading"] is not None and r["isf_hours_before_loading"] < 24),
            "exams": sum(1 for r in rows if r["status"] == "EXAM")}


def _last_mile_rows(conn, days=None):
    rows = q(conn, f"""SELECT s.shipment_id, s.order_id, s.carrier, s.mode, s.tracking_no, s.status, {TS.format('s.atd')} AS shipped_at,
                             {TS.format('s.ata')} AS delivered_at, o.ship_to_region AS region, o.ship_to_state AS state, o.channel,
                             o.promised_date, o.first_promised_date,
                             (SELECT COUNT(*) FROM shipment_event e WHERE e.shipment_id = s.shipment_id AND e.code = 'EXCEPTION') AS exceptions
                      FROM shipment s JOIN customer_order o USING (order_id)
                      WHERE s.leg = '3PL_TO_CUSTOMER' {"AND julianday(:now) - julianday(s.atd) <= :days" if days else ""}""",
             {"now": now(conn), "days": days})
    for r in rows:
        r["region_key"] = "FLEET" if r["channel"] == "FLEET" else r["region"]
        if r["delivered_at"] and r["shipped_at"]:
            d0, d1 = S.to_date(r["shipped_at"]), S.to_date(r["delivered_at"])
            r["transit_days"] = _biz_days(d0, d1)
            r["sla_days"] = SLA_DAYS.get(r["region"], 5)
            r["within_sla"] = r["transit_days"] <= r["sla_days"]
            promise = r["first_promised_date"] or r["promised_date"]
            r["on_time_promise"] = (d1.isoformat() <= promise) if promise else None
        else:
            r["transit_days"] = r["within_sla"] = r["on_time_promise"] = None
        r["rate_usd"] = LM_RATE.get((r["carrier"], r["region_key"])) or LM_RATE.get((r["carrier"], r["region"])) or \
            (165.0 if r["carrier"] == "CWF" else 149.0)
    return rows


def _last_mile_stats(conn, days):
    rows = [r for r in _last_mile_rows(conn, days) if r["within_sla"] is not None]
    return {"n": len(rows), "sla_rate": (sum(1 for r in rows if r["within_sla"]) / len(rows)) if rows else None}


@get(r"^/api/shipments/last-mile$")
def last_mile(req):
    c = req.conn
    days = req.arg("days", 60, int)
    rows = _last_mile_rows(c, days)
    groups = {}
    for r in rows:
        g = groups.setdefault((r["carrier"], r["region"]), {"carrier": r["carrier"], "region": r["region"], "shipments": 0,
                                                            "delivered": 0, "within_sla": 0, "transit": [], "exceptions": 0,
                                                            "on_time_promise": 0, "promised": 0, "sla_days": SLA_DAYS.get(r["region"])})
        g["shipments"] += 1
        g["exceptions"] += 1 if r["exceptions"] else 0
        if r["transit_days"] is not None:
            g["delivered"] += 1
            g["within_sla"] += 1 if r["within_sla"] else 0
            g["transit"].append(r["transit_days"])
        if r["on_time_promise"] is not None:
            g["promised"] += 1
            g["on_time_promise"] += 1 if r["on_time_promise"] else 0
    stats = []
    for g in groups.values():
        t = g.pop("transit")
        g["avg_transit"] = round(sum(t) / len(t), 2) if t else None
        g["sla_rate"] = g["within_sla"] / g["delivered"] if g["delivered"] else None
        g["otd_promise"] = g["on_time_promise"] / g["promised"] if g["promised"] else None
        g["dist"] = {str(k): t.count(k) for k in sorted(set(t))}
        stats.append(g)
    stats.sort(key=lambda g: (g["carrier"], ["WEST", "MOUNTAIN", "CENTRAL", "EAST"].index(g["region"])
                              if g["region"] in SLA_DAYS else 9))
    carriers = {}
    for g in stats:
        cc = carriers.setdefault(g["carrier"], {"carrier": g["carrier"], "shipments": 0, "delivered": 0, "within_sla": 0,
                                                 "exceptions": 0})
        for k in ("shipments", "delivered", "within_sla", "exceptions"):
            cc[k] += g[k]
    for cc in carriers.values():
        cc["sla_rate"] = cc["within_sla"] / cc["delivered"] if cc["delivered"] else None
        cc["name"] = val(c, "SELECT name FROM carrier WHERE carrier_id = ?", (cc["carrier"],))
    in_flight = [r for r in _last_mile_rows(c) if r["status"] in ("IN_TRANSIT", "OUT_FOR_DELIVERY", "EXCEPTION")]
    exc = q(c, f"""SELECT e.shipment_id, {TS.format('e.event_ts')} AS event_ts, e.location, s.order_id, s.carrier, s.status,
                          o.ship_to_state AS state
                   FROM shipment_event e JOIN shipment s USING (shipment_id) JOIN customer_order o USING (order_id)
                   WHERE e.code = 'EXCEPTION' ORDER BY e.event_ts DESC LIMIT 40""")
    promised = sum(1 for r in rows if r["on_time_promise"] is not None)
    return {"days": days, "stats": stats, "carriers": list(carriers.values()), "in_flight": in_flight, "exceptions": exc,
            "promises_available": promised > 0, "sla_days": SLA_DAYS}


@get(r"^/api/shipments/trucks$")
def trucks(req):
    c = req.conn
    rows = q(c, f"""SELECT s.shipment_id, s.tracking_no AS pro, s.carrier, s.status, s.dg_class, s.freight_usd, s.asn_qty,
                          {TS.format('s.atd')} AS picked_up, {TS.format('s.ata')} AS arrived, {TS.format('s.received_at')} AS received_at,
                          {TS.format('s.etd_planned')} AS etd_planned,
                          (SELECT COUNT(*) FROM shipment_unit su JOIN unit u USING (serial) WHERE su.shipment_id = s.shipment_id
                             AND u.item_id = 'PK-STD') AS std,
                          (SELECT COUNT(*) FROM shipment_unit su JOIN unit u USING (serial) WHERE su.shipment_id = s.shipment_id
                             AND u.item_id = 'PK-LRG') AS lrg
                   FROM shipment s WHERE s.leg = 'PLANT_TO_3PL' ORDER BY s.etd_planned DESC""")
    for r in rows:
        r["per_pack"] = round(r["freight_usd"] / r["asn_qty"], 2) if r["asn_qty"] and r["freight_usd"] else None
    return {"rows": rows, "inbound": _inbound(c)}


def _spend(conn, days=None):
    cond = "AND julianday(:now) - julianday({col}) <= :days" if days else ""
    args = {"now": now(conn), "days": days}
    ocean = q(conn, f"SELECT date(s.atd) AS d, s.freight_usd AS usd, (SELECT COUNT(*) FROM shipment_unit su"
                    f" WHERE su.shipment_id = s.shipment_id) AS units FROM shipment s WHERE s.leg = 'CM_TO_3PL' AND s.atd IS NOT NULL "
                    + cond.format(col="s.atd"), args)
    dray = q(conn, "SELECT date(e.event_ts) AS d, 1450.0 AS usd FROM shipment_event e JOIN shipment s USING (shipment_id)"
                   " WHERE s.leg = 'CM_TO_3PL' AND e.code = 'OUT_GATE' " + cond.format(col="e.event_ts"), args)
    truck = q(conn, "SELECT date(s.atd) AS d, s.freight_usd AS usd FROM shipment s WHERE s.leg = 'PLANT_TO_3PL' AND s.atd IS NOT NULL "
                    + cond.format(col="s.atd"), args)
    lm = [r for r in _last_mile_rows(conn, days)]
    out = {"ocean": ocean, "drayage": dray, "dg_truck": truck,
           "last_mile": [{"d": r["shipped_at"][:10], "usd": r["rate_usd"]} for r in lm if r["shipped_at"]]}
    total = sum(x["usd"] or 0 for k in out for x in out[k])
    units = sum(r["units"] for r in ocean)
    per_vehicle = None
    if units:
        per_vehicle = round((sum(x["usd"] or 0 for x in ocean) + sum(x["usd"] for x in dray)) / units
                            + (sum(r["rate_usd"] for r in lm) / len(lm) if lm else 0), 2)
    return {"lanes": out, "total": round(total, 2), "per_vehicle": per_vehicle}


@get(r"^/api/shipments/freight$")
def freight(req):
    c = req.conn
    sp = _spend(c)
    weekly = {}
    for lane, rows in sp["lanes"].items():
        for r in rows:
            d = S.to_date(r["d"])
            wk = (d - dt.timedelta(days=d.weekday())).isoformat()
            weekly.setdefault(wk, {"week": wk, "ocean": 0.0, "drayage": 0.0, "dg_truck": 0.0, "last_mile": 0.0})
            weekly[wk][lane] += r["usd"] or 0
    rates = q(c, "SELECT fr.*, ca.name AS carrier_name, ca.mode FROM freight_rate fr JOIN carrier ca ON ca.carrier_id = fr.carrier"
                 " ORDER BY fr.carrier, fr.lane, fr.valid_from")
    by_lane = {k: round(sum(x["usd"] or 0 for x in v), 2) for k, v in sp["lanes"].items()}
    gri = q1(c, "SELECT a.rate_usd AS old, b.rate_usd AS new, b.valid_from FROM freight_rate a JOIN freight_rate b"
                " ON a.carrier = b.carrier AND a.lane = b.lane AND a.valid_to = b.valid_from WHERE a.lane = 'TWTXG-USOAK'")
    return {"weekly": sorted(weekly.values(), key=lambda w: w["week"]), "rates": rates, "by_lane": by_lane,
            "total": sp["total"], "per_vehicle": sp["per_vehicle"], "gri": gri,
            "carriers": q(c, "SELECT * FROM carrier ORDER BY mode")}


@get(r"^/api/shipments/detail$")
def detail(req):
    c = req.conn
    sid = req.arg("id")
    if not sid:
        raise HttpError(400, "id is required")
    s = q1(c, f"""SELECT s.*, {TS.format('s.etd_planned')} AS etd_planned_z, {TS.format('s.eta_planned')} AS eta_planned_z,
                        {TS.format('s.eta_current')} AS eta_current_z, {TS.format('s.atd')} AS atd_z, {TS.format('s.ata')} AS ata_z,
                        {TS.format('s.received_at')} AS received_at_z, os.name AS origin_name, ds.name AS dest_name,
                        ca.name AS carrier_name, pol.name AS pol_name, pod.name AS pod_name
                 FROM shipment s JOIN site os ON os.site_id = s.origin_site_id LEFT JOIN site ds ON ds.site_id = s.dest_site_id
                 LEFT JOIN carrier ca ON ca.carrier_id = s.carrier LEFT JOIN site pol ON pol.site_id = s.pol_site_id
                 LEFT JOIN site pod ON pod.site_id = s.pod_site_id WHERE s.shipment_id = ?""", (sid,))
    if not s:
        s = q1(c, "SELECT shipment_id FROM shipment WHERE container_no = ? OR tracking_no = ?", (sid, sid))
        if s:
            req.query["id"] = s["shipment_id"]
            return detail(req)
        raise HttpError(404, f"shipment {sid} not found")
    for k in ("etd_planned", "eta_planned", "eta_current", "atd", "ata", "received_at"):
        s[k] = s.pop(k + "_z")
    events = q(c, f"""SELECT {TS.format('event_ts')} AS event_ts, code, location, detail, source, raw_ref FROM shipment_event
                      WHERE shipment_id = ? ORDER BY julianday(event_ts), event_id""", (sid,))
    for e in events:
        ref = e["raw_ref"] or ""
        if ref.startswith("raw_carrier_event:"):
            r = q1(c, "SELECT format, payload, ingest_status FROM raw_carrier_event WHERE raw_id = ?", (int(ref.split(":")[1]),))
            if r:
                e["raw"] = {"format": r["format"], "payload": r["payload"], "status": r["ingest_status"]}
        elif ref.startswith("raw_3pl_message:"):
            r = q1(c, "SELECT msg_type, ingest_status, ingest_note FROM raw_3pl_message WHERE raw_id = ?", (int(ref.split(":")[1]),))
            if r:
                e["raw"] = {"format": r["msg_type"], "status": r["ingest_status"], "note": r["ingest_note"]}
    units = q(c, """SELECT su.serial, u.item_id, u.status, u.on_hold, u.location_site_id FROM shipment_unit su JOIN unit u USING (serial)
                    WHERE su.shipment_id = ? ORDER BY su.serial LIMIT 400""", (sid,))
    entry = q1(c, f"""SELECT *, {TS.format('isf_filed_at')} AS isf_z, {TS.format('entry_filed_at')} AS entry_z,
                             {TS.format('released_at')} AS released_z FROM customs_entry WHERE shipment_id = ?""", (sid,))
    if entry:
        for k, z in (("isf_filed_at", "isf_z"), ("entry_filed_at", "entry_z"), ("released_at", "released_z")):
            entry[k] = entry.pop(z)
    order = q1(c, "SELECT order_id, status, ship_to_region, ship_to_state, channel, promised_date, delivered_at FROM customer_order"
                  " WHERE order_id = ?", (s["order_id"],)) if s["order_id"] else None
    po_line = None
    if s["leg"] == "SUPPLIER_TO_PLANT":
        parts = sid.split("-")
        if len(parts) == 3:
            po_line = q1(c, "SELECT pl.po_id, pl.line_no, pl.item_id, i.name, pl.qty, pl.promise_date FROM po_line pl"
                            " JOIN item i USING (item_id) WHERE pl.po_id = ? AND pl.line_no = ?", (parts[1], int(parts[2])))
    items = {}
    for u in units:
        items[u["item_id"]] = items.get(u["item_id"], 0) + 1
    in_transit = sum(1 for u in units if u["status"] == "IN_TRANSIT")
    return {"shipment": s, "events": events, "units": units, "unit_mix": items, "customs": entry, "order": order,
            "po_line": po_line, "short_units": [u["serial"] for u in units if u["status"] == "IN_TRANSIT"]
            if s["received_at"] and s["leg"] == "CM_TO_3PL" else [], "units_in_transit": in_transit}
