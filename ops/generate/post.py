"""After ingest: stock snapshots, chargebacks already in flight, the closed loops that
already ran (so the audit trail has history), and the assurance history (earlier
eval and test runs, change reviews) that the current runs extend.
"""
import datetime as dt
import json

from ..logic.chargeback import claim_lines, post_to_erp, write_chargeback
from .util import PT, TPE, UTC, add_days, at, iso


def post_ingest(w):
    inventory(w)
    chargebacks(w)
    history(w)


def inventory(w):
    c = w.conn
    now = w.now_s
    rows = []
    for lot in w.lots.values():
        if lot["origin"] != "OEM_RECEIPT":
            continue
        left = lot["usable"] - lot["used"]
        if lot["iqc"] == "REJECTED":
            continue                                             # returned to vendor
        if left <= 0:
            continue
        status = "QC_HOLD" if lot["iqc"] == "PENDING" else "AVAILABLE"
        rows.append(("OEM-FRE", lot["item"], lot["lot_id"], "OEM", status, left, now, "OEM_ERP"))
    rows.append(("3PL-RNO", "CHG-1", None, "OEM", "AVAILABLE", max(0, getattr(w, "charger_on_hand", 0)), now, "3PL_WMS"))
    # CM-owned stock of CM-sourced parts, from the CM's weekly report
    for lot in w.lots.values():
        if lot["origin"] == "CM_FEED":
            left = lot["qty"] - lot["used"]
            if left > 0:
                rows.append(("CM-TXG", lot["item"], lot["lot_id"], "CM", "AVAILABLE", left, now, "CM_REPORT"))
    # supplier-held stock (portal / EDI 846); Kestrel's arrives through the email pipeline
    for site, item, avail, wip in (("SUP-PNC", "BMS-B", 180, 1200), ("SUP-TNM", "DU-C", 420, 600),
                                   ("SUP-TNM", "PU-1", 380, 450), ("SUP-HDS", "HMI-1", 900, 800),
                                   ("SUP-SMT", "ENC-STD", 300, 250), ("SUP-SMT", "ENC-LRG", 240, 200),
                                   ("SUP-VPC", "CHG-1", 2600, 1500)):
        rows.append((site, item, None, "SUPPLIER", "AVAILABLE", avail, w.as_of.isoformat(), "SUPPLIER_REPORT"))
        rows.append((site, item, None, "SUPPLIER", "IN_PRODUCTION", wip, w.as_of.isoformat(), "SUPPLIER_REPORT"))
    c.executemany("INSERT INTO inventory_balance(site_id, item_id, lot_id, owner, stock_status, qty, as_of, source)"
                  " VALUES (?,?,?,?,?,?,?,?)", rows)


def chargebacks(w):
    c = w.conn
    a = w.as_of
    t = lambda d, h=10: iso(at(d, h, 0, PT))
    # CB-0001: cell lot sorted at IQC (posted)
    lot = w.stories.get("cell_sort_lot")
    write_chargeback(c, "CB-0001", "KES", "IQC_REJECT", f"100% sort of cell lot {lot} (OCV/IR outliers)",
                     [("QUALITY_EVENT", None, f"Sort labor 24 h x $55 on lot {lot}", 1320.0),
                      ("COST", None, "Retest fixtures and admin fee", 150.0)],
                     "ACCEPTED", t(add_days(a, -88)), sent_at=t(add_days(a, -87)), responded_at=t(add_days(a, -76)))
    c.execute("UPDATE chargeback_line SET ref_id=(SELECT qe_id FROM quality_event WHERE lot_id=? LIMIT 1)"
              " WHERE chargeback_id='CB-0001' AND line_no=1", (lot,))
    post_to_erp(c, "CB-0001", t(add_days(a, -70)))
    # CB-0002: brake squeal claims billed to the CM (disputed)
    squeal = [r["claim_id"] for r in c.execute(
        "SELECT claim_id FROM warranty_claim WHERE defect_code='FLD-SQUEAL' AND supplier_id='FAP' AND reported_at < ?"
        " AND status != 'REJECTED' ORDER BY reported_at", (t(add_days(a, -10)),))]
    if squeal:
        write_chargeback(c, "CB-0002", "FAP", "WARRANTY", f"Brake squeal: {len(squeal)} field claims (S50 torque process)",
                         claim_lines(c, "FAP", squeal), "DISPUTED", t(add_days(a, -10)), sent_at=t(add_days(a, -9)),
                         responded_at=t(add_days(a, -4)),
                         notes="FAP attributes root cause to Taoyuan Brake pad compound; requests joint teardown")
    # CB-0003: HMI dead pixels billed to Hsinchu Display (accepted and posted)
    pixel = [r["claim_id"] for r in c.execute(
        "SELECT claim_id FROM warranty_claim WHERE defect_code='FLD-PIXEL' AND reported_at < ? AND status != 'REJECTED'"
        " ORDER BY reported_at",
        (t(add_days(a, -14)),))]
    if pixel:
        write_chargeback(c, "CB-0003", "HDS", "WARRANTY", f"HMI dead pixels: {len(pixel)} field claims",
                         claim_lines(c, "HDS", pixel), "ACCEPTED", t(add_days(a, -13)), decision_id=None,
                         sent_at=t(add_days(a, -12)), responded_at=t(add_days(a, -10)))
        post_to_erp(c, "CB-0003", t(add_days(a, -9)))
    # CB-0004: enclosure lot rejected at IQC (sent, no response yet)
    enc = w.stories.get("enc_reject_lot")
    write_chargeback(c, "CB-0004", "SMT", "IQC_REJECT", f"Enclosure lot {enc} rejected (sealing-face flatness)",
                     [("QUALITY_EVENT", "NCR-0031", "Return-to-vendor freight", 640.0),
                      ("COST", None, "100% CMM check of next lots, 20 h x $55", 1100.0),
                      ("COST", None, "Pack line slowdown, 2 shifts at 70% (contribution lost)", 3200.0),
                      ("COST", None, "Admin fee", 150.0)],
                     "SENT", t(add_days(a, -31)), sent_at=t(add_days(a, -30)))


def history(w):
    """Closed loops that already ran, plus eval/test history and reviewed changes."""
    c = w.conn
    a = w.as_of
    t = lambda d, h=10: iso(at(d, h, 0, PT))
    dec = [
        ("D-0097", "DATA", "DATA-SCHEMA-DRIFT", "CM_MES", "CM MES upgrade broke the feed: deploy mapping v2 and replay",
         "Messages after the FAP MES upgrade arrived with camelCase keys and ISO timestamps; mapping v1 quarantined them.",
         json.dumps({"quarantined": "see raw_cm_mes_event"}), "Deploy mapping v2 (reviewed as CR-0007) and replay quarantine",
         json.dumps({"messages": "all replayed"}), "EXECUTED", iso(w.schema_cutover + dt.timedelta(minutes=40)),
         "Data eng. lead", iso(w.mapping_v2_at), json.dumps({"writes": {"mapping_version": 1, "station_event": "replayed"}})),
        ("D-0099", "SUPPLY", "SUPPLY-DEVIATION", "BMS-B", "Approve DEV-0012: use BMS rev A for up to 300 packs",
         "Rev B deliveries short on AFE allocation; 300 rev A boards on hand are safe with firmware lockout.",
         None, "Approve deviation DEV-0012 and allow rev A at P30 in the MES", None, "EXECUTED",
         t(add_days(a, -13)), "Battery eng. + Planning", t(w.dev12_start, 9),
         json.dumps({"writes": {"deviation": 1, "outbound_message": 1}})),
        ("D-0101", "QUALITY", "QUALITY-RECOVERY", "HDS", "Recover HMI dead-pixel claims from Hsinchu Display",
         "Field claims traced to HMI serials; supplier terms cover parts, labor and admin.", None,
         "Draft chargeback CB-0003, send, post debit memo on acceptance", None, "EXECUTED", t(add_days(a, -13)),
         "SQE", t(add_days(a, -9)), json.dumps({"writes": {"chargeback": 1, "chargeback_line": "n", "erp_journal_entry": 1}})),
        ("D-0102", "QUALITY", "QUALITY-IQC-REJECT", "ENC-STD", "Reject enclosure lot at IQC, RTV and recover costs",
         "Sealing-face flatness out of tolerance in 18 of 32 samples.", None,
         "RTV the lot, expedite replacement, charge back Summit (CB-0004)", None, "EXECUTED", t(add_days(a, -34), 16),
         "SQE", t(add_days(a, -33)), json.dumps({"writes": {"quality_event": 1, "chargeback": 1}})),
    ]
    c.executemany("INSERT INTO decision_log VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", dec)
    c.execute("UPDATE chargeback SET decision_id='D-0101' WHERE chargeback_id='CB-0003'")
    c.execute("UPDATE chargeback SET decision_id='D-0102' WHERE chargeback_id='CB-0004'")
    out = [
        ("CM_MES", "MAPPING_DEPLOYED", "CM_MES v2", json.dumps({"version": "v2"}), iso(w.mapping_v2_at), "ACKED", "D-0097"),
        ("OEM_MES", "ALLOW_ALTERNATE_PART", "DEV-0012", json.dumps({"station": "FRE-P1-P30", "allow": "BMS-A", "limit": 300}),
         t(w.dev12_start, 9), "ACKED", "D-0099"),
        ("SUPPLIER_PORTAL", "CHARGEBACK_NOTICE", "CB-0003", json.dumps({"supplier": "HDS"}), t(add_days(a, -12)), "ACKED", "D-0101"),
        ("ERP", "DEBIT_MEMO", "CB-0003", json.dumps({"debit_memo": "DM-0003"}), t(add_days(a, -9)), "ACKED", "D-0101"),
        ("SUPPLIER_PORTAL", "CHARGEBACK_NOTICE", "CB-0004", json.dumps({"supplier": "SMT"}), t(add_days(a, -30)), "SENT", "D-0102"),
        ("ERP", "DEBIT_MEMO", "CB-0001", json.dumps({"debit_memo": "DM-0001"}), t(add_days(a, -70)), "ACKED", None),
    ]
    c.executemany("INSERT INTO outbound_message(target_system, message_type, ref, payload, created_at, status, decision_id)"
                  " VALUES (?,?,?,?,?,?,?)", out)

    # earlier eval runs (the current ones are computed live by ops.logic.evals)
    runs = []
    for k in range(8, 0, -1):
        day = add_days(a, -7 * k)
        score = 0.9996 if day < add_days(a, -30) else 0.9994
        runs.append(("EV-CM-MES", t(day, 6), "v1" if day < add_days(a, -30) else "v2", 400, round(400 * score), score, "PASS"))
        n, score = 20 + 4 * (8 - k), 0.86 if k > 4 else 0.93
        runs.append(("EV-WARRANTY-CLS", t(day, 6), "rules-2026.08" if k > 4 else "rules-2026.09",
                     n, round(n * score), score, "FAIL" if k > 4 else "PASS"))
        n = 40 + 6 * (8 - k)
        runs.append(("EV-PROMISE-PARSE", t(day, 6), "parser-1.1" if k > 3 else "parser-1.2", n, n, 1.0, "PASS"))
        runs.append(("EV-MRP-TEXTBOOK", t(day, 6), "mrp-0.9" if k > 5 else "mrp-1.0", 6, 5 if k > 5 else 6,
                     0.8333 if k > 5 else 1.0, "FAIL" if k > 5 else "PASS"))
    # the schema-drift morning: the gate caught it before anything downstream consumed bad data
    runs.append(("EV-CM-MES", iso(w.schema_cutover + dt.timedelta(minutes=35)), "v1", 400, 373, 0.9325, "FAIL"))
    ids = {}
    for r in sorted(runs, key=lambda r: r[1]):
        cur = c.execute("INSERT INTO eval_run(suite_id, ran_at, subject_version, cases, passed, score, gate, metrics_json)"
                        " VALUES (?,?,?,?,?,?,?,?)", r + (json.dumps({"historical": True}),))
        ids[(r[0], r[2])] = cur.lastrowid
    tests = []
    for k, (n, fails) in enumerate([(31, 0), (38, 2), (38, 0), (44, 0), (52, 1), (52, 0), (58, 0)]):
        day = add_days(a, -60 + k * 9)
        cur = c.execute("INSERT INTO test_run(ran_at, suite, tests, failures, errors, skipped, duration_s, detail_json)"
                        " VALUES (?,?,?,?,?,?,?,?)", (t(day, 7), "unit+integration", n, fails, 0, 0, round(2.1 + n * 0.05, 2),
                                                      json.dumps({"historical": True})))
        tests.append(cur.lastrowid)
    reviews = [
        ("CR-0007", "CM MES mapping v2 (FAP MES upgrade: camelCase keys, ISO timestamps)", "ops.ingest.cm_mes", "MAPPING",
         "Coding agent (Claude) + data eng.", iso(w.schema_cutover + dt.timedelta(minutes=45)),
         "New mapping_version row; normalizer unchanged (mapping is data). Replay quarantined messages after deploy.",
         tests[3], ids.get(("EV-CM-MES", "v2")), 1,
         json.dumps(["unit tests pass", "eval EV-CM-MES >= 99.5% on replayed sample", "contracts clean", "rollback = previous version row"]),
         "Data eng. lead", "DEPLOYED", iso(w.mapping_v2_at), "Deployed 2h15m after the CM's upgrade", "D-0097"),
        ("CR-0009", "Warranty classifier rules 2026.09: 'won't hold a charge' -> capacity fade", "ops.ingest.warranty", "RULE",
         "Coding agent (Claude)", t(add_days(a, -30), 11),
         "Two rules added, one reordered. Labeled-set accuracy 86% -> 93%; confusion concentrated in charge vs. fade.",
         tests[5], ids.get(("EV-WARRANTY-CLS", "rules-2026.09")), 1,
         json.dumps(["eval above 90% threshold", "no regression on other codes", "sample of 10 cases read by SQE"]),
         "SQE lead", "DEPLOYED", t(add_days(a, -28)), None, None),
        ("CR-0011", "ATP: fleet orders take priority within 14 days of requested date", "ops.logic.atp", "RULE",
         "Ops eng.", t(add_days(a, -40), 15), "Allocation priority tuple gains a fleet window; backtest OTD +2.4 pts.",
         tests[2], None, 1, json.dumps(["backtest on 60 days", "commercial sign-off"]), "Commercial ops", "DEPLOYED",
         t(add_days(a, -38)), None, None),
        ("CR-0012", "Promise parser 1.2: Spanish confirmations and 'ETD dd-Mon-yyyy'", "ops.ingest.email", "LOGIC",
         "Coding agent (Claude)", t(add_days(a, -24), 13),
         "Coverage 81% -> 88% with precision held at 100% (parser abstains instead of guessing).",
         tests[5], ids.get(("EV-PROMISE-PARSE", "parser-1.2")), 1,
         json.dumps(["precision 100% on labeled set", "abstentions routed to buyer queue"]), "Procurement lead", "DEPLOYED",
         t(add_days(a, -23)), None, None),
        ("CR-0014", "MRP 1.0: alternate part under deviation + lot sizing by multiple", "ops.logic.mrp", "LOGIC",
         "Coding agent (Claude)", t(add_days(a, -20), 10),
         "Textbook eval failed on MOQ-with-multiple case in 0.9; fixed and all six cases pass.",
         tests[5], ids.get(("EV-MRP-TEXTBOOK", "mrp-1.0")), 1,
         json.dumps(["6/6 textbook cases", "planner reviewed BMS-B plan"]), "Planning lead", "DEPLOYED", t(add_days(a, -19)),
         None, None),
    ]
    c.executemany("INSERT INTO change_review VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", reviews)
