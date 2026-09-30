"""Run the test suite and record it in test_run.

    python3 -m ops.proof                  # run tests, record the result in the live database
    python3 -m ops.proof --json out.json  # also write a machine-readable report
    python3 -m ops.proof --no-record      # do not touch the database (used by the API's scratch runs)
"""
import argparse
import json
import sys
import time
import unittest

from . import config


class Collect(unittest.TextTestResult):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.records = []
        self._t0 = {}

    def startTest(self, test):
        self._t0[test.id()] = time.perf_counter()
        super().startTest(test)

    def _rec(self, test, status, err=None):
        ms = round((time.perf_counter() - self._t0.get(test.id(), time.perf_counter())) * 1000, 1)
        msg = None
        if err is not None:
            msg = self._exc_info_to_string(err, test).strip().splitlines()[-1][:300]
        doc = (test.shortDescription() or "").strip()
        self.records.append({"test": test.id().replace("tests.", ""), "status": status, "ms": ms, "doc": doc, "message": msg})

    def addSuccess(self, test):
        super().addSuccess(test)
        self._rec(test, "pass")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._rec(test, "fail", err)

    def addError(self, test, err):
        super().addError(test, err)
        self._rec(test, "error", err)

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.records.append({"test": test.id().replace("tests.", ""), "status": "skip", "ms": 0, "doc": "", "message": reason})


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("--no-record", action="store_true")
    ap.add_argument("-k", help="only tests whose id contains this")
    args = ap.parse_args(argv)
    target = config.DB_PATH          # fixed before the tests run: a test may point config at a scratch copy
    suite = unittest.TestLoader().discover(str(config.ROOT / "tests"), top_level_dir=str(config.ROOT))
    if args.k:
        def flat(s):
            for t in s:
                if isinstance(t, unittest.TestSuite):
                    yield from flat(t)
                else:
                    yield t
        suite = unittest.TestSuite([t for t in flat(suite) if args.k in t.id()])
    t0 = time.time()
    res = unittest.TextTestRunner(resultclass=Collect, verbosity=2).run(suite)
    report = {"tests": res.testsRun, "failures": len(res.failures), "errors": len(res.errors),
              "skipped": len(res.skipped), "duration_s": round(time.time() - t0, 2), "results": res.records}
    if args.json:
        with open(args.json, "w") as f:
            json.dump(report, f)
    if not args.no_record and target.exists():
        from .db import connect, now
        conn = connect(target)
        conn.execute("INSERT INTO test_run(ran_at, suite, tests, failures, errors, skipped, duration_s, detail_json)"
                     " VALUES (?,?,?,?,?,?,?,?)", (now(conn), "unit+integration", report["tests"], report["failures"],
                                                   report["errors"], report["skipped"], report["duration_s"],
                                                   json.dumps(report["results"])))
        conn.commit()
        conn.close()
    return 0 if res.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
