"""MES analytics shared by the Production and CM Feed pages: line definitions,
local production days, first-pass and rolled-throughput yield, OEE, WIP position.

Production days are local to the plant: Taichung runs on UTC+8 (no DST), Fremont
on Pacific time. Every shift in the dataset falls inside PDT (UTC-7), so a fixed
offset per site is exact for this window.
"""
from ..db import q, q1

LINES = {
    "L1": {"id": "L1", "site_id": "CM-TXG", "line": "L1", "final": "S80", "offset": "+8 hours", "tz": "Asia/Taipei",
           "label": "CM Line 1", "place": "Formosa Assembly Partners · Taichung", "kind": "VEHICLE"},
    "L2": {"id": "L2", "site_id": "CM-TXG", "line": "L2", "final": "S80", "offset": "+8 hours", "tz": "Asia/Taipei",
           "label": "CM Line 2", "place": "Formosa Assembly Partners · Taichung", "kind": "VEHICLE"},
    "P1": {"id": "P1", "site_id": "OEM-FRE", "line": "P1", "final": "P60", "offset": "-7 hours",
           "tz": "America/Los_Angeles", "label": "Pack Line P1", "place": "OEM · Fremont", "kind": "PACK"},
}


def line_def(line_id):
    return LINES.get(line_id or "L1", LINES["L1"])


def stations(conn, ld):
    return q(conn, "SELECT station_id, code, seq, name, kind, std_cycle_min, parallel_fixtures FROM station "
                   "WHERE site_id=? AND line=? ORDER BY seq", (ld["site_id"], ld["line"]))


def capacity(conn, ld, as_of):
    return q1(conn, "SELECT * FROM line_capacity WHERE site_id=? AND line=? AND effective_from<=? "
                    "ORDER BY effective_from DESC LIMIT 1", (ld["site_id"], ld["line"], as_of))


def daily_output(conn, ld, since):
    """Final-station passes per local production day."""
    rows = q(conn, f"""
        SELECT date(se.event_ts, '{ld['offset']}') AS day, COUNT(*) AS n
        FROM station_event se
        WHERE se.station_id = ? AND se.result = 'PASS' AND se.event_ts >= ?
        GROUP BY day ORDER BY day""", (f"{_prefix(ld)}-{ld['final']}", since))
    return {r["day"]: r["n"] for r in rows}


def daily_plan(conn, ld, since, until):
    rows = q(conn, "SELECT plan_date AS day, SUM(qty) AS n FROM build_plan WHERE site_id=? AND line=? "
                   "AND plan_date BETWEEN ? AND ? GROUP BY plan_date ORDER BY plan_date",
             (ld["site_id"], ld["line"], since, until))
    return {r["day"]: r["n"] for r in rows}


def station_fpy(conn, ld, since):
    """First-pass yield per station per local day: a unit passes first time if its
    first event at that station is a PASS. Units whose first visit predates `since`
    are excluded, not re-counted."""
    return q(conn, f"""
        WITH firsts AS (
          SELECT se.serial, se.station_id, MIN(se.event_ts) AS ts
          FROM station_event se
          WHERE se.station_id LIKE ?
          GROUP BY se.serial, se.station_id
          HAVING MIN(se.event_ts) >= ?
        )
        SELECT s.code, date(f.ts, '{ld['offset']}') AS day, COUNT(*) AS units,
               SUM(CASE WHEN se.result = 'PASS' THEN 1 ELSE 0 END) AS first_pass
        FROM firsts f
        JOIN station_event se ON se.serial = f.serial AND se.station_id = f.station_id AND se.event_ts = f.ts
        JOIN station s ON s.station_id = f.station_id
        GROUP BY s.code, day ORDER BY day, s.seq""", (f"{_prefix(ld)}-%", since))


def _prefix(ld):
    return ("TXG-" if ld["site_id"] == "CM-TXG" else "FRE-") + ld["line"]


def station_id(ld, code):
    return f"{_prefix(ld)}-{code}"


def wip(conn, ld):
    """Units started but not through the final station, positioned where they wait:
    after a PASS they queue at the next station; after a FAIL/REWORK they sit in rework."""
    sts = stations(conn, ld)
    order = [s["code"] for s in sts]
    rows = q(conn, """
        SELECT u.serial, u.item_id, s.code, se.result, se.event_ts, se.defect_code
        FROM unit u
        JOIN station_event se ON se.serial = u.serial
             AND se.event_ts = (SELECT MAX(x.event_ts) FROM station_event x WHERE x.serial = u.serial)
        JOIN station s ON s.station_id = se.station_id
        WHERE u.status = 'WIP' AND u.build_site_id = ? AND u.line = ?""", (ld["site_id"], ld["line"]))
    out = []
    for r in rows:
        if r["result"] == "PASS":
            i = order.index(r["code"]) if r["code"] in order else -1
            pos = order[i + 1] if 0 <= i < len(order) - 1 else r["code"]
            state = "queued"
        else:
            pos, state = r["code"], "rework"
        out.append({**r, "position": pos, "state": state})
    return out
