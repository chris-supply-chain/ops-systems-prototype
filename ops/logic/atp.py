"""Available-to-promise and capable-to-promise across both supply chains.

A sellable kit needs a CM-built vehicle (in Taiwan, on the water, or at the 3PL)
and an OEM-built pack (at Fremont, on a DG truck, or at the 3PL), plus 3PL
kitting capacity. Supply is every unit and every planned build with the date it
can be kitted in Reno:

  vehicles  3PL stock today · containers at ETA + port/customs/dray · CM finished
            goods at the next sailing · CM commit by build day
  packs     3PL stock today · trucks in transit · Fremont stock at the next truck ·
            pack MPS as constrained by MRP (a BMS shortage removes pack supply)

Open orders are allocated day by day in priority order: fleet orders inside their
14-day window first, then everyone else by reservation time. An order that cannot
be completed does not block the orders behind it, which is exactly how the 3PL
works. The day an order's kit can ship becomes its promise, plus the carrier's
transit for the region.
"""
import datetime as dt
from collections import deque

from ..db import as_of as get_as_of

TRANSIT_MAX = {"WEST": 3, "MOUNTAIN": 4, "CENTRAL": 5, "EAST": 6}
KIT_CAPACITY = {0: 160, 1: 160, 2: 160, 3: 160, 4: 160, 5: 80, 6: 0}
PORT_TO_3PL_DAYS = 4          # discharge, customs, drayage, receipt + putaway
TRANSIT_DAYS = 15


def skip_sundays(day, n):
    d = day
    while n > 0:
        d += dt.timedelta(days=1)
        if d.weekday() != 6:
            n -= 1
    return d


def next_sailing_available(build_day):
    """CM loads Thursdays (built by Wednesday night), sails Sunday, 15 days to Oakland, 4 more to Reno."""
    load = build_day + dt.timedelta(days=(3 - build_day.weekday()) % 7 or 7)
    return load + dt.timedelta(days=3 + TRANSIT_DAYS + PORT_TO_3PL_DAYS)


def next_truck_available(ready_day):
    """DG trucks leave Fremont Monday and Thursday; kit-able in Reno two days later."""
    d = ready_day
    while d.weekday() not in (0, 3):
        d += dt.timedelta(days=1)
    return d + dt.timedelta(days=2)


def _d(s):
    return dt.date.fromisoformat(s[:10])


def vehicle_supply(conn, eta_mode="current"):
    """{sku: [(date_kit_able, qty, source)]} for every vehicle not yet committed to an order."""
    as_of = _d(get_as_of(conn))
    lots = {}

    def add(sku, day, q, src):
        lots.setdefault(sku, []).append((max(day, as_of), q, src))

    for r in conn.execute("SELECT item_id, COUNT(*) n FROM unit WHERE item_id LIKE 'LV1-%' AND status='AT_3PL' AND on_hold=0"
                          " GROUP BY item_id"):
        add(r["item_id"], as_of, r["n"], "3PL stock")
    rows = conn.execute("""SELECT u.item_id, s.shipment_id, s.status, s.eta_planned, s.eta_current, s.ata, s.etd_planned,
                                  s.received_at, s.vessel, s.voyage, ce.status AS customs, COUNT(*) n
                           FROM unit u JOIN shipment_unit su ON su.serial = u.serial
                           JOIN shipment s ON s.shipment_id = su.shipment_id AND s.leg='CM_TO_3PL'
                           LEFT JOIN customs_entry ce ON ce.shipment_id = s.shipment_id
                           WHERE u.item_id LIKE 'LV1-%' AND u.status IN ('IN_TRANSIT','BUILT','WIP')
                           GROUP BY u.item_id, s.shipment_id""").fetchall()
    in_container = set()
    for r in rows:
        in_container.add(r["shipment_id"])
        if r["received_at"]:
            continue                                   # listed on the ASN, never received: not supply
        if r["customs"] == "EXAM":
            day = as_of + dt.timedelta(days=2 + 2)
        elif r["ata"]:
            day = max(_d(r["ata"]) + dt.timedelta(days=PORT_TO_3PL_DAYS), as_of + dt.timedelta(days=1))
        elif r["status"] in ("BOOKED", "GATED_IN") or not r["eta_current"]:
            day = _d(r["etd_planned"]) + dt.timedelta(days=TRANSIT_DAYS + PORT_TO_3PL_DAYS)
        else:
            eta = r["eta_planned"] if eta_mode == "planned" else r["eta_current"]
            day = _d(eta) + dt.timedelta(days=PORT_TO_3PL_DAYS)
        add(r["item_id"], day, r["n"], f"{r['shipment_id']} · {r['vessel']} {r['voyage']}")
    for r in conn.execute("""SELECT item_id, status, COUNT(*) n FROM unit u
                             WHERE item_id LIKE 'LV1-%' AND status IN ('BUILT','WIP')
                             AND NOT EXISTS (SELECT 1 FROM shipment_unit su JOIN shipment s USING(shipment_id)
                                             WHERE su.serial = u.serial AND s.leg='CM_TO_3PL')
                             GROUP BY item_id, status"""):
        add(r["item_id"], next_sailing_available(as_of), r["n"], "CM finished goods" if r["status"] == "BUILT" else "CM WIP")
    for r in conn.execute("SELECT item_id, plan_date, SUM(qty) q FROM build_plan WHERE plan_type='CM_COMMIT' AND plan_date > ?"
                          " GROUP BY item_id, plan_date", (as_of.isoformat(),)):
        b = _d(r["plan_date"])
        add(r["item_id"], next_sailing_available(b), r["q"], f"CM build {b.isoformat()}")
    for sku in lots:
        lots[sku].sort(key=lambda x: x[0])
    return lots


def pack_supply(conn, constrained=None):
    as_of = _d(get_as_of(conn))
    lots = {}

    def add(sku, day, q, src):
        if q > 0:
            lots.setdefault(sku, []).append((max(day, as_of), q, src))

    for r in conn.execute("SELECT item_id, COUNT(*) n FROM unit WHERE item_id LIKE 'PK-%' AND status='AT_3PL' AND on_hold=0"
                          " GROUP BY item_id"):
        add(r["item_id"], as_of, r["n"], "3PL stock")
    for r in conn.execute("""SELECT u.item_id, s.shipment_id, s.eta_current, COUNT(*) n FROM unit u
                             JOIN shipment_unit su ON su.serial=u.serial
                             JOIN shipment s ON s.shipment_id=su.shipment_id AND s.leg='PLANT_TO_3PL'
                             WHERE u.status='IN_TRANSIT' AND u.item_id LIKE 'PK-%' GROUP BY u.item_id, s.shipment_id"""):
        add(r["item_id"], _d(r["eta_current"]) + dt.timedelta(days=1), r["n"], f"DG truck {r['shipment_id']}")
    for r in conn.execute("SELECT item_id, status, COUNT(*) n FROM unit WHERE item_id LIKE 'PK-%' AND status IN ('BUILT','WIP')"
                          " AND on_hold=0 GROUP BY item_id, status"):
        add(r["item_id"], next_truck_available(as_of + dt.timedelta(days=0 if r["status"] == "BUILT" else 1)), r["n"],
            "Fremont stock" if r["status"] == "BUILT" else "Fremont WIP")
    if constrained is None:
        from .mrp import constrained_pack_plan
        constrained = constrained_pack_plan(conn)
    for sku, by_day in constrained["plan"].items():
        for i, q in by_day.items():
            if i <= 0:
                continue
            b = as_of + dt.timedelta(days=i)
            add(sku, next_truck_available(b), q, f"Pack MPS {b.isoformat()}")
    for sku in lots:
        lots[sku].sort(key=lambda x: x[0])
    return lots


def open_orders(conn):
    return conn.execute("""SELECT o.order_id, o.channel, o.reserved_at, o.ordered_at, o.requested_date, o.ship_to_region,
                                  o.ship_to_state, o.promised_date, o.first_promised_date,
                                  k.item_id AS kit, b1.child_item_id AS vehicle_sku, b2.child_item_id AS pack_sku,
                                  x.item_id AS extra_pack
                           FROM customer_order o
                           JOIN order_line k ON k.order_id = o.order_id AND k.line_no = 1
                           JOIN bom_line b1 ON b1.parent_item_id = k.item_id AND b1.position='VEHICLE'
                           JOIN bom_line b2 ON b2.parent_item_id = k.item_id AND b2.position='PACK'
                           LEFT JOIN order_line x ON x.order_id = o.order_id AND x.line_no = 2
                           WHERE o.status = 'OPEN'""").fetchall()


def allocate(conn, vehicles, packs, orders, extra=None, horizon=180):
    """Day-stepped allocation. Returns {order_id: {...promise, pegs...}} (+ the extra hypothetical order)."""
    as_of = _d(get_as_of(conn))
    queues = {}
    arrivals = {}
    for lots in (vehicles, packs):
        for sku, ls in lots.items():
            for day, q, src in ls:
                arrivals.setdefault(day, []).append((sku, q, src))
    pending = []
    for o in orders:
        fleet = o["channel"] == "FLEET"
        open_day = (_d(o["requested_date"]) - dt.timedelta(days=14)) if fleet and o["requested_date"] else as_of
        prio = (0 if fleet else 1, (o["requested_date"] if fleet else (o["reserved_at"] or o["ordered_at"])) or "")
        pending.append((prio, open_day, dict(o)))
    if extra:
        pending.append(((2, "~"), as_of, dict(extra)))
    pending.sort(key=lambda p: p[0])
    out = {}
    day = as_of
    for _ in range(horizon):
        for sku, q, src in arrivals.get(day, []):
            queues.setdefault(sku, deque()).append([q, src, day])
        cap = KIT_CAPACITY[day.weekday()]
        still = []
        for prio, open_day, o in pending:
            if cap <= 0 or open_day > day:
                still.append((prio, open_day, o))
                continue
            need = {}
            for sku in (o["vehicle_sku"], o["pack_sku"], o.get("extra_pack")):
                if sku:
                    need[sku] = need.get(sku, 0) + 1
            if any(sum(x[0] for x in queues.get(sku, ())) < n for sku, n in need.items()):
                still.append((prio, open_day, o))
                continue
            pegs = {}
            for sku, n in need.items():
                for _ in range(n):
                    head = queues[sku][0]
                    head[0] -= 1
                    pegs.setdefault(sku, []).append({"source": head[1], "available": head[2].isoformat()})
                    if head[0] <= 0:
                        queues[sku].popleft()
            cap -= 1
            region = o.get("ship_to_region") or "WEST"
            out[o["order_id"]] = {"ship_date": day.isoformat(),
                                  "promise": skip_sundays(day, TRANSIT_MAX[region]).isoformat(),
                                  "pegs": pegs, "priority": 0 if o["channel"] == "FLEET" else 1}
        pending = still
        if not pending:
            break
        day += dt.timedelta(days=1)
    for prio, open_day, o in pending:
        out[o["order_id"]] = {"ship_date": None, "promise": None, "pegs": {}, "beyond_horizon": True}
    return out


def promise_all(conn, eta_mode="current", constrained=None):
    v, p = vehicle_supply(conn, eta_mode), pack_supply(conn, constrained)
    return allocate(conn, v, p, open_orders(conn)), v, p


def check(conn, kit, region="WEST", extra_pack=None, qty=1):
    """Promise a hypothetical new order at the back of the queue (CTP: kitting capacity + both supply chains)."""
    kit_row = conn.execute("""SELECT b1.child_item_id AS v, b2.child_item_id AS p FROM bom_line b1
                              JOIN bom_line b2 ON b2.parent_item_id=b1.parent_item_id AND b2.position='PACK'
                              WHERE b1.parent_item_id=? AND b1.position='VEHICLE'""", (kit,)).fetchone()
    if not kit_row:
        raise ValueError(f"unknown kit {kit}")
    v, p = vehicle_supply(conn), pack_supply(conn)
    extras = []
    for k in range(qty):
        extras.append({"order_id": f"NEW-{k + 1}", "channel": "D2C", "reserved_at": None, "ordered_at": "~",
                       "requested_date": None, "ship_to_region": region, "vehicle_sku": kit_row["v"],
                       "pack_sku": kit_row["p"], "extra_pack": extra_pack})
    orders = [dict(o) for o in open_orders(conn)]
    res = allocate(conn, v, p, orders + extras[:-1] if qty > 1 else orders, extra=extras[-1])
    return {"kit": kit, "vehicle_sku": kit_row["v"], "pack_sku": kit_row["p"], "region": region, "qty": qty,
            "results": [res.get(e["order_id"]) for e in extras]}


def atp_table(conn, weeks=12):
    """Classic ATP by SKU and week: supply, committed to orders, uncommitted (cumulative)."""
    as_of = _d(get_as_of(conn))
    res, v, p = promise_all(conn)
    table = {}
    for lots in (v, p):
        for sku, ls in lots.items():
            t = table.setdefault(sku, [{"week": (as_of + dt.timedelta(days=7 * k)).isoformat(), "supply": 0,
                                        "committed": 0} for k in range(weeks)])
            for day, q, src in ls:
                k = (day - as_of).days // 7
                if 0 <= k < weeks:
                    t[k]["supply"] += q
    for oid, r in res.items():
        for sku, pegs in r.get("pegs", {}).items():
            for pg in pegs:
                k = (_d(pg["available"]) - as_of).days // 7
                if sku in table and 0 <= k < weeks:
                    table[sku][k]["committed"] += 1
    for sku, rows in table.items():
        cum = 0
        for row in rows:
            cum += row["supply"] - row["committed"]
            row["atp_cumulative"] = cum
    return table
