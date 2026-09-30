"""Supplier cost recovery: contractual terms -> chargeback lines -> ERP debit memo.

Terms live on the supplier (supplier.recovery_terms): parts at a percentage,
labor at a rate with an hours cap, logistics at actuals, an admin fee per claim.
The same function prices a draft in the closed loop and a posted historical one,
so the numbers in the ERP always trace back to claims.
"""
import json

LABOR_RATE_DEFAULT = 85.0

# A claim is billable once it is diagnosed (not open, not rejected), belongs to a supplier, is not already on a
# chargeback, and the classifier's code agrees with the technician's evidence. The closed loop and the Warranty page
# both bill by this one rule.
BILLABLE = ("DIAGNOSED", "REPAIRED", "CLOSED")
CATEGORY_OF = {"FLD-PIXEL": "Display", "FLD-SQUEAL": "Brakes", "FLD-NOISE": "Drive", "FLD-FW": "Software",
               "FLD-CHG": "Battery", "FLD-CAPFADE": "Battery"}
PART_FAMILY = {"HMI": "Display", "BRK": "Brakes", "DU-": "Drive", "PK-": "Battery", "BMS": "Battery"}


def conflict(defect_code, crm_category, parts):
    """The classifier reads the symptom; the technician's category and replaced part are independent evidence.
    Returns why they disagree (the claim goes to review before billing), or None."""
    if not defect_code:
        return "Symptom was not classified"
    want = CATEGORY_OF.get(defect_code)
    notes = []
    if crm_category and want and crm_category != want:
        notes.append(f"CRM category is '{crm_category}'")
    for p in parts:
        fam = next((v for k, v in PART_FAMILY.items() if (p.get("part") or "").startswith(k)), None)
        if fam and want and fam != want:
            notes.append(f"technician replaced {p['part']}")
    if not notes:
        return None
    return f"{' and '.join(notes)}, but the classifier coded {defect_code} ({want}). Review before billing."


def billable_claim_ids(conn, supplier_id, claim_ids):
    """The subset of `claim_ids` that `supplier_id` can be billed for now."""
    out = []
    for cid in claim_ids:
        r = conn.execute("""SELECT w.supplier_id, w.status, w.chargeback_id, w.defect_code,
                                   json_extract(r.payload, '$.category') AS crm_category,
                                   json_extract(r.payload, '$.parts_replaced') AS parts_json
                            FROM warranty_claim w LEFT JOIN raw_warranty_case r ON r.raw_id = w.raw_id
                            WHERE w.claim_id = ?""", (cid,)).fetchone()
        if (r and r["supplier_id"] == supplier_id and r["status"] in BILLABLE and not r["chargeback_id"]
                and not conflict(r["defect_code"], r["crm_category"], json.loads(r["parts_json"] or "[]"))):
            out.append(cid)
    return out


def terms_for(conn, supplier_id):
    row = conn.execute("SELECT recovery_terms FROM supplier WHERE supplier_id=?", (supplier_id,)).fetchone()
    return json.loads(row["recovery_terms"]) if row and row["recovery_terms"] else {
        "parts_pct": 1.0, "labor_rate_usd": LABOR_RATE_DEFAULT, "labor_hours_cap": 3, "admin_fee_usd": 150}


def claim_lines(conn, supplier_id, claim_ids):
    """One chargeback line per claim, priced by the supplier's recovery terms."""
    t = terms_for(conn, supplier_id)
    lines = []
    for cid in claim_ids:
        c = conn.execute("SELECT * FROM warranty_claim WHERE claim_id=?", (cid,)).fetchone()
        labor_hours = c["cost_labor_usd"] / LABOR_RATE_DEFAULT
        labor = min(labor_hours, t["labor_hours_cap"]) * t["labor_rate_usd"]
        amount = round(c["cost_parts_usd"] * t["parts_pct"] + labor + c["cost_logistics_usd"] + t["admin_fee_usd"], 2)
        desc = (f"{c['defect_code']} on {c['serial']}: parts ${c['cost_parts_usd']:.2f} x {t['parts_pct']:.0%}, "
                f"labor {min(labor_hours, t['labor_hours_cap']):.1f}h x ${t['labor_rate_usd']}, logistics "
                f"${c['cost_logistics_usd']:.2f}, admin ${t['admin_fee_usd']}")
        lines.append(("CLAIM", cid, desc, amount))
    return lines


def write_chargeback(conn, cb_id, supplier_id, basis, title, lines, status, created_at, decision_id=None, notes=None,
                     sent_at=None, responded_at=None):
    total = round(sum(l[3] for l in lines), 2)
    conn.execute("INSERT INTO chargeback(chargeback_id, supplier_id, basis, title, amount_usd, status, created_at, sent_at,"
                 " responded_at, decision_id, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 (cb_id, supplier_id, basis, title, total, status, created_at, sent_at, responded_at, decision_id, notes))
    conn.executemany("INSERT INTO chargeback_line VALUES (?,?,?,?,?,?)",
                     [(cb_id, i + 1, l[0], l[1], l[2], l[3]) for i, l in enumerate(lines)])
    for kind, ref, _, _ in lines:
        if kind == "CLAIM":
            conn.execute("UPDATE warranty_claim SET chargeback_id=? WHERE claim_id=?", (cb_id, ref))
    return total


def post_to_erp(conn, cb_id, posted_at, je_id=None):
    """Debit memo: reduce what we owe the supplier, book the recovery against the cost it offsets."""
    cb = conn.execute("SELECT * FROM chargeback WHERE chargeback_id=?", (cb_id,)).fetchone()
    je_id = je_id or f"JE-{posted_at[:4]}-{int(conn.execute('SELECT COUNT(*) n FROM erp_journal_entry').fetchone()['n']) + 1:05d}"
    memo_no = f"DM-{cb_id[3:]}"
    credit_acct = "5410" if cb["basis"] == "WARRANTY" else "5110"
    conn.execute("INSERT INTO erp_journal_entry VALUES (?,?,?,?,?,?,?)",
                 (je_id, "DEBIT_MEMO", posted_at, cb["supplier_id"], f"Debit memo {memo_no}: {cb['title']}", cb_id, "POSTED"))
    conn.executemany("INSERT INTO erp_journal_line VALUES (?,?,?,?,?,?,?)", [
        (je_id, 1, "2000", cb["amount_usd"], 0.0, "PURCH", f"Reduce AP to {cb['supplier_id']}"),
        (je_id, 2, credit_acct, 0.0, cb["amount_usd"], "QUALITY" if credit_acct == "5110" else "SERVICE",
         "Supplier recovery"),
    ])
    conn.execute("UPDATE chargeback SET status='POSTED', posted_at=?, debit_memo_no=?, je_id=? WHERE chargeback_id=?",
                 (posted_at, memo_no, je_id, cb_id))
    return je_id
