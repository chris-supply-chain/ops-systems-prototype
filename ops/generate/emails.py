"""The email side of the world: a Taiwanese CM's bilingual daily Excel report,
Pinecrest's weekly open-order workbook, Kestrel's VMI workbook, free-text PO
confirmations in English and Spanish, EDI 855s and portal confirmations, and
the noise any shared inbox collects. Everything is a real RFC 822 message with
real .xlsx attachments.
"""
import datetime as dt
import hashlib
import json
from email.message import EmailMessage
from email.utils import format_datetime

from ..xlsx import write_xlsx
from .master import VEHICLE_SKUS
from .plan import split_det
from .util import PT, TPE, UTC, add_days, at

XLSX_TYPE = ("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet")
CM_PN = {"LV1-DUNE": "FAP-LV1-DN", "LV1-SLATE": "FAP-LV1-SL", "LV1-FERN": "FAP-LV1-FN", "LV1-EMBER": "FAP-LV1-EM"}
CM_MODEL = {"LV1-DUNE": "LV-1 沙丘 Dune", "LV1-SLATE": "LV-1 岩灰 Slate", "LV1-FERN": "LV-1 蕨綠 Fern",
            "LV1-EMBER": "LV-1 餘燼 Ember"}
CM_DEFECT = {"DU-CONN": ("A201", "驅動單元接頭未插到位"), "HRN-PINCH": ("A401", "線束夾傷"),
             "HMI-PIXEL": ("A402", "螢幕壞點"), "BRK-BLEED": ("A501", "煞車排氣不良"),
             "EOL-MOTOR": ("T601", "馬達異音"), "EOL-FW": ("T602", "韌體燒錄失敗"),
             "EOL-BRK": ("T603", "煞車扭力不足"), "WTR-LEAK": ("T701", "淋水測試漏水"),
             "COS-SCR": ("Q801", "外觀刮傷")}
TWD_PER_USD = 32.4
CONTACTS = {
    "SMT": ("Ana Villarreal", "pedidos@summitdc.example", "Summit Die Casting", "MX"),
    "NRT": ("Luis Ortega", "ventas@norteharness.example", "Norte Harness", "MX"),
    "BAY": ("Dana Kim", "orders@baylineseals.example", "Bayline Seals", "US"),
    "VPC": ("Kevin Lin", "sales@voltaicpower.example", "Voltaic Power Co.", "TW"),
}
SHIP_TRANSIT_DAYS = {"SMT": 4, "NRT": 2, "BAY": 1, "VPC": 18}


def _mail(w, frm, to, subject, when, body, attachments=()):
    m = EmailMessage()
    domain = frm.split("@")[1]
    m["From"] = frm
    m["To"] = to
    m["Subject"] = subject
    m["Date"] = format_datetime(when)
    n = w.nid("msg")
    m["Message-ID"] = f"<{n:06d}.{hashlib.sha1(subject.encode()).hexdigest()[:10]}@{domain}>"
    m.set_content(body)
    for fname, data in attachments:
        m.add_attachment(data, maintype=XLSX_TYPE[0], subtype=XLSX_TYPE[1], filename=fname)
    if attachments:
        m.set_boundary(f"=_part_{n:06d}")        # the stdlib default is random; same seed must give the same bytes
    rec = {"n": n, "received_at": when + dt.timedelta(seconds=w.rng.randint(3, 90)), "from": frm, "to": to,
           "subject": subject, "message_id": m["Message-ID"], "body": body, "mime": m.as_string(),
           "mailbox": to.split("@")[0] + "@"}
    w.emails.append(rec)
    return rec


def build(w):
    w.emails = []
    w.golden_promises = []
    cm_daily_reports(w)
    pnc_open_order_reports(w)
    kes_vmi_reports(w)
    text_confirmations(w)
    edi_and_portal(w)
    noise(w)
    w.emails.sort(key=lambda e: e["received_at"])


# ------------------------------------------------------------------ CM daily report (bilingual Excel)

def _consigned_on_hand(w, item, t):
    arrived = sum(d["qty"] for d in w.deliveries.get(item, []) if d["arrival"] <= t)
    used = len([e for e in w.consume.get(item, []) if e["t"] <= t])
    return arrived - used


def cm_daily_reports(w):
    rng = w.rng
    days = [d for d in w.cm_days if add_days(w.as_of, -30) <= d <= w.as_of]
    prev_date = None
    quirks = {"extra_col": None, "stale_date": None, "resend": None}
    if len(days) > 12:
        quirks["extra_col"] = days[-4]
        quirks["stale_date"] = days[-7]
        quirks["resend"] = days[-10]
    for d in days:
        sent = at(d, 18, 40, TPE).astimezone(UTC)
        if sent > w.now:
            continue
        report_t = sent
        rows_out = []
        header = ["線別 Line", "機種 Model", "料號 CM P/N", "計畫 Plan", "實際產出 Actual", "在製品 WIP", "報廢 Scrap",
                  "報廢金額 Scrap (NTD)"]
        if d == quirks["extra_col"]:
            header.insert(1, "班別 Shift")
        tot = [0, 0, 0, 0, 0]
        for line in ("L1", "L2"):
            plan = w.cm_plan(line, d)
            if not plan:
                continue
            split = split_det(plan, {"LV1-DUNE": 0.30, "LV1-SLATE": 0.34, "LV1-FERN": 0.21, "LV1-EMBER": 0.15})
            for sku in VEHICLE_SKUS:
                vs = [v for v in w.vehicles if v["line"] == line and v["sku"] == sku]
                actual = sum(1 for v in vs if v["built_at"] and v["built_at"].astimezone(TPE).date() == d)
                wip = sum(1 for v in vs if v["events"] and v["events"][0]["t"] <= report_t and not v["scrapped"]
                          and (v["built_at"] is None or v["built_at"] > report_t))
                scrap = sum(1 for v in vs if v["scrapped"] and v["events"][-1]["t"].astimezone(TPE).date() == d)
                ntd = round(scrap * 1552 * TWD_PER_USD)
                row = [line, CM_MODEL[sku], CM_PN[sku], split.get(sku, 0), actual, wip, scrap, ntd]
                if d == quirks["extra_col"]:
                    row.insert(1, "日班 Day")
                rows_out.append(row)
                for i, val in enumerate((split.get(sku, 0), actual, wip, scrap, ntd)):
                    tot[i] += val
        total_row = ["合計 Total", None, None] + tot
        if d == quirks["extra_col"]:
            total_row.insert(1, None)
        date_cell = (prev_date or d) if d == quirks["stale_date"] else d
        out_sheet = {"name": "生產日報 Output",
                     "rows": [["Formosa Assembly Partners 台中廠 · LV-1 生產日報 Daily Production Report"],
                              [f"報表日期 Report date: {date_cell:%Y/%m/%d}"], [], header] + rows_out + [total_row],
                     "bold_rows": {0, 3, 4 + len(rows_out)}, "merges": ["A1:H1"],
                     "widths": [12, 18, 14, 10, 14, 12, 10, 18]}
        defects = {}
        for v in w.vehicles:
            for e in v["events"]:
                if e["result"] == "FAIL" and e["t"].astimezone(TPE).date() == d:
                    key = (v["line"], e["code"], e["defect"])
                    defects[key] = defects.get(key, 0) + 1
        def_rows = [[line, code, CM_DEFECT[dc][0], CM_DEFECT[dc][1], q] for (line, code, dc), q in sorted(defects.items())]
        def_sheet = {"name": "不良統計 Defects",
                     "rows": [["不良統計 Defect summary"], [], ["線別 Line", "站別 Station", "不良代碼 Code",
                                                              "不良描述 Description", "數量 Qty"]] + def_rows,
                     "bold_rows": {0, 2}, "widths": [10, 10, 12, 26, 8]}
        stock_rows = []
        for item, rev in (("DU-B", "B"), ("DU-C", "C"), ("PU-1", "A"), ("HMI-1", "A")):
            oh = _consigned_on_hand(w, item, report_t)
            if item == "DU-C" and rng.random() < 0.25:
                oh += rng.choice([-2, -1, 1])                     # a miscount on the CM floor
            note = "ECO-0042 停用 frozen (use-up / 待處理)" if item == "DU-B" else ""
            stock_rows.append([item, rev, max(0, oh), 0, note])
        stock_sheet = {"name": "寄售庫存 Consigned",
                       "rows": [["OEM 寄售料庫存 Consigned stock (OEM-owned)"], [],
                                ["料號 Part No.", "版次 Rev", "良品 Good", "待驗 In QC", "備註 Remark"]] + stock_rows,
                       "bold_rows": {0, 2}, "widths": [12, 8, 10, 10, 34]}
        data = write_xlsx([out_sheet, def_sheet, stock_sheet])
        subject = f"【日報】LV-1 生產日報 Daily Production Report {d:%Y/%m/%d}"
        body = (f"Dear OEM team,\n\n附件為 {d:%Y/%m/%d} LV-1 生產日報，請查收。\n"
                f"Attached is the LV-1 daily production report for {d:%Y/%m/%d}.\n\nBest regards,\nFAP 生管 PMC\n")
        fname = f"FAP_LV1_DailyReport_{d:%Y%m%d}.xlsx"
        rec = _mail(w, "pmc-report@formosa-ap.example", "cm-reports@oem.example", subject, sent, body, [(fname, data)])
        rec["truth"] = {"kind": "CM_DAILY", "date": d.isoformat()}
        if d == quirks["resend"]:
            dup = _mail(w, "pmc-report@formosa-ap.example", "cm-reports@oem.example", "RE: " + subject,
                        sent + dt.timedelta(minutes=41), "Resending, sorry for the duplicate.\n\n" + body, [(fname, data)])
            dup["truth"] = {"kind": "CM_DAILY_DUP", "date": d.isoformat()}
        prev_date = d


# ------------------------------------------------------------------ Pinecrest weekly open-order workbook

def pnc_open_order_reports(w):
    lines = sorted([l for l in w.po_lines if l["supplier"] == "PNC"], key=lambda l: (l["po_id"], l["line_no"]))
    mondays = []
    d = add_days(w.as_of, -((w.as_of.weekday()) % 7))
    for k in range(10):
        mondays.append(add_days(d, -7 * k))
    mondays = sorted(m for m in mondays if at(m, 9, 20, TPE).astimezone(UTC) <= w.now)
    # re-time Pinecrest acknowledgements to the report they first appear in
    for ln in lines:
        new_hist = []
        for h in ln.get("history", []):
            rep = next((m for m in mondays if at(m, 9, 20, TPE).astimezone(UTC) >= h["t"]), None)
            if rep is None:
                continue
            h = dict(h, t=at(rep, 9, 20, TPE).astimezone(UTC), channel="EXCEL")
            if new_hist and new_hist[-1]["t"] == h["t"]:
                new_hist[-1] = h
            else:
                new_hist.append(h)
        ln["history"] = new_hist
        ln["confirm"] = new_hist[0]["t"] if new_hist else None
    blank_eta_done = False
    for m in mondays:
        rt = at(m, 9, 20, TPE).astimezone(UTC)
        rows = []
        golden = []
        for ln in lines:
            if ln["po_created"] > rt:
                continue
            if ln["received"] and ln["arrival"] and ln["arrival"] <= rt:
                continue
            current = [h for h in ln["history"] if h["t"] <= rt]
            if not current:
                continue
            h = current[-1]
            eta = h["promise"]
            etd = add_days(eta, -4)
            remark = h.get("note", "")
            eta_cell = f"{eta:%Y/%m/%d}"
            if not blank_eta_done and m == mondays[-2] and not ln["received"]:
                eta_cell, remark, blank_eta_done = None, remark or "ETA TBC", True
            rows.append([ln["po_id"], ln["line_no"], ln["item"], "BMS board rev " + ln["item"][-1],
                         ln["qty"], ln["qty"], f"{etd:%Y/%m/%d}", eta_cell, remark])
            changed = len(current) == 1 or current[-1]["promise"] != current[-2]["promise"]
            if current[-1]["t"] == rt:
                golden.append({"po": ln["po_id"], "line": ln["line_no"], "promise": eta.isoformat(), "qty": ln["qty"]})
        sheet = {"name": "Open Orders 未交訂單",
                 "rows": [["Pinecrest Electronics 松峰電子 · Open Order Report 未交訂單明細"],
                          [f"Customer: OEM · Report date: {m:%Y-%m-%d}"], [],
                          ["採購單號 PO No.", "項次 Line", "料號 Part No.", "品名 Description", "訂購量 Order Qty",
                           "未交量 Open Qty", "預計出貨日 ETD", "預計到貨日 ETA", "備註 Remarks"]] + rows,
                 "bold_rows": {0, 3}, "merges": ["A1:I1"], "widths": [14, 8, 10, 18, 12, 12, 14, 14, 40]}
        data = write_xlsx([sheet])
        wk = f"{m.isocalendar()[1]:02d}"
        rec = _mail(w, "planning@pinecrest-elec.example", "po-confirm@oem.example",
                    f"Pinecrest open order report - OEM - {m:%Y-%m-%d}", rt,
                    "Hi purchasing team,\n\nPlease find this week's open order status attached.\n"
                    "Note: AFE IC supply remains on allocation; ETAs reflect our current plan.\n\nRegards,\n"
                    "Pinecrest Electronics planning", [(f"PNC_OpenOrders_OEM_2026W{wk}.xlsx", data)])
        rec["truth"] = {"kind": "PNC_OPEN_ORDERS", "promises": golden}


# ------------------------------------------------------------------ Kestrel weekly VMI workbook

def kes_vmi_reports(w):
    rng = w.rng
    d = add_days(w.as_of, -((w.as_of.weekday()) % 7))
    for k in range(5, -1, -1):
        m = add_days(d, -7 * k)
        rt = at(m, 9, 0, "Asia/Seoul").astimezone(UTC)
        if rt > w.now:
            continue
        in_transit = sum(l["qty"] for l in w.po_lines if l["item"] == "CEL-21700" and not l["received"]
                         and l["need"] <= add_days(m, 18))
        fg = int(round(rng.uniform(38000, 62000), -3))
        wip = int(round(rng.uniform(55000, 70000), -3))
        caps = [90000, 90000, 85000, 95000]
        rows = [["CEL-21700", "21700 cell 5.0Ah (OEM spec)", fg, wip, 30000, in_transit] + caps]
        sheet = {"name": "VMI",
                 "rows": [["Kestrel Cell Co. · VMI & capacity report for the OEM"], [f"As of {m:%d-%b-%Y}"], [],
                          ["Part", "Description", "FG on hand (Cheongju)", "In production", "Reserved for the OEM (VMI)",
                           "In transit to the OEM", "Wk+1 capacity", "Wk+2 capacity", "Wk+3 capacity", "Wk+4 capacity"]] + rows,
                 "bold_rows": {0, 3}, "widths": [12, 28, 20, 14, 22, 18, 14, 14, 14, 14]}
        rec = _mail(w, "scm@kestrelcell.example", "supplier-reports@oem.example",
                    f"[Kestrel] VMI stock & capacity - OEM - {m:%Y-%m-%d}", rt,
                    "Dear OEM,\n\nAttached is the weekly VMI and capacity report.\n\nKestrel SCM",
                    [(f"Kestrel_VMI_OEM_{m:%Y%m%d}.xlsx", write_xlsx([sheet]))])
        rec["truth"] = {"kind": "KES_VMI", "fg": fg, "wip": wip, "date": m.isoformat()}


# ------------------------------------------------------------------ free-text confirmations (EN / ES)

def text_confirmations(w):
    rng = w.rng
    for ln in w.po_lines:
        sup = ln["supplier"]
        if sup not in CONTACTS or not ln.get("history"):
            continue
        name, addr, company, country = CONTACTS[sup]
        po, line, qty = ln["po_id"], ln["line_no"], int(ln["qty"])
        if rng.random() < 0.08:                      # a vague first reply the parser must not guess at
            t0 = ln["history"][0]["t"] - dt.timedelta(hours=rng.uniform(6, 30))
            if t0 > ln["po_created"]:
                body = (f"Hi team,\n\nRe PO {po}: we will need to check line {line} with production and will revert "
                        f"with a firm date by Friday.\n\n{name}\n{company}")
                if country == "MX" and rng.random() < 0.5:
                    body = (f"Hola,\n\nSobre la OC {po}: estamos revisando la partida {line} con producción; les "
                            f"confirmamos fecha a más tardar el viernes.\n\n{name}\n{company}")
                rec = _mail(w, addr, "po-confirm@oem.example", f"RE: PO {po}", t0, body)
                rec["truth"] = {"kind": "PROMISE_TEXT", "abstain": True}
        for k, h in enumerate(ln["history"]):
            promise = h["promise"]
            ship = add_days(promise, -SHIP_TRANSIT_DAYS[sup])
            slip = k > 0
            style = rng.random()
            if slip:
                reason = h.get("note") or "capacity"
                if country == "MX" and style < 0.4:
                    body = (f"Buen día,\n\nPara la OC {po} partida {line} necesitamos mover la entrega al "
                            f"{promise:%d/%m/%Y} por {reason.lower()}. La cantidad no cambia ({qty} piezas).\n\n{name}")
                else:
                    body = (f"Hi,\n\nFor PO {po} line {line} we need to move delivery to {promise:%B} {promise.day}, "
                            f"{promise.year} due to {reason.lower()}. Qty unchanged ({qty:,}).\n\nSorry for the trouble,\n{name}")
            elif country == "MX" and style < 0.4:
                body = (f"Buen día,\n\nConfirmamos la OC {po} partida {line}: {qty:,} piezas, entrega "
                        f"{promise:%d/%m/%Y}.\n\nSaludos,\n{name}\n{company}")
            elif style < 0.55:
                body = (f"Hello,\n\nWe confirm PO {po} line {line}: {qty:,} pcs, delivery {promise:%Y-%m-%d}.\n\n"
                        f"Best regards,\n{name}\n{company}")
            elif style < 0.72:
                body = (f"Hi,\nPO#{po}-{line} confirmed. Qty {qty} / ship date {ship:%m/%d/%Y}.\nThanks!\n{name}")
            elif style < 0.86:
                body = (f"Yes, confirmed. Thank you.\n\n{name}\n\n> From: OEM Purchasing <po@oem.example>\n"
                        f"> Please confirm PO {po} line {line}: {qty} pcs, need date {promise:%Y-%m-%d}.\n")
            else:
                body = (f"Hi team,\n\nConfirming order {po}, item {line}. ETD {ship:%d-%b-%Y}, {qty:,} units.\n\n{name}")
            subj = f"RE: PO {po} line {line}" if rng.random() < 0.7 else f"PO {po} confirmation"
            rec = _mail(w, addr, "po-confirm@oem.example", subj, h["t"], body)
            rec["truth"] = {"kind": "PROMISE_TEXT", "po": po, "line": line, "promise": promise.isoformat(), "qty": qty}


def edi_and_portal(w):
    rng = w.rng
    w.raw_confirmations = []
    for ln in w.po_lines + w.cm_po_lines:
        sup = ln["supplier"]
        if sup in ("KES", "HDS"):
            ch = "EDI855"
        elif sup in ("CSP", "TNM", "FAP"):
            ch = "PORTAL"
        else:
            continue
        if ln.get("unparsed_email") and sup == "HDS":
            t = ln["po_created"] + dt.timedelta(hours=3)
            payload = _edi855(ln, ln["need"], "BP", t)
            w.raw_confirmations.append({"supplier": sup, "t": t, "channel": "EDI855", "payload": payload,
                                        "truth": {"abstain": True}})
            continue
        for k, h in enumerate(ln.get("history", [])):
            if ch == "EDI855":
                payload = _edi855(ln, h["promise"], "IA" if k == 0 else "IC", h["t"])
            else:
                payload = json.dumps({"po": ln["po_id"], "line": ln["line_no"], "qty": ln["qty"],
                                      "promise_date": h["promise"].isoformat(),
                                      "comment": h.get("note", ""), "user": f"{sup.lower()}.planner"})
            w.raw_confirmations.append({"supplier": sup, "t": h["t"], "channel": ch, "payload": payload,
                                        "truth": {"po": ln["po_id"], "line": ln["line_no"],
                                                  "promise": h["promise"].isoformat(), "qty": ln["qty"]}})
    w.raw_confirmations.sort(key=lambda r: r["t"])


def _edi855(ln, promise, ack, t):
    ts = t.astimezone(UTC)
    ctrl = int(hashlib.md5(f"{ln['po_id']}-{ln['line_no']}-{ack}".encode()).hexdigest()[:8], 16) % 10 ** 9
    return ("ISA*00*          *00*          *ZZ*SUPPLIER       *ZZ*OEMMOBILITY    *"
            f"{ts:%y%m%d}*{ts:%H%M}*U*00401*{ctrl:09d}*0*P*>~"
            f"GS*PR*SUPPLIER*OEMMOBILITY*{ts:%Y%m%d}*{ts:%H%M}*{ctrl % 100000}*X*004010~ST*855*0001~"
            f"BAK*00*AC*{ln['po_id']}*{ts:%Y%m%d}~PO1*{ln['line_no']}*{int(ln['qty'])}*EA*{ln['unit_price']:.2f}**BP*{ln['item']}~"
            f"ACK*{ack}*{int(ln['qty'])}*EA*067*{promise:%Y%m%d}~CTT*1~SE*6*0001~GE*1*{ctrl % 100000}~IEA*1*{ctrl:09d}~")


def noise(w):
    rng = w.rng
    for k in range(7):
        t = at(add_days(w.as_of, -rng.randint(1, 28)), rng.randint(6, 22), rng.randint(0, 59), PT).astimezone(UTC)
        kind = k % 3
        if kind == 0:
            rec = _mail(w, "noreply@baylineseals.example", "po-confirm@oem.example", "Automatic reply: Out of office",
                        t, "I am out of the office until Monday with limited access to email.")
        elif kind == 1:
            rec = _mail(w, "news@pacificlinklines.example", "cm-reports@oem.example",
                        "Pacific Link Lines: Q4 transpacific market update", t,
                        "Rates, blank sailings and peak-season surcharges for Q4.")
        else:
            rec = _mail(w, "pmc-report@formosa-ap.example", "cm-reports@oem.example",
                        "中秋節放假通知 Mid-Autumn Festival holiday notice", t,
                        "親愛的客戶：本廠於中秋節放假一天，9/25 停工。Plant closed for Mid-Autumn Festival on 9/25.")
        rec["truth"] = {"kind": "NOISE"}
