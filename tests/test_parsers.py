"""Email, Excel and EDI parsing: the parser must be right when it commits and abstain when it cannot be."""
import unittest

from ops.ingest.documents import EmailPipeline, classify, parse_confirmation
from ops.ingest.warranty import classify as classify_symptom
from ops.xlsx import read_xlsx, write_xlsx


class PromiseParser(unittest.TestCase):
    def test_ship_date_adds_supplier_transit(self):
        """Regression (caught by EV-PROMISE-PARSE): 'Qty 600 / ship date 05/21/2026' is a ship date, +2d from Tijuana."""
        r = parse_confirmation("Hi,\nPO#4500111-2 confirmed. Qty 600 / ship date 05/21/2026.\nThanks!", "RE: PO", "NRT")
        self.assertEqual((r["po"], r["line"], r["promise"], r["qty"]), ("4500111", 2, "2026-05-23", 600.0))

    def test_etd_month_name_from_taiwan_is_ocean_transit(self):
        r = parse_confirmation("Confirming order 4500150, item 1. ETD 05-Oct-2026, 1,000 units.", "PO", "VPC")
        self.assertEqual(r["promise"], "2026-10-23")

    def test_spanish_day_first(self):
        """Mexican suppliers write dd/mm/yyyy: 07/10/2026 is 7 October."""
        r = parse_confirmation("Confirmamos la OC 4500120 partida 3: 1,200 piezas, entrega 07/10/2026.", "RE", "SMT")
        self.assertEqual((r["line"], r["promise"], r["qty"]), (3, "2026-10-07", 1200.0))

    def test_us_supplier_month_first(self):
        r = parse_confirmation("PO#4500133-1 confirmed. Qty 500 / ship date 10/07/2026.", "RE", "BAY")
        self.assertEqual(r["promise"], "2026-10-08")

    def test_slip_in_prose(self):
        r = parse_confirmation("For PO 4500120 line 3 we need to move delivery to October 13, 2026 due to capacity.",
                               "RE", "SMT")
        self.assertEqual(r["promise"], "2026-10-13")

    def test_yes_confirmed_accepts_our_need_date(self):
        body = "Yes, confirmed. Thank you.\n\n> From: OEM Purchasing\n> Please confirm PO 4500133 line 1: 500 pcs, need date 2026-10-02.\n"
        r = parse_confirmation(body, "RE: PO 4500133", "BAY")
        self.assertEqual((r["po"], r["line"], r["promise"]), ("4500133", 1, "2026-10-02"))

    def test_abstains_without_a_firm_date(self):
        r = parse_confirmation("Re PO 4500133: we will need to check line 1 with production and will revert.", "RE", "BAY")
        self.assertTrue(r["abstain"])

    def test_abstains_without_po(self):
        self.assertTrue(parse_confirmation("Thanks, delivery 2026-10-10.", "Hello", "BAY")["abstain"])

    def test_classifier_routes_feeds(self):
        self.assertEqual(classify("pmc-report@formosa-ap.example", "【日報】LV-1 生產日報 Daily Production Report 2026/09/24", ""),
                         "CM_DAILY_REPORT")
        self.assertIsNone(classify("noreply@baylineseals.example", "Automatic reply: Out of office", ""))


class Excel(unittest.TestCase):
    def test_round_trip_bilingual_with_merged_title(self):
        data = write_xlsx([{"name": "生產日報 Output", "rows": [["Title"], [], ["線別 Line", "實際產出 Actual"], ["L1", 12]],
                            "merges": ["A1:B1"]}])
        (name, rows), = read_xlsx(data)
        self.assertEqual(name, "生產日報 Output")
        self.assertEqual(rows[3], ["L1", 12])

    def test_header_row_found_by_bilingual_synonyms(self):
        rows = [["Formosa report"], ["報表日期 Report date: 2026/09/24"], [],
                ["線別 Line", "機種 Model", "料號 CM P/N", "計畫 Plan", "實際產出 Actual"]]
        hi, cols = EmailPipeline.find_header(rows, {"line": ["線別", "line"], "pn": ["料號", "p/n"], "actual": ["實際", "actual"]})
        self.assertEqual((hi, cols["line"], cols["pn"], cols["actual"]), (3, 0, 2, 4))

    def test_dates_in_supplier_formats(self):
        self.assertEqual(EmailPipeline.parse_date("2026/10/05").isoformat(), "2026-10-05")
        self.assertEqual(EmailPipeline.parse_date("05-Oct-2026").isoformat(), "2026-10-05")
        self.assertEqual(EmailPipeline.parse_date("07/10/2026", day_first=True).isoformat(), "2026-10-07")


class SymptomClassifier(unittest.TestCase):
    def test_clear_phrasings(self):
        self.assertEqual(classify_symptom("Range dropped by half in a month"), "FLD-CAPFADE")
        self.assertEqual(classify_symptom("Display has a vertical line through it"), "FLD-PIXEL")
        self.assertEqual(classify_symptom("Battery shows error E07 when plugged in"), "FLD-CHG")

    def test_known_blind_spot_is_documented(self):
        """'won't charge past 60%' reads as a charging fault; the eval tracks this confusion (CAPFADE -> CHG)."""
        self.assertEqual(classify_symptom("Battery dies fast and won't charge past 60%"), "FLD-CHG")
