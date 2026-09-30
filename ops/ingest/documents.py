"""Documents: EDI 855 and portal acknowledgements, and the email pipeline.

Every email goes RECEIVED -> CLASSIFIED -> EXTRACTED -> PARSED -> VALIDATED ->
MAPPED -> LOADED -> RECONCILED, and each step is written to ingest_step. The
parsers read what suppliers actually send: bilingual headers in any row, merged
title rows, totals rows, text dates in several formats, English and Spanish prose.
They abstain rather than guess.
"""
import datetime as dt
import email
import hashlib
import json
import re
import time
from email import policy

from ..xlsx import read_xlsx
from .common import Run, apply_promise

ACK_STATUS = {"IA": "accepted", "IC": "accepted with changes", "IR": "rejected"}
CM_MODELS = {"FAP-LV1-DN": "LV1-DUNE", "FAP-LV1-SL": "LV1-SLATE", "FAP-LV1-FN": "LV1-FERN", "FAP-LV1-EM": "LV1-EMBER"}
SUPPLIER_BY_DOMAIN = {"summitdc.example": "SMT", "norteharness.example": "NRT", "baylineseals.example": "BAY",
                      "voltaicpower.example": "VPC", "pinecrest-elec.example": "PNC", "kestrelcell.example": "KES",
                      "formosa-ap.example": "FAP"}
SHIP_TRANSIT_DAYS = {"SMT": 4, "NRT": 2, "BAY": 1, "VPC": 18, "PNC": 4}   # ship date -> delivered at our dock
DAY_FIRST = {"SMT", "NRT"}                                                  # Mexican suppliers write dd/mm/yyyy


# ------------------------------------------------------------------ EDI 855 + portal

def run_confirmations(conn, now_s):
    run = Run(conn, "SUPPLIER_ACK", now_s)
    rows = conn.execute("SELECT raw_id, supplier_id, received_at, channel, payload FROM raw_supplier_confirmation"
                        " WHERE ingest_status='PENDING' ORDER BY received_at").fetchall()
    for r in rows:
        run.counts["in"] += 1
        ref = f"raw_supplier_confirmation:{r['raw_id']}"
        try:
            if r["channel"] == "EDI855":
                seg = {s.split("*")[0]: s.split("*") for s in r["payload"].strip("~").split("~")}
                po, line = seg["BAK"][3], int(seg["PO1"][1])
                ack = seg["ACK"]
                if ack[1] not in ACK_STATUS:
                    raise ValueError(f"unmapped ACK status {ack[1]} (e.g. BP = partial shipment, balance backordered)")
                if ack[1] == "IR":
                    raise ValueError("supplier rejected the line (ACK IR)")
                qty, promise = float(ack[2]), dt.datetime.strptime(ack[5], "%Y%m%d").date().isoformat()
                note = ACK_STATUS[ack[1]]
            else:
                p = json.loads(r["payload"])
                po, line, qty, promise, note = p["po"], int(p["line"]), float(p["qty"]), p["promise_date"], p.get("comment") or None
        except (KeyError, ValueError, IndexError) as e:
            conn.execute("UPDATE raw_supplier_confirmation SET ingest_status='QUARANTINED', ingest_note=? WHERE raw_id=?",
                         (str(e), r["raw_id"]))
            run.counts["quarantined"] += 1
            run.step(ref, 1, "QUARANTINED", "FAILED", r["received_at"], str(e))
            continue
        ok, msg = apply_promise(conn, po, line, promise, qty, r["channel"], ref, r["received_at"], note)
        conn.execute("UPDATE raw_supplier_confirmation SET ingest_status=?, ingest_note=? WHERE raw_id=?",
                     ("OK" if ok else "QUARANTINED", msg, r["raw_id"]))
        run.counts["ok" if ok else "quarantined"] += 1
        run.step(ref, 1, "PARSED", "OK", r["received_at"], f"{r['channel']} PO {po}-{line} -> {promise}")
        run.step(ref, 2, "LOADED", "OK" if ok else "FAILED", r["received_at"], msg)
    run.finish(now_s)
    return run.counts


# ------------------------------------------------------------------ email pipeline

TEMPLATES = [
    ("CM_DAILY_REPORT", "formosa-ap.example", re.compile(r"生產日報|Daily Production Report", re.I)),
    ("PNC_OPEN_ORDERS", "pinecrest-elec.example", re.compile(r"open order report", re.I)),
    ("KES_VMI", "kestrelcell.example", re.compile(r"VMI", re.I)),
    ("PO_CONFIRMATION_TEXT", None, re.compile(r"\b4[57]\d{5}\b")),
]


def classify(from_addr, subject, body):
    domain = from_addr.split("@")[-1].strip(">")
    if re.search(r"automatic reply|out of office", subject, re.I):
        return None
    for name, dom, rx in TEMPLATES:
        if dom and dom != domain:
            continue
        if rx.search(subject) or (name == "PO_CONFIRMATION_TEXT" and rx.search(body or "")):
            return name
    return None


class EmailPipeline:
    def __init__(self, conn, now_s):
        self.conn = conn
        self.run = Run(conn, "EMAIL", now_s)
        self.seen_sha = {r["sha256"] for r in conn.execute("SELECT sha256 FROM raw_attachment")}

    def step(self, ref, seq, step, status, at, detail, t0=None):
        ms = round((time.perf_counter() - t0) * 1000, 2) if t0 else None
        self.run.step(ref, seq, step, status, at, detail, ms)

    def process(self, e):
        conn, ref, at = self.conn, f"raw_email:{e['raw_id']}", e["received_at"]
        self.run.counts["in"] += 1
        t0 = time.perf_counter()
        self.step(ref, 1, "RECEIVED", "OK", at, f"{e['mailbox']} from {e['from_addr']}: {e['subject'][:80]}", t0)
        kind = classify(e["from_addr"], e["subject"], e["body_text"])
        if kind is None:
            self.step(ref, 2, "CLASSIFIED", "SKIPPED", at, "no template matched (auto-reply, newsletter or notice)")
            self._done(e, "IGNORED", "not a data feed", None)
            return
        self.step(ref, 2, "CLASSIFIED", "OK", at, kind)
        msg = email.message_from_string(e["mime"], policy=policy.default)
        atts = []
        for part in msg.iter_attachments():
            data = part.get_content()
            sha = hashlib.sha256(data).hexdigest()
            dup = sha in self.seen_sha
            cur = conn.execute("INSERT INTO raw_attachment(raw_id, filename, content_type, size_bytes, sha256, content,"
                               " parser, parse_status, parse_note) VALUES (?,?,?,?,?,?,?,?,?)",
                               (e["raw_id"], part.get_filename(), part.get_content_type(), len(data), sha, data,
                                kind, "SKIPPED" if dup else "PENDING", "duplicate of an earlier attachment" if dup else None))
            self.seen_sha.add(sha)
            atts.append((cur.lastrowid, part.get_filename(), data, dup))
        if atts:
            self.step(ref, 3, "EXTRACTED", "OK", at, ", ".join(f"{a[1]} ({len(a[2]):,} B){' DUP' if a[3] else ''}" for a in atts))
        if kind != "PO_CONFIRMATION_TEXT" and atts and all(a[3] for a in atts):
            self.step(ref, 4, "VALIDATED", "SKIPPED", at, "attachment already ingested (same SHA-256): resend ignored")
            self._done(e, "IGNORED", "duplicate resend", kind)
            return
        handler = {"CM_DAILY_REPORT": self.cm_daily, "PNC_OPEN_ORDERS": self.pnc_orders, "KES_VMI": self.kes_vmi,
                   "PO_CONFIRMATION_TEXT": self.text_confirmation}[kind]
        try:
            status, note = handler(e, [a for a in atts if not a[3]])
        except Exception as ex:          # a parser failure quarantines the message; it never half-loads
            status, note = "QUARANTINED", f"{type(ex).__name__}: {ex}"
            self.step(ref, 9, "QUARANTINED", "FAILED", at, note)
        self._done(e, status, note, kind)

    def _done(self, e, status, note, kind):
        self.conn.execute("UPDATE raw_email SET ingest_status=?, ingest_note=?, classified_as=? WHERE raw_id=?",
                          (status, note, kind, e["raw_id"]))
        key = {"OK": "ok", "WARN": "warn", "QUARANTINED": "quarantined", "IGNORED": "ok"}[status]
        self.run.counts[key] += 1

    # -------------------------------------------------------------- Excel helpers
    @staticmethod
    def find_header(rows, synonyms, need=3):
        """Locate the header row by matching bilingual synonyms; returns (row_index, {field: col})."""
        for i, row in enumerate(rows[:15]):
            cols = {}
            for j, cell in enumerate(row):
                if not isinstance(cell, str):
                    continue
                low = cell.lower()
                for field, keys in synonyms.items():
                    if field not in cols and any(k in low for k in keys):
                        cols[field] = j
                        break
            if len(cols) >= need:
                return i, cols
        raise ValueError("header row not found")

    @staticmethod
    def parse_date(text, day_first=False):
        if text is None:
            return None
        s = str(text).strip()
        for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%d-%b-%Y"):
            try:
                return dt.datetime.strptime(s, fmt).date()
            except ValueError:
                pass
        m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})$", s)
        if m:
            a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            d, mo = (a, b) if day_first else (b, a)
            return dt.date(y, mo, d)
        raise ValueError(f"unparseable date {s!r}")

    # -------------------------------------------------------------- CM daily report
    def cm_daily(self, e, atts):
        conn, ref, at = self.conn, f"raw_email:{e['raw_id']}", e["received_at"]
        att_id, fname, data, _ = atts[0]
        aref = f"raw_attachment:{att_id}"
        sheets = dict(read_xlsx(data))
        out_name = next(n for n in sheets if "output" in n.lower() or "生產日報" in n)
        rows = sheets[out_name]
        hi, cols = self.find_header(rows, {"line": ["線別", "line"], "model": ["機種", "model"],
                                           "pn": ["料號", "p/n"], "plan": ["計畫", "plan"], "actual": ["實際", "actual"],
                                           "wip": ["在製", "wip"], "scrap_ntd": ["金額", "ntd"],
                                           "scrap": ["報廢 scrap"]})
        warnings = []
        header_cells = [c for c in rows[hi] if c]
        if len(header_cells) > len(cols):
            extra = [c for j, c in enumerate(rows[hi]) if c and j not in cols.values()]
            warnings.append(f"unexpected column(s) ignored: {', '.join(extra)}")
        # report date: the cell says one thing, the subject another; the subject wins when they disagree
        cell_date = None
        for row in rows[:hi]:
            for cell in row:
                m = re.search(r"(\d{4}/\d{2}/\d{2})", str(cell or ""))
                if m:
                    cell_date = self.parse_date(m.group(1))
        subj = re.search(r"(\d{4}/\d{2}/\d{2})", e["subject"])
        subj_date = self.parse_date(subj.group(1)) if subj else None
        report_date = subj_date or cell_date
        if cell_date and subj_date and cell_date != subj_date:
            warnings.append(f"report-date cell says {cell_date}, subject says {subj_date}: used the subject")
        self.step(ref, 4, "PARSED", "OK", at, f"{fname}: sheet '{out_name}', header at row {hi + 1}, "
                                               f"{len(rows) - hi - 1} data rows")
        data_rows, total = [], None
        for row in rows[hi + 1:]:
            first = row[cols["line"]] if cols["line"] < len(row) else None
            if first is None:
                continue
            if isinstance(first, str) and ("合計" in first or "total" in first.lower()):
                total = row
                break
            data_rows.append(row)
        loaded = []
        fx = conn.execute("SELECT usd_per_unit FROM fx_rate WHERE currency='TWD' AND rate_date<=? ORDER BY rate_date DESC LIMIT 1",
                          (report_date.isoformat(),)).fetchone()
        for row in data_rows:
            sku = CM_MODELS.get(row[cols["pn"]])
            if sku is None:
                warnings.append(f"unknown CM part number {row[cols['pn']]!r}")
                continue
            ntd = row[cols["scrap_ntd"]] if "scrap_ntd" in cols else 0
            loaded.append((report_date.isoformat(), row[cols["line"]], sku, row[cols["plan"]], row[cols["actual"]],
                           row[cols["wip"]], row[cols["scrap"]] if "scrap" in cols else None, ntd,
                           round(ntd * fx["usd_per_unit"], 2) if (fx and ntd) else 0.0, aref))
        if total:
            s_actual = sum(r[4] for r in loaded)
            if total[cols["actual"]] != s_actual:
                warnings.append(f"totals row says {total[cols['actual']]} built, rows sum to {s_actual}")
        self.step(ref, 5, "VALIDATED", "WARN" if warnings else "OK", at, "; ".join(warnings) or
                  "part numbers known, totals tie out, date consistent")
        self.step(ref, 6, "MAPPED", "OK", at, f"{len(loaded)} output rows; CM P/N -> OEM SKU; NTD -> USD at "
                                              f"{fx['usd_per_unit']:.5f}" if fx else "no FX rate")
        conn.executemany("INSERT OR REPLACE INTO cm_output_report VALUES (?,?,?,?,?,?,?,?,?,?)", loaded)
        stock = 0
        stock_name = next((n for n in sheets if "consigned" in n.lower() or "寄售" in n), None)
        if stock_name:
            srows = sheets[stock_name]
            shi, scols = self.find_header(srows, {"pn": ["料號", "part"], "good": ["良品", "good"], "qc": ["待驗", "qc"]})
            for row in srows[shi + 1:]:
                if not row or row[scols["pn"]] is None:
                    continue
                conn.execute("INSERT OR REPLACE INTO cm_stock_report VALUES (?,?,?,?,?)",
                             (report_date.isoformat(), row[scols["pn"]], int(row[scols["good"]] or 0),
                              int(row[scols["qc"]] or 0), aref))
                stock += 1
        self.step(ref, 7, "LOADED", "OK", at, f"cm_output_report {len(loaded)} rows, cm_stock_report {stock} rows")
        # reconcile the Excel against what the MES event stream says was built that day
        mes = {r["line"]: r["n"] for r in conn.execute(
            "SELECT u.line, COUNT(*) n FROM station_event se JOIN unit u ON u.serial = se.serial "
            "JOIN station s ON s.station_id = se.station_id WHERE s.code='S80' AND se.result='PASS' "
            "AND substr(datetime(se.event_ts, '+8 hours'), 1, 10) = ? GROUP BY u.line", (report_date.isoformat(),))}
        rep = {}
        for r in loaded:
            rep[r[1]] = rep.get(r[1], 0) + r[4]
        diffs = [f"{ln}: Excel {rep.get(ln, 0)} vs MES {mes.get(ln, 0)}" for ln in sorted(set(rep) | set(mes))
                 if rep.get(ln, 0) != mes.get(ln, 0)]
        self.step(ref, 8, "RECONCILED", "WARN" if diffs else "OK", at,
                  "; ".join(diffs) if diffs else f"Excel output matches MES S80 passes ({sum(rep.values())} units)")
        conn.execute("UPDATE raw_attachment SET parse_status=?, rows_parsed=?, rows_loaded=?, parse_note=? WHERE attachment_id=?",
                     ("WARN" if warnings else "OK", len(data_rows), len(loaded) + stock, "; ".join(warnings) or None, att_id))
        return ("WARN" if warnings else "OK"), "; ".join(warnings) or f"{len(loaded)} output rows loaded"

    # -------------------------------------------------------------- Pinecrest open orders
    def pnc_orders(self, e, atts):
        conn, ref, at = self.conn, f"raw_email:{e['raw_id']}", e["received_at"]
        att_id, fname, data, _ = atts[0]
        aref = f"raw_attachment:{att_id}"
        (sheet, rows), = read_xlsx(data)
        hi, cols = self.find_header(rows, {"po": ["採購單號", "po no"], "line": ["項次", "line"], "part": ["料號", "part"],
                                           "qty": ["訂購量", "order qty"], "open": ["未交量", "open qty"],
                                           "etd": ["出貨日", "etd"], "eta": ["到貨日", "eta"], "remark": ["備註", "remark"]})
        self.step(ref, 4, "PARSED", "OK", at, f"{fname}: header at row {hi + 1}, {len(rows) - hi - 1} open lines")
        warnings, changed, same = [], 0, 0
        for row in rows[hi + 1:]:
            if not row or row[cols["po"]] is None:
                continue
            po, line = str(row[cols["po"]]), int(row[cols["line"]])
            eta = self.parse_date(row[cols["eta"]]) if row[cols["eta"]] else None
            if eta is None:
                etd = self.parse_date(row[cols["etd"]])
                eta = etd + dt.timedelta(days=SHIP_TRANSIT_DAYS["PNC"])
                warnings.append(f"{po}-{line}: ETA blank, derived ETD {etd} + {SHIP_TRANSIT_DAYS['PNC']}d air transit")
            ok, msg = apply_promise(conn, po, line, eta.isoformat(), float(row[cols["open"]]), "EXCEL", aref, at,
                                    row[cols["remark"]] or None)
            if not ok:
                warnings.append(msg)
            elif msg == "no change":
                same += 1
            else:
                changed += 1
        self.step(ref, 5, "VALIDATED", "WARN" if warnings else "OK", at, "; ".join(warnings) or "all PO lines known")
        self.step(ref, 7, "LOADED", "OK", at, f"{changed} promise changes recorded, {same} unchanged")
        conn.execute("UPDATE raw_attachment SET parse_status=?, rows_parsed=?, rows_loaded=?, parse_note=? WHERE attachment_id=?",
                     ("WARN" if warnings else "OK", changed + same, changed, "; ".join(warnings) or None, att_id))
        return ("WARN" if warnings else "OK"), f"{changed} promise changes"

    # -------------------------------------------------------------- Kestrel VMI
    def kes_vmi(self, e, atts):
        conn, ref, at = self.conn, f"raw_email:{e['raw_id']}", e["received_at"]
        att_id, fname, data, _ = atts[0]
        (sheet, rows), = read_xlsx(data)
        hi, cols = self.find_header(rows, {"part": ["part"], "fg": ["fg on hand"], "wip": ["in production"],
                                           "reserved": ["reserved"], "transit": ["in transit"]})
        m = re.search(r"As of (\d{2}-\w{3}-\d{4})", " ".join(str(c) for r in rows[:hi] for c in r if c))
        as_of = self.parse_date(m.group(1)).isoformat()
        loaded = 0
        for row in rows[hi + 1:]:
            if not row or row[cols["part"]] is None:
                continue
            conn.execute("DELETE FROM inventory_balance WHERE site_id='SUP-KES' AND item_id=?", (row[cols["part"]],))
            conn.execute("INSERT INTO inventory_balance(site_id, item_id, owner, stock_status, qty, as_of, source)"
                         " VALUES ('SUP-KES', ?, 'SUPPLIER', 'AVAILABLE', ?, ?, 'SUPPLIER_REPORT')", (row[cols["part"]], row[cols["fg"]], as_of))
            conn.execute("INSERT INTO inventory_balance(site_id, item_id, owner, stock_status, qty, as_of, source)"
                         " VALUES ('SUP-KES', ?, 'SUPPLIER', 'IN_PRODUCTION', ?, ?, 'SUPPLIER_REPORT')", (row[cols["part"]], row[cols["wip"]], as_of))
            loaded += 2
        self.step(ref, 4, "PARSED", "OK", at, f"{fname}: as of {as_of}")
        self.step(ref, 7, "LOADED", "OK", at, f"{loaded} supplier-held stock rows (replacing the prior week)")
        conn.execute("UPDATE raw_attachment SET parse_status='OK', rows_parsed=?, rows_loaded=? WHERE attachment_id=?",
                     (loaded // 2, loaded, att_id))
        return "OK", f"supplier stock as of {as_of}"

    # -------------------------------------------------------------- free-text confirmations
    def text_confirmation(self, e, atts):
        conn, ref, at = self.conn, f"raw_email:{e['raw_id']}", e["received_at"]
        domain = e["from_addr"].split("@")[-1].strip(">")
        supplier = SUPPLIER_BY_DOMAIN.get(domain)
        parsed = parse_confirmation(e["body_text"] or "", e["subject"], supplier)
        if parsed.get("abstain"):
            self.step(ref, 4, "PARSED", "WARN", at, "abstained: " + parsed["reason"])
            self.step(ref, 9, "QUARANTINED", "WARN", at, "routed to buyer queue; no date written")
            return "QUARANTINED", "parser abstained: " + parsed["reason"]
        self.step(ref, 4, "PARSED", "OK", at, json.dumps(parsed))
        ok, msg = apply_promise(conn, parsed["po"], parsed["line"], parsed["promise"], parsed.get("qty"), "EMAIL", ref, at,
                                parsed.get("basis"))
        self.step(ref, 5, "VALIDATED", "OK" if ok else "FAILED", at, "PO line exists" if ok else msg)
        self.step(ref, 7, "LOADED", "OK" if ok else "FAILED", at, msg)
        return ("OK" if ok else "QUARANTINED"), msg


MONTHS = {m: i + 1 for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                          "september", "october", "november", "december"])}
MONTHS_ES = {m: i + 1 for i, m in enumerate(["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                                             "septiembre", "octubre", "noviembre", "diciembre"])}
VAGUE = re.compile(r"will revert|revisando|check .* with production|to be confirmed|tbc|a más tardar", re.I)


def parse_confirmation(body, subject, supplier):
    """Extract (po, line, promise date, qty) from a supplier email, or abstain with a reason."""
    own = "\n".join(l for l in body.splitlines() if not l.startswith(">"))
    quoted = "\n".join(l[1:] for l in body.splitlines() if l.startswith(">"))
    text = own + "\n" + subject
    m = re.search(r"\b(4[57]\d{5})(?:-(\d{1,2}))?\b", text)
    if not m:
        m = re.search(r"\b(4[57]\d{5})(?:-(\d{1,2}))?\b", quoted)
    if not m:
        return {"abstain": True, "reason": "no PO number"}
    po = m.group(1)
    line = m.group(2)
    if not line:
        lm = re.search(r"(?:line|partida|item|l[ií]nea)\s*#?\s*(\d{1,2})\b", own + "\n" + quoted + "\n" + subject, re.I)
        if not lm:
            return {"abstain": True, "reason": "no line number"}
        line = lm.group(1)
    qm = re.search(r"([\d][\d,]*)\s*(?:pcs|piezas|units|unidades)\b", own, re.I) or \
        re.search(r"\bqty\.?\s*([\d][\d,]*)", own, re.I) or re.search(r"\(([\d,]+)\)", own)
    qty = float(qm.group(1).replace(",", "")) if qm else None
    if VAGUE.search(own) and not re.search(r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}", own):
        return {"abstain": True, "reason": "no firm date (supplier will revert)"}
    date, basis = None, None
    # find the first date in the supplier's own words, then read the few words before it to learn what it means
    cands = []
    for m in re.finditer(r"\d{4}-\d{2}-\d{2}", own):
        cands.append((m.start(), "iso", m.group(0)))
    for m in re.finditer(r"\b\d{1,2}/\d{1,2}/\d{4}\b", own):
        cands.append((m.start(), "slash", m.group(0)))
    for m in re.finditer(r"\b\d{1,2}-[A-Z][a-z]{2}-\d{4}\b", own):
        cands.append((m.start(), "dmy", m.group(0)))
    for m in re.finditer(r"(january|february|march|april|may|june|july|august|september|october|november|december)"
                         r"\s+(\d{1,2}),\s*(\d{4})", own, re.I):
        cands.append((m.start(), "month", m.group(0)))
    if cands:
        pos, kind, text_ = min(cands)
        before = own[max(0, pos - 30):pos].lower()
        ship_words = re.search(r"ship date|etd|fecha de embarque|embarque", before)
        if kind == "iso":
            date = dt.date.fromisoformat(text_)
        elif kind == "slash":
            a, b, y = (int(x) for x in text_.split("/"))
            day_first = supplier in DAY_FIRST and not ship_words        # Mexican suppliers write dd/mm in Spanish
            d, mo = (a, b) if day_first else (b, a)
            try:
                date = dt.date(y, mo, d)
            except ValueError:
                return {"abstain": True, "reason": f"ambiguous date {text_}"}
        elif kind == "dmy":
            date = dt.datetime.strptime(text_, "%d-%b-%Y").date()
        else:
            mm = re.match(r"(\w+)\s+(\d{1,2}),\s*(\d{4})", text_)
            date = dt.date(int(mm.group(3)), MONTHS[mm.group(1).lower()], int(mm.group(2)))
        basis = "ship date" if ship_words else "delivery"
    if not date and re.search(r"\byes\b|confirmed|confirmado", own, re.I):
        nm = re.search(r"need date\s+(\d{4}-\d{2}-\d{2})", quoted, re.I)
        if nm:
            date, basis = dt.date.fromisoformat(nm.group(1)), "accepted our need date"
    if not date:
        return {"abstain": True, "reason": "no recognizable date"}
    if basis in ("ship date", "etd"):
        transit = SHIP_TRANSIT_DAYS.get(supplier)
        if transit is None:
            return {"abstain": True, "reason": "ship date given but transit time unknown for this supplier"}
        date = date + dt.timedelta(days=transit)
        basis = f"ship date + {transit}d transit"
    return {"po": po, "line": int(line), "promise": date.isoformat(), "qty": qty, "basis": basis}


def run_email(conn, now_s):
    pipe = EmailPipeline(conn, now_s)
    for e in conn.execute("SELECT * FROM raw_email WHERE ingest_status='PENDING' ORDER BY received_at, raw_id").fetchall():
        pipe.process(e)
    pipe.run.finish(now_s)
    return pipe.run.counts
