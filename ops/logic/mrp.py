"""Multi-level MRP.

The MPS (the CM's committed vehicle builds, the OEM's pack-line schedule, and the
kitting the 3PL can do as vehicles land) is exploded level by level through the
BOM, honoring effectivity dates, so ECO cut-ins change what is required on the
day they take effect. Each purchased item is then netted day by day:

    projected(d) = projected(d-1) + scheduled receipts(d) + alternate used(d) - gross requirements(d)

Scheduled receipts land on the supplier's promise date, not our need date. An
unconfirmed line is flagged. Below safety stock, the engine first reschedules an
existing receipt in (EXPEDITE) and only then plans a new order, lot-sized by MOQ
and multiple and offset by lead time. A release date in the past is
PAST_DUE_RELEASE: it cannot arrive in time, so the as-is projection shows the
SHORTAGE. Items below what the OEM buys (motors, cathode, magnets, wafers) are
exploded for visibility only. That is the demand we release to tiers 2 and 3.

`net_item` is a pure function: the textbook eval cases and the unit tests call
it directly with hand-computed expectations.
"""
import datetime as dt
import json
import math

from ..db import as_of as get_as_of, now as get_now

HORIZON_DAYS = 84
PLANNED = {  # item -> planning site
    "CEL-21700": "OEM-FRE", "BMS-B": "OEM-FRE", "ENC-STD": "OEM-FRE", "ENC-LRG": "OEM-FRE", "BUS-S": "OEM-FRE",
    "BUS-L": "OEM-FRE", "HRN-PK": "OEM-FRE", "GSK-B": "OEM-FRE", "DU-C": "CM-TXG", "PU-1": "CM-TXG",
    "HMI-1": "CM-TXG", "CHG-1": "3PL-RNO",
}
MPS_SITE = {"VEHICLE": "CM-TXG", "PACK": "OEM-FRE", "KIT": "3PL-RNO"}
STAGING_DAYS = {"CM-TXG": 1, "OEM-FRE": 0, "3PL-RNO": 0}   # consigned parts must be at the CM the day before the build


# ---------------------------------------------------------------------------- pure netting

def lot_size(net, moq, mult, rule="MOQ_MULTIPLE"):
    if net <= 0:
        return 0
    if rule == "LOT_FOR_LOT" or not (moq or mult):
        return int(math.ceil(net))
    q = max(net, moq or 0)
    m = mult or 1
    return int(math.ceil(q / m) * m)


def net_item(days, on_hand, safety_stock, lead_time, moq, mult, gross, receipts, alternates=None,
             rule="MOQ_MULTIPLE", defer_days=7):
    """Time-phased record for one item over `days` daily buckets (index 0 = today).

    gross:      {index: qty}
    receipts:   [{"index": i, "qty": q, "ref": str, "confirmed": bool}] (index < 0 = past due, lands today)
    alternates: [{"item": id, "on_hand": q, "allowance": q, "valid_to": index}] used only to cover a shortfall
    Returns arrays (gross, receipts, alt, projected, planned_receipts, planned_releases, recommended) and messages.
    """
    ss = safety_stock or 0
    g = [float(gross.get(i, 0)) for i in range(days)]
    rec = [0.0] * days
    for r in receipts:
        i = max(0, r["index"])
        if i < days:
            rec[i] += r["qty"]
    msgs = []
    for r in receipts:
        if r["index"] < 0:
            msgs.append({"message": "EXPEDITE", "ref": r["ref"], "index": 0, "qty": r["qty"],
                         "detail": f"past due: promised {-r['index']}d ago, not received"})
        if not r.get("confirmed", True) and r["index"] < days:
            msgs.append({"message": "UNCONFIRMED", "ref": r["ref"], "index": max(0, r["index"]), "qty": r["qty"],
                         "detail": "no supplier promise; planned on our need date"})

    def project(receipt_arr):
        alt_left = [dict(a) for a in (alternates or [])]
        pab, used, out = on_hand, [0.0] * days, []
        for i in range(days):
            avail = pab + receipt_arr[i]
            if avail < g[i]:
                for a in alt_left:
                    if i <= a["valid_to"]:
                        take = min(g[i] - avail, a["on_hand"], a["allowance"])
                        if take > 0:
                            a["on_hand"] -= take
                            a["allowance"] -= take
                            used[i] += take
                            avail += take
            pab = avail - g[i]
            out.append(pab)
        return out, used

    projected, alt_used = project(rec)
    first_short = next((i for i, v in enumerate(projected) if v < 0), None)
    if first_short is not None and first_short > int(lead_time or 0):
        first_short = None          # a normal planned order covers it in time; not a line-stop risk
    if first_short is not None:
        worst = min(projected)
        msgs.append({"message": "SHORTAGE", "ref": None, "index": first_short, "qty": round(-worst, 1),
                     "detail": f"stock runs out on day {first_short}; worst gap {-worst:,.0f} with current supply"})
    else:
        below = next((i for i, v in enumerate(projected) if v < ss), None)
        if below is not None:
            msgs.append({"message": "BELOW_SAFETY_STOCK", "ref": None, "index": below, "qty": round(ss - projected[below], 1),
                         "detail": f"projected {projected[below]:,.0f} under safety stock {ss:,.0f}"})

    # recommended plan: pull existing receipts in before creating new orders
    plan_rec = list(rec)
    movable = sorted([dict(r, index=max(0, r["index"])) for r in receipts if 0 <= max(0, r["index"]) < days],
                     key=lambda r: r["index"])
    planned_rec = [0.0] * days
    planned_rel = {}
    pab = on_hand
    alt_left = [dict(a) for a in (alternates or [])]
    for i in range(days):
        pab += plan_rec[i] + planned_rec[i]
        if pab < g[i]:
            for a in alt_left:
                if i <= a["valid_to"]:
                    take = min(g[i] - pab, a["on_hand"], a["allowance"])
                    if take > 0:
                        a["on_hand"] -= take
                        a["allowance"] -= take
                        pab += take
        pab -= g[i]
        while pab < ss:
            nxt = next((r for r in movable if r["index"] > i and not r.get("moved")), None)
            if nxt is None:
                break
            nxt["moved"] = True
            plan_rec[nxt["index"]] -= nxt["qty"]
            plan_rec[i] += nxt["qty"]
            pab += nxt["qty"]
            msgs.append({"message": "EXPEDITE", "ref": nxt["ref"], "index": i, "qty": nxt["qty"], "from_index": nxt["index"],
                         "detail": f"pull in from day {nxt['index']} to day {i}"})
        if pab < ss:
            qty = lot_size(ss - pab, moq, mult, rule)
            planned_rec[i] += qty
            pab += qty
            rel = i - int(lead_time or 0)
            planned_rel[rel] = planned_rel.get(rel, 0) + qty
            if rel < 0:
                msgs.append({"message": "PAST_DUE_RELEASE", "ref": None, "index": i, "qty": qty, "release_index": rel,
                             "detail": f"needed day {i} but lead time is {lead_time}d: release is {-rel}d late"})
            elif rel <= 2:
                msgs.append({"message": "RELEASE", "ref": None, "index": i, "qty": qty, "release_index": rel,
                             "detail": f"release by day {rel} to arrive day {i}"})
    recommended, _ = project([plan_rec[i] + planned_rec[i] for i in range(days)])

    # receipts that arrive long before they are needed: defer or cancel
    for r in receipts:
        i = r["index"]
        if i < 0 or i >= days or any(m.get("ref") == r["ref"] and m["message"] == "EXPEDITE" for m in msgs):
            continue
        without = [plan_rec[k] + planned_rec[k] - (r["qty"] if k == i else 0) for k in range(days)]
        proj_wo, _ = project(without)
        need_day = next((k for k in range(i, days) if proj_wo[k] < ss), None)
        if need_day is None:
            msgs.append({"message": "CANCEL" if i > 30 else "DEFER", "ref": r["ref"], "index": i, "qty": r["qty"],
                         "detail": "not needed inside the horizon" if i > 30 else "not needed inside the horizon; push out"})
        elif need_day - i >= defer_days:
            msgs.append({"message": "DEFER", "ref": r["ref"], "index": i, "qty": r["qty"], "to_index": need_day,
                         "detail": f"arrives day {i}, first needed day {need_day}"})
    return {"gross": g, "receipts": rec, "alt": alt_used, "projected": projected, "planned_receipts": planned_rec,
            "planned_releases": planned_rel, "recommended": recommended, "messages": msgs,
            "first_short": first_short}


# ---------------------------------------------------------------------------- explosion

def bom_children(bom, parent, day):
    ds = day.isoformat()
    return [(b["child_item_id"], b["qty_per"], b["bom_level"], b["position"]) for b in bom.get(parent, [])
            if b["eff_from"] <= ds and (b["eff_to"] is None or b["eff_to"] > ds)]


def low_level_codes(bom):
    llc = {}

    def walk(item, level, seen):
        if level > llc.get(item, -1):
            llc[item] = level
        for b in bom.get(item, []):
            if b["child_item_id"] not in seen:
                walk(b["child_item_id"], level + 1, seen | {b["child_item_id"]})
    for parent in list(bom):
        walk(parent, 0, {parent})
    return llc


def load_bom(conn):
    bom = {}
    for b in conn.execute("SELECT * FROM bom_line"):
        bom.setdefault(b["parent_item_id"], []).append(b)
    return bom


def mps(conn, start, days):
    """Firm MPS by item/day: CM commit (vehicles), pack MPS, and kitting as vehicles land at the 3PL."""
    end = (start + dt.timedelta(days=days)).isoformat()
    plan = {}
    for r in conn.execute("SELECT item_id, plan_date, SUM(qty) q FROM build_plan WHERE plan_date>=? AND plan_date<?"
                          " GROUP BY item_id, plan_date", (start.isoformat(), end)):
        plan.setdefault(r["item_id"], {})[(dt.date.fromisoformat(r["plan_date"]) - start).days] = r["q"]
    # kits: the 3PL kits every vehicle that lands (backlog absorbs all supply)
    from .atp import vehicle_supply
    kits = {}
    for sku, lots in vehicle_supply(conn).items():
        for d, q, src in lots:
            i = (d - start).days
            if 0 <= i < days:
                kits[i] = kits.get(i, 0) + q
    plan["KITS"] = kits
    return plan


# ---------------------------------------------------------------------------- the run

def run(conn, horizon=HORIZON_DAYS, receipts_override=None, alternates_override=None):
    as_of = dt.date.fromisoformat(get_as_of(conn))
    days = horizon
    bom = load_bom(conn)
    items = {r["item_id"]: r for r in conn.execute("SELECT * FROM item")}
    plan = mps(conn, as_of, days)
    gross = {}
    pegs = {}

    def add(item, i, q, parent, parent_i):
        if 0 <= i < days and q:
            gross.setdefault(item, {})
            gross[item][i] = gross[item].get(i, 0) + q
            pegs.setdefault((item, i), []).append({"parent": parent, "parent_day": parent_i, "qty": q})

    # level 1: explode the MPS (vehicles at the CM, packs at Fremont, kits at the 3PL)
    for item, by_day in plan.items():
        for i, q in by_day.items():
            day = as_of + dt.timedelta(days=i)
            if item == "KITS":
                add("CHG-1", i, q, "KIT", i)
                continue
            kind = items[item]["kind"]
            site = MPS_SITE.get(kind)
            for child, per, level, pos in bom_children(bom, item, day):
                if child in PLANNED or items[child]["make_buy"] == "CM_SOURCED":
                    add(child, i - STAGING_DAYS.get(PLANNED.get(child, site), 0), q * per, item, i)

    # net planned items; collect their supply schedule to explode visibility levels below them
    results = {}
    ohs = on_hand(conn)
    recs = receipts_override if receipts_override is not None else scheduled_receipts(conn, as_of)
    alts = alternates_override if alternates_override is not None else alternates(conn, as_of)
    for item, site in PLANNED.items():
        it = items[item]
        res = net_item(days, ohs.get(item, 0), it["safety_stock"] or 0, it["lead_time_days"] or 0, it["moq"] or 0,
                       it["order_multiple"] or 0, gross.get(item, {}), recs.get(item, []), alts.get(item))
        res.update(item_id=item, site_id=site, name=it["name"], uom=it["uom"], lead_time=it["lead_time_days"],
                   safety_stock=it["safety_stock"], moq=it["moq"], mult=it["order_multiple"], on_hand=ohs.get(item, 0),
                   supplier_id=it["primary_supplier_id"], receipt_detail=recs.get(item, []),
                   alternates=alts.get(item, []))
        results[item] = res

    # visibility levels: what our purchases and the CM's builds ask of tiers 2 and 3
    vis = {}

    def vis_add(item, i, q, parent):
        if q and 0 <= i < days:
            vis.setdefault(item, {})
            vis[item][i] = vis[item].get(i, 0) + q
            pegs.setdefault((item, i), []).append({"parent": parent, "parent_day": i, "qty": q})

    frontier = []
    for item, res in results.items():
        lt = int(res["lead_time"] or 0)
        for i in range(days):
            q = res["receipts"][i] + res["planned_receipts"][i]
            if q:
                frontier.append((item, i - lt, q))
    for item in ("FRM-1", "WHL-F", "WHL-R", "TIR-1", "BRK-1", "HRN-VH"):
        for i, q in gross.get(item, {}).items():
            vis_add(item, i, q, "VEHICLE")
    while frontier:
        nxt = []
        for parent, i, q in frontier:
            day = as_of + dt.timedelta(days=max(0, i))
            for child, per, level, pos in bom_children(bom, parent, day):
                if level != "SUPPLIER":
                    continue
                vis_add(child, i, q * per, parent)
                lt = 14 if items[child]["kind"] != "MATERIAL" else 28
                nxt.append((child, i - lt, q * per))
        frontier = nxt
    llc = low_level_codes(bom)
    return {"as_of": as_of.isoformat(), "days": days, "mps": plan, "items": results, "visibility": vis,
            "pegs": pegs, "llc": llc, "gross": gross}


def on_hand(conn):
    oh = {}
    for r in conn.execute("SELECT item_id, SUM(qty) q FROM inventory_balance WHERE owner='OEM' AND stock_status='AVAILABLE'"
                          " AND site_id IN ('OEM-FRE','3PL-RNO') GROUP BY item_id"):
        oh[r["item_id"]] = r["q"]
    for r in conn.execute("SELECT item_id, COUNT(*) n FROM unit WHERE status='COMPONENT' AND on_hold=0 AND"
                          " ((location_site_id='OEM-FRE' AND item_id LIKE 'BMS-%') OR (location_site_id='CM-TXG' AND"
                          " item_id IN ('DU-C','PU-1','HMI-1'))) GROUP BY item_id"):
        oh[r["item_id"]] = oh.get(r["item_id"], 0) + r["n"]
    return oh


def scheduled_receipts(conn, as_of):
    recs = {}
    for r in conn.execute("""SELECT pl.po_id, pl.line_no, pl.item_id, pl.qty - pl.received_qty AS open_qty, pl.need_date,
                                    pl.promise_date, pl.confirm_status
                             FROM po_line pl WHERE pl.status='OPEN' AND pl.qty > pl.received_qty"""):
        if r["item_id"] not in PLANNED:
            continue
        when = r["promise_date"] or r["need_date"]
        recs.setdefault(r["item_id"], []).append({
            "index": (dt.date.fromisoformat(when) - as_of).days, "qty": r["open_qty"], "ref": f"{r['po_id']}-{r['line_no']}",
            "confirmed": r["promise_date"] is not None, "need_index": (dt.date.fromisoformat(r["need_date"]) - as_of).days})
    # lots received but still in IQC become available the next day
    for r in conn.execute("SELECT item_id, SUM(qty) q FROM inventory_balance WHERE stock_status='QC_HOLD' AND site_id='OEM-FRE'"
                          " GROUP BY item_id"):
        if r["item_id"] in PLANNED:
            recs.setdefault(r["item_id"], []).append({"index": 1, "qty": r["q"], "ref": "IQC pending", "confirmed": True})
    return recs


def alternates(conn, as_of):
    """Approved deviations let a phase-out part cover the active one, within quantity and date limits."""
    out = {}
    for d in conn.execute("SELECT * FROM deviation WHERE status='APPROVED' AND valid_to >= ?", (as_of.isoformat(),)):
        if d["item_id"] == "BMS-A":
            stock = conn.execute("SELECT COUNT(*) n FROM unit WHERE item_id='BMS-A' AND status='COMPONENT' AND on_hold=0"
                                 " AND location_site_id='OEM-FRE'").fetchone()["n"]
            out.setdefault("BMS-B", []).append({"item": "BMS-A", "deviation": d["deviation_id"], "on_hand": stock,
                                                "allowance": max(0, d["qty_limit"] - d["qty_used"]),
                                                "valid_to": (dt.date.fromisoformat(d["valid_to"]) - as_of).days})
    return out


def constrained_pack_plan(conn, result=None):
    """Pack MPS limited by component shortages (used by ATP so promises reflect the BMS gap)."""
    result = result or run(conn)
    as_of = dt.date.fromisoformat(result["as_of"])
    plan = {k: dict(v) for k, v in result["mps"].items() if k in ("PK-STD", "PK-LRG")}
    lost = {}
    for item in ("CEL-21700", "BMS-B", "ENC-STD", "ENC-LRG", "BUS-S", "BUS-L", "HRN-PK", "GSK-B"):
        res = result["items"][item]
        prev_short = 0.0
        for i, v in enumerate(res["projected"]):
            short = max(0.0, -v)
            inc = short - prev_short
            prev_short = short
            if inc > 0:
                per = 49.0 if item == "CEL-21700" else 1.0
                lost[i] = max(lost.get(i, 0.0), inc / per)
    out = {}
    for sku, by_day in plan.items():
        out[sku] = {}
        for i, q in by_day.items():
            day_total = sum(plan[s].get(i, 0) for s in plan)
            cut = lost.get(i, 0.0) * (q / day_total if day_total else 0)
            out[sku][i] = max(0, int(round(q - cut)))
    return {"as_of": as_of, "plan": out, "lost_by_day": lost}


def run_and_store(conn, triggered_by="nightly run"):
    res = run(conn)
    now = get_now(conn)
    as_of = dt.date.fromisoformat(res["as_of"])
    n_orders = sum(len(r["planned_releases"]) for r in res["items"].values())
    n_msgs = sum(len(r["messages"]) for r in res["items"].values())
    summary = {item: {"first_short": r["first_short"], "messages": len(r["messages"])} for item, r in res["items"].items()}
    cur = conn.execute("INSERT INTO mrp_run(ran_at, as_of, horizon_days, forecast_version, triggered_by, items_planned,"
                       " planned_orders, messages, summary_json) VALUES (?,?,?,?,?,?,?,?,?)",
                       (now, res["as_of"], res["days"], "S&OP-2026-09", triggered_by, len(res["items"]), n_orders, n_msgs,
                        json.dumps(summary)))
    run_id = cur.lastrowid
    k = 0
    for item, r in res["items"].items():
        lt = int(r["lead_time"] or 0)
        otype = "PURCHASE"
        for rel, qty in sorted(r["planned_releases"].items()):
            k += 1
            due = rel + lt
            conn.execute("INSERT INTO planned_order VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (f"PLO-{run_id}-{k:04d}", run_id, item, r["site_id"], otype, r["supplier_id"], qty,
                          (as_of + dt.timedelta(days=rel)).isoformat(), (as_of + dt.timedelta(days=due)).isoformat(),
                          0, "PLANNED", None))
        for m in r["messages"]:
            conn.execute("INSERT INTO mrp_message(run_id, item_id, site_id, message, ref, bucket_date, qty, detail)"
                         " VALUES (?,?,?,?,?,?,?,?)", (run_id, item, r["site_id"], m["message"], m.get("ref"),
                                                       (as_of + dt.timedelta(days=m["index"])).isoformat(), m.get("qty"),
                                                       m.get("detail")))
    return run_id
