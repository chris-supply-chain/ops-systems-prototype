"""Shared ingest plumbing: run bookkeeping, step traces, time parsing, promise updates."""
import datetime as dt
import json
import time
from zoneinfo import ZoneInfo

UTC = dt.timezone.utc

STATUS_RANK = {"WIP": 0, "BUILT": 1, "IN_TRANSIT": 2, "AT_3PL": 3, "ALLOCATED": 4, "SHIPPED": 5, "DELIVERED": 6}


def parse_iso(s):
    """Accepts '...Z', '+08:00' offsets, or naive (treated as UTC)."""
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    t = dt.datetime.fromisoformat(s)
    if t.tzinfo is None:
        t = t.replace(tzinfo=UTC)
    return t.astimezone(UTC)


def local_to_utc(text, fmt, tz):
    return dt.datetime.strptime(text, fmt).replace(tzinfo=ZoneInfo(tz)).astimezone(UTC)


class Run:
    """One ingest_run row plus counters; steps are written to ingest_step."""

    def __init__(self, conn, source, started_at, mapping_version=None):
        self.conn = conn
        self.source = source
        self.counts = {"in": 0, "ok": 0, "warn": 0, "quarantined": 0, "duplicate": 0}
        cur = conn.execute("INSERT INTO ingest_run(source, started_at, mapping_version) VALUES (?,?,?)",
                           (source, started_at, mapping_version))
        self.run_id = cur.lastrowid
        self.t0 = time.perf_counter()

    def step(self, source_ref, seq, step, status, at, detail=None, ms=None):
        self.conn.execute(
            "INSERT INTO ingest_step(source, source_ref, seq, step, status, at, duration_ms, detail) VALUES (?,?,?,?,?,?,?,?)",
            (self.source, source_ref, seq, step, status, at, ms, json.dumps(detail, ensure_ascii=False)
             if isinstance(detail, (dict, list)) else detail))

    def finish(self, finished_at, notes=None):
        c = self.counts
        self.conn.execute(
            "UPDATE ingest_run SET finished_at=?, rows_in=?, rows_ok=?, rows_warn=?, rows_quarantined=?, rows_duplicate=?, notes=?"
            " WHERE run_id=?", (finished_at, c["in"], c["ok"], c["warn"], c["quarantined"], c["duplicate"], notes,
                                self.run_id))


def bump_status(conn, serial, status, location=None, keep_location=False):
    """Advance a unit's lifecycle status; never moves backwards."""
    row = conn.execute("SELECT status FROM unit WHERE serial=?", (serial,)).fetchone()
    if row is None:
        return False
    cur = row["status"]
    if cur in ("SCRAPPED", "RETURNED"):
        return False
    if STATUS_RANK.get(status, -1) > STATUS_RANK.get(cur, -1):
        if keep_location:
            conn.execute("UPDATE unit SET status=? WHERE serial=?", (status, serial))
        else:
            conn.execute("UPDATE unit SET status=?, location_site_id=? WHERE serial=?", (status, location, serial))
        return True
    return False


def apply_promise(conn, po_id, line_no, promise_date, qty, channel, raw_ref, received_at, note=None):
    """Record a supplier promise for a PO line. Returns (ok, message)."""
    line = conn.execute("SELECT qty, promise_date, confirmed_at FROM po_line WHERE po_id=? AND line_no=?",
                        (po_id, line_no)).fetchone()
    if line is None:
        return False, f"PO line {po_id}-{line_no} not found"
    if line["promise_date"] == promise_date and line["confirmed_at"]:
        return True, "no change"
    conn.execute("INSERT INTO po_promise_history(po_id, line_no, promise_date, promise_qty, recorded_at, channel, raw_ref, note)"
                 " VALUES (?,?,?,?,?,?,?,?)", (po_id, line_no, promise_date, qty, received_at, channel, raw_ref, note))
    status = "CONFIRMED" if (qty is None or qty >= line["qty"]) else "PARTIAL"
    conn.execute("UPDATE po_line SET promise_date=?, confirm_status=?, confirmed_at=COALESCE(confirmed_at, ?)"
                 " WHERE po_id=? AND line_no=?", (promise_date, status, received_at, po_id, line_no))
    return True, "promise recorded" if line["promise_date"] is None else f"promise moved {line['promise_date']} -> {promise_date}"
