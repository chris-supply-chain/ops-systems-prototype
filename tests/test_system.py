"""End to end: build the whole simulated world, then check the invariants the platform promises,
run every closed loop and prove the gates pass afterwards."""
import json
import threading
import unittest
import urllib.request

from ops import config
from ops.db import connect
from ops.logic import contracts, decisions, evals
from tests.fixtures import AS_OF, built_db, copy_of_built, temp_dir

EXPECTED_STORY_VIOLATIONS = {"C-GEN-02", "C-GEN-05", "C-FEED-01", "C-SRC-01", "C-SRC-03", "C-MOV-01", "C-MOV-02", "C-INV-01",
                             "C-QUA-03"}


def row_key_breaks(conn):
    """Tables and views with no row key, and row keys two rows share (a key the database enforces passes for free)."""
    from ops.api.routes.sandbox import key_sql, row_key
    out = []
    for o in conn.execute("SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%'"
                          " ORDER BY name").fetchall():
        k = row_key(conn, o["name"], o["type"])
        if k is None:
            out.append((o["name"], "no row key"))
            continue
        cols = ", ".join(key_sql(c) for c in k["columns"])
        dup = conn.execute(f'SELECT {cols}, COUNT(*) AS n FROM "{o["name"]}" GROUP BY {cols} HAVING COUNT(*) > 1 LIMIT 1').fetchone()
        if dup:
            out.append((o["name"], dict(dup)))
    return out


class BuiltWorld(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = connect(built_db(), readonly=True)

    def test_foreign_keys_hold_everywhere(self):
        """PRAGMA foreign_key_check over all 90+ tables returns nothing."""
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_every_landed_message_reached_a_terminal_state(self):
        self.assertEqual(contracts.run_one(self.conn, "C-FEED-02")["violations"], 0)

    def test_only_the_planted_problems_violate_contracts(self):
        """Clean data except the storylines the demo is about (and each of those is actually detected)."""
        failing = {r["contract_id"] for r in contracts.run_all(self.conn, store=False) if r["violations"]}
        self.assertEqual(failing, EXPECTED_STORY_VIOLATIONS)

    def test_every_planned_story_raises_its_exception(self):
        rules = {r["rule_id"] for r in self.conn.execute("SELECT rule_id FROM ops_exception WHERE status!='RESOLVED'")}
        for rule in ("QUALITY-LOT-CLUSTER", "PLAN-LINE-STOP", "MOVE-ETA-SLIP", "MOVE-SHORT-RECEIPT", "DATA-QUARANTINE",
                     "PROMISE-AT-RISK", "MOVE-CUSTOMS-EXAM", "DATA-PROVISIONAL", "QUALITY-DEVIATION",
                     "QUALITY-DEVIATION-OVERRUN"):
            self.assertIn(rule, rules)

    def test_each_eval_run_is_compared_with_the_one_before(self):
        """prev_score is the suite's previous run and regressed marks a fall, for seeded history and live runs alike. The
        CM feed's cutover morning is one of the falls."""
        last = {}
        for r in self.conn.execute("SELECT suite_id, score, prev_score, regressed FROM eval_run ORDER BY suite_id, ran_at, run_id"):
            prev = last.get(r["suite_id"])
            self.assertEqual(r["prev_score"], prev)
            self.assertEqual(r["regressed"], int(prev is not None and r["score"] < prev))
            last[r["suite_id"]] = r["score"]
        self.assertGreater(self.conn.execute("SELECT COUNT(*) n FROM eval_run WHERE suite_id='EV-CM-MES' AND regressed=1")
                           .fetchone()["n"], 0)

    def test_every_proposed_decision_points_at_the_exception_it_answers(self):
        """Linked by the exception's id, so DATA's decision (about station S65) finds its exception (about the feed)."""
        for d in self.conn.execute("SELECT decision_id FROM decision_log WHERE status='PROPOSED'").fetchall():
            n = self.conn.execute("SELECT COUNT(*) n FROM ops_exception WHERE decision_id=?", (d["decision_id"],)).fetchone()["n"]
            self.assertEqual(n, 1, d["decision_id"])

    def test_one_proposed_decision_per_loop(self):
        loops = [r["loop"] for r in self.conn.execute("SELECT loop FROM decision_log WHERE status='PROPOSED'")]
        self.assertEqual(sorted(loops), ["DATA", "PROMISE", "QUALITY", "SUPPLY"])

    def test_same_seed_same_data(self):
        """Two builds in fresh processes (string-hash seeds 1 and 2, a later wall clock) match this one row for row, so
        a demo rebuilt tomorrow shows the same serials, lots and dollars. Hash seeds 1 and 2 order small string sets
        differently, which is what exposes a set iterated into the data."""
        import os
        import subprocess
        import sys
        from pathlib import Path
        paths, procs = [], []
        for hash_seed in ("1", "2"):
            paths.append(Path(temp_dir("opsos-det-")) / "ops.db")
            procs.append(subprocess.Popen(
                [sys.executable, "app.py", "--generate-only", "--reset", "--as-of", AS_OF, "--seed", "7"],
                cwd=Path(__file__).resolve().parent.parent, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                env=dict(os.environ, PYTHONHASHSEED=hash_seed, OPS_DB=str(paths[-1]))))
        for p in procs:
            self.assertEqual(p.wait(timeout=300), 0, p.stderr.read().decode()[-2000:])
            p.stderr.close()
        others = [connect(p, readonly=True) for p in paths]
        tables = [r["name"] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != 'meta'")]
        self.assertGreater(len(tables), 80)
        for t in tables:
            cols = ", ".join(r["name"] for r in self.conn.execute(f"PRAGMA table_info({t})")
                             if r["name"] not in ("duration_ms", "duration_s"))
            mine = [tuple(r.values()) for r in self.conn.execute(f"SELECT {cols} FROM {t}")]
            for other in others:
                theirs = [tuple(r.values()) for r in other.execute(f"SELECT {cols} FROM {t}")]
                self.assertTrue(mine == theirs, f"{t} differs between two builds with the same seed")

    def test_data_dictionary_and_use_cases(self):
        """Every table and view says what one row is, and every Sandbox use case walks real tables with a query that
        answers something inside the Sandbox's 3-second limit. The genealogy grain's kit size is what the data holds."""
        import statistics
        import time
        from ops.api.routes.sandbox_guide import GRAIN, USE_CASES
        names = {r["name"] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%'")}
        self.assertEqual(sorted(names - set(GRAIN)), [])          # a new table needs its grain
        self.assertEqual(sorted(set(GRAIN) - names), [])          # and a dropped one loses it
        kits = self.conn.execute("""
            WITH RECURSIVE tree(root, serial, depth) AS (         -- each vehicle shipped with one pack, down its whole tree
              SELECT parent_serial, parent_serial, 0 FROM genealogy WHERE relation = 'SHIPPED_WITH' AND removed_at IS NULL
              GROUP BY parent_serial HAVING COUNT(*) = 1
              UNION ALL
              SELECT t.root, g.child_serial, t.depth + 1 FROM genealogy g JOIN tree t ON g.parent_serial = t.serial
              WHERE g.child_serial IS NOT NULL AND g.removed_at IS NULL)
            SELECT COUNT(*) AS n_rows, MAX(t.depth) + 1 AS levels
            FROM tree t JOIN genealogy g ON g.parent_serial = t.serial AND g.removed_at IS NULL GROUP BY t.root""")
        self.assertEqual(statistics.mode((k["n_rows"], k["levels"]) for k in kits), (19, 3))   # "... is 19 rows, 3 levels deep"
        self.assertIn("19 rows, 3 levels deep", GRAIN["genealogy"])
        for u in USE_CASES:
            for table, _, _ in u["path"]:
                self.assertIn(table, names, u["id"])
            t0 = time.perf_counter()
            rows = self.conn.execute(u["sql"]).fetchall()
            self.assertTrue(rows, f"{u['id']} returns nothing")
            self.assertLess(time.perf_counter() - t0, 2.0, u["id"])

    def test_every_table_names_a_row_key_that_holds(self):
        """Every table and view says which columns make a row unique (the row number only where each delivery, run or
        use counts on its own), and no two rows share one. Declarations name real tables and never hide behind a
        primary key or UNIQUE constraint that already says it."""
        from ops.api.routes.sandbox import row_key
        from ops.api.routes.sandbox_guide import NUMBERED, ROW_KEY
        self.assertEqual(row_key_breaks(self.conn), [])
        kinds = {r["name"]: r["type"] for r in self.conn.execute("SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view')")}
        for name in list(ROW_KEY) + list(NUMBERED):
            self.assertIn(name, kinds)
            self.assertIn(row_key(self.conn, name, kinds[name])["how"], ("PARTIAL_INDEX", "TESTED", "ROW_NUMBER"), name)
        self.assertEqual(row_key(self.conn, "genealogy", "table")["how"], "PARTIAL_INDEX")

    def test_installed_lot_quantities_match_the_bom(self):
        """Every lot-tracked position holds exactly its BOM quantity (40 or 60 cells, 2 tires, 0.42 kg of magnet), including
        packs whose cells straddle two lots and lost a cell at P10."""
        rows = self.conn.execute("""
            WITH got AS (SELECT g.parent_serial, u.item_id AS parent_item, g.position, SUM(g.qty) AS qty
                         FROM genealogy g JOIN unit u ON u.serial = g.parent_serial
                         WHERE g.child_lot_id IS NOT NULL AND g.removed_at IS NULL GROUP BY 1, 2, 3)
            SELECT got.*, (SELECT MAX(b.qty_per) FROM bom_line b WHERE b.parent_item_id = got.parent_item
                           AND b.position = got.position) AS bom_qty FROM got""").fetchall()
        self.assertGreater(len(rows), 1000)
        self.assertEqual([dict(r) for r in rows if r["bom_qty"] is None or abs(r["qty"] - r["bom_qty"]) > 1e-9], [])
        splits = self.conn.execute("""SELECT COUNT(*) AS n FROM (SELECT parent_serial FROM genealogy WHERE position = 'CELLS'
                                      AND removed_at IS NULL GROUP BY 1 HAVING COUNT(*) > 1)""").fetchone()["n"]
        self.assertGreater(splits, 0)                    # the straddling case is actually exercised

    def test_only_the_bad_batch_is_flagged(self):
        """Every batch has some background claims; only the one far above the fleet's rate per month in service
        becomes an exception, so a few claims on an old batch never trigger a containment."""
        from ops.logic.exceptions import BASELINE_FLOOR, SIGNAL_RATIO
        from ops.logic.genealogy import batch_signal
        flagged = [r["ref_id"] for r in self.conn.execute("SELECT ref_id FROM ops_exception WHERE rule_id='QUALITY-LOT-CLUSTER'")]
        self.assertEqual(len(flagged), 1)
        mine, rest = batch_signal(self.conn, flagged[0])
        self.assertGreater(rest["claims"], 0)          # a real baseline, not a suspicious zero
        self.assertGreaterEqual(mine["rate"], SIGNAL_RATIO * max(rest["rate"], BASELINE_FLOOR))

    def test_every_exception_names_what_it_counts(self):
        """'7' on the Control Tower must read as 7 messages or 7 orders; a new rule has to declare its noun."""
        from ops.logic.exceptions import IMPACT_UNIT
        for r in self.conn.execute("SELECT rule_id, impact_units, impact_unit FROM ops_exception WHERE impact_units IS NOT NULL"):
            self.assertIn(r["rule_id"], IMPACT_UNIT)
            self.assertEqual(r["impact_unit"], IMPACT_UNIT[r["rule_id"]])

    def test_launcher_rebuilds_a_database_from_an_older_schema(self):
        """A database stamped with another schema version is rebuilt with its own date and seed, not served broken."""
        import sqlite3
        import app
        path = copy_of_built()
        self.assertIsNone(app._stale(path))
        c = sqlite3.connect(str(path))
        c.execute("UPDATE meta SET value='0.9' WHERE key='schema_version'")
        c.commit()
        c.close()
        self.assertEqual(app._stale(path), (AS_OF, 7))

    def test_eval_run_counts_agree_with_scores(self):
        """History and live runs alike: passed <= cases, and the pass rate is the score to within rounding."""
        for r in self.conn.execute("SELECT run_id, suite_id, cases, passed, score FROM eval_run"):
            self.assertLessEqual(r["passed"], r["cases"], r)
            self.assertAlmostEqual(r["passed"] / r["cases"], r["score"], delta=0.02, msg=dict(r))

    def test_timestamps_are_canonical_utc(self):
        """Every stored instant is 'YYYY-MM-DDTHH:MM:SSZ' so string comparison is time comparison, across all sources."""
        import re
        pat = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        checks = [("station_event", "event_ts"), ("genealogy", "installed_at"), ("lot", "received_at"),
                  ("customer_order", "ordered_at"), ("raw_cm_mes_event", "received_at"), ("raw_email", "received_at"),
                  ("shipment_event", "event_ts"), ("goods_receipt", "received_at"), ("quality_event", "detected_at"),
                  ("warranty_claim", "reported_at"), ("process_event", "ts"), ("unit", "built_at")]
        for table, col in checks:
            bad = [r[col] for r in self.conn.execute(f"SELECT {col} FROM {table} WHERE {col} IS NOT NULL LIMIT 20000")
                   if not pat.match(r[col])]
            self.assertEqual(bad[:3], [], f"{table}.{col}")

    def test_genealogy_is_complete_for_built_vehicles_and_shipped_packs(self):
        self.assertEqual(contracts.run_one(self.conn, "C-GEN-01")["violations"], 0)
        self.assertEqual(contracts.run_one(self.conn, "C-GEN-04")["violations"], 0)

    def test_inventory_flows_balance(self):
        """Every received lot's stock + consumption never exceeds what was received."""
        rows = self.conn.execute("""SELECT l.lot_id, l.qty_received, COALESCE(SUM(g.qty),0) used
                                    FROM lot l LEFT JOIN genealogy g ON g.child_lot_id = l.lot_id
                                    WHERE l.origin='OEM_RECEIPT' GROUP BY l.lot_id HAVING used > l.qty_received""").fetchall()
        self.assertEqual(rows, [])

    def test_mrp_and_textbook_evals_pass(self):
        runs = {r["suite_id"]: r["gate"] for r in self.conn.execute(
            "SELECT suite_id, gate FROM eval_run WHERE run_id IN (SELECT MAX(run_id) FROM eval_run GROUP BY suite_id)")}
        for s in ("EV-MRP-TEXTBOOK", "EV-PROMISE-PARSE", "EV-WARRANTY-CLS", "EV-ATP-BACKTEST"):
            self.assertEqual(runs[s], "PASS", s)
        self.assertEqual(runs["EV-CM-MES"], "FAIL")        # until the S65 mapping is fixed by the data loop


class ClosedLoops(unittest.TestCase):
    """Execute every proposed decision on a copy and prove each loop closes."""

    @classmethod
    def setUpClass(cls):
        cls.path = copy_of_built()
        cls.conn = connect(cls.path)
        cls.conn.execute("INSERT INTO test_run(ran_at, suite, tests, failures, errors, skipped, duration_s)"
                         " VALUES ('2026-09-26T15:00:00Z','unit+integration',1,0,0,0,1)")
        cls.out = {}
        for d in cls.conn.execute("SELECT decision_id, loop FROM decision_log WHERE status='PROPOSED' ORDER BY decision_id").fetchall():
            cls.out[d["loop"]] = decisions.execute(cls.conn, d["decision_id"], actor="test")
        cls.conn.commit()

    def test_no_slot_or_part_is_doubled_after_the_loops(self):
        """D-0107 replays the S65 rework swaps: each closes the drive unit it replaces, so no slot ends up with two and
        no drive unit is in two vehicles."""
        swapped = self.conn.execute("SELECT COUNT(*) AS n FROM genealogy WHERE removal_reason LIKE 'Removed at S65%'").fetchone()["n"]
        self.assertGreater(swapped, 0)
        for contract_id in ("C-GEN-03", "C-GEN-06"):
            self.assertEqual(contracts.run_one(self.conn, contract_id)["violations"], 0, contract_id)

    def test_row_keys_hold_after_every_loop(self):
        """The loops write at the dataset's frozen 'now' (re-promises, messages, replayed CM events, contract runs), and
        every row key still holds afterwards."""
        self.assertEqual(row_key_breaks(self.conn), [])

    def test_containment_holds_units_and_drafts_recovery(self):
        o = self.out["QUALITY"]
        self.assertEqual(o["status"], "EXECUTED")
        self.assertGreater(o["writes"]["hold"], 100)
        self.assertEqual(o["writes"]["chargeback"], 1)
        self.assertEqual(contracts.run_one(self.conn, "C-QUA-01")["violations"], 0)

    def test_containment_does_what_the_proposal_said(self):
        """The numbers a reviewer approved are the numbers executed: serial holds, lot holds, units flagged."""
        impact = json.loads(self.conn.execute("SELECT impact_json FROM decision_log WHERE decision_id=?",
                                              (self.out["QUALITY"]["decision_id"],)).fetchone()["impact_json"])
        writes = self.out["QUALITY"]["writes"]
        self.assertEqual(impact["serial_holds"], writes["unit"])
        self.assertEqual(impact["serial_holds"] + impact["lot_holds"], writes["hold"])
        self.assertEqual(impact["serial_holds"], impact["hold_units"] + impact["kit_companions"])

    def test_recovery_estimate_is_the_chargeback_drafted(self):
        """The dollar figure on the proposal is the chargeback the execution drafts, priced by the same contract terms,
        and a rejected (not warrantable) claim is never on it."""
        did = self.out["QUALITY"]["decision_id"]
        impact = json.loads(self.conn.execute("SELECT impact_json FROM decision_log WHERE decision_id=?",
                                              (did,)).fetchone()["impact_json"])
        drafted = self.conn.execute("SELECT amount_usd FROM chargeback WHERE decision_id=?", (did,)).fetchone()["amount_usd"]
        self.assertAlmostEqual(impact["recovery_estimate_usd"], drafted, places=2)
        self.assertEqual(contracts.run_one(self.conn, "C-FIN-03")["violations"], 0)

    def test_expedite_closes_the_line_stop(self):
        self.assertEqual(self.out["SUPPLY"]["result"], "gap closed")

    def test_mapping_fix_passes_every_gate_and_heals_as_built(self):
        o = self.out["DATA"]
        self.assertTrue(o["ok"], o.get("gates"))
        self.assertIn("no_eval_regressed", o["gates"])
        self.assertEqual(o["c_gen_05_after"], 0)
        self.assertEqual(evals.run_suite(self.conn, "EV-CM-MES")["gate"], "PASS")
        self.assertEqual(evals.run_suite(self.conn, "EV-GENEALOGY")["gate"], "PASS")

    def test_proof_page_reflects_the_actions(self):
        """Contracts re-run and store after every action, and the mapping fix re-scores the genealogy eval it repairs, so
        the stored results the Proof page shows are the state after the actions, with no button pressed."""
        latest = lambda cid: self.conn.execute("SELECT violations FROM contract_run WHERE contract_id=?"
                                               " ORDER BY run_id DESC LIMIT 1", (cid,)).fetchone()["violations"]
        self.assertEqual(latest("C-GEN-05"), 0)
        self.assertEqual(latest("C-FEED-01"), 0)
        self.assertTrue(self.out["DATA"]["gates"]["eval_EV-GENEALOGY"])

    def test_repromise_clears_promises_at_risk(self):
        self.assertGreater(self.out["PROMISE"]["orders"], 0)
        from ops.logic.promises import at_risk
        self.assertEqual(at_risk(self.conn), [])

    def test_each_decision_records_what_it_achieved(self):
        """After each loop runs, every figure it promised is counted again from the data. Containment, expedite and the
        mapping fix achieve exactly what they proposed; the re-promise covers the orders at risk when it ran."""
        for loop, o in self.out.items():
            row = self.conn.execute("SELECT impact_json, achieved_json FROM decision_log WHERE decision_id=?",
                                    (o["decision_id"],)).fetchone()
            impact, achieved = json.loads(row["impact_json"]), json.loads(row["achieved_json"])
            self.assertEqual(set(achieved["figures"]), set(impact), loop)
            if loop != "PROMISE":
                self.assertEqual(achieved["figures"], impact, loop)
        figures = json.loads(self.conn.execute("SELECT achieved_json FROM decision_log WHERE decision_id=?",
                                               (self.out["PROMISE"]["decision_id"],)).fetchone()["achieved_json"])["figures"]
        self.assertEqual(figures["orders"], self.out["PROMISE"]["orders"])

    def test_the_problems_the_loops_target_clear(self):
        """Re-detection after each loop: the line-stop risk, the quarantine and the promises at risk are gone. (The
        quality cluster stays: holds contain the batch, they don't erase its claims.)"""
        for loop in ("SUPPLY", "DATA", "PROMISE"):
            achieved = json.loads(self.conn.execute("SELECT achieved_json FROM decision_log WHERE decision_id=?",
                                                    (self.out[loop]["decision_id"],)).fetchone()["achieved_json"])
            self.assertTrue(achieved["exceptions"], loop)
            self.assertEqual({e["after"]["status"] for e in achieved["exceptions"]}, {"RESOLVED"}, loop)

    def test_every_write_is_attributed_to_its_decision(self):
        n = self.conn.execute("SELECT COUNT(*) n FROM outbound_message WHERE decision_id IS NULL AND created_at >= "
                              "(SELECT MIN(executed_at) FROM decision_log WHERE decided_by='test')").fetchone()["n"]
        self.assertEqual(n, 0)


class Api(unittest.TestCase):
    """The HTTP layer serves every page's main endpoint from the built database."""

    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        from ops.api import server
        cls.saved_db_path = config.DB_PATH
        config.DB_PATH = copy_of_built()
        server.load_routes()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        config.DB_PATH = cls.saved_db_path

    def get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=30) as r:
            return r.status, json.loads(r.read())

    def test_core_endpoints(self):
        for path in ("/api/meta", "/api/tower", "/api/loop", "/api/proof", "/api/genealogy/examples",
                     "/api/sandbox/tables", "/api/search?q=LV1", "/api/schedule/board"):
            status, body = self.get(path)
            self.assertEqual(status, 200, path)

    def test_line_schedule_adds_up(self):
        """Load is plan over capacity, fences sit where the site says, the dispatch list runs the day's plan from the shift
        start, and the pack line shows an MRP cut exactly when the line-stop exception is open."""
        _, d = self.get("/api/schedule/board")
        _, meta = self.get("/api/meta")
        self.assertEqual(len(d["lines"]), 3)
        today = d["as_of"]
        for ln in d["lines"]:
            for x in ln["days"]:
                if x["capacity"]:
                    self.assertAlmostEqual(x["load"], x["planned"] / x["capacity"], delta=0.0006)   # stored to 3 places
                self.assertEqual(x["zone"] == "past", x["date"] < today)
                if x["zone"] == "frozen":
                    self.assertLessEqual(x["date"], ln["fence"]["frozen_to"])
            dp = ln["dispatch"]
            self.assertIsNotNone(dp)
            self.assertEqual(sum(r["qty"] for r in dp["rows"]), dp["total"])
            self.assertEqual(dp["rows"][0]["start"], dp["shift"].split("–")[0])
            for r in dp["rows"]:
                self.assertLess(r["start"], r["end"])
        if d["kpis"]["mrp_cut_next"]:       # a pack-line cut is always backed by an open line-stop exception
            self.assertTrue(any(a["rule_id"] == "PLAN-LINE-STOP" for a in meta["alerts"]))
        rank = {"CRITICAL": 0, "SERIOUS": 1, "WARNING": 2, "INFO": 3}
        self.assertEqual([a["severity"] for a in meta["alerts"]],
                         sorted((a["severity"] for a in meta["alerts"]), key=rank.get))

    def test_read_only_sql_sandbox_rejects_writes(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/sandbox/sql", method="POST",
                                     data=json.dumps({"sql": "DELETE FROM site"}).encode(),
                                     headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(cm.exception.code, 400)
