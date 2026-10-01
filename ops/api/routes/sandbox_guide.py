"""The data dictionary and use cases behind the Data Sandbox.

GRAIN says what one row of each table is, which is the first thing to know before joining it: genealogy is one row
per parent-contains-child edge, not one row per vehicle. ROW_KEY and NUMBERED say which columns make a row unique
where the schema's primary key is only a row number. USE_CASES are the questions the operation asks, each with the
tables it walks through in order, how each step joins to the last, and a read-only query that answers it. Tests check
that every table has a grain and a row key that holds, and that every use-case query runs.
"""
from ops.api.router import get

GRAIN = {
    # landing: exactly as received
    "raw_cm_mes_event": "One message the CM's MES sent (a station scan, test result or install), exactly as received, with its ingest status.",
    "raw_supplier_asn": "One advance ship notice from a supplier: the header and the serials it lists, as received.",
    "raw_supplier_confirmation": "One PO confirmation from a supplier (EDI 855, portal or email), as received.",
    "raw_email": "One email that arrived in an operations mailbox, with the full message.",
    "raw_attachment": "One file attached to an email, usually a supplier's or the CM's Excel workbook.",
    "raw_3pl_message": "One message from the 3PL: a receipt, an allocation, a ship confirm or an inventory snapshot.",
    "raw_carrier_event": "One tracking event from a carrier (EDI 315 or API), as received.",
    "raw_warranty_case": "One warranty case from the service CRM, as it arrived by webhook.",
    "ingest_run": "One run of a normalizer over a feed: how many messages came in, loaded, warned or went to quarantine.",
    "ingest_step": "One step one inbound document went through (received, classified, extracted, parsed, validated, mapped, loaded, reconciled).",
    "mapping_version": "One version of a feed's field mapping, stored as data so a partner's change is a new row, not new code.",
    # master data
    "site": "One physical place: a CM plant, the OEM's pack plant, a port, the 3PL warehouse or a supplier's plant.",
    "supplier": "One supplier company at tier 1, 2 or 3, pointing at its parent for sub-tiers.",
    "item": "One part number: a kit, vehicle, pack, module, component or raw material.",
    "supplier_item": "One supplier approved to supply one item.",
    "price": "One contract price for one supplier and item, valid between two dates.",
    "bom_line": "One parent-child line in a bill of materials: this parent uses this many of that child, between two dates.",
    "eco": "One engineering change: an old part replaced by a new one from a cut-in date.",
    "station": "One work station on a line, such as S60 end-of-line test on CM line 1.",
    "carrier": "One transport carrier (ocean line, DG trucker or parcel carrier).",
    "customer": "One customer, a direct buyer or a fleet.",
    "defect_code": "One defect code in the quality catalog, with the item it applies to.",
    "gl_account": "One general-ledger account.",
    "source_system": "One system that feeds the platform, with its owner, channel and latency.",
    "integration_option": "One way of integrating one source system (API, EDI, SFTP, portal, email), with its costs and status.",
    "line_capacity": "One line's rated capacity from a date on: shifts, takt, units per day and target OEE.",
    "time_fence": "One site's planning fences: how many days are frozen and how many slushy.",
    "fx_rate": "One currency's exchange rate on one day.",
    "freight_rate": "One carrier rate for a lane and mode, valid between two dates.",
    "replenishment_policy": "How one item is replenished at one site: policy, safety stock, reorder point, min and max.",
    # make
    "work_order": "One work order: one item on one line for one day, with planned, completed and scrapped quantities.",
    "unit": "One serialized physical thing (a vehicle, pack, module or serialized component), with its status and where it is.",
    "lot": "One lot of a lot-controlled item received from a supplier: quantity, receipt date and inspection status.",
    "lot_link": "This lot was made from that lot, from supplier certificates (lithium lot to cathode batch to cell lot).",
    "genealogy": "One as-built link: a parent serial contains one serialized child, or a quantity drawn from one lot (say 40 cells of lot CL2604-101: one row, not 40). Rows count links, not parts or levels: a complete kit (a vehicle and its pack) is 19 rows, 3 levels deep.",
    "station_event": "One thing that happened to one serial at one station (a test pass or fail, a scan), normalized from the MES.",
    "downtime_event": "One stoppage on a line (changeover, equipment, material, quality), with start and end.",
    "cm_output_report": "What the CM's daily Excel says one line built of one item on one day.",
    "cm_stock_report": "What the CM's daily Excel says it holds of one item on one day.",
    "control_plan": "One check in the control plan: what is inspected, where, how often, and the reaction if it fails.",
    # plan and promise
    "customer_order": "One customer order with its status, current promise and first promise.",
    "order_line": "One line of an order; for a kit, the vehicle serial and pack serial allocated to it.",
    "order_promise": "One promise date given for an order, with the reason and what it was pegged to. Append-only history.",
    "demand_forecast": "Forecast units for one item in one week, in one forecast version.",
    "build_plan": "Planned units of one item on one line on one day, in one plan version (the CM commit or the pack MPS).",
    "mrp_run": "One MRP run: when it ran, why, and its horizon.",
    "mrp_message": "One action message from an MRP run (shortage, expedite, defer, release) for one item on one day.",
    "planned_order": "One order MRP proposes: item, quantity, release date and due date.",
    "forecast_release": "One forecast we released to suppliers, as a dated version.",
    "forecast_line": "Forecast units sent to one supplier for one item and week, in one release.",
    "supplier_commit": "What one supplier committed against one forecast line.",
    # source
    "purchase_order": "One purchase order header: supplier, ship-to site and dates.",
    "po_line": "One PO line: item, quantity, price, need date, the supplier's current promise and confirmation status.",
    "po_promise_history": "One promise date a supplier gave for a PO line, with where it came from (the EDI segment, email or Excel row).",
    "goods_receipt": "One receipt of a PO line into a site: quantity and the lot it created.",
    "supplier_invoice": "One supplier invoice against one PO line, with its three-way match and pay status.",
    "rfq": "One request for quotation for an item.",
    "rfq_quote": "One supplier's quote on one RFQ.",
    "supplier_scorecard": "One supplier's performance in one week.",
    # move and inventory
    "shipment": "One physical move: an ocean container, a DG truck of packs, a supplier truck or a last-mile parcel.",
    "shipment_unit": "One serial inside one shipment.",
    "shipment_event": "One milestone of one shipment (booked, loaded, departed, ETA change, arrived, customs, received).",
    "customs_entry": "One customs entry for one shipment: ISF, duty and exam status.",
    "inventory_balance": "Stock of one item (and lot) at one site, by owner and status, for items not tracked by serial.",
    "wms_snapshot": "The 3PL's own count of one item at one moment, for reconciling against ours.",
    # quality
    "quality_event": "One quality event: an incoming inspection, an in-line failure or an audit.",
    "deviation": "One approved deviation: permission to use a part outside spec, until a date and a quantity.",
    "capa": "One corrective action (an 8D) with its stage, owner and due date.",
    "warranty_claim": "One field claim: the vehicle, the failed part, lot and supplier, the cost, and whether it has been charged back.",
    "hold": "One hold on one serial or one lot: where, why, when placed and released, and by which decision.",
    # action
    "ops_exception": "One problem a rule raised, such as 'BMS-B runs out Sep 30'. It resolves itself when the condition clears.",
    "decision_log": "One decision, proposed or executed: its evidence, the action, the impact, what it wrote and what it achieved.",
    "outbound_message": "One message the platform sent or queued to another system or a customer.",
    "chargeback": "One chargeback to a supplier: amount, status and the journal entry that booked it.",
    "chargeback_line": "One line of a chargeback: one claim, or one cost such as inspection.",
    "erp_journal_entry": "One journal entry (the header).",
    "erp_journal_line": "One debit or credit line of a journal entry.",
    # platform
    "process_activity": "One process step, classified as value, control, glue or wait.",
    "process_event": "One step of one case of a process as it actually ran (the process-mining log).",
    "app_telemetry": "One use of a feature in a thin app: who, what and how long.",
    "capability": "One capability a platform could provide, with the weight the team assumed before measuring.",
    "vendor": "One candidate platform vendor.",
    "vendor_capability": "How well one vendor covers one capability (0 to 3).",
    "data_contract": "One data contract: SQL that returns the rows breaking a rule. No rows means it passes.",
    "contract_run": "One run of one contract: when, and how many rows broke it.",
    "eval_suite": "One eval suite: the code it scores, the metric and the threshold to pass.",
    "eval_golden": "One golden case: the right answer for one input, from the simulator's truth or built by hand.",
    "eval_run": "One run of one eval suite: the version tested, cases passed, score, gate and the previous score it is compared with.",
    "eval_case": "One case in one eval run: expected against actual, pass or fail.",
    "test_run": "One run of the test suite: tests, failures and errors.",
    "change_review": "One change to a mapping, rule or logic, with the evidence that let it deploy and the reviewer.",
    "meta": "One dataset setting, such as the as-of date, the seed or the schema version.",
    # views
    "v_genealogy_active": "One as-built edge that is still in place (removed parts left out).",
    "v_po_line_status": "One PO line with how late it is against need and how many times its promise changed.",
    "v_inventory_position": "One item at one site for one owner and status, counting serials and balances together.",
    "v_fpy_daily": "One station on one day, with its first-pass yield.",
}

# Which columns make one row unique, for the tables whose primary key is only a row number (a natural primary key or a
# UNIQUE constraint says it in the schema, and the Sandbox reads it from there). 'a|b' is whichever of the two is
# filled. test_system checks every key on the built data and again after every closed loop has run.
ROW_KEY = {
    "genealogy": ("parent_serial", "position", "child_serial|child_lot_id", "installed_at"),
    "station_event": ("serial", "station_id", "event_ts", "result"),
    "downtime_event": ("site_id", "line", "station_id", "start_ts", "reason"),
    "inventory_balance": ("site_id", "item_id", "lot_id", "owner", "stock_status", "as_of"),
    "price": ("item_id", "supplier_id", "min_qty", "eff_from"),
    "po_promise_history": ("po_id", "line_no", "recorded_at", "channel"),
    "freight_rate": ("carrier", "lane", "basis", "valid_from"),
    "shipment_event": ("shipment_id", "event_ts", "code"),
    "order_promise": ("order_id", "decided_at", "decision_id"),
    "mrp_message": ("run_id", "item_id", "site_id", "message", "bucket_date", "ref"),
    "outbound_message": ("target_system", "message_type", "ref", "decision_id", "created_at"),
    "process_event": ("process", "case_id", "activity", "ts"),
    "raw_attachment": ("raw_id", "filename"),
    "v_genealogy_active": ("parent_serial", "position", "child_serial|child_lot_id"),
    "v_fpy_daily": ("station_id", "day"),
    "v_inventory_position": ("site_id", "item_id", "bucket", "owner"),
    "v_po_line_status": ("po_id", "line_no"),
}

# Where the row number is the identity: every delivery, run or use counts, even two alike at the same moment.
_LANDED = "every message lands exactly as it was sent, retries included, and the loaders deduplicate downstream"
_RUN = ("two runs of the same thing at the same moment are still two runs, and in this app they happen, because its "
        "clock is the dataset's and stands still")
NUMBERED = {
    "raw_3pl_message": _LANDED, "raw_carrier_event": _LANDED, "raw_cm_mes_event": _LANDED, "raw_supplier_asn": _LANDED,
    "raw_supplier_confirmation": _LANDED, "raw_warranty_case": _LANDED,
    "contract_run": _RUN, "eval_run": _RUN, "test_run": _RUN, "ingest_run": _RUN, "mrp_run": _RUN,
    "ingest_step": "this is a trace log, and a message processed again (quarantined, then replayed) writes its steps again",
    "app_telemetry": "an anonymous usage log, so the same role using the same feature in the same second is two uses",
}

# Each step names a table, how it joins to the step before, and what it contributes to the answer.
USE_CASES = [
    {
        "id": "material-bottleneck", "category": "Plan",
        "title": "Material bottleneck: what will stop a line",
        "question": "Which part runs out first, on what day, and what supply could close the gap?",
        "page": "#/mrp?item=BMS-B",
        "path": [
            ("mrp_run", "start here", "the latest MRP run"),
            ("mrp_message", "run_id", "the SHORTAGE messages: item, the day it runs out, units short"),
            ("item", "item_id", "lead time: a shortage inside lead time can't be fixed by a new order"),
            ("inventory_balance", "item_id", "what is on hand and available now"),
            ("po_line", "item_id", "open supply for the item: need date and the supplier's promise"),
            ("purchase_order", "po_id", "the PO header"),
            ("supplier", "supplier_id", "who owes the parts"),
        ],
        "sql": """-- Material bottleneck: shortages MRP found inside lead time, with the open supply that could close them
WITH latest AS (SELECT MAX(run_id) AS run_id FROM mrp_run)
SELECT m.item_id, i.name, m.bucket_date AS runs_out, m.qty AS short_by, i.lead_time_days,
       (SELECT COALESCE(SUM(b.qty), 0) FROM inventory_balance b
         WHERE b.item_id = m.item_id AND b.stock_status = 'AVAILABLE') AS on_hand_available,
       pl.po_id || '-' || pl.line_no AS open_po_line, pl.qty AS po_qty, pl.need_date,
       pl.promise_date, pl.confirm_status, s.name AS supplier
FROM mrp_message m
JOIN latest l ON l.run_id = m.run_id
JOIN item i ON i.item_id = m.item_id
LEFT JOIN po_line pl ON pl.item_id = m.item_id AND pl.status = 'OPEN'
LEFT JOIN purchase_order po ON po.po_id = pl.po_id
LEFT JOIN supplier s ON s.supplier_id = po.supplier_id
WHERE m.message = 'SHORTAGE'
ORDER BY m.bucket_date, pl.promise_date;""",
    },
    {
        "id": "capacity-bottleneck", "category": "Plan",
        "title": "Capacity bottleneck: which line is overloaded",
        "question": "Which line carries the most load against its rated capacity in the coming weeks?",
        "page": "#/schedule",
        "path": [
            ("build_plan", "start here", "planned units by line and day (the CM commit and the pack MPS)"),
            ("line_capacity", "site_id + line, effective date", "rated units per day in effect that week"),
            ("time_fence", "site_id", "how far out the plan is frozen"),
        ],
        "sql": """-- Capacity bottleneck: planned units against rated capacity, by line and week (next six weeks)
WITH today AS (SELECT value AS d FROM meta WHERE key = 'as_of'),
plan AS (
  SELECT b.site_id, b.line, date(b.plan_date, '-6 days', 'weekday 1') AS week,
         SUM(b.qty) AS planned, COUNT(DISTINCT b.plan_date) AS build_days
  FROM build_plan b, today
  WHERE b.plan_date >= today.d AND b.plan_date < date(today.d, '+42 days')
    AND b.version = (SELECT MAX(version) FROM build_plan x WHERE x.plan_type = b.plan_type)
  GROUP BY b.site_id, b.line, week
)
SELECT p.site_id, p.line, p.week, p.planned, p.build_days * c.rated_units_per_day AS capacity,
       ROUND(100.0 * p.planned / (p.build_days * c.rated_units_per_day), 1) AS load_pct,
       date((SELECT d FROM today), '+' || f.frozen_days || ' days') AS frozen_until
FROM plan p
JOIN line_capacity c ON c.site_id = p.site_id AND c.line = p.line
 AND c.effective_from = (SELECT MAX(z.effective_from) FROM line_capacity z
                          WHERE z.site_id = p.site_id AND z.line = p.line AND z.effective_from <= p.week)
JOIN time_fence f ON f.site_id = p.site_id
ORDER BY load_pct DESC, p.week
LIMIT 40;""",
    },
    {
        "id": "recall-scope", "category": "Quality",
        "title": "Recall scope: everything built from a bad batch",
        "question": "Starting from the lot with the most field claims, which units share its batch, and are they still ours to stop?",
        "page": "#/genealogy?q=CA2605-103",
        "path": [
            ("warranty_claim", "start here", "the lot with the most claims (failed_lot_id)"),
            ("lot_link", "child_lot_id, then parent_lot_id", "up to the cathode batch it was made from, then down to every cell lot from that batch"),
            ("lot", "lot_id", "the suspect lots"),
            ("genealogy", "child_lot_id, then child_serial to parent_serial, repeated",
             "walk up the as-built tree: cells into packs, packs into the kits they ship with"),
            ("unit", "serial", "each unit found, with status and location: in our control or with a customer"),
            ("order_line", "vehicle_serial or pack_serial", "the order each unit went into"),
        ],
        "sql": """-- Recall scope: every unit built from the same cathode batch as the worst lot, by who holds it now
WITH RECURSIVE worst AS (
  SELECT failed_lot_id AS lot_id FROM warranty_claim
  WHERE failed_lot_id IS NOT NULL
  GROUP BY failed_lot_id ORDER BY COUNT(*) DESC LIMIT 1
),
lots AS (   -- every cell lot made from the worst lot's cathode batch (supplier certificates)
  SELECT k.child_lot_id AS lot_id FROM lot_link p JOIN lot_link k ON k.parent_lot_id = p.parent_lot_id
  WHERE p.child_lot_id = (SELECT lot_id FROM worst)
  UNION SELECT lot_id FROM worst
),
up(serial) AS (
  SELECT g.parent_serial FROM genealogy g JOIN lots ON g.child_lot_id = lots.lot_id WHERE g.removed_at IS NULL
  UNION
  SELECT g.parent_serial FROM genealogy g JOIN up ON g.child_serial = up.serial WHERE g.removed_at IS NULL
)
SELECT CASE WHEN u.status IN ('SHIPPED', 'DELIVERED') THEN 'with customer'
            WHEN u.status = 'SCRAPPED' THEN 'scrapped' ELSE 'in our control' END AS reach,
       i.kind, u.status, COALESCE(u.location_site_id, '—') AS location, COUNT(*) AS units,
       COUNT(DISTINCT ol.order_id) AS orders
FROM up JOIN unit u ON u.serial = up.serial JOIN item i ON i.item_id = u.item_id
LEFT JOIN order_line ol ON ol.vehicle_serial = u.serial OR ol.pack_serial = u.serial
WHERE i.kind IN ('PACK', 'VEHICLE')
GROUP BY reach, i.kind, u.status, location
ORDER BY reach, units DESC;""",
    },
    {
        "id": "as-built", "category": "Quality",
        "title": "As-built tree of one vehicle",
        "question": "What exactly is inside this vehicle: which modules, which serials, which lots, from which suppliers?",
        "page": "#/genealogy",
        "path": [
            ("unit", "start here", "one delivered vehicle"),
            ("genealogy", "parent_serial, then child_serial to parent_serial, repeated", "walk down the tree"),
            ("item", "child_item_id", "what each part is"),
            ("lot", "child_lot_id", "for lot-controlled parts, the lot"),
            ("supplier", "lot.supplier_id", "who made it"),
        ],
        "sql": """-- As-built tree: everything inside one delivered vehicle, level by level
WITH RECURSIVE root AS (
  SELECT serial FROM unit WHERE item_id LIKE 'LV1-%' AND status = 'DELIVERED' ORDER BY serial LIMIT 1
),
tree(serial, lot_id, item_id, qty, depth, path) AS (
  SELECT g.child_serial, g.child_lot_id, g.child_item_id, g.qty, 1, (SELECT serial FROM root)
  FROM genealogy g WHERE g.parent_serial = (SELECT serial FROM root) AND g.removed_at IS NULL
  UNION ALL
  SELECT g.child_serial, g.child_lot_id, g.child_item_id, g.qty, t.depth + 1, t.path || ' > ' || t.serial
  FROM genealogy g JOIN tree t ON g.parent_serial = t.serial
  WHERE t.serial IS NOT NULL AND g.removed_at IS NULL
)
SELECT t.depth, t.item_id, i.name, i.kind, COALESCE(t.serial, 'lot ' || t.lot_id) AS serial_or_lot, t.qty,
       s.name AS supplier
FROM tree t JOIN item i ON i.item_id = t.item_id
LEFT JOIN lot l ON l.lot_id = t.lot_id
LEFT JOIN supplier s ON s.supplier_id = COALESCE(l.supplier_id, i.primary_supplier_id)
ORDER BY t.depth, t.item_id;""",
    },
    {
        "id": "promise-risk", "category": "Plan",
        "title": "Promises at risk: orders riding on a late container",
        "question": "Which containers are late, and how many customer promises are pegged to them?",
        "page": "#/atp?tab=risk",
        "path": [
            ("shipment", "start here", "ocean containers not yet arrived whose ETA moved"),
            ("shipment_unit", "shipment_id", "the vehicles aboard"),
            ("order_promise", "pegged_to names the container", "the promises that depend on it"),
            ("customer_order", "order_id", "the orders and their current promise"),
        ],
        "sql": """-- Promises at risk: late containers and the customer promises pegged to them
SELECT s.shipment_id, s.vessel, s.voyage, substr(s.eta_planned, 1, 10) AS eta_planned,
       substr(s.eta_current, 1, 10) AS eta_now,
       CAST(julianday(s.eta_current) - julianday(s.eta_planned) AS INTEGER) AS days_late,
       (SELECT COUNT(*) FROM shipment_unit su WHERE su.shipment_id = s.shipment_id) AS vehicles_aboard,
       (SELECT COUNT(DISTINCT p.order_id) FROM order_promise p
         WHERE p.pegged_to LIKE s.shipment_id || ' %') AS orders_pegged
FROM shipment s
WHERE s.leg = 'CM_TO_3PL' AND s.ata IS NULL AND s.eta_current > s.eta_planned
ORDER BY days_late DESC, s.shipment_id;""",
    },
    {
        "id": "supplier-reliability", "category": "Source",
        "title": "Supplier reliability: promise slips and late confirmations",
        "question": "Which suppliers move their promise dates, by how much, and where did each promise come from?",
        "page": "#/suppliers",
        "path": [
            ("supplier", "start here", "each supplier"),
            ("purchase_order", "supplier_id", "their POs"),
            ("po_line", "po_id", "each line: need date and current promise"),
            ("po_promise_history", "po_id + line_no", "every promise they gave, and its source (EDI, email, Excel)"),
        ],
        "sql": """-- Supplier reliability: how often and how far each supplier's promise dates moved
SELECT s.name AS supplier, COUNT(DISTINCT pl.po_id || '-' || pl.line_no) AS po_lines,
       COUNT(h.id) AS promises_given,
       SUM(pl.confirm_status = 'UNCONFIRMED' AND pl.status = 'OPEN') AS open_unconfirmed,
       ROUND(AVG(julianday(pl.promise_date) - julianday(pl.need_date)), 1) AS avg_days_late_vs_need
FROM supplier s
JOIN purchase_order po ON po.supplier_id = s.supplier_id
JOIN po_line pl ON pl.po_id = po.po_id
LEFT JOIN po_promise_history h ON h.po_id = pl.po_id AND h.line_no = pl.line_no
GROUP BY s.supplier_id
ORDER BY avg_days_late_vs_need DESC;""",
    },
    {
        "id": "three-way-match", "category": "Source",
        "title": "Three-way match: which invoices are held and why",
        "question": "For each invoice that didn't match, what did we order, receive and get billed?",
        "page": "#/erp?tab=ap",
        "path": [
            ("supplier_invoice", "start here", "invoices whose match failed"),
            ("po_line", "po_id + line_no", "what we ordered, at what price"),
            ("goods_receipt", "po_id + line_no", "what we received"),
            ("price", "po_line.price_id", "the contract price behind the PO"),
            ("supplier", "supplier_id", "who billed us"),
        ],
        "sql": """-- Three-way match: invoices that failed, against the PO line and what was received
SELECT i.invoice_id, s.name AS supplier, i.po_id || '-' || i.line_no AS po_line, pl.item_id,
       pl.qty AS ordered, pl.unit_price AS po_price,
       (SELECT COALESCE(SUM(g.qty), 0) FROM goods_receipt g
         WHERE g.po_id = i.po_id AND g.line_no = i.line_no) AS received,
       i.qty AS invoiced, i.unit_price AS invoice_price, i.match_status, i.pay_status, i.variance_usd
FROM supplier_invoice i
JOIN po_line pl ON pl.po_id = i.po_id AND pl.line_no = i.line_no
JOIN supplier s ON s.supplier_id = i.supplier_id
WHERE i.match_status <> 'MATCHED'
ORDER BY i.variance_usd DESC;""",
    },
    {
        "id": "claim-to-ledger", "category": "Finance",
        "title": "Warranty to ledger: a defect becomes a debit memo",
        "question": "Which claims were billed to which supplier, and where did each chargeback land in the books?",
        "page": "#/warranty",
        "path": [
            ("warranty_claim", "start here", "field claims with a supplier"),
            ("chargeback_line", "ref_id = claim_id", "the line that bills the claim"),
            ("chargeback", "chargeback_id", "the chargeback and its status"),
            ("erp_journal_entry", "je_id", "the debit memo, once posted"),
            ("erp_journal_line", "je_id", "debit Accounts payable, credit the cost it recovers"),
            ("gl_account", "gl_account", "the account names"),
        ],
        "sql": """-- Warranty to ledger: each chargeback, the claims on it, and the journal lines it posted
SELECT cb.chargeback_id, s.name AS supplier, cb.status, cb.amount_usd,
       (SELECT COUNT(*) FROM chargeback_line l
         WHERE l.chargeback_id = cb.chargeback_id AND l.ref_type = 'CLAIM') AS claims_billed,
       cb.je_id, jl.gl_account, a.name AS account, jl.debit_usd, jl.credit_usd
FROM chargeback cb
JOIN supplier s ON s.supplier_id = cb.supplier_id
LEFT JOIN erp_journal_line jl ON jl.je_id = cb.je_id
LEFT JOIN gl_account a ON a.gl_account = jl.gl_account
ORDER BY cb.chargeback_id, jl.line_no;""",
    },
    {
        "id": "cm-lineage", "category": "Data",
        "title": "Lineage: from the CM's raw message to a canonical event",
        "question": "For a station event, which raw message did it come from, under which mapping, and what happened to messages that didn't fit?",
        "page": "#/cm-feed",
        "path": [
            ("raw_cm_mes_event", "start here", "the payload exactly as the CM sent it, with its ingest status"),
            ("mapping_version", "the version in force when it arrived", "how its fields were read"),
            ("station_event", "raw_id", "the canonical event it became"),
            ("unit", "serial", "the vehicle it happened to"),
            ("station", "station_id", "where"),
        ],
        "sql": """-- Lineage: raw CM messages by ingest outcome, and how many became canonical station events
WITH linked AS (SELECT DISTINCT raw_id FROM station_event WHERE raw_id IS NOT NULL)
SELECT r.ingest_status, COUNT(*) AS messages, MIN(r.received_at) AS first_seen, MAX(r.received_at) AS last_seen,
       SUM(l.raw_id IS NOT NULL) AS became_station_events, MAX(r.ingest_note) AS example_note
FROM raw_cm_mes_event r
LEFT JOIN linked l ON l.raw_id = r.raw_id
GROUP BY r.ingest_status
ORDER BY messages DESC;""",
    },
    {
        "id": "short-shipment", "category": "Move",
        "title": "Short shipment: serials on the ASN that never arrived",
        "question": "Which containers were received without all the vehicles the CM said it loaded?",
        "page": "#/shipments",
        "path": [
            ("shipment", "start here", "containers received at the 3PL"),
            ("shipment_unit", "shipment_id", "the serials on the CM's ASN"),
            ("unit", "serial", "a serial still in transit after its container was received never arrived"),
        ],
        "sql": """-- Short shipment: serials listed on a received container that the 3PL never scanned in
SELECT s.shipment_id, s.vessel, substr(s.received_at, 1, 10) AS received, su.serial, u.item_id, u.status,
       u.location_site_id AS last_known_at
FROM shipment s
JOIN shipment_unit su ON su.shipment_id = s.shipment_id
JOIN unit u ON u.serial = su.serial
WHERE s.leg = 'CM_TO_3PL' AND s.received_at IS NOT NULL AND u.status = 'IN_TRANSIT'
ORDER BY s.shipment_id, su.serial;""",
    },
    {
        "id": "audit-trail", "category": "Data",
        "title": "Closed-loop audit trail: every row a decision wrote",
        "question": "For each decision, what did it change: holds, messages, promises, chargebacks and journal entries?",
        "page": "#/loop",
        "path": [
            ("ops_exception", "start here", "the problem a rule raised"),
            ("decision_log", "decision_id", "the decision that answered it"),
            ("hold", "decision_id", "holds it placed"),
            ("outbound_message", "decision_id", "messages it sent"),
            ("order_promise", "decision_id", "promises it moved"),
            ("chargeback", "decision_id", "chargebacks it drafted"),
        ],
        "sql": """-- Closed-loop audit trail: each decision and every row it wrote
SELECT d.decision_id, d.loop, d.status, d.title,
       (SELECT COUNT(*) FROM hold h WHERE h.decision_id = d.decision_id) AS holds,
       (SELECT COUNT(*) FROM outbound_message m WHERE m.decision_id = d.decision_id) AS messages,
       (SELECT COUNT(*) FROM order_promise p WHERE p.decision_id = d.decision_id) AS promises_moved,
       (SELECT GROUP_CONCAT(c.chargeback_id) FROM chargeback c WHERE c.decision_id = d.decision_id) AS chargebacks,
       (SELECT GROUP_CONCAT(e.exception_id) FROM ops_exception e WHERE e.decision_id = d.decision_id) AS answered
FROM decision_log d
ORDER BY COALESCE(d.executed_at, d.proposed_at) DESC, d.decision_id DESC;""",
    },
]


@get(r"^/api/sandbox/use-cases$")
def use_cases(req):
    return {"use_cases": [dict(u, path=[{"table": t, "join": j, "why": w, "grain": GRAIN.get(t)} for t, j, w in u["path"]])
                          for u in USE_CASES]}
