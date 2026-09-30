"""Genealogy traces, ATP allocation rules and chargeback / journal math on hand-built data."""
import datetime as dt
import json
import sqlite3
import unittest

from ops.logic import contracts
from ops.logic import genealogy as g
from ops.logic.atp import allocate, next_sailing_available, next_truck_available, skip_sundays
from ops.logic.chargeback import claim_lines, post_to_erp, write_chargeback
from tests.fixtures import mini_db


def seed_units(c):
    c.executemany("INSERT INTO unit(serial, item_id, origin, status, location_site_id, on_hold) VALUES (?,?,?,?,?,0)", [
        ("V1", "LV1-SLATE", "CM_FEED", "DELIVERED", None), ("V2", "LV1-SLATE", "CM_FEED", "AT_3PL", "3PL-RNO"),
        ("P1", "PK-STD", "OEM_MES", "DELIVERED", None), ("P2", "PK-STD", "OEM_MES", "AT_3PL", "3PL-RNO"),
        ("P3", "PK-STD", "OEM_MES", "BUILT", "OEM-FRE"), ("DU1", "DU-C", "SUPPLIER_ASN", "INSTALLED", None),
        ("MT1", "MTR-1", "SUPPLIER_ASN", "INSTALLED", None)])
    c.executemany("INSERT INTO lot(lot_id, item_id, supplier_id, site_id, qty_received, received_at, iqc_status, origin)"
                  " VALUES (?,?,?,?,?,?,?,?)", [
                      ("CA1", "CAM-NMC", "NWC", "SUP-NWC", 900, "2026-06-01T00:00:00Z", "NOT_INSPECTED", "SUPPLIER_ASN"),
                      ("CL1", "CEL-21700", "KES", "OEM-FRE", 20000, "2026-07-01T00:00:00Z", "ACCEPTED", "OEM_RECEIPT"),
                      ("CL2", "CEL-21700", "KES", "OEM-FRE", 20000, "2026-07-08T00:00:00Z", "ACCEPTED", "OEM_RECEIPT"),
                      ("MG1", "MAG-NDFEB", "KSM", "CM-TXG", 290, "2026-06-01T00:00:00Z", "NOT_INSPECTED", "SUPPLIER_ASN")])
    c.executemany("INSERT INTO lot_link VALUES (?,?,?,?)", [("CL1", "CA1", 420, "SUPPLIER_COA"),
                                                            ("CL2", "CA1", 420, "SUPPLIER_COA")])
    edges = [("V1", "DU1", None, "DU-C", 1, "INSTALLED", "DRIVE_UNIT"), ("DU1", "MT1", None, "MTR-1", 1, "INSTALLED", "MOTOR"),
             ("MT1", None, "MG1", "MAG-NDFEB", 0.42, "INSTALLED", "MAGNETS"),
             ("P1", None, "CL1", "CEL-21700", 40, "INSTALLED", "CELLS"), ("P2", None, "CL2", "CEL-21700", 40, "INSTALLED", "CELLS"),
             ("P3", None, "CL2", "CEL-21700", 40, "INSTALLED", "CELLS"), ("V1", "P1", None, "PK-STD", 1, "SHIPPED_WITH", "PACK"),
             ("V2", "P2", None, "PK-STD", 1, "SHIPPED_WITH", "PACK")]
    c.executemany("INSERT INTO genealogy(parent_serial, child_serial, child_lot_id, child_item_id, qty, relation, position,"
                  " installed_at, source) VALUES (?,?,?,?,?,?,?,'2026-08-01T00:00:00Z','OEM_MES')", edges)


def seed_order(c):
    """Two vehicles and two packs at the 3PL, and an open order SO-1 they can be allocated to."""
    c.executemany("INSERT INTO unit(serial, item_id, origin, status, location_site_id, on_hold) VALUES (?,?,?,?,?,0)",
                  [("V3", "LV1-SLATE", "CM_FEED", "AT_3PL", "3PL-RNO"), ("V4", "LV1-SLATE", "CM_FEED", "AT_3PL", "3PL-RNO"),
                   ("P4", "PK-STD", "OEM_MES", "AT_3PL", "3PL-RNO"), ("P5", "PK-STD", "OEM_MES", "AT_3PL", "3PL-RNO")])
    c.execute("INSERT INTO customer(customer_id, kind, display_name, state, region) VALUES ('C1','CONSUMER','C1','NV','West')")
    c.execute("INSERT INTO customer_order(order_id, customer_id, channel, ordered_at, ship_to_region, ship_to_state, status)"
              " VALUES ('SO-1','C1','D2C','2026-09-01T00:00:00Z','West','NV','OPEN')")
    c.execute("INSERT INTO order_line(order_id, line_no, item_id, qty, unit_price_usd) VALUES ('SO-1',1,'LV1-SLATE',1,1499)")


def allocation(c, received, pack, allocated="2026-09-20T17:00:00Z", vehicle="V3"):
    """The 3PL's message pairing a vehicle with a pack on SO-1, landed for the next load."""
    msg = json.dumps({"order_id": "SO-1", "allocated_at": allocated, "lines": [{"line": 1, "vehicle": vehicle, "pack": pack}]})
    c.execute("INSERT INTO raw_3pl_message(received_at, msg_type, payload, ingest_status) VALUES (?, 'ALLOCATION', ?, 'PENDING')",
              (received, msg))


class Genealogy(unittest.TestCase):
    def setUp(self):
        self.c = mini_db()
        seed_units(self.c)

    def test_backward_tree_reaches_the_magnet_lot(self):
        tree = g.backward(self.c, "V1")
        du = next(n for n in tree["children"] if n["id"] == "DU1")
        mt = du["children"][0]
        self.assertEqual((mt["id"], mt["children"][0]["id"]), ("MT1", "MG1"))

    def test_backward_tree_climbs_lot_links_to_tier_two(self):
        """Pack P1's cell lot shows the cathode batch it was made from."""
        tree = g.backward(self.c, "P1")
        cl = tree["children"][0]
        self.assertEqual((cl["id"], cl["children"][0]["id"], cl["children"][0]["relation"]), ("CL1", "CA1", "MADE_FROM"))

    def test_forward_from_a_cathode_batch_finds_every_pack_and_vehicle(self):
        found = {u["serial"] for u in g.forward(self.c, "CA1")}
        self.assertEqual(found, {"P1", "P2", "P3", "V1", "V2"})

    def test_recall_scope_splits_control_from_customers(self):
        """V2 (at the 3PL) and P3 (unmarried, at Fremont) are in our control; V1 is with a customer."""
        sc = g.recall_scope(self.c, "CA1")
        self.assertEqual((sc["in_control"], sc["with_customer"], sc["packs_unmarried"]), (2, 1, 1))
        self.assertEqual(set(sc["lots"]), {"CA1", "CL1", "CL2"})

    def test_one_current_copy_of_each_link(self):
        """The grain is enforced by a unique index on current links: P1's cell lot cannot be recorded twice, while a
        second lot in the same slot (a split) and a closed copy kept as history are fine."""
        add = ("INSERT INTO genealogy(parent_serial, child_lot_id, child_item_id, qty, relation, position, installed_at,"
               " source) VALUES ('P1', ?, 'CEL-21700', ?, 'INSTALLED', 'CELLS', ?, 'OEM_MES')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.c.execute(add, ("CL1", 40, "2026-08-02T00:00:00Z"))
        self.c.execute("UPDATE genealogy SET qty=30 WHERE parent_serial='P1' AND child_lot_id='CL1'")
        self.c.execute(add, ("CL2", 10, "2026-08-01T00:00:00Z"))             # CL1 ran out after 30 cells
        self.c.execute("UPDATE genealogy SET removed_at='2026-08-03T00:00:00Z' WHERE parent_serial='P1' AND child_lot_id='CL1'")
        self.c.execute(add, ("CL1", 30, "2026-08-03T00:00:00Z"))             # reworked with the same lot
        rows = self.c.execute("SELECT child_lot_id, removed_at IS NULL AS is_current FROM genealogy WHERE parent_serial='P1'"
                              " ORDER BY installed_at, child_lot_id").fetchall()
        self.assertEqual([(r["child_lot_id"], r["is_current"]) for r in rows], [("CL1", 0), ("CL2", 1), ("CL1", 1)])

    def test_a_resent_allocation_records_the_kit_once(self):
        """The 3PL sends the same allocation twice. With the database refusing a second current link, the load must not
        crash on the retry: the kit link is recorded once and both messages land."""
        from ops.ingest.logistics import run_3pl
        c = self.c
        seed_order(c)
        allocation(c, "2026-09-20T17:01:00Z", "P4")
        allocation(c, "2026-09-20T17:31:00Z", "P4")
        run_3pl(c, "2026-09-26T15:00:00Z")
        kit = c.execute("SELECT child_serial, position FROM genealogy WHERE parent_serial='V3'").fetchall()
        self.assertEqual([(r["child_serial"], r["position"]) for r in kit], [("P4", "PACK")])
        self.assertEqual([r["ingest_status"] for r in c.execute("SELECT ingest_status FROM raw_3pl_message")], ["OK", "OK"])

    def test_a_second_part_in_one_slot_is_flagged(self):
        """A drive unit installed where one is still recorded (its removal never came) is a contradiction, not a
        duplicate: it lands, and C-GEN-06 flags the slot until the removal is recorded."""
        c = self.c
        self.assertEqual(contracts.run_one(c, "C-GEN-06")["violations"], 0)
        c.execute("INSERT INTO unit(serial, item_id, origin, status, on_hold) VALUES ('DU2','DU-C','SUPPLIER_ASN','INSTALLED',0)")
        c.execute("INSERT INTO genealogy(parent_serial, child_serial, child_item_id, qty, relation, position, installed_at, source)"
                  " VALUES ('V1','DU2','DU-C',1,'INSTALLED','DRIVE_UNIT','2026-08-05T00:00:00Z','CM_FEED')")
        found = contracts.run_one(c, "C-GEN-06")
        self.assertEqual((found["violations"], found["sample"][0]["parent_serial"], found["sample"][0]["position"]),
                         (1, "V1", "DRIVE_UNIT"))
        c.execute("UPDATE genealogy SET removed_at='2026-08-05T00:00:00Z' WHERE child_serial='DU1'")
        self.assertEqual(contracts.run_one(c, "C-GEN-06")["violations"], 0)

    def test_a_reallocation_moves_the_kit_link(self):
        """The 3PL swaps the pack on SO-1 before it ships. The latest allocation is the truth: the first kit link closes
        and stays as history, P4 is free again, and the slot holds one pack."""
        from ops.ingest.logistics import run_3pl
        c = self.c
        seed_order(c)
        allocation(c, "2026-09-20T17:01:00Z", "P4")
        allocation(c, "2026-09-21T09:01:00Z", "P5", allocated="2026-09-21T09:00:00Z")
        run_3pl(c, "2026-09-26T15:00:00Z")
        kit = c.execute("SELECT child_serial, removed_at, removal_reason FROM genealogy WHERE parent_serial='V3'"
                        " ORDER BY installed_at").fetchall()
        self.assertEqual([(r["child_serial"], r["removed_at"]) for r in kit], [("P4", "2026-09-21T09:00:00Z"), ("P5", None)])
        self.assertEqual(kit[0]["removal_reason"], "Re-allocated by the 3PL (SO-1)")
        status = {r["serial"]: r["status"] for r in c.execute("SELECT serial, status FROM unit WHERE serial IN ('P4','P5','V3')")}
        self.assertEqual(status, {"P4": "AT_3PL", "P5": "ALLOCATED", "V3": "ALLOCATED"})
        self.assertEqual(contracts.run_one(c, "C-GEN-06")["violations"], 0)

    def test_a_reallocation_to_another_vehicle_moves_the_pack(self):
        """The 3PL gives SO-1 a different vehicle with the same pack: the pack leaves V3 (kept as history) for V4, V3 is
        free again, and the pack is in one place."""
        from ops.ingest.logistics import run_3pl
        c = self.c
        seed_order(c)
        allocation(c, "2026-09-20T17:01:00Z", "P4")
        allocation(c, "2026-09-21T09:01:00Z", "P4", allocated="2026-09-21T09:00:00Z", vehicle="V4")
        run_3pl(c, "2026-09-26T15:00:00Z")
        links = c.execute("SELECT parent_serial, removed_at FROM genealogy WHERE child_serial='P4' ORDER BY installed_at").fetchall()
        self.assertEqual([(r["parent_serial"], r["removed_at"]) for r in links], [("V3", "2026-09-21T09:00:00Z"), ("V4", None)])
        status = {r["serial"]: r["status"] for r in c.execute("SELECT serial, status FROM unit WHERE serial IN ('V3','V4','P4')")}
        self.assertEqual(status, {"V3": "AT_3PL", "V4": "ALLOCATED", "P4": "ALLOCATED"})
        self.assertEqual(contracts.run_one(c, "C-GEN-03")["violations"], 0)

    def test_a_pack_kitted_to_two_vehicles_is_flagged(self):
        """P1 already ships with V1; a kit link putting it with V2 as well lands, and C-GEN-03 flags the pack until one
        of the links is closed."""
        c = self.c
        self.assertEqual(contracts.run_one(c, "C-GEN-03")["violations"], 0)
        c.execute("INSERT INTO genealogy(parent_serial, child_serial, child_item_id, qty, relation, position, installed_at, source)"
                  " VALUES ('V2','P1','PK-STD',1,'SHIPPED_WITH','EXTRA_PACK','2026-08-05T00:00:00Z','3PL_FEED')")
        found = contracts.run_one(c, "C-GEN-03")
        self.assertEqual((found["violations"], found["sample"][0]["child_serial"], found["sample"][0]["parents"]), (1, "P1", 2))
        c.execute("UPDATE genealogy SET removed_at='2026-08-05T00:00:00Z' WHERE parent_serial='V2' AND child_serial='P1'")
        self.assertEqual(contracts.run_one(c, "C-GEN-03")["violations"], 0)

    def test_a_service_swap_keeps_the_slot(self):
        """A warranty swap of V1's second pack puts the new pack in that slot (EXTRA_PACK), not on top of the first."""
        from ops.ingest.warranty import run_warranty
        c = self.c
        c.executemany("INSERT INTO unit(serial, item_id, origin, status, location_site_id, on_hold) VALUES (?,?,?,?,?,0)",
                      [("P5", "PK-STD", "OEM_MES", "DELIVERED", None), ("P6", "PK-STD", "OEM_MES", "AT_3PL", "3PL-RNO")])
        c.execute("INSERT INTO genealogy(parent_serial, child_serial, child_item_id, qty, relation, position, installed_at, source)"
                  " VALUES ('V1','P5','PK-STD',1,'SHIPPED_WITH','EXTRA_PACK','2026-08-01T00:00:00Z','3PL_FEED')")
        case = {"case_no": "CS-900001", "opened_at": "2026-09-20T17:00:00Z", "asset_serial": "V1",
                "symptom": "Range dropped by half", "status": "CLOSED", "labor_hours": 0.5, "parts_cost": 235.4,
                "parts_replaced": [{"part": "PK-STD", "removed_serial": "P5", "installed_serial": "P6"}]}
        c.execute("INSERT INTO raw_warranty_case(received_at, payload, ingest_status) VALUES ('2026-09-20T17:05:00Z', ?, 'PENDING')",
                  (json.dumps(case),))
        run_warranty(c, "2026-09-26T15:00:00Z")
        packs = {r["position"]: r["child_serial"] for r in c.execute(
            "SELECT position, child_serial FROM genealogy WHERE parent_serial='V1' AND relation='SHIPPED_WITH' AND removed_at IS NULL")}
        self.assertEqual(packs, {"PACK": "P1", "EXTRA_PACK": "P6"})
        self.assertEqual(contracts.run_one(c, "C-GEN-06")["violations"], 0)


class Atp(unittest.TestCase):
    AS_OF = dt.date(2026, 9, 26)          # a Saturday

    def conn(self):
        c = mini_db()
        return c

    def order(self, oid, v, p, ordered, channel="D2C", requested=None, extra=None, region="WEST"):
        return {"order_id": oid, "channel": channel, "reserved_at": None, "ordered_at": ordered, "requested_date": requested,
                "ship_to_region": region, "vehicle_sku": v, "pack_sku": p, "extra_pack": extra}

    def test_calendar_helpers(self):
        """Built Saturday 9/26 -> loaded Thu 10/1 -> sails 10/4 -> Oakland +15 -> Reno +4 = 10/23."""
        self.assertEqual(next_sailing_available(self.AS_OF), dt.date(2026, 10, 23))
        self.assertEqual(next_truck_available(dt.date(2026, 9, 26)), dt.date(2026, 9, 30))   # Monday truck + 2
        self.assertEqual(skip_sundays(dt.date(2026, 9, 26), 2), dt.date(2026, 9, 29))        # skips Sunday 9/27

    def test_fifo_by_reservation_and_joint_constraint(self):
        """Two orders, one vehicle today and one pack today, a second pack on day 3: first order ships today."""
        c = self.conn()
        veh = {"LV1-SLATE": [(self.AS_OF, 2, "3PL stock")]}
        pk = {"PK-STD": [(self.AS_OF, 1, "3PL stock"), (self.AS_OF + dt.timedelta(days=3), 1, "truck")]}
        orders = [self.order("B", "LV1-SLATE", "PK-STD", "2026-09-02"), self.order("A", "LV1-SLATE", "PK-STD", "2026-09-01")]
        res = allocate(c, veh, pk, orders)
        self.assertEqual(res["A"]["ship_date"], "2026-09-26")
        self.assertEqual(res["B"]["ship_date"], "2026-09-29")           # waits for the pack on day 3
        self.assertEqual(res["A"]["promise"], "2026-09-30")             # West: 3 carrier days, Sunday skipped

    def test_blocked_order_does_not_block_the_queue(self):
        """The oldest order needs a Large pack that never comes; the next order still ships today."""
        c = self.conn()
        veh = {"LV1-SLATE": [(self.AS_OF, 2, "stock")]}
        pk = {"PK-STD": [(self.AS_OF, 1, "stock")]}
        orders = [self.order("OLD", "LV1-SLATE", "PK-LRG", "2026-08-01"), self.order("NEW", "LV1-SLATE", "PK-STD", "2026-09-01")]
        res = allocate(c, veh, pk, orders, horizon=10)
        self.assertEqual(res["NEW"]["ship_date"], "2026-09-26")
        self.assertTrue(res["OLD"].get("beyond_horizon"))

    def test_fleet_waits_for_its_window_then_jumps_the_queue(self):
        """Fleet order requested 10/20 may ship from 10/6 (14-day window) and then goes before older D2C orders."""
        c = self.conn()
        veh = {"LV1-SLATE": [(dt.date(2026, 10, 6), 1, "container")]}
        pk = {"PK-STD": [(self.AS_OF, 5, "stock")]}
        orders = [self.order("D2C", "LV1-SLATE", "PK-STD", "2026-08-01"),
                  self.order("FLEET", "LV1-SLATE", "PK-STD", "2026-09-20", channel="FLEET", requested="2026-10-20")]
        res = allocate(c, veh, pk, orders)
        self.assertEqual(res["FLEET"]["ship_date"], "2026-10-06")
        self.assertIsNone(res["D2C"]["ship_date"])


class Finance(unittest.TestCase):
    def test_claim_priced_by_recovery_terms_and_posted_balanced(self):
        """Parts 235.40 x 100% + labor 0.5h x $85 + logistics 140 + admin 150 = 567.90; JE debits = credits."""
        c = mini_db()
        seed_units(c)
        c.execute("INSERT INTO warranty_claim(claim_id, serial, reported_at, symptom, defect_code, supplier_id, cost_parts_usd,"
                  " cost_labor_usd, cost_logistics_usd, status) VALUES ('WC-1','V1','2026-09-20T00:00:00Z','fade','FLD-CAPFADE',"
                  "'KES',235.40,42.50,140,'CLOSED')")
        c.executemany("INSERT INTO gl_account VALUES (?,?,?)", [("2000", "AP", "LIABILITY"), ("5410", "Recovery", "CONTRA_EXPENSE"),
                                                                ("5110", "Q recovery", "CONTRA_EXPENSE")])
        lines = claim_lines(c, "KES", ["WC-1"])
        self.assertAlmostEqual(lines[0][3], 567.90, places=2)
        total = write_chargeback(c, "CB-9", "KES", "WARRANTY", "test", lines, "ACCEPTED", "2026-09-21T00:00:00Z")
        je = post_to_erp(c, "CB-9", "2026-09-22T00:00:00Z")
        d, cr = c.execute("SELECT SUM(debit_usd) d, SUM(credit_usd) c FROM erp_journal_line WHERE je_id=?", (je,)).fetchone().values()
        self.assertAlmostEqual(d, total, places=2)
        self.assertAlmostEqual(d, cr, places=2)
        self.assertEqual(c.execute("SELECT chargeback_id FROM warranty_claim WHERE claim_id='WC-1'").fetchone()["chargeback_id"],
                         "CB-9")
