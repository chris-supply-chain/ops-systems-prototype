"""MRP netting against hand-computed records (the same cases the EV-MRP-TEXTBOOK eval scores)."""
import datetime as dt
import unittest

from ops.logic.mrp import bom_children, lot_size, low_level_codes, net_item


class LotSizing(unittest.TestCase):
    def test_moq_then_multiple(self):
        """Net 10 with MOQ 200 and multiple 50 orders 200; net 230 orders 250."""
        self.assertEqual(lot_size(10, 200, 50), 200)
        self.assertEqual(lot_size(230, 200, 50), 250)

    def test_lot_for_lot_and_zero(self):
        self.assertEqual(lot_size(7.2, 0, 0, "LOT_FOR_LOT"), 8)
        self.assertEqual(lot_size(0, 200, 50), 0)


class Netting(unittest.TestCase):
    def test_lot_for_lot(self):
        """OH 20, LT 2: days 2-4 short by 5, 5, 20 -> planned receipts on those days, released two days earlier."""
        r = net_item(5, 20, 0, 2, 0, 0, {1: 10, 2: 15, 3: 5, 4: 20}, [], rule="LOT_FOR_LOT")
        self.assertEqual({i: q for i, q in enumerate(r["planned_receipts"]) if q}, {2: 5, 3: 5, 4: 20})
        self.assertEqual(r["planned_releases"], {0: 5, 1: 5, 2: 20})
        self.assertEqual(r["projected"], [20, 10, -5, -10, -30])

    def test_safety_stock_moq_multiple_past_due(self):
        """OH 100, SS 50: day 1 dips to 40 -> order 200 (MOQ), released 2 days ago; day 6 again."""
        r = net_item(8, 100, 50, 3, 200, 50, {1: 60, 4: 80, 6: 120}, [])
        self.assertEqual({i: q for i, q in enumerate(r["planned_receipts"]) if q}, {1: 200, 6: 200})
        self.assertEqual(r["planned_releases"], {-2: 200, 3: 200})
        self.assertIn("PAST_DUE_RELEASE", {m["message"] for m in r["messages"]})

    def test_reschedule_in_before_new_order(self):
        """The 100 already on order for day 6 is pulled in to day 2 instead of planning a new order."""
        r = net_item(8, 30, 0, 5, 0, 0, {2: 50}, [{"index": 6, "qty": 100, "ref": "PO-1", "confirmed": True}])
        self.assertFalse(any(r["planned_receipts"]))
        exp = [m for m in r["messages"] if m["message"] == "EXPEDITE"]
        self.assertEqual((exp[0]["from_index"], exp[0]["index"]), (6, 2))
        self.assertEqual(r["first_short"], 2)
        self.assertEqual(r["recommended"][2], 80)

    def test_cancel_unneeded_receipt(self):
        r = net_item(60, 500, 0, 5, 0, 0, {3: 100}, [{"index": 40, "qty": 200, "ref": "PO-2", "confirmed": True}])
        self.assertEqual([m["message"] for m in r["messages"] if m["ref"] == "PO-2"], ["CANCEL"])

    def test_defer_early_receipt(self):
        """Receipt on day 2, first needed on day 12 -> defer."""
        r = net_item(20, 100, 0, 3, 0, 0, {5: 50, 12: 100}, [{"index": 2, "qty": 100, "ref": "PO-3", "confirmed": True}])
        d = [m for m in r["messages"] if m["message"] == "DEFER"]
        self.assertEqual((d[0]["index"], d[0]["to_index"]), (2, 12))

    def test_alternate_under_deviation(self):
        """Rev A covers 20 of day 1 (allowance 35, valid to day 1); day 2 is short 30 with no alternate left."""
        r = net_item(4, 10, 0, 10, 0, 0, {1: 30, 2: 30}, [],
                     alternates=[{"item": "BMS-A", "on_hand": 40, "allowance": 35, "valid_to": 1}])
        self.assertEqual(r["alt"], [0, 20, 0, 0])
        self.assertEqual(r["projected"], [10, 0, -30, -30])
        self.assertEqual(r["first_short"], 2)

    def test_shortage_only_inside_lead_time(self):
        """A stock-out on day 30 with a 5-day lead time is just a planned order, not a line-stop risk."""
        r = net_item(40, 100, 0, 5, 0, 0, {30: 150}, [])
        self.assertIsNone(r["first_short"])
        self.assertEqual(r["planned_releases"], {25: 50})

    def test_unconfirmed_receipt_flagged(self):
        r = net_item(6, 0, 0, 1, 0, 0, {3: 10}, [{"index": 3, "qty": 10, "ref": "PO-4", "confirmed": False}])
        self.assertIn("UNCONFIRMED", {m["message"] for m in r["messages"]})
        self.assertFalse(any(r["planned_receipts"]))

    def test_past_due_receipt_lands_today(self):
        r = net_item(3, 0, 0, 1, 0, 0, {0: 5}, [{"index": -2, "qty": 5, "ref": "PO-5", "confirmed": True}])
        self.assertEqual(r["receipts"][0], 5)
        self.assertEqual(r["projected"][0], 0)


class Explosion(unittest.TestCase):
    BOM = {"P": [{"child_item_id": "A", "qty_per": 2, "bom_level": "OEM", "position": "X", "eff_from": "2026-01-01",
                  "eff_to": "2026-06-01"},
                 {"child_item_id": "B", "qty_per": 2, "bom_level": "OEM", "position": "X", "eff_from": "2026-06-01",
                  "eff_to": None}],
           "B": [{"child_item_id": "M", "qty_per": 0.5, "bom_level": "SUPPLIER", "position": "Y", "eff_from": "2026-01-01",
                  "eff_to": None}]}

    def test_effectivity_switches_on_the_cut_in_date(self):
        """ECO cut-in: before 2026-06-01 the parent uses A, from that day B (eff_to is exclusive)."""
        self.assertEqual([c[0] for c in bom_children(self.BOM, "P", dt.date(2026, 5, 31))], ["A"])
        self.assertEqual([c[0] for c in bom_children(self.BOM, "P", dt.date(2026, 6, 1))], ["B"])

    def test_low_level_codes(self):
        llc = low_level_codes(self.BOM)
        self.assertEqual((llc["P"], llc["B"], llc["M"]), (0, 1, 2))
