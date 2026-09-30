"""Platform: process event logs (as the work actually ran), thin-app telemetry,
buy-vs-build candidates, the system landscape with integration options, and the
golden truth the eval suites score against.
"""
import datetime as dt
import json

from .util import PT, TPE, UTC, add_days, at, week_start

# ------------------------------------------------------------------ process mining

ACTIVITIES = [
    # process, activity, category, system, note
    ("PO_CONFIRMATION", "Create PO", "VALUE", "ERP", None),
    ("PO_CONFIRMATION", "Transmit PO (EDI 850)", "VALUE", "EDI VAN", None),
    ("PO_CONFIRMATION", "Publish PO to supplier portal", "VALUE", "Supplier portal", None),
    ("PO_CONFIRMATION", "Email PO PDF to supplier", "GLUE", "Email", "Exists because ERP cannot reach the supplier"),
    ("PO_CONFIRMATION", "Chase supplier by email", "WAIT", "Email", "Rework loop: nobody knows if the PO landed"),
    ("PO_CONFIRMATION", "Supplier acknowledges (EDI 855)", "VALUE", "EDI VAN", None),
    ("PO_CONFIRMATION", "Supplier confirms in portal", "VALUE", "Supplier portal", None),
    ("PO_CONFIRMATION", "Supplier replies by email", "VALUE", "Email", "The information is real; the channel is not"),
    ("PO_CONFIRMATION", "Supplier sends open-order Excel", "VALUE", "Email", None),
    ("PO_CONFIRMATION", "Buyer re-keys promise into tracker", "GLUE", "Excel", "Copying email text into a spreadsheet"),
    ("PO_CONFIRMATION", "Buyer copies Excel into tracker", "GLUE", "Excel", "Copying one spreadsheet into another"),
    ("PO_CONFIRMATION", "Buyer updates promise in ERP", "GLUE", "ERP", "Re-keying the same date a second time"),
    ("PO_CONFIRMATION", "Planner exports open POs (CSV)", "GLUE", "ERP", "Weekly; the plan is blind until Monday"),
    ("PO_CONFIRMATION", "Planner updates plan workbook", "GLUE", "Excel", "Third copy of the promise date"),
    ("PO_CONFIRMATION", "Escalate slip in chat", "CONTROL", "Chat", None),
    ("PO_CONFIRMATION", "Platform parses email/Excel", "VALUE", "Ops OS", "Automated extraction, abstains when unsure"),
    ("PO_CONFIRMATION", "Buyer resolves parser abstention", "CONTROL", "Ops OS", "Only the exceptions reach a human"),
    ("PO_CONFIRMATION", "Promise loaded to ERP", "VALUE", "ERP", None),
    ("PO_CONFIRMATION", "MRP consumes promise", "VALUE", "Ops OS", None),
    ("DEVIATION_APPROVAL", "Deviation requested by email", "VALUE", "Email", None),
    ("DEVIATION_APPROVAL", "SQE drafts deviation in Word", "GLUE", "Word", "Template re-typed each time"),
    ("DEVIATION_APPROVAL", "Route for signatures", "CONTROL", "DocuSign", None),
    ("DEVIATION_APPROVAL", "Returned for more data", "WAIT", "DocuSign", "Missing lot or quantity limit"),
    ("DEVIATION_APPROVAL", "Quality approves", "CONTROL", "DocuSign", None),
    ("DEVIATION_APPROVAL", "Engineering approves", "CONTROL", "DocuSign", None),
    ("DEVIATION_APPROVAL", "Planner notified by email", "GLUE", "Email", "Planner learns second-hand"),
    ("DEVIATION_APPROVAL", "Logged in deviation tracker", "GLUE", "Excel", "Tracker drifts from the signed PDF"),
    ("DEVIATION_APPROVAL", "MES updated to allow part", "VALUE", "MES", None),
    ("INBOUND_RECEIPT", "Arrival notice emailed", "GLUE", "Email", "Carrier ETA already in the TMS feed"),
    ("INBOUND_RECEIPT", "Dock appointment booked", "CONTROL", "3PL portal", None),
    ("INBOUND_RECEIPT", "Container unloaded", "VALUE", "WMS", None),
    ("INBOUND_RECEIPT", "Serials scanned", "VALUE", "WMS", None),
    ("INBOUND_RECEIPT", "Discrepancy emailed to the OEM", "GLUE", "Email", "ASN vs scan compared by eye"),
    ("INBOUND_RECEIPT", "OEM reconciles ASN vs receipt in Excel", "GLUE", "Excel", "Should be a contract, not a person"),
    ("INBOUND_RECEIPT", "Put away", "VALUE", "WMS", None),
    ("INBOUND_RECEIPT", "Available to allocate", "VALUE", "WMS", None),
]

BUYER_ROLE = "Buyer"


def process_events(w):
    rng = w.rng
    ev = []

    def add(proc, case, act, t, role):
        ev.append((proc, case, act, t, role))

    auto_since = add_days(w.as_of, -21)
    for ln in w.po_lines:
        if not ln.get("confirm"):
            continue
        case = f"{ln['po_id']}-{ln['line_no']}"
        t = ln["po_created"]
        add("PO_CONFIRMATION", case, "Create PO", t, BUYER_ROLE)
        ch = {"KES": "EDI855", "HDS": "EDI855", "CSP": "PORTAL", "TNM": "PORTAL", "PNC": "EXCEL"}.get(ln["supplier"], "EMAIL")
        conf = ln["confirm"]
        if ch == "EDI855":
            add("PO_CONFIRMATION", case, "Transmit PO (EDI 850)", t + dt.timedelta(minutes=rng.randint(2, 20)), "System")
            add("PO_CONFIRMATION", case, "Supplier acknowledges (EDI 855)", conf, "Supplier")
            add("PO_CONFIRMATION", case, "Promise loaded to ERP", conf + dt.timedelta(minutes=rng.randint(1, 5)), "System")
            add("PO_CONFIRMATION", case, "MRP consumes promise", conf + dt.timedelta(minutes=rng.randint(10, 60)), "System")
            continue
        if ch == "PORTAL":
            add("PO_CONFIRMATION", case, "Publish PO to supplier portal", t + dt.timedelta(minutes=rng.randint(2, 20)), "System")
            add("PO_CONFIRMATION", case, "Supplier confirms in portal", conf, "Supplier")
            add("PO_CONFIRMATION", case, "Promise loaded to ERP", conf + dt.timedelta(minutes=rng.randint(1, 5)), "System")
            add("PO_CONFIRMATION", case, "MRP consumes promise", conf + dt.timedelta(minutes=rng.randint(10, 60)), "System")
            continue
        add("PO_CONFIRMATION", case, "Email PO PDF to supplier", t + dt.timedelta(hours=rng.uniform(0.5, 6)), BUYER_ROLE)
        chase_t = t + dt.timedelta(hours=rng.uniform(30, 60))
        if (conf - t).total_seconds() > 60 * 3600:
            for k in range(rng.randint(1, 3)):
                ct = chase_t + dt.timedelta(hours=26 * k)
                if ct < conf:
                    add("PO_CONFIRMATION", case, "Chase supplier by email", ct, BUYER_ROLE)
        reply = "Supplier sends open-order Excel" if ch == "EXCEL" else "Supplier replies by email"
        add("PO_CONFIRMATION", case, reply, conf, "Supplier")
        if conf.astimezone(PT).date() >= auto_since:
            add("PO_CONFIRMATION", case, "Platform parses email/Excel", conf + dt.timedelta(minutes=rng.randint(2, 15)), "System")
            t_load = conf + dt.timedelta(minutes=rng.randint(16, 40))
            if rng.random() < 0.1:                      # the parser abstained: a buyer answers before anything loads
                t_fix = conf + dt.timedelta(hours=rng.uniform(1, 6))
                add("PO_CONFIRMATION", case, "Buyer resolves parser abstention", t_fix, BUYER_ROLE)
                t_load = t_fix + dt.timedelta(minutes=rng.randint(1, 5))
            add("PO_CONFIRMATION", case, "Promise loaded to ERP", t_load, "System")
            add("PO_CONFIRMATION", case, "MRP consumes promise", t_load + dt.timedelta(minutes=rng.randint(20, 50)), "System")
            continue
        rekey = conf + dt.timedelta(hours=rng.uniform(2, 30))
        add("PO_CONFIRMATION", case, "Buyer copies Excel into tracker" if ch == "EXCEL" else
            "Buyer re-keys promise into tracker", rekey, BUYER_ROLE)
        erp = rekey + dt.timedelta(hours=rng.uniform(0.2, 20))
        add("PO_CONFIRMATION", case, "Buyer updates promise in ERP", erp, BUYER_ROLE)
        if len(ln.get("history", [])) > 1:
            add("PO_CONFIRMATION", case, "Escalate slip in chat", erp + dt.timedelta(hours=rng.uniform(0.5, 8)), BUYER_ROLE)
        d = erp.astimezone(PT).date()
        monday = add_days(d, (7 - d.weekday()) % 7 or 7)
        export = at(monday, 8, rng.randint(0, 40), PT).astimezone(UTC)
        add("PO_CONFIRMATION", case, "Planner exports open POs (CSV)", export, "Planner")
        add("PO_CONFIRMATION", case, "Planner updates plan workbook", export + dt.timedelta(hours=rng.uniform(1, 5)), "Planner")
        add("PO_CONFIRMATION", case, "MRP consumes promise", export + dt.timedelta(hours=rng.uniform(5, 8)), "Planner")

    # deviation approvals: the real four plus older ones
    for k in range(26):
        case = f"DEV-{k + 1:04d}"
        t = at(add_days(w.as_of, -170 + k * 6 + rng.randint(0, 3)), rng.randint(8, 15), 0, PT).astimezone(UTC)
        seq = [("Deviation requested by email", 0, "Engineer"), ("SQE drafts deviation in Word", rng.uniform(4, 30), "SQE"),
               ("Route for signatures", rng.uniform(1, 8), "SQE")]
        if rng.random() < 0.35:
            seq += [("Returned for more data", rng.uniform(10, 40), "Quality"),
                    ("Route for signatures", rng.uniform(6, 30), "SQE")]
        seq += [("Quality approves", rng.uniform(4, 30), "Quality"), ("Engineering approves", rng.uniform(4, 48), "Engineering"),
                ("Planner notified by email", rng.uniform(1, 30), "SQE"),
                ("Logged in deviation tracker", rng.uniform(2, 48), "SQE"),
                ("MES updated to allow part", rng.uniform(1, 24), "Manufacturing eng.")]
        for act, hrs, role in seq:
            t = t + dt.timedelta(hours=hrs)
            if t > w.now:
                break
            add("DEVIATION_APPROVAL", case, act, t, role)

    for c in w.containers:
        if c["received_at"] > w.now:
            continue
        case = c["shipment_id"]
        arrived = next(e[1] for e in c["events"] if e[0] == "ARRIVED")
        add("INBOUND_RECEIPT", case, "Arrival notice emailed", arrived - dt.timedelta(hours=rng.uniform(20, 40)), "Carrier")
        add("INBOUND_RECEIPT", case, "Dock appointment booked", arrived + dt.timedelta(hours=rng.uniform(2, 12)), "3PL")
        add("INBOUND_RECEIPT", case, "Container unloaded", c["received_at"], "3PL")
        add("INBOUND_RECEIPT", case, "Serials scanned", c["received_at"] + dt.timedelta(minutes=rng.randint(40, 110)), "3PL")
        t = c["received_at"] + dt.timedelta(hours=2)
        if c["short"] or c["damaged"]:
            add("INBOUND_RECEIPT", case, "Discrepancy emailed to the OEM", t + dt.timedelta(hours=rng.uniform(2, 20)), "3PL")
            t = t + dt.timedelta(hours=rng.uniform(24, 70))
            add("INBOUND_RECEIPT", case, "OEM reconciles ASN vs receipt in Excel", t, "OEM ops")
        add("INBOUND_RECEIPT", case, "Put away", t + dt.timedelta(hours=rng.uniform(1, 4)), "3PL")
        add("INBOUND_RECEIPT", case, "Available to allocate", t + dt.timedelta(hours=rng.uniform(4, 16)), "3PL")
    w.process_rows = [e for e in ev if e[3] <= w.now]


# ------------------------------------------------------------------ thin apps + buy vs build

FEATURES = [
    # app, feature, capability, weekly events at maturity, roles
    ("QUALITY_THIN", "q_trace", "QMS-TRACE", 260, ["SQE", "Quality mgr", "Ops"]),
    ("QUALITY_THIN", "q_hold", "QMS-HOLD", 140, ["SQE", "Ops"]),
    ("QUALITY_THIN", "q_iqc_record", "QMS-IQC", 180, ["IQC tech"]),
    ("QUALITY_THIN", "q_deviation", "QMS-DEV", 36, ["SQE", "Engineer"]),
    ("QUALITY_THIN", "q_spc", "QMS-SPC", 44, ["Quality mgr"]),
    ("QUALITY_THIN", "q_ncr_8d", "QMS-8D", 9, ["SQE"]),
    ("QUALITY_THIN", "q_supplier_scar", "QMS-SCAR", 7, ["SQE"]),
    ("QUALITY_THIN", "q_doc_control", "QMS-DOC", 4, ["Quality mgr"]),
    ("MOVE_THIN", "t_track", "TMS-TRACK", 320, ["Logistics", "CX", "Planner"]),
    ("MOVE_THIN", "t_eta_alerts", "TMS-ETA", 150, ["Logistics", "Planner"]),
    ("MOVE_THIN", "t_booking", "TMS-BOOK", 22, ["Logistics"]),
    ("MOVE_THIN", "t_customs_docs", "TMS-CUSTOMS", 18, ["Logistics"]),
    ("MOVE_THIN", "t_freight_audit", "TMS-AUDIT", 12, ["Finance"]),
    ("MOVE_THIN", "t_rate_shop", "TMS-RATE", 1, ["Logistics"]),
    ("DOCK_THIN", "w_serial_scan", "WMS-SERIAL", 290, ["3PL lead"]),
    ("DOCK_THIN", "w_kitting", "WMS-KIT", 210, ["3PL lead"]),
    ("DOCK_THIN", "w_hold", "WMS-HOLD", 40, ["3PL lead", "SQE"]),
    ("DOCK_THIN", "w_cycle_count", "WMS-CC", 30, ["3PL lead"]),
    ("DOCK_THIN", "w_returns", "WMS-RMA", 26, ["3PL lead", "CX"]),
    ("DOCK_THIN", "w_slotting", "WMS-SLOT", 0.5, ["3PL lead"]),
]
CAPABILITIES = [
    ("QMS-TRACE", "QMS", "Lot & serial trace (forward/backward)", "Recall scope in one query", 0.08),
    ("QMS-HOLD", "QMS", "Holds & quarantine", "Place/release holds across sites", 0.10),
    ("QMS-IQC", "QMS", "Incoming inspection records", "Sampling plans, results, dispositions", 0.12),
    ("QMS-DEV", "QMS", "Deviation workflow", "Request, approve, limit, expire", 0.10),
    ("QMS-SPC", "QMS", "SPC & control charts", "Control plans, Cpk, rule violations", 0.10),
    ("QMS-8D", "QMS", "8D / CAPA workflow", "Full eight-discipline problem solving", 0.22),
    ("QMS-SCAR", "QMS", "Supplier corrective action portal", "SCARs issued to suppliers", 0.14),
    ("QMS-DOC", "QMS", "Document control", "Controlled documents, training records", 0.14),
    ("TMS-TRACK", "TMS", "Track & trace", "Container, truck and parcel status", 0.15),
    ("TMS-ETA", "TMS", "ETA change alerts", "Proactive delay detection", 0.10),
    ("TMS-BOOK", "TMS", "Booking & tendering", "Book ocean/truck capacity", 0.18),
    ("TMS-CUSTOMS", "TMS", "Customs documents & ISF", "Entry packets, ISF timing", 0.12),
    ("TMS-AUDIT", "TMS", "Freight audit & pay", "Match invoices to rates", 0.15),
    ("TMS-RATE", "TMS", "Rate shopping", "Compare carrier quotes per load", 0.30),
    ("WMS-SERIAL", "WMS", "Serial capture at receipt", "Scan every unit against the ASN", 0.15),
    ("WMS-KIT", "WMS", "Kitting (vehicle + pack + charger)", "Build the sellable kit to order", 0.15),
    ("WMS-HOLD", "WMS", "Quarantine holds", "Honor holds from quality", 0.10),
    ("WMS-CC", "WMS", "Cycle counting", "Count and reconcile", 0.10),
    ("WMS-RMA", "WMS", "Returns / RMA", "Receive and disposition returns", 0.10),
    ("WMS-SLOT", "WMS", "Slotting optimization", "Optimize pick locations", 0.40),
]
VENDORS = [
    ("QMS-A", "QMS", "QMS Vendor A · enterprise suite", 186000, 26, "Deep 8D/doc control; trace via add-on module"),
    ("QMS-B", "QMS", "QMS Vendor B · mid-market", 64000, 10, "Strong IQC/holds/trace; light 8D"),
    ("QMS-X", "QMS", "Extend the thin app", 38000, 6, "Keep building on what people use"),
    ("TMS-A", "TMS", "TMS Vendor A · full TMS", 140000, 20, "Tendering, rating, audit"),
    ("TMS-B", "TMS", "Visibility platform B", 52000, 6, "Multi-carrier track & ETA prediction"),
    ("TMS-X", "TMS", "Extend the thin app", 30000, 5, "Carrier APIs + EDI 315 already integrated"),
    ("WMS-3", "WMS", "Use the 3PL's WMS", 0, 2, "Their system, our contracts on top"),
    ("WMS-A", "WMS", "WMS Vendor A", 120000, 24, "Full WMS with slotting"),
    ("WMS-X", "WMS", "Extend the thin app", 26000, 5, "Serial + kitting layer over the 3PL feed"),
]
FIT = {
    "QMS-A": {"QMS-TRACE": 1, "QMS-HOLD": 2, "QMS-IQC": 2, "QMS-DEV": 3, "QMS-SPC": 2, "QMS-8D": 3, "QMS-SCAR": 3, "QMS-DOC": 3},
    "QMS-B": {"QMS-TRACE": 3, "QMS-HOLD": 3, "QMS-IQC": 3, "QMS-DEV": 2, "QMS-SPC": 2, "QMS-8D": 1, "QMS-SCAR": 1, "QMS-DOC": 1},
    "QMS-X": {"QMS-TRACE": 3, "QMS-HOLD": 3, "QMS-IQC": 2, "QMS-DEV": 2, "QMS-SPC": 1, "QMS-8D": 0, "QMS-SCAR": 0, "QMS-DOC": 0},
    "TMS-A": {"TMS-TRACK": 2, "TMS-ETA": 1, "TMS-BOOK": 3, "TMS-CUSTOMS": 2, "TMS-AUDIT": 3, "TMS-RATE": 3},
    "TMS-B": {"TMS-TRACK": 3, "TMS-ETA": 3, "TMS-BOOK": 1, "TMS-CUSTOMS": 1, "TMS-AUDIT": 0, "TMS-RATE": 0},
    "TMS-X": {"TMS-TRACK": 3, "TMS-ETA": 2, "TMS-BOOK": 1, "TMS-CUSTOMS": 2, "TMS-AUDIT": 1, "TMS-RATE": 0},
    "WMS-3": {"WMS-SERIAL": 2, "WMS-KIT": 1, "WMS-HOLD": 1, "WMS-CC": 3, "WMS-RMA": 2, "WMS-SLOT": 3},
    "WMS-A": {"WMS-SERIAL": 3, "WMS-KIT": 2, "WMS-HOLD": 3, "WMS-CC": 3, "WMS-RMA": 3, "WMS-SLOT": 3},
    "WMS-X": {"WMS-SERIAL": 3, "WMS-KIT": 3, "WMS-HOLD": 3, "WMS-CC": 1, "WMS-RMA": 1, "WMS-SLOT": 0},
}


def telemetry(w):
    rng = w.rng
    rows = []
    start = week_start(add_days(w.as_of, -56))
    for k in range(9):
        ws = add_days(start, 7 * k)
        ramp = min(1.0, 0.35 + 0.1 * k)
        for app, feature, cap, weekly, roles in FEATURES:
            lam = weekly * ramp
            n = int(lam) + (1 if rng.random() < lam - int(lam) else 0)
            for _ in range(n):
                t = at(add_days(ws, rng.randint(0, 4)), rng.randint(7, 18), rng.randint(0, 59), PT).astimezone(UTC)
                if t > w.now:
                    continue
                rows.append((app, t, rng.choice(roles), feature, round(rng.uniform(8, 240), 1),
                             "completed" if rng.random() < 0.93 else "abandoned"))
    w.telemetry_rows = rows


# ------------------------------------------------------------------ system landscape + integration options

SYSTEMS = [
    ("MES-CM", "FAP MES (Taichung)", "MES", "CM", "Formosa Assembly Partners", "TW", "API", "INBOUND", "Streaming (per station event)",
     "raw_cm_mes_event", "CM-owned MES; JSON webhook to our gateway. Local time UTC+8; bilingual defect text."),
    ("RPT-CM", "FAP daily production report", "EMAIL", "CM", "Formosa Assembly Partners", "TW", "EMAIL_XLSX", "INBOUND",
     "Daily 18:40 Taipei", "raw_email / raw_attachment", "Bilingual Excel: output, defects, consigned stock, scrap in NTD"),
    ("MES-FRE", "OEM pack line MES (Fremont)", "MES", "OEM", "OEM", "US", "NATIVE", "BOTH", "Streaming",
     "station_event", "Our own line; writes the core directly"),
    ("ERP", "OEM ERP", "ERP", "OEM", "OEM", "US", "NATIVE", "BOTH", "Real time",
     "purchase_order / goods_receipt / supplier_invoice / erp_journal_entry", "Item master, BOMs, POs, AP, GL"),
    ("QMS", "Quality thin app", "QMS", "OEM", "OEM", "US", "NATIVE", "BOTH", "Real time",
     "quality_event / deviation / capa / hold", "Thin, disposable app instrumented to settle buy vs build"),
    ("PLAN", "Planning engine (MRP / ATP)", "PLANNING", "OEM", "OEM", "US", "NATIVE", "BOTH", "On demand + nightly",
     "mrp_run / planned_order / order_promise", "Multi-level MRP, ATP/CTP, replenishment"),
    ("WMS-3PL", "Sierra Fulfillment WMS", "WMS", "3PL", "Sierra Fulfillment", "US", "API", "BOTH", "Near real time",
     "raw_3pl_message", "Receipts, allocations, ship confirms, nightly snapshot"),
    ("TMS-OCEAN", "Pacific Link Lines", "TMS", "CARRIER", "Pacific Link Lines", "TW", "EDI", "INBOUND", "Per milestone",
     "raw_carrier_event", "EDI 315 container status + ETA updates"),
    ("TMS-DG", "Sierra DG Freight", "TMS", "CARRIER", "Sierra DG Freight", "US", "API", "INBOUND", "Per milestone",
     "raw_carrier_event", "Hazmat (UN3480) LTL Fremont to Reno"),
    ("TMS-PARCEL", "Crossway / ParcelPro", "TMS", "CARRIER", "Crossway Freight; ParcelPro Ground", "US", "API", "INBOUND",
     "Per scan", "raw_carrier_event", "Last-mile tracking"),
    ("BROKER", "Customs broker", "BROKER", "BROKER", "Bayline Customs Brokerage", "US", "API", "INBOUND", "Per entry",
     "customs_entry", "ISF, entry summary, holds, releases"),
    ("EDI-VAN", "EDI VAN (850/855/856/830)", "EDI", "SUPPLIER", "Kestrel, Hsinchu Display, Tainan Motion", "KR/TW",
     "EDI", "BOTH", "Per document", "raw_supplier_confirmation / raw_supplier_asn", "PO acks, ASNs, forecast (830) out"),
    ("PORTAL", "Supplier portal", "PORTAL", "OEM", "OEM", "US", "PORTAL", "BOTH", "Per confirmation",
     "raw_supplier_confirmation", "Long-tail suppliers confirm here"),
    ("MAIL-PO", "po-confirm@ mailbox", "EMAIL", "SUPPLIER", "Summit, Norte, Bayline, Voltaic, Pinecrest", "MX/US/TW",
     "EMAIL_TEXT", "INBOUND", "As they arrive", "raw_email", "Free-text confirmations (EN/ES) + Pinecrest weekly Excel"),
    ("MAIL-SUP", "supplier-reports@ mailbox", "EMAIL", "SUPPLIER", "Kestrel Cell", "KR", "EMAIL_XLSX", "INBOUND", "Weekly",
     "raw_email", "VMI stock and capacity workbook"),
    ("CRM", "Service CRM", "CRM", "OEM", "OEM", "US", "WEBHOOK", "INBOUND", "Per case", "raw_warranty_case",
     "Warranty cases, symptoms, parts replaced"),
]
OPTIONS = [
    # system, option, channel, latency min, touches/wk, error %, build wks, run $/mo, depends on, pros, cons, status
    ("RPT-CM", "Email + Excel, parsed automatically", "EMAIL_XLSX", 720, 1, 1.8, 1, 0, None,
     "Zero ask of the CM; live in a week", "Daily latency; layout drift breaks parsers", "CURRENT"),
    ("RPT-CM", "CM MES API: add consigned-stock endpoint", "API", 2, 0, 0.3, 4, 300, "CM IT sprint (Q4)",
     "Same pipe as station events; minutes, not a day", "Needs CM engineering time", "RECOMMENDED"),
    ("RPT-CM", "SFTP CSV drop from CM ERP", "SFTP_CSV", 1440, 0, 0.8, 2, 50, None, "Stable schema",
     "Still daily; another pipe to monitor", "CONSIDERED"),
    ("RPT-CM", "CM planners type into our portal", "PORTAL", 240, 10, 2.5, 3, 0, None, "Structured at entry",
     "Moves the re-keying to the CM; they will resent it", "REJECTED"),
    ("MAIL-PO", "Buyer reads email and re-keys (before)", "EMAIL_TEXT", 5900, 58, 3.2, 0, 0, None,
     "No build", "4-day latency to plan; three copies of each date", "REJECTED"),
    ("MAIL-PO", "Parse email + Excel automatically (abstain when unsure)", "EMAIL_TEXT", 15, 6, 0.0, 2, 0, None,
     "Suppliers change nothing; humans see only abstentions", "Coverage ~88%, not 100%", "CURRENT"),
    ("MAIL-PO", "EDI 855 via VAN for top-5 suppliers", "EDI", 60, 0, 0.2, 6, 400, "Supplier EDI onboarding",
     "Structured, standard", "Weeks per supplier; VAN fees", "RECOMMENDED"),
    ("MAIL-PO", "Supplier portal for the long tail", "PORTAL", 480, 0, 0.5, 3, 0, "Portal adoption",
     "Free for suppliers", "Adoption is a people problem", "RECOMMENDED"),
    ("WMS-3PL", "3PL REST API + nightly snapshot", "API", 5, 0, 0.2, 3, 0, None, "Serial-level, near real time",
     "Their outage is our blind spot", "CURRENT"),
    ("WMS-3PL", "Nightly CSV from 3PL", "SFTP_CSV", 1440, 1, 1.0, 1, 0, None, "Cheap", "No intraday allocations", "CONSIDERED"),
    ("WMS-3PL", "Log in to the 3PL web portal", "PORTAL", 600, 20, 4.0, 0, 0, None, "No build",
     "People as the integration layer", "REJECTED"),
    ("TMS-OCEAN", "Carrier EDI 315", "EDI", 30, 0, 0.5, 2, 150, None, "Standard milestones", "Single carrier only", "CURRENT"),
    ("TMS-OCEAN", "Multi-carrier visibility platform", "API", 15, 0, 0.3, 3, 1200, "Second ocean carrier",
     "Predictive ETA; carrier-agnostic", "Cost only pays off with 2+ carriers", "CONSIDERED"),
    ("TMS-OCEAN", "Scrape the carrier website", "PORTAL", 240, 2, 6.0, 1, 0, None, "Fast to start",
     "Breaks silently; terms-of-service risk", "REJECTED"),
    ("CRM", "Webhook per case", "WEBHOOK", 1, 0, 0.1, 1, 0, None, "Real time", "Needs retry/replay handling", "CURRENT"),
    ("CRM", "Nightly export", "SFTP_CSV", 1440, 0, 0.5, 1, 0, None, "Simple", "Day-late warranty signal", "CONSIDERED"),
]


# ------------------------------------------------------------------ eval suites + golden truth

SUITES = [
    ("EV-CM-MES", "CM MES normalizer rebuilds each vehicle's station history and as-built",
     "ops.ingest.cm_mes", "GOLDEN_TRUTH", "exact-match rate", 0.995,
     "For every sampled vehicle, compare what the normalizer wrote (events, stations, frame, DU, HMI, PU) with what "
     "physically happened in the simulation. Catches dropped, duplicated, mis-timed or mis-mapped messages."),
    ("EV-GENEALOGY", "Recall trace returns exactly the truly affected units", "ops.logic.genealogy", "GOLDEN_TRUTH",
     "set-exact rate", 1.0,
     "Forward-trace cell lots and drive units; the returned set must equal the true set. A miss is a recall hole."),
    ("EV-WARRANTY-CLS", "Warranty symptom classifier", "ops.ingest.warranty", "LABELED_SET", "accuracy", 0.90,
     "Free-text CRM symptoms mapped to defect codes, scored against the true failure mode. An LLM classifier would be "
     "swapped in behind the same harness and must beat this score to ship."),
    ("EV-PROMISE-PARSE", "Supplier email & Excel promise extraction", "ops.ingest.email", "LABELED_SET",
     "precision on committed parses", 1.0,
     "Every date the parser writes must be right (precision 100%); when unsure it must abstain. Coverage is tracked "
     "and must stay >= 80%."),
    ("EV-ATP-BACKTEST", "ATP promises vs actual delivery", "ops.logic.atp", "BACKTEST", "on-time rate", 0.85,
     "Delivered orders from the last 60 days: was the first promise kept?"),
    ("EV-MRP-TEXTBOOK", "MRP netting against hand-computed cases", "ops.logic.mrp", "TEXTBOOK_CASES",
     "exact-match rate", 1.0,
     "Lot-for-lot, MOQ + multiples, safety stock, lead-time offset, multi-level explosion with effectivity, "
     "alternate part under deviation. Every planned order must match the hand calculation."),
]


def golden(w):
    rng = w.rng
    rows = []
    # EV-CM-MES: all vehicles with rework at S65 or events in the noisy windows, plus a random sample
    def truth_du(v):
        return v["du_slots"][-1][1]["serial"] if v["du_slots"] else None

    special, rest = [], []
    for v in w.vehicles:
        codes = {e["code"] for e in v["events"]}
        t0 = v["events"][0]["t"] if v["events"] else None
        noisy = t0 and (w.tz_bug[0] <= t0.astimezone(TPE).date() <= w.tz_bug[1]
                        or abs((t0 - w.retry_storm[0]).total_seconds()) < 4 * 3600
                        or abs((t0 - w.schema_cutover).total_seconds()) < 4 * 3600)
        (special if ("S65" in codes or noisy) else rest).append(v)
    sample = special + rng.sample(rest, min(400, len(rest)))
    for v in sample:
        if not v["events"]:
            continue
        exp = {"events": len(v["events"]), "stations": sorted({e["code"] for e in v["events"]}),
               "first_ts": v["events"][0]["t"].strftime("%Y-%m-%dT%H:%M:%SZ"),
               "frame": v.get("frame"), "du": truth_du(v),
               "hmi": v["hmi_slots"][-1][1]["serial"] if v["hmi_slots"] else None,
               "pu": v.get("pu_slot", {}).get("serial") if v.get("pu_slot") else None,
               "built": bool(v["built_at"])}
        rows.append(("EV-CM-MES", v["serial"], f"unit:{v['serial']}", json.dumps(exp)))
    # EV-GENEALOGY: cell lots -> packs that truly contain them; drive units -> the vehicle they truly sit in
    packs_by_lot = {}
    for p in w.packs:
        for ev in p["lot_uses"]:
            if ev["family"] == "CEL-21700":
                for lot_id, q, _ in ev["alloc"]:
                    packs_by_lot.setdefault(lot_id, set()).add(p["serial"])
    lots = sorted(packs_by_lot)
    pick = set(rng.sample(lots, min(24, len(lots))))
    pick.add(w.stories["bad_cell_lot"])
    for lot_id in sorted(pick):
        rows.append(("EV-GENEALOGY", f"lot:{lot_id}", f"lot:{lot_id}",
                     json.dumps({"kind": "lot->packs", "expected": sorted(packs_by_lot[lot_id])})))
    du_home = {}
    for v in w.vehicles:
        for k, (t, slot) in enumerate(v["du_slots"]):
            du_home[slot["serial"]] = v["serial"] if k == len(v["du_slots"]) - 1 else None
    swapped = [s for s, home in du_home.items() if home is None]
    installed = [s for s, home in du_home.items() if home]
    s65_vehicles = {v["serial"] for v in w.vehicles if any(e["code"] == "S65" for e in v["events"])}
    s65_dus = [v["du_slots"][-1][1]["serial"] for v in w.vehicles if v["serial"] in s65_vehicles and len(v["du_slots"]) > 1]
    du_pick = set(rng.sample(installed, min(20, len(installed)))) | set(swapped[-6:]) | set(s65_dus)
    for du in sorted(du_pick):
        rows.append(("EV-GENEALOGY", f"du:{du}", f"unit:{du}",
                     json.dumps({"kind": "du->vehicle", "expected": [du_home[du]] if du_home[du] else []})))
    # EV-WARRANTY-CLS: the true failure mode behind every CRM case
    for c in w.claims:
        rows.append(("EV-WARRANTY-CLS", c["case_no"], f"case:{c['case_no']}", json.dumps({"defect_code": c["code"]})))
    w.golden_rows = rows
    w.golden_promises = []          # filled by emails.py (needs message ids)


def build(w):
    process_events(w)
    telemetry(w)
    golden(w)
