"""Sourcing, costing and logistics calculations behind the Supplier Loop, ERP and
Shipments pages: price on a date, RFQ total cost of ownership, multi-level BOM
with effectivity and a cost roll-up that follows who pays for what, and the
supplier scorecard computed from receipts, promises, IQC and chargebacks.
"""
import datetime as dt
import json
import statistics

from ..db import q, q1

TS = "strftime('%Y-%m-%dT%H:%M:%SZ', {})"          # normalize any stored timestamp to ISO-8601 Z


def z(v):
    """Python-side twin of TS: '2026-09-26T04:28:36.47+00:00' -> '2026-09-26T04:28:36Z'."""
    if not v:
        return v
    s = str(v)
    if len(s) == 10:
        return s
    try:
        t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_date(v):
    return dt.date.fromisoformat(str(v)[:10]) if v else None


# ------------------------------------------------------------------ prices

def price_on(conn, item_id, supplier_id, day, qty=0):
    """Effective contract price for an item/supplier on a date, honoring price breaks."""
    return q1(conn, "SELECT price_id, unit_price, min_qty, eff_from, eff_to, basis, source_ref FROM price"
                    " WHERE item_id=? AND supplier_id=? AND eff_from<=? AND (eff_to IS NULL OR eff_to>?) AND min_qty<=?"
                    " ORDER BY min_qty DESC, eff_from DESC LIMIT 1", (item_id, supplier_id, day, day, qty or 0))


def price_exposure(conn):
    """Open PO lines whose price differs from the contract price in effect on the delivery date."""
    rows = q(conn, "SELECT pl.po_id, pl.line_no, pl.item_id, po.supplier_id, pl.qty, pl.received_qty, pl.unit_price,"
                   " COALESCE(pl.promise_date, pl.need_date) AS deliver, substr(po.created_at, 1, 10) AS po_date"
                   " FROM po_line pl JOIN purchase_order po USING (po_id) WHERE pl.status = 'OPEN'")
    out = []
    for r in rows:
        eff = price_on(conn, r["item_id"], r["supplier_id"], r["deliver"], r["qty"])
        at_po = price_on(conn, r["item_id"], r["supplier_id"], r["po_date"], r["qty"])
        if not eff:
            continue
        open_qty = r["qty"] - (r["received_qty"] or 0)
        diff = round(r["unit_price"] - eff["unit_price"], 4)
        if abs(diff) < 1e-9:
            continue
        out.append({**r, "open_qty": open_qty, "effective_price": eff["unit_price"], "effective_ref": eff["source_ref"],
                    "effective_from": eff["eff_from"], "price_at_po_date": at_po["unit_price"] if at_po else None,
                    "diff": diff, "exposure_usd": round(diff * open_qty, 2),
                    "cause": ("PO issued at a superseded price" if at_po and abs(r["unit_price"] - at_po["unit_price"]) > 1e-9
                              else "Contract price steps before delivery; PO not re-priced")})
    out.sort(key=lambda r: -abs(r["exposure_usd"]))
    return out


# ------------------------------------------------------------------ RFQ total cost of ownership

def tco(quote, annual_qty):
    unit = quote["unit_price"] + (quote.get("freight_per_unit") or 0)
    total = unit * annual_qty + (quote.get("tooling_usd") or 0)
    return {"landed_unit": round(unit, 4), "annual_spend": round(unit * annual_qty, 2), "tooling": quote.get("tooling_usd") or 0,
            "tco": round(total, 2), "tco_unit": round(total / annual_qty, 4) if annual_qty else None}


def rfq_analysis(conn):
    rfqs = q(conn, "SELECT r.*, i.name AS item_name FROM rfq r JOIN item i USING (item_id) ORDER BY r.created_at DESC")
    incumbents = {r["item_id"]: r["supplier_id"] for r in q(conn, "SELECT item_id, supplier_id FROM supplier_item WHERE role='PRIMARY'")}
    cb = {r["supplier_id"]: r["usd"] for r in q(conn, "SELECT supplier_id, SUM(amount_usd) usd FROM chargeback GROUP BY 1")}
    out = []
    for r in rfqs:
        quotes = q(conn, "SELECT qt.*, s.name AS supplier_name, s.country FROM rfq_quote qt JOIN supplier s USING (supplier_id)"
                         " WHERE rfq_id=? ORDER BY unit_price", (r["rfq_id"],))
        inc = incumbents.get(r["item_id"])
        for qt in quotes:
            qt.update(tco(qt, r["annual_qty"]))
            qt["incumbent"] = qt["supplier_id"] == inc
            qt["quality_cost_usd"] = cb.get(qt["supplier_id"], 0.0)
        quotes.sort(key=lambda x: x["tco"])
        inc_q = next((x for x in quotes if x["incumbent"]), None)
        for qt in quotes:
            qt["vs_incumbent"] = round(qt["tco"] - inc_q["tco"], 2) if inc_q else None
            savings_unit = (inc_q["landed_unit"] - qt["landed_unit"]) if inc_q else 0
            qt["payback_months"] = (round(qt["tooling"] / (savings_unit * r["annual_qty"] / 12.0), 1)
                                    if qt["tooling"] and savings_unit > 0 else None)
        out.append({**r, "quotes": quotes, "incumbent": inc, "recommendation": _recommend(r, quotes, inc_q)})
    return out


def _recommend(r, quotes, inc):
    if r["status"] == "AWARDED":
        return {"action": f"Awarded to {r['awarded_supplier_id']}", "rationale": r["award_rationale"] or ""}
    if not quotes:
        return {"action": "Waiting for quotes", "rationale": ""}
    best = quotes[0]
    notes = " ".join((x.get("notes") or "") for x in quotes).lower()
    if inc and best["supplier_id"] != inc["supplier_id"]:
        risk_inc = (inc.get("notes") or "").lower()
        diversifies = "different" in (best.get("notes") or "").lower()
        split = "Dual-source: award 30% to {} now, 70% stays with {} until PPAP closes".format(best["supplier_id"], inc["supplier_id"])
        why = (f"{best['supplier_name']} has the lowest total cost of ownership ({_usd(best['tco'])}/yr, "
               f"{_usd(-best['vs_incumbent'])} below the incumbent) with tooling paid back in {best['payback_months']} months. ")
        if "allocation" in risk_inc and diversifies:
            why += ("It also sources its AFE IC elsewhere, which removes the single point of failure behind the current "
                    "Pinecrest slip. Longer lead time and PPAP argue for a split rather than a switch.")
        else:
            why += "Longer lead time argues for qualifying before moving volume."
        return {"action": split if diversifies else f"Award to {best['supplier_id']}", "rationale": why,
                "supplier_id": best["supplier_id"]}
    runner = quotes[1] if len(quotes) > 1 else None
    why = (f"The incumbent {best['supplier_name']} stays cheapest ({_usd(best['tco'])}/yr) even after adding its "
           f"{_usd(best['quality_cost_usd'])} of recovered quality cost. ")
    if runner:
        why += (f"{runner['supplier_name']} costs {_usd(runner['tco'] - best['tco'])} more per year"
                f"{' but ships in ' + str(runner['lead_time_days']) + ' days' if runner['lead_time_days'] < best['lead_time_days'] else ''}; "
                "qualify it as a backup while the incumbent's corrective action is open.")
    return {"action": f"Keep {best['supplier_id']}; qualify {runner['supplier_id'] if runner else 'a backup'}", "rationale": why,
            "supplier_id": best["supplier_id"]}


def _usd(v):
    return f"${v:,.0f}"


# ------------------------------------------------------------------ BOM with effectivity + cost roll-up

BOM_SQL = """
WITH RECURSIVE tree(lvl, parent, child, position, qty_per, ext_qty, bom_level, eco_id, eff_from, eff_to, sort_key) AS (
  SELECT 1, b.parent_item_id, b.child_item_id, b.position, b.qty_per, b.qty_per, b.bom_level, b.eco_id, b.eff_from, b.eff_to,
         printf('%s:%s', b.position, b.child_item_id)
  FROM bom_line b
  WHERE b.parent_item_id = :item AND b.eff_from <= :d AND (b.eff_to IS NULL OR b.eff_to > :d)
  UNION ALL
  SELECT t.lvl + 1, b.parent_item_id, b.child_item_id, b.position, b.qty_per, t.ext_qty * b.qty_per, b.bom_level, b.eco_id,
         b.eff_from, b.eff_to, t.sort_key || '/' || printf('%s:%s', b.position, b.child_item_id)
  FROM bom_line b JOIN tree t ON b.parent_item_id = t.child
  WHERE b.eff_from <= :d AND (b.eff_to IS NULL OR b.eff_to > :d) AND t.lvl < 8
)
SELECT t.*, i.name, i.kind, i.make_buy, i.uom, i.revision, i.primary_supplier_id, i.std_cost, i.lifecycle
FROM tree t JOIN item i ON i.item_id = t.child
ORDER BY t.sort_key
"""

WHERE_USED_SQL = """
WITH RECURSIVE up(lvl, parent, child, qty, bom_level) AS (
  SELECT 1, b.parent_item_id, b.child_item_id, b.qty_per, b.bom_level FROM bom_line b
  WHERE b.child_item_id = :item AND b.eff_from <= :d AND (b.eff_to IS NULL OR b.eff_to > :d)
  UNION ALL
  SELECT u.lvl + 1, b.parent_item_id, b.child_item_id, u.qty * b.qty_per, b.bom_level FROM bom_line b
  JOIN up u ON b.child_item_id = u.parent
  WHERE b.eff_from <= :d AND (b.eff_to IS NULL OR b.eff_to > :d) AND u.lvl < 8
)
SELECT u.lvl, u.parent, u.child, u.qty, u.bom_level, i.name, i.kind, i.make_buy
FROM up u JOIN item i ON i.item_id = u.parent ORDER BY u.lvl, u.parent
"""


def bom_tree(conn, item_id, day):
    return q(conn, BOM_SQL, {"item": item_id, "d": day})


def where_used(conn, item_id, day):
    return q(conn, WHERE_USED_SQL, {"item": item_id, "d": day})


class Coster:
    """Unit cost of an item on a date, following who pays: the OEM buys components at contract
    price, the CM's price covers the parts it sources, suppliers' prices cover their sub-tier."""

    def __init__(self, conn, day):
        self.conn, self.day, self.cache = conn, day, {}
        self.items = {r["item_id"]: r for r in q(conn, "SELECT * FROM item")}

    def cm_price(self, item_id):
        it = self.items[item_id]
        p = price_on(self.conn, item_id, it["primary_supplier_id"], self.day)
        return p["unit_price"] if p else (it["std_cost"] or 0.0)

    def children(self, item_id):
        return q(self.conn, "SELECT child_item_id, qty_per, bom_level FROM bom_line WHERE parent_item_id=? AND eff_from<=?"
                            " AND (eff_to IS NULL OR eff_to>?)", (item_id, self.day, self.day))

    def cost(self, item_id):
        if item_id in self.cache:
            return self.cache[item_id]
        it = self.items[item_id]
        mb = it["make_buy"]
        if mb in ("BUY_DIRECT", "BUY_CONSIGNED"):
            p = price_on(self.conn, item_id, it["primary_supplier_id"], self.day)
            res = (p["unit_price"], f"contract {p['source_ref']} ({it['primary_supplier_id']})") if p else \
                (it["std_cost"] or 0.0, "standard cost (no contract price)")
        elif mb == "CM_BUILT":
            p = price_on(self.conn, item_id, it["primary_supplier_id"], self.day)
            cm = p["unit_price"] if p else (it["std_cost"] or 0.0)
            consigned = sum(c["qty_per"] * self.cost(c["child_item_id"])[0] for c in self.children(item_id)
                            if c["bom_level"] == "OEM")
            res = (round(cm + consigned, 4), f"CM price {p['source_ref'] if p else 'std'} ${cm:,.2f} + consigned parts ${consigned:,.2f}")
        elif mb in ("OEM_BUILT", "KITTED"):
            total = sum(c["qty_per"] * self.cost(c["child_item_id"])[0] for c in self.children(item_id)
                        if c["bom_level"] == "OEM")
            res = (round(total, 4), "materials roll-up (labor and overhead not modeled)")
        elif mb == "CM_SOURCED":
            res = (it["std_cost"] or 0.0, "inside the CM price")
        else:
            res = (it["std_cost"] or 0.0, "inside the supplier's price")
        self.cache[item_id] = res
        return res


def costed_bom(conn, item_id, day):
    """Tree rows with unit and extended cost; `counted` marks lines that roll into the top-level cost,
    and `breakdown` lists the leaves (plus the CM's conversion price) that sum to the unit cost."""
    rows = bom_tree(conn, item_id, day)
    c = Coster(conn, day)
    top_cost, top_basis = c.cost(item_id)
    # a line counts toward the OEM's cost if every ancestor edge on its path is OEM-level and its parent is OEM-costed
    counted_path = {item_id: True}
    for r in rows:
        unit, basis = c.cost(r["child"])
        parent_counts = counted_path.get(r["parent"], False)
        parent_mb = c.items[r["parent"]]["make_buy"]
        counted = parent_counts and r["bom_level"] == "OEM" and parent_mb in ("KITTED", "OEM_BUILT", "CM_BUILT")
        counted_path[r["child"]] = counted and r["kind"] in ("VEHICLE", "PACK", "KIT")
        r.update(unit_cost=round(unit, 4), cost_basis=basis, ext_cost=round(unit * r["ext_qty"], 2), counted=counted,
                 rolls_up=counted and c.items[r["child"]]["make_buy"] not in ("KITTED", "OEM_BUILT", "CM_BUILT"))
    breakdown = []
    top = c.items[item_id]
    if top["make_buy"] == "CM_BUILT":
        breakdown.append({"label": "CM conversion price (incl. CM-sourced parts)", "item_id": item_id,
                          "amount": round(c.cm_price(item_id), 2)})
    for r in rows:
        if r["counted"] and c.items[r["child"]]["make_buy"] == "CM_BUILT":
            breakdown.append({"label": "CM conversion price (incl. CM-sourced parts)", "item_id": r["child"],
                              "amount": round(c.cm_price(r["child"]) * r["ext_qty"], 2)})
        elif r["rolls_up"]:
            breakdown.append({"label": r["name"], "item_id": r["child"], "amount": r["ext_cost"]})
    return {"item": c.items[item_id], "date": day, "unit_cost": round(top_cost, 2), "basis": top_basis, "rows": rows,
            "breakdown": breakdown, "breakdown_total": round(sum(b["amount"] for b in breakdown), 2)}


# ------------------------------------------------------------------ supplier scorecard

def scorecard(conn, as_of=None):
    """Trailing windows, as a scorecard should be: OTD on receipts in the last 90 days, confirmation
    speed and promise slips on PO lines created in the last 60 days."""
    as_of = as_of or q1(conn, "SELECT value FROM meta WHERE key='as_of'")["value"]
    d90 = (dt.date.fromisoformat(as_of) - dt.timedelta(days=90)).isoformat()
    d60 = (dt.date.fromisoformat(as_of) - dt.timedelta(days=60)).isoformat()
    lines = q(conn, """
        SELECT po.supplier_id, pl.po_id, pl.line_no, pl.need_date, pl.promise_date, pl.status,
               date(po.created_at) AS created,
               julianday(pl.confirmed_at) - julianday(po.created_at) AS conf_days,
               (SELECT h.promise_date FROM po_promise_history h WHERE h.po_id=pl.po_id AND h.line_no=pl.line_no
                ORDER BY julianday(h.recorded_at), h.id LIMIT 1) AS first_promise,
               (SELECT COUNT(DISTINCT h.promise_date) FROM po_promise_history h WHERE h.po_id=pl.po_id AND h.line_no=pl.line_no) AS n_promises,
               (SELECT MIN(date(g.received_at)) FROM goods_receipt g WHERE g.po_id=pl.po_id AND g.line_no=pl.line_no) AS received
        FROM po_line pl JOIN purchase_order po USING (po_id)""")
    iqc = {r["supplier_id"]: r for r in q(conn, "SELECT supplier_id, SUM(qty_inspected) n, SUM(qty_defective) bad,"
                                                " SUM(result='REJECT') rejects, COUNT(*) lots FROM quality_event"
                                                " WHERE kind='IQC' GROUP BY 1")}
    cbs = {r["supplier_id"]: r for r in q(conn, "SELECT supplier_id, SUM(amount_usd) usd, COUNT(*) n FROM chargeback GROUP BY 1")}
    claims = {r["supplier_id"]: r["n"] for r in q(conn, "SELECT supplier_id, COUNT(*) n FROM warranty_claim"
                                                        " WHERE supplier_id IS NOT NULL GROUP BY 1")}
    sups = q(conn, "SELECT supplier_id, name, tier, country, commodity, is_cm FROM supplier WHERE tier=1 ORDER BY supplier_id")
    out = []
    for s in sups:
        ls = [l for l in lines if l["supplier_id"] == s["supplier_id"]]
        if not ls and not s["is_cm"]:
            continue
        rec = [l for l in ls if l["received"] and l["received"] >= d90]
        otd_first = [l for l in rec if l["first_promise"]]
        on_first = sum(1 for l in otd_first if l["received"] <= l["first_promise"])
        on_need = sum(1 for l in rec if l["received"] <= l["need_date"])
        recent = [l for l in ls if l["created"] >= d60]
        conf = [l["conf_days"] * 24 for l in recent if l["conf_days"] is not None]
        slipped = sum(1 for l in recent if (l["n_promises"] or 0) > 1)
        confirmed = sum(1 for l in recent if l["n_promises"])
        late_open = [l for l in ls if l["status"] == "OPEN" and l["promise_date"] and l["promise_date"] > l["need_date"]]
        i = iqc.get(s["supplier_id"])
        ppm = round(1e6 * (i["bad"] or 0) / i["n"], 0) if i and i["n"] else None
        otd = on_first / len(otd_first) if otd_first else None
        med_h = statistics.median(conf) if conf else None
        slip_rate = slipped / confirmed if confirmed else None
        parts = []
        if otd is not None:
            parts.append((0.4, otd))
        if med_h is not None:
            parts.append((0.2, max(0.0, min(1.0, 1 - (med_h - 8) / 112))))
        if ppm is not None:
            parts.append((0.2, max(0.0, min(1.0, 1 - ppm / 5000))))
        if slip_rate is not None:
            parts.append((0.2, 1 - slip_rate))
        score = round(100 * sum(w * v for w, v in parts) / sum(w for w, _ in parts), 1) if parts else None
        out.append({**s, "po_lines": len(ls), "open_lines": sum(1 for l in ls if l["status"] == "OPEN"),
                    "received_lines": len(rec), "otd_first_promise": otd, "otd_need": on_need / len(rec) if rec else None,
                    "confirm_hours_median": round(med_h, 1) if med_h is not None else None,
                    "slip_rate": slip_rate, "slipped": slipped, "confirmed_recent": confirmed, "otd_n": len(otd_first),
                    "late_open_lines": len(late_open), "iqc_lots": i["lots"] if i else 0,
                    "iqc_rejects": i["rejects"] if i else 0, "iqc_ppm": ppm, "field_claims": claims.get(s["supplier_id"], 0),
                    "chargeback_usd": (cbs.get(s["supplier_id"]) or {}).get("usd") or 0.0, "score": score})
    out.sort(key=lambda r: (r["score"] is None, r["score"] or 0))
    return out


def recovery_terms(raw):
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None
