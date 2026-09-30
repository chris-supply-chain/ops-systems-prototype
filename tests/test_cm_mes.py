"""CM MES normalizer: mapping as data, time zones, duplicates, quarantine and replay, provisional parts."""
import json
import unittest

from ops.ingest.cm_mes import CmMes, run_cm_and_asn
from ops.ingest.common import Run
from tests.fixtures import cm_mapping, mini_db


def v1(sn, code, ts, rslt="OK", parts=(), parts_out=(), ng="", ng_desc="", meas=None, msg_id="M1", lots=()):
    return json.dumps({"msg_id": msg_id, "sn": sn, "stn_cd": code, "line": "L1", "model": "FAP-LV1-SL", "wo": None,
                       "evt_ts": ts, "rslt": rslt, "ng_cd": ng, "ng_desc": ng_desc, "op": "A1",
                       "parts": [{"pn": p[0], "sn": p[1]} for p in parts],
                       "parts_out": [{"pn": p[0], "sn": p[1]} for p in parts_out],
                       "lots": [{"pn": x[0], "lot": x[1], "qty": x[2]} for x in lots], "meas": meas or {}},
                      ensure_ascii=False)


class Normalizer(unittest.TestCase):
    def setUp(self):
        self.c = mini_db()
        cm_mapping(self.c)
        self.run = Run(self.c, "CM_MES", "2026-09-26T15:00:00Z")
        self.mes = CmMes(self.c, self.run)

    def land(self, payload, received):
        cur = self.c.execute("INSERT INTO raw_cm_mes_event(source_system, received_at, payload) VALUES ('FAP-MES',?,?)",
                             (received, payload))
        return cur.lastrowid

    def test_local_taipei_time_becomes_utc(self):
        """'2026-07-29 14:03:11' in Taipei is 06:03:11Z."""
        rid = self.land(v1("LV1-1", "S10", "2026-07-29 14:03:11", parts=[("FRM-1", "FR-1")]), "2026-07-29T06:04:00Z")
        self.assertEqual(self.mes.process(rid, "2026-07-29T06:04:00Z", self.c.execute(
            "SELECT payload FROM raw_cm_mes_event WHERE raw_id=?", (rid,)).fetchone()["payload"]), "OK")
        ev = self.c.execute("SELECT event_ts, result FROM station_event").fetchone()
        self.assertEqual((ev["event_ts"], ev["result"]), ("2026-07-29T06:03:11Z", "PASS"))
        self.assertEqual(self.c.execute("SELECT child_serial FROM genealogy WHERE parent_serial='LV1-1'").fetchone()["child_serial"],
                         "FR-1")

    def test_local_time_labelled_utc_is_corrected(self):
        """The TZ bug: local 14:03 sent as '14:03:11Z' arrives 8h 'in the future' and is reinterpreted."""
        p = v1("LV1-2", "S10", "2026-07-16T14:03:11Z", parts=[("FRM-1", "FR-2")])
        rid = self.land(p, "2026-07-16T06:03:40Z")
        self.assertEqual(self.mes.process(rid, "2026-07-16T06:03:40Z", p), "WARN")
        self.assertEqual(self.c.execute("SELECT event_ts FROM station_event").fetchone()["event_ts"], "2026-07-16T06:03:11Z")

    def test_retry_with_new_message_id_is_a_duplicate(self):
        p1 = v1("LV1-3", "S10", "2026-07-29 14:00:00", parts=[("FRM-1", "FR-3")], msg_id="A")
        p2 = v1("LV1-3", "S10", "2026-07-29 14:00:00", parts=[("FRM-1", "FR-3")], msg_id="A-R1")
        self.mes.process(self.land(p1, "2026-07-29T06:01:00Z"), "2026-07-29T06:01:00Z", p1)
        self.assertEqual(self.mes.process(self.land(p2, "2026-07-29T08:00:00Z"), "2026-07-29T08:00:00Z", p2), "DUPLICATE")
        self.assertEqual(self.c.execute("SELECT COUNT(*) n FROM station_event").fetchone()["n"], 1)

    def test_retry_with_a_corrected_timestamp_does_not_double_a_lot(self):
        """The event dedup keys on the timestamp, so a retry whose time was corrected gets through. The unique index on
        current links keeps the tire lot on the vehicle once, and the event says why."""
        self.c.execute("INSERT INTO lot(lot_id, item_id, supplier_id, site_id, qty_received, received_at, iqc_status, origin)"
                       " VALUES ('TR-1', 'TIR-1', 'FAP', 'CM-TXG', 400, '2026-08-01T00:00:00Z', 'ACCEPTED', 'CM_FEED')")
        mes = CmMes(self.c, self.run)                   # a fresh normalizer knows the new lot
        a = v1("LV1-9", "S20", "2026-08-10 09:00:00", lots=[("TIR-1", "TR-1", 2)], msg_id="a")
        b = v1("LV1-9", "S20", "2026-08-10 09:00:07", lots=[("TIR-1", "TR-1", 2)], msg_id="a-R1")
        self.assertEqual(mes.process(self.land(a, "2026-08-10T01:01:00Z"), "2026-08-10T01:01:00Z", a), "OK")
        rid = self.land(b, "2026-08-10T01:30:00Z")
        self.assertEqual(mes.process(rid, "2026-08-10T01:30:00Z", b), "WARN")
        self.assertEqual([r["qty"] for r in self.c.execute("SELECT qty FROM genealogy WHERE child_lot_id='TR-1'")], [2])
        note = self.c.execute("SELECT ingest_note FROM raw_cm_mes_event WHERE raw_id=?", (rid,)).fetchone()["ingest_note"]
        self.assertIn("lot TR-1 already recorded on LV1-9", note)

    def test_schema_change_quarantines_then_replays(self):
        """A v2 payload before mapping v2 is active is quarantined, then replayed once v2 is live."""
        p = json.dumps({"serial": "LV1-4", "stationCode": "S10", "lineId": "L1", "modelNo": "FAP-LV1-SL",
                        "eventTime": "2026-08-27T12:40:00+08:00", "result": "PASS", "components": [], "measurements": {}})
        rid = self.land(p, "2026-08-27T04:41:00Z")
        self.assertEqual(self.mes.process(rid, "2026-08-27T04:41:00Z", p), "QUARANTINED")
        self.assertEqual(self.mes.replay_quarantined(), 1)
        row = self.c.execute("SELECT ingest_status FROM raw_cm_mes_event WHERE raw_id=?", (rid,)).fetchone()
        self.assertEqual(row["ingest_status"], "REPLAYED")
        self.assertEqual(self.c.execute("SELECT event_ts FROM station_event").fetchone()["event_ts"], "2026-08-27T04:40:00Z")

    def test_unknown_station_is_quarantined_not_dropped(self):
        p = v1("LV1-5", "S65", "2026-08-10 10:00:00", rslt="RWK")
        rid = self.land(p, "2026-08-10T02:01:00Z")
        self.assertEqual(self.mes.process(rid, "2026-08-10T02:01:00Z", p), "QUARANTINED")
        self.assertIn("unknown station S65", self.c.execute("SELECT ingest_note FROM raw_cm_mes_event").fetchone()["ingest_note"])

    def test_chinese_defect_text_is_mapped(self):
        p = v1("LV1-6", "S60", "2026-08-10 10:00:00", rslt="NG", ng_desc="馬達異音")
        self.mes.process(self.land(p, "2026-08-10T02:01:00Z"), "2026-08-10T02:01:00Z", p)
        self.assertEqual(self.c.execute("SELECT defect_code FROM station_event").fetchone()["defect_code"], "EOL-MOTOR")

    def test_part_swap_closes_the_old_edge(self):
        """Rework swaps the drive unit: the old edge gets removed_at, the new one is active (as-maintained history)."""
        a = v1("LV1-7", "S20", "2026-08-10 09:00:00", parts=[("DU-C", "DU-OLD")], msg_id="a")
        b = v1("LV1-7", "S60", "2026-08-10 10:30:00", rslt="RWK", parts=[("DU-C", "DU-NEW")], parts_out=[("DU-C", "DU-OLD")],
               msg_id="b")
        self.mes.process(self.land(a, "2026-08-10T01:01:00Z"), "2026-08-10T01:01:00Z", a)
        self.mes.process(self.land(b, "2026-08-10T02:31:00Z"), "2026-08-10T02:31:00Z", b)
        rows = {r["child_serial"]: r["removed_at"] for r in self.c.execute(
            "SELECT child_serial, removed_at FROM genealogy WHERE parent_serial='LV1-7' AND position='DRIVE_UNIT'")}
        self.assertIsNotNone(rows["DU-OLD"])
        self.assertIsNone(rows["DU-NEW"])

    def test_component_without_asn_is_provisional_until_the_asn_lands(self):
        p = v1("LV1-8", "S20", "2026-08-12 09:00:00", parts=[("DU-C", "DUC26-9")])
        self.land(p, "2026-08-12T01:01:00Z")
        asn = json.dumps({"asn_no": "ASN-TNM-1", "supplier": "TNM", "ship_date": "2026-08-11", "ship_to": "CM-TXG",
                          "items": [{"part": "DU-C", "serial": "DUC26-9", "children": [
                              {"part": "MTR-1", "serial": "MT-9"}, {"part": "CTL-C", "serial": "CT-9"}]}]})
        self.c.execute("INSERT INTO raw_supplier_asn(supplier_id, received_at, payload) VALUES ('TNM','2026-08-13T00:00:00Z',?)",
                       (asn,))
        run_cm_and_asn(self.c, "2026-09-26T15:00:00Z")
        u = self.c.execute("SELECT origin FROM unit WHERE serial='DUC26-9'").fetchone()
        self.assertEqual(u["origin"], "SUPPLIER_ASN")
        self.assertEqual(self.c.execute("SELECT ingest_status FROM raw_supplier_asn").fetchone()["ingest_status"], "WARN")
        self.assertEqual(self.c.execute("SELECT COUNT(*) n FROM genealogy WHERE parent_serial='DUC26-9'").fetchone()["n"], 2)
