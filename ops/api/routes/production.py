"""Production & Yield (MES): one payload per line with WIP, FPY/RTY, output vs
plan, line balance, OEE, downtime and defect Paretos, and work orders."""
import json

from ...db import as_of, now, q, q1, val
from ...logic import mes
from ..router import HttpError, get


TW_HOLIDAY_NAMES = {"2026-06-19": "Dragon Boat Festival", "2026-09-25": "Mid-Autumn Festival",
                    "2026-10-09": "National Day bridge holiday", "2026-10-10": "National Day"}


def _minus(day, n):
    return f"date('{day}', '-{n} days')"


@get(r"^/api/production/overview$")
def overview(req):
    conn = req.conn
    ld = mes.line_def(req.arg("line", "L1"))
    if req.arg("line") and req.arg("line") not in mes.LINES:
        raise HttpError(400, "line must be L1, L2 or P1")
    today = as_of(conn)
    since30 = val(conn, f"SELECT {_minus(today, 30)}")
    since45 = val(conn, f"SELECT {_minus(today, 45)}")
    cap = mes.capacity(conn, ld, today)
    sts = mes.stations(conn, ld)

    # --- output vs plan (local production days) -------------------------------
    out = mes.daily_output(conn, ld, since45)
    plan = mes.daily_plan(conn, ld, since45, today)
    days = sorted(set(out) | set(plan))
    days = [d for d in days if d <= today][-30:]
    prod_days = [d for d in days if out.get(d)]
    last_day = prod_days[-1] if prod_days else None
    prev_day = prod_days[-2] if len(prod_days) > 1 else None
    output_vs_plan = [{"day": d, "plan": plan.get(d, 0), "actual": out.get(d, 0)} for d in days]

    # --- station FPY + RTY ------------------------------------------------------
    fpy_rows = mes.station_fpy(conn, ld, since30 + "T00:00:00Z")
    codes = [s["code"] for s in sts]
    fpy_trend = {c: [] for c in codes}
    agg7 = {c: [0, 0] for c in codes}
    last7 = set(prod_days[-7:])
    for r in fpy_rows:
        if r["code"] not in fpy_trend:
            continue
        fpy_trend[r["code"]].append({"day": r["day"], "fpy": r["first_pass"] / r["units"], "units": r["units"],
                                     "first_pass": r["first_pass"]})
        if r["day"] in last7:
            agg7[r["code"]][0] += r["first_pass"]
            agg7[r["code"]][1] += r["units"]
    station_fpy7 = []
    rty = 1.0
    for s in sts:
        fp, n = agg7[s["code"]]
        f = fp / n if n else None
        if f is not None:
            rty *= f
        station_fpy7.append({"code": s["code"], "name": s["name"], "fpy": f, "units": n, "fails": n - fp})

    # --- unit-level first-pass yield on the last production day -----------------
    final_id = mes.station_id(ld, ld["final"])
    fpy_last = None
    if last_day:
        r = q1(conn, f"""
            SELECT COUNT(*) n,
                   SUM(CASE WHEN NOT EXISTS (SELECT 1 FROM station_event f WHERE f.serial = se.serial
                                             AND f.result IN ('FAIL','REWORK')) THEN 1 ELSE 0 END) clean
            FROM station_event se
            WHERE se.station_id = ? AND se.result = 'PASS' AND date(se.event_ts, '{ld['offset']}') = ?""",
               (final_id, last_day))
        fpy_last = (r["clean"] / r["n"]) if r and r["n"] else None

    # --- WIP --------------------------------------------------------------------
    wip_units = mes.wip(conn, ld)
    by_pos = {c: {"queued": [], "rework": []} for c in codes}
    for u in wip_units:
        by_pos.setdefault(u["position"], {"queued": [], "rework": []})[u["state"]].append(u)
    wip_by_station = [{"code": s["code"], "name": s["name"], "seq": s["seq"],
                       "queued": len(by_pos[s["code"]]["queued"]), "rework": len(by_pos[s["code"]]["rework"]),
                       "units": [{"serial": u["serial"], "item_id": u["item_id"], "state": u["state"],
                                  "last_event": u["event_ts"], "last_code": u["code"], "result": u["result"],
                                  "defect_code": u["defect_code"]} for u in by_pos[s["code"]]["queued"] + by_pos[s["code"]]["rework"]]}
                      for s in sts]

    # --- line balance -----------------------------------------------------------
    balance = []
    for s in sts:
        eff = (s["std_cycle_min"] or 0) / (s["parallel_fixtures"] or 1)
        balance.append({"code": s["code"], "name": s["name"], "std_cycle": s["std_cycle_min"],
                        "parallel": s["parallel_fixtures"], "effective": round(eff, 2)})
    bottleneck = max(balance, key=lambda b: b["effective"]) if balance else None
    takt = cap["takt_min"] if cap else None

    # --- OEE (per day, last 30 production days) -------------------------------
    dt_rows = q(conn, f"""
        SELECT date(start_ts, '{ld['offset']}') AS day, category, reason,
               (julianday(end_ts) - julianday(start_ts)) * 1440.0 AS minutes
        FROM downtime_event WHERE site_id = ? AND line = ? AND start_ts >= ?""",
                (ld["site_id"], ld["line"], since45 + "T00:00:00Z"))
    down_by_day = {}
    for r in dt_rows:
        if r["category"] != "PLANNED":
            down_by_day[r["day"]] = down_by_day.get(r["day"], 0) + r["minutes"]
    scrap = {r["day"]: r["n"] for r in q(conn, f"""
        SELECT date(se.event_ts, '{ld['offset']}') AS day, COUNT(*) n FROM station_event se
        WHERE se.station_id LIKE ? AND se.result = 'SCRAP' AND se.event_ts >= ? GROUP BY day""",
                                         (mes.station_id(ld, "%"), since45 + "T00:00:00Z"))}
    clean = {r["day"]: r["n"] for r in q(conn, f"""
        SELECT date(se.event_ts, '{ld['offset']}') AS day, COUNT(*) n FROM station_event se
        WHERE se.station_id = ? AND se.result = 'PASS' AND se.event_ts >= ?
          AND NOT EXISTS (SELECT 1 FROM station_event f WHERE f.serial = se.serial AND f.result IN ('FAIL','REWORK','SCRAP'))
        GROUP BY day""", (final_id, since45 + "T00:00:00Z"))}
    shift = cap["shift_minutes"] if cap else 570
    ideal = bottleneck["effective"] if bottleneck else None
    oee_daily = []
    for d in prod_days:
        planned = shift
        down = min(down_by_day.get(d, 0.0), planned)
        run = max(planned - down, 1.0)
        total = out.get(d, 0) + scrap.get(d, 0)
        a = run / planned
        p = min(1.0, (ideal * total) / run) if ideal else None
        qy = (clean.get(d, 0) / total) if total else None
        oee_daily.append({"day": d, "availability": a, "performance": p, "quality": qy,
                          "oee": a * p * qy if (p is not None and qy is not None) else None,
                          "downtime_min": round(down, 1), "units": total})
    w7 = oee_daily[-7:]
    def _avg(key):
        vals = [x[key] for x in w7 if x[key] is not None]
        return sum(vals) / len(vals) if vals else None
    planned7 = shift * len(w7)
    down7 = sum(x["downtime_min"] for x in w7)
    units7 = sum(x["units"] for x in w7)
    clean7 = sum(clean.get(x["day"], 0) for x in w7)
    oee = None
    if w7 and ideal:
        a7 = (planned7 - down7) / planned7
        p7 = min(1.0, ideal * units7 / max(planned7 - down7, 1))
        q7 = clean7 / units7 if units7 else None
        oee = {"availability": a7, "performance": p7, "quality": q7, "oee": a7 * p7 * q7 if q7 is not None else None,
               "planned_min": planned7, "downtime_min": round(down7, 1), "run_min": round(planned7 - down7, 1),
               "units": units7, "good_units": clean7, "ideal_cycle": ideal, "days": len(w7),
               "target": cap["oee_target"] if cap else None}

    # --- Paretos ------------------------------------------------------------------
    since30ts = since30 + "T00:00:00Z"
    downtime = [{"category": r["category"], "reason": r["reason"], "events": r["events"], "minutes": round(r["minutes"], 1)}
                for r in q(conn, """
        SELECT category, reason, COUNT(*) events, SUM((julianday(end_ts) - julianday(start_ts)) * 1440.0) minutes
        FROM downtime_event WHERE site_id = ? AND line = ? AND start_ts >= ?
        GROUP BY category, reason ORDER BY minutes DESC""", (ld["site_id"], ld["line"], since30ts))]
    downtime_all = [{"category": r["category"], "reason": r["reason"], "day": r["day"], "minutes": round(r["minutes"], 1)}
                    for r in q(conn, f"""
        SELECT category, reason, date(start_ts, '{ld['offset']}') day,
               (julianday(end_ts) - julianday(start_ts)) * 1440.0 minutes
        FROM downtime_event WHERE site_id = ? AND line = ? AND category IN ('MATERIAL','QUALITY','SYSTEMS')
        ORDER BY start_ts DESC LIMIT 20""", (ld["site_id"], ld["line"]))]
    defects = q(conn, """
        SELECT se.defect_code, d.description, d.category, d.default_responsible, s.code AS station, COUNT(*) fails
        FROM station_event se
        JOIN station s ON s.station_id = se.station_id
        LEFT JOIN defect_code d ON d.defect_code = se.defect_code
        WHERE se.station_id LIKE ? AND se.result = 'FAIL' AND se.event_ts >= ?
        GROUP BY se.defect_code, s.code ORDER BY fails DESC""", (mes.station_id(ld, "%"), since30ts))

    # --- work orders ------------------------------------------------------------------
    wos = q(conn, f"""
        SELECT wo_id, item_id, qty_planned, qty_started, qty_completed, qty_scrapped, sched_date, status, source
        FROM work_order WHERE site_id = ? AND line = ? AND sched_date >= {_minus(today, 6)}
        ORDER BY sched_date DESC, wo_id""", (ld["site_id"], ld["line"]))

    # --- what the system noticed ---------------------------------------------------
    insights = _insights(conn, ld, today, prod_days, output_vs_plan)

    lines = []
    for lid, d in mes.LINES.items():
        w = val(conn, "SELECT COUNT(*) FROM unit WHERE status='WIP' AND build_site_id=? AND line=?", (d["site_id"], d["line"]))
        lines.append({"id": lid, "label": d["label"], "place": d["place"], "wip": w, "tz": d["tz"]})

    return {
        "as_of": today, "now": now(conn),
        "line": {**ld, "shift_minutes": shift, "takt": takt, "rated_per_day": cap["rated_units_per_day"] if cap else None},
        "lines": lines,
        "kpis": {"last_day": last_day, "prev_day": prev_day,
                 "output_last": out.get(last_day, 0) if last_day else 0, "plan_last": plan.get(last_day, 0) if last_day else 0,
                 "output_prev": out.get(prev_day, 0) if prev_day else 0, "plan_prev": plan.get(prev_day, 0) if prev_day else 0,
                 "wip": len(wip_units), "wip_rework": sum(1 for u in wip_units if u["state"] == "rework"),
                 "fpy_last": fpy_last, "rty7": rty if any(a[1] for a in agg7.values()) else None,
                 "oee7": oee["oee"] if oee else None,
                 "output_spark": [out.get(d, 0) for d in prod_days[-12:]]},
        "output_vs_plan": output_vs_plan,
        "station_fpy7": station_fpy7,
        "fpy_trend": fpy_trend,
        "wip_by_station": wip_by_station,
        "balance": balance, "takt": takt, "bottleneck": bottleneck["code"] if bottleneck else None,
        "oee": oee, "oee_daily": oee_daily,
        "downtime": downtime, "downtime_notable": downtime_all,
        "defects": defects,
        "work_orders": wos,
        "insights": insights,
    }


def _insights(conn, ld, today, prod_days, ovp):
    out = []
    if ld["site_id"] == "CM-TXG":
        s60 = mes.station_id(ld, "S60")
        spike = q1(conn, """
            SELECT date(event_ts, '+8 hours') day, COUNT(*) n,
                   SUM(CASE WHEN result='FAIL' AND defect_code='EOL-FW' THEN 1 ELSE 0 END) fw
            FROM station_event WHERE station_id = ? AND event_ts >= date(?, '-60 days')
            GROUP BY day HAVING n >= 10 ORDER BY 1.0 * fw / n DESC LIMIT 1""", (s60, today))
        if spike and spike["fw"] and spike["fw"] / spike["n"] > 0.08:
            out.append({"tone": "serious", "title": f"EOL firmware-flash failures spiked on {spike['day']}",
                        "body": f"{spike['fw']} of {spike['n']} units failed EOL-FW at S60 "
                                f"({spike['fw'] / spike['n']:.0%}) against a normal rate under 1%. Root cause "
                                "(NCR-0023): the tester was re-imaged with the wrong flash package. It was "
                                "fixed the next morning and the golden image is now locked.",
                        "link": "#/quality", "link_label": "Open NCR-0023 in the Quality Loop"})
        mat = q(conn, """SELECT date(start_ts, '+8 hours') day, reason,
                                ROUND((julianday(end_ts) - julianday(start_ts)) * 1440) m
                         FROM downtime_event WHERE site_id = ? AND line = ? AND category = 'MATERIAL'
                         ORDER BY start_ts""", (ld["site_id"], ld["line"]))
        if mat:
            out.append({"tone": "warning", "title": f"Material stoppage: {mat[0]['reason']}",
                        "body": f"{len(mat)} shifts lost {int(sum(r['m'] for r in mat))} minutes between "
                                f"{mat[0]['day']} and {mat[-1]['day']}. The drive units are OEM-owned "
                                "consigned stock, so the OEM's own replenishment signal stopped the CM's line, "
                                "not the CM.", "link": "#/inventory?item=DU-C", "link_label": "Consigned stock at the CM"})
        q65 = val(conn, """SELECT COUNT(*) FROM raw_cm_mes_event WHERE ingest_status='QUARANTINED'
                           AND ingest_note LIKE 'unknown station S65 on ' || ? || '%'""", (ld["line"],))
        if q65:
            out.append({"tone": "serious", "title": f"{q65} rework events from the CM's new S65 rework bay are quarantined",
                        "body": "The CM started routing EOL rework through a station it never told us about. "
                                "Those events can't map to a known station, so these units' rework history "
                                "and any parts swapped at S65 are missing from the as-built record until the "
                                "mapping is fixed.", "link": "#/cm-feed", "link_label": "See the quarantine in CM Feed"})
        if prod_days and prod_days[-1] < today:
            gap = q(conn, """WITH RECURSIVE d(x) AS (SELECT date(?, '+1 day') UNION ALL
                                 SELECT date(x, '+1 day') FROM d WHERE x < ?)
                             SELECT x FROM d WHERE strftime('%w', x) NOT IN ('0','6')""", (prod_days[-1], today))
            names = [f"{g['x']} ({TW_HOLIDAY_NAMES.get(g['x'], 'no build plan')})" for g in gap]
            body = ("The CM runs Monday to Friday, day shift 08:00-17:30 Taipei. "
                    + (f"Weekday without production: {', '.join(names)}. " if names else "")
                    + "WIP stays on the line where the last shift left it and moves again when the next shift starts.")
            out.append({"tone": "info", "title": f"Last shift: {prod_days[-1]} (Taichung, UTC+8)", "body": body})
    else:
        seal = q(conn, """SELECT date(event_ts, '-7 hours') day, COUNT(*) n,
                                 SUM(CASE WHEN result='FAIL' AND defect_code='SEAL-LEAK' THEN 1 ELSE 0 END) f
                          FROM station_event WHERE station_id='FRE-P1-P40' GROUP BY day ORDER BY day""")
        eco = q1(conn, "SELECT effective_date FROM eco WHERE eco_id='ECO-0036'")
        if seal and eco:
            before = [r for r in seal if r["day"] < eco["effective_date"]][-25:]
            after = [r for r in seal if r["day"] >= eco["effective_date"]]
            fb = sum(r["f"] for r in before) / max(1, sum(r["n"] for r in before))
            fa = sum(r["f"] for r in after) / max(1, sum(r["n"] for r in after))
            out.append({"tone": "good", "title": f"Seal-leak failures at P40: {fb:.1%} before ECO-0036, {fa:.1%} after",
                        "body": "NCR-0019 traced cold-morning leak-test failures to EPDM gasket compression "
                                "set. The silicone gasket (ECO-0036) cut them; the remaining EPDM stock was "
                                "used up under DEV-0017.", "link": "#/quality", "link_label": "Quality Loop"})
        mat = q(conn, """SELECT date(start_ts, '-7 hours') day, category, reason,
                                ROUND((julianday(end_ts) - julianday(start_ts)) * 1440) m
                         FROM downtime_event WHERE site_id='OEM-FRE' AND category IN ('MATERIAL','QUALITY')
                         ORDER BY start_ts""")
        for r in mat:
            out.append({"tone": "warning", "title": f"{r['day']}: {r['reason']}",
                        "body": f"{int(r['m'])} minutes lost ({r['category'].lower()})."})
        cyc = val(conn, "SELECT parallel_fixtures FROM station WHERE station_id='FRE-P1-P50'")
        out.append({"tone": "info", "title": "P50 end-of-line test is a 92-minute charge/discharge cycle",
                    "body": f"{cyc} cyclers run in parallel, so most pack WIP sits in P50 at any moment. "
                            "Its effective cycle is 92 ÷ 12 ≈ 7.7 minutes, under takt. Stack & weld (P20) "
                            "is the single-station bottleneck."})
    return out


@get(r"^/api/production/unit$")
def unit_detail(req):
    conn = req.conn
    sn = req.arg("serial")
    if not sn:
        raise HttpError(400, "serial is required")
    u = q1(conn, """SELECT u.*, i.name AS item_name, i.kind FROM unit u JOIN item i ON i.item_id = u.item_id
                    WHERE u.serial = ?""", (sn,))
    if not u:
        raise HttpError(404, f"unit {sn} not found")
    events = q(conn, """
        SELECT se.event_id, se.event_ts, s.code, s.name AS station_name, se.result, se.defect_code, d.description,
               se.operator_id, se.measurements, se.source, se.raw_id
        FROM station_event se JOIN station s ON s.station_id = se.station_id
        LEFT JOIN defect_code d ON d.defect_code = se.defect_code
        WHERE se.serial = ? ORDER BY se.event_ts, se.event_id""", (sn,))
    for e in events:
        e["measurements"] = json.loads(e["measurements"]) if e["measurements"] else None
    children = q(conn, """
        SELECT g.position, g.child_serial, g.child_lot_id, g.child_item_id, g.qty, g.installed_at, g.removed_at,
               g.removal_reason, g.source
        FROM genealogy g WHERE g.parent_serial = ? ORDER BY g.installed_at""", (sn,))
    quarantined = q(conn, """SELECT raw_id, received_at, ingest_note FROM raw_cm_mes_event
                             WHERE ingest_status = 'QUARANTINED'
                               AND (json_extract(payload, '$.serial') = ? OR json_extract(payload, '$.sn') = ?)""", (sn, sn))
    wo = q1(conn, "SELECT * FROM work_order WHERE wo_id = ?", (u["wo_id"],)) if u["wo_id"] else None
    return {"unit": u, "events": events, "children": children, "quarantined": quarantined, "work_order": wo}
