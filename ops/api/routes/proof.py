"""Tests · Evals · Review: the four gates that keep agent-written logic honest."""
import json
import os
import subprocess
import sys
import tempfile
import time

from ops import config
from ops.api.router import HttpError, get, post
from ops.db import now, q, q1, val


@get(r"^/api/proof$")
def overview(req):
    c = req.conn
    suites = q(c, "SELECT * FROM eval_suite ORDER BY suite_id")
    out = []
    for s in suites:
        runs = q(c, "SELECT run_id, ran_at, subject_version, cases, passed, score, gate, prev_score, regressed, metrics_json"
                    " FROM eval_run WHERE suite_id=? ORDER BY ran_at, run_id", (s["suite_id"],))
        latest = runs[-1] if runs else None
        out.append({**s, "latest": latest, "history": [{"ran_at": r["ran_at"], "score": r["score"], "gate": r["gate"],
                                                        "regressed": r["regressed"], "version": r["subject_version"]}
                                                       for r in runs],
                    "metrics": json.loads(latest["metrics_json"]) if latest and latest["metrics_json"] else {}})
    tests = q(c, "SELECT * FROM test_run ORDER BY run_id DESC LIMIT 12")
    latest_test = tests[0] if tests else None
    contracts = q(c, """SELECT d.contract_id, d.name, d.severity, d.layer, r.violations, r.ran_at FROM data_contract d
                        LEFT JOIN contract_run r ON r.run_id = (SELECT MAX(run_id) FROM contract_run x WHERE x.contract_id=d.contract_id)
                        ORDER BY r.violations DESC, d.contract_id""")
    reviews = q(c, """SELECT cr.*, er.suite_id, er.score AS eval_score, er.gate AS eval_gate,
                             tr.tests, tr.failures AS test_failures FROM change_review cr
                      LEFT JOIN eval_run er ON er.run_id = cr.eval_run_id LEFT JOIN test_run tr ON tr.run_id = cr.test_run_id
                      ORDER BY cr.proposed_at DESC""")
    return {"suites": out, "tests": tests, "latest_test": latest_test, "contracts": contracts, "reviews": reviews,
            "summary": {"evals_pass": sum(1 for s in out if s["latest"] and s["latest"]["gate"] == "PASS"),
                        "evals_total": len(out),
                        "evals_regressed": sum(1 for s in out if s["latest"] and s["latest"]["regressed"]),
                        "contracts_failing": sum(1 for x in contracts if (x["violations"] or 0) > 0),
                        "contracts_total": len(contracts),
                        "reviews_open": sum(1 for r in reviews if r["status"] == "OPEN"),
                        "reviews_deployed": sum(1 for r in reviews if r["status"] == "DEPLOYED")}}


@get(r"^/api/proof/eval/(\d+)$")
def eval_cases(req):
    c = req.conn
    run = q1(c, "SELECT r.*, s.name, s.description, s.metric, s.threshold, s.target, s.method FROM eval_run r"
                " JOIN eval_suite s USING(suite_id) WHERE run_id=?", (int(req.params[0]),))
    if not run:
        raise HttpError(404, "no such eval run")
    cases = q(c, "SELECT case_id, expected, actual, passed, note FROM eval_case WHERE run_id=? ORDER BY passed, case_id LIMIT 400",
              (run["run_id"],))
    return {"run": run, "cases": cases}


@post(r"^/api/proof/run-evals$")
def run_evals(req):
    from ops.logic import evals
    return {"runs": evals.run_all(req.conn)}


@post(r"^/api/proof/run-contracts$")
def run_contracts(req):
    from ops.logic import contracts
    res = contracts.run_all(req.conn, store=True)
    return {"runs": [{"contract_id": r["contract_id"], "violations": r["violations"], "ms": r["ms"]} for r in res]}


@post(r"^/api/proof/run-tests$")
def run_tests(req):
    """Run the unit + integration suite in a subprocess (against a scratch database) and record it."""
    t0 = time.time()
    env = dict(os.environ)
    with tempfile.TemporaryDirectory() as tmp:
        env["OPS_DB"] = os.path.join(tmp, "test.db")
        env["OPS_TEST_REPORT"] = os.path.join(tmp, "report.json")
        try:
            proc = subprocess.run([sys.executable, "-m", "ops.proof", "--json", env["OPS_TEST_REPORT"], "--no-record"],
                                  cwd=str(config.ROOT), env=env, capture_output=True, text=True, timeout=240)
        except subprocess.TimeoutExpired:
            raise HttpError(504, "test run timed out")
        try:
            report = json.loads(open(env["OPS_TEST_REPORT"]).read())
        except (OSError, ValueError):
            raise HttpError(500, "test runner produced no report: " + (proc.stderr or proc.stdout)[-400:])
    c = req.conn
    cur = c.execute("INSERT INTO test_run(ran_at, suite, tests, failures, errors, skipped, duration_s, detail_json)"
                    " VALUES (?,?,?,?,?,?,?,?)", (now(c), "unit+integration", report["tests"], report["failures"],
                                                  report["errors"], report["skipped"], round(time.time() - t0, 2),
                                                  json.dumps(report["results"])))
    return {"run_id": cur.lastrowid, **{k: report[k] for k in ("tests", "failures", "errors", "skipped")},
            "duration_s": round(time.time() - t0, 2)}


@get(r"^/api/proof/test/(\d+)$")
def test_detail(req):
    r = q1(req.conn, "SELECT * FROM test_run WHERE run_id=?", (int(req.params[0]),))
    if not r:
        raise HttpError(404, "no such test run")
    r = dict(r)
    try:
        r["results"] = json.loads(r.pop("detail_json") or "[]")
    except ValueError:
        r["results"] = []
    return r
