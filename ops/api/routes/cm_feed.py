"""CM Feed: health, incidents, quarantine and a message-level inspector for the
CM's MES integration (the CM is offshore in Taichung, UTC+8)."""
import json

from ...db import now, q, q1, val
from ..router import HttpError, get

SERIAL = "COALESCE(json_extract(payload,'$.sn'), json_extract(payload,'$.serial'))"
STATION = "COALESCE(json_extract(payload,'$.stn_cd'), json_extract(payload,'$.stationCode'))"
RESULT = "COALESCE(json_extract(payload,'$.rslt'), json_extract(payload,'$.result'))"
EVT = "COALESCE(json_extract(payload,'$.evt_ts'), json_extract(payload,'$.eventTime'))"
LINE = "COALESCE(json_extract(payload,'$.line'), json_extract(payload,'$.lineId'))"
LAT_BUCKETS = [(15, "< 15 s"), (30, "15–30 s"), (60, "30–60 s"), (120, "1–2 min"), (300, "2–5 min"),
               (1800, "5–30 min"), (7200, "30 min–2 h"), (10 ** 9, "> 2 h")]


def _pct(sorted_vals, p):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def _note_group(status, note):
    note = note or ""
    if status == "WARN":
        if note.startswith("TZ_CORRECTED"):
            return "Timestamp corrected (local time labeled UTC)"
        if "Chinese text" in note:
            return "Defect mapped from Chinese description"
        if "provisional" in note:
            return "Component unknown (no ASN yet), provisional"
        if "unmapped defect" in note:
            return "Unmapped defect code"
        return "Other warning"
    if status == "QUARANTINED":
        if note.startswith("unknown station"):
            return "Unknown station (not in master or mapping)"
        if note.startswith("schema mismatch"):
            return "Schema mismatch"
        return "Other"
    if status == "DUPLICATE":
        return "Duplicate of an ingested event"
    if status == "REPLAYED":
        return "Replayed after mapping v2"
    return "Normalized"


def _fix_for(note):
    note = note or ""
    if note.startswith("unknown station S65"):
        return ("Add S65 (rework bay) to station master data, publish mapping v2.1 with the S65 alias, then replay. "
                "This is the Closed Loop decision for the data loop, and it goes through change review first.")
    if note.startswith("schema mismatch"):
        return "Publish a mapping version for the new payload shape and replay the quarantine."
    if note.startswith("invalid JSON"):
        return "Ask the CM to resend; the gateway kept the original bytes."
    return "Review the payload against the active mapping."


@get(r"^/api/cm-feed/overview$")
def overview(req):
    conn = req.conn
    now_s = now(conn)
    # --- volumes -------------------------------------------------------------------
    total = val(conn, "SELECT COUNT(*) FROM raw_cm_mes_event")
    last_recv = val(conn, "SELECT MAX(received_at) FROM raw_cm_mes_event")
    n24 = val(conn, "SELECT COUNT(*) FROM raw_cm_mes_event WHERE julianday(received_at) >= julianday(?) - 1", (now_s,))
    n7 = val(conn, "SELECT COUNT(*) FROM raw_cm_mes_event WHERE julianday(received_at) >= julianday(?) - 7", (now_s,))
    last_day = q1(conn, """SELECT date(received_at, '+8 hours') d, COUNT(*) n FROM raw_cm_mes_event
                           GROUP BY d ORDER BY d DESC LIMIT 1""")
    status = q(conn, "SELECT ingest_status, ingest_note, COUNT(*) n FROM raw_cm_mes_event GROUP BY ingest_status, ingest_note")
    counts = {}
    groups = {}
    for r in status:
        counts[r["ingest_status"]] = counts.get(r["ingest_status"], 0) + r["n"]
        g = (r["ingest_status"], _note_group(r["ingest_status"], r["ingest_note"]))
        groups[g] = groups.get(g, 0) + r["n"]
    tz_fixed = sum(n for (s, g), n in groups.items() if g.startswith("Timestamp corrected"))

    # --- latency (received - event), from normalized events -----------------------
    lat = [r["s"] for r in q(conn, """
        SELECT (julianday(r.received_at) - julianday(se.event_ts)) * 86400.0 AS s
        FROM raw_cm_mes_event r JOIN station_event se ON se.raw_id = r.raw_id
        WHERE julianday(r.received_at) >= julianday(?) - 14""", (now_s,))]
    lat.sort()
    hist = [0] * len(LAT_BUCKETS)
    for s in lat:
        for i, (lim, _) in enumerate(LAT_BUCKETS):
            if s < lim:
                hist[i] += 1
                break
    hourly = q(conn, """
        SELECT substr(r.received_at, 1, 13) || ':00:00Z' AS hour, COUNT(*) n,
               SUM(CASE WHEN r.ingest_status IN ('QUARANTINED') THEN 1 ELSE 0 END) quarantined,
               SUM(CASE WHEN r.ingest_status = 'DUPLICATE' THEN 1 ELSE 0 END) dup
        FROM raw_cm_mes_event r
        WHERE julianday(r.received_at) >= julianday(?) - 14
        GROUP BY hour ORDER BY hour""", (now_s,))
    late_hourly = {r["hour"]: r["n"] for r in q(conn, """
        SELECT substr(r.received_at, 1, 13) || ':00:00Z' AS hour, COUNT(*) n
        FROM raw_cm_mes_event r JOIN station_event se ON se.raw_id = r.raw_id
        WHERE julianday(r.received_at) >= julianday(?) - 14
          AND (julianday(r.received_at) - julianday(se.event_ts)) * 1440 > 30
        GROUP BY hour""", (now_s,))}
    for h in hourly:
        h["late"] = late_hourly.get(h["hour"], 0)

    # --- mappings ------------------------------------------------------------------
    maps = q(conn, "SELECT version, effective_from, notes, spec_json FROM mapping_version WHERE source='CM_MES' ORDER BY effective_from")
    specs = [(m["version"], json.loads(m["spec_json"])) for m in maps]
    diff = []
    if len(specs) >= 2:
        (va, a), (vb, b) = specs[-2], specs[-1]
        for field in sorted(set(a["fields"]) | set(b["fields"]), key=lambda f: list(b["fields"]).index(f) if f in b["fields"] else 99):
            diff.append({"field": field, "a": a["fields"].get(field), "b": b["fields"].get(field),
                         "changed": a["fields"].get(field) != b["fields"].get(field)})
        diff.append({"field": "event time format", "a": a["time"]["format"] + " (" + a["time"]["tz"] + ")",
                     "b": b["time"]["format"] + " (" + b["time"]["tz"] + ")", "changed": a["time"] != b["time"]})
        diff.append({"field": "result codes", "a": ", ".join(f"{k}→{v}" for k, v in a["result_map"].items()),
                     "b": ", ".join(f"{k}→{v}" for k, v in b["result_map"].items()),
                     "changed": a["result_map"] != b["result_map"]})
    mappings = [{"version": m["version"], "effective_from": m["effective_from"], "notes": m["notes"],
                 "stations": sorted(json.loads(m["spec_json"]).get("stations", {})),
                 "defect_codes": len(json.loads(m["spec_json"]).get("defect_codes", {})),
                 "defect_text": json.loads(m["spec_json"]).get("defect_text", {})} for m in maps]

    # --- incidents, derived from the data ------------------------------------------
    incidents = []
    tz = q1(conn, """SELECT COUNT(*) n, MIN(received_at) a, MAX(received_at) b FROM raw_cm_mes_event
                     WHERE ingest_note LIKE 'TZ_CORRECTED%'""")
    if tz and tz["n"]:
        incidents.append({"kind": "TZ", "tone": "warning", "start": tz["a"], "end": tz["b"], "messages": tz["n"],
                          "title": "CM gateway labeled Taipei local time as UTC",
                          "detail": "Timestamps arrived 8 hours in the future relative to receipt. The normalizer "
                                    "caught every one (event time > receipt + 5 min), reinterpreted it as Asia/Taipei "
                                    "and flagged it WARN instead of silently shifting station history.",
                          "outcome": f"{tz['n']:,} messages corrected, 0 lost"})
    dup = q1(conn, "SELECT COUNT(*) n, MIN(received_at) a, MAX(received_at) b FROM raw_cm_mes_event WHERE ingest_status='DUPLICATE'")
    if dup and dup["n"]:
        incidents.append({"kind": "RETRY", "tone": "warning", "start": dup["a"], "end": dup["b"], "messages": dup["n"],
                          "title": "Gateway retry storm resent three hours of events",
                          "detail": "The resends carried new message ids (-R1), so id-based dedupe would have "
                                    "double-counted output and FPY. The normalizer dedupes on (serial, station, "
                                    "event time, result).",
                          "outcome": f"{dup['n']:,} duplicates suppressed"})
    rep = q1(conn, "SELECT COUNT(*) n, MIN(received_at) a, MAX(received_at) b FROM raw_cm_mes_event WHERE ingest_status='REPLAYED'")
    if rep and rep["n"]:
        v2 = next((m for m in maps if m["version"] == "v2"), None)
        incidents.append({"kind": "SCHEMA", "tone": "serious", "start": rep["a"], "end": v2["effective_from"] if v2 else rep["b"],
                          "messages": rep["n"], "title": "CM MES upgrade changed the payload schema",
                          "detail": "camelCase keys, ISO-8601 with offset, English result codes. Under mapping v1 "
                                    "those messages failed validation and were quarantined, not dropped. Mapping "
                                    "v2 (change review CR-0007) went live and the quarantine replayed in arrival order.",
                          "outcome": f"{rep['n']:,} messages replayed; 0 lost"})
    outage = q1(conn, """
        SELECT substr(r.received_at, 1, 13) h, COUNT(*) n, MIN(se.event_ts) a, MAX(se.event_ts) b, MIN(r.received_at) rcv
        FROM raw_cm_mes_event r JOIN station_event se ON se.raw_id = r.raw_id
        WHERE (julianday(r.received_at) - julianday(se.event_ts)) * 1440 > 60
        GROUP BY h ORDER BY n DESC LIMIT 1""")
    if outage and outage["n"] >= 20:
        incidents.append({"kind": "OUTAGE", "tone": "warning", "start": outage["a"], "end": outage["rcv"],
                          "messages": outage["n"], "title": "CM network outage: events buffered, then delivered in a burst",
                          "detail": "Events produced during the outage reached us hours late, in one burst when the "
                                    "link came back. Because the normalizer keys on event time, not arrival time, "
                                    "WIP, output and FPY for those hours were rebuilt correctly.",
                          "outcome": f"{outage['n']:,} late events, all normalized"})
    s65 = q1(conn, """SELECT COUNT(*) n, MIN(received_at) a, MAX(received_at) b FROM raw_cm_mes_event
                      WHERE ingest_status='QUARANTINED' AND ingest_note LIKE 'unknown station%'""")
    if s65 and s65["n"]:
        incidents.append({"kind": "S65", "tone": "critical", "start": s65["a"], "end": None, "messages": s65["n"],
                          "title": "Unknown station S65: the CM opened a rework bay without telling us",
                          "detail": "EOL rework now reports from S65, which isn't in our station master or mapping. "
                                    "The events sit in quarantine, so any drive units swapped at S65 are missing from "
                                    "the as-built record. That is a recall-scope hole.",
                          "outcome": "Open: waiting on the Closed Loop data decision"})
    prov = val(conn, "SELECT COUNT(*) FROM unit WHERE origin='PROVISIONAL'")
    asn_fixed = q1(conn, "SELECT COUNT(*) n, MAX(received_at) b, MAX(ingest_note) note FROM raw_supplier_asn WHERE ingest_status='WARN'")
    if asn_fixed and asn_fixed["n"]:
        incidents.append({"kind": "ASN_LATE", "tone": "good", "start": asn_fixed["b"], "end": asn_fixed["b"],
                          "messages": asn_fixed["n"],
                          "title": "Late ASN: drive units installed before the supplier's ASN arrived",
                          "detail": "The CM scanned drive-unit serials at S20 two days before Tainan Motion "
                                    "transmitted the ASN. The normalizer created provisional units rather than "
                                    "rejecting the events, and the ASN resolved them when it landed.",
                          "outcome": "Resolved automatically"})
    if prov:
        incidents.append({"kind": "PROVISIONAL", "tone": "serious", "start": val(conn, """
                              SELECT MIN(g.installed_at) FROM genealogy g JOIN unit u ON u.serial = g.child_serial
                              WHERE u.origin='PROVISIONAL'"""), "end": None, "messages": prov,
                          "title": f"{prov} drive units installed with no supplier ASN at all",
                          "detail": "An emergency hand-carry to the CM skipped the ASN. These units have no motor, "
                                    "controller or magnet-lot genealogy, so a magnet-lot recall would miss the "
                                    "vehicles they sit in.",
                          "outcome": "Open: ASN requested from Tainan Motion"})
    incidents.sort(key=lambda i: i["start"] or "", reverse=True)

    # --- quarantine queue ----------------------------------------------------------------
    quarantine = []
    for r in q(conn, f"""SELECT raw_id, received_at, ingest_note, mapping_version, payload, {SERIAL} serial,
                                {STATION} station, {LINE} line
                         FROM raw_cm_mes_event WHERE ingest_status='QUARANTINED' ORDER BY received_at DESC LIMIT 50"""):
        quarantine.append({"raw_id": r["raw_id"], "received_at": r["received_at"], "note": r["ingest_note"],
                           "mapping_version": r["mapping_version"], "serial": r["serial"], "station": r["station"],
                           "line": r["line"], "payload": r["payload"], "fix": _fix_for(r["ingest_note"])})

    provisional = q(conn, """
        SELECT u.serial, u.item_id, g.parent_serial, g.installed_at, s.code station, g.station_id
        FROM unit u JOIN genealogy g ON g.child_serial = u.serial AND g.removed_at IS NULL
        LEFT JOIN station s ON s.station_id = g.station_id
        WHERE u.origin = 'PROVISIONAL' ORDER BY g.installed_at DESC""")
    asn_warn = q(conn, """SELECT raw_id, supplier_id, received_at, ingest_note,
                                 json_extract(payload, '$.asn_no') asn_no, json_extract(payload, '$.ship_date') ship_date
                          FROM raw_supplier_asn WHERE ingest_status='WARN' ORDER BY received_at DESC""")

    # --- Excel (email) vs MES reconciliation ---------------------------------------------
    recon = q(conn, """
        WITH rep AS (SELECT report_date, line, SUM(actual) excel, SUM(planned) planned, MAX(source_ref) ref
                     FROM cm_output_report GROUP BY report_date, line),
             mes AS (SELECT date(se.event_ts, '+8 hours') d, u.line, COUNT(*) n
                     FROM station_event se JOIN station s ON s.station_id = se.station_id
                     JOIN unit u ON u.serial = se.serial
                     WHERE s.code = 'S80' AND se.result = 'PASS' AND s.site_id = 'CM-TXG'
                       AND se.event_ts >= (SELECT date(MIN(report_date), '-1 day') FROM cm_output_report)
                     GROUP BY d, u.line)
        SELECT rep.report_date, rep.line, rep.planned, rep.excel, COALESCE(mes.n, 0) mes, rep.ref,
               (SELECT a.raw_id FROM raw_attachment a WHERE 'raw_attachment:' || a.attachment_id = rep.ref) email_id
        FROM rep LEFT JOIN mes ON mes.d = rep.report_date AND mes.line = rep.line
        ORDER BY rep.report_date DESC, rep.line""")
    stock_recon = q(conn, """
        SELECT r.report_date, r.item_id, r.on_hand AS excel,
               (SELECT COUNT(*) FROM unit u WHERE u.item_id = r.item_id AND u.status = 'COMPONENT'
                  AND u.location_site_id = 'CM-TXG') AS system_now
        FROM cm_stock_report r WHERE r.report_date = (SELECT MAX(report_date) FROM cm_stock_report)""")
    runs = q(conn, "SELECT * FROM ingest_run WHERE source IN ('CM_MES','SUPPLIER_ASN') ORDER BY run_id DESC LIMIT 10")

    return {
        "now": now_s,
        "kpis": {"total": total, "last_received": last_recv, "n24": n24, "n7": n7,
                 "last_day": last_day["d"] if last_day else None, "last_day_n": last_day["n"] if last_day else 0,
                 "p50": _pct(lat, 0.5), "p95": _pct(lat, 0.95), "quarantined": counts.get("QUARANTINED", 0),
                 "duplicates": counts.get("DUPLICATE", 0), "replayed": counts.get("REPLAYED", 0),
                 "tz_fixed": tz_fixed, "warn": counts.get("WARN", 0), "ok": counts.get("OK", 0),
                 "provisional": prov, "active_mapping": maps[-1]["version"] if maps else None},
        "status_groups": [{"status": s, "group": g, "n": n} for (s, g), n in sorted(groups.items(), key=lambda x: -x[1])],
        "latency_hist": [{"bucket": label, "n": hist[i]} for i, (_, label) in enumerate(LAT_BUCKETS)],
        "hourly": hourly,
        "mappings": mappings, "mapping_diff": diff,
        "incidents": incidents,
        "quarantine": quarantine,
        "provisional": provisional, "asn_resolved": asn_warn,
        "recon": recon, "stock_recon": stock_recon,
        "runs": runs,
    }


@get(r"^/api/cm-feed/messages$")
def messages(req):
    conn = req.conn
    status = req.arg("status")
    term = (req.arg("q") or "").strip()
    limit = min(req.arg("limit", 100, int), 500)
    offset = req.arg("offset", 0, int)
    where, args = [], []
    if status:
        where.append("ingest_status = ?")
        args.append(status.upper())
    if term:
        where.append(f"({SERIAL} LIKE ? OR ingest_note LIKE ?)")
        args += [f"%{term}%", f"%{term}%"]
    w = ("WHERE " + " AND ".join(where)) if where else ""
    rows = q(conn, f"""SELECT raw_id, received_at, ingest_status, ingest_note, mapping_version, {SERIAL} serial,
                              {STATION} station, {RESULT} result, {EVT} event_time, {LINE} line
                       FROM raw_cm_mes_event {w} ORDER BY received_at DESC, raw_id DESC LIMIT ? OFFSET ?""",
             args + [limit, offset])
    total = val(conn, f"SELECT COUNT(*) FROM raw_cm_mes_event {w}", args)
    return {"rows": rows, "total": total, "offset": offset, "limit": limit}


@get(r"^/api/cm-feed/message/(\d+)$")
def message(req):
    conn = req.conn
    raw_id = int(req.params[0])
    r = q1(conn, "SELECT * FROM raw_cm_mes_event WHERE raw_id = ?", (raw_id,))
    if not r:
        raise HttpError(404, f"raw message {raw_id} not found")
    try:
        payload = json.loads(r["payload"])
    except json.JSONDecodeError:
        payload = None
    ver = r["mapping_version"]
    if r["ingest_status"] == "REPLAYED":
        ver = q1(conn, "SELECT version FROM mapping_version WHERE source='CM_MES' ORDER BY effective_from DESC LIMIT 1")["version"]
    spec_row = q1(conn, "SELECT spec_json FROM mapping_version WHERE source='CM_MES' AND version = ?", (ver,)) if ver else None
    spec = json.loads(spec_row["spec_json"]) if spec_row else None
    events = q(conn, """SELECT se.*, s.code, s.name station_name FROM station_event se
                        JOIN station s ON s.station_id = se.station_id WHERE se.raw_id = ?""", (raw_id,))
    dup_of = None
    if r["ingest_status"] == "DUPLICATE" and r["ingest_note"]:
        import re
        m = re.search(r"raw_id (\d+)", r["ingest_note"])
        if m:
            dup_of = int(m.group(1))
            events = q(conn, """SELECT se.*, s.code, s.name station_name FROM station_event se
                                JOIN station s ON s.station_id = se.station_id WHERE se.raw_id = ?""", (dup_of,))
    for e in events:
        e["measurements"] = json.loads(e["measurements"]) if e["measurements"] else None
    edges = []
    if events:
        e = events[0]
        edges = q(conn, """SELECT genealogy_id, child_serial, child_lot_id, child_item_id, qty, relation, position,
                                  installed_at, removed_at, removal_reason
                           FROM genealogy WHERE parent_serial = ? AND station_id = ?
                             AND (installed_at = ? OR removed_at = ?)""",
                  (e["serial"], e["station_id"], e["event_ts"], e["event_ts"]))
    # field-by-field: raw key -> raw value -> canonical value
    fields = []
    unit = None
    if payload is not None:
        sn = payload.get("sn") or payload.get("serial")
        unit = q1(conn, "SELECT line, item_id, wo_id FROM unit WHERE serial = ?", (sn,)) if sn else None
    if spec and payload is not None:
        canon = dict(events[0]) if events else {}
        if unit and events:
            canon.update({"unit.line": unit["line"], "unit.item_id": unit["item_id"], "unit.wo_id": unit["wo_id"]})
        mapping = [("serial", "serial"), ("station", "station_id"), ("line", "unit.line"), ("event_time", "event_ts"),
                   ("result", "result"), ("defect_code", "defect_code"), ("defect_text", "defect_code"),
                   ("operator", "operator_id"), ("model", "unit.item_id"), ("work_order", "unit.wo_id")]
        for key, col in mapping:
            rk = spec["fields"].get(key)
            if rk is None:
                continue
            present = rk in payload
            fields.append({"canonical": key, "raw_key": rk, "raw_value": payload.get(rk) if present else None,
                           "present": present, "column": col, "value": canon.get(col) if (col and canon) else None})
    steps = q(conn, "SELECT seq, step, status, at, duration_ms, detail FROM ingest_step WHERE source_ref = ? ORDER BY seq",
              (f"raw_cm_mes_event:{raw_id}",))
    serial = (payload or {}).get("sn") or (payload or {}).get("serial")
    return {"raw": {k: r[k] for k in ("raw_id", "source_system", "received_at", "ingest_status", "ingest_note",
                                      "mapping_version", "run_id")},
            "payload": r["payload"], "serial": serial, "mapping_used": ver, "fields": fields,
            "events": events, "duplicate_of": dup_of, "edges": edges, "steps": steps}
