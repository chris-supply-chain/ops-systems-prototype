"""ATP & Queues: promises pegged to real supply across both supply chains.

One allocation run (every open order, in priority order, against every vehicle and
pack lot) answers the whole page: the promise for a new order of each kit, weekly
ATP, which supply lot each promise pegs to, the orders a delay has put at risk,
and the build and fulfillment queues. The run is cached against the database
file's modification time, so a closed-loop action invalidates it automatically.
"""
import datetime as dt
import os
import threading

from ... import config
from ...dates import month_day, to_date
from ...db import as_of as get_as_of, now as get_now
from ...logic import atp, mrp, promises
from ..router import HttpError, get, post

REGIONS = ["WEST", "MOUNTAIN", "CENTRAL", "EAST"]
_CACHE = {"key": None, "val": None}
_LOCK = threading.Lock()


def _wmd(d):
    return f"{d:%a %b} {d.day}"


def _cache_key(conn):
    try:
        st = os.stat(config.DB_PATH)
        return (str(config.DB_PATH), st.st_mtime_ns, st.st_size, get_as_of(conn))
    except OSError:
        return None


def _state(conn):
    key = _cache_key(conn)
    with _LOCK:
        if key is not None and _CACHE["key"] == key:
            return _CACHE["val"]
    val = _compute(conn)
    with _LOCK:
        _CACHE.update(key=key, val=val)
    return val


# ---------------------------------------------------------------------------- the run

def _kits(conn):
    return [dict(r) for r in conn.execute("""
        SELECT i.item_id AS kit, i.name, b1.child_item_id AS vehicle_sku, b2.child_item_id AS pack_sku
        FROM item i
        JOIN bom_line b1 ON b1.parent_item_id = i.item_id AND b1.position = 'VEHICLE'
        JOIN bom_line b2 ON b2.parent_item_id = i.item_id AND b2.position = 'PACK'
        WHERE i.kind = 'KIT' ORDER BY i.item_id""")]


def _compute(conn):
    as_of = to_date(get_as_of(conn))
    plan = mrp.run(conn)
    cons = mrp.constrained_pack_plan(conn, plan)
    v = atp.vehicle_supply(conn)
    p = atp.pack_supply(conn, cons)
    orders = [dict(o) for o in atp.open_orders(conn)]
    res = atp.allocate(conn, v, p, orders)
    kits = _kits(conn)
    kit_promises = []
    for k in kits:
        extra = {"order_id": "NEW-1", "channel": "D2C", "reserved_at": None, "ordered_at": "~", "requested_date": None,
                 "ship_to_region": "WEST", "vehicle_sku": k["vehicle_sku"], "pack_sku": k["pack_sku"], "extra_pack": None}
        r = atp.allocate(conn, v, p, orders, extra=extra).get("NEW-1") or {}
        kit_promises.append({**k, **_explain(as_of, r, k["vehicle_sku"], [k["pack_sku"]], "WEST")})
    return {"as_of": as_of, "plan": plan, "cons": cons, "v": v, "p": p, "orders": orders, "res": res,
            "kits": kits, "kit_promises": kit_promises, "risk": promises.at_risk(conn, res),
            "table": atp.atp_table(conn)}


# ---------------------------------------------------------------------------- explaining a peg

def _source_kind(src):
    if src == "3PL stock":
        return "STOCK"
    if src.startswith("OC-"):
        return "CONTAINER"
    if src.startswith("CM build"):
        return "CM_BUILD"
    if src.startswith("CM "):
        return "CM_FG"
    if src.startswith("DG truck"):
        return "TRUCK"
    if src.startswith("Fremont"):
        return "PLANT"
    if src.startswith("Pack MPS"):
        return "PACK_MPS"
    return "OTHER"


def _describe(src, available):
    av = month_day(available)
    kind = _source_kind(src)
    if kind == "STOCK":
        return "in stock at the 3PL in Reno"
    if kind == "CONTAINER":
        cid, sailing = (src.split(" · ") + [""])[:2]
        return f"on container {cid} ({sailing}), kit-able in Reno {av}"
    if kind == "CM_BUILD":
        return f"the CM's committed build on {month_day(src[9:])} in Taichung, next sailing, in Reno {av}"
    if kind == "CM_FG":
        return f"{'built' if 'finished' in src else 'in WIP'} at the CM, waiting for the next sailing, in Reno {av}"
    if kind == "TRUCK":
        return f"on {src} from Fremont, in Reno {av}"
    if kind == "PLANT":
        return f"{'built' if 'stock' in src else 'in WIP'} at Fremont, on the next DG truck, in Reno {av}"
    if kind == "PACK_MPS":
        return f"the pack line's build on {month_day(src[9:])} (MPS after MRP constraints), in Reno {av}"
    return src


def _explain(as_of, r, vehicle_sku, pack_skus, region):
    """Promise + pegging + the binding constraint, for one (real or hypothetical) order."""
    if not r or not r.get("ship_date"):
        return {"ship_date": None, "promise": None, "days_out": None, "binding": "beyond horizon", "pegs": [],
                "text": "No supply inside the 180-day horizon."}
    ship = to_date(r["ship_date"])
    pegs = []
    latest = None
    for sku, lst in (r.get("pegs") or {}).items():
        for pg in lst:
            av = to_date(pg["available"])
            role = "vehicle" if sku == vehicle_sku else "pack"
            pegs.append({"sku": sku, "role": role, "source": pg["source"], "kind": _source_kind(pg["source"]),
                         "available": pg["available"], "text": _describe(pg["source"], pg["available"])})
            if latest is None or av > latest[0]:
                latest = (av, role, sku)
    if latest and latest[0] < ship:
        binding = "kitting capacity"
    elif latest:
        roles = {pg["role"] for pg in pegs if to_date(pg["available"]) == latest[0]}
        binding = "vehicle and pack" if len(roles) > 1 else latest[1]
    else:
        binding = "—"
    promise = to_date(r["promise"])
    carrier = (promise - ship).days
    head = (f"Ships {_wmd(ship)} from Reno and arrives by {_wmd(promise)} "
            f"({region.title()}: {carrier} calendar days with the carrier). ")
    tail = {"kitting capacity": "Both parts are in Reno earlier; the 3PL's kitting capacity is the constraint.",
            "vehicle": "The vehicle is the binding constraint.", "pack": "The battery pack is the binding constraint.",
            "vehicle and pack": "Vehicle and pack arrive together; both bind."}.get(binding, "")
    body = "".join(f"The {pg['role']} ({pg['sku']}) pegs to {pg['text']}. " for pg in pegs)
    return {"ship_date": r["ship_date"], "promise": r["promise"], "days_out": (promise - as_of).days,
            "ship_days_out": (ship - as_of).days, "binding": binding, "pegs": pegs,
            "summary": (head + tail).strip(), "text": (head + body + tail).strip()}


# ---------------------------------------------------------------------------- summary

def _containers(conn):
    out = {}
    for r in conn.execute("""SELECT s.shipment_id, s.vessel, s.voyage, s.status, s.eta_planned, s.eta_current, s.ata,
                                    ce.status AS customs
                             FROM shipment s LEFT JOIN customs_entry ce ON ce.shipment_id = s.shipment_id
                             WHERE s.leg = 'CM_TO_3PL' AND s.received_at IS NULL"""):
        r = dict(r)
        r["delay_days"] = ((to_date(r["eta_current"]) - to_date(r["eta_planned"])).days
                           if r["eta_current"] and r["eta_planned"] else 0)
        out[r["shipment_id"]] = r
    return out


def _pipeline(st, containers):
    as_of = st["as_of"]
    committed = {}
    for r in st["res"].values():
        for sku, lst in (r.get("pegs") or {}).items():
            for pg in lst:
                k = (sku, pg["source"], pg["available"])
                committed[k] = committed.get(k, 0) + 1
    mps, cons = st["plan"]["mps"], st["cons"]["plan"]
    lots = []
    for chain, supply in (("VEHICLE", st["v"]), ("PACK", st["p"])):
        for sku, ls in supply.items():
            for day, q, src in ls:
                c = committed.get((sku, src, day.isoformat()), 0)
                row = {"chain": chain, "sku": sku, "available": day.isoformat(), "qty": q, "committed": min(c, q),
                       "uncommitted": max(0, q - c), "source": src, "kind": _source_kind(src), "note": None,
                       "shipment_id": None, "week": (day - as_of).days // 7}
                if row["kind"] == "CONTAINER":
                    sid = src.split(" · ")[0]
                    row["shipment_id"] = sid
                    ct = containers.get(sid)
                    if ct:
                        if ct["customs"] == "EXAM":
                            row["note"] = "CBP exam: +4 days to release"
                        elif ct["delay_days"] > 0:
                            row["note"] = (f"ETA +{ct['delay_days']}d vs plan "
                                           f"({month_day(ct['eta_planned'])} → {month_day(ct['eta_current'])})")
                        row["status"] = ct["status"]
                if row["kind"] == "PACK_MPS":
                    i = (to_date(src[9:]) - as_of).days
                    planned = (mps.get(sku) or {}).get(i, 0)
                    got = (cons.get(sku) or {}).get(i, 0)
                    if planned > got:
                        row["note"] = f"MRP cut {planned - got} of {planned} (BMS-B shortage)"
                lots.append(row)
    return lots


def _delayed_sailings(containers):
    out = {}
    for c in containers.values():
        if c["delay_days"] > 0 and not c["ata"]:
            out[f"{c['vessel']} {c['voyage']}"] = max(out.get(f"{c['vessel']} {c['voyage']}", 0), c["delay_days"])
    return out


def _risk_groups(risk, delayed):
    groups = {}
    for r in risk:
        cause = next((f"Delayed sailing: {s} (+{d}d)" for s, d in delayed.items() if s in (r["pegged_to"] or "")),
                     "Knock-on: supply re-sequenced behind delayed orders")
        r["cause"] = cause
        g = groups.setdefault(cause, {"cause": cause, "orders": 0, "slip_sum": 0, "max_slip": 0})
        g["orders"] += 1
        g["slip_sum"] += r["slip_days"]
        g["max_slip"] = max(g["max_slip"], r["slip_days"])
    out = []
    for g in groups.values():
        g["avg_slip"] = round(g.pop("slip_sum") / g["orders"], 1)
        out.append(g)
    return sorted(out, key=lambda g: -g["orders"])


def _priority(o, as_of):
    fleet = o["channel"] == "FLEET"
    open_day = (to_date(o["requested_date"]) - dt.timedelta(days=14)) if fleet and o["requested_date"] else as_of
    basis = (o["requested_date"] if fleet else (o["reserved_at"] or o["ordered_at"])) or ""
    return (0 if fleet else 1, basis), open_day


def _fulfillment(st):
    as_of = st["as_of"]
    rows = []
    for o in st["orders"]:
        prio, open_day = _priority(o, as_of)
        r = st["res"].get(o["order_id"]) or {}
        packs = [o["pack_sku"]] + ([o["extra_pack"]] if o.get("extra_pack") else [])
        ex = _explain(as_of, r, o["vehicle_sku"], packs, o["ship_to_region"])
        if open_day > as_of and o["channel"] == "FLEET":
            blocked = "FLEET_WINDOW"
        elif ex["ship_date"] and to_date(ex["ship_date"]) <= as_of:
            blocked = "READY"
        else:
            blocked = {"vehicle": "VEHICLE", "pack": "PACK", "vehicle and pack": "BOTH",
                       "kitting capacity": "CAPACITY"}.get(ex["binding"], "NO_SUPPLY")
        row = {"prio": prio, "order_id": o["order_id"], "channel": o["channel"], "kit": o["kit"],
               "basis": prio[1][:10],
               "basis_kind": "requested" if o["channel"] == "FLEET" else ("reserved" if o["reserved_at"] else "ordered"),
               "state": o["ship_to_state"], "promised": o["promised_date"], "ship_date": ex["ship_date"],
               "atp_promise": ex["promise"], "blocked": blocked}
        if o.get("extra_pack"):
            row["extra_pack"] = o["extra_pack"]
        if o["channel"] == "FLEET":
            row["window_opens"] = open_day.isoformat()
        rows.append(row)
    rows.sort(key=lambda x: x["prio"])
    counts = {}
    for i, r in enumerate(rows):
        r["rank"] = i + 1
        del r["prio"]
        counts[r["blocked"]] = counts.get(r["blocked"], 0) + 1
    return rows, counts


def _build_queue(conn, st):
    as_of = st["as_of"]
    cm_days = [r["plan_date"] for r in conn.execute(
        "SELECT DISTINCT plan_date FROM build_plan WHERE plan_type='CM_COMMIT' AND plan_date > ? ORDER BY plan_date LIMIT 10",
        (as_of.isoformat(),))]
    fence = conn.execute("SELECT frozen_days, slushy_days FROM time_fence WHERE site_id='CM-TXG'").fetchone()
    cm = []
    for d in cm_days:
        for line in ("L1", "L2"):
            split = {r["item_id"]: r["qty"] for r in conn.execute(
                "SELECT item_id, qty FROM build_plan WHERE plan_type='CM_COMMIT' AND plan_date=? AND line=?", (d, line))}
            if not split:
                continue
            wos = conn.execute("SELECT COUNT(*) n, SUM(qty_planned) q FROM work_order WHERE site_id='CM-TXG' AND line=?"
                               " AND sched_date=? AND status='RELEASED'", (line, d)).fetchone()
            days_out = (to_date(d) - as_of).days
            cm.append({"date": d, "line": line, "qty": sum(split.values()), "split": split, "wo_released": wos["n"],
                       "fence": ("FROZEN" if fence and days_out <= fence["frozen_days"] else
                                 "SLUSHY" if fence and days_out <= fence["slushy_days"] else "FREE"),
                       "lands_in_reno": atp.next_sailing_available(to_date(d)).isoformat()})
    mps, cons = st["plan"]["mps"], st["cons"]["plan"]
    lost = st["cons"].get("lost_by_day", {})
    pack = []
    days = sorted({i for sku in ("PK-STD", "PK-LRG") for i in (mps.get(sku) or {}) if i > 0})[:10]
    for i in days:
        d = as_of + dt.timedelta(days=i)
        row = {"date": d.isoformat(), "lands_in_reno": atp.next_truck_available(d).isoformat()}
        for sku, key in (("PK-STD", "std"), ("PK-LRG", "lrg")):
            row[f"{key}_planned"] = (mps.get(sku) or {}).get(i, 0)
            row[f"{key}_constrained"] = (cons.get(sku) or {}).get(i, 0)
        row["lost"] = row["std_planned"] + row["lrg_planned"] - row["std_constrained"] - row["lrg_constrained"]
        row["reason"] = "BMS-B short (MRP)" if row["lost"] > 0 and lost.get(i) else None
        pack.append(row)
    bms = st["plan"]["items"].get("BMS-B", {})
    return {"cm": cm, "pack": pack, "fence": dict(fence) if fence else None,
            "bms_first_short": (as_of + dt.timedelta(days=bms["first_short"])).isoformat()
            if bms.get("first_short") is not None else None}


@get(r"^/api/atp/summary$")
def summary(req):
    conn = req.conn
    st = _state(conn)
    as_of = st["as_of"]
    containers = _containers(conn)
    delayed = _delayed_sailings(containers)
    risk = [dict(r) for r in st["risk"]]
    groups = _risk_groups(risk, delayed)
    fq, counts = _fulfillment(st)
    leads = [(to_date(r["promise"]) - as_of).days for r in st["res"].values() if r.get("promise")]
    allocated = conn.execute("SELECT COUNT(*) n FROM customer_order WHERE status='ALLOCATED'").fetchone()["n"]
    decision = conn.execute("""SELECT decision_id, status, title, proposed_at, executed_at FROM decision_log
                               WHERE rule_id='PROMISE-SUPPLY-DELAY' ORDER BY proposed_at DESC LIMIT 1""").fetchone()
    repromised = conn.execute("SELECT COUNT(DISTINCT order_id) n FROM order_promise WHERE reason='SUPPLY_DELAY'").fetchone()["n"]
    table = {sku: rows for sku, rows in st["table"].items()}
    atp12 = {sku: rows[-1]["atp_cumulative"] for sku, rows in table.items()}
    best = min((k for k in st["kit_promises"] if k["promise"]), key=lambda k: k["promise"], default=None)
    return {
        "as_of": as_of.isoformat(), "now": get_now(conn),
        "kpis": {"open_orders": len(st["orders"]), "allocated_today": allocated, "at_risk": len(risk),
                 "at_risk_avg_slip": round(sum(r["slip_days"] for r in risk) / len(risk), 1) if risk else 0,
                 "avg_days_to_promise": round(sum(leads) / len(leads), 1) if leads else None,
                 "earliest_new": best, "atp_12w_vehicles": sum(v for k, v in atp12.items() if k.startswith("LV1")),
                 "atp_12w_packs": sum(v for k, v in atp12.items() if k.startswith("PK")),
                 "repromised": repromised},
        "kit_promises": st["kit_promises"],
        "atp_table": table,
        "pipeline": _pipeline(st, containers),
        "containers": list(containers.values()),
        "delayed_sailings": delayed,
        "risk": {"orders": risk, "groups": groups, "decision": dict(decision) if decision else None},
        "fulfillment": {"rows": fq, "total": len(fq), "counts": counts, "allocated_today": allocated},
        "build_queue": _build_queue(conn, st),
        "regions": REGIONS,
        "kitting_capacity": {"weekday": atp.KIT_CAPACITY[0], "saturday": atp.KIT_CAPACITY[5], "sunday": atp.KIT_CAPACITY[6]},
    }


# ---------------------------------------------------------------------------- promise check (CTP)

@post(r"^/api/atp/check$")
def check(req):
    b = req.body or {}
    conn = req.conn
    st = _state(conn)
    kit = next((k for k in st["kits"] if k["kit"] == b.get("kit")), None)
    if kit is None:
        raise HttpError(400, f"unknown kit {b.get('kit')!r}")
    region = (b.get("region") or "WEST").upper()
    if region not in REGIONS:
        raise HttpError(400, f"region must be one of {', '.join(REGIONS)}")
    extra = b.get("extra_pack") or None
    if extra not in (None, "PK-STD", "PK-LRG"):
        raise HttpError(400, "extra_pack must be PK-STD, PK-LRG or empty")
    try:
        qty = int(b.get("qty") or 1)
    except (TypeError, ValueError):
        raise HttpError(400, "qty must be a whole number")
    if not 1 <= qty <= 25:
        raise HttpError(400, "qty must be between 1 and 25")
    extras = [{"order_id": f"NEW-{i + 1}", "channel": "D2C", "reserved_at": None, "ordered_at": "~", "requested_date": None,
               "ship_to_region": region, "vehicle_sku": kit["vehicle_sku"], "pack_sku": kit["pack_sku"],
               "extra_pack": extra} for i in range(qty)]
    orders = st["orders"] + extras[:-1]
    res = atp.allocate(conn, st["v"], st["p"], orders, extra=extras[-1])
    packs = [kit["pack_sku"]] + ([extra] if extra else [])
    units = [{"unit": i + 1, **_explain(st["as_of"], res.get(e["order_id"]), kit["vehicle_sku"], packs, region)}
             for i, e in enumerate(extras)]
    last = units[-1]
    displaced = sum(1 for oid, r in res.items() if not oid.startswith("NEW-") and r.get("ship_date")
                    and st["res"].get(oid, {}).get("ship_date") and r["ship_date"] > st["res"][oid]["ship_date"])
    return {"kit": kit, "region": region, "extra_pack": extra, "qty": qty, "units": units,
            "promise": last["promise"], "ship_date": last["ship_date"], "days_out": last["days_out"],
            "binding": last["binding"], "text": last["text"], "summary": last["summary"], "displaced_orders": displaced,
            "method": "CTP: material across both supply chains (CM vehicles, Fremont packs) plus 3PL kitting capacity, "
                      "allocated behind every open order in priority order"}


# ---------------------------------------------------------------------------- one order

@get(r"^/api/atp/order/([A-Za-z0-9-]+)$")
def order(req):
    conn = req.conn
    oid = req.params[0]
    o = conn.execute("""SELECT o.*, c.display_name, c.kind AS customer_kind, c.city, c.state
                        FROM customer_order o JOIN customer c USING (customer_id) WHERE o.order_id = ?""", (oid,)).fetchone()
    if o is None:
        raise HttpError(404, f"order {oid} not found")
    lines = [dict(r) for r in conn.execute("""SELECT ol.*, i.name FROM order_line ol JOIN item i USING (item_id)
                                              WHERE ol.order_id = ? ORDER BY ol.line_no""", (oid,))]
    history = [dict(r) for r in conn.execute("""SELECT op.*, d.title AS decision_title FROM order_promise op
                                                LEFT JOIN decision_log d ON d.decision_id = op.decision_id
                                                WHERE op.order_id = ? ORDER BY op.promise_id""", (oid,))]
    out = {"order": dict(o), "lines": lines, "history": history, "allocation": None, "shipment": None, "claims": []}
    if o["status"] == "OPEN":
        st = _state(conn)
        oo = next((x for x in st["orders"] if x["order_id"] == oid), None)
        if oo:
            prio, open_day = _priority(oo, st["as_of"])
            packs = [oo["pack_sku"]] + ([oo["extra_pack"]] if oo.get("extra_pack") else [])
            ex = _explain(st["as_of"], st["res"].get(oid), oo["vehicle_sku"], packs, oo["ship_to_region"])
            ahead = sum(1 for x in st["orders"] if _priority(x, st["as_of"])[0] < prio)
            out["allocation"] = {**ex, "queue_position": ahead + 1, "queue_total": len(st["orders"]),
                                 "priority_basis": prio[1], "fleet_window_opens": open_day.isoformat()
                                 if oo["channel"] == "FLEET" else None,
                                 "at_risk": bool(ex["promise"] and o["promised_date"] and ex["promise"] > o["promised_date"])}
    s = conn.execute("SELECT * FROM shipment WHERE order_id = ? ORDER BY etd_planned DESC LIMIT 1", (oid,)).fetchone()
    if s:
        ev = [dict(r) for r in conn.execute("SELECT event_ts, code, location, detail, source FROM shipment_event"
                                            " WHERE shipment_id = ? ORDER BY event_ts", (s["shipment_id"],))]
        out["shipment"] = {**dict(s), "events": ev}
    out["claims"] = [dict(r) for r in conn.execute(
        "SELECT claim_id, reported_at, symptom, defect_code, status FROM warranty_claim WHERE order_id = ?", (oid,))]
    return out
