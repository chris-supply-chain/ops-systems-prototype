"""CRM warranty cases -> warranty_claim, with the symptom classifier, failed-part
attribution through genealogy, and service swaps recorded as as-maintained
genealogy.

The classifier is deliberately simple, ordered rules (v2026.09). It sits behind
the EV-WARRANTY-CLS eval. Anything that replaces it, an LLM included, has to
beat its score on the same labeled set before it ships.
"""
import json
import re

from .common import Run

CLASSIFIER_VERSION = "rules-2026.09"
RULES = [
    ("FLD-PIXEL", [r"pixel", r"line through", r"black spots", r"stuck line", r"flicker"]),
    ("FLD-CHG", [r"won'?t charge", r"not charging", r"\bE07\b", r"charger light", r"dead even after charging"]),
    ("FLD-CAPFADE", [r"hold a charge", r"range dropped", r"drops from \d+%", r"falls quickly", r"dies fast"]),
    ("FLD-SQUEAL", [r"squeal", r"squeak", r"grind", r"rotor", r"brak"]),
    ("FLD-NOISE", [r"whin", r"\bhum\b", r"click", r"motor", r"\bhub\b"]),
    ("FLD-FW", [r"\bapp\b", r"bluetooth", r"pair", r"update", r"froze", r"ride modes", r"reboot"]),
]


def classify(symptom):
    for code, pats in RULES:
        if any(re.search(p, symptom, re.I) for p in pats):
            return code
    return None


def run_warranty(conn, now_s):
    run = Run(conn, "CRM", now_s, CLASSIFIER_VERSION)
    resp = {r["defect_code"]: r["default_responsible"] for r in conn.execute("SELECT defect_code, default_responsible FROM defect_code")}
    rows = conn.execute("SELECT raw_id, received_at, payload FROM raw_warranty_case WHERE ingest_status='PENDING'"
                        " ORDER BY received_at").fetchall()
    for r in rows:
        run.counts["in"] += 1
        p = json.loads(r["payload"])
        ref = f"raw_warranty_case:{r['raw_id']}"
        vehicle = p["asset_serial"]
        if not conn.execute("SELECT 1 FROM unit WHERE serial=?", (vehicle,)).fetchone():
            conn.execute("UPDATE raw_warranty_case SET ingest_status='QUARANTINED', ingest_note=? WHERE raw_id=?",
                         (f"unknown asset serial {vehicle}", r["raw_id"]))
            run.counts["quarantined"] += 1
            continue
        code = classify(p["symptom"])
        notes = [] if code else ["symptom not classified"]
        failed_item = failed_serial = failed_lot = supplier = None
        parts = p.get("parts_replaced") or []
        if parts:
            failed_item = parts[0]["part"]
            failed_serial = parts[0].get("removed_serial")
        if code in ("FLD-CAPFADE", "FLD-CHG"):
            pack = failed_serial or _current_pack(conn, vehicle)
            failed_serial, failed_item = pack, failed_item or _item(conn, pack)
            if code == "FLD-CAPFADE":
                lot = conn.execute("SELECT child_lot_id, SUM(qty) q FROM genealogy WHERE parent_serial=? AND child_item_id='CEL-21700'"
                                   " GROUP BY child_lot_id ORDER BY q DESC LIMIT 1", (pack,)).fetchone()
                if lot:
                    failed_lot = lot["child_lot_id"]
                    supplier = "KES"
            else:
                bms = conn.execute("SELECT child_serial FROM genealogy WHERE parent_serial=? AND position='BMS' AND removed_at IS NULL",
                                   (pack,)).fetchone()
                if bms:
                    failed_serial, failed_item = bms["child_serial"], _item(conn, bms["child_serial"])
                supplier = "PNC"
        elif code == "FLD-SQUEAL":
            lot = conn.execute("SELECT child_lot_id FROM genealogy WHERE parent_serial=? AND child_item_id='BRK-1'",
                               (vehicle,)).fetchone()
            failed_item, failed_lot = "BRK-1", lot["child_lot_id"] if lot else None
            supplier = "FAP"
        elif code == "FLD-PIXEL":
            if not failed_serial:
                row = conn.execute("SELECT child_serial FROM genealogy WHERE parent_serial=? AND position='HMI' AND removed_at IS NULL",
                                   (vehicle,)).fetchone()
                failed_serial = row["child_serial"] if row else None
            failed_item, supplier = "HMI-1", "HDS"
        elif code == "FLD-NOISE":
            if not failed_serial:
                row = conn.execute("SELECT child_serial FROM genealogy WHERE parent_serial=? AND position='DRIVE_UNIT'"
                                   " AND removed_at IS NULL", (vehicle,)).fetchone()
                failed_serial = row["child_serial"] if row else None
            failed_item, supplier = failed_item or _item(conn, failed_serial), "TNM"
        if code and resp.get(code) == "OEM":
            supplier = None
        labor = min(float(p.get("labor_hours") or 0), 3.0) * 85.0
        claim_id = "WC-" + p["case_no"].split("-")[1]
        status = p["status"]
        conn.execute(
            "INSERT INTO warranty_claim(claim_id, serial, order_id, reported_at, symptom, defect_code, failed_item_id,"
            " failed_serial, failed_lot_id, supplier_id, cost_parts_usd, cost_labor_usd, cost_logistics_usd, status, raw_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (claim_id, vehicle, p.get("order_ref"), p["opened_at"], p["symptom"], code, failed_item,
             failed_serial if _exists(conn, failed_serial) else None, failed_lot, supplier,
             float(p.get("parts_cost") or 0), labor, float(p.get("logistics_cost") or 0), status, r["raw_id"]))
        # service swap of a pack: the as-maintained genealogy changes, the as-built history stays
        for part in parts:
            new = part.get("installed_serial")
            old = part.get("removed_serial")
            if part["part"] in ("PK-STD", "PK-LRG") and new and _exists(conn, new):
                slot = conn.execute("SELECT position FROM genealogy WHERE parent_serial=? AND child_serial=? AND removed_at IS NULL",
                                    (vehicle, old)).fetchone()        # the new pack takes the old one's slot (PACK or EXTRA_PACK)
                conn.execute("UPDATE genealogy SET removed_at=?, removal_reason=? WHERE parent_serial=? AND child_serial=?"
                             " AND removed_at IS NULL", (p["opened_at"], f"Warranty swap ({claim_id})", vehicle, old))
                conn.execute("INSERT INTO genealogy(parent_serial, child_serial, child_item_id, qty, relation, position,"
                             " installed_at, source) VALUES (?,?,?,1,'SHIPPED_WITH',?,?,'SERVICE') ON CONFLICT DO NOTHING",
                             (vehicle, new, part["part"], slot["position"] if slot else "PACK", p["opened_at"]))
                conn.execute("UPDATE unit SET status='DELIVERED', location_site_id=NULL WHERE serial=?", (new,))
                conn.execute("UPDATE unit SET status='RETURNED', location_site_id='OEM-FRE' WHERE serial=?", (old,))
                notes.append(f"pack swap {old} -> {new}")
        conn.execute("UPDATE raw_warranty_case SET ingest_status=?, ingest_note=? WHERE raw_id=?",
                     ("WARN" if not code else "OK", "; ".join(notes) or f"classified {code} ({CLASSIFIER_VERSION})", r["raw_id"]))
        run.counts["warn" if not code else "ok"] += 1
        run.step(ref, 1, "PARSED", "OK", r["received_at"], f"case {p['case_no']}: '{p['symptom']}'")
        run.step(ref, 2, "MAPPED", "OK" if code else "WARN", r["received_at"], f"{code or 'unclassified'} via {CLASSIFIER_VERSION}"
                 + (f"; traced to lot {failed_lot}" if failed_lot else ""))
    run.finish(now_s)
    return run.counts


def _current_pack(conn, vehicle):
    row = conn.execute("SELECT child_serial FROM genealogy WHERE parent_serial=? AND relation='SHIPPED_WITH' AND position='PACK'"
                       " AND removed_at IS NULL", (vehicle,)).fetchone()
    return row["child_serial"] if row else None


def _item(conn, serial):
    if not serial:
        return None
    row = conn.execute("SELECT item_id FROM unit WHERE serial=?", (serial,)).fetchone()
    return row["item_id"] if row else None


def _exists(conn, serial):
    return bool(serial) and conn.execute("SELECT 1 FROM unit WHERE serial=?", (serial,)).fetchone() is not None
