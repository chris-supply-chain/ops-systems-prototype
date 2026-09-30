"""Reconciliation: where two systems describe the same physical thing, put their
numbers side by side, explain the delta from the data, and name who owns what's left.

Every function is a pure read over the connection and returns the same shape:

    {id, title, question, a: {system, table, value}, b: {system, table, value},
     delta, status: MATCH | EXPLAINED | OPEN, explanation, owner, tables,
     columns: [{key, label, num}], rows: [...]}

MATCH means the sources agree. EXPLAINED means they disagree for a reason the data
itself proves (timing, a known receipt without an ASN). OPEN means someone has to
go and look.
"""

import datetime as dt
from zoneinfo import ZoneInfo

TAIPEI = "+8 hours"
PT = ZoneInfo("America/Los_Angeles")


def _md(day):
    d = dt.date.fromisoformat(str(day)[:10])
    return f"{d:%b} {d.day}"


def _pt(ts):
    t = dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(PT)
    return f"{t:%b} {t.day} {t:%H:%M} PT"


def _n(k, word, plural=None):
    return f"{k} {word if k == 1 else (plural or word + 's')}"


def _status(delta, explained=0):
    if not delta:
        return "MATCH"
    return "EXPLAINED" if explained == delta else "OPEN"


# ---------------------------------------------------------------------------- 1. CM Excel vs MES

def cm_output_vs_mes(conn, days=30):
    """The CM's emailed daily Excel (units built) against S80 passes in its own MES event stream."""
    rows = [dict(r) for r in conn.execute(f"""
        WITH rep AS (
          SELECT report_date AS day, line, SUM(planned) AS planned, SUM(actual) AS excel, MAX(source_ref) AS ref
          FROM cm_output_report GROUP BY report_date, line),
        mes AS (
          SELECT substr(datetime(se.event_ts, '{TAIPEI}'), 1, 10) AS day, u.line, COUNT(*) AS mes
          FROM station_event se
          JOIN station s ON s.station_id = se.station_id AND s.code = 'S80'
          JOIN unit u ON u.serial = se.serial
          WHERE se.result = 'PASS' AND se.source = 'CM_FEED'
          GROUP BY 1, 2)
        SELECT rep.day, rep.line, rep.planned, rep.excel, COALESCE(mes.mes, 0) AS mes,
               rep.excel - COALESCE(mes.mes, 0) AS delta, rep.ref
        FROM rep LEFT JOIN mes ON mes.day = rep.day AND mes.line = rep.line
        ORDER BY rep.day DESC, rep.line
        LIMIT ?""", (days * 2,))]
    lo = min((r["day"] for r in rows), default=None)
    missing = []
    if lo:
        missing = [dict(r) for r in conn.execute(f"""
            SELECT substr(datetime(se.event_ts, '{TAIPEI}'), 1, 10) AS day, u.line, COUNT(*) AS mes
            FROM station_event se JOIN station s ON s.station_id = se.station_id AND s.code = 'S80'
            JOIN unit u ON u.serial = se.serial
            WHERE se.result = 'PASS' AND se.source = 'CM_FEED'
              AND substr(datetime(se.event_ts, '{TAIPEI}'), 1, 10) >= ?
              AND NOT EXISTS (SELECT 1 FROM cm_output_report r
                              WHERE r.report_date = substr(datetime(se.event_ts, '{TAIPEI}'), 1, 10) AND r.line = u.line)
            GROUP BY 1, 2""", (lo,))]
    for m in missing:
        rows.append({"day": m["day"], "line": m["line"], "planned": None, "excel": None, "mes": m["mes"],
                     "delta": -m["mes"], "ref": None})
    rows.sort(key=lambda r: (r["day"], r["line"]), reverse=True)
    handled = [dict(r) for r in conn.execute("""
        SELECT raw_id, subject, ingest_status, ingest_note FROM raw_email
        WHERE classified_as = 'CM_DAILY_REPORT' AND ingest_status IN ('WARN', 'IGNORED') ORDER BY received_at DESC""")]
    a = sum(r["excel"] or 0 for r in rows)
    b = sum(r["mes"] or 0 for r in rows)
    bad = [r for r in rows if r["delta"]]
    for r in rows:
        r["status"] = "MATCH" if not r["delta"] else ("NO_REPORT" if r["excel"] is None else "OPEN")
    return {
        "id": "cm-output", "title": "CM daily Excel vs CM MES events",
        "question": "Did the CM build what its emailed report says it built?",
        "a": {"system": "CM daily Excel (email)", "table": "cm_output_report", "value": a, "unit": "vehicles"},
        "b": {"system": "CM MES · S80 passes", "table": "station_event", "value": b, "unit": "vehicles"},
        "delta": a - b, "status": _status(a - b),
        "explanation": (f"Every report day in the window ties to the MES event stream ({len(rows)} day-lines). "
                        if not bad else f"{len(bad)} day-lines disagree. ")
        + (f"The email pipeline absorbed {len(handled)} report problems on the way in without a human (listed below)."
           if handled else ""),
        "owner": "Data eng.", "tables": ["cm_output_report", "station_event", "raw_email", "raw_attachment"],
        "columns": [{"key": "day", "label": "Report day (Taipei)"}, {"key": "line", "label": "Line"},
                    {"key": "planned", "label": "Plan", "num": True}, {"key": "excel", "label": "Excel", "num": True},
                    {"key": "mes", "label": "MES", "num": True}, {"key": "delta", "label": "Δ", "num": True},
                    {"key": "status", "label": "Status"}, {"key": "ref", "label": "Source"}],
        "rows": rows, "handled": handled,
    }


# ---------------------------------------------------------------------------- 2. CM consigned stock vs our serials

def cm_stock_vs_units(conn):
    """OEM-owned consigned stock at the CM: the CM's count vs the serials we know are on its shelf."""
    day = conn.execute("SELECT MAX(report_date) d FROM cm_stock_report").fetchone()["d"]
    rows = []
    for r in conn.execute("""
        SELECT c.item_id, c.on_hand, c.qc_hold, c.source_ref,
               (SELECT COUNT(*) FROM unit u WHERE u.item_id = c.item_id AND u.status = 'COMPONENT'
                  AND u.location_site_id = 'CM-TXG') AS ours,
               (SELECT COALESCE(SUM(gr.qty), 0) FROM goods_receipt gr JOIN po_line pl
                  ON pl.po_id = gr.po_id AND pl.line_no = gr.line_no
                  WHERE pl.item_id = c.item_id AND gr.site_id = 'CM-TXG' AND gr.asn_no IS NULL) AS no_asn_received,
               (SELECT GROUP_CONCAT(gr.po_id || '-' || gr.line_no) FROM goods_receipt gr JOIN po_line pl
                  ON pl.po_id = gr.po_id AND pl.line_no = gr.line_no
                  WHERE pl.item_id = c.item_id AND gr.site_id = 'CM-TXG' AND gr.asn_no IS NULL) AS no_asn_refs,
               (SELECT COUNT(*) FROM unit u WHERE u.item_id = c.item_id AND u.origin = 'PROVISIONAL') AS provisional
        FROM cm_stock_report c WHERE c.report_date = ? ORDER BY c.item_id""", (day,)):
        r = dict(r)
        cm = r["on_hand"] + r["qc_hold"]
        delta = cm - r["ours"]
        unseen = int(r["no_asn_received"] - r["provisional"])
        residual = delta - unseen
        if not delta:
            why = "Counts agree."
        else:
            why = []
            if unseen:
                why.append(f"{int(r['no_asn_received'])} received without an ASN ({r['no_asn_refs']}), "
                           f"{r['provisional']} of them installed on provisional serials, so {unseen} sit on the "
                           f"CM's shelf with no serial record")
            if residual:
                why.append(f"{residual:+d} unexplained: physical count requested from the CM")
            why = "; ".join(why) + "."
        r.update(cm=cm, delta=delta, explained=unseen, residual=residual, why=why,
                 status="MATCH" if not delta else ("EXPLAINED" if not residual else "OPEN"))
        if r["item_id"] == "DU-B" and not delta:
            r["why"] = "Counts agree. All stranded after the ECO-0042 cut-in: rev B is frozen at the CM."
        rows.append(r)
    a = sum(r["cm"] for r in rows)
    b = sum(r["ours"] for r in rows)
    explained = sum(r["explained"] for r in rows if r["delta"])
    return {
        "id": "cm-stock", "title": "CM consigned-stock Excel vs our serialized count",
        "question": "Is the OEM-owned stock sitting at the CM in Taiwan where both of us think it is?",
        "a": {"system": f"CM report · {_md(day)}", "table": "cm_stock_report", "value": a, "unit": "units"},
        "b": {"system": "Serials at CM-TXG", "table": "unit", "value": b, "unit": "units"},
        "delta": a - b, "status": _status(a - b, explained if all(r["residual"] == 0 for r in rows) else None),
        "explanation": " ".join(f"{r['item_id']}: {r['why']}" for r in rows if r["delta"]) or "All items agree.",
        "owner": "Planning + CM", "tables": ["cm_stock_report", "unit", "goods_receipt"],
        "columns": [{"key": "item_id", "label": "Item"}, {"key": "cm", "label": "CM says", "num": True},
                    {"key": "ours", "label": "Our serials", "num": True}, {"key": "delta", "label": "Δ", "num": True},
                    {"key": "explained", "label": "Explained", "num": True},
                    {"key": "residual", "label": "Residual", "num": True}, {"key": "status", "label": "Status"},
                    {"key": "why", "label": "Why"}],
        "rows": rows, "report_date": day,
    }


# ---------------------------------------------------------------------------- 3. CM ASN vs 3PL receipt

def asn_vs_receipt(conn):
    """Every container the 3PL received: serials on the CM's ASN vs serials the 3PL scanned in."""
    rows = [dict(r) for r in conn.execute("""
        SELECT s.shipment_id, s.container_no, s.vessel || ' ' || s.voyage AS sailing, s.received_at,
               COUNT(su.serial) AS on_asn,
               SUM(CASE WHEN u.status = 'IN_TRANSIT' THEN 1 ELSE 0 END) AS missing,
               SUM(CASE WHEN EXISTS (SELECT 1 FROM hold h WHERE h.serial = u.serial AND h.hold_id LIKE 'HOLD-3PL-%')
                        THEN 1 ELSE 0 END) AS damaged
        FROM shipment s JOIN shipment_unit su USING (shipment_id) JOIN unit u ON u.serial = su.serial
        WHERE s.leg = 'CM_TO_3PL' AND s.received_at IS NOT NULL
        GROUP BY s.shipment_id ORDER BY s.received_at DESC""")]
    for r in rows:
        r["received"] = r["on_asn"] - r["missing"]
        r["status"] = "OPEN" if r["missing"] else ("EXCEPTION" if r["damaged"] else "MATCH")
    missing = [dict(r) for r in conn.execute("""
        SELECT su.shipment_id, u.serial, u.item_id, u.built_at FROM shipment s JOIN shipment_unit su USING (shipment_id)
        JOIN unit u ON u.serial = su.serial
        WHERE s.leg = 'CM_TO_3PL' AND s.received_at IS NOT NULL AND u.status = 'IN_TRANSIT' ORDER BY u.serial""")]
    damaged = [dict(r) for r in conn.execute("""
        SELECT su.shipment_id, h.serial, h.reason, h.placed_at FROM hold h JOIN shipment_unit su ON su.serial = h.serial
        JOIN shipment s ON s.shipment_id = su.shipment_id AND s.leg = 'CM_TO_3PL'
        WHERE h.hold_id LIKE 'HOLD-3PL-%' ORDER BY h.placed_at DESC""")]
    trucks = conn.execute("""
        SELECT COUNT(DISTINCT s.shipment_id) n, COUNT(*) units,
               SUM(CASE WHEN u.status = 'IN_TRANSIT' THEN 1 ELSE 0 END) missing
        FROM shipment s JOIN shipment_unit su USING (shipment_id) JOIN unit u ON u.serial = su.serial
        WHERE s.leg = 'PLANT_TO_3PL' AND s.received_at IS NOT NULL""").fetchone()
    a = sum(r["on_asn"] for r in rows)
    b = sum(r["received"] for r in rows)
    ship = sorted({m["shipment_id"] for m in missing})
    return {
        "id": "asn-receipt", "title": "CM ASN vs 3PL receipt scan",
        "question": "Did every vehicle the CM says it loaded arrive in Reno?",
        "a": {"system": "CM ASN (serials loaded)", "table": "shipment_unit", "value": a, "unit": "vehicles"},
        "b": {"system": "3PL receipt scans", "table": "raw_3pl_message", "value": b, "unit": "vehicles"},
        "delta": a - b, "status": _status(a - b),
        "explanation": (f"{_n(len(missing), 'serial')} on {', '.join(ship)}'s ASN never scanned in Reno. Until the CM "
                        f"checks its dock and the carrier checks the container, they stay in transit on our books and "
                        f"ATP counts them as supply nowhere. " if missing else "Every ASN serial was scanned. ")
        + (f"{_n(len(damaged), 'received vehicle')} arrived freight-damaged and {'is' if len(damaged) == 1 else 'are'} "
           f"on hold (received, not available). "
           if damaged else "")
        + f"DG pack trucks: {trucks['n']} received, {trucks['units']:,} packs, {trucks['missing'] or 0} missing.",
        "owner": "Logistics", "tables": ["shipment", "shipment_unit", "raw_3pl_message", "hold"],
        "columns": [{"key": "shipment_id", "label": "Container"}, {"key": "sailing", "label": "Sailing"},
                    {"key": "received_at", "label": "Received"}, {"key": "on_asn", "label": "On ASN", "num": True},
                    {"key": "received", "label": "Scanned", "num": True}, {"key": "missing", "label": "Missing", "num": True},
                    {"key": "damaged", "label": "Damaged", "num": True}, {"key": "status", "label": "Status"}],
        "rows": rows, "missing": missing, "damaged": damaged, "trucks": dict(trucks),
    }


# ---------------------------------------------------------------------------- 4. 3PL WMS vs serialized

def wms_vs_serialized(conn):
    """The 3PL's nightly WMS count against our serialized units in Reno, rolled back to the snapshot time."""
    t = conn.execute("SELECT MAX(snapshot_at) t FROM wms_snapshot").fetchone()["t"]
    if t is None:
        return None
    rows = []
    for r in conn.execute("""
        SELECT w.item_id, w.qty_available, w.qty_allocated, w.qty_hold,
               w.qty_available + w.qty_allocated + w.qty_hold AS wms,
               (SELECT COUNT(*) FROM unit u WHERE u.item_id = w.item_id AND u.location_site_id = '3PL-RNO'
                  AND u.status IN ('AT_3PL', 'ALLOCATED')) AS ours_now,
               (SELECT COUNT(*) FROM unit u WHERE u.item_id = w.item_id AND u.location_site_id = '3PL-RNO'
                  AND u.status IN ('AT_3PL', 'ALLOCATED') AND u.on_hold = 1) AS ours_hold,
               (SELECT COUNT(*) FROM unit u JOIN shipment_unit su ON su.serial = u.serial
                  JOIN shipment s ON s.shipment_id = su.shipment_id AND s.leg IN ('CM_TO_3PL', 'PLANT_TO_3PL')
                  WHERE u.item_id = w.item_id AND u.location_site_id = '3PL-RNO' AND u.status IN ('AT_3PL', 'ALLOCATED')
                  AND s.received_at > w.snapshot_at) AS received_since,
               (SELECT COUNT(*) FROM unit u JOIN shipment_unit su ON su.serial = u.serial
                  JOIN shipment s ON s.shipment_id = su.shipment_id AND s.leg = '3PL_TO_CUSTOMER'
                  WHERE u.item_id = w.item_id AND s.atd > w.snapshot_at) AS shipped_since,
               (SELECT COUNT(*) FROM genealogy g JOIN unit u ON u.serial = g.child_serial
                  WHERE g.source = 'SERVICE' AND u.item_id = w.item_id AND g.installed_at > w.snapshot_at) AS service_since
        FROM wms_snapshot w WHERE w.snapshot_at = ? ORDER BY w.item_id""", (t,)):
        r = dict(r)
        r["ours_at_snapshot"] = r["ours_now"] - r["received_since"] + r["shipped_since"] + r["service_since"]
        r["delta"] = r["wms"] - r["ours_at_snapshot"]
        r["hold_delta"] = r["qty_hold"] - r["ours_hold"]
        if r["delta"] > 0:
            r["why"] = (f"WMS holds {r['delta']} more than any serial we can place in Reno: a unit counted on the floor "
                        f"without a serial scan. Cycle-count it.")
        elif r["delta"] < 0:
            r["why"] = f"We place {-r['delta']} serials in Reno that the WMS does not count: find or write off."
        else:
            r["why"] = "Counts agree" + (f"; {r['qty_hold']} on hold in both" if r["qty_hold"] else "") + "."
        r["status"] = "MATCH" if not r["delta"] else "OPEN"
        rows.append(r)
    a = sum(r["wms"] for r in rows)
    b = sum(r["ours_at_snapshot"] for r in rows)
    return {
        "id": "wms", "title": "3PL WMS snapshot vs our serialized units",
        "question": "Does the 3PL's system of record agree with ours, unit for unit?",
        "a": {"system": "3PL WMS (nightly snapshot)", "table": "wms_snapshot", "value": a, "unit": "units"},
        "b": {"system": "Our serials in Reno", "table": "unit", "value": b, "unit": "units"},
        "delta": a - b, "status": _status(a - b),
        "explanation": (" ".join(f"{r['item_id']}: {r['why']}" for r in rows if r["delta"]) or "All SKUs agree.")
        + f" Our count is rolled back to the snapshot time ({_pt(t)}) by undoing receipts and shipments since.",
        "owner": "Logistics (3PL)", "tables": ["wms_snapshot", "unit", "shipment_unit"],
        "columns": [{"key": "item_id", "label": "SKU"}, {"key": "wms", "label": "WMS", "num": True},
                    {"key": "ours_at_snapshot", "label": "Ours at snapshot", "num": True},
                    {"key": "delta", "label": "Δ", "num": True}, {"key": "qty_hold", "label": "WMS hold", "num": True},
                    {"key": "ours_hold", "label": "Our holds", "num": True},
                    {"key": "received_since", "label": "Recv. since", "num": True},
                    {"key": "shipped_since", "label": "Shipped since", "num": True},
                    {"key": "status", "label": "Status"}, {"key": "why", "label": "Why"}],
        "rows": rows, "snapshot_at": t,
    }


# ---------------------------------------------------------------------------- 5. invoice vs PO vs contract

def invoice_price_match(conn):
    """Three-way match (PO, receipt, invoice) plus the fourth leg it cannot see: the contract."""
    match = [dict(r) for r in conn.execute("""
        SELECT match_status, pay_status, COUNT(*) n, ROUND(SUM(amount_usd), 2) usd, ROUND(SUM(variance_usd), 2) variance
        FROM supplier_invoice GROUP BY 1, 2 ORDER BY 1, 2""")]
    exceptions = [dict(r) for r in conn.execute("""
        SELECT si.invoice_id, si.supplier_id, si.po_id, si.line_no, pl.item_id, si.qty AS invoiced_qty,
               pl.received_qty, si.unit_price AS invoice_price, pl.unit_price AS po_price, si.amount_usd,
               si.variance_usd, si.match_status, si.pay_status,
               (SELECT GROUP_CONCAT(gr.lot_id) FROM goods_receipt gr WHERE gr.po_id = si.po_id AND gr.line_no = si.line_no) AS lots,
               (SELECT GROUP_CONCAT(l.iqc_status) FROM goods_receipt gr JOIN lot l ON l.lot_id = gr.lot_id
                  WHERE gr.po_id = si.po_id AND gr.line_no = si.line_no) AS iqc
        FROM supplier_invoice si JOIN po_line pl ON pl.po_id = si.po_id AND pl.line_no = si.line_no
        WHERE si.match_status <> 'MATCHED' ORDER BY si.invoice_date DESC""")]
    contract = [dict(r) for r in conn.execute("""
        SELECT pl.po_id, pl.line_no, pl.item_id, po.supplier_id, substr(po.created_at, 1, 10) AS po_date, pl.qty,
               pl.unit_price AS po_price, p.unit_price AS contract_price, p.source_ref AS contract_ref,
               ROUND((pl.unit_price - p.unit_price) * pl.qty, 2) AS overpay_usd, pl.status,
               (SELECT si.match_status FROM supplier_invoice si WHERE si.po_id = pl.po_id AND si.line_no = pl.line_no) AS invoice
        FROM po_line pl JOIN purchase_order po USING (po_id)
        JOIN price p ON p.item_id = pl.item_id AND p.supplier_id = po.supplier_id AND p.min_qty = 0 AND p.basis <> 'QUOTE'
         AND p.eff_from <= substr(po.created_at, 1, 10) AND (p.eff_to IS NULL OR p.eff_to > substr(po.created_at, 1, 10))
        WHERE ABS(pl.unit_price - p.unit_price) > 0.001 ORDER BY overpay_usd DESC""")]
    superseded = {}
    for c in contract:
        prev = conn.execute("""SELECT source_ref, eff_to FROM price WHERE item_id = ? AND supplier_id = ? AND min_qty = 0
                               AND ABS(unit_price - ?) < 0.001 AND eff_to IS NOT NULL AND eff_to <= ?
                               ORDER BY eff_to DESC LIMIT 1""",
                            (c["item_id"], c["supplier_id"], c["po_price"], c["po_date"])).fetchone()
        c["superseded_ref"] = prev["source_ref"] if prev else None
        c["superseded_on"] = prev["eff_to"] if prev else None
        superseded[c["po_id"]] = prev
    overpay = round(sum(c["overpay_usd"] for c in contract if c["overpay_usd"] > 0), 2)
    blocked = round(sum(e["variance_usd"] for e in exceptions), 2)
    three_way_ok = sum(m["n"] for m in match if m["match_status"] == "MATCHED")
    uninvoiced = [c for c in contract if not c["invoice"]]
    expl = []
    if exceptions:
        expl.append(f"Three-way match blocked {_n(len(exceptions), 'invoice')} (${blocked:,.0f}): "
                    + "; ".join(f"{e['invoice_id']} {e['match_status'].lower().replace('_', ' ')}"
                                + (" on a lot rejected at IQC" if e["iqc"] and "REJECTED" in e["iqc"] else "")
                                for e in exceptions[:3]) + ".")
    if contract:
        c0 = contract[0]
        expl.append(f"The contract check finds {_n(len(contract), 'PO line')} priced above the contract, ${overpay:,.0f} "
                    f"overpaid if invoiced as ordered: {c0['item_id']} at ${c0['po_price']:.2f} vs ${c0['contract_price']:.2f} "
                    f"({c0['contract_ref']})" + (f", a price superseded on {_md(c0['superseded_on'])}" if c0["superseded_on"] else "")
                    + ". Three-way match will pass these, because PO, receipt and invoice agree with each other; "
                    "only the contract sees that the PO itself is wrong.")
        if uninvoiced:
            expl.append("None of them is invoiced yet: fix the PO price before the supplier bills."
                        if len(uninvoiced) == len(contract) else
                        f"{_n(len(uninvoiced), 'line')} not yet invoiced: fix the PO price before the supplier bills.")
    return {
        "id": "invoice", "title": "Supplier invoices vs PO vs contract price",
        "question": "Are we paying the price we negotiated, not just the price on the PO?",
        "a": {"system": "Three-way match exceptions", "table": "supplier_invoice", "value": len(exceptions), "unit": "invoices"},
        "b": {"system": "Contract-price exceptions", "table": "price", "value": len(contract), "unit": "PO lines"},
        "delta": overpay, "delta_unit": "usd", "status": "OPEN" if (contract or exceptions) else "MATCH",
        "explanation": " ".join(expl) or f"All {three_way_ok} invoices match PO, receipt and contract.",
        "owner": "Procurement + AP", "tables": ["supplier_invoice", "po_line", "goods_receipt", "price"],
        "columns": [{"key": "po_id", "label": "PO"}, {"key": "item_id", "label": "Item"},
                    {"key": "supplier_id", "label": "Supplier"}, {"key": "po_date", "label": "PO date"},
                    {"key": "qty", "label": "Qty", "num": True}, {"key": "po_price", "label": "PO price", "num": True},
                    {"key": "contract_price", "label": "Contract", "num": True},
                    {"key": "overpay_usd", "label": "Overpay $", "num": True}, {"key": "invoice", "label": "Invoice"}],
        "rows": contract, "match": match, "exceptions": exceptions, "overpay_usd": overpay, "blocked_usd": blocked,
        "three_way_matched": three_way_ok,
    }


# ---------------------------------------------------------------------------- 6. chargebacks vs ERP

def chargebacks_vs_erp(conn):
    """What quality says it recovered against what the ERP actually booked, to the cent."""
    rows = [dict(r) for r in conn.execute("""
        SELECT c.chargeback_id, c.supplier_id, c.basis, c.status, c.amount_usd,
               (SELECT ROUND(SUM(l.amount_usd), 2) FROM chargeback_line l WHERE l.chargeback_id = c.chargeback_id) AS lines_usd,
               c.je_id, c.debit_memo_no, c.posted_at, j.debit, j.credit, j.ap_debit
        FROM chargeback c
        LEFT JOIN (SELECT je_id, ROUND(SUM(debit_usd), 2) AS debit, ROUND(SUM(credit_usd), 2) AS credit,
                          ROUND(SUM(CASE WHEN gl_account = '2000' THEN debit_usd ELSE 0 END), 2) AS ap_debit
                   FROM erp_journal_line GROUP BY je_id) j ON j.je_id = c.je_id
        ORDER BY c.chargeback_id""")]
    for r in rows:
        if r["status"] == "POSTED":
            ok = (r["debit"] is not None and abs(r["debit"] - r["credit"]) < 0.005
                  and abs((r["ap_debit"] or 0) - r["amount_usd"]) < 0.005 and abs((r["lines_usd"] or 0) - r["amount_usd"]) < 0.005)
            r["check"] = "MATCH" if ok else "OPEN"
            r["why"] = ("Debit memo balanced; AP reduced by exactly the chargeback." if ok
                        else "Journal entry does not tie to the chargeback.")
        else:
            r["check"] = "NOT_POSTED"
            r["why"] = {"DRAFT": "Draft: not sent, nothing in the ERP yet.", "SENT": "Sent: awaiting the supplier.",
                        "ACCEPTED": "Accepted: ready to post.", "DISPUTED": "Disputed: nothing books until it resolves."
                        }.get(r["status"], "Not posted.")
    orphans = [dict(r) for r in conn.execute("""
        SELECT je.je_id, je.memo, je.posted_at FROM erp_journal_entry je
        WHERE je.doc_type = 'DEBIT_MEMO' AND NOT EXISTS (SELECT 1 FROM chargeback c WHERE c.je_id = je.je_id)""")]
    posted = [r for r in rows if r["status"] == "POSTED"]
    a = round(sum(r["amount_usd"] for r in posted), 2)
    b = round(sum(r["ap_debit"] or 0 for r in posted) + sum(0 for _ in orphans), 2)
    open_usd = round(sum(r["amount_usd"] for r in rows if r["status"] != "POSTED"), 2)
    bad = [r for r in posted if r["check"] != "MATCH"]
    return {
        "id": "chargeback-erp", "title": "Chargebacks vs ERP debit memos",
        "question": "Did every dollar quality recovered land in the general ledger, balanced?",
        "a": {"system": "Posted chargebacks (QMS)", "table": "chargeback", "value": a, "unit": "usd"},
        "b": {"system": "AP debits on debit memos (ERP)", "table": "erp_journal_line", "value": b, "unit": "usd"},
        "delta": round(a - b, 2), "delta_unit": "usd", "status": "MATCH" if not bad and not orphans and abs(a - b) < 0.005 else "OPEN",
        "explanation": (f"{_n(len(posted), 'posted chargeback')} {'ties' if len(posted) == 1 else 'tie'} to balanced debit memos to the cent. " if not bad
                        else f"{len(bad)} posted chargebacks do not tie. ")
        + (f"{_n(len(orphans), 'debit memo')} with no chargeback behind {'it' if len(orphans) == 1 else 'them'}. " if orphans else "")
        + f"${open_usd:,.0f} more is in flight (draft, sent, accepted or disputed) and correctly not in the ledger yet.",
        "owner": "Finance", "tables": ["chargeback", "chargeback_line", "erp_journal_entry", "erp_journal_line"],
        "columns": [{"key": "chargeback_id", "label": "Chargeback"}, {"key": "supplier_id", "label": "Supplier"},
                    {"key": "status", "label": "Status"}, {"key": "amount_usd", "label": "Amount $", "num": True},
                    {"key": "lines_usd", "label": "Lines $", "num": True}, {"key": "je_id", "label": "Journal entry"},
                    {"key": "ap_debit", "label": "AP debit $", "num": True}, {"key": "credit", "label": "Credit $", "num": True},
                    {"key": "check", "label": "Check"}, {"key": "why", "label": "Why"}],
        "rows": rows, "orphans": orphans, "in_flight_usd": open_usd,
    }


def all_recons(conn):
    out = [cm_output_vs_mes(conn), cm_stock_vs_units(conn), asn_vs_receipt(conn), wms_vs_serialized(conn),
           invoice_price_match(conn), chargebacks_vs_erp(conn)]
    return [r for r in out if r]
