"""Data contracts: invariants the data must satisfy, written as SQL that returns the
violating rows. Zero rows means pass. They run after every ingest and after
every closed-loop action, and their results are stored, so drift is a trend
rather than a surprise.
"""
import json
import time

from ..db import now as get_now

NOW = "(SELECT value FROM meta WHERE key='now_utc')"

CONTRACTS = [
    ("C-GEN-01", "Every built vehicle has a complete as-built record", "genealogy", "CORE", "CRITICAL",
     "Frame, drive unit, pedal unit and HMI must each be on an active genealogy edge once S80 passes.",
     """SELECT u.serial, (SELECT GROUP_CONCAT(DISTINCT g.position) FROM genealogy g WHERE g.parent_serial=u.serial
               AND g.removed_at IS NULL AND g.position IN ('FRAME','DRIVE_UNIT','PEDAL_UNIT','HMI')) AS present
        FROM unit u WHERE u.item_id LIKE 'LV1-%' AND u.built_at IS NOT NULL
        AND (SELECT COUNT(DISTINCT g.position) FROM genealogy g WHERE g.parent_serial=u.serial AND g.removed_at IS NULL
             AND g.position IN ('FRAME','DRIVE_UNIT','PEDAL_UNIT','HMI')) < 4""", "Data eng."),
    ("C-GEN-02", "Installed drive units carry supplier sub-genealogy", "genealogy", "CORE", "SERIOUS",
     "A drive unit in a vehicle must have its motor and controller serials (from the supplier ASN).",
     """SELECT u.serial, u.origin, (SELECT g.parent_serial FROM genealogy g WHERE g.child_serial=u.serial
               AND g.removed_at IS NULL) AS vehicle
        FROM unit u WHERE u.item_id IN ('DU-B','DU-C') AND u.status='INSTALLED'
        AND (SELECT COUNT(*) FROM genealogy g WHERE g.parent_serial=u.serial AND g.removed_at IS NULL) < 2""", "Data eng."),
    ("C-GEN-03", "No part is in two places at once", "genealogy", "CORE", "CRITICAL",
     "At most one current parent per serialized part: installed in one unit, or kitted with one vehicle.",
     """SELECT child_serial, COUNT(*) AS parents, GROUP_CONCAT(parent_serial, ', ') AS parent_serials FROM genealogy
        WHERE removed_at IS NULL AND child_serial IS NOT NULL GROUP BY child_serial HAVING COUNT(*) > 1""", "Data eng."),
    ("C-GEN-04", "Every pack at or past the 3PL traces to a BMS and a cell lot", "genealogy", "CORE", "CRITICAL",
     "Needed for any battery recall to be scoped by lot.",
     """SELECT u.serial FROM unit u WHERE u.item_id LIKE 'PK-%' AND u.status IN ('AT_3PL','ALLOCATED','SHIPPED','DELIVERED')
        AND (NOT EXISTS (SELECT 1 FROM genealogy g WHERE g.parent_serial=u.serial AND g.position='BMS' AND g.removed_at IS NULL)
          OR NOT EXISTS (SELECT 1 FROM genealogy g WHERE g.parent_serial=u.serial AND g.child_item_id='CEL-21700'))""", "Data eng."),
    ("C-GEN-05", "End-of-line drive-unit read matches the as-built drive unit", "genealogy", "CORE", "CRITICAL",
     "The S60 tester reads the drive-unit serial over CAN; it must equal the drive unit genealogy says is installed.",
     """WITH last_pass AS (      -- the latest passing S60 test per vehicle and line (a window, not a correlated MAX)
          SELECT se.serial, json_extract(se.measurements,'$.du_sn') AS eol_read,
                 RANK() OVER (PARTITION BY se.serial, se.station_id ORDER BY se.event_ts DESC) AS k
          FROM station_event se JOIN station s ON s.station_id=se.station_id AND s.code='S60'
          WHERE se.result='PASS')
        SELECT lp.serial, lp.eol_read, g.child_serial AS as_built
        FROM last_pass lp JOIN genealogy g ON g.parent_serial=lp.serial AND g.position='DRIVE_UNIT' AND g.removed_at IS NULL
        WHERE lp.k=1 AND lp.eol_read IS NOT NULL AND lp.eol_read <> g.child_serial""", "Data eng."),
    ("C-GEN-06", "A serialized slot holds one part at a time", "genealogy", "CORE", "CRITICAL",
     "One current part per parent and slot (drive unit, HMI, pack). A second means a removal was never recorded; the "
     "loaders land it rather than guess which record is wrong, and this flags it.",
     """SELECT parent_serial, position, COUNT(*) AS parts, GROUP_CONCAT(child_serial, ', ') AS children FROM genealogy
        WHERE removed_at IS NULL AND child_serial IS NOT NULL GROUP BY parent_serial, position HAVING COUNT(*) > 1""",
     "Data eng."),
    ("C-FEED-01", "No CM MES message sits in quarantine for more than 24 hours", "cm_feed", "LANDING", "SERIOUS",
     "Quarantine keeps bad data out; this contract keeps quarantine from becoming a place data goes to die.",
     f"""SELECT raw_id, received_at, ingest_note FROM raw_cm_mes_event WHERE ingest_status='QUARANTINED'
         AND received_at < strftime('%Y-%m-%dT%H:%M:%SZ', {NOW}, '-24 hours')""", "Data eng."),
    ("C-FEED-02", "Every landed message reached a terminal state", "landing", "LANDING", "WARNING",
     "Nothing left PENDING after an ingest run.",
     """SELECT 'raw_cm_mes_event' AS tbl, raw_id FROM raw_cm_mes_event WHERE ingest_status='PENDING'
        UNION ALL SELECT 'raw_3pl_message', raw_id FROM raw_3pl_message WHERE ingest_status='PENDING'
        UNION ALL SELECT 'raw_carrier_event', raw_id FROM raw_carrier_event WHERE ingest_status='PENDING'
        UNION ALL SELECT 'raw_email', raw_id FROM raw_email WHERE ingest_status='PENDING'
        UNION ALL SELECT 'raw_warranty_case', raw_id FROM raw_warranty_case WHERE ingest_status='PENDING'""", "Data eng."),
    ("C-SRC-01", "PO lines are confirmed within 72 hours", "sourcing", "CORE", "WARNING",
     "Without a promise, MRP plans on our need date and cannot see slips.",
     f"""SELECT pl.po_id, pl.line_no, pl.item_id, po.supplier_id, po.created_at FROM po_line pl JOIN purchase_order po USING(po_id)
         WHERE pl.status='OPEN' AND pl.confirm_status='UNCONFIRMED'
         AND po.created_at < strftime('%Y-%m-%dT%H:%M:%SZ', {NOW}, '-72 hours')""", "Procurement"),
    ("C-SRC-02", "Price effectivity windows never overlap", "pricing", "CORE", "SERIOUS",
     "For one item, supplier and price break, exactly one contract price applies on any date.",
     """SELECT a.item_id, a.supplier_id, a.price_id AS a_id, b.price_id AS b_id FROM price a JOIN price b
        ON a.item_id=b.item_id AND a.supplier_id=b.supplier_id AND a.min_qty=b.min_qty AND a.price_id<b.price_id
        AND a.basis<>'QUOTE' AND b.basis<>'QUOTE'
        WHERE a.eff_from < COALESCE(b.eff_to,'9999-12-31') AND b.eff_from < COALESCE(a.eff_to,'9999-12-31')""", "Procurement"),
    ("C-SRC-03", "PO prices equal the contract price effective on the PO date", "pricing", "CORE", "SERIOUS",
     "Catches superseded prices copied forward onto new POs.",
     """SELECT pl.po_id, pl.line_no, pl.item_id, pl.unit_price, p.unit_price AS contract_price,
               ROUND((pl.unit_price - p.unit_price) * pl.qty, 2) AS overpay_usd
        FROM po_line pl JOIN purchase_order po USING(po_id)
        JOIN price p ON p.item_id=pl.item_id AND p.supplier_id=po.supplier_id AND p.min_qty=0 AND p.basis<>'QUOTE'
         AND p.eff_from <= substr(po.created_at,1,10) AND (p.eff_to IS NULL OR p.eff_to > substr(po.created_at,1,10))
        WHERE ABS(pl.unit_price - p.unit_price) > 0.001""", "Procurement"),
    ("C-MOV-01", "Every unit on a received container's ASN was received", "logistics", "CORE", "SERIOUS",
     "The CM's ASN, the carrier's container and the 3PL's scan must agree.",
     """SELECT s.shipment_id, su.serial FROM shipment s JOIN shipment_unit su USING(shipment_id) JOIN unit u ON u.serial=su.serial
        WHERE s.leg='CM_TO_3PL' AND s.received_at IS NOT NULL AND u.status='IN_TRANSIT'""", "Logistics"),
    ("C-MOV-02", "ISF is filed at least 24 hours before vessel loading", "customs", "CORE", "SERIOUS",
     "Late ISF risks a $5,000 penalty per filing and holds at discharge.",
     """SELECT ce.entry_no, ce.shipment_id, ce.isf_filed_at, se.event_ts AS loaded_at FROM customs_entry ce
        JOIN shipment_event se ON se.shipment_id=ce.shipment_id AND se.code='LOADED'
        WHERE julianday(se.event_ts) - julianday(ce.isf_filed_at) < 1.0""", "Logistics"),
    ("C-QUA-01", "Nothing on an active hold leaves for a customer", "quality", "ACTION", "CRITICAL",
     "A hold must stop the unit at the 3PL; shipping after the hold was placed is a containment failure.",
     """SELECT h.serial, h.placed_at, s.shipment_id, s.atd FROM hold h JOIN shipment_unit su ON su.serial=h.serial
        JOIN shipment s ON s.shipment_id=su.shipment_id AND s.leg='3PL_TO_CUSTOMER'
        WHERE h.released_at IS NULL AND s.atd > h.placed_at""", "SQE"),
    ("C-QUA-02", "Deviations are used within their quantity and dates", "quality", "CORE", "SERIOUS",
     "An approved deviation is a limit, not a license.",
     """SELECT deviation_id, qty_used, qty_limit, valid_to, status FROM deviation
        WHERE qty_used > qty_limit OR (status='APPROVED' AND valid_to < (SELECT value FROM meta WHERE key='as_of'))""", "SQE"),
    ("C-QUA-03", "Use-up parts are only installed while their deviation is valid", "quality", "CORE", "SERIOUS",
     "A deviation that lets a phase-out part be used up ends on its valid_to date; installs after that are unauthorized.",
     """SELECT d.deviation_id, d.item_id, d.valid_to, COUNT(*) AS installs_after_expiry,
               MIN(g.installed_at) AS first_after, MAX(g.installed_at) AS last_after
        FROM deviation d JOIN item i ON i.item_id = d.item_id AND i.lifecycle = 'PHASE_OUT'
        JOIN genealogy g ON g.child_item_id = d.item_id AND substr(g.installed_at, 1, 10) > d.valid_to
        GROUP BY d.deviation_id""", "SQE"),
    ("C-FIN-01", "Posted chargebacks have a balanced journal entry equal to the amount", "finance", "ACTION", "CRITICAL",
     "What quality claims back must land in the ERP to the cent.",
     """SELECT c.chargeback_id, c.amount_usd, j.d, j.cr FROM chargeback c
        LEFT JOIN (SELECT je_id, SUM(debit_usd) d, SUM(credit_usd) cr FROM erp_journal_line GROUP BY je_id) j ON j.je_id=c.je_id
        WHERE c.status='POSTED' AND (j.je_id IS NULL OR ABS(j.d - j.cr) > 0.005 OR ABS(j.d - c.amount_usd) > 0.005)""", "Finance"),
    ("C-FIN-02", "A chargeback equals the sum of its lines", "finance", "ACTION", "SERIOUS",
     "Header and lines never drift.",
     """SELECT c.chargeback_id, c.amount_usd, ROUND(SUM(l.amount_usd),2) AS lines FROM chargeback c
        JOIN chargeback_line l USING(chargeback_id) GROUP BY c.chargeback_id HAVING ABS(c.amount_usd - SUM(l.amount_usd)) > 0.005""",
     "Finance"),
    ("C-FIN-03", "Suppliers are billed only for diagnosed warranty claims", "finance", "ACTION", "CRITICAL",
     "An open claim has no root cause yet and a rejected one was not a warrantable failure; billing either is a "
     "chargeback the supplier will rightly dispute.",
     """SELECT w.claim_id, w.status, l.chargeback_id FROM warranty_claim w
        JOIN chargeback_line l ON l.ref_type = 'CLAIM' AND l.ref_id = w.claim_id
        WHERE w.status NOT IN ('DIAGNOSED', 'REPAIRED', 'CLOSED')""",
     "Finance"),
    ("C-PLN-01", "Every open order carries a promise date", "planning", "CORE", "SERIOUS",
     "No customer waits without a date.",
     """SELECT order_id FROM customer_order WHERE status IN ('OPEN','ALLOCATED','SHIPPED') AND promised_date IS NULL""",
     "Customer ops"),
    ("C-INV-01", "The 3PL's WMS count equals our serialized count by SKU", "inventory", "CORE", "WARNING",
     "One honest picture: their system of record against ours, every night.",
     """WITH latest AS (SELECT MAX(snapshot_at) t FROM wms_snapshot),
             wms AS (SELECT item_id, qty_available + qty_allocated + qty_hold AS wms_qty FROM wms_snapshot
                     WHERE snapshot_at=(SELECT t FROM latest)),
             ours AS (SELECT item_id, COUNT(*) n FROM unit WHERE location_site_id='3PL-RNO' AND status IN ('AT_3PL','ALLOCATED')
                      GROUP BY item_id)
        SELECT w.item_id, w.wms_qty, COALESCE(o.n,0) AS ours FROM wms w LEFT JOIN ours o USING(item_id)
        WHERE w.wms_qty <> COALESCE(o.n,0)""", "Logistics"),
]


def register(conn):
    for c in CONTRACTS:
        conn.execute("INSERT OR REPLACE INTO data_contract VALUES (?,?,?,?,?,?,?,?)", c)


def run_one(conn, contract_id, store=False):
    row = conn.execute("SELECT * FROM data_contract WHERE contract_id=?", (contract_id,)).fetchone()
    if row is None:
        register(conn)
        row = conn.execute("SELECT * FROM data_contract WHERE contract_id=?", (contract_id,)).fetchone()
    t0 = time.perf_counter()
    rows = conn.execute(row["check_sql"]).fetchall()
    ms = (time.perf_counter() - t0) * 1000
    result = {"contract_id": contract_id, "violations": len(rows), "sample": rows[:25], "ms": round(ms, 1)}
    if store:
        conn.execute("INSERT INTO contract_run(contract_id, ran_at, violations, sample_json, duration_ms) VALUES (?,?,?,?,?)",
                     (contract_id, get_now(conn), len(rows), json.dumps(rows[:25], default=str), round(ms, 1)))
    return result


def run_all(conn, store=True):
    if store:
        register(conn)
    return [run_one(conn, c[0], store=store) for c in CONTRACTS]


def after_action(conn):
    """Every closed-loop action ends by re-running the contracts, so what they report is never older than the data."""
    return run_all(conn, store=True)


def register_and_run(conn):
    return run_all(conn, store=True)
