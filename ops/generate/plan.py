"""Plan & source: MES work orders, the MPS / CM commit, the S&OP forecast, purchase
orders with receipts, invoices and supplier promises (history and open), the
forecast released to tiers 1-3 with supplier commits, RFQs, replenishment
policies, freight rates and line downtime.
"""
import datetime as dt
import math

from .master import COLORS, ITEMS, KITS, PACK_SKUS, VEHICLE_SKUS
from .util import (PT, TPE, UTC, TW_HOLIDAYS, US_HOLIDAYS, add_days, add_workdays, at, is_workday,
                   next_workday, week_code, week_start)

COLOR_W = {c[0]: c[2] for c in COLORS}
PACK_W = {"PK-STD": 0.54, "PK-LRG": 0.46}
SHORT = {"LV1-DUNE": "DUN", "LV1-SLATE": "SLT", "LV1-FERN": "FRN", "LV1-EMBER": "EMB"}

# supplier -> confirmation channel for PO acknowledgements
CHANNEL = {"KES": "EDI855", "HDS": "EDI855", "CSP": "PORTAL", "TNM": "PORTAL", "FAP": "PORTAL",
           "PNC": "EXCEL", "SMT": "EMAIL", "NRT": "EMAIL", "BAY": "EMAIL", "VPC": "EMAIL"}
SITE_OF = {"CEL-21700": "OEM-FRE", "BMS-A": "OEM-FRE", "BMS-B": "OEM-FRE", "ENC-STD": "OEM-FRE",
           "ENC-LRG": "OEM-FRE", "BUS-S": "OEM-FRE", "BUS-L": "OEM-FRE", "HRN-PK": "OEM-FRE",
           "GSK-A": "OEM-FRE", "GSK-B": "OEM-FRE", "DU-B": "CM-TXG", "DU-C": "CM-TXG", "PU-1": "CM-TXG",
           "HMI-1": "CM-TXG", "CHG-1": "3PL-RNO"}
TERMS_DAYS = {"Net 30": 30, "Net 45": 45, "Net 60": 60}


def split_det(n, weights):
    raw = {k: n * v for k, v in weights.items()}
    out = {k: int(v) for k, v in raw.items()}
    for k in sorted(raw, key=lambda k: raw[k] - out[k], reverse=True)[: n - sum(out.values())]:
        out[k] += 1
    return out


def build(w):
    work_orders(w)
    build_plans(w)
    demand_forecast(w)
    purchasing(w)
    cm_purchase_orders(w)
    supplier_forecasts(w)
    rfqs(w)
    policies(w)
    freight(w)
    downtime(w)


# ------------------------------------------------------------------ MES work orders

def work_orders(w):
    wos = {}
    for v in w.vehicles:
        key = ("CM-TXG", v["line"], v["day"], v["sku"])
        wo = wos.setdefault(key, {"started": 0, "completed": 0, "scrapped": 0})
        wo["started"] += 1
        wo["completed"] += 1 if v["built_at"] else 0
        wo["scrapped"] += 1 if v["scrapped"] else 0
        v["wo_id"] = f"WO-TXG-{v['day'].strftime('%y%m%d')}-{v['line']}-{SHORT[v['sku']]}"
    for p in w.packs:
        key = ("OEM-FRE", "P1", p["day"], p["sku"])
        wo = wos.setdefault(key, {"started": 0, "completed": 0, "scrapped": 0})
        wo["started"] += 1
        wo["completed"] += 1 if p["built_at"] else 0
        wo["scrapped"] += 1 if p["scrapped"] else 0
        p["wo_id"] = f"WO-FRE-{p['day'].strftime('%y%m%d')}-{'S' if p['sku'] == 'PK-STD' else 'L'}"
    rows = []
    for (site, line, day, sku), c in wos.items():
        if site == "CM-TXG":
            plan, n = w.cm_output_plan[(line, day)]
            wo_id = f"WO-TXG-{day.strftime('%y%m%d')}-{line}-{SHORT[sku]}"
        else:
            plan, n = w.pack_output_plan[day]
            wo_id = f"WO-FRE-{day.strftime('%y%m%d')}-{'S' if sku == 'PK-STD' else 'L'}"
        planned = max(1, int(round(plan * c["started"] / max(1, n))))
        done = c["completed"] + c["scrapped"]
        if done >= c["started"]:
            status = "CLOSED" if day < add_days(w.as_of, -2) else "COMPLETE"
        else:
            status = "IN_PROCESS"
        rows.append((wo_id, site, line, sku, planned, c["started"], c["completed"], c["scrapped"],
                     day.isoformat(), status, "CM_MES" if site == "CM-TXG" else "OEM_MES"))
    # released but not started: the next frozen days
    for day in [d for d in w.cm_days if d > w.as_of][:5]:
        for line in ("L1", "L2"):
            plan = w.cm_plan(line, day)
            for sku, q in split_det(plan, COLOR_W).items():
                if q:
                    rows.append((f"WO-TXG-{day.strftime('%y%m%d')}-{line}-{SHORT[sku]}", "CM-TXG", line, sku, q,
                                 0, 0, 0, day.isoformat(), "RELEASED", "CM_MES"))
    for day in [d for d in w.us_days if d > w.as_of][:5]:
        for sku, q in split_det(w.pack_plan(day), PACK_W).items():
            if q:
                rows.append((f"WO-FRE-{day.strftime('%y%m%d')}-{'S' if sku == 'PK-STD' else 'L'}", "OEM-FRE", "P1",
                             sku, q, 0, 0, 0, day.isoformat(), "RELEASED", "OEM_MES"))
    w.work_orders = rows


# ------------------------------------------------------------------ MPS and CM commit

def build_plans(w):
    rows = []
    ver_cm, ver_mps = f"CM-{week_code(w.as_of)}", f"MPS-{week_code(w.as_of)}"
    for day in w.cm_days:
        if day < w.cm_sop:
            continue
        for line in ("L1", "L2"):
            plan = w.cm_plan(line, day)
            for sku, q in split_det(plan, COLOR_W).items():
                if q:
                    rows.append(("CM-TXG", line, sku, day.isoformat(), q, "CM_COMMIT", ver_cm))
    for day in w.us_days:
        if day < w.pack_sop:
            continue
        for sku, q in split_det(w.pack_plan(day), PACK_W).items():
            if q:
                rows.append(("OEM-FRE", "P1", sku, day.isoformat(), q, "OEM_MPS", ver_mps))
    w.build_plan_rows = rows


# ------------------------------------------------------------------ S&OP demand forecast

def demand_forecast(w):
    weekly = {}
    for o in w.orders:
        if o["channel"] != "D2C" or o["cancelled"]:
            continue
        wk = week_start(o["ordered_at"].astimezone(PT).date())
        weekly[wk] = weekly.get(wk, 0) + 1
    this_week = week_start(w.as_of)
    recent = [weekly.get(add_days(this_week, -7 * k), 0) for k in range(1, 5)]
    base = sum(recent) / 4.0
    rows = []
    for version, published, bias in (("S&OP-2026-08", add_days(w.as_of, -40), 0.94),
                                      ("S&OP-2026-09", add_days(w.as_of, -12), 1.0)):
        for k in range(-8, 27):
            wk = add_days(this_week, 7 * k)
            if k < 0:
                total = weekly.get(wk, base) * (1.04 if version.endswith("08") else 1.0)
            else:
                total = base * (1.012 ** k) * bias
            for kit, (veh, pk) in KITS.items():
                share = COLOR_W[veh] * (0.56 if pk == "PK-STD" else 0.44)
                rows.append((version, kit, wk.isoformat(), round(total * share, 1), published.isoformat()))
            for pk, share in (("PK-LRG", 0.6), ("PK-STD", 0.4)):
                rows.append((version, pk, wk.isoformat(), round(total * 0.08 * share, 1), published.isoformat()))
    w.forecast_rows = rows


# ------------------------------------------------------------------ purchasing (ERP)

def _future_daily_need(w):
    """Projected daily consumption per purchased item from the future MPS / CM commit."""
    need = {}

    def add(item, day, q):
        need.setdefault(item, {})
        need[item][day] = need[item].get(day, 0) + q

    end = add_days(w.as_of, 100)
    for day in w.us_days:
        if w.as_of < day <= end:
            split = split_det(w.pack_plan(day), PACK_W)
            for sku, q in split.items():
                add("CEL-21700", day, q * (40 if sku == "PK-STD" else 60))
                add("BMS-B", day, q)
                add("ENC-STD" if sku == "PK-STD" else "ENC-LRG", day, q)
                add("BUS-S" if sku == "PK-STD" else "BUS-L", day, q)
                add("HRN-PK", day, q)
                add("GSK-B", day, q)
    for day in w.cm_days:
        if w.as_of < day <= end:
            q = w.cm_plan_total(day)
            for item in ("DU-C", "PU-1", "HMI-1"):
                add(item, day, q)
    for k in range(1, 101):
        day = add_days(w.as_of, k)
        if day.weekday() == 6:
            continue
        add("CHG-1", day, round(w.cm_plan_total(add_days(day, -25)) * (1.0 if day.weekday() < 5 else 0.5)))
    for c in w.containers:                     # vehicles already on the water or at the CM dock also get kitted
        if c["received_at"] > w.now:
            land = max(add_days(w.as_of, 1), add_days(max(c["ata_truth"], c["eta_planned"]).astimezone(PT).date(), 4))
            add("CHG-1", land, len(c["units"]))
    add("CHG-1", add_days(w.as_of, 22), len(w.cm_dock))
    return need


def _on_hand_now(w):
    oh = {}
    for item, dlvs in w.deliveries.items():
        if item in ("DU-B", "DU-C", "PU-1", "HMI-1", "BMS-A", "BMS-B"):
            arrived = sum(d["qty"] for d in dlvs if d["arrival"] <= w.now)
            used = len([e for e in w.consume[item] if e["t"] <= w.now])
            oh[item] = arrived - used
    for lot in w.lots.values():
        if lot["origin"] == "OEM_RECEIPT" and lot["iqc"] != "REJECTED":
            oh[lot["item"]] = oh.get(lot["item"], 0) + lot["usable"] - lot["used"]
    oh["CHG-1"] = getattr(w, "charger_on_hand", 0)
    return oh


FUTURE_POLICY = {  # item: (weekday, interval days, cover days, safety stock, multiple, moq, tz)
    "CEL-21700": (1, 7, 0, 15000, 5000, 20000, PT), "BMS-B": (1, 7, 2, 260, 250, 500, PT),
    "ENC-STD": (0, 7, 1, 200, 200, 400, PT), "ENC-LRG": (0, 7, 1, 160, 200, 400, PT),
    "BUS-S": (3, 7, 1, 250, 250, 500, PT), "BUS-L": (3, 7, 1, 200, 250, 500, PT),
    "HRN-PK": (2, 14, 1, 300, 300, 600, PT), "GSK-B": (4, 14, 1, 400, 500, 1000, PT),
    "DU-C": (2, 7, 1, 180, 50, 300, TPE), "PU-1": (2, 7, 1, 180, 50, 300, TPE),
    "HMI-1": (2, 7, 1, 200, 50, 500, TPE), "CHG-1": (0, 7, 7, 600, 500, 1000, PT),
}


def _future_deliveries(w, need, oh):
    out = {}
    for item, (weekday, interval, cover, ss, mult, moq, tz) in FUTURE_POLICY.items():
        daily = need.get(item, {})
        on_hand = oh.get(item, 0)
        d = add_days(w.as_of, 1)
        d += dt.timedelta(days=(weekday - d.weekday()) % 7)
        horizon = add_days(w.as_of, 84)
        consumed_upto = w.as_of
        dlvs = []
        while d <= horizon:
            used = sum(q for day, q in daily.items() if consumed_upto < day < d)
            on_hand -= used
            consumed_upto = add_days(d, -1)
            upcoming = sum(q for day, q in daily.items() if d <= day < add_days(d, interval + cover))
            q = upcoming + ss - on_hand
            if q > 0:
                q = int(-(-max(q, moq) // mult) * mult)
                dlvs.append({"need": d, "qty": q})
                on_hand += q
            d = add_days(d, interval)
        out[item] = dlvs
    return out


def purchasing(w):
    rng = w.rng
    supplier_terms = {"KES": "Net 60", "PNC": "Net 45", "SMT": "Net 45", "CSP": "Net 30", "NRT": "Net 45",
                      "BAY": "Net 30", "TNM": "Net 60", "HDS": "Net 60", "VPC": "Net 45"}
    lines = []            # every PO line, history and open
    for item, dlvs in w.deliveries.items():
        for dlv in dlvs:
            if dlv["arrival"] > w.now:
                continue
            tz = TPE if SITE_OF[item] == "CM-TXG" else PT
            arrive_day = dlv["arrival"].astimezone(tz).date()
            slip = rng.choice([0] * 22 + [2, 3, 4, 6]) if not dlv.get("expedited") else 0
            lines.append({"item": item, "supplier": dlv["supplier"], "site": SITE_OF[item], "qty": dlv["qty"],
                          "need": add_days(arrive_day, -slip), "arrival": dlv["arrival"], "slip": slip,
                          "dlv": dlv, "received": True, "expedited": dlv.get("expedited", False)})
    need = _future_daily_need(w)
    oh = _on_hand_now(w)
    w.on_hand_now = oh
    future = _future_deliveries(w, need, oh)
    w.future_need = need
    for item, dlvs in future.items():
        supplier = ITEMS[item][15]
        for f in dlvs:
            lines.append({"item": item, "supplier": supplier, "site": SITE_OF[item], "qty": f["qty"],
                          "need": f["need"], "arrival": None, "slip": 0, "dlv": None, "received": False})

    # group into monthly POs per supplier + ship-to, created one lead time ahead of the first need
    groups = {}
    for ln in lines:
        key = (ln["supplier"], ln["site"], ln["need"].strftime("%Y-%m"))
        groups.setdefault(key, []).append(ln)
    pos = []
    for key in sorted(groups, key=lambda k: min(l["need"] for l in groups[k])):
        supplier, site, _ = key
        grp = sorted(groups[key], key=lambda l: (l["need"], l["item"]))
        lt = max(ITEMS[l["item"]][8] or 14 for l in grp)
        created_day = add_days(min(l["need"] for l in grp), -(lt + rng.randint(4, 12)))
        created_day = min(created_day, add_days(w.as_of, -1))
        created = at(created_day, rng.randint(8, 17), rng.randint(0, 59), PT).astimezone(UTC)
        pos.append({"supplier": supplier, "site": site, "created": created, "lines": grp})
    pos.sort(key=lambda p: p["created"])
    po_rows, line_rows = [], []
    for i, po in enumerate(pos):
        po_id = f"45{i + 101:05d}"
        po["po_id"] = po_id
        open_ = any(not l["received"] for l in po["lines"])
        po_rows.append((po_id, po["supplier"], po["site"], po["created"], "J. Park" if po["site"] == "CM-TXG" else
                        ("M. Alvarez" if po["supplier"] in ("KES", "PNC", "VPC") else "R. Chen"),
                        "FCA" if po["supplier"] in ("KES", "PNC", "HDS", "VPC") else "DAP", "USD",
                        "OPEN" if open_ else "CLOSED"))
        for n, ln in enumerate(po["lines"], start=1):
            ln["po_id"], ln["line_no"], ln["po_created"] = po_id, n, po["created"]
            if ln["dlv"] is not None:
                ln["dlv"]["po_ref"] = (po_id, n)
    w.po_rows = po_rows
    w.po_lines = [l for po in pos for l in po["lines"]]
    _promises(w)


def _promises(w):
    """Supplier acknowledgements and promise-date history per line, by channel."""
    rng = w.rng
    cell_step = w.cell_price_step
    mismatch_budget = 3
    story_bms = []
    for ln in w.po_lines:
        item, sup = ln["item"], ln["supplier"]
        created_day = ln["po_created"].astimezone(PT).date()
        # price: the effective contract price on the PO date, except three cell lines a buyer copied
        # from an older PO after the price step
        price = _price(w, item, sup, created_day)
        if item == "CEL-21700" and created_day >= cell_step and mismatch_budget > 0:
            price, mismatch_budget = 3.10, mismatch_budget - 1
            ln["price_mismatch"] = True
        ln["unit_price"] = price
        ch = CHANNEL.get(sup, "EMAIL")
        lat = {"EDI855": (1, 6), "PORTAL": (4, 30), "EMAIL": (16, 110), "EXCEL": (24, 160)}[ch]
        confirmed = ln["po_created"] + dt.timedelta(hours=rng.uniform(*lat))
        ln["history"] = []
        if confirmed > w.now:
            ln["confirm"] = None
            continue
        first_promise = ln["need"]
        ln["history"].append({"t": confirmed, "promise": first_promise, "qty": ln["qty"], "channel": ch})
        if ln["slip"]:
            upd = at(add_days(ln["need"], -rng.randint(4, 9)), 10, 0, PT).astimezone(UTC)
            upd = max(upd, confirmed + dt.timedelta(hours=6))
            if upd <= w.now:
                ln["history"].append({"t": upd, "promise": add_days(ln["need"], ln["slip"]), "qty": ln["qty"],
                                      "channel": ch, "note": "Supplier capacity"})
        if item == "BMS-B" and not ln["received"]:
            story_bms.append(ln)
        ln["confirm"] = confirmed
    # the BMS-B story: Pinecrest's weekly open-order report pushes the next lines out
    story_bms.sort(key=lambda l: l["need"])
    report_day = add_days(w.as_of, -((w.as_of.weekday() - 0) % 7) or -7)      # last Monday
    report_t = at(report_day, 9, 20, TPE).astimezone(UTC)
    w.stories["pnc_report_t"] = report_t
    for k, ln in enumerate(story_bms[:3]):
        if not ln["history"]:
            continue
        if k == 0:
            slip = _bms_slip(w, ln)
            ln["history"].append({"t": report_t, "promise": add_days(ln["need"], slip), "qty": ln["qty"],
                                  "channel": "EXCEL", "note": "AFE IC allocation (Microvolt); partial recovery wk+2"})
            w.stories["bms_slipped_line"] = (ln["po_id"], ln["line_no"])
        elif k == 1:
            ln["history"].append({"t": report_t, "promise": add_days(ln["need"], 7), "qty": ln["qty"],
                                  "channel": "EXCEL", "note": "AFE IC allocation (Microvolt)"})
        else:
            ln["history"] = []          # not yet acknowledged
            ln["confirm"] = None
    # an HMI line whose confirmation email could not be parsed: still unconfirmed after 5 days
    hmi_open = sorted([l for l in w.po_lines if l["item"] == "HMI-1" and not l["received"] and l["history"]],
                      key=lambda l: l["need"])
    if hmi_open:
        ln = hmi_open[0]
        ln["history"], ln["confirm"], ln["unparsed_email"] = [], None, True
        w.stories["hmi_unconfirmed"] = (ln["po_id"], ln["line_no"])


def _bms_slip(w, ln):
    """Slip the first open BMS-B line far enough past the day stock runs out to open a real gap."""
    need = w.future_need.get("BMS-B", {})
    stock = w.on_hand_now.get("BMS-B", 0)
    a_left = min(w.on_hand_now.get("BMS-A", 0), 300 - w.stories.get("dev12_used", 0))
    for k in range(1, 60):
        day = add_days(w.as_of, k)
        use = need.get(day, 0)
        if day <= w.dev12_end and a_left > 0:
            take = min(a_left, use)
            a_left -= take
            use -= take
        stock -= use
        if stock < 0:
            target = add_workdays(day, 3, US_HOLIDAYS)
            w.stories["bms_stockout_day"] = day.isoformat()
            return max(5, (target - ln["need"]).days)
    return 12


def _price(w, item, supplier, day):
    best = None
    for row in w.conn.execute(
            "SELECT unit_price, min_qty, eff_from, eff_to FROM price WHERE item_id=? AND supplier_id=?",
            (item, supplier)).fetchall():
        if row["eff_from"] <= day.isoformat() and (row["eff_to"] is None or row["eff_to"] > day.isoformat()) \
                and row["min_qty"] == 0:
            best = row["unit_price"]
    return best if best is not None else ITEMS[item][12]


def cm_purchase_orders(w):
    """Monthly vehicle POs to the CM, received when loaded at Taichung (FCA)."""
    rng = w.rng
    loaded = {}
    for c in w.containers:
        t_load = next((e[1] for e in c["events"] if e[0] == "LOADED"), None)
        if t_load and t_load <= w.now:
            m = t_load.astimezone(TPE).strftime("%Y-%m")
            for sku in c["skus"][: len(c["units"])]:
                loaded[(m, sku)] = loaded.get((m, sku), 0) + 1
    months = sorted({d.strftime("%Y-%m") for d in w.cm_days if w.cm_sop <= d <= add_days(w.as_of, 60)})
    rows, lines = [], []
    for k, m in enumerate(months):
        po_id = f"47{k + 101:05d}"
        month_days = [d for d in w.cm_days if d.strftime("%Y-%m") == m and d >= w.cm_sop]
        first = month_days[0]
        created = at(add_days(first, -30), 10, 0, PT).astimezone(UTC)
        plan = {}
        for d in month_days:
            for sku, q in split_det(w.cm_plan_total(d), COLOR_W).items():
                plan[sku] = plan.get(sku, 0) + q
        done = month_days[-1] < add_days(w.as_of, -10)
        rows.append((po_id, "FAP", "CM-TXG", min(created, w.now), "J. Park", "FCA", "USD", "CLOSED" if done else "OPEN"))
        for n, sku in enumerate(VEHICLE_SKUS, start=1):
            qty = max(1, plan.get(sku, 0))
            rec = loaded.get((m, sku), 0)
            price = _price(w, sku, "FAP", min(created, w.now).astimezone(PT).date())
            conf = created + dt.timedelta(hours=rng.uniform(4, 30))
            lines.append({"po_id": po_id, "line_no": n, "item": sku, "supplier": "FAP", "site": "CM-TXG",
                          "qty": qty, "unit_price": price, "need": month_days[-1],
                          "received_qty": min(rec, qty) if done else rec, "received": done,
                          "confirm": conf if conf <= w.now else None, "po_created": created,
                          "history": ([{"t": conf, "promise": month_days[-1], "qty": qty, "channel": "PORTAL"}]
                                      if conf <= w.now else [])})
    w.po_rows += rows
    w.cm_po_lines = lines


# ------------------------------------------------------------------ forecast released to tiers 1-3

TIER_OFFSET_WEEKS = {1: 0, 2: 4, 3: 8}


def supplier_forecasts(w):
    rng = w.rng
    bom = w.conn.execute("SELECT parent_item_id, child_item_id, qty_per, bom_level, eff_from, eff_to FROM bom_line").fetchall()
    sup_of = {r["item_id"]: r["primary_supplier_id"] for r in w.conn.execute("SELECT item_id, primary_supplier_id FROM item")}
    tier_of = {r["supplier_id"]: r["tier"] for r in w.conn.execute("SELECT supplier_id, tier FROM supplier")}

    def children(parent, day):
        ds = day.isoformat()
        return [(r["child_item_id"], r["qty_per"]) for r in bom if r["parent_item_id"] == parent
                and r["eff_from"] <= ds and (r["eff_to"] is None or r["eff_to"] > ds)]

    this_week = week_start(w.as_of)
    releases, lines, commits = [], [], []
    for k in range(12, -1, -1):
        rel_week = add_days(this_week, -7 * k)
        rel_id = f"FR-{week_code(rel_week)}"
        released_at = at(rel_week, 9, 0, PT).astimezone(UTC)
        revision = 0.95 if k >= 6 else (1.03 if k >= 3 else 1.0)
        releases.append((rel_id, released_at, 26, f"S&OP {'2026-08' if k >= 5 else '2026-09'} + CM commit",
                         "Weekly release (EDI 830 to tier 1; portal to tiers 2-3)"))
        demand = {}
        for wk in range(26):
            ws = add_days(rel_week, 7 * wk)
            days = [add_days(ws, i) for i in range(7)]
            veh = sum(w.cm_plan_total(d) for d in days if is_workday(d, TW_HOLIDAYS)) * revision
            if ws > add_days(w.as_of, 120):
                veh = 62 * 5 * (1.01 ** wk) * revision
            packs = sum(w.pack_plan(d) for d in days if is_workday(d, US_HOLIDAYS)) * revision
            if ws > add_days(w.as_of, 120):
                packs = veh * 1.1
            for sku, sh in COLOR_W.items():
                demand[(sku, ws)] = demand.get((sku, ws), 0) + veh * sh
            for pk, sh in PACK_W.items():
                demand[(pk, ws)] = demand.get((pk, ws), 0) + packs * sh
            demand[("CHG-1", ws)] = demand.get(("CHG-1", ws), 0) + veh
        # explode level by level; purchased/sourced items become forecast lines for their supplier
        req = {}
        frontier = dict(demand)
        while frontier:
            nxt = {}
            for (item, ws), q in frontier.items():
                if item not in ("PK-STD", "PK-LRG"):
                    req[(item, ws)] = req.get((item, ws), 0) + q
                for child, per in children(item, ws):
                    nxt[(child, ws)] = nxt.get((child, ws), 0) + q * per
            frontier = nxt
        for (item, ws), q in req.items():
            sup = sup_of.get(item)
            if not sup or item in ("MTR-1", "CTL-B", "CTL-C", "BMS-A", "GSK-A", "DU-B"):
                continue
            tier = tier_of[sup]
            ship_week = add_days(ws, -7 * TIER_OFFSET_WEEKS[tier])
            if ship_week < rel_week:
                continue
            key = (rel_id, sup, item, ship_week.isoformat())
            lines.append([rel_id, sup, item, tier, ship_week.isoformat(), q])
    merged = {}
    for rel_id, sup, item, tier, ws, q in lines:
        key = (rel_id, sup, item, ws)
        if key in merged:
            merged[key][5] += q
        else:
            merged[key] = [rel_id, sup, item, tier, ws, q]
    lines = [tuple(v[:5]) + (round(v[5], 1 if ITEMS[v[2]][4] == "kg" else 0),) for v in merged.values()]
    rel_start = {r[0]: r[1] for r in releases}
    for rel_id, sup, item, tier, ws, q in lines:
        released_at = rel_start[rel_id]
        weeks_out = (dt.date.fromisoformat(ws) - released_at.astimezone(PT).date()).days // 7
        if sup in ("SLX", "TCW"):
            continue                                    # no visibility: they have not responded to any release
        age_weeks = (w.now - released_at).days // 7
        if rel_id == f"FR-{week_code(week_start(w.as_of))}" and sup in ("HDS", "SMT"):
            continue                                    # this week's release: not answered yet
        ratio = rng.uniform(0.97, 1.0)
        if sup == "MVS" and 2 <= age_weeks <= 8 and weeks_out <= 8:
            ratio = 0.70                                # AFE IC allocation: the early warning
        elif sup == "MVS" and age_weeks < 2 and weeks_out <= 8:
            ratio = 0.84
        if sup == "PNC" and age_weeks <= 4 and weeks_out <= 6:
            ratio = rng.uniform(0.72, 0.78)
        if sup == "KSM":
            ratio = 0.92
        responded = released_at + dt.timedelta(days=rng.uniform(1, 4))
        if responded > w.now:
            continue
        commits.append((rel_id, sup, item, ws, round(q * ratio, 1 if ITEMS[item][4] == "kg" else 0), responded))
    w.releases, w.release_lines, w.commits = releases, lines, commits


# ------------------------------------------------------------------ RFQs

def rfqs(w):
    a = w.as_of
    w.rfq_rows = [
        ("RFQ-0009", "GSK-B", "Silicone enclosure gasket (ECO-0036)", 60000, add_days(a, -70), add_days(a, -58),
         "AWARDED", "BAY", "Sole qualified source; price held for 12 months", "ECO-0036"),
        ("RFQ-0012", "BMS-B", "BMS rev B: second source", 42000, add_days(a, -21), add_days(a, -4),
         "EVALUATING", None, None, None),
        ("RFQ-0014", "ENC-STD", "Standard enclosure: alternate die caster after flatness reject", 36000,
         add_days(a, -10), add_days(a, 7), "OPEN", None, None, None),
    ]
    w.rfq_quotes = [
        ("RFQ-0009", "BAY", 2.35, 1000, 14, 0.0, 0.05, add_days(a, -61), add_days(a, 300), "Tooling owned by OEM"),
        ("RFQ-0012", "PNC", 41.50, 500, 42, 0.0, 0.35, add_days(a, -12), add_days(a, 60), "Incumbent; AFE allocation risk through Q4"),
        ("RFQ-0012", "LMB", 39.20, 1000, 56, 18000.0, 0.35, add_days(a, -6), add_days(a, 60),
         "Different AFE supplier (no allocation); PPAP 6 weeks"),
        ("RFQ-0012", "KBS", 40.10, 500, 49, 9500.0, 0.40, add_days(a, -4), add_days(a, 45), "Shares incumbent AFE; smaller line"),
        ("RFQ-0014", "SMT", 22.40, 400, 28, 0.0, 1.10, add_days(a, -6), add_days(a, 90), "Incumbent; new die insert by wk+3"),
        ("RFQ-0014", "PCX", 24.10, 300, 21, 42000.0, 0.45, add_days(a, -3), add_days(a, 90),
         "Stockton, 60 mi from Fremont; tool transfer 8 weeks"),
    ]


# ------------------------------------------------------------------ replenishment policy

def policies(w):
    now = w.now_s
    rows = []
    for item, (weekday, interval, cover, ss, mult, moq, tz) in FUTURE_POLICY.items():
        site = SITE_OF[item]
        lt = ITEMS[item][8] or 14
        policy = "CONSIGNMENT" if site == "CM-TXG" else ("REORDER_POINT" if item == "CHG-1" else "MRP")
        owner = "OEM"
        daily = sum(w.future_need.get(item, {}).values()) / 100.0
        rop = round(daily * lt + ss) if policy == "REORDER_POINT" else None
        mx = round(rop + daily * 30) if rop else (round(ss + daily * (interval + cover)) if policy == "CONSIGNMENT" else None)
        rows.append((item, site, policy, owner, lt, round(lt * 0.18, 1), interval, 0.98, ss, rop, mx,
                     "MOQ_MULTIPLE", now))
    for sku in VEHICLE_SKUS:
        rows.append((sku, "3PL-RNO", "DRP", "OEM", 24, 3.5, 1, 0.95, round(18 * COLOR_W[sku] * 4), None, None,
                     "LOT_FOR_LOT", now))
    for pk, sh in PACK_W.items():
        rows.append((pk, "3PL-RNO", "DRP", "OEM", 3, 0.6, 1, 0.97, round(60 * sh * 5), None, None, "LOT_FOR_LOT", now))
    rows.append(("CEL-21700", "SUP-KES", "VMI", "SUPPLIER", 18, 3.0, 7, 0.98, 30000, 45000, 90000, "FIXED", now))
    rows.append(("BMS-B", "SUP-PNC", "VMI", "SUPPLIER", 5, 1.0, 7, 0.95, 250, 400, 1200, "FIXED", now))
    for item in ("FRM-1", "WHL-F", "WHL-R", "TIR-1", "BRK-1", "HRN-VH"):
        rows.append((item, "CM-TXG", "MIN_MAX", "CM", 21, 5.0, 7, 0.95, 200, 400, 1400, "MOQ_MULTIPLE", now))
    w.policy_rows = rows


# ------------------------------------------------------------------ TMS rates

def freight(w):
    a = w.as_of
    w.rate_rows = [
        ("PLL", "TWTXG-USOAK", "PER_CONTAINER", 2850.0, add_days(a, -200), add_days(a, -20), "PLL-TP-2026"),
        ("PLL", "TWTXG-USOAK", "PER_CONTAINER", 3400.0, add_days(a, -20), None, "PLL-TP-2026 GRI Sep"),
        ("BDR", "USOAK-RNO", "PER_CONTAINER", 1450.0, add_days(a, -200), None, "BDR-2026"),
        ("SDG", "FRE-RNO", "PER_SHIPMENT", 1180.0, add_days(a, -200), None, "SDG-DG-2026"),
        ("CWF", "RNO-WEST", "PER_UNIT", 165.0, add_days(a, -200), None, "CWF-2026"),
        ("CWF", "RNO-FLEET", "PER_UNIT", 140.0, add_days(a, -200), None, "CWF-2026"),
        ("PPG", "RNO-MOUNTAIN", "PER_UNIT", 129.0, add_days(a, -200), None, "PPG-2026"),
        ("PPG", "RNO-CENTRAL", "PER_UNIT", 149.0, add_days(a, -200), None, "PPG-2026"),
        ("PPG", "RNO-EAST", "PER_UNIT", 169.0, add_days(a, -200), None, "PPG-2026"),
        ("SKA", "TPE-SFO", "PER_KG", 6.40, add_days(a, -200), None, "SKA-SPOT"),
        ("SKA", "HSZ-SFO", "PER_KG", 6.10, add_days(a, -200), None, "SKA-SPOT"),
    ]


# ------------------------------------------------------------------ downtime (OEE availability)

def downtime(w):
    rng = w.rng
    rows = []

    def add(site, line, station, start, minutes, cat, reason, src):
        rows.append((site, line, station, start, start + dt.timedelta(minutes=minutes), cat, reason, src))

    for (line, day), (plan, n) in w.cm_output_plan.items():
        base = at(day, 8, 0, TPE).astimezone(UTC)
        for k in range(3):                                          # color changeovers
            add("CM-TXG", line, f"TXG-{line}-S10", base + dt.timedelta(minutes=140 * (k + 1)),
                rng.randint(6, 11), "CHANGEOVER", "Color batch changeover", "CM_MES")
        if rng.random() < 0.18:
            st = rng.choice(["S20", "S40", "S50", "S60"])
            add("CM-TXG", line, f"TXG-{line}-{st}", base + dt.timedelta(minutes=rng.randint(60, 480)),
                rng.randint(15, 70), "EQUIPMENT", rng.choice(["Nutrunner fault", "Conveyor jam", "Scanner offline",
                                                              "Dyno calibration drift"]), "CM_MES")
        if line == "L1" and add_days(w.as_of, -58) <= day <= add_days(w.as_of, -56):
            add("CM-TXG", line, f"TXG-{line}-S20", base + dt.timedelta(minutes=150), 290, "MATERIAL",
                "Consigned drive units short (TNM shipment 3 days late)", "CM_MES")
        if line == "L2" and day == w.fw_spike_day:
            add("CM-TXG", line, f"TXG-{line}-S60", base + dt.timedelta(minutes=300), 130, "EQUIPMENT",
                "EOL tester flashing wrong image (NCR-0023)", "CM_MES")
        if day.day <= 7 and day.weekday() == 5:
            add("CM-TXG", line, None, base, 240, "PLANNED", "Monthly preventive maintenance", "CM_MES")
    for day, (plan, n) in w.pack_output_plan.items():
        base = at(day, 6, 0, PT).astimezone(UTC)
        add("OEM-FRE", "P1", "FRE-P1-P10", base + dt.timedelta(minutes=300), rng.randint(5, 9), "CHANGEOVER",
            "Standard/Large changeover", "OEM_MES")
        if rng.random() < 0.15:
            add("OEM-FRE", "P1", rng.choice(["FRE-P1-P20", "FRE-P1-P50"]),
                base + dt.timedelta(minutes=rng.randint(60, 500)), rng.randint(15, 60), "EQUIPMENT",
                rng.choice(["Laser welder recalibration", "Cycler channel fault", "Fixture sensor"]), "OEM_MES")
        if day == add_days(w.as_of, -72):
            add("OEM-FRE", "P1", "FRE-P1-P40", base + dt.timedelta(minutes=200), 300, "MATERIAL",
                "EPDM gasket shortage", "OEM_MES")
        if day in (add_days(w.as_of, -33), add_days(w.as_of, -32)):
            add("OEM-FRE", "P1", "FRE-P1-P40", base + dt.timedelta(minutes=90), 180, "QUALITY",
                "Enclosure lot rejected at IQC (flatness); waiting on expedited lot", "OEM_MES")
    w.downtime_rows = rows
