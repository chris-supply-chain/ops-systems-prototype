"""Supplier scorecard: the "learn" step. Delivery, quality, responsiveness and
recoveries roll up weekly per tier-1 supplier. Sourcing reads it (the RFQ award
weighs it), so outcomes change future decisions.
"""
import datetime as dt
import statistics

from ..db import as_of as get_as_of

SUPPLIERS = ("KES", "PNC", "SMT", "CSP", "NRT", "BAY", "TNM", "HDS", "VPC", "FAP")


def compute(conn, weeks=12):
    as_of = dt.date.fromisoformat(get_as_of(conn))
    ws0 = as_of - dt.timedelta(days=as_of.weekday())
    conn.execute("DELETE FROM supplier_scorecard")
    rows = []
    for k in range(weeks):
        start = ws0 - dt.timedelta(days=7 * (weeks - 1 - k))
        end = start + dt.timedelta(days=7)
        s, e = start.isoformat(), end.isoformat()
        for sup in SUPPLIERS:
            rec = conn.execute("""SELECT gr.received_at, pl.promise_date, pl.need_date, gr.qty FROM goods_receipt gr
                                  JOIN po_line pl ON pl.po_id=gr.po_id AND pl.line_no=gr.line_no
                                  JOIN purchase_order po ON po.po_id=gr.po_id
                                  WHERE po.supplier_id=? AND gr.received_at>=? AND gr.received_at<?""", (sup, s, e)).fetchall()
            if sup == "FAP":
                rec = conn.execute("""SELECT se.event_ts AS received_at, NULL AS promise_date, NULL AS need_date, s.asn_qty AS qty
                                      FROM shipment s JOIN shipment_event se ON se.shipment_id=s.shipment_id AND se.code='LOADED'
                                      WHERE s.leg='CM_TO_3PL' AND se.event_ts>=? AND se.event_ts<?""", (s, e)).fetchall()
            ontime = [1 if (r["promise_date"] is None or r["received_at"][:10] <= r["promise_date"]) else 0 for r in rec]
            otd = 100.0 * sum(ontime) / len(ontime) if ontime else None
            units = sum(r["qty"] or 0 for r in rec)
            bad = conn.execute("""SELECT COALESCE(SUM(qty_defective),0) n FROM quality_event WHERE supplier_id=? AND kind='IQC'
                                  AND detected_at>=? AND detected_at<?""", (sup, s, e)).fetchone()["n"]
            claims = conn.execute("""SELECT COUNT(*) n FROM warranty_claim WHERE supplier_id=? AND reported_at>=? AND reported_at<?""",
                                  (sup, s, e)).fetchone()["n"]
            ppm = 1e6 * (bad + claims) / units if units else None
            conf = [(dt.datetime.fromisoformat(r["confirmed_at"][:19]) - dt.datetime.fromisoformat(r["created_at"][:19])
                     ).total_seconds() / 3600 for r in conn.execute(
                """SELECT pl.confirmed_at, po.created_at FROM po_line pl JOIN purchase_order po USING(po_id)
                   WHERE po.supplier_id=? AND pl.confirmed_at>=? AND pl.confirmed_at<?""", (sup, s, e))]
            slips = [r["d"] for r in conn.execute(
                """SELECT julianday(h.promise_date) - julianday(pl.need_date) d FROM po_promise_history h
                   JOIN po_line pl ON pl.po_id=h.po_id AND pl.line_no=h.line_no JOIN purchase_order po ON po.po_id=h.po_id
                   WHERE po.supplier_id=? AND h.recorded_at>=? AND h.recorded_at<?""", (sup, s, e))]
            cb = conn.execute("SELECT COALESCE(SUM(amount_usd),0) v FROM chargeback WHERE supplier_id=? AND created_at>=? AND created_at<?",
                              (sup, s, e)).fetchone()["v"]
            conf_h = statistics.median(conf) if conf else None
            slip = sum(slips) / len(slips) if slips else None
            score = 100.0
            if otd is not None:
                score -= (100 - otd) * 0.5
            if ppm:
                score -= min(30.0, ppm / 200.0)
            if conf_h:
                score -= min(15.0, conf_h / 8.0)
            if slip and slip > 0:
                score -= min(20.0, slip * 2.0)
            rows.append((sup, s, None if otd is None else round(otd, 1), None if ppm is None else round(ppm, 0),
                         None if conf_h is None else round(conf_h, 1), None if slip is None else round(slip, 1),
                         round(cb, 2), round(max(0.0, score), 1)))
    conn.executemany("INSERT INTO supplier_scorecard VALUES (?,?,?,?,?,?,?,?)", rows)
    return len(rows)
