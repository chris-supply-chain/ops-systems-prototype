"""Plan: master schedule & capacity, time-phased MRP, replenishment.

Thin over ops.logic.mrp and ops.logic.atp. The MRP grid is computed live from the
current data by the same function the nightly run and the closed loop call, and
the stored runs (mrp_run) are shown as history.
"""
import datetime as dt
import json
import math
import re
import statistics

from ...dates import month_day, to_date
from ...db import as_of as get_as_of, now as get_now
from ...logic import atp as A
from ...logic import mrp as M
from ...logic.contracts import after_action
from ..router import HttpError, get, post

SITE_LABEL = {"OEM-FRE": "Fremont pack line", "CM-TXG": "Consigned at the CM · Taichung", "3PL-RNO": "3PL kitting · Reno"}
SITE_ORDER = ["OEM-FRE", "CM-TXG", "3PL-RNO"]
MSG_RANK = {"SHORTAGE": 0, "PAST_DUE_RELEASE": 1, "EXPEDITE": 2, "BELOW_SAFETY_STOCK": 3, "UNCONFIRMED": 4,
            "RELEASE": 5, "DEFER": 6, "CANCEL": 7}
MSG_TONE = {"SHORTAGE": "critical", "PAST_DUE_RELEASE": "serious", "EXPEDITE": "serious", "BELOW_SAFETY_STOCK": "warning",
            "UNCONFIRMED": "warning", "RELEASE": "info", "DEFER": "neutral", "CANCEL": "neutral"}


def _day(as_of, i):
    return (as_of + dt.timedelta(days=int(i))).isoformat()


def _ref(ref):
    """'4500136-5' -> ('4500136', 5); anything else -> (None, None)."""
    if not ref or "-" not in str(ref):
        return None, None
    po, _, line = str(ref).rpartition("-")
    return (po, int(line)) if po.isdigit() and line.isdigit() else (None, None)


def _num(v):
    if v is None:
        return None
    return int(v) if float(v).is_integer() else round(float(v), 3)


_DAY_RE = re.compile(r"\bday (-?\d+)")


def _dated(text, as_of):
    """The engine speaks in day indexes ('day 4'); planners read dates ('Sep 30')."""
    if not text:
        return text

    def sub(m):
        d = as_of + dt.timedelta(days=int(m.group(1)))
        return month_day(d)
    return _DAY_RE.sub(sub, text)


def _messages(res, as_of):
    out = []
    for m in res["messages"]:
        po, line = _ref(m.get("ref"))
        row = {"message": m["message"], "tone": MSG_TONE.get(m["message"], "neutral"), "index": m["index"],
               "date": _day(as_of, m["index"]), "qty": _num(m.get("qty")), "ref": m.get("ref"), "po_id": po,
               "line_no": line, "detail": _dated(m.get("detail"), as_of)}
        for k in ("from_index", "to_index", "release_index"):
            if m.get(k) is not None:
                row[k.replace("index", "date")] = _day(as_of, m[k])
        out.append(row)
    out.sort(key=lambda r: (MSG_RANK.get(r["message"], 9), r["index"]))
    return out


def _worst(msgs):
    if not msgs:
        return None
    return min(msgs, key=lambda m: MSG_RANK.get(m["message"], 9))["message"]


def _latest_run(conn):
    return conn.execute("SELECT * FROM mrp_run ORDER BY run_id DESC LIMIT 1").fetchone()


def _runs(conn, limit=12):
    out = []
    for r in conn.execute("SELECT * FROM mrp_run ORDER BY run_id DESC LIMIT ?", (limit,)):
        summary = json.loads(r["summary_json"] or "{}")
        short = sorted(k for k, v in summary.items() if v.get("first_short") is not None)
        out.append({"run_id": r["run_id"], "ran_at": r["ran_at"], "as_of": r["as_of"], "triggered_by": r["triggered_by"],
                    "items_planned": r["items_planned"], "planned_orders": r["planned_orders"], "messages": r["messages"],
                    "short_items": short,
                    "bms_first_short": (summary.get("BMS-B") or {}).get("first_short")})
    return out


# ============================================================================ MRP

@get(r"^/api/plan/mrp$")
def mrp_view(req):
    conn = req.conn
    res = M.run(conn)
    as_of = to_date(res["as_of"])
    days = res["days"]
    llc = res["llc"]
    items = {r["item_id"]: r for r in conn.execute(
        "SELECT i.*, s.name AS supplier_name, s.tier FROM item i LEFT JOIN supplier s ON s.supplier_id = i.primary_supplier_id")}
    policy = {(r["item_id"], r["site_id"]): r for r in conn.execute("SELECT * FROM replenishment_policy")}

    picker = []
    for item_id, r in res["items"].items():
        msgs = _messages(r, as_of)
        worst = _worst(msgs)
        proj = r["projected"]
        low = min(range(len(proj)), key=lambda i: proj[i]) if proj else 0
        picker.append({"item_id": item_id, "name": r["name"], "site_id": r["site_id"], "site": SITE_LABEL[r["site_id"]],
                       "llc": llc.get(item_id), "kind": items[item_id]["kind"], "status": worst,
                       "tone": MSG_TONE.get(worst, "good"), "messages": len(msgs),
                       "first_short": _day(as_of, r["first_short"]) if r["first_short"] is not None else None,
                       "min_projected": _num(proj[low]) if proj else None, "supplier_id": r["supplier_id"]})
    picker.sort(key=lambda p: (SITE_ORDER.index(p["site_id"]), p["llc"] or 0, p["item_id"]))

    sel = req.arg("item")
    if sel not in res["items"]:
        crit = [p for p in picker if p["status"] == "SHORTAGE"]
        sel = crit[0]["item_id"] if crit else "BMS-B"
    r = res["items"][sel]
    it = items[sel]
    pol = policy.get((sel, r["site_id"])) or {}
    rel = r["planned_releases"]
    releases = [{"index": i, "date": _day(as_of, i), "qty": _num(q), "past_due": i < 0} for i, q in sorted(rel.items())]
    rel_arr = [0.0] * days
    for i, q in rel.items():
        if 0 <= i < days:
            rel_arr[i] += q
    receipts = []
    for x in sorted(r["receipt_detail"], key=lambda x: x["index"]):
        po, line = _ref(x["ref"])
        receipts.append({"ref": x["ref"], "po_id": po, "line_no": line, "qty": _num(x["qty"]), "index": x["index"],
                         "date": _day(as_of, max(0, x["index"])), "promise_date": _day(as_of, x["index"]),
                         "need_date": _day(as_of, x["need_index"]) if x.get("need_index") is not None else None,
                         "confirmed": bool(x.get("confirmed", True)), "past_due": x["index"] < 0})
    alts = [{"item": a["item"], "deviation": a.get("deviation"), "on_hand": a["on_hand"], "allowance": a["allowance"],
             "valid_to": _day(as_of, a["valid_to"])} for a in r.get("alternates") or []]
    proj = r["projected"]
    low_i = min(range(days), key=lambda i: proj[i])
    stored = _latest_run(conn)
    stored_orders = []
    if stored:
        stored_orders = [dict(x) for x in conn.execute(
            "SELECT planned_order_id, qty, release_date, due_date, status, order_type, supplier_id FROM planned_order"
            " WHERE run_id=? AND item_id=? ORDER BY due_date", (stored["run_id"], sel))]
    parents = sorted({b["parent_item_id"] for b in conn.execute(
        "SELECT DISTINCT parent_item_id FROM bom_line WHERE child_item_id=?", (sel,))})

    record = {
        "item_id": sel, "name": r["name"], "site_id": r["site_id"], "site": SITE_LABEL[r["site_id"]], "uom": r["uom"],
        "kind": it["kind"], "make_buy": it["make_buy"], "llc": llc.get(sel), "revision": it["revision"],
        "on_hand": _num(r["on_hand"]), "safety_stock": _num(r["safety_stock"] or 0), "lead_time": r["lead_time"],
        "moq": r["moq"], "mult": r["mult"], "lot_rule": pol.get("lot_size_rule") or "MOQ_MULTIPLE",
        "policy": pol.get("policy") or "MRP", "supplier_id": r["supplier_id"], "supplier_name": it["supplier_name"],
        "std_cost": it["std_cost"], "parents": parents,
        "gross": [_num(v) for v in r["gross"]], "receipts": [_num(v) for v in r["receipts"]],
        "alt": [_num(v) for v in r["alt"]], "projected": [_num(v) for v in proj],
        "planned_receipts": [_num(v) for v in r["planned_receipts"]], "planned_releases": [_num(v) for v in rel_arr],
        "recommended": [_num(v) for v in r["recommended"]], "releases": releases, "receipt_detail": receipts,
        "alternates": alts, "messages": _messages(r, as_of),
        "first_short": _day(as_of, r["first_short"]) if r["first_short"] is not None else None,
        "min_projected": _num(proj[low_i]), "min_projected_date": _day(as_of, low_i),
        "gross_total": _num(sum(r["gross"])), "receipts_total": _num(sum(r["receipts"])),
        "alt_total": _num(sum(r["alt"])), "planned_total": _num(sum(r["planned_receipts"])),
        "stored_planned_orders": stored_orders,
        "pegged_days": sorted(i for (item, i) in res["pegs"] if item == sel),
    }

    # the demand we release to tiers 2-3 (and the CM's own suppliers), by week
    tiers = {r2["supplier_id"]: r2["tier"] for r2 in conn.execute("SELECT supplier_id, tier FROM supplier")}
    weeks = [(as_of + dt.timedelta(days=7 * k)).isoformat() for k in range((days + 6) // 7)]
    vis = []
    for item_id, by_day in res["visibility"].items():
        i2 = items[item_id]
        buckets = [0.0] * len(weeks)
        for i, q in by_day.items():
            if 0 <= i < days:
                buckets[i // 7] += q
        par = sorted({p["parent"] for (itm, _), ps in res["pegs"].items() if itm == item_id for p in ps})
        vis.append({"item_id": item_id, "name": i2["name"], "uom": i2["uom"], "supplier_id": i2["primary_supplier_id"],
                    "supplier_name": i2["supplier_name"], "tier": tiers.get(i2["primary_supplier_id"]),
                    "llc": llc.get(item_id), "make_buy": i2["make_buy"], "parents": par,
                    "weeks": [round(b, 1 if i2["uom"] == "kg" else 0) for b in buckets],
                    "total": round(sum(buckets), 1)})
    vis.sort(key=lambda v: (0 if v["make_buy"] == "CM_SOURCED" else 1, v["llc"] or 0, v["item_id"]))

    from ...logic.evals import TEXTBOOK, textbook_results
    tb_res = {cid: (why, exp, act, ok) for cid, why, exp, act, ok in textbook_results()}
    textbook = []
    for case in TEXTBOOK:
        why, exp, act, ok = tb_res[case["id"]]
        textbook.append({"id": case["id"], "why": why, "args": case["args"], "expect": exp, "actual": act, "ok": ok})

    return {
        "as_of": res["as_of"], "now": get_now(conn), "days": days,
        "dates": [_day(as_of, i) for i in range(days)],
        "items": picker, "record": record, "visibility": {"weeks": weeks, "rows": vis},
        "latest_run": dict(stored) if stored else None, "runs": _runs(conn),
        "textbook": textbook,
        "mps_days": {k: len(v) for k, v in res["mps"].items()},
    }


@get(r"^/api/plan/mrp/peg$")
def mrp_peg(req):
    """Pegging for one gross-requirement cell: which builds need it, and which customer promises ride on them."""
    conn = req.conn
    item = req.arg("item")
    day = req.arg("day", cast=int)
    if not item or day is None:
        raise HttpError(400, "item and day are required")
    res = M.run(conn)
    as_of = to_date(res["as_of"])
    pegs = res["pegs"].get((item, day), [])
    names = {r["item_id"]: r["name"] for r in conn.execute("SELECT item_id, name FROM item")}
    parents = []
    sources = {}
    for p in pegs:
        pdate = _day(as_of, p["parent_day"])
        par = p["parent"]
        kind = ("KIT" if par == "KIT" else "PACK" if par.startswith("PK-") else "VEHICLE" if par.startswith("LV1-")
                else "ITEM")
        parents.append({"parent": par, "parent_name": names.get(par, "Kits shipped from the 3PL" if par == "KIT" else par),
                        "parent_date": pdate, "qty": _num(p["qty"]), "kind": kind,
                        "build_qty": _num(res["mps"].get(par, {}).get(p["parent_day"])) if kind in ("PACK", "VEHICLE") else None})
        if kind == "PACK":
            sources[f"Pack MPS {pdate}"] = par
        elif kind == "VEHICLE":
            sources[f"CM build {pdate}"] = par
    orders = []
    total_orders = 0
    binding, binding_late = {}, {}
    if sources or any(p["kind"] == "KIT" for p in parents):
        alloc, _, _ = A.promise_all(conn)
        info = {r["order_id"]: r for r in conn.execute(
            "SELECT o.order_id, o.channel, o.ship_to_region, o.ship_to_state, o.promised_date, ol.item_id AS kit"
            " FROM customer_order o JOIN order_line ol ON ol.order_id=o.order_id AND ol.line_no=1 WHERE o.status='OPEN'")}
        kit_day = _day(as_of, day)
        for oid, a in alloc.items():
            hit = None
            for sku, lst in a.get("pegs", {}).items():
                for pg in lst:
                    if pg["source"] in sources:
                        hit = (sku, pg["source"])
            if hit is None and any(p["kind"] == "KIT" for p in parents) and a.get("ship_date") == kit_day:
                hit = ("KIT", f"Kitted {kit_day}")
            if hit and oid in info:
                total_orders += 1
                o = info[oid]
                slip = (to_date(a["promise"]) - to_date(o["promised_date"])).days if a.get("promise") and o["promised_date"] else None
                # the binding supply is whichever component of the kit becomes available last
                bind = max(((sku, pg) for sku, lst in a.get("pegs", {}).items() for pg in lst),
                           key=lambda t: t[1]["available"], default=(None, {}))
                kind = "vehicle" if (bind[0] or "").startswith("LV1") else "pack" if (bind[0] or "").startswith("PK") else None
                binding[kind] = binding.get(kind, 0) + 1
                if slip and slip > 0:
                    binding_late[kind] = binding_late.get(kind, 0) + 1
                if len(orders) < 250:
                    orders.append({"order_id": oid, "kit": o["kit"], "channel": o["channel"], "region": o["ship_to_region"],
                                   "state": o["ship_to_state"], "promised_date": o["promised_date"],
                                   "atp_promise": a.get("promise"), "ship_date": a.get("ship_date"), "slip_days": slip,
                                   "pegged_sku": hit[0], "source": hit[1], "binding_sku": bind[0],
                                   "binding_kind": kind, "binding_source": bind[1].get("source"),
                                   "binding_available": bind[1].get("available")})
        orders.sort(key=lambda o: (o["ship_date"] or "9999", o["order_id"]))
    gross = res["items"].get(item, {}).get("gross") if item in res["items"] else None
    return {"item_id": item, "item_name": names.get(item), "day": day, "date": _day(as_of, day),
            "gross": _num(gross[day]) if gross and 0 <= day < len(gross) else _num(sum(p["qty"] for p in pegs)),
            "parents": parents, "orders": orders, "orders_total": total_orders,
            "binding": binding, "binding_late": binding_late,
            "note": ("Consigned parts are staged at the CM the day before the build they feed."
                     if res["items"].get(item, {}).get("site_id") == "CM-TXG" else None)}


@post(r"^/api/plan/mrp/run$")
def mrp_rerun(req):
    conn = req.conn
    run_id = M.run_and_store(conn, triggered_by=(req.body.get("triggered_by") or "manual (MRP page)")[:80])
    from ...logic import exceptions
    exceptions.detect(conn)
    run = conn.execute("SELECT * FROM mrp_run WHERE run_id=?", (run_id,)).fetchone()
    short = [dict(r) for r in conn.execute(
        "SELECT item_id, bucket_date, qty, detail FROM mrp_message WHERE run_id=? AND message='SHORTAGE'", (run_id,))]
    after_action(conn)
    return {"run": dict(run), "shortages": short}


# ============================================================================ BOM explosion

ECO_RE_POS = {"BMS": "ECO-0031", "GASKET": "ECO-0036", "DRIVE_UNIT": "ECO-0042"}


@get(r"^/api/plan/bom$")
def bom_view(req):
    conn = req.conn
    as_of = to_date(get_as_of(conn))
    root = req.arg("item", "LV1-SLATE-L")
    on = req.arg("date") or as_of.isoformat()
    try:
        on_d = to_date(on)
    except ValueError:
        raise HttpError(400, f"bad date {on!r}")
    items = {r["item_id"]: r for r in conn.execute(
        "SELECT i.item_id, i.name, i.kind, i.make_buy, i.uom, i.revision, i.primary_supplier_id, i.lead_time_days,"
        " i.std_cost, s.name AS supplier_name, s.tier FROM item i LEFT JOIN supplier s ON s.supplier_id=i.primary_supplier_id")}
    if root not in items:
        raise HttpError(404, f"unknown item {root}")
    bom = M.load_bom(conn)
    ecos = {r["eco_id"]: dict(r) for r in conn.execute("SELECT * FROM eco")}
    ds = on_d.isoformat()

    def active(b):
        return b["eff_from"] <= ds and (b["eff_to"] is None or b["eff_to"] > ds)

    count = [0]

    def explode(parent, level, ext, seen):
        by_pos = {}
        for b in bom.get(parent, []):
            by_pos.setdefault(b["position"], []).append(b)
        out = []
        for pos, lines in by_pos.items():
            act = [b for b in lines if active(b)]
            others = [b for b in lines if not active(b)]
            show = act or sorted(others, key=lambda b: b["eff_from"])[:1]
            for b in show:
                child = b["child_item_id"]
                it = items[child]
                count[0] += 1
                node = {"item_id": child, "name": it["name"], "kind": it["kind"], "make_buy": it["make_buy"],
                        "uom": it["uom"], "revision": it["revision"], "supplier_id": it["primary_supplier_id"],
                        "supplier_name": it["supplier_name"], "tier": it["tier"], "lead_time": it["lead_time_days"],
                        "std_cost": it["std_cost"], "position": pos, "level": level, "qty_per": b["qty_per"],
                        "ext_qty": round(ext * b["qty_per"], 4), "bom_level": b["bom_level"], "eff_from": b["eff_from"],
                        "eff_to": b["eff_to"], "eco_id": b["eco_id"], "active": b in act,
                        "alternates": [{"item_id": o["child_item_id"], "name": items[o["child_item_id"]]["name"],
                                        "eff_from": o["eff_from"], "eff_to": o["eff_to"], "eco_id": o["eco_id"],
                                        "state": "superseded" if (o["eff_to"] and o["eff_to"] <= ds) else "future"}
                                       for o in others if o is not b],
                        "children": []}
                if child not in seen and level < 8:
                    node["children"] = explode(child, level + 1, ext * b["qty_per"], seen | {child})
                out.append(node)
        return out

    tree = explode(root, 1, 1.0, {root})
    used_ecos = sorted({n for n in _walk_ecos(tree)})
    roots = [{"item_id": r["parent_item_id"], "name": items[r["parent_item_id"]]["name"], "kind": items[r["parent_item_id"]]["kind"]}
             for r in conn.execute("SELECT DISTINCT parent_item_id FROM bom_line")]
    kind_order = {"KIT": 0, "VEHICLE": 1, "PACK": 2, "MODULE": 3, "COMPONENT": 4, "MATERIAL": 5}
    roots.sort(key=lambda r: (kind_order.get(r["kind"], 9), r["item_id"]))
    r = items[root]
    return {"as_of": as_of.isoformat(), "date": ds, "root": {"item_id": root, "name": r["name"], "kind": r["kind"],
                                                            "make_buy": r["make_buy"], "uom": r["uom"]},
            "tree": tree, "lines": count[0], "roots": roots,
            "ecos": [ecos[e] for e in used_ecos if e in ecos],
            "all_ecos": [{k: e[k] for k in ("eco_id", "title", "status", "effectivity_type", "effective_date", "old_item_id",
                                            "new_item_id", "cost_delta")} for e in ecos.values()]}


def _walk_ecos(nodes):
    for n in nodes:
        if n.get("eco_id"):
            yield n["eco_id"]
        for a in n.get("alternates", []):
            if a.get("eco_id"):
                yield a["eco_id"]
        yield from _walk_ecos(n.get("children", []))


# ============================================================================ MPS & capacity

FAMILIES = {
    "VEH": {"label": "Vehicles (CM, Taichung)", "site": "CM-TXG", "plan_type": "CM_COMMIT", "lines": ["L1", "L2"],
            "skus": ["LV1-DUNE", "LV1-SLATE", "LV1-FERN", "LV1-EMBER"]},
    "PACK": {"label": "Battery packs (OEM, Fremont)", "site": "OEM-FRE", "plan_type": "OEM_MPS", "lines": ["P1"],
             "skus": ["PK-STD", "PK-LRG"]},
}


def _back_skip_sundays(day, n):
    d = day
    while n > 0:
        d -= dt.timedelta(days=1)
        if d.weekday() != 6:
            n -= 1
    return d


def _week_start(as_of):
    start = as_of - dt.timedelta(days=as_of.weekday())
    if as_of.weekday() >= 5:
        start += dt.timedelta(days=7)
    return start


def _rated(caps, site, line, day):
    best = None
    for c in caps:
        if c["site_id"] == site and c["line"] == line and c["effective_from"] <= day.isoformat():
            if best is None or c["effective_from"] > best["effective_from"]:
                best = c
    return best


@get(r"^/api/plan/mps$")
def mps_view(req):
    from ...generate.util import TW_HOLIDAYS, US_HOLIDAYS
    conn = req.conn
    as_of = to_date(get_as_of(conn))
    start = _week_start(as_of)
    W = 13
    weeks = [start + dt.timedelta(days=7 * k) for k in range(W)]
    end = start + dt.timedelta(days=7 * W)

    def wk(day):
        k = (day - start).days // 7
        return 0 if k < 0 else (k if k < W else None)

    fences = {r["site_id"]: r for r in conn.execute("SELECT * FROM time_fence")}
    version = conn.execute("SELECT version, MAX(published_at) p FROM demand_forecast").fetchone()["version"]
    kits = {r["parent_item_id"]: {} for r in conn.execute("SELECT DISTINCT parent_item_id FROM bom_line b JOIN item i"
                                                             " ON i.item_id=b.parent_item_id WHERE i.kind='KIT'")}
    for r in conn.execute("SELECT parent_item_id, child_item_id, position FROM bom_line WHERE parent_item_id IN"
                          " (SELECT item_id FROM item WHERE kind='KIT')"):
        kits[r["parent_item_id"]][r["position"]] = r["child_item_id"]

    fam = {}
    for key, f in FAMILIES.items():
        fam[key] = {k: [0.0] * W for k in ("forecast", "booked", "mps_build", "landing", "landing_unconstrained")}

    # forecast (latest S&OP), weekly buckets are Monday-based in the table
    for r in conn.execute("SELECT item_id, week_start, qty FROM demand_forecast WHERE version=? AND week_start>=? AND week_start<?",
                          (version, start.isoformat(), end.isoformat())):
        k = wk(to_date(r["week_start"]))
        if k is None:
            continue
        if r["item_id"] in kits:
            fam["VEH"]["forecast"][k] += r["qty"]
            fam["PACK"]["forecast"][k] += r["qty"]
        elif r["item_id"].startswith("PK-"):
            fam["PACK"]["forecast"][k] += r["qty"]

    # booked demand: open (unallocated) orders by the day they must ship to keep their promise
    past_due = {"VEH": 0, "PACK": 0}
    for r in conn.execute("""SELECT o.promised_date, o.ship_to_region, COUNT(ol.line_no) AS packs
                             FROM customer_order o JOIN order_line ol ON ol.order_id=o.order_id
                             WHERE o.status='OPEN' AND o.promised_date IS NOT NULL GROUP BY o.order_id"""):
        ship_by = _back_skip_sundays(to_date(r["promised_date"]), A.TRANSIT_MAX.get(r["ship_to_region"], 5))
        k = wk(ship_by)
        if ship_by < start:
            past_due["VEH"] += 1
            past_due["PACK"] += r["packs"]
        if k is None:
            continue
        fam["VEH"]["booked"][k] += 1
        fam["PACK"]["booked"][k] += r["packs"]

    # MPS by build week, and by SKU
    mps_sku = {}
    for r in conn.execute("SELECT plan_type, item_id, plan_date, SUM(qty) q FROM build_plan WHERE plan_date>? AND plan_date<?"
                          " GROUP BY plan_type, item_id, plan_date", (as_of.isoformat(), end.isoformat())):
        k = wk(to_date(r["plan_date"]))
        if k is None:
            continue
        key = "VEH" if r["plan_type"] == "CM_COMMIT" else "PACK"
        fam[key]["mps_build"][k] += r["q"]
        mps_sku.setdefault(r["item_id"], [0] * W)[k] += r["q"]

    # supply landing at the kitting point (Reno): pipeline + builds offset by transit; packs constrained by MRP
    res = M.run(conn)
    cons = M.constrained_pack_plan(conn, res)
    full = {"plan": {s: dict(v) for s, v in res["mps"].items() if s in ("PK-STD", "PK-LRG")}}
    vs = A.vehicle_supply(conn)
    ps = A.pack_supply(conn, cons)
    ps_full = A.pack_supply(conn, full)
    horizon_end = as_of + dt.timedelta(days=res["days"])
    for r in conn.execute("SELECT item_id, plan_date, SUM(qty) q FROM build_plan WHERE plan_type='OEM_MPS' AND plan_date>=?"
                          " AND plan_date<? GROUP BY 1, 2", (horizon_end.isoformat(), end.isoformat())):
        landing = A.next_truck_available(to_date(r["plan_date"]))
        for lots in (ps, ps_full):
            lots.setdefault(r["item_id"], []).append((landing, r["q"], f"Pack MPS {r['plan_date']} (beyond MRP horizon)"))
    on_hand = {"VEH": 0, "PACK": 0}
    for lots, key, target in ((vs, "VEH", "landing"), (ps, "PACK", "landing"), (ps_full, "PACK", "landing_unconstrained")):
        for sku, ls in lots.items():
            for day, q, src in ls:
                k = wk(day)
                if k is not None:
                    fam[key][target][k] += q
                if target == "landing" and src == "3PL stock":
                    on_hand[key] += q
    for sku, ls in vs.items():
        for day, q, src in ls:
            k = wk(day)
            if k is not None:
                fam["VEH"]["landing_unconstrained"][k] += q

    # time fences, in build dates and in the landing dates they imply
    fence_out = {}
    for key, f in FAMILIES.items():
        tf = fences[f["site"]]
        frozen = as_of + dt.timedelta(days=tf["frozen_days"])
        slushy = as_of + dt.timedelta(days=tf["slushy_days"])
        if key == "VEH":
            lf, ls_ = A.next_sailing_available(frozen), A.next_sailing_available(slushy)
        else:
            lf, ls_ = A.next_truck_available(frozen), A.next_truck_available(slushy)
        fence_out[key] = {"frozen_days": tf["frozen_days"], "slushy_days": tf["slushy_days"], "note": tf["note"],
                          "frozen_until": frozen.isoformat(), "slushy_until": slushy.isoformat(),
                          "landing_frozen_until": lf.isoformat(), "landing_slushy_until": ls_.isoformat()}

    # the textbook MPS record per family (landing buckets)
    grids = {}
    for key, f in FAMILIES.items():
        g = fam[key]
        fz = fence_out[key]
        dtf = to_date(fz["landing_frozen_until"])
        proj, pab, atp_cum, zone = [], [], [], []
        run_pab = 0.0
        cum = []
        c = 0.0
        for k in range(W):
            ws = weeks[k]
            z = "frozen" if ws < dtf else ("slushy" if ws < to_date(fz["landing_slushy_until"]) else "liquid")
            zone.append(z)
            booked = g["booked"][k] + (past_due[key] if k == 0 else 0)
            demand = booked if z == "frozen" else max(g["forecast"][k], booked)
            proj.append(round(demand, 1))
            run_pab += g["landing"][k] - demand
            pab.append(round(run_pab, 1))
            c += g["landing"][k] - booked
            cum.append(c)
        for k in range(W):
            atp_cum.append(round(min(cum[k:]), 1))
        grids[key] = {"label": f["label"], "zone": zone, "forecast": [round(x, 1) for x in g["forecast"]],
                      "booked": [round(x, 1) for x in g["booked"]], "past_due": past_due[key],
                      "projected_demand": proj, "mps_build": [round(x) for x in g["mps_build"]],
                      "landing": [round(x) for x in g["landing"]],
                      "landing_unconstrained": [round(x) for x in g["landing_unconstrained"]],
                      "pab": pab, "atp_cumulative": atp_cum, "on_hand_start": on_hand[key]}

    # RCCP: planned load vs rated and demonstrated capacity per line per week
    caps = [dict(r) for r in conn.execute("SELECT * FROM line_capacity")]
    planned = {}
    for r in conn.execute("SELECT site_id, line, plan_date, SUM(qty) q FROM build_plan WHERE plan_date>? AND plan_date<?"
                          " GROUP BY site_id, line, plan_date", (as_of.isoformat(), end.isoformat())):
        k = wk(to_date(r["plan_date"]))
        if k is not None:
            planned[(r["line"], k)] = planned.get((r["line"], k), 0) + r["q"]
    demo = {}
    for r in conn.execute("""SELECT line, COUNT(DISTINCT sched_date) days, SUM(qty_completed) done FROM work_order
                             WHERE sched_date > ? AND sched_date <= ? AND qty_completed > 0 GROUP BY line""",
                          ((as_of - dt.timedelta(days=28)).isoformat(), as_of.isoformat())):
        demo[r["line"]] = r["done"] / max(1, r["days"])
    rccp = []
    for site, line, hol in (("CM-TXG", "L1", TW_HOLIDAYS), ("CM-TXG", "L2", TW_HOLIDAYS), ("OEM-FRE", "P1", US_HOLIDAYS)):
        row = {"site_id": site, "line": line, "label": f"CM {line}" if site == "CM-TXG" else "Pack P1",
               "weeks": []}
        for k in range(W):
            days_ = [weeks[k] + dt.timedelta(days=i) for i in range(7)]
            work = [d for d in days_ if d.weekday() < 5 and d not in hol and d > as_of]
            cap = 0
            takt = None
            for d in work:
                c = _rated(caps, site, line, d)
                if c:
                    cap += c["rated_units_per_day"]
                    takt = c["takt_min"]
            pl = planned.get((line, k), 0)
            dem = round(demo.get(line, 0) * len(work))
            load = pl / cap if cap else None
            row["weeks"].append({"week": weeks[k].isoformat(), "workdays": len(work), "planned": pl, "capacity": cap,
                                 "demonstrated": dem, "load": round(load, 3) if load is not None else None,
                                 "load_vs_demonstrated": round(pl / dem, 3) if dem else None, "takt_min": takt})
        row["demonstrated_per_day"] = round(demo.get(line, 0), 1)
        rccp.append(row)

    # vehicles vs packs at the kitting point; the BMS-driven pack loss by day
    lost = []
    for i, q in sorted(cons["lost_by_day"].items()):
        day = as_of + dt.timedelta(days=i)
        plan_q = sum(res["mps"].get(s, {}).get(i, 0) for s in ("PK-STD", "PK-LRG"))
        lost.append({"date": day.isoformat(), "lost": round(q, 1), "planned": plan_q})
    balance = [{"week": weeks[k].isoformat(), "vehicles": round(fam["VEH"]["landing"][k]),
                "packs": round(fam["PACK"]["landing"][k]), "packs_unconstrained": round(fam["PACK"]["landing_unconstrained"][k]),
                "kit_capacity": sum(A.KIT_CAPACITY[(weeks[k] + dt.timedelta(days=i)).weekday()] for i in range(7))}
               for k in range(W)]

    # S&OP versions and forecast accuracy (D2C only: fleet volume is contracted, not forecast)
    hist_start = start - dt.timedelta(days=7 * 8)
    actual = {}
    for r in conn.execute("SELECT ordered_at FROM customer_order WHERE channel='D2C' AND ordered_at>=?",
                          ((hist_start - dt.timedelta(days=1)).isoformat(),)):
        d = dt.datetime.fromisoformat(r["ordered_at"].replace("Z", "+00:00")).astimezone(
            dt.timezone(dt.timedelta(hours=-7))).date()
        wsd = d - dt.timedelta(days=d.weekday())
        actual[wsd] = actual.get(wsd, 0) + 1
    versions = []
    for v in conn.execute("SELECT version, MAX(published_at) published_at FROM demand_forecast GROUP BY version ORDER BY 2"):
        byw = {}
        for r in conn.execute("SELECT week_start, SUM(qty) q FROM demand_forecast WHERE version=? AND item_id IN"
                              " (SELECT item_id FROM item WHERE kind='KIT') AND week_start>=? AND week_start<? GROUP BY 1",
                              (v["version"], hist_start.isoformat(), end.isoformat())):
            byw[r["week_start"]] = r["q"]
        errs = []
        for wsk, q in byw.items():
            wsd = to_date(wsk)
            complete = wsd + dt.timedelta(days=7) <= as_of
            if complete and wsd >= to_date(v["published_at"]) and actual.get(wsd):
                errs.append((q, actual[wsd]))
        mape = sum(abs(f - a) / a for f, a in errs) / len(errs) if errs else None
        bias = (sum(f for f, a in errs) - sum(a for f, a in errs)) / sum(a for f, a in errs) if errs else None
        versions.append({"version": v["version"], "published_at": v["published_at"],
                         "weeks": [{"week": k2, "qty": round(q, 1)} for k2, q in sorted(byw.items())],
                         "accuracy_weeks": len(errs), "mape": round(mape, 4) if mape is not None else None,
                         "bias": round(bias, 4) if bias is not None else None})
    actual_rows = [{"week": w_.isoformat(), "orders": n, "complete": w_ + dt.timedelta(days=7) <= as_of}
                   for w_, n in sorted(actual.items()) if w_ >= hist_start]

    # KPIs
    backlog = conn.execute("SELECT COUNT(*) n FROM customer_order WHERE status IN ('OPEN','ALLOCATED')").fetchone()["n"]
    backlog_open = conn.execute("SELECT COUNT(*) n FROM customer_order WHERE status='OPEN'").fetchone()["n"]
    extra = conn.execute("""SELECT COUNT(*) n FROM order_line ol JOIN customer_order o USING(order_id)
                            WHERE o.status IN ('OPEN','ALLOCATED') AND ol.line_no=2""").fetchone()["n"]
    last4 = conn.execute("SELECT COUNT(*) n FROM customer_order WHERE ordered_at>=? AND ordered_at<?",
                         ((start - dt.timedelta(days=28)).isoformat(), start.isoformat())).fetchone()["n"] / 4.0
    fc4 = sum(fam["VEH"]["forecast"][:4]) / 4.0
    cm4 = sum(fam["VEH"]["mps_build"][:4]) / 4.0
    pk4 = sum(fam["PACK"]["mps_build"][:4]) / 4.0
    built4 = conn.execute("SELECT COALESCE(SUM(qty_completed),0) n FROM work_order WHERE site_id='CM-TXG' AND sched_date>? AND sched_date<=?",
                          ((as_of - dt.timedelta(days=28)).isoformat(), as_of.isoformat())).fetchone()["n"] / 4.0
    return {
        "as_of": as_of.isoformat(), "now": get_now(conn), "start": start.isoformat(),
        "weeks": [w_.isoformat() for w_ in weeks], "forecast_version": version,
        "kpis": {"backlog": backlog, "backlog_open": backlog_open, "backlog_allocated": backlog - backlog_open,
                 "extra_packs": extra, "orders_per_week": round(last4, 1), "forecast_per_week": round(fc4, 1),
                 "cm_weekly": round(cm4, 1), "pack_weekly": round(pk4, 1), "cm_built_weekly": round(built4, 1),
                 "weeks_of_backlog": round(backlog / cm4, 1) if cm4 else None},
        "fences": fence_out, "grids": grids, "mps_by_sku": [{"item_id": k2, "weeks": v} for k2, v in sorted(mps_sku.items())],
        "rccp": rccp, "balance": balance, "pack_loss": lost,
        "pack_loss_total": round(sum(x["lost"] for x in lost), 1),
        "sop": {"versions": versions, "actual": actual_rows},
        "kit_capacity_week": sum(A.KIT_CAPACITY.values()),
    }


# ============================================================================ Replenishment

POLICY_TEXT = {
    "MRP": "Time-phased netting of dependent demand from the MPS through the BOM; orders are planned, not triggered by a level.",
    "REORDER_POINT": "Order when the position (on hand + on order − allocated) falls to the reorder point: demand over the lead time plus safety stock; order up to max.",
    "MIN_MAX": "Keep stock between a min and a max; when it drops below min the owner (here the CM) orders back up to max.",
    "ORDER_UP_TO": "Every review period, order enough to bring the position up to a target level. Not in use today.",
    "DRP": "Distribution requirements: net the 3PL's demand against its stock and pipeline and pass the net requirement upstream (CM commit, pack MPS).",
    "VMI": "The supplier owns and manages stock between an agreed min and max, using our forecast and usage.",
    "CONSIGNMENT": "OEM-owned stock held at the CM in Taiwan: the CM consumes it at the line and the OEM replenishes it.",
}
PROD_DAYS = {"OEM-FRE": 5, "CM-TXG": 5, "3PL-RNO": 6, "SUP-KES": 5, "SUP-PNC": 5}
USAGE_SITE = {"SUP-KES": "OEM-FRE", "SUP-PNC": "OEM-FRE"}      # a VMI hub serves our line's usage


def _usage(conn, since):
    """Daily usage per (item, site) from what the lines and the 3PL actually consumed."""
    use = {}

    def add(key, day, q):
        use.setdefault(key, {})
        use[key][day] = use[key].get(day, 0) + q

    for r in conn.execute("""SELECT child_item_id AS item, substr(installed_at,1,10) AS d, source, SUM(qty) AS q
                             FROM genealogy WHERE installed_at >= ? AND relation='INSTALLED'
                             AND source IN ('OEM_MES','CM_FEED') GROUP BY 1, 2, 3""", (since,)):
        add((r["item"], "OEM-FRE" if r["source"] == "OEM_MES" else "CM-TXG"), r["d"], r["q"])
    for r in conn.execute("SELECT substr(shipped_at,1,10) d, COUNT(*) n FROM customer_order WHERE shipped_at >= ? GROUP BY 1",
                          (since,)):
        add(("CHG-1", "3PL-RNO"), r["d"], r["n"])
    for col in ("vehicle_serial", "pack_serial"):
        for r in conn.execute(f"""SELECT u.item_id, substr(o.allocated_at,1,10) d, COUNT(*) n FROM customer_order o
                                   JOIN order_line ol ON ol.order_id=o.order_id JOIN unit u ON u.serial=ol.{col}
                                   WHERE o.allocated_at >= ? GROUP BY 1, 2""", (since,)):
            add((r["item_id"], "3PL-RNO"), r["d"], r["n"])
    return use


def _stats(series, as_of, site, window=56):
    """Mean and standard deviation of usage per production day, from first use in the window."""
    from ...generate.util import TW_HOLIDAYS, US_HOLIDAYS
    hol = TW_HOLIDAYS if site == "CM-TXG" else US_HOLIDAYS
    six = PROD_DAYS.get(site, 5) == 6
    days = [as_of - dt.timedelta(days=k) for k in range(window, 0, -1)]
    days = [d for d in days if (d.weekday() < 5 or (six and d.weekday() == 5)) and d not in hol]
    if series:
        first = min(series)
        days = [d for d in days if d.isoformat() >= first]
    vals = [float(series.get(d.isoformat(), 0)) for d in days] if series else []
    if len(vals) < 2:
        return None
    return {"mean": statistics.fmean(vals), "sd": statistics.pstdev(vals), "days": len(vals)}


def _ss_calc(stats, pol, site):
    if not stats:
        return None
    factor = PROD_DAYS.get(site, 5) / 7.0
    z = statistics.NormalDist().inv_cdf(min(0.9999, max(0.5, pol["service_level"])))
    lt = pol["lead_time_days"] * factor
    slt = (pol["lt_std_days"] or 0) * factor
    demand_term = z * stats["sd"] * math.sqrt(lt)
    lt_term = z * stats["mean"] * slt
    rss = z * math.sqrt(lt * stats["sd"] ** 2 + (stats["mean"] ** 2) * slt ** 2)
    return {"z": round(z, 3), "service_level": pol["service_level"], "mean": round(stats["mean"], 2),
            "sd": round(stats["sd"], 2), "days": stats["days"], "lt_prod_days": round(lt, 1), "lt_sd_prod_days": round(slt, 2),
            "demand_term": round(demand_term, 1), "lt_term": round(lt_term, 1), "ss": round(demand_term + lt_term),
            "ss_rss": round(rss), "stored": pol["safety_stock"],
            "ratio": round(pol["safety_stock"] / (demand_term + lt_term), 2) if (demand_term + lt_term) > 0 else None}


@get(r"^/api/plan/replenishment$")
def replenishment_view(req):
    conn = req.conn
    as_of = to_date(get_as_of(conn))
    items = {r["item_id"]: r for r in conn.execute(
        "SELECT i.*, s.name AS supplier_name FROM item i LEFT JOIN supplier s ON s.supplier_id=i.primary_supplier_id")}
    sites = {r["site_id"]: r["name"] for r in conn.execute("SELECT site_id, name FROM site")}
    pols = [dict(r) for r in conn.execute("SELECT * FROM replenishment_policy ORDER BY policy, site_id, item_id")]

    # stock, by where it sits and who owns it
    bal = {}
    for r in conn.execute("SELECT site_id, item_id, owner, stock_status, SUM(qty) q, MAX(as_of) as_of FROM inventory_balance"
                          " GROUP BY 1, 2, 3, 4"):
        bal[(r["site_id"], r["item_id"], r["owner"], r["stock_status"])] = (r["q"], r["as_of"])
    units = {}
    for r in conn.execute("SELECT location_site_id AS site, item_id, status, on_hold, COUNT(*) n FROM unit"
                          " WHERE status IN ('COMPONENT','AT_3PL','ALLOCATED','BUILT') GROUP BY 1, 2, 3, 4"):
        units[(r["site"], r["item_id"], r["status"], r["on_hold"])] = r["n"]
    transit = {r["item_id"]: r["n"] for r in conn.execute(
        "SELECT item_id, COUNT(*) n FROM unit WHERE status='IN_TRANSIT' GROUP BY 1")}
    on_order = {}
    next_rcpt = {}
    for r in conn.execute("""SELECT pl.item_id, po.ship_to_site_id AS site, pl.po_id, pl.line_no,
                                    pl.qty - pl.received_qty AS open_qty, COALESCE(pl.promise_date, pl.need_date) AS due,
                                    pl.promise_date IS NULL AS unconfirmed
                             FROM po_line pl JOIN purchase_order po USING(po_id)
                             WHERE pl.status='OPEN' AND pl.qty > pl.received_qty ORDER BY due"""):
        key = (r["item_id"], r["site"])
        on_order[key] = on_order.get(key, 0) + r["open_qty"]
        next_rcpt.setdefault(key, {"po_id": r["po_id"], "line_no": r["line_no"], "qty": r["open_qty"], "date": r["due"],
                                   "unconfirmed": bool(r["unconfirmed"])})
    backlog = {}
    for r in conn.execute("""SELECT b.child_item_id AS item, COUNT(*) n FROM customer_order o
                             JOIN order_line ol ON ol.order_id=o.order_id AND ol.line_no=1
                             JOIN bom_line b ON b.parent_item_id=ol.item_id AND b.position IN ('VEHICLE','PACK')
                             WHERE o.status='OPEN' GROUP BY 1"""):
        backlog[r["item"]] = backlog.get(r["item"], 0) + r["n"]
    for r in conn.execute("""SELECT ol.item_id AS item, COUNT(*) n FROM customer_order o JOIN order_line ol
                             ON ol.order_id=o.order_id AND ol.line_no=2 WHERE o.status='OPEN' GROUP BY 1"""):
        backlog[r["item"]] = backlog.get(r["item"], 0) + r["n"]

    since = (as_of - dt.timedelta(days=60)).isoformat()
    usage = _usage(conn, since)
    # usage follows the BOM position: a superseded part's history counts for its replacement (ECO cut-ins)
    supersedes = {r["new_item_id"]: r["old_item_id"] for r in conn.execute(
        "SELECT new_item_id, old_item_id FROM eco WHERE new_item_id IS NOT NULL AND old_item_id IS NOT NULL")}

    def series_for(item, site):
        base = dict(usage.get((item, site)) or {})
        old = supersedes.get(item)
        if old and usage.get((old, site)):
            for d_, q_ in usage[(old, site)].items():
                base[d_] = base.get(d_, 0) + q_
        return base or None

    res = M.run(conn)

    def q(key):
        return (bal.get(key) or (0, None))[0] or 0

    rows = []
    for p in pols:
        item, site, pol = p["item_id"], p["site_id"], p["policy"]
        it = items[item]
        uom = it["uom"]
        row = {"item_id": item, "name": it["name"], "site_id": site, "site": sites.get(site, site), "policy": pol,
               "owner": p["owner"], "uom": uom, "lead_time": p["lead_time_days"], "lt_std": p["lt_std_days"],
               "review_days": p["review_days"], "service_level": p["service_level"], "safety_stock": p["safety_stock"],
               "reorder_point": p["reorder_point"], "max_qty": p["max_qty"], "lot_rule": p["lot_size_rule"],
               "moq": it["moq"], "mult": it["order_multiple"], "std_cost": it["std_cost"],
               "supplier_id": it["primary_supplier_id"], "supplier_name": it["supplier_name"],
               "on_hand": None, "on_order": 0, "allocated": 0, "in_production": None, "report_as_of": None,
               "pipeline": None, "backlog": None, "status": "OK", "tone": "good", "action": None, "order_qty": None,
               "link": None}
        if site == "OEM-FRE":
            oh = q((site, item, "OEM", "AVAILABLE")) + units.get((site, item, "COMPONENT", 0), 0)
            row["on_hand"], row["in_qc"] = oh, q((site, item, "OEM", "QC_HOLD"))
        elif site == "CM-TXG" and pol == "CONSIGNMENT":
            row["on_hand"] = units.get((site, item, "COMPONENT", 0), 0)
        elif site == "CM-TXG":
            v = bal.get((site, item, "CM", "AVAILABLE"))
            row["on_hand"] = v[0] if v else None
            row["report_as_of"] = v[1][:10] if v else None
        elif site == "3PL-RNO" and pol == "DRP":
            row["on_hand"] = units.get((site, item, "AT_3PL", 0), 0)
            row["allocated"] = units.get((site, item, "ALLOCATED", 0), 0)
            row["on_hold"] = units.get((site, item, "AT_3PL", 1), 0)
            build_site = "CM-TXG" if item.startswith("LV1") else "OEM-FRE"
            row["pipeline"] = transit.get(item, 0) + units.get((build_site, item, "BUILT", 0), 0)
            row["backlog"] = backlog.get(item, 0)
        elif site == "3PL-RNO":
            row["on_hand"] = q((site, item, "OEM", "AVAILABLE"))
        elif pol == "VMI":
            v = bal.get((site, item, "SUPPLIER", "AVAILABLE"))
            row["on_hand"] = v[0] if v else None
            row["report_as_of"] = v[1][:10] if v else None
            row["in_production"] = q((site, item, "SUPPLIER", "IN_PRODUCTION"))
        row["on_order"] = on_order.get((item, site), 0) if pol != "VMI" else 0
        row["next_receipt"] = next_rcpt.get((item, site)) if pol != "VMI" else None
        oh = row["on_hand"] or 0
        row["position"] = oh + (row["pipeline"] or 0) + row["on_order"] - row["allocated"] if pol == "DRP" \
            else oh + row["on_order"] - row["allocated"]
        st = _stats(series_for(item, USAGE_SITE.get(site, site)), as_of, USAGE_SITE.get(site, site))
        row["usage"] = {"mean": round(st["mean"], 2), "sd": round(st["sd"], 2), "days": st["days"],
                        "includes": supersedes.get(item) if supersedes.get(item) else None} if st else None
        row["ss_calc"] = _ss_calc(st, p, USAGE_SITE.get(site, site))
        mean = st["mean"] if st else 0
        factor = PROD_DAYS.get(USAGE_SITE.get(site, site), 5) / 7.0
        row["dos"] = round(oh / mean / factor, 1) if mean and row["on_hand"] is not None else None
        row["cover_days"] = round(row["position"] / mean / factor, 1) if mean else None
        row["ss_days"] = round((p["safety_stock"] or 0) / mean / factor, 1) if mean else None

        if pol == "MRP":
            r = res["items"].get(item)
            msgs = r["messages"] if r else []
            worst = _worst([{"message": m["message"]} for m in msgs])
            row["status"] = "SHORTAGE" if (r and r["first_short"] is not None) else ("MRP_PLANNED" if not worst else worst)
            row["tone"] = "critical" if row["status"] == "SHORTAGE" else MSG_TONE.get(worst, "good") if worst else "good"
            row["link"] = f"#/mrp?item={item}"
            tally = {}
            for m in msgs:
                tally[m["message"]] = tally.get(m["message"], 0) + 1
            summary = " · ".join(f"{n} {k.lower().replace('_', ' ')}" for k, n in
                                 sorted(tally.items(), key=lambda kv: MSG_RANK.get(kv[0], 9)))
            row["action"] = (f"Runs out {month_day(as_of + dt.timedelta(days=r['first_short']))}: see the MRP record"
                             if r and r["first_short"] is not None
                             else (f"MRP messages: {summary}" if summary else "Planned by MRP, no action messages"))
        elif pol == "REORDER_POINT":
            if row["position"] <= (p["reorder_point"] or 0):
                qty = M.lot_size((p["max_qty"] or 0) - row["position"], it["moq"], it["order_multiple"])
                row.update(status="REORDER", tone="warning", order_qty=qty,
                           action=f"Order {qty:,} from {it['supplier_name']} (up to max {p['max_qty']:,.0f}; MOQ {it['moq']:,}, x{it['order_multiple']:,})")
            elif p["max_qty"] and row["position"] > p["max_qty"]:
                row.update(status="OVER_MAX", tone="info", action="Position above max: defer the next receipt")
            else:
                row["action"] = f"Position {row['position']:,.0f} above reorder point {p['reorder_point']:,.0f}"
        elif pol == "MIN_MAX":
            if row["on_hand"] is None:
                row.update(status="NO_DATA", tone="neutral", action="CM does not report this part (serialized, built to order)")
            elif row["on_hand"] < (p["reorder_point"] or 0):
                row.update(status="BELOW_MIN", tone="warning", action="CM below its min: CM replenishes (visibility only)")
            elif p["max_qty"] and row["on_hand"] > p["max_qty"]:
                row.update(status="OVER_MAX", tone="info", action="CM holding above max")
            else:
                row["action"] = "CM-owned, within min/max"
        elif pol == "CONSIGNMENT":
            if oh < (p["safety_stock"] or 0):
                nr = row["next_receipt"]
                row.update(status="BELOW_MIN", tone="warning",
                           action=f"Below min {p['safety_stock']:,.0f}: expedite {nr['po_id']}-{nr['line_no']} due {month_day(to_date(nr['date']))}"
                           if nr else f"Below min {p['safety_stock']:,.0f}: no open PO")
            elif p["max_qty"] and oh > p["max_qty"]:
                row.update(status="OVER_MAX", tone="info", action=f"Above max {p['max_qty']:,.0f}: push out the next receipt")
            else:
                row["action"] = f"Within {p['safety_stock']:,.0f}–{p['max_qty']:,.0f} at the CM"
        elif pol == "DRP":
            net = (row["backlog"] or 0) - (oh + (row["pipeline"] or 0))
            row["net_requirement"] = net
            if net > 0:
                src = "CM commit" if item.startswith("LV1") else "pack MPS"
                row.update(status="CONSTRAINED", tone="serious",
                           action=f"Backlog exceeds stock + pipeline by {net:,}: supply-constrained, raise the {src} (see MPS)")
                row["link"] = "#/mps"
            else:
                row["action"] = "Pipeline covers the backlog"
        elif pol == "VMI":
            if row["on_hand"] is None:
                row.update(status="NO_DATA", tone="neutral", action="No stock report received")
            elif row["on_hand"] < (p["reorder_point"] or 0):
                row.update(status="BELOW_MIN", tone="serious",
                           action=f"{it['supplier_name']} holds {row['on_hand']:,.0f}, below the agreed min {p['reorder_point']:,.0f}: raise it at the weekly review")
            elif p["max_qty"] and row["on_hand"] > p["max_qty"]:
                row.update(status="OVER_MAX", tone="info", action="Supplier above agreed max (their cost, our obsolescence risk)")
            else:
                row["action"] = f"Within agreed {p['reorder_point']:,.0f}–{p['max_qty']:,.0f}"
        rows.append(row)

    recs = [r for r in rows if r["status"] not in ("OK", "MRP_PLANNED") and r["tone"] not in ("good",)]
    rank = {"critical": 0, "serious": 1, "warning": 2, "info": 3, "neutral": 4}
    recs.sort(key=lambda r: (rank.get(r["tone"], 5), r["item_id"]))

    # consigned stock at the CM: our count vs the CM's own Excel report
    rep_date = conn.execute("SELECT MAX(report_date) d FROM cm_stock_report").fetchone()["d"]
    rep = {r["item_id"]: r for r in conn.execute("SELECT * FROM cm_stock_report WHERE report_date=?", (rep_date,))} if rep_date else {}
    pol_by = {(p["item_id"], p["site_id"]): p for p in pols}
    consigned = []
    for item in ("DU-C", "PU-1", "HMI-1", "DU-B"):
        n = units.get(("CM-TXG", item, "COMPONENT", 0), 0)
        p = pol_by.get((item, "CM-TXG"))
        st = _stats(series_for(item, "CM-TXG") if item != "DU-B" else usage.get((item, "CM-TXG")), as_of, "CM-TXG")
        stranded = item == "DU-B"
        consigned.append({"item_id": item, "name": items[item]["name"], "ours": n,
                          "cm_report": rep[item]["on_hand"] if item in rep else None,
                          "cm_report_ref": rep[item]["source_ref"] if item in rep else None,
                          "min": p["safety_stock"] if p else None, "max": p["max_qty"] if p else None,
                          "value": round(n * (items[item]["std_cost"] or 0), 2),
                          "daily_use": 0 if stranded else (round(st["mean"], 1) if st else 0),
                          "days_cover": None if stranded else (round(n / st["mean"], 1) if st and st["mean"] else None),
                          "lifecycle": items[item]["lifecycle"], "stranded": item == "DU-B",
                          "next_receipt": next_rcpt.get((item, "CM-TXG"))})
    eco42 = conn.execute("SELECT eco_id, title, effective_date, stock_disposition FROM eco WHERE eco_id='ECO-0042'").fetchone()

    # supplier-held stock we can see (VMI agreements and plain reports)
    supplier_stock = []
    for r in conn.execute("""SELECT b.site_id, b.item_id, b.stock_status, SUM(b.qty) q, MAX(b.as_of) as_of, s.name AS site_name
                             FROM inventory_balance b JOIN site s USING(site_id) WHERE b.owner='SUPPLIER'
                             GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"""):
        supplier_stock.append({"site_id": r["site_id"], "site": r["site_name"], "item_id": r["item_id"],
                               "name": items[r["item_id"]]["name"], "status": r["stock_status"], "qty": r["q"],
                               "as_of": r["as_of"][:10], "value": round(r["q"] * (items[r["item_id"]]["std_cost"] or 0), 2),
                               "vmi": (r["item_id"], r["site_id"]) in pol_by})
    kes_ref = conn.execute("SELECT raw_id, subject, received_at FROM raw_email WHERE classified_as='KES_VMI'"
                           " ORDER BY received_at DESC LIMIT 1").fetchone()

    counts = {}
    for p in pols:
        counts[p["policy"]] = counts.get(p["policy"], 0) + 1
    consigned_value = sum(c["value"] for c in consigned if not c["stranded"])
    stranded = next((c for c in consigned if c["stranded"]), None)
    return {
        "as_of": as_of.isoformat(), "now": get_now(conn), "rows": rows, "recommendations": recs,
        "policies": [{"policy": k, "text": v, "count": counts.get(k, 0)} for k, v in POLICY_TEXT.items()],
        "consigned": consigned, "cm_report_date": rep_date, "eco42": dict(eco42) if eco42 else None,
        "supplier_stock": supplier_stock, "kes_email": dict(kes_ref) if kes_ref else None,
        "kpis": {"policies": len(pols), "need_action": len(recs),
                 "consigned_value": round(consigned_value, 2),
                 "stranded_units": stranded["ours"] if stranded else 0,
                 "stranded_value": stranded["value"] if stranded else 0,
                 "supplier_value": round(sum(s["value"] for s in supplier_stock if s["status"] == "AVAILABLE"), 2),
                 "below_min": sum(1 for r in rows if r["status"] in ("BELOW_MIN", "REORDER", "SHORTAGE"))},
    }
