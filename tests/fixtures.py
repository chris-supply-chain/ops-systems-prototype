"""Tiny hand-built databases for unit tests (every row is visible in the test that uses it)."""
import atexit
import json
import os
import shutil
import tempfile
from pathlib import Path

from ops.db import connect, init_schema


def mini_db():
    conn = connect(":memory:")
    init_schema(conn)
    conn.executemany("INSERT INTO meta VALUES (?,?)", [("as_of", "2026-09-26"), ("now_utc", "2026-09-26T15:00:00Z")])
    conn.executemany("INSERT INTO site(site_id, name, kind, country, tz) VALUES (?,?,?,?,?)", [
        ("CM-TXG", "CM", "CM", "TW", "Asia/Taipei"), ("OEM-FRE", "Fremont", "OEM_PLANT", "US", "America/Los_Angeles"),
        ("3PL-RNO", "Reno", "3PL", "US", "America/Los_Angeles"), ("SUP-NWC", "Northwind", "SUPPLIER", "JP", "Asia/Tokyo")])
    conn.executemany("INSERT INTO supplier(supplier_id, name, tier, parent_supplier_id, country, commodity, recovery_terms)"
                     " VALUES (?,?,?,?,?,?,?)", [
                         ("FAP", "CM", 1, None, "TW", "CM", None), ("TNM", "Motion", 1, None, "TW", "DU", None),
                         ("KES", "Kestrel", 1, None, "KR", "Cells", json.dumps({"parts_pct": 1.0, "labor_rate_usd": 85,
                                                                              "labor_hours_cap": 3, "admin_fee_usd": 150})),
                         ("KSM", "Magnets", 2, "TNM", "JP", "Magnets", None), ("NWC", "Cathode", 2, "KES", "JP", "Cathode", None)])
    items = [("LV1-SLATE", "VEHICLE", "CM_BUILT", 1, 0), ("PK-STD", "PACK", "OEM_BUILT", 1, 0),
             ("DU-C", "MODULE", "BUY_CONSIGNED", 1, 0), ("MTR-1", "COMPONENT", "SUPPLIER_SOURCED", 1, 0),
             ("CTL-C", "COMPONENT", "SUPPLIER_SOURCED", 1, 0), ("MAG-NDFEB", "MATERIAL", "SUPPLIER_SOURCED", 0, 1),
             ("FRM-1", "COMPONENT", "CM_SOURCED", 1, 0), ("TIR-1", "COMPONENT", "CM_SOURCED", 0, 1),
             ("CEL-21700", "COMPONENT", "BUY_DIRECT", 0, 1), ("CAM-NMC", "MATERIAL", "SUPPLIER_SOURCED", 0, 1),
             ("BMS-B", "MODULE", "BUY_DIRECT", 1, 0)]
    conn.executemany("INSERT INTO item(item_id, name, kind, commodity, make_buy, serialized, lot_controlled, primary_supplier_id)"
                     " VALUES (?,?,?,?,?,?,?,?)",
                     [(i, i, k, k, mb, s, l, {"DU-C": "TNM", "MTR-1": "TNM", "CTL-C": "TNM", "MAG-NDFEB": "KSM",
                                              "CEL-21700": "KES", "CAM-NMC": "NWC", "FRM-1": "FAP", "TIR-1": "FAP"}.get(i))
                      for i, k, mb, s, l in items])
    for line in ("L1",):
        for code, seq in (("S10", 10), ("S20", 20), ("S60", 60), ("S80", 80)):
            conn.execute("INSERT INTO station(station_id, site_id, line, code, seq, name, kind) VALUES (?,?,?,?,?,?,?)",
                         (f"TXG-{line}-{code}", "CM-TXG", line, code, seq, code, "ASSEMBLY"))
    conn.executemany("INSERT INTO defect_code VALUES (?,?,?,?,?)", [
        ("EOL-MOTOR", "Motor noise", "COMPONENT", "SUPPLIER", None), ("FLD-CAPFADE", "Fade", "FIELD", "SUPPLIER", None)])
    return conn


def cm_mapping(conn):
    v1 = {"fields": {"serial": "sn", "station": "stn_cd", "line": "line", "event_time": "evt_ts", "result": "rslt",
                     "defect_code": "ng_cd", "defect_text": "ng_desc", "operator": "op", "parts": "parts", "parts_out": "parts_out",
                     "lots": "lots", "measurements": "meas", "message_id": "msg_id", "model": "model", "work_order": "wo"},
          "part_keys": {"item": "pn", "serial": "sn"}, "lot_keys": {"item": "pn", "lot": "lot", "qty": "qty"},
          "time": {"format": "%Y-%m-%d %H:%M:%S", "tz": "Asia/Taipei"},
          "result_map": {"OK": "PASS", "NG": "FAIL", "RWK": "REWORK", "SCR": "SCRAP"},
          "defect_codes": {"T601": "EOL-MOTOR"}, "defect_text": {"馬達異音": "EOL-MOTOR"},
          "stations": {"S10": "S10", "S20": "S20", "S60": "S60", "S80": "S80"}, "models": {"FAP-LV1-SL": "LV1-SLATE"}}
    v2 = json.loads(json.dumps(v1))
    v2["fields"].update({"serial": "serial", "station": "stationCode", "line": "lineId", "event_time": "eventTime",
                         "result": "result", "defect_code": "defectCode", "defect_text": "defectDesc", "operator": "operatorId",
                         "parts": "components", "parts_out": "componentsRemoved", "lots": "lotConsumption",
                         "measurements": "measurements", "message_id": "messageId", "model": "modelNo", "work_order": "workOrder"})
    v2["part_keys"] = {"item": "partNo", "serial": "serialNo"}
    v2["lot_keys"] = {"item": "partNo", "lot": "lotNo", "qty": "qty"}
    v2["time"] = {"format": "iso8601", "tz": "Asia/Taipei"}
    v2["result_map"] = {"PASS": "PASS", "FAIL": "FAIL", "REWORK": "REWORK", "SCRAP": "SCRAP"}
    conn.executemany("INSERT INTO mapping_version VALUES (?,?,?,?,?)", [
        ("CM_MES", "v1", "2026-01-01T00:00:00Z", json.dumps(v1, ensure_ascii=False), "v1"),
        ("CM_MES", "v2", "2026-08-27T06:45:00Z", json.dumps(v2, ensure_ascii=False), "v2")])


_BUILT = {}
_TEMP_DIRS = []


def temp_dir(prefix):
    """A scratch directory removed when the test process exits (each built database is about 55 MB)."""
    d = tempfile.mkdtemp(prefix=prefix)
    _TEMP_DIRS.append(d)
    return d


@atexit.register
def _remove_temp_dirs():
    for d in _TEMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)

# the story is anchored to as_of, so the suite must pass for any dataset date (OPS_TEST_AS_OF=YYYY-MM-DD)
AS_OF = os.environ.get("OPS_TEST_AS_OF", "2026-09-26")


def built_db():
    """One full simulated database per test process (about 8 seconds), in a temp dir."""
    if "path" not in _BUILT:
        from ops.generate import build_database
        d = temp_dir("opsos-test-")
        path = Path(d) / "ops.db"
        build_database(path, as_of=AS_OF, seed=7, verbose=False)
        _BUILT["path"] = path
    return _BUILT["path"]


def copy_of_built():
    src = built_db()
    d = temp_dir("opsos-copy-")
    dst = Path(d) / "ops.db"
    shutil.copy(src, dst)
    return dst
