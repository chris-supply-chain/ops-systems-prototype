"""The check on the checks: every eval run is compared with the suite's previous run, so a fall is caught even
while the score still clears its threshold and the gate passes."""
import unittest

from ops.db import connect
from ops.logic import evals
from tests.fixtures import copy_of_built, mini_db


class EvalRegression(unittest.TestCase):
    def test_a_run_is_compared_with_the_latest_earlier_run(self):
        conn = mini_db()
        conn.execute("INSERT INTO eval_suite VALUES ('EV-X', 'x', 'ops.x', 'LABELED_SET', 'accuracy', 0.9, 'x')")
        add = lambda at, score: conn.execute("INSERT INTO eval_run(suite_id, ran_at, subject_version, cases, passed, score,"
                                             " gate) VALUES ('EV-X', ?, 'v', 100, 0, ?, 'PASS')", (at, score))
        self.assertEqual(evals.compare_with_last(conn, "EV-X", "2026-09-01T00:00:00Z", 0.95), (None, 0))   # first run
        add("2026-09-01T00:00:00Z", 0.95)
        add("2026-09-20T00:00:00Z", 0.99)          # later, so it is not the previous run of a run on Sep 10
        self.assertEqual(evals.compare_with_last(conn, "EV-X", "2026-09-10T00:00:00Z", 0.93), (0.95, 1))   # fell, still passing
        self.assertEqual(evals.compare_with_last(conn, "EV-X", "2026-09-10T00:00:00Z", 0.95), (0.95, 0))   # level is not a fall
        self.assertEqual(evals.compare_with_last(conn, "EV-X", "2026-09-26T00:00:00Z", 0.97), (0.99, 1))   # vs the latest

    def test_a_falling_eval_is_flagged_even_while_it_passes(self):
        conn = connect(copy_of_built())
        for suite in ("EV-WARRANTY-CLS", "EV-ATP-BACKTEST"):          # scored on the data, so below 100%
            first = evals.run_suite(conn, suite)
            if first["gate"] == "PASS" and first["score"] < 1:
                break
        else:
            self.skipTest("no passing suite below 100% on this dataset date")
        self.assertFalse(evals.run_suite(conn, suite)["regressed"])  # same data, same score: level
        conn.execute("UPDATE eval_run SET score = MIN(1.0, score + 0.01) WHERE run_id ="
                     " (SELECT MAX(run_id) FROM eval_run WHERE suite_id=?)", (suite,))   # as if the last run scored higher
        again = evals.run_suite(conn, suite)
        self.assertTrue(again["regressed"])
        self.assertEqual(again["gate"], "PASS")
        row = conn.execute("SELECT prev_score, regressed FROM eval_run WHERE run_id=?", (again["run_id"],)).fetchone()
        self.assertEqual(row["regressed"], 1)
        self.assertGreater(row["prev_score"], again["score"])


if __name__ == "__main__":
    unittest.main()
