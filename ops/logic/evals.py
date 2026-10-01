"""Evals: every "model" in the platform scored against truth before it may act.

  EV-CM-MES          normalizer output vs what physically happened (the simulator's ground truth)
  EV-GENEALOGY       forward trace vs the true set of affected units
  EV-WARRANTY-CLS    symptom classifier vs the true failure mode
  EV-PROMISE-PARSE   email/Excel/EDI promise extraction vs the date the supplier meant (precision first)
  EV-ATP-BACKTEST    first promise vs actual delivery
  EV-MRP-TEXTBOOK    MRP netting vs hand-computed textbook cases

A suite below its threshold fails its gate. Every run is also compared with the
suite's previous run, and a score below it is marked regressed even while it still
clears the threshold. A mapping or rule change cannot be marked deployed in
change_review with a failing gate or a regressed eval.
"""
import datetime as dt
import json

from ..db import now as get_now

VERSIONS = {"EV-CM-MES": lambda c: c.execute("SELECT version FROM mapping_version WHERE source='CM_MES'"
                                             " ORDER BY effective_from DESC LIMIT 1").fetchone()["version"],
            "EV-GENEALOGY": lambda c: "genealogy-1.0", "EV-WARRANTY-CLS": lambda c: "rules-2026.09",
            "EV-PROMISE-PARSE": lambda c: "parser-1.2", "EV-ATP-BACKTEST": lambda c: "atp-1.1",
            "EV-MRP-TEXTBOOK": lambda c: "mrp-1.0"}


# ---------------------------------------------------------------- textbook MRP cases (hand-computed)

TEXTBOOK = [
    {"id": "lot-for-lot",
     "why": "OH 20, LT 2, L4L: shortfalls on days 2-4 become planned receipts of exactly the net requirement",
     "args": {"days": 5, "on_hand": 20, "safety_stock": 0, "lead_time": 2, "moq": 0, "mult": 0,
              "gross": {1: 10, 2: 15, 3: 5, 4: 20}, "receipts": [], "rule": "LOT_FOR_LOT"},
     "expect": {"planned_receipts": {2: 5, 3: 5, 4: 20}, "planned_releases": {0: 5, 1: 5, 2: 20}}},
    {"id": "moq-and-multiple",
     "why": "OH 100, SS 50, MOQ 200, multiple 50: day-1 net of 10 becomes 200 (released 2 days late); day 6 again",
     "args": {"days": 8, "on_hand": 100, "safety_stock": 50, "lead_time": 3, "moq": 200, "mult": 50,
              "gross": {1: 60, 4: 80, 6: 120}, "receipts": []},
     "expect": {"planned_receipts": {1: 200, 6: 200}, "planned_releases": {-2: 200, 3: 200},
                "messages": ["PAST_DUE_RELEASE"]}},
    {"id": "reschedule-in",
     "why": "OH 30 cannot cover day-2 demand of 50; the 100 already on order for day 6 is pulled in instead of a new order",
     "args": {"days": 8, "on_hand": 30, "safety_stock": 0, "lead_time": 5, "moq": 0, "mult": 0,
              "gross": {2: 50}, "receipts": [{"index": 6, "qty": 100, "ref": "PO-1", "confirmed": True}]},
     "expect": {"planned_receipts": {}, "messages": ["SHORTAGE", "EXPEDITE"]}},
    {"id": "not-needed",
     "why": "OH 500 covers all demand; a receipt of 200 on day 40 is not needed inside the horizon",
     "args": {"days": 60, "on_hand": 500, "safety_stock": 0, "lead_time": 5, "moq": 0, "mult": 0,
              "gross": {3: 100}, "receipts": [{"index": 40, "qty": 200, "ref": "PO-2", "confirmed": True}]},
     "expect": {"planned_receipts": {}, "messages": ["CANCEL"]}},
    {"id": "alternate-under-deviation",
     "why": "Primary 10 on hand; day-1 need 30 uses 20 of the alternate (allowance 35, valid to day 1); day 2 is short 30",
     "args": {"days": 4, "on_hand": 10, "safety_stock": 0, "lead_time": 10, "moq": 0, "mult": 0,
              "gross": {1: 30, 2: 30}, "receipts": [],
              "alternates": [{"item": "ALT", "on_hand": 40, "allowance": 35, "valid_to": 1}]},
     "expect": {"alt": {1: 20}, "first_short": 2, "projected_min": -30}},
    {"id": "unconfirmed-receipt",
     "why": "A receipt without a supplier promise is planned on the need date and flagged",
     "args": {"days": 6, "on_hand": 0, "safety_stock": 0, "lead_time": 1, "moq": 0, "mult": 0,
              "gross": {3: 10}, "receipts": [{"index": 3, "qty": 10, "ref": "PO-3", "confirmed": False}]},
     "expect": {"planned_receipts": {}, "messages": ["UNCONFIRMED"]}},
]


def textbook_results():
    from .mrp import net_item
    out = []
    for case in TEXTBOOK:
        a = dict(case["args"])
        res = net_item(a.pop("days"), a.pop("on_hand"), a.pop("safety_stock"), a.pop("lead_time"), a.pop("moq"),
                       a.pop("mult"), a.pop("gross"), a.pop("receipts"), alternates=a.pop("alternates", None),
                       rule=a.pop("rule", "MOQ_MULTIPLE"))
        actual = {"planned_receipts": {i: q for i, q in enumerate(res["planned_receipts"]) if q},
                  "planned_releases": dict(res["planned_releases"]),
                  "messages": sorted({m["message"] for m in res["messages"]}),
                  "alt": {i: q for i, q in enumerate(res["alt"]) if q}, "first_short": res["first_short"],
                  "projected_min": min(res["projected"])}
        exp = case["expect"]
        ok = True
        for k, v in exp.items():
            if k == "messages":
                ok &= set(v) <= set(actual["messages"])
            else:
                ok &= actual[k] == v
        out.append((case["id"], case["why"], exp, {k: actual[k] for k in exp}, ok))
    return out


# ---------------------------------------------------------------- suites

def _cm_mes(conn):
    cases = []
    for g in conn.execute("SELECT case_id, expected_json FROM eval_golden WHERE suite_id='EV-CM-MES'"):
        exp = json.loads(g["expected_json"])
        sn = g["case_id"]
        ev = conn.execute("""SELECT COUNT(*) n, MIN(event_ts) first, GROUP_CONCAT(DISTINCT s.code) codes
                             FROM station_event se JOIN station s USING(station_id) WHERE se.serial=?""", (sn,)).fetchone()
        pos = {r["position"]: r["child_serial"] for r in conn.execute(
            "SELECT position, child_serial FROM genealogy WHERE parent_serial=? AND removed_at IS NULL AND child_serial IS NOT NULL",
            (sn,))}
        built = conn.execute("SELECT built_at FROM unit WHERE serial=?", (sn,)).fetchone()
        act = {"events": ev["n"], "stations": sorted((ev["codes"] or "").split(",")) if ev["codes"] else [],
               "first_ts": ev["first"], "frame": pos.get("FRAME"), "du": pos.get("DRIVE_UNIT"), "hmi": pos.get("HMI"),
               "pu": pos.get("PEDAL_UNIT"), "built": bool(built and built["built_at"])}
        diff = [k for k in exp if exp[k] != act.get(k)]
        cases.append((sn, json.dumps(exp), json.dumps(act), not diff, "mismatch: " + ", ".join(diff) if diff else None))
    return cases, {}


def _genealogy(conn):
    from .genealogy import forward
    cases = []
    for g in conn.execute("SELECT case_id, expected_json FROM eval_golden WHERE suite_id='EV-GENEALOGY'"):
        exp = json.loads(g["expected_json"])
        ref = g["case_id"].split(":", 1)[1]
        units = forward(conn, ref)
        if exp["kind"] == "lot->packs":
            act = sorted(u["serial"] for u in units if u["kind"] == "PACK")
        else:
            act = sorted(u["serial"] for u in units if u["kind"] == "VEHICLE")
        ok = act == sorted(exp["expected"])
        missing = sorted(set(exp["expected"]) - set(act))
        extra = sorted(set(act) - set(exp["expected"]))
        note = None if ok else f"missing {missing[:3]}{'...' if len(missing) > 3 else ''}; extra {extra[:3]}"
        cases.append((g["case_id"], json.dumps(exp["expected"][:10]), json.dumps(act[:10]), ok, note))
    return cases, {}


def _warranty(conn):
    cases, confusion = [], {}
    for g in conn.execute("SELECT case_id, expected_json FROM eval_golden WHERE suite_id='EV-WARRANTY-CLS'"):
        exp = json.loads(g["expected_json"])["defect_code"]
        row = conn.execute("SELECT defect_code, symptom FROM warranty_claim WHERE claim_id=?",
                           ("WC-" + g["case_id"].split("-")[1],)).fetchone()
        act = row["defect_code"] if row else None
        confusion[f"{exp}->{act}"] = confusion.get(f"{exp}->{act}", 0) + 1
        cases.append((g["case_id"], exp, act, exp == act, row["symptom"] if row and exp != act else None))
    return cases, {"confusion": confusion}


def _promise(conn):
    cases = []
    commits = correct = expected_commits = abstain_ok = abstain_total = 0
    for g in conn.execute("SELECT case_id, input_ref, expected_json FROM eval_golden WHERE suite_id='EV-PROMISE-PARSE'"):
        exp = json.loads(g["expected_json"])
        kind, rest = g["case_id"].split(":", 1)
        if kind == "xlsx":
            rid, pol = rest.split(":")
            po, line = pol.split("-")
            att = conn.execute("SELECT attachment_id FROM raw_attachment WHERE raw_id=?", (int(rid),)).fetchone()
            row = conn.execute("SELECT promise_date, promise_qty FROM po_promise_history WHERE raw_ref=? AND po_id=? AND line_no=?",
                               (f"raw_attachment:{att['attachment_id']}" if att else "-", po, int(line))).fetchone()
        else:
            row = conn.execute("SELECT po_id, line_no, promise_date, promise_qty FROM po_promise_history WHERE raw_ref=?",
                               (g["input_ref"],)).fetchone()
        if exp.get("abstain"):
            abstain_total += 1
            ok = row is None
            abstain_ok += ok
            cases.append((g["case_id"], "ABSTAIN", "ABSTAIN" if row is None else row["promise_date"], ok,
                          None if ok else "wrote a date where it should have abstained"))
            if row is not None:
                commits += 1
            continue
        expected_commits += 1
        if row is None:
            cases.append((g["case_id"], exp["promise"], "ABSTAIN", False, "abstained (coverage miss, not an error)"))
            continue
        commits += 1
        ok = row["promise_date"] == exp["promise"]
        correct += ok
        cases.append((g["case_id"], exp["promise"], row["promise_date"], ok, None if ok else "wrong date written"))
    precision = correct / commits if commits else 1.0
    coverage = correct / expected_commits if expected_commits else 1.0
    return cases, {"precision": round(precision, 4), "coverage": round(coverage, 4),
                   "abstain_correct": f"{abstain_ok}/{abstain_total}", "score_is": "precision; gate also needs coverage >= 0.80"}


def _atp_backtest(conn):
    cases = []
    for o in conn.execute("""SELECT order_id, first_promised_date, delivered_at FROM customer_order WHERE status='DELIVERED'
                             AND delivered_at >= strftime('%Y-%m-%dT%H:%M:%SZ', (SELECT value FROM meta WHERE key='now_utc'),
                             '-60 days')"""):
        ok = o["delivered_at"][:10] <= o["first_promised_date"]
        cases.append((o["order_id"], o["first_promised_date"], o["delivered_at"][:10], ok, None if ok else "late"))
    return cases, {}


def _mrp_textbook(conn):
    return [(cid, json.dumps(exp, default=str), json.dumps(act, default=str), ok, why)
            for cid, why, exp, act, ok in textbook_results()], {}


SUITE_FN = {"EV-CM-MES": _cm_mes, "EV-GENEALOGY": _genealogy, "EV-WARRANTY-CLS": _warranty,
            "EV-PROMISE-PARSE": _promise, "EV-ATP-BACKTEST": _atp_backtest, "EV-MRP-TEXTBOOK": _mrp_textbook}


def compare_with_last(conn, suite_id, ran_at, score):
    """The suite's score on its previous run (by time, then run id) and whether `score` fell below it.
    A drop counts even when both runs pass, so a regression can't hide behind a passing gate."""
    prev = conn.execute("SELECT score FROM eval_run WHERE suite_id=? AND ran_at<=? ORDER BY ran_at DESC, run_id DESC"
                        " LIMIT 1", (suite_id, ran_at)).fetchone()
    prev_score = prev["score"] if prev else None
    return prev_score, 1 if prev_score is not None and round(score, 4) < prev_score else 0


def run_suite(conn, suite_id):
    suite = conn.execute("SELECT * FROM eval_suite WHERE suite_id=?", (suite_id,)).fetchone()
    cases, metrics = SUITE_FN[suite_id](conn)
    passed = sum(1 for c in cases if c[3])
    score = passed / len(cases) if cases else 1.0
    if suite_id == "EV-PROMISE-PARSE":
        score = metrics["precision"]
        gate = "PASS" if (metrics["precision"] >= suite["threshold"] and metrics["coverage"] >= 0.80) else "FAIL"
    else:
        gate = "PASS" if score >= suite["threshold"] else "FAIL"
    metrics.update({"cases": len(cases), "passed": passed})
    ran_at = get_now(conn)
    prev_score, regressed = compare_with_last(conn, suite_id, ran_at, score)
    cur = conn.execute("INSERT INTO eval_run(suite_id, ran_at, subject_version, cases, passed, score, gate, prev_score,"
                       " regressed, metrics_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (suite_id, ran_at, VERSIONS[suite_id](conn), len(cases), passed, round(score, 4), gate, prev_score,
                        regressed, json.dumps(metrics)))
    run_id = cur.lastrowid
    conn.executemany("INSERT INTO eval_case VALUES (?,?,?,?,?,?,?)",
                     [(run_id, c[0], None, c[1], c[2], 1 if c[3] else 0, c[4]) for c in cases])
    return {"run_id": run_id, "suite_id": suite_id, "score": score, "gate": gate, "prev_score": prev_score,
            "regressed": bool(regressed), "cases": len(cases), "passed": passed, "metrics": metrics}


def run_all(conn):
    return [run_suite(conn, s) for s in SUITE_FN]
