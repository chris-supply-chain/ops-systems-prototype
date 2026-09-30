"""Integration Hub: every inbound feed with live health, the email pipeline traced
step by step (with the original Excel attachments), a parser playground, and the
integration options per source.
"""
import base64
import datetime as dt
import re
from collections import defaultdict

from ...db import now, q, q1
from ...ingest.documents import VAGUE, EmailPipeline, SUPPLIER_BY_DOMAIN, parse_confirmation
from ...xlsx import col_letter, read_xlsx
from ..router import HttpError, Raw, get, post

CHANNEL_ORDER = ["API", "EDI", "EMAIL_XLSX", "EMAIL_TEXT", "PORTAL", "WEBHOOK", "NATIVE"]
CHANNEL_LABEL = {"API": "API / webhook-in", "EDI": "EDI (X12 / 315)", "EMAIL_XLSX": "Email + Excel",
                 "EMAIL_TEXT": "Email (free text)", "PORTAL": "Supplier portal", "WEBHOOK": "Webhook",
                 "NATIVE": "Native (system of record writes the core)"}
PAGES = {"MES-CM": ["cm-feed", "production", "genealogy"], "RPT-CM": ["integrations", "cm-feed", "inventory"],
         "MES-FRE": ["production", "genealogy"], "ERP": ["erp", "suppliers"], "QMS": ["quality", "warranty"],
         "PLAN": ["mrp", "atp", "mps"], "WMS-3PL": ["inventory", "atp"], "TMS-OCEAN": ["shipments"],
         "TMS-DG": ["shipments"], "TMS-PARCEL": ["shipments"], "BROKER": ["shipments"], "EDI-VAN": ["suppliers"],
         "PORTAL": ["suppliers"], "MAIL-PO": ["integrations", "suppliers"], "MAIL-SUP": ["integrations", "inventory"],
         "CRM": ["warranty"]}

# (table, where, time column, status column) per landing source; several tables add up
RAW_SOURCES = {
    "MES-CM": [("raw_cm_mes_event", "1=1", "received_at", "ingest_status")],
    "RPT-CM": [("raw_email", "classified_as = 'CM_DAILY_REPORT' OR from_addr LIKE '%formosa-ap.example'", "received_at", "ingest_status")],
    "WMS-3PL": [("raw_3pl_message", "1=1", "received_at", "ingest_status")],
    "TMS-OCEAN": [("raw_carrier_event", "carrier = 'PLL'", "received_at", "ingest_status")],
    "TMS-DG": [("raw_carrier_event", "carrier = 'SDG'", "received_at", "ingest_status")],
    "TMS-PARCEL": [("raw_carrier_event", "carrier IN ('CWF','PPG')", "received_at", "ingest_status")],
    "EDI-VAN": [("raw_supplier_confirmation", "channel = 'EDI855'", "received_at", "ingest_status"),
                ("raw_supplier_asn", "1=1", "received_at", "ingest_status")],
    "PORTAL": [("raw_supplier_confirmation", "channel = 'PORTAL'", "received_at", "ingest_status")],
    "MAIL-PO": [("raw_email", "mailbox = 'po-confirm@'", "received_at", "ingest_status")],
    "MAIL-SUP": [("raw_email", "mailbox = 'supplier-reports@'", "received_at", "ingest_status")],
    "CRM": [("raw_warranty_case", "1=1", "received_at", "ingest_status")],
}
# OEM-native systems write the core directly: count their records instead
NATIVE_SOURCES = {
    "MES-FRE": [("station_event", "source = 'OEM_MES'", "event_ts")],
    "ERP": [("goods_receipt", "1=1", "received_at"), ("supplier_invoice", "1=1", "invoice_date"),
            ("erp_journal_entry", "1=1", "posted_at")],
    "QMS": [("quality_event", "1=1", "detected_at"), ("hold", "1=1", "placed_at"), ("deviation", "1=1", "valid_from")],
    "PLAN": [("mrp_run", "1=1", "ran_at"), ("order_promise", "1=1", "decided_at")],
    "BROKER": [("customs_entry", "1=1", "isf_filed_at"), ("shipment_event", "source = 'BROKER'", "event_ts")],
}

# The parsers' header synonyms (mirrors ops/ingest/documents.py) so the viewer can show what they matched
HEADER_SYNONYMS = {
    "CM_DAILY_REPORT": {"line": ["線別", "line"], "model": ["機種", "model"], "pn": ["料號", "p/n"], "plan": ["計畫", "plan"],
                        "actual": ["實際", "actual"], "wip": ["在製", "wip"], "scrap_ntd": ["金額", "ntd"],
                        "scrap": ["報廢 scrap"]},
    "CM_STOCK": {"pn": ["料號", "part"], "good": ["良品", "good"], "qc": ["待驗", "qc"]},
    "CM_DEFECTS": {"line": ["線別", "line"], "station": ["站別", "station"], "code": ["不良代碼", "code"],
                   "desc": ["不良描述", "description"], "qty": ["數量", "qty"]},
    "PNC_OPEN_ORDERS": {"po": ["採購單號", "po no"], "line": ["項次", "line"], "part": ["料號", "part"],
                        "qty": ["訂購量", "order qty"], "open": ["未交量", "open qty"], "etd": ["出貨日", "etd"],
                        "eta": ["到貨日", "eta"], "remark": ["備註", "remark"]},
    "KES_VMI": {"part": ["part"], "fg": ["fg on hand"], "wip": ["in production"], "reserved": ["reserved"],
                "transit": ["in transit"]},
}
FEED_LABEL = {"CM_DAILY_REPORT": "CM daily production report (Output sheet)", "CM_STOCK": "CM consigned-stock sheet",
              "CM_DEFECTS": "CM defect summary sheet", "PNC_OPEN_ORDERS": "Pinecrest open-order report",
              "KES_VMI": "Kestrel VMI & capacity report"}
MAX_ROWS, MAX_COLS = 80, 16


def _cutoff(conn, days):
    t = dt.datetime.strptime(now(conn), "%Y-%m-%dT%H:%M:%SZ") - dt.timedelta(days=days)
    return t.strftime("%Y-%m-%dT%H:%M:%S")


def _norm_ts(s):
    """Timestamps arrive as '...Z', '+00:00' with microseconds, or a local offset ('+08:00' from the CM);
    hand the UI one shape: UTC 'YYYY-MM-DDTHH:MM:SSZ' (dates stay dates)."""
    if not s:
        return s
    s = str(s)
    if len(s) == 10:
        return s
    try:
        t = dt.datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith("Z") else s)
    except ValueError:
        return s
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _health(conn, sid, cut7, cut14):
    now_s = now(conn)
    out = {"kind": "raw" if sid in RAW_SOURCES else "native", "total": 0, "last_7d": 0, "last_at": None,
           "status": defaultdict(int), "daily": defaultdict(int)}
    if sid in RAW_SOURCES:
        for table, where, tcol, scol in RAW_SOURCES[sid]:
            for r in q(conn, f"SELECT {scol} AS s, COUNT(*) AS n, MAX({tcol}) AS last, "
                             f"SUM(CASE WHEN {tcol} >= ? THEN 1 ELSE 0 END) AS n7 FROM {table} WHERE ({where}) AND {tcol} <= ?"
                             f" GROUP BY {scol}", (cut7, now_s)):
                out["status"][r["s"]] += r["n"]
                out["total"] += r["n"]
                out["last_7d"] += r["n7"] or 0
                if r["last"] and (out["last_at"] is None or _norm_ts(r["last"]) > out["last_at"]):
                    out["last_at"] = _norm_ts(r["last"])
            for r in q(conn, f"SELECT substr({tcol}, 1, 10) AS d, COUNT(*) AS n FROM {table} WHERE ({where}) AND {tcol} >= ?"
                             f" AND {tcol} <= ? GROUP BY d", (cut14, now_s)):
                out["daily"][r["d"]] += r["n"]
    else:
        for table, where, tcol in NATIVE_SOURCES.get(sid, []):
            r = q1(conn, f"SELECT COUNT(*) AS n, MAX({tcol}) AS last, SUM(CASE WHEN {tcol} >= ? THEN 1 ELSE 0 END) AS n7"
                         f" FROM {table} WHERE ({where}) AND {tcol} <= ?", (cut7, now_s))
            out["total"] += r["n"]
            out["last_7d"] += r["n7"] or 0
            last = _norm_ts(r["last"]) if r["last"] else None
            if last and len(last) == 10:
                last += "T00:00:00Z"
            if last and last > now_s:
                last = now_s
            if last and (out["last_at"] is None or last > out["last_at"]):
                out["last_at"] = last
            for d in q(conn, f"SELECT substr({tcol}, 1, 10) AS d, COUNT(*) AS n FROM {table} WHERE ({where}) AND {tcol} >= ?"
                             f" AND {tcol} <= ? GROUP BY d", (cut14, now_s)):
                out["daily"][d["d"]] += d["n"]
    out["status"] = dict(out["status"])
    out["daily"] = dict(out["daily"])
    ok = sum(v for k, v in out["status"].items() if k in ("OK", "REPLAYED", "IGNORED", "DUPLICATE"))
    out["automated_share"] = (ok + out["status"].get("WARN", 0)) / out["total"] if (out["kind"] == "raw" and out["total"]) else None
    out["quarantined"] = out["status"].get("QUARANTINED", 0)
    return out


@get(r"^/api/integrations/overview$")
def overview(req):
    conn = req.conn
    cut7, cut14 = _cutoff(conn, 7), _cutoff(conn, 14)
    now_s = now(conn)
    systems = q(conn, "SELECT * FROM source_system")
    options = q(conn, "SELECT * FROM integration_option")
    opt_by = defaultdict(list)
    for o in options:
        opt_by[o["system_id"]].append(o)
    days = [(dt.datetime.strptime(now_s, "%Y-%m-%dT%H:%M:%SZ") - dt.timedelta(days=13 - k)).strftime("%Y-%m-%d")
            for k in range(14)]
    out = []
    for s in systems:
        h = _health(conn, s["system_id"], cut7, cut14)
        cur = next((o for o in opt_by[s["system_id"]] if o["status"] == "CURRENT"), None)
        out.append({**s, "health": h, "spark": [h["daily"].get(d, 0) for d in days], "pages": PAGES.get(s["system_id"], []),
                    "options": len(opt_by[s["system_id"]]),
                    "current_option": cur["option"] if cur else None,
                    "recommended": [o["option"] for o in opt_by[s["system_id"]] if o["status"] == "RECOMMENDED"]})
    out.sort(key=lambda s: (CHANNEL_ORDER.index(s["channel"]) if s["channel"] in CHANNEL_ORDER else 99, s["system_id"]))
    runs = q(conn, "SELECT * FROM ingest_run ORDER BY run_id")
    raw_total = sum(s["health"]["total"] for s in out if s["health"]["kind"] == "raw")
    quarantined = sum(s["health"]["quarantined"] for s in out if s["health"]["kind"] == "raw")
    landed7 = sum(s["health"]["last_7d"] for s in out if s["health"]["kind"] == "raw")
    before = after = 0.0
    for sid, opts in opt_by.items():
        cur = next((o for o in opts if o["status"] == "CURRENT"), None)
        old = next((o for o in opts if "(before)" in o["option"]), None)
        after += cur["touches_per_week"] if cur else 0
        before += (old or cur)["touches_per_week"] if (old or cur) else 0
    open_q = q(conn, "SELECT 'raw_cm_mes_event' AS t, raw_id, received_at, ingest_note FROM raw_cm_mes_event WHERE ingest_status='QUARANTINED'"
                     " UNION ALL SELECT 'raw_email', raw_id, received_at, ingest_note FROM raw_email WHERE ingest_status='QUARANTINED'"
                     " UNION ALL SELECT 'raw_supplier_confirmation', raw_id, received_at, ingest_note FROM raw_supplier_confirmation WHERE ingest_status='QUARANTINED'"
                     " UNION ALL SELECT 'raw_carrier_event', raw_id, received_at, ingest_note FROM raw_carrier_event WHERE ingest_status='QUARANTINED'"
                     " UNION ALL SELECT 'raw_3pl_message', raw_id, received_at, ingest_note FROM raw_3pl_message WHERE ingest_status='QUARANTINED'"
                     " UNION ALL SELECT 'raw_warranty_case', raw_id, received_at, ingest_note FROM raw_warranty_case WHERE ingest_status='QUARANTINED'"
                     " ORDER BY received_at DESC")
    for r in open_q:
        r["received_at"] = _norm_ts(r["received_at"])
    return {
        "now": now_s, "days": days,
        "kpis": {"sources": len(out), "external": sum(1 for s in out if s["owner"] != "OEM"),
                 "raw_total": raw_total, "landed_7d": landed7, "quarantined": quarantined,
                 "automated_share": (raw_total - quarantined) / raw_total if raw_total else None,
                 "touches_before": before, "touches_after": after,
                 "channels": {c: sum(1 for s in out if s["channel"] == c) for c in CHANNEL_ORDER}},
        "channel_order": CHANNEL_ORDER, "channel_label": CHANNEL_LABEL,
        "sources": out, "runs": runs, "quarantine": open_q,
    }


# ------------------------------------------------------------------ email pipeline

@get(r"^/api/integrations/emails$")
def emails(req):
    rows = q(req.conn, "SELECT e.raw_id, e.mailbox, e.received_at, e.from_addr, e.subject, e.classified_as, e.ingest_status,"
                       " e.ingest_note, (SELECT COUNT(*) FROM raw_attachment a WHERE a.raw_id = e.raw_id) AS attachments"
                       " FROM raw_email e ORDER BY e.received_at DESC, e.raw_id DESC")
    for r in rows:
        r["received_at"] = _norm_ts(r["received_at"])
    mailboxes = sorted({r["mailbox"] for r in rows})
    samples = q(req.conn, "SELECT a.attachment_id, a.filename, a.parser, a.raw_id FROM raw_attachment a"
                          " WHERE a.attachment_id IN (SELECT MAX(attachment_id) FROM raw_attachment WHERE parse_status != 'SKIPPED'"
                          " GROUP BY parser) ORDER BY a.parser")
    return {"emails": rows, "mailboxes": mailboxes, "samples": samples}


def _mark_sheet(name, rows, parser):
    """Grid for the viewer plus what the parser would match: header row, mapped columns, title/totals rows."""
    candidates = []
    if parser == "CM_DAILY_REPORT":
        low = name.lower()
        candidates = ["CM_STOCK"] if ("consigned" in low or "寄售" in name) else (
            ["CM_DEFECTS"] if ("defect" in low or "不良" in name) else ["CM_DAILY_REPORT"])
    elif parser in HEADER_SYNONYMS:
        candidates = [parser]
    else:
        candidates = list(HEADER_SYNONYMS)
    best = None
    for feed in candidates:
        try:
            hi, cols = EmailPipeline.find_header(rows, HEADER_SYNONYMS[feed], need=3)
        except ValueError:
            continue
        if best is None or len(cols) > len(best[2]):
            best = (feed, hi, cols)
    ncols = min(MAX_COLS, max((len(r) for r in rows), default=0))
    grid = [[_cell(v) for v in (r[:ncols] + [None] * (ncols - len(r[:ncols])))] for r in rows[:MAX_ROWS]]
    out = {"name": name, "rows": grid, "n_rows": len(rows), "n_cols": ncols, "truncated": len(rows) > MAX_ROWS,
           "letters": [col_letter(i) for i in range(ncols)], "feed": None, "header_row": None, "mapped": {},
           "unmapped_cols": [], "title_rows": [], "totals_row": None}
    if best:
        feed, hi, cols = best
        out.update(feed=feed, feed_label=FEED_LABEL.get(feed, feed), header_row=hi,
                   mapped={str(v): k for k, v in cols.items()},
                   unmapped_cols=[j for j, c in enumerate(rows[hi][:ncols]) if c not in (None, "") and j not in cols.values()],
                   title_rows=[i for i in range(hi) if any(c not in (None, "") for c in rows[i])])
        for i in range(hi + 1, len(rows)):
            first = rows[i][0] if rows[i] else None
            if isinstance(first, str) and ("合計" in first or "total" in first.lower()):
                out["totals_row"] = i
                break
    return out


def _cell(v):
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


HL = [  # (type, regex, group) in priority order; spans never overlap
    ("po", re.compile(r"\b4[57]\d{5}(?:-\d{1,2})?\b"), 0),
    ("date", re.compile(r"(?:ETD|ETA)\s+\d{1,2}-[A-Za-z]{3}-\d{4}"), 0),
    ("date", re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), 0),
    ("date", re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b"), 0),
    ("date", re.compile(r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)"
                        r"\s+\d{1,2},\s*\d{4}", re.I), 0),
    ("line", re.compile(r"\b(?:line|partida|item|l[ií]nea)\s*#?\s*\d{1,2}\b", re.I), 0),
    ("qty", re.compile(r"[\d][\d,]*\s*(?:pcs|piezas|units|unidades)\b", re.I), 0),
    ("qty", re.compile(r"\bqty\.?\s*[\d][\d,]*", re.I), 0),
    ("qty", re.compile(r"\(\s*[\d,]+\s*\)"), 0),
    ("basis", re.compile(r"ship date|\bETD\b|\bETA\b|entrega|delivery|move delivery to|need date|confirmed|confirmamos", re.I), 0),
    ("vague", VAGUE, 0),
]


def highlight(text):
    """Split text into segments tagged with what the promise parser keys on."""
    spans = []
    for kind, rx, g in HL:
        for m in rx.finditer(text or ""):
            s, e = m.span(g)
            if any(not (e <= a or s >= b) for a, b, _ in spans):
                continue
            spans.append((s, e, kind))
    spans.sort()
    segs, pos = [], 0
    for s, e, kind in spans:
        if s > pos:
            segs.append({"text": text[pos:s], "type": None})
        segs.append({"text": text[s:e], "type": kind})
        pos = e
    if pos < len(text or ""):
        segs.append({"text": text[pos:], "type": None})
    return segs


@get(r"^/api/integrations/email/(\d+)$")
def email_detail(req):
    conn = req.conn
    rid = int(req.params[0])
    e = q1(conn, "SELECT raw_id, mailbox, received_at, from_addr, to_addr, subject, message_id, body_text, classified_as,"
                 " ingest_status, ingest_note, length(mime) AS mime_bytes FROM raw_email WHERE raw_id=?", (rid,))
    if not e:
        raise HttpError(404, f"no email {rid}")
    e["received_at"] = _norm_ts(e["received_at"])
    steps = q(conn, "SELECT seq, step, status, at, duration_ms, detail FROM ingest_step WHERE source_ref=? ORDER BY seq, step_id",
              (f"raw_email:{rid}",))
    for s in steps:
        s["at"] = _norm_ts(s["at"])
    atts = []
    for a in q(conn, "SELECT * FROM raw_attachment WHERE raw_id=? ORDER BY attachment_id", (rid,)):
        content = a.pop("content")
        dup = None
        if a["parse_status"] == "SKIPPED":
            dup = q1(conn, "SELECT a.attachment_id, a.raw_id, e.received_at, e.subject FROM raw_attachment a"
                           " JOIN raw_email e ON e.raw_id = a.raw_id WHERE a.sha256=? AND a.attachment_id < ?"
                           " ORDER BY a.attachment_id LIMIT 1", (a["sha256"], a["attachment_id"]))
            if dup:
                dup["received_at"] = _norm_ts(dup["received_at"])
        sheets = []
        if a["filename"].lower().endswith(".xlsx"):
            try:
                sheets = [_mark_sheet(n, r, a["parser"]) for n, r in read_xlsx(content)]
            except Exception as ex:  # a corrupt workbook is itself a finding; show it rather than fail the page
                sheets = [{"name": "unreadable", "error": f"{type(ex).__name__}: {ex}", "rows": []}]
        ref = f"raw_attachment:{a['attachment_id']}"
        loaded = {}
        if a["parser"] == "CM_DAILY_REPORT":
            loaded["cm_output_report"] = q(conn, "SELECT * FROM cm_output_report WHERE source_ref=? ORDER BY line, item_id", (ref,))
            loaded["cm_stock_report"] = q(conn, "SELECT * FROM cm_stock_report WHERE source_ref=? ORDER BY item_id", (ref,))
        elif a["parser"] == "PNC_OPEN_ORDERS":
            loaded["po_promise_history"] = q(conn, "SELECT * FROM po_promise_history WHERE raw_ref=? ORDER BY po_id, line_no", (ref,))
        elif a["parser"] == "KES_VMI":
            m = re.search(r"(\d{8})", a["filename"])
            day = f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}" if m else None
            rows = q(conn, "SELECT * FROM inventory_balance WHERE site_id='SUP-KES' AND as_of=?", (day,))
            loaded["inventory_balance"] = rows
            if not rows:
                latest = q1(conn, "SELECT MAX(as_of) AS d FROM inventory_balance WHERE site_id='SUP-KES'")
                a["superseded_by"] = latest["d"] if latest else None
        atts.append({**a, "dup_of": dup, "sheets": sheets, "loaded": loaded})
    parse = None
    loaded_text = []
    if e["mailbox"] == "po-confirm@" and not atts:
        domain = e["from_addr"].split("@")[-1].strip(">")
        supplier = SUPPLIER_BY_DOMAIN.get(domain)
        result = parse_confirmation(e["body_text"] or "", e["subject"], supplier)
        parse = {"supplier": supplier, "result": result, "segments": highlight(e["body_text"] or "")}
        loaded_text = q(conn, "SELECT h.*, pl.item_id, pl.need_date, pl.qty AS line_qty FROM po_promise_history h"
                              " JOIN po_line pl ON pl.po_id = h.po_id AND pl.line_no = h.line_no WHERE h.raw_ref=?",
                        (f"raw_email:{rid}",))
    tz_note = None
    if "formosa-ap" in e["from_addr"] or "pinecrest" in e["from_addr"]:
        t = dt.datetime.strptime(e["received_at"], "%Y-%m-%dT%H:%M:%SZ") + dt.timedelta(hours=8)
        tz_note = f"{t:%a %b %d %H:%M} Taipei (UTC+8)"
    return {"email": e, "steps": steps, "attachments": atts, "parse": parse, "loaded": loaded_text, "local_time": tz_note}


@get(r"^/api/integrations/attachment/(\d+)/download$")
def download(req):
    a = q1(req.conn, "SELECT filename, content_type, content FROM raw_attachment WHERE attachment_id=?", (int(req.params[0]),))
    if not a:
        raise HttpError(404, "no such attachment")
    return Raw(a["content"], a["content_type"] or "application/octet-stream", a["filename"])


# ------------------------------------------------------------------ playground

@post(r"^/api/integrations/parse-text$")
def parse_text(req):
    body = str(req.body.get("body") or "")
    if not body.strip():
        raise HttpError(400, "paste an email body to parse")
    if len(body) > 20000:
        raise HttpError(400, "email body too long for the playground (20,000 characters max)")
    subject = str(req.body.get("subject") or "")
    supplier = req.body.get("supplier") or None
    result = parse_confirmation(body, subject, supplier)
    known = None
    if not result.get("abstain"):
        known = q1(req.conn, "SELECT pl.po_id, pl.line_no, pl.item_id, pl.qty, pl.need_date, pl.promise_date, po.supplier_id"
                             " FROM po_line pl JOIN purchase_order po USING (po_id) WHERE pl.po_id=? AND pl.line_no=?",
                   (result["po"], result["line"]))
    return {"result": result, "segments": highlight(body), "po_line": known, "loaded": False,
            "note": "Preview only: nothing was written. In the pipeline this result would call apply_promise()."}


@post(r"^/api/integrations/parse-xlsx$")
def parse_xlsx(req):
    data, name = None, req.body.get("filename") or "upload.xlsx"
    if req.body.get("attachment_id"):
        a = q1(req.conn, "SELECT filename, content FROM raw_attachment WHERE attachment_id=?", (int(req.body["attachment_id"]),))
        if not a:
            raise HttpError(404, "no such attachment")
        data, name = a["content"], a["filename"]
    else:
        b64 = req.body.get("content_b64") or ""
        if "," in b64[:80]:
            b64 = b64.split(",", 1)[1]
        try:
            data = base64.b64decode(b64, validate=True)
        except Exception:
            raise HttpError(400, "content_b64 is not valid base64")
    if not data:
        raise HttpError(400, "empty file")
    if len(data) > 5 * 1024 * 1024:
        raise HttpError(400, "file larger than 5 MB")
    try:
        sheets = read_xlsx(data)
    except Exception as ex:
        raise HttpError(400, f"not a readable .xlsx ({type(ex).__name__}: {ex})")
    marked = [_mark_sheet(n, r, None) for n, r in sheets]
    guesses = [(s["feed"], len(s["mapped"])) for s in marked if s["feed"]]
    guess = max(guesses, key=lambda g: g[1])[0] if guesses else None
    return {"filename": name, "bytes": len(data), "sheets": marked, "guess": guess,
            "guess_label": FEED_LABEL.get(guess) if guess else None, "loaded": False,
            "note": "Preview only: nothing was loaded. The pipeline would classify by sender and subject first, "
                    "then apply this header match."}


# ------------------------------------------------------------------ options

@get(r"^/api/integrations/options$")
def options(req):
    systems = {s["system_id"]: s for s in q(req.conn, "SELECT * FROM source_system")}
    out = defaultdict(list)
    for o in q(req.conn, "SELECT * FROM integration_option"):
        out[o["system_id"]].append(o)
    order = {"CURRENT": 0, "RECOMMENDED": 1, "CONSIDERED": 2, "REJECTED": 3}
    res = []
    for sid, opts in out.items():
        opts.sort(key=lambda o: (order.get(o["status"], 9), o["latency_minutes"]))
        cur = next((o for o in opts if o["status"] == "CURRENT"), None)
        rec = [o for o in opts if o["status"] == "RECOMMENDED"]
        res.append({"system": systems.get(sid, {"system_id": sid, "name": sid}), "options": opts,
                    "current": cur["option"] if cur else None, "recommended": [o["option"] for o in rec]})
    res.sort(key=lambda r: r["system"]["system_id"])
    return {"sources": res}
