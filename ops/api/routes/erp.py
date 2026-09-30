"""ERP views: item master, multi-level BOM with effectivity and a cost roll-up, purchase
orders, goods receipts, accounts payable with three-way match, and the general ledger
that supplier chargebacks post into.
"""
from ...db import as_of, q, q1
from ...logic import sourcing as S
from ..router import HttpError, get

TS = S.TS


@get(r"^/api/erp/items$")
def items(req):
    c = req.conn
    today = as_of(c)
    rows = q(c, """SELECT i.*, s.name AS supplier_name,
                     (SELECT COUNT(*) FROM supplier_item si WHERE si.item_id = i.item_id) AS avl,
                     (SELECT COUNT(DISTINCT parent_item_id) FROM bom_line b WHERE b.child_item_id = i.item_id) AS used_in,
                     (SELECT COUNT(*) FROM bom_line b WHERE b.parent_item_id = i.item_id) AS children,
                     (SELECT COALESCE(SUM(pl.qty - pl.received_qty), 0) FROM po_line pl WHERE pl.item_id = i.item_id
                        AND pl.status = 'OPEN') AS open_po_qty
                   FROM item i LEFT JOIN supplier s ON s.supplier_id = i.primary_supplier_id
                   ORDER BY CASE i.kind WHEN 'KIT' THEN 0 WHEN 'VEHICLE' THEN 1 WHEN 'PACK' THEN 2 WHEN 'MODULE' THEN 3
                            WHEN 'COMPONENT' THEN 4 ELSE 5 END, i.item_id""")
    for r in rows:
        p = S.price_on(c, r["item_id"], r["primary_supplier_id"], today) if r["primary_supplier_id"] else None
        r["contract_price"] = p["unit_price"] if p else None
        r["contract_ref"] = p["source_ref"] if p else None
    kinds = {}
    for r in rows:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    return {"rows": rows, "kinds": kinds, "as_of": today}


@get(r"^/api/erp/bom$")
def bom(req):
    c = req.conn
    item = req.arg("item") or "LV1-SLATE-L"
    day = req.arg("date") or as_of(c)
    if not q1(c, "SELECT 1 FROM item WHERE item_id = ?", (item,)):
        raise HttpError(404, f"item {item} not found")
    cur = S.costed_bom(c, item, day)
    out = {"current": cur}
    other = req.arg("compare")
    if other:
        cmp_ = S.costed_bom(c, item, other)
        before = {b["item_id"]: b["amount"] for b in cmp_["breakdown"]}
        after = {b["item_id"]: b["amount"] for b in cur["breakdown"]}
        labels = {b["item_id"]: b["label"] for b in cmp_["breakdown"] + cur["breakdown"]}
        eff = q(c, "SELECT eco_id, title, effective_date, old_item_id, new_item_id, cost_delta FROM eco"
                   " WHERE effective_date > ? AND effective_date <= ?", (min(day, other), max(day, other)))
        deltas = []
        for k in sorted(set(before) | set(after)):
            d = round(after.get(k, 0) - before.get(k, 0), 2)
            if abs(d) > 0.004:
                deltas.append({"item_id": k, "label": labels[k], "before": before.get(k), "after": after.get(k), "delta": d})
        out["compare"] = {"date": other, "unit_cost": cmp_["unit_cost"], "delta": round(cur["unit_cost"] - cmp_["unit_cost"], 2),
                          "lines": deltas, "ecos": eff}
    out["picker"] = q(c, "SELECT item_id, name, kind FROM item WHERE item_id IN (SELECT parent_item_id FROM bom_line)"
                         " ORDER BY CASE kind WHEN 'KIT' THEN 0 WHEN 'VEHICLE' THEN 1 WHEN 'PACK' THEN 2 ELSE 3 END, item_id")
    return out


@get(r"^/api/erp/where-used$")
def where_used(req):
    c = req.conn
    item = req.arg("item")
    if not item:
        raise HttpError(400, "item is required")
    day = req.arg("date") or as_of(c)
    return {"item": q1(c, "SELECT item_id, name, kind, uom FROM item WHERE item_id = ?", (item,)), "date": day,
            "rows": S.where_used(c, item, day)}


@get(r"^/api/erp/pos$")
def pos(req):
    c = req.conn
    rows = q(c, f"""SELECT po.po_id, po.supplier_id, s.name AS supplier_name, po.ship_to_site_id, st.name AS ship_to,
                          {TS.format('po.created_at')} AS created_at, po.buyer, po.incoterm, po.currency, po.status,
                          COUNT(pl.line_no) AS lines, SUM(pl.qty * pl.unit_price) AS value,
                          SUM((pl.qty - pl.received_qty) * pl.unit_price) AS open_value,
                          SUM(pl.received_qty) * 1.0 / SUM(pl.qty) AS received_pct,
                          SUM(pl.confirm_status = 'UNCONFIRMED' AND pl.status = 'OPEN') AS unconfirmed,
                          MIN(pl.need_date) AS first_need, MAX(pl.need_date) AS last_need,
                          GROUP_CONCAT(DISTINCT pl.item_id) AS items
                   FROM purchase_order po JOIN supplier s USING (supplier_id) JOIN site st ON st.site_id = po.ship_to_site_id
                   JOIN po_line pl USING (po_id) GROUP BY po.po_id ORDER BY po.created_at DESC""")
    return {"rows": rows}


@get(r"^/api/erp/receipts$")
def receipts(req):
    c = req.conn
    rows = q(c, f"""SELECT g.receipt_id, g.po_id, g.line_no, {TS.format('g.received_at')} AS received_at, g.qty, g.lot_id, g.asn_no,
                          g.site_id, pl.item_id, i.name AS item_name, po.supplier_id, s.name AS supplier_name, l.iqc_status,
                          pl.unit_price, ROUND(g.qty * pl.unit_price, 2) AS value
                   FROM goods_receipt g JOIN po_line pl USING (po_id, line_no) JOIN purchase_order po USING (po_id)
                   JOIN supplier s ON s.supplier_id = po.supplier_id JOIN item i ON i.item_id = pl.item_id
                   LEFT JOIN lot l ON l.lot_id = g.lot_id
                   ORDER BY g.received_at DESC""")
    return {"rows": rows}


@get(r"^/api/erp/ap$")
def ap(req):
    c = req.conn
    today = as_of(c)
    rows = q(c, """SELECT v.*, s.name AS supplier_name, pl.item_id, pl.qty AS po_qty, pl.unit_price AS po_price,
                     (SELECT COALESCE(SUM(g.qty), 0) FROM goods_receipt g WHERE g.po_id = v.po_id AND g.line_no = v.line_no) AS received,
                     (SELECT COALESCE(SUM(g.qty), 0) FROM goods_receipt g LEFT JOIN lot l ON l.lot_id = g.lot_id
                       WHERE g.po_id = v.po_id AND g.line_no = v.line_no AND COALESCE(l.iqc_status, '') != 'REJECTED') AS accepted,
                     (SELECT GROUP_CONCAT(g.lot_id) FROM goods_receipt g JOIN lot l ON l.lot_id = g.lot_id
                       WHERE g.po_id = v.po_id AND g.line_no = v.line_no AND l.iqc_status = 'REJECTED') AS rejected_lots
                   FROM supplier_invoice v JOIN supplier s USING (supplier_id) JOIN po_line pl USING (po_id, line_no)
                   ORDER BY v.invoice_date DESC""")
    for r in rows:
        checks = []
        checks.append({"check": "Price = PO", "ok": abs(r["unit_price"] - r["po_price"]) < 1e-9,
                       "detail": f"invoice ${r['unit_price']:,.2f} vs PO ${r['po_price']:,.2f}"})
        checks.append({"check": "Qty ≤ received", "ok": r["qty"] <= r["received"] + 1e-9,
                       "detail": f"invoiced {r['qty']:,.0f} vs received {r['received']:,.0f}"})
        checks.append({"check": "Qty ≤ accepted at IQC", "ok": r["qty"] <= r["accepted"] + 1e-9,
                       "detail": (f"{r['received'] - r['accepted']:,.0f} rejected at IQC (lot {r['rejected_lots']}, returned to vendor)"
                                  if r["rejected_lots"] else "no IQC rejects")})
        r["checks"] = checks
        r["days_to_due"] = (S.to_date(r["due_date"]) - S.to_date(today)).days
    buckets = {"PAID": 0.0, "SCHEDULED": 0.0, "OPEN": 0.0, "BLOCKED": 0.0}
    aging = {"overdue": 0.0, "0-15": 0.0, "16-30": 0.0, "31-60": 0.0, "60+": 0.0}
    for r in rows:
        buckets[r["pay_status"]] = buckets.get(r["pay_status"], 0) + r["amount_usd"]
        if r["pay_status"] in ("OPEN", "SCHEDULED", "BLOCKED"):
            d = r["days_to_due"]
            key = "overdue" if d < 0 else "0-15" if d <= 15 else "16-30" if d <= 30 else "31-60" if d <= 60 else "60+"
            aging[key] += r["amount_usd"]
    return {"rows": rows, "by_status": buckets, "aging": aging, "as_of": today,
            "match_counts": {m: sum(1 for r in rows if r["match_status"] == m) for m in
                             ("MATCHED", "PRICE_VARIANCE", "QTY_VARIANCE", "ON_HOLD")}}


@get(r"^/api/erp/gl$")
def gl(req):
    c = req.conn
    entries = q(c, f"""SELECT je.je_id, je.doc_type, {TS.format('je.posted_at')} AS posted_at, je.supplier_id, s.name AS supplier_name,
                             je.memo, je.source_ref, je.status, cb.chargeback_id, cb.basis, cb.debit_memo_no,
                             SUM(jl.debit_usd) AS debits, SUM(jl.credit_usd) AS credits
                      FROM erp_journal_entry je LEFT JOIN supplier s USING (supplier_id)
                      LEFT JOIN chargeback cb ON cb.je_id = je.je_id
                      JOIN erp_journal_line jl USING (je_id) GROUP BY je.je_id ORDER BY je.posted_at DESC""")
    lines = q(c, """SELECT jl.*, a.name AS account_name, a.kind FROM erp_journal_line jl JOIN gl_account a USING (gl_account)
                    ORDER BY jl.je_id, jl.line_no""")
    by_je = {}
    for l in lines:
        by_je.setdefault(l["je_id"], []).append(l)
    for e in entries:
        e["lines"] = by_je.get(e["je_id"], [])
        e["balanced"] = abs((e["debits"] or 0) - (e["credits"] or 0)) < 0.005
    tb = q(c, """SELECT a.gl_account, a.name, a.kind, COALESCE(SUM(jl.debit_usd), 0) AS debits, COALESCE(SUM(jl.credit_usd), 0) AS credits
                 FROM gl_account a LEFT JOIN erp_journal_line jl USING (gl_account) GROUP BY a.gl_account ORDER BY a.gl_account""")
    pending = q(c, f"""SELECT chargeback_id, supplier_id, title, amount_usd, status, {TS.format('created_at')} AS created_at
                       FROM chargeback WHERE status IN ('DRAFT', 'SENT', 'ACCEPTED', 'DISPUTED') ORDER BY created_at""")
    return {"entries": entries, "trial_balance": tb, "pending_recoveries": pending,
            "balanced": all(e["balanced"] for e in entries)}


@get(r"^/api/erp/fx$")
def fx(req):
    """Daily FX the ERP uses to book CM reports (TWD) and supplier statements (KRW, MXN, JPY)."""
    c = req.conn
    today = as_of(c)
    rows = q(c, "SELECT currency, rate_date, usd_per_unit FROM fx_rate WHERE rate_date <= ?"
                " AND julianday(?) - julianday(rate_date) <= 90 ORDER BY currency, rate_date", (today, today))
    out = {}
    for r in rows:
        out.setdefault(r["currency"], []).append({"date": r["rate_date"], "per_usd": round(1.0 / r["usd_per_unit"], 4)})
    return {"as_of": today, "series": [{"currency": k, "points": v, "latest": v[-1]["per_usd"],
                                        "change_30d": round(v[-1]["per_usd"] / v[-31]["per_usd"] - 1, 4) if len(v) > 31 else None}
                                       for k, v in sorted(out.items())]}
