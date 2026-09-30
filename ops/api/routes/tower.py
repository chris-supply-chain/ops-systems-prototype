"""Control Tower: factory-to-door flow, headline KPIs, the exception queue and the
decisions waiting on a human."""
import datetime as dt

from ops.api.router import get
from ops.db import as_of, now, q, q1, val


def _containers(n):
    return f"{n} container" + ("" if n == 1 else "s")


def _day(s):
    return dt.date.fromisoformat(s[:10])


@get(r"^/api/tower$")
def tower(req):
    c = req.conn
    today = _day(as_of(c))
    now_s = now(c)
    d7 = (today - dt.timedelta(days=7)).isoformat()
    d30 = (today - dt.timedelta(days=30)).isoformat()

    # ---- flow: where every vehicle and pack is right now (factory -> door)
    v = {r["status"]: r["n"] for r in q(c, "SELECT CASE WHEN on_hold=1 AND status IN ('AT_3PL','ALLOCATED') THEN 'HOLD'"
                                           " ELSE status END status, COUNT(*) n FROM unit WHERE item_id LIKE 'LV1-%' GROUP BY 1")}
    p = {r["status"]: r["n"] for r in q(c, "SELECT CASE WHEN on_hold=1 AND status IN ('AT_3PL','ALLOCATED','BUILT') THEN 'HOLD'"
                                           " ELSE status END status, COUNT(*) n FROM unit WHERE item_id LIKE 'PK-%' GROUP BY 1")}
    ship = {r["status"]: {"n": r["n"], "units": r["units"]} for r in q(
        c, "SELECT status, COUNT(*) n, SUM(asn_qty) units FROM shipment WHERE leg='CM_TO_3PL' GROUP BY status")}
    gated = q1(c, """SELECT COUNT(DISTINCT s.shipment_id) n, COUNT(*) units FROM shipment s JOIN shipment_unit su USING(shipment_id)
                     JOIN unit u ON u.serial=su.serial WHERE s.leg='CM_TO_3PL' AND u.status='BUILT'""")
    at_port = q1(c, """SELECT COUNT(DISTINCT s.shipment_id) n, COUNT(*) units FROM shipment s JOIN shipment_unit su USING(shipment_id)
                       JOIN unit u ON u.serial=su.serial WHERE s.leg='CM_TO_3PL' AND u.status='IN_TRANSIT'
                       AND s.ata IS NOT NULL AND s.received_at IS NULL""")
    on_water = q1(c, """SELECT COUNT(DISTINCT s.shipment_id) n, COUNT(*) units FROM shipment s JOIN shipment_unit su USING(shipment_id)
                        JOIN unit u ON u.serial=su.serial WHERE s.leg='CM_TO_3PL' AND u.status='IN_TRANSIT' AND s.ata IS NULL""")
    missing = val(c, """SELECT COUNT(*) FROM shipment s JOIN shipment_unit su USING(shipment_id) JOIN unit u ON u.serial=su.serial
                        WHERE s.leg='CM_TO_3PL' AND s.received_at IS NOT NULL AND u.status='IN_TRANSIT'""") or 0
    delivered7 = val(c, "SELECT COUNT(*) FROM customer_order WHERE status='DELIVERED' AND delivered_at >= ?", (d7,)) or 0
    flow = [
        {"id": "cm", "label": "CM line · Taichung", "sub": "WIP + finished goods", "route": "#/production",
         "value": (v.get("WIP", 0) + v.get("BUILT", 0)) - (gated["units"] or 0),
         "detail": f"{v.get('WIP', 0)} WIP · {v.get('BUILT', 0) - (gated['units'] or 0)} awaiting container"},
        {"id": "port_tw", "label": "Port of Taichung", "sub": "gated in, not sailed", "route": "#/shipments",
         "value": gated["units"] or 0, "detail": _containers(gated['n'] or 0)},
        {"id": "ocean", "label": "Pacific", "sub": "on the water", "route": "#/shipments",
         "value": on_water["units"] or 0, "detail": _containers(on_water['n'] or 0)},
        {"id": "port_us", "label": "Oakland + customs", "sub": "discharged, clearing", "route": "#/shipments",
         "value": at_port["units"] or 0, "detail": _containers(at_port['n'] or 0)},
        {"id": "3pl", "label": "Sierra 3PL · Reno", "sub": "available · allocated · held", "route": "#/inventory",
         "value": v.get("AT_3PL", 0) + v.get("ALLOCATED", 0) + v.get("HOLD", 0),
         "detail": f"{v.get('AT_3PL', 0)} avail · {v.get('ALLOCATED', 0)} alloc · {v.get('HOLD', 0)} held"},
        {"id": "last_mile", "label": "Last mile", "sub": "out for delivery", "route": "#/shipments",
         "value": v.get("SHIPPED", 0), "detail": "Crossway · ParcelPro"},
        {"id": "door", "label": "Customer door", "sub": "delivered, last 7 days", "route": "#/atp", "value": delivered7,
         "detail": f"{v.get('DELIVERED', 0):,} delivered to date"},
    ]
    pack_flow = [
        {"id": "fre", "label": "Pack line · Fremont", "value": p.get("WIP", 0) + p.get("BUILT", 0),
         "detail": f"{p.get('WIP', 0)} WIP · {p.get('BUILT', 0)} FG", "route": "#/production"},
        {"id": "dg", "label": "DG truck", "value": val(c, "SELECT COUNT(*) FROM unit u JOIN shipment_unit su USING(serial)"
                                                         " JOIN shipment s USING(shipment_id) WHERE s.leg='PLANT_TO_3PL'"
                                                         " AND u.status='IN_TRANSIT'") or 0,
         "detail": "UN3480 class 9", "route": "#/shipments"},
        {"id": "pk3pl", "label": "3PL packs", "value": p.get("AT_3PL", 0) + p.get("ALLOCATED", 0) + p.get("HOLD", 0),
         "detail": f"{p.get('AT_3PL', 0)} avail · {p.get('ALLOCATED', 0)} alloc · {p.get('HOLD', 0)} held", "route": "#/inventory"},
    ]

    # ---- KPIs
    def built(prefix, since, until=None):
        sql = ("SELECT COUNT(*) FROM unit WHERE item_id LIKE ? AND built_at >= ?" + (" AND built_at < ?" if until else ""))
        return val(c, sql, (prefix, since) + ((until,) if until else ())) or 0
    prev7 = (today - dt.timedelta(days=14)).isoformat()
    plan7 = val(c, "SELECT SUM(qty) FROM build_plan WHERE plan_type='CM_COMMIT' AND plan_date >= ? AND plan_date < ?",
                (d7, today.isoformat())) or 0
    pplan7 = val(c, "SELECT SUM(qty) FROM build_plan WHERE plan_type='OEM_MPS' AND plan_date >= ? AND plan_date < ?",
                 (d7, today.isoformat())) or 0
    otd = q1(c, """SELECT COUNT(*) n, SUM(substr(delivered_at,1,10) <= first_promised_date) ok FROM customer_order
                   WHERE status='DELIVERED' AND delivered_at >= ?""", (d30,))
    backlog = val(c, "SELECT COUNT(*) FROM customer_order WHERE status='OPEN'") or 0
    lead = q1(c, """SELECT AVG(julianday(promised_date) - julianday(?)) d FROM customer_order WHERE status='OPEN'
                    AND ordered_at >= ?""", (today.isoformat(), d7))
    exc = {r["severity"]: r["n"] for r in q(c, "SELECT severity, COUNT(*) n FROM ops_exception WHERE status!='RESOLVED'"
                                               " GROUP BY severity")}
    d70 = (today - dt.timedelta(days=today.weekday() + 7 * 9)).isoformat()
    wk = "date(substr({col},1,10), '-' || ((CAST(strftime('%w', substr({col},1,10)) AS INTEGER) + 6) % 7) || ' days')"
    built_w = {r["week"]: r for r in q(c, f"""SELECT {wk.format(col='built_at')} week, SUM(item_id LIKE 'LV1-%') veh,
                                                  SUM(item_id LIKE 'PK-%') pk FROM unit WHERE built_at >= ?
                                                  AND (item_id LIKE 'LV1-%' OR item_id LIKE 'PK-%') GROUP BY 1""", (d70,))}
    plan_w = {r["week"]: r for r in q(c, f"""SELECT {wk.format(col='plan_date')} week,
                                                 SUM(CASE WHEN plan_type='CM_COMMIT' THEN qty END) veh_plan,
                                                 SUM(CASE WHEN plan_type='OEM_MPS' THEN qty END) pk_plan
                                          FROM build_plan WHERE plan_date >= ? AND plan_date < ? GROUP BY 1""",
                                       (d70, today.isoformat()))}
    daily = [{"week": w, "veh": (built_w.get(w) or {}).get("veh") or 0, "pk": (built_w.get(w) or {}).get("pk") or 0,
              "veh_plan": (plan_w.get(w) or {}).get("veh_plan") or 0, "pk_plan": (plan_w.get(w) or {}).get("pk_plan") or 0}
             for w in sorted(set(built_w) | set(plan_w))
             if w < (today - dt.timedelta(days=today.weekday())).isoformat() or today.weekday() >= 5]   # weekends: the week is done
    deliveries = q(c, """SELECT substr(delivered_at,1,10) day, COUNT(*) n,
                                SUM(substr(delivered_at,1,10) <= first_promised_date) ontime
                         FROM customer_order WHERE status='DELIVERED' AND delivered_at >= ? GROUP BY 1 ORDER BY 1""", (d30,))
    kpis = {
        "vehicles_7d": built("LV1-%", d7), "vehicles_prev7": built("LV1-%", prev7, d7), "vehicles_plan7": plan7,
        "packs_7d": built("PK-%", d7), "packs_prev7": built("PK-%", prev7, d7), "packs_plan7": pplan7,
        "on_water": on_water["units"] or 0, "containers_on_water": on_water["n"] or 0,
        "backlog": backlog, "promise_lead_days": round(lead["d"], 1) if lead and lead["d"] is not None else None,
        "otd_30d": (otd["ok"] or 0) / otd["n"] if otd and otd["n"] else None, "delivered_30d": otd["n"] if otd else 0,
        "exceptions": exc, "missing_units": missing,
        "fpy_7d": q1(c, """SELECT SUM(first_pass)*1.0/SUM(units) f FROM v_fpy_daily WHERE day >= ?
                          AND station_id LIKE 'TXG-%-S60'""", (d7,))["f"],
    }

    # ---- queues
    exceptions = q(c, """SELECT e.*, d.status AS decision_status, d.title AS decision_title FROM ops_exception e
                         LEFT JOIN decision_log d ON d.decision_id = e.decision_id
                         WHERE e.status != 'RESOLVED'
                         ORDER BY CASE e.severity WHEN 'CRITICAL' THEN 0 WHEN 'SERIOUS' THEN 1 WHEN 'WARNING' THEN 2 ELSE 3 END,
                                  e.detected_at DESC""")
    decisions = q(c, """SELECT decision_id, loop, title, proposed_at, impact_json FROM decision_log WHERE status='PROPOSED'
                        ORDER BY decision_id""")
    resolved = q(c, """SELECT exception_id, title, resolved_at, decision_id FROM ops_exception WHERE status='RESOLVED'
                       ORDER BY resolved_at DESC LIMIT 6""")

    # ---- feed freshness + assurance
    feeds = []
    for name, sql, route in (
        ("CM MES (Taiwan)", "SELECT MAX(received_at) t, SUM(received_at >= ?) n FROM raw_cm_mes_event", "#/cm-feed"),
        ("Supplier ASNs", "SELECT MAX(received_at) t, SUM(received_at >= ?) n FROM raw_supplier_asn", "#/integrations"),
        ("Carriers", "SELECT MAX(received_at) t, SUM(received_at >= ?) n FROM raw_carrier_event", "#/shipments"),
        ("3PL WMS", "SELECT MAX(received_at) t, SUM(received_at >= ?) n FROM raw_3pl_message", "#/inventory"),
        ("Email + Excel", "SELECT MAX(received_at) t, SUM(received_at >= ?) n FROM raw_email", "#/integrations"),
        ("CRM", "SELECT MAX(received_at) t, SUM(received_at >= ?) n FROM raw_warranty_case", "#/warranty"),
    ):
        r = q1(c, sql, (d7,))
        feeds.append({"name": name, "last": r["t"], "count_7d": r["n"] or 0, "route": route})
    quarantined = val(c, "SELECT COUNT(*) FROM raw_cm_mes_event WHERE ingest_status='QUARANTINED'") or 0
    contracts = q1(c, """SELECT COUNT(*) n, SUM(violations > 0) failing FROM contract_run r
                         WHERE run_id = (SELECT MAX(run_id) FROM contract_run x WHERE x.contract_id = r.contract_id)""")
    evals = q1(c, """SELECT COUNT(*) n, SUM(gate='PASS') pass FROM eval_run r
                     WHERE run_id = (SELECT MAX(run_id) FROM eval_run x WHERE x.suite_id = r.suite_id)""")
    return {"as_of": today.isoformat(), "now": now_s, "flow": flow, "pack_flow": pack_flow, "kpis": kpis,
            "daily": daily, "deliveries": deliveries, "exceptions": exceptions, "decisions": decisions,
            "resolved": resolved, "feeds": feeds, "quarantined": quarantined,
            "assurance": {"contracts": dict(contracts), "evals": dict(evals)}}
