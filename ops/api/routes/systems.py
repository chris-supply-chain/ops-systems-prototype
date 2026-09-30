"""System Landscape: every system of record around the platform, live volumes,
the layers the data moves through, the write-backs that close the loop, the
offshore-CM context, and who owns which entity.
"""
import datetime as dt
from collections import defaultdict

from ...db import as_of, now, q, q1, val
from ...generate.util import TW_HOLIDAYS
from ..router import get
from .integrations import PAGES, _cutoff, _health

GROUPS = [  # landscape columns (left = outside partners, right = OEM-native)
    ("Contract manufacturer · Taiwan", ["MES-CM", "RPT-CM"]),
    ("Suppliers", ["EDI-VAN", "PORTAL", "MAIL-PO", "MAIL-SUP"]),
    ("Logistics partners", ["WMS-3PL", "TMS-OCEAN", "TMS-DG", "TMS-PARCEL", "BROKER"]),
    ("Customers", ["CRM"]),
    ("OEM systems", ["MES-FRE", "ERP", "QMS", "PLAN"]),
]
TARGET_SYSTEM = {"ERP": "ERP", "WMS_3PL": "WMS-3PL", "CM_MES": "MES-CM", "OEM_MES": "MES-FRE",
                 "SUPPLIER_PORTAL": "PORTAL", "CUSTOMER_COMMS": "CRM", "TMS": "TMS-OCEAN", "EDI": "EDI-VAN"}

OWNERSHIP = [
    # entity, system of record, canonical tables, landing, page
    ("Vehicle build events (CM line)", "FAP MES (Taichung)", ["station_event", "unit"], "raw_cm_mes_event", "production"),
    ("Pack build events (Fremont line)", "OEM pack-line MES", ["station_event", "unit"], None, "production"),
    ("As-built genealogy", "CM MES + supplier ASN + OEM MES + 3PL kitting", ["genealogy"],
     "raw_cm_mes_event · raw_supplier_asn · raw_3pl_message", "genealogy"),
    ("CM daily output & consigned stock (as reported)", "FAP daily Excel report", ["cm_output_report", "cm_stock_report"],
     "raw_email · raw_attachment", "integrations"),
    ("Purchase orders, receipts, invoices", "OEM ERP", ["purchase_order", "po_line", "goods_receipt", "supplier_invoice"],
     None, "erp"),
    ("Supplier promise dates", "EDI 855 · portal · email · Excel", ["po_promise_history"],
     "raw_supplier_confirmation · raw_email", "suppliers"),
    ("Supplier-held stock", "Supplier reports (Excel, portal)", ["inventory_balance"], "raw_email · raw_attachment", "inventory"),
    ("Shipments & milestones", "Carriers (EDI 315, APIs) + 3PL + broker", ["shipment", "shipment_event", "customs_entry"],
     "raw_carrier_event · raw_3pl_message", "shipments"),
    ("3PL stock & allocations", "Sierra Fulfillment WMS", ["unit", "order_line", "wms_snapshot"], "raw_3pl_message", "inventory"),
    ("Quality events, deviations, holds", "QMS thin app", ["quality_event", "deviation", "hold", "capa"], None, "quality"),
    ("Warranty claims", "Service CRM", ["warranty_claim"], "raw_warranty_case", "warranty"),
    ("Chargebacks & journal entries", "Ops OS → OEM ERP", ["chargeback", "erp_journal_entry", "erp_journal_line"], None, "warranty"),
    ("Plans, promises & decisions", "Planning engine (Ops OS)", ["mrp_run", "planned_order", "order_promise", "decision_log"],
     None, "mrp"),
    ("Customer orders", "OEM e-commerce", ["customer_order", "order_line"], None, "atp"),
]

LAYER_PREFIX = {"LANDING": ("raw_", "mapping_version", "ingest_run", "ingest_step"),
                "ACTION": ("decision_log", "hold", "outbound_message", "ops_exception", "chargeback", "chargeback_line",
                           "erp_journal_entry", "erp_journal_line", "supplier_scorecard", "order_promise", "mrp_run",
                           "planned_order", "mrp_message")}
PLATFORM = ("process_activity", "process_event", "app_telemetry", "capability", "vendor", "vendor_capability",
            "data_contract", "contract_run", "eval_suite", "eval_golden", "eval_run", "eval_case", "test_run",
            "change_review", "meta")


def _layer(name):
    if name.startswith("raw_") or name in LAYER_PREFIX["LANDING"]:
        return "LANDING"
    if name in LAYER_PREFIX["ACTION"]:
        return "ACTION"
    if name in PLATFORM:
        return "PLATFORM"
    return "CORE"


@get(r"^/api/systems/landscape$")
def landscape(req):
    conn = req.conn
    cut7, cut14 = _cutoff(conn, 7), _cutoff(conn, 14)
    systems = {s["system_id"]: s for s in q(conn, "SELECT * FROM source_system")}
    for sid, s in systems.items():
        h = _health(conn, sid, cut7, cut14)
        s["health"] = {k: h[k] for k in ("kind", "total", "last_7d", "last_at", "quarantined", "automated_share", "status")}
        s["pages"] = PAGES.get(sid, [])
        s["options"] = q(conn, "SELECT option, status FROM integration_option WHERE system_id=? ORDER BY status", (sid,))
    # layers: table and row counts
    layers = defaultdict(lambda: {"tables": 0, "rows": 0, "top": []})
    for t in q(conn, "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        n = val(conn, f'SELECT COUNT(*) FROM "{t["name"]}"')
        L = layers[_layer(t["name"])]
        L["tables"] += 1
        L["rows"] += n
        L["top"].append((n, t["name"]))
    for L in layers.values():
        L["top"] = [name for n, name in sorted(L["top"], reverse=True)[:4]]
    # write-backs: the "act" half of the loop
    out_rows = q(conn, "SELECT target_system, message_type, COUNT(*) AS n, MAX(created_at) AS last FROM outbound_message"
                       " GROUP BY target_system, message_type ORDER BY n DESC")
    outbound = defaultdict(lambda: {"n": 0, "types": [], "last": None})
    for r in out_rows:
        o = outbound[r["target_system"]]
        o["n"] += r["n"]
        o["types"].append(f"{r['message_type']} ×{r['n']}")
        o["last"] = max(o["last"] or "", r["last"] or "") or None
    outbound = [{"target": k, "system_id": TARGET_SYSTEM.get(k), **v} for k, v in outbound.items()]
    # the offshore contract manufacturer, in numbers
    now_s = now(conn)
    t_now = dt.datetime.strptime(now_s, "%Y-%m-%dT%H:%M:%SZ")
    day = dt.date.fromisoformat(as_of(conn))
    holidays = sorted(h for h in TW_HOLIDAYS if day - dt.timedelta(days=120) <= h <= day + dt.timedelta(days=45))
    consigned = q1(conn, "SELECT COUNT(*) AS n, COALESCE(SUM(i.std_cost), 0) AS usd FROM unit u JOIN item i USING (item_id)"
                         " WHERE u.status='COMPONENT' AND u.location_site_id='CM-TXG'")
    stranded = q1(conn, "SELECT COUNT(*) AS n FROM unit WHERE item_id='DU-B' AND status='COMPONENT' AND location_site_id='CM-TXG'")
    transit = q(conn, "SELECT julianday(ata) - julianday(atd) AS d FROM shipment WHERE leg='CM_TO_3PL' AND ata IS NOT NULL AND atd IS NOT NULL")
    tdays = sorted(r["d"] for r in transit if r["d"] is not None)
    assists = q1(conn, "SELECT COUNT(*) AS entries, COALESCE(SUM(s.asn_qty), 0) AS units, COALESCE(SUM(c.entered_value), 0) AS value,"
                       " COALESCE(SUM(c.duty_usd), 0) AS duty FROM customs_entry c JOIN shipment s USING (shipment_id)")
    mapping = q(conn, "SELECT version, effective_from, notes FROM mapping_version WHERE source='CM_MES' ORDER BY effective_from")
    quarantine_cm = q(conn, "SELECT ingest_note, COUNT(*) AS n FROM raw_cm_mes_event WHERE ingest_status='QUARANTINED' GROUP BY 1")
    tz_fixed = val(conn, "SELECT COUNT(*) FROM raw_cm_mes_event WHERE ingest_note LIKE 'TZ_CORRECTED%'")
    zh_text = val(conn, "SELECT COUNT(*) FROM raw_cm_mes_event WHERE ingest_note LIKE 'defect mapped from Chinese text%'")
    cm_reports = val(conn, "SELECT COUNT(*) FROM raw_email WHERE classified_as='CM_DAILY_REPORT'")
    cm = {
        "name": "Formosa Assembly Partners", "city": "Taichung, Taiwan", "tz": "Asia/Taipei (UTC+8, no DST)",
        "local_now": (t_now + dt.timedelta(hours=8)).strftime("%a %b %d · %H:%M"),
        "pt_now": (t_now - dt.timedelta(hours=7)).strftime("%a %b %d · %H:%M"),
        "holidays": [h.isoformat() for h in holidays],
        "consigned_units": consigned["n"], "consigned_usd": consigned["usd"], "stranded_du_b": stranded["n"],
        "transit_median_days": tdays[len(tdays) // 2] if tdays else None, "containers": len(tdays),
        "assists_per_unit": 188.40 + 64.0 + 58.0, "entries": assists["entries"], "entered_value": assists["value"],
        "duty_usd": assists["duty"], "mappings": mapping, "quarantine": quarantine_cm, "tz_corrected": tz_fixed,
        "chinese_defects": zh_text, "daily_reports": cm_reports,
    }
    ownership = []
    for entity, sor, tables, landing, page in OWNERSHIP:
        counts = {t: val(conn, f'SELECT COUNT(*) FROM "{t}"') for t in tables}
        ownership.append({"entity": entity, "system": sor, "tables": counts, "landing": landing, "page": page})
    groups = [{"label": label, "systems": [systems[s] for s in ids if s in systems]} for label, ids in GROUPS]
    return {"now": now_s, "groups": groups, "layers": {k: v for k, v in layers.items()}, "outbound": outbound,
            "cm": cm, "ownership": ownership,
            "kpis": {"systems": len(systems), "external": sum(1 for s in systems.values() if s["owner"] != "OEM"),
                     "landed_7d": sum(s["health"]["last_7d"] for s in systems.values() if s["health"]["kind"] == "raw"),
                     "writes": sum(o["n"] for o in outbound), "tables": sum(v["tables"] for v in layers.values()),
                     "rows": sum(v["rows"] for v in layers.values())}}
