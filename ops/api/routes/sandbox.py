"""Data Sandbox API: browse every table, its relationships and rows, and run
read-only SQL against the live database.

The SQL runner uses a separate read-only connection (mode=ro + query_only),
accepts only SELECT / WITH / EXPLAIN, stops after 3 seconds and returns at
most 1000 rows. Table and column names in the browse endpoints are validated
against sqlite_master / PRAGMA table_info before they reach any SQL string.
"""
import re
import sqlite3
import time

from ..router import HttpError, get, post
from ...db import connect, q
from .sandbox_guide import GRAIN, NUMBERED, ROW_KEY

# ---------------------------------------------------------------------------
# classification: layer + domain for every table
# ---------------------------------------------------------------------------

LAYER_DOMAIN = {
    # LANDING: raw payloads as received + pipeline audit
    "raw_cm_mes_event": ("LANDING", "landing"),
    "raw_supplier_asn": ("LANDING", "landing"),
    "raw_carrier_event": ("LANDING", "landing"),
    "raw_3pl_message": ("LANDING", "landing"),
    "raw_supplier_confirmation": ("LANDING", "landing"),
    "raw_warranty_case": ("LANDING", "landing"),
    "mapping_version": ("LANDING", "landing"),
    "ingest_run": ("LANDING", "landing"),
    "raw_email": ("LANDING", "landing"),
    "raw_attachment": ("LANDING", "landing"),
    "ingest_step": ("LANDING", "landing"),
    # CORE
    "site": ("CORE", "master"), "supplier": ("CORE", "master"), "item": ("CORE", "master"),
    "supplier_item": ("CORE", "master"), "eco": ("CORE", "master"), "bom_line": ("CORE", "master"),
    "price": ("CORE", "master"), "station": ("CORE", "master"), "defect_code": ("CORE", "master"),
    "customer": ("CORE", "master"), "gl_account": ("CORE", "master"),
    "fx_rate": ("CORE", "master"), "carrier": ("CORE", "master"),
    "source_system": ("CORE", "master"), "integration_option": ("CORE", "master"),
    "unit": ("CORE", "make"), "lot": ("CORE", "make"), "genealogy": ("CORE", "make"),
    "station_event": ("CORE", "make"),
    "work_order": ("CORE", "make"), "line_capacity": ("CORE", "make"), "cm_output_report": ("CORE", "make"),
    "time_fence": ("CORE", "make"), "downtime_event": ("CORE", "make"),
    "customer_order": ("CORE", "plan"), "order_line": ("CORE", "plan"), "demand_forecast": ("CORE", "plan"),
    "build_plan": ("CORE", "plan"), "forecast_release": ("CORE", "plan"), "forecast_line": ("CORE", "plan"),
    "supplier_commit": ("CORE", "plan"), "replenishment_policy": ("CORE", "plan"),
    "purchase_order": ("CORE", "source"), "po_line": ("CORE", "source"), "po_promise_history": ("CORE", "source"),
    "rfq": ("CORE", "source"), "rfq_quote": ("CORE", "source"),
    "goods_receipt": ("CORE", "source"), "supplier_invoice": ("CORE", "source"),
    "shipment": ("CORE", "move"), "shipment_unit": ("CORE", "move"), "shipment_event": ("CORE", "move"),
    "customs_entry": ("CORE", "move"), "freight_rate": ("CORE", "move"),
    "inventory_balance": ("CORE", "inventory"), "wms_snapshot": ("CORE", "inventory"),
    "cm_stock_report": ("CORE", "inventory"),
    "deviation": ("CORE", "quality"), "quality_event": ("CORE", "quality"), "warranty_claim": ("CORE", "quality"),
    "control_plan": ("CORE", "quality"), "capa": ("CORE", "quality"),
    # ACTION: what the system decided and every write it made
    "decision_log": ("ACTION", "decide"), "ops_exception": ("ACTION", "decide"), "hold": ("ACTION", "decide"),
    "outbound_message": ("ACTION", "decide"), "order_promise": ("ACTION", "decide"),
    "supplier_scorecard": ("ACTION", "decide"),
    "mrp_run": ("ACTION", "plan"), "planned_order": ("ACTION", "plan"), "mrp_message": ("ACTION", "plan"),
    "chargeback": ("ACTION", "finance"), "chargeback_line": ("ACTION", "finance"),
    "erp_journal_entry": ("ACTION", "finance"), "erp_journal_line": ("ACTION", "finance"),
    # PLATFORM
    "process_activity": ("PLATFORM", "platform"), "process_event": ("PLATFORM", "platform"),
    "app_telemetry": ("PLATFORM", "platform"), "capability": ("PLATFORM", "platform"),
    "vendor": ("PLATFORM", "platform"), "vendor_capability": ("PLATFORM", "platform"),
    "meta": ("PLATFORM", "platform"),
    # PLATFORM / ASSURANCE: tests, evals, contracts and review
    "eval_suite": ("PLATFORM", "assurance"), "eval_golden": ("PLATFORM", "assurance"),
    "eval_run": ("PLATFORM", "assurance"), "eval_case": ("PLATFORM", "assurance"),
    "test_run": ("PLATFORM", "assurance"), "change_review": ("PLATFORM", "assurance"),
    "data_contract": ("PLATFORM", "assurance"), "contract_run": ("PLATFORM", "assurance"),
}

DESCRIPTIONS = {
    "meta": "Dataset clock and build info: as_of, now_utc, seed.",
    "site": "Every physical place: CM plants, the OEM's pack line, supplier sites, ports and the 3PL.",
    "supplier": "Suppliers across tiers 1-3 (the CM included), who each sells into, and contractual recovery terms.",
    "item": "Part master: sellable kits, vehicles, packs, modules, components and tier-2/3 materials with make/buy, lead times and cost.",
    "supplier_item": "Approved vendor list: which suppliers are primary, alternate or qualifying for each item.",
    "eco": "Engineering change orders with date, serial or lot effectivity and stock disposition.",
    "bom_line": "Multi-level BOM with effectivity: the OEM's, the CM's and suppliers' BOMs down to tier-3 materials.",
    "price": "Component pricing with price breaks and effective-dated windows.",
    "station": "Assembly, test and pack stations on each line with standard cycle time and parallel fixtures, keyed by the plant MES code.",
    "defect_code": "Defect taxonomy with category and default responsible party.",
    "customer": "Customers (consumers and fleets) with ship-to region.",
    "gl_account": "General-ledger accounts that supplier recovery postings hit.",
    "fx_rate": "Daily FX rates (USD per unit of currency) used to land foreign-currency POs and invoices.",
    "carrier": "Carriers by mode with SCAC and how each integrates: EDI 315, API, email or portal.",
    "source_system": "Every system the platform reads or writes (MES, QMS, ERP, TMS, WMS, portals) with owner, channel and landing table.",
    "integration_option": "Integration options per system (API, EDI, SFTP, email + Excel) scored on latency, manual touches, errors, build and run cost.",
    "work_order": "Work orders per line: planned, started, completed and scrapped quantities by scheduled date.",
    "line_capacity": "Line capacity by effective date: shifts, takt time, rated units per day and OEE target.",
    "time_fence": "Planning time fences per site: the frozen and slushy horizons the MPS respects.",
    "downtime_event": "Line and station downtime with category and reason, from the plant MES or CM reports.",
    "cm_output_report": "What the CM's daily Excel report says it built (parsed from email), kept beside its MES events so the two can be reconciled.",
    "cm_stock_report": "The CM's daily stock report (parsed from email Excel): on hand and on QC hold, for reconciliation against our records.",
    "goods_receipt": "ERP goods receipts against PO lines, with the lot created at receipt.",
    "supplier_invoice": "Supplier invoices with three-way match status (PO, receipt, invoice), variance and payment status.",
    "replenishment_policy": "Replenishment policy per item and site (MRP, reorder point, min-max, VMI, consignment) with service level, safety stock and lot sizing.",
    "freight_rate": "Contracted freight rates by carrier, lane and basis, with validity windows.",
    "control_plan": "Control plan: characteristic, spec limits, method, frequency and reaction plan per station.",
    "capa": "Corrective and preventive actions (8D) linked to quality events and suppliers.",
    "raw_email": "Inbound emails exactly as received (full MIME), classified to the feed they belong to.",
    "raw_attachment": "Email attachments (Excel, CSV, PDF) with hash, parser used, and rows parsed vs loaded.",
    "ingest_step": "Step-by-step trace for every inbound item: received, classified, parsed, validated, mapped, loaded.",
    "unit": "Every serialized thing (vehicles, packs, drive units, displays, BMS boards) with its work order, current status and location.",
    "lot": "Lot-controlled receipts with supplier, PO line and incoming-inspection status.",
    "genealogy": "As-built and as-maintained edges: which serial or lot went into which parent, when and where.",
    "station_event": "Station-level pass/fail/rework events from the CM feed and the OEM's MES.",
    "customer_order": "Customer orders: channel, ship-to, status and the current promise date.",
    "order_line": "Order lines: the configured LV-1 kit (line 1), optional extra pack, and the serials allocated to them.",
    "demand_forecast": "Independent S&OP forecast for sellable items by week, consumed by orders in MRP.",
    "build_plan": "Daily build plan: CM commits for vehicles and the OEM's MPS for packs.",
    "forecast_release": "Each forecast released to suppliers (EDI 830-style), weekly.",
    "forecast_line": "Released forecast by supplier, item, tier and week, exploded down to tier-3 materials.",
    "supplier_commit": "Supplier commitments against each released forecast line.",
    "purchase_order": "Purchase order headers.",
    "po_line": "PO lines with need date, current promise date, confirmation status and receipts.",
    "po_promise_history": "Every promise date a supplier gave for a PO line, and through which channel.",
    "rfq": "Requests for quote, with award and rationale.",
    "rfq_quote": "Supplier quotes: price, MOQ, lead time, tooling and freight.",
    "shipment": "Every move (ocean containers, DG trucks, last-mile deliveries) with planned vs actual dates.",
    "shipment_unit": "Which serials rode in which shipment.",
    "shipment_event": "Track-and-trace milestones from carriers, the 3PL and the customs broker.",
    "customs_entry": "Import entries: ISF, HTS code, entered value, duty, MPF/HMF and exam status.",
    "inventory_balance": "Non-serialized stock by site, lot, owner and status, including supplier-held stock.",
    "wms_snapshot": "What the 3PL's WMS says it holds, kept for reconciliation against unit.",
    "deviation": "Approved deviations: what may be used out of spec, how many, and until when.",
    "quality_event": "Incoming, inline, end-of-line and field quality events with disposition and cost.",
    "warranty_claim": "Field claims traced to the failed serial, lot and responsible supplier.",
    "chargeback": "Supplier chargebacks, from draft through ERP posting.",
    "chargeback_line": "Evidence lines behind each chargeback: claims, quality events and costs.",
    "erp_journal_entry": "Journal entries posted to the ERP (debit memos, accruals).",
    "erp_journal_line": "Debit and credit lines of each journal entry.",
    "decision_log": "Every decision the system proposed or executed: rule, inputs, rationale, action and outcome.",
    "hold": "Quality and containment holds on serials and lots.",
    "outbound_message": "Every write the platform made to another system: ERP, WMS, CM MES, supplier portal, customer comms.",
    "ops_exception": "Detected exceptions with severity, impact, owner and the page to act on them.",
    "supplier_scorecard": "Weekly supplier OTD, PPM, responsiveness and recovery: the learn step that sourcing reads.",
    "order_promise": "Promise-date history per order, with the reason it moved.",
    "mrp_run": "Every MRP run kept: when, as-of, horizon, forecast version and what it produced.",
    "planned_order": "MRP planned orders (buy, CM build, OEM build, kit) with release and due dates.",
    "mrp_message": "MRP action messages: release, expedite, defer, cancel, shortage, below safety stock.",
    "mapping_version": "Versioned field mappings for each inbound source.",
    "ingest_run": "Audit of every ingest run: rows in, ok, warned, quarantined, duplicated.",
    "raw_cm_mes_event": "CM MES messages exactly as received (JSON), before normalization.",
    "raw_supplier_asn": "Supplier advance ship notices with child serials, as received.",
    "raw_carrier_event": "Carrier status messages (EDI 315 or JSON), as received.",
    "raw_3pl_message": "3PL receipts, allocations, ship confirms and inventory snapshots, as received.",
    "raw_supplier_confirmation": "Supplier PO confirmations by email, portal or EDI 855, as received.",
    "raw_warranty_case": "Customer-service warranty cases from the CRM, as received.",
    "process_activity": "Process steps classified as value, control, glue or wait.",
    "process_event": "Event log of how processes actually ran (the process-mining input).",
    "app_telemetry": "Instrumentation from the thin apps: who used which feature and how long it took.",
    "capability": "QMS, TMS and WMS capabilities, with the weight the team assumed before measuring.",
    "vendor": "Candidate platforms by domain with cost and implementation time.",
    "vendor_capability": "How well each vendor covers each capability (0-3).",
    "data_contract": "Data contracts: SQL that returns violating rows. Zero rows means pass.",
    "contract_run": "Results of every contract run.",
    "eval_suite": "Eval suites: which code is scored (normalizer, parser, ATP, MRP), by what metric, against what threshold.",
    "eval_golden": "Golden expectations per eval case, captured from the simulator's ground truth or built by hand.",
    "eval_run": "Every eval run: version under test, cases passed, score and whether it cleared its gate.",
    "eval_case": "Per-case eval results: expected vs actual, pass or fail.",
    "test_run": "Unit test runs: tests, failures, errors and per-test detail.",
    "change_review": "Every mapping, rule or logic change with its tests, evals, contract status and the reviewer's decision.",
    "v_genealogy_active": "View: active as-built edges only (removed components excluded).",
    "v_po_line_status": "View: PO lines with days late vs need and number of promise changes.",
    "v_inventory_position": "View: multi-echelon position, serialized units plus non-serialized balances.",
    "v_fpy_daily": "View: first-pass yield by station and day.",
}

NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAX_ROWS = 1000
SQL_TIMEOUT_S = 3.0


def classify(name, kind):
    if kind == "view":
        return "VIEW", "view"
    if name in LAYER_DOMAIN:
        return LAYER_DOMAIN[name]
    if name.startswith("raw_"):
        return "LANDING", "landing"
    return "CORE", "other"


def objects(conn):
    return q(conn, "SELECT name, type, sql FROM sqlite_master "
                   "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name")


def get_object(conn, name):
    if not NAME_RE.match(name or ""):
        raise HttpError(400, "invalid table name")
    for o in objects(conn):
        if o["name"] == name:
            return o
    raise HttpError(404, f"no table or view named {name!r}")


def guarded(conn, seconds):
    """Install a progress handler that aborts statements running past `seconds`."""
    deadline = time.perf_counter() + seconds
    conn.set_progress_handler(lambda: 1 if time.perf_counter() > deadline else 0, 5000)


def count_rows(conn, name, kind, seconds=0.4):
    guarded(conn, seconds if kind == "view" else 5.0)
    try:
        return conn.execute(f'SELECT COUNT(*) AS n FROM "{name}"').fetchone()["n"]
    except sqlite3.OperationalError:
        return None
    finally:
        conn.set_progress_handler(None, 0)


def cell(v):
    if isinstance(v, (bytes, bytearray)):
        return f"<blob {len(v)} bytes>"
    return v


# ---------------------------------------------------------------------------
# CREATE statement parsing (CHECK / UNIQUE per column, table constraints)
# ---------------------------------------------------------------------------

def _strip_comments(sql):
    out = []
    for line in sql.splitlines():
        in_q = False
        buf = []
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == "'":
                in_q = not in_q
            if not in_q and line.startswith("--", i):
                break
            buf.append(ch)
            i += 1
        out.append("".join(buf))
    return "\n".join(out)


def _split_top(body):
    parts, depth, cur, in_q = [], 0, [], False
    for ch in body:
        if ch == "'":
            in_q = not in_q
        if not in_q:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                parts.append("".join(cur).strip())
                cur = []
                continue
        cur.append(ch)
    tail = "".join(cur).strip()
    if tail:
        parts.append(tail)
    return parts


def _paren(s, start):
    depth, in_q = 0, False
    for i in range(start, len(s)):
        ch = s[i]
        if ch == "'":
            in_q = not in_q
        if in_q:
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return s[start + 1:i]
    return s[start + 1:]


def parse_create(sql):
    info = {"checks": {}, "unique": set(), "table_checks": [], "table_constraints": []}
    if not sql or "(" not in sql:
        return info
    sql = _strip_comments(sql)
    body = sql[sql.find("(") + 1: sql.rfind(")")]
    for part in _split_top(body):
        if not part:
            continue
        head = part.split(None, 1)[0].upper()
        if head in ("PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT"):
            if head == "CHECK":
                info["table_checks"].append(" ".join(_paren(part, part.find("(")).split()))
            else:
                info["table_constraints"].append(" ".join(part.split()))
            continue
        col = part.split(None, 1)[0].strip('"`[]')
        m = re.search(r"\bCHECK\s*\(", part, re.I)
        if m:
            info["checks"][col] = " ".join(_paren(part, m.end() - 1).split())
        if re.search(r"\bUNIQUE\b", part, re.I):
            info["unique"].add(col)
    return info


def fk_rows(conn, name):
    return q(conn, f'PRAGMA foreign_key_list("{name}")')


def pk_columns(conn, name):
    return [c["name"] for c in sorted(q(conn, f'PRAGMA table_info("{name}")'), key=lambda c: c["pk"]) if c["pk"]]


def key_sql(col):
    """One row-key column as SQL: 'child_serial|child_lot_id' is whichever of the two is filled."""
    return "COALESCE(" + ", ".join(col.split("|")) + ")" if "|" in col else col


def row_key(conn, name, kind):
    """Which columns make one row unique, and what guarantees it. A primary key that is only a row number says
    nothing about that, so the key comes from a UNIQUE constraint or the declared ROW_KEY; None if nothing covers it."""
    pk = pk_columns(conn, name)
    types = {c["name"]: (c["type"] or "").upper() for c in q(conn, f'PRAGMA table_info("{name}")')}
    row_number = pk[0] if len(pk) == 1 and types[pk[0]] == "INTEGER" else None
    if pk and not row_number:
        return {"columns": pk, "how": "PRIMARY_KEY", "note": "The primary key, so the database enforces it."}
    unique = partial = None
    for ix in q(conn, f'PRAGMA index_list("{name}")') if kind == "table" else []:
        if ix["unique"] and ix["origin"] != "pk":
            cols = [r["name"] for r in q(conn, f'PRAGMA index_info("{ix["name"]}")')]
            if ix["partial"]:
                partial = ix["name"]
            elif None not in cols and unique is None:
                unique = cols
    base = {"row_number": row_number}
    if name in ROW_KEY:
        if partial:
            how, note = "PARTIAL_INDEX", (f"Current rows: the database enforces it with the unique index {partial}. "
                                          "Closed rows kept as history: checked by tests.")
        else:
            how, note = "TESTED", ("Checked by tests on the built data and after every closed loop runs; "
                                   + ("a view has no constraints." if kind == "view" else "the database does not enforce it."))
        return {**base, "columns": list(ROW_KEY[name]), "how": how, "note": note}
    if unique:
        return {**base, "columns": unique, "how": "UNIQUE", "note": "A UNIQUE constraint, so the database enforces it."}
    if name in NUMBERED:
        return {**base, "columns": pk, "how": "ROW_NUMBER", "note": f"The row number is the identity: {NUMBERED[name]}."}
    return None


def resolve_fk_target(conn, fk):
    """PRAGMA returns to=None when the FK names only the parent table."""
    if fk["to"]:
        return fk["to"]
    pks = pk_columns(conn, fk["table"])
    return pks[fk["seq"]] if fk["seq"] < len(pks) else (pks[0] if pks else None)


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------

@get(r"^/api/sandbox/tables$")
def tables(req):
    out = []
    fk_total = 0
    for o in objects(req.conn):
        layer, domain = classify(o["name"], o["type"])
        fks = fk_rows(req.conn, o["name"]) if o["type"] == "table" else []
        fk_total += len({fk["id"] for fk in fks})
        cols = q(req.conn, f'PRAGMA table_info("{o["name"]}")')
        out.append({
            "name": o["name"],
            "kind": o["type"],
            "layer": layer,
            "domain": domain,
            "rows": count_rows(req.conn, o["name"], o["type"], seconds=0.25),
            "columns": len(cols),
            "fks": len({fk["id"] for fk in fks}),
            "description": DESCRIPTIONS.get(o["name"], ""), "grain": GRAIN.get(o["name"]),
        })
    return {"tables": out, "foreign_keys": fk_total}


@get(r"^/api/sandbox/table/([A-Za-z0-9_]+)$")
def table_detail(req):
    name = req.params[0]
    obj = get_object(req.conn, name)
    kind = obj["type"]
    layer, domain = classify(name, kind)
    parsed = parse_create(obj["sql"]) if kind == "table" else parse_create(None)
    fks = fk_rows(req.conn, name) if kind == "table" else []
    fk_by_col = {}
    for fk in fks:
        fk_by_col[fk["from"]] = {"table": fk["table"], "column": resolve_fk_target(req.conn, fk), "id": fk["id"]}
    columns = []
    for c in q(req.conn, f'PRAGMA table_info("{name}")'):
        columns.append({
            "cid": c["cid"],
            "name": c["name"],
            "type": c["type"] or "",
            "notnull": bool(c["notnull"]),
            "default": c["dflt_value"],
            "pk": c["pk"],
            "fk": fk_by_col.get(c["name"]),
            "check": parsed["checks"].get(c["name"]),
            "unique": c["name"] in parsed["unique"],
        })
    referenced_by = []
    for o in objects(req.conn):
        if o["type"] != "table":
            continue
        for fk in fk_rows(req.conn, o["name"]):
            if fk["table"] == name:
                referenced_by.append({"table": o["name"], "column": fk["from"],
                                      "to_column": resolve_fk_target(req.conn, fk)})
    indexes = []
    if kind == "table":
        for ix in q(req.conn, f'PRAGMA index_list("{name}")'):
            cols = [r["name"] for r in q(req.conn, f'PRAGMA index_info("{ix["name"]}")')]
            indexes.append({"name": ix["name"], "unique": bool(ix["unique"]), "origin": ix["origin"], "columns": cols})
    return {
        "name": name,
        "kind": kind,
        "layer": layer,
        "domain": domain,
        "description": DESCRIPTIONS.get(name, ""), "grain": GRAIN.get(name), "row_key": row_key(req.conn, name, kind),
        "rows": count_rows(req.conn, name, kind, seconds=1.0),
        "columns": columns,
        "table_checks": parsed["table_checks"],
        "table_constraints": parsed["table_constraints"],
        "referenced_by": referenced_by,
        "indexes": indexes,
        "create_sql": obj["sql"],
    }


@get(r"^/api/sandbox/rows/([A-Za-z0-9_]+)$")
def table_rows(req):
    name = req.params[0]
    obj = get_object(req.conn, name)
    cols = [c["name"] for c in q(req.conn, f'PRAGMA table_info("{name}")')]
    limit = max(1, min(req.arg("limit", 50, int), 500))
    offset = max(0, req.arg("offset", 0, int))
    order = req.arg("order")
    direction = "DESC" if (req.arg("dir", "asc") or "asc").lower() == "desc" else "ASC"
    sql = f'SELECT * FROM "{name}"'
    if order:
        if order not in cols:
            raise HttpError(400, f"unknown column {order!r}")
        sql += f' ORDER BY "{order}" {direction}'
    sql += " LIMIT ? OFFSET ?"
    guarded(req.conn, 3.0)
    try:
        cur = req.conn.execute(sql, (limit, offset))
        names = [d[0] for d in cur.description]
        data = [[cell(r[n]) for n in names] for r in cur.fetchall()]
    except sqlite3.OperationalError as e:
        raise HttpError(400, "query stopped after 3 s" if "interrupt" in str(e) else str(e))
    finally:
        req.conn.set_progress_handler(None, 0)
    return {
        "name": name,
        "columns": names,
        "rows": data,
        "offset": offset,
        "limit": limit,
        "total": count_rows(req.conn, name, obj["type"], seconds=0.5),
    }


@get(r"^/api/sandbox/lookup/([A-Za-z0-9_]+)$")
def lookup(req):
    """Rows of `table` where `col` = `val`: lets the UI follow a foreign key."""
    name = req.params[0]
    get_object(req.conn, name)
    col = req.arg("col")
    value = req.arg("val")
    cols = [c["name"] for c in q(req.conn, f'PRAGMA table_info("{name}")')]
    if col not in cols:
        raise HttpError(400, f"unknown column {col!r}")
    if value is None:
        raise HttpError(400, "val is required")
    cur = req.conn.execute(f'SELECT * FROM "{name}" WHERE "{col}" = ? LIMIT 20', (value,))
    names = [d[0] for d in cur.description]
    rows = [[cell(r[n]) for n in names] for r in cur.fetchall()]
    fks = {}
    for fk in fk_rows(req.conn, name):
        fks[fk["from"]] = {"table": fk["table"], "column": resolve_fk_target(req.conn, fk)}
    return {"name": name, "columns": names, "rows": rows, "fks": fks}


def _first_keyword(sql):
    s = sql
    while True:
        s = s.lstrip()
        if s.startswith("--"):
            nl = s.find("\n")
            s = "" if nl < 0 else s[nl + 1:]
        elif s.startswith("/*"):
            end = s.find("*/")
            s = "" if end < 0 else s[end + 2:]
        else:
            break
    m = re.match(r"([A-Za-z]+)", s)
    return m.group(1).upper() if m else ""


@post(r"^/api/sandbox/sql$")
def run_sql(req):
    sql = (req.body.get("sql") or "").strip()
    if not sql:
        raise HttpError(400, "Write a query first.")
    first = _first_keyword(sql)
    if first not in ("SELECT", "WITH", "EXPLAIN", "VALUES"):
        raise HttpError(400, "The sandbox is read-only: only SELECT, WITH and EXPLAIN statements run here"
                             f" (got {first or 'no statement'}).")
    conn = connect(readonly=True)
    conn.row_factory = None
    started = time.perf_counter()
    guarded(conn, SQL_TIMEOUT_S)
    try:
        cur = conn.execute(sql)
        names = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(MAX_ROWS + 1) if cur.description else []
    except sqlite3.OperationalError as e:
        msg = str(e)
        if "interrupt" in msg:
            raise HttpError(400, f"Query stopped: it ran past the {SQL_TIMEOUT_S:.0f} second limit.")
        if "readonly" in msg.lower() or "read-only" in msg.lower():
            raise HttpError(400, "The sandbox is read-only.")
        raise HttpError(400, msg)
    except (sqlite3.ProgrammingError, sqlite3.Warning) as e:
        msg = str(e)
        if "one statement" in msg:
            msg = "Run one statement at a time."
        raise HttpError(400, msg)
    except sqlite3.Error as e:
        raise HttpError(400, str(e))
    finally:
        conn.close()
    truncated = len(rows) > MAX_ROWS
    rows = rows[:MAX_ROWS]
    return {
        "columns": names,
        "rows": [[cell(v) for v in r] for r in rows],
        "row_count": len(rows),
        "truncated": truncated,
        "ms": round((time.perf_counter() - started) * 1000, 1),
    }


@get(r"^/api/sandbox/erd$")
def erd(req):
    tables_out = []
    edges = []
    for o in objects(req.conn):
        name = o["name"]
        layer, domain = classify(name, o["type"])
        fks = fk_rows(req.conn, name) if o["type"] == "table" else []
        fk_by_col = {}
        for fk in fks:
            target = resolve_fk_target(req.conn, fk)
            fk_by_col[fk["from"]] = {"table": fk["table"], "column": target}
            edges.append({"from_table": name, "from_col": fk["from"], "to_table": fk["table"], "to_col": target})
        cols = [{"name": c["name"], "type": c["type"] or "", "pk": c["pk"], "notnull": bool(c["notnull"]),
                 "fk": fk_by_col.get(c["name"])} for c in q(req.conn, f'PRAGMA table_info("{name}")')]
        tables_out.append({
            "name": name, "kind": o["type"], "layer": layer, "domain": domain,
            "rows": count_rows(req.conn, name, o["type"], seconds=0.2),
            "description": DESCRIPTIONS.get(name, ""), "grain": GRAIN.get(name),
            "row_key": row_key(req.conn, name, o["type"]),
            "columns": cols,
        })
    return {"tables": tables_out, "edges": edges}


EXAMPLES = [
    {
        "id": "recall-scope",
        "title": "Recall scope: everything built from the worst cell lot",
        "description": "Forward trace through genealogy (recursive CTE) from the lot with the most warranty claims, up to packs and the vehicles they shipped with.",
        "sql": """-- Forward trace: every unit that contains the lot with the most warranty claims
WITH RECURSIVE target AS (
  SELECT failed_lot_id AS lot_id
  FROM warranty_claim
  WHERE failed_lot_id IS NOT NULL
  GROUP BY failed_lot_id
  ORDER BY COUNT(*) DESC
  LIMIT 1
),
up(serial, depth) AS (
  SELECT g.parent_serial, 1
  FROM genealogy g JOIN target t ON g.child_lot_id = t.lot_id
  WHERE g.removed_at IS NULL
  UNION
  SELECT g.parent_serial, up.depth + 1
  FROM genealogy g JOIN up ON g.child_serial = up.serial
  WHERE g.removed_at IS NULL
)
SELECT (SELECT lot_id FROM target) AS lot_id,
       u.serial, u.item_id, i.kind, u.status, u.location_site_id, u.on_hold,
       MIN(up.depth) AS depth
FROM up
JOIN unit u ON u.serial = up.serial
JOIN item i ON i.item_id = u.item_id
GROUP BY u.serial
ORDER BY i.kind, u.status, u.serial;""",
    },
    {
        "id": "as-built",
        "title": "As-built tree of one vehicle",
        "description": "Backward trace from the most recently built vehicle down through modules, sub-components and lots.",
        "sql": """-- As-built tree for the most recently built vehicle (swap in any serial)
WITH RECURSIVE root AS (
  SELECT u.serial
  FROM unit u JOIN item i ON i.item_id = u.item_id
  WHERE i.kind = 'VEHICLE' AND u.built_at IS NOT NULL
  ORDER BY u.built_at DESC
  LIMIT 1
),
tree(parent, child_serial, child_lot, item_id, relation, depth, path) AS (
  SELECT g.parent_serial, g.child_serial, g.child_lot_id, g.child_item_id, g.relation, 1,
         g.parent_serial || ' > ' || COALESCE(g.child_serial, g.child_lot_id)
  FROM genealogy g JOIN root r ON g.parent_serial = r.serial
  WHERE g.removed_at IS NULL
  UNION ALL
  SELECT g.parent_serial, g.child_serial, g.child_lot_id, g.child_item_id, g.relation, t.depth + 1,
         t.path || ' > ' || COALESCE(g.child_serial, g.child_lot_id)
  FROM genealogy g JOIN tree t ON g.parent_serial = t.child_serial
  WHERE g.removed_at IS NULL AND t.depth < 6
)
SELECT depth,
       substr('· · · · · · · · · · ', 1, 2 * (depth - 1)) || COALESCE(child_serial, child_lot) AS component,
       item_id, relation, parent
FROM tree
ORDER BY path;""",
    },
    {
        "id": "bom-explosion",
        "title": "Multi-level BOM explosion for a kit",
        "description": "Recursive CTE over bom_line with effectivity on the dataset date, multiplying qty_per down every level to tier-3 materials.",
        "sql": """-- Multi-level BOM explosion for one LV-1 kit, with effectivity on the dataset date
WITH RECURSIVE params AS (
  SELECT COALESCE((SELECT item_id FROM item WHERE item_id = 'LV1-SLATE-S'),
                  (SELECT item_id FROM item WHERE kind = 'KIT' ORDER BY item_id LIMIT 1)) AS root,
         (SELECT value FROM meta WHERE key = 'as_of') AS on_date
),
explode(level, parent_item_id, child_item_id, qty_per, ext_qty, bom_level, path) AS (
  SELECT 1, b.parent_item_id, b.child_item_id, b.qty_per, b.qty_per, b.bom_level,
         b.parent_item_id || ' > ' || b.child_item_id
  FROM bom_line b JOIN params p ON b.parent_item_id = p.root
  WHERE b.eff_from <= p.on_date AND (b.eff_to IS NULL OR b.eff_to > p.on_date)
  UNION ALL
  SELECT e.level + 1, b.parent_item_id, b.child_item_id, b.qty_per, e.ext_qty * b.qty_per, b.bom_level,
         e.path || ' > ' || b.child_item_id
  FROM explode e
  JOIN bom_line b ON b.parent_item_id = e.child_item_id
  JOIN params p
  WHERE b.eff_from <= p.on_date AND (b.eff_to IS NULL OR b.eff_to > p.on_date)
    AND e.level < 8
)
SELECT e.level, e.parent_item_id AS parent, e.child_item_id AS child, i.name, i.kind,
       i.make_buy, e.bom_level, e.qty_per, ROUND(e.ext_qty, 4) AS extended_qty, i.uom
FROM explode e
JOIN item i ON i.item_id = e.child_item_id
ORDER BY e.path;""",
    },
    {
        "id": "lineage",
        "title": "Lineage: landing payload → canonical event",
        "description": "Each normalized station event keeps raw_id, so you can always see the exact CM MES payload it came from.",
        "sql": """-- Lineage: a canonical station event and the raw CM MES payload it came from
SELECT se.event_id, se.serial, se.station_id, se.event_ts, se.result, se.defect_code,
       r.raw_id, r.received_at, r.ingest_status, r.mapping_version, r.payload
FROM station_event se
JOIN raw_cm_mes_event r ON r.raw_id = se.raw_id
ORDER BY se.event_ts DESC
LIMIT 50;""",
    },
    {
        "id": "promise-slip",
        "title": "Promise-date slip by supplier",
        "description": "First vs latest promise per PO line from po_promise_history, rolled up by supplier.",
        "sql": """-- Promise-date slip by supplier: first promise vs latest promise per PO line
WITH per_line AS (
  SELECT h.po_id, h.line_no, COUNT(*) AS promises,
         (SELECT f.promise_date FROM po_promise_history f
           WHERE f.po_id = h.po_id AND f.line_no = h.line_no
           ORDER BY f.recorded_at ASC LIMIT 1) AS first_promise,
         (SELECT l.promise_date FROM po_promise_history l
           WHERE l.po_id = h.po_id AND l.line_no = h.line_no
           ORDER BY l.recorded_at DESC LIMIT 1) AS last_promise
  FROM po_promise_history h
  GROUP BY h.po_id, h.line_no
)
SELECT po.supplier_id, s.name AS supplier, COUNT(*) AS po_lines,
       SUM(CASE WHEN p.last_promise > p.first_promise THEN 1 ELSE 0 END) AS lines_slipped,
       ROUND(AVG(julianday(p.last_promise) - julianday(p.first_promise)), 1) AS avg_slip_days,
       MAX(julianday(p.last_promise) - julianday(p.first_promise)) AS worst_slip_days
FROM per_line p
JOIN purchase_order po ON po.po_id = p.po_id
JOIN supplier s ON s.supplier_id = po.supplier_id
GROUP BY po.supplier_id
ORDER BY avg_slip_days DESC;""",
    },
    {
        "id": "fpy",
        "title": "First-pass yield by station (last 14 days)",
        "description": "Rolls v_fpy_daily up by station. Rolled throughput yield is the product of station FPYs.",
        "sql": """-- First-pass yield by station over the last 14 days of data
SELECT st.site_id, st.line, st.code, st.name,
       SUM(f.units) AS units, SUM(f.first_pass) AS first_pass,
       ROUND(1.0 * SUM(f.first_pass) / SUM(f.units), 4) AS fpy
FROM v_fpy_daily f
JOIN station st ON st.station_id = f.station_id
WHERE f.day >= (SELECT date(value, '-14 days') FROM meta WHERE key = 'as_of')
GROUP BY st.station_id
ORDER BY st.site_id, st.line, st.seq;""",
    },
    {
        "id": "supplier-held",
        "title": "Inventory down to supplier-held stock",
        "description": "What suppliers are holding for us, by site, item and status, from their stock reports.",
        "sql": """-- Supplier-held stock by site and item
SELECT b.site_id, st.name AS site, b.item_id, i.name AS item,
       b.stock_status, SUM(b.qty) AS qty, i.uom, MAX(b.as_of) AS reported
FROM inventory_balance b
JOIN site st ON st.site_id = b.site_id
JOIN item i ON i.item_id = b.item_id
WHERE b.owner = 'SUPPLIER'
GROUP BY b.site_id, b.item_id, b.stock_status
ORDER BY qty DESC;""",
    },
    {
        "id": "mrp-messages",
        "title": "MRP action messages from the latest run",
        "description": "What the planner should do, by item and date: shortages first, then past-due releases and expedites.",
        "sql": """-- MRP action messages from the latest run
SELECT m.message, m.item_id, i.name, m.site_id, m.bucket_date, m.qty, m.ref, m.detail
FROM mrp_message m
JOIN item i ON i.item_id = m.item_id
WHERE m.run_id = (SELECT MAX(run_id) FROM mrp_run)
ORDER BY CASE m.message WHEN 'SHORTAGE' THEN 0 WHEN 'PAST_DUE_RELEASE' THEN 1
                        WHEN 'EXPEDITE' THEN 2 ELSE 3 END,
         m.bucket_date;""",
    },
    {
        "id": "audit-trail",
        "title": "Closed-loop audit trail",
        "description": "Every decision and every write it caused: holds, outbound messages, moved promises, chargebacks and ERP entries.",
        "sql": """-- Closed-loop audit trail: each decision and every row it wrote
SELECT d.decision_id, d.loop, d.status, d.title,
       (SELECT COUNT(*) FROM hold h WHERE h.decision_id = d.decision_id) AS holds,
       (SELECT COUNT(*) FROM outbound_message m WHERE m.decision_id = d.decision_id) AS outbound_msgs,
       (SELECT COUNT(*) FROM order_promise p WHERE p.decision_id = d.decision_id) AS promises_moved,
       (SELECT GROUP_CONCAT(c.chargeback_id) FROM chargeback c WHERE c.decision_id = d.decision_id) AS chargebacks,
       (SELECT GROUP_CONCAT(c.je_id) FROM chargeback c
         WHERE c.decision_id = d.decision_id AND c.je_id IS NOT NULL) AS erp_entries,
       d.proposed_at, d.executed_at
FROM decision_log d
ORDER BY COALESCE(d.executed_at, d.proposed_at) DESC, d.decision_id DESC;""",
    },
    {
        "id": "je-balance",
        "title": "Every ERP journal entry balances",
        "description": "Debits must equal credits per entry. Any non-zero imbalance is a bug in the posting logic.",
        "sql": """-- Every ERP journal entry must balance: debits = credits
SELECT je.je_id, je.doc_type, je.posted_at, je.supplier_id,
       ROUND(SUM(l.debit_usd), 2) AS debits,
       ROUND(SUM(l.credit_usd), 2) AS credits,
       ROUND(SUM(l.debit_usd) - SUM(l.credit_usd), 2) AS imbalance
FROM erp_journal_entry je
JOIN erp_journal_line l ON l.je_id = je.je_id
GROUP BY je.je_id
ORDER BY ABS(SUM(l.debit_usd) - SUM(l.credit_usd)) DESC, je.posted_at DESC;""",
    },
    {
        "id": "promise-moved",
        "title": "Orders whose promise moved on a supply delay",
        "description": "order_promise history joined to the order and its kit line.",
        "sql": """-- Orders whose promise date moved because of a supply delay
SELECT p.order_id, ol.item_id AS kit, o.ship_to_state, o.first_promised_date,
       p.promised_date AS new_promise,
       CAST(julianday(p.promised_date) - julianday(o.first_promised_date) AS INTEGER) AS days_moved,
       p.pegged_to, p.decided_at, p.decision_id
FROM order_promise p
JOIN customer_order o ON o.order_id = p.order_id
LEFT JOIN order_line ol ON ol.order_id = o.order_id AND ol.line_no = 1
WHERE p.reason = 'SUPPLY_DELAY'
ORDER BY days_moved DESC, p.decided_at DESC;""",
    },
    {
        "id": "position",
        "title": "Multi-echelon inventory position",
        "description": "v_inventory_position: serialized units plus non-serialized balances, by item, site and bucket.",
        "sql": """-- Multi-echelon inventory position by item and bucket
SELECT v.item_id, i.name, i.kind, v.site_id, st.kind AS site_kind,
       v.bucket, v.owner, v.qty, v.source_table
FROM v_inventory_position v
JOIN item i ON i.item_id = v.item_id
LEFT JOIN site st ON st.site_id = v.site_id
ORDER BY i.kind, v.item_id, st.kind, v.bucket;""",
    },
    {
        "id": "fk-check",
        "title": "Referential integrity check",
        "description": "SQLite's own foreign-key check as a table-valued function. Zero rows means every reference resolves.",
        "sql": """-- Referential integrity: zero rows = every foreign key resolves
SELECT * FROM pragma_foreign_key_check;""",
    },
    {
        "id": "feed-health",
        "title": "CM feed health by day",
        "description": "Landing-layer ingest status counts per day for the CM MES feed.",
        "sql": """-- CM MES feed: ingest outcome per day
SELECT substr(received_at, 1, 10) AS day, ingest_status, COUNT(*) AS messages
FROM raw_cm_mes_event
GROUP BY day, ingest_status
ORDER BY day DESC, messages DESC;""",
    },
]


@get(r"^/api/sandbox/examples$")
def examples(req):
    return {"examples": EXAMPLES}
