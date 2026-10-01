"""Production Scheduling: the line schedule.

Every line, every day: the committed build (the CM's commit, the OEM's pack MPS, and the released work orders that firm
them up) against the line's rated capacity and its site's time fences; what actually ran; the pack days MRP says the
BMS shortage would cut; and a dispatch list for the next working day, sequenced by colour batch with start and end
times from takt and the line's measured changeover.
"""
import datetime as dt
from zoneinfo import ZoneInfo

from ops.api.router import HttpError, get
from ops.dates import to_date
from ops.db import as_of, q
from ops.logic import mrp as M

LINES = [
    {"site_id": "CM-TXG", "line": "L1", "name": "CM line 1", "where": "Taichung", "tz": "Asia/Taipei", "start": "08:00",
     "plan_type": "CM_COMMIT", "family": "Vehicles"},
    {"site_id": "CM-TXG", "line": "L2", "name": "CM line 2", "where": "Taichung", "tz": "Asia/Taipei", "start": "08:00",
     "plan_type": "CM_COMMIT", "family": "Vehicles"},
    {"site_id": "OEM-FRE", "line": "P1", "name": "Pack line", "where": "Fremont", "tz": "America/Los_Angeles",
     "start": "06:00", "plan_type": "OEM_MPS", "family": "Battery packs"},
]
# the order a line runs its batches in (paint colour for vehicles; standard before large for packs)
SEQUENCE = ["LV1-DUNE", "LV1-SLATE", "LV1-FERN", "LV1-EMBER", "PK-STD", "PK-LRG"]
HOLIDAYS = {
    "CM-TXG": {"2026-06-19": "Dragon Boat Festival", "2026-09-25": "Mid-Autumn Festival",
               "2026-10-09": "National Day (bridge)", "2026-10-10": "National Day"},
    "OEM-FRE": {"2026-05-25": "Memorial Day", "2026-07-03": "Independence Day", "2026-09-07": "Labor Day",
                 "2026-11-26": "Thanksgiving", "2026-11-27": "Thanksgiving", "2026-12-25": "Christmas"},
}


def _local_day(ts, tz):
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(ZoneInfo(tz)).date().isoformat()


def _capacity(rows, day):
    """The line_capacity row in effect on `day` (the latest effective_from on or before it)."""
    best = None
    for r in rows:
        if r["effective_from"] <= day and (best is None or r["effective_from"] > best["effective_from"]):
            best = r
    return best


def _sequence(items):
    return sorted(items, key=lambda it: SEQUENCE.index(it) if it in SEQUENCE else len(SEQUENCE))


@get(r"^/api/schedule/board$")
def board(req):
    c = req.conn
    today = to_date(as_of(c))
    try:
        back = max(0, min(28, int(req.arg("back") or 7)))
        ahead = max(7, min(56, int(req.arg("ahead") or 21)))
    except ValueError:
        raise HttpError(400, "back and ahead are whole numbers of days")
    days = [today + dt.timedelta(days=k) for k in range(-back, ahead + 1)]
    lo, hi = days[0].isoformat(), days[-1].isoformat()

    names = {r["item_id"]: r["name"] for r in q(c, "SELECT item_id, name FROM item WHERE item_id IN (%s)"
                                                   % ",".join("?" * len(SEQUENCE)), SEQUENCE)}
    fences = {r["site_id"]: r for r in q(c, "SELECT * FROM time_fence")}
    versions = {r["plan_type"]: r["v"] for r in q(c, "SELECT plan_type, MAX(version) v FROM build_plan GROUP BY plan_type")}

    # MRP's view of the pack line: the MPS it was given and what the component shortages leave of it
    mrp = M.run(c)
    cons = M.constrained_pack_plan(c, mrp)
    mps_by_day, cut_by_day = {}, {}
    for sku, by_i in mrp["mps"].items():
        if sku in ("PK-STD", "PK-LRG"):
            for i, qty in by_i.items():
                d = (today + dt.timedelta(days=i)).isoformat()
                mps_by_day[d] = mps_by_day.get(d, 0) + qty
    for sku, by_i in cons["plan"].items():
        for i, qty in by_i.items():
            d = (today + dt.timedelta(days=i)).isoformat()
            cut_by_day[d] = cut_by_day.get(d, 0) + qty
    cut_by_day = {d: max(0, round(mps_by_day.get(d, 0) - kept)) for d, kept in cut_by_day.items()}

    lines = []
    for ln in LINES:
        site, line, tz = ln["site_id"], ln["line"], ln["tz"]
        cap_rows = q(c, "SELECT * FROM line_capacity WHERE site_id=? AND line=?", (site, line))
        fence = fences.get(site) or {"frozen_days": 0, "slushy_days": 0}
        frozen_to = today + dt.timedelta(days=fence["frozen_days"])
        slushy_to = today + dt.timedelta(days=fence["slushy_days"])
        plan = {}
        for r in q(c, """SELECT plan_date, item_id, SUM(qty) qty FROM build_plan WHERE site_id=? AND line=? AND plan_type=?
                         AND version=? AND plan_date BETWEEN ? AND ? GROUP BY plan_date, item_id""",
                   (site, line, ln["plan_type"], versions.get(ln["plan_type"]), lo, hi)):
            plan.setdefault(r["plan_date"], {})[r["item_id"]] = r["qty"]
        wos = {}
        for r in q(c, """SELECT wo_id, item_id, qty_planned, qty_completed, qty_scrapped, status, sched_date FROM work_order
                         WHERE site_id=? AND line=? AND sched_date BETWEEN ? AND ? ORDER BY wo_id""", (site, line, lo, hi)):
            wos.setdefault(r["sched_date"], []).append(r)
        down = {}
        for r in q(c, """SELECT start_ts, end_ts, category, reason FROM downtime_event WHERE site_id=? AND line=?
                         AND start_ts BETWEEN ? AND ?""", (site, line, f"{(days[0] - dt.timedelta(days=1)).isoformat()}T00:00:00Z",
                                                         f"{(days[-1] + dt.timedelta(days=1)).isoformat()}T23:59:59Z")):
            day = _local_day(r["start_ts"], tz)
            mins = (dt.datetime.fromisoformat(r["end_ts"].replace("Z", "+00:00"))
                    - dt.datetime.fromisoformat(r["start_ts"].replace("Z", "+00:00"))).total_seconds() / 60
            b = down.setdefault(day, {"changeovers": 0, "changeover_min": 0.0, "downtime_min": 0.0, "events": []})
            if r["category"] == "CHANGEOVER":
                b["changeovers"] += 1
                b["changeover_min"] += mins
            else:
                b["downtime_min"] += mins
                b["events"].append({"category": r["category"], "reason": r["reason"], "minutes": round(mins)})
        hist = [b for d, b in down.items() if d < today.isoformat() and b["changeovers"]]
        avg_co = (sum(b["changeover_min"] for b in hist) / max(1, sum(b["changeovers"] for b in hist))) if hist else 8.0

        out_days = []
        for day in days:
            iso = day.isoformat()
            cap = _capacity(cap_rows, iso)
            p = plan.get(iso, {})
            w = wos.get(iso, [])
            past = day < today
            zone = "past" if past else "frozen" if day <= frozen_to else "slushy" if day <= slushy_to else "liquid"
            sched = {}
            for r in w:
                if r["status"] in ("RELEASED", "IN_PROCESS"):
                    sched[r["item_id"]] = sched.get(r["item_id"], 0) + r["qty_planned"]
            actual = {}
            for r in w:
                if r["qty_completed"]:
                    actual[r["item_id"]] = actual.get(r["item_id"], 0) + r["qty_completed"]
            # what the line is scheduled to run: released work orders firm up the plan inside the frozen window
            firm = sched if (sched and not past) else p
            total = sum(firm.values())
            holiday = HOLIDAYS.get(site, {}).get(iso)
            working = bool(total or actual) and not holiday
            reason = holiday or ("Weekend" if day.weekday() >= 5 else None if working else "No build planned")
            b = down.get(iso, {})
            if past:
                changeovers, co_min, co_est = b.get("changeovers", 0), round(b.get("changeover_min", 0)), False
            else:
                n = max(0, len([k for k, v in firm.items() if v]) - 1)
                changeovers, co_min, co_est = n, round(n * avg_co), True
            rated = cap["rated_units_per_day"] if cap and working else 0
            cut = cut_by_day.get(iso, 0) if site == "OEM-FRE" and not past else 0
            out_days.append({
                "date": iso, "dow": day.strftime("%a"), "working": working, "reason": reason, "zone": zone,
                "capacity": rated, "planned": total, "plan_by_item": {k: v for k, v in firm.items() if v},
                "commit": sum(p.values()), "firmed": bool(sched) and not past,
                "actual": sum(actual.values()) if past or actual else None,
                "actual_by_item": actual, "load": round(total / rated, 3) if rated else None,
                "adherence": round(sum(actual.values()) / sum(p.values()), 3) if past and sum(p.values()) else None,
                "changeovers": changeovers, "changeover_min": co_min, "changeover_est": co_est,
                "downtime_min": round(b.get("downtime_min", 0)), "downtime": b.get("events", []),
                "mrp_cut": cut,
                "work_orders": [{"wo_id": r["wo_id"], "item_id": r["item_id"], "qty": r["qty_planned"],
                                 "completed": r["qty_completed"], "scrapped": r["qty_scrapped"], "status": r["status"]}
                                for r in w],
            })
        cur = _capacity(cap_rows, today.isoformat())
        change = next((r for r in sorted(cap_rows, key=lambda r: r["effective_from"])
                       if r["effective_from"] > today.isoformat()), None)
        lines.append({
            "site_id": site, "line": line, "name": ln["name"], "where": ln["where"], "tz": tz, "family": ln["family"],
            "plan_type": ln["plan_type"], "plan_version": versions.get(ln["plan_type"]),
            "capacity": dict(cur) if cur else None, "capacity_change": dict(change) if change else None,
            "fence": {"frozen_days": fence["frozen_days"], "slushy_days": fence["slushy_days"],
                      "frozen_to": frozen_to.isoformat(), "slushy_to": slushy_to.isoformat(), "note": fence.get("note")},
            "avg_changeover_min": round(avg_co, 1), "days": out_days,
            "dispatch": _dispatch(ln, cur, out_days, today, avg_co),
        })

    return {"as_of": today.isoformat(), "days": [d.isoformat() for d in days], "lines": lines,
            "items": [{"item_id": i, "name": names.get(i, i)} for i in SEQUENCE],
            "kpis": _kpis(lines, today)}


def _dispatch(ln, cap, days, today, avg_co):
    """The next working day's work orders in run order, with start and end from takt and changeovers."""
    nxt = next((d for d in days if d["date"] >= today.isoformat() and d["working"] and d["planned"]), None)
    if not nxt or not cap:
        return None
    takt = cap["takt_min"]
    h, m = map(int, ln["start"].split(":"))
    clock = h * 60 + m
    end_of_shift = clock + cap["shift_minutes"] * cap["shifts_per_day"]
    rows, prev = [], None
    wos = {w["item_id"]: w for w in nxt["work_orders"] if w["status"] in ("RELEASED", "IN_PROCESS")}
    for item in _sequence(list(nxt["plan_by_item"])):
        qty = nxt["plan_by_item"][item]
        co = round(avg_co) if prev else 0
        start = clock + co
        finish = start + qty * takt
        rows.append({"seq": len(rows) + 1, "wo_id": wos[item]["wo_id"] if item in wos else None, "item_id": item,
                     "qty": qty, "changeover_min": co, "start": _hhmm(start), "end": _hhmm(finish),
                     "status": wos[item]["status"] if item in wos else "PLANNED",
                     "over_shift": finish > end_of_shift + 0.5})
        clock, prev = finish, item
    short = nxt["mrp_cut"]
    return {"date": nxt["date"], "dow": nxt["dow"], "shift": f"{ln['start']}–{_hhmm(end_of_shift)}",
            "tz": ln["tz"], "rows": rows, "total": nxt["planned"], "capacity": nxt["capacity"],
            "finish": _hhmm(clock), "material_short": short}


def _hhmm(minutes):
    minutes = int(round(minutes))
    return f"{(minutes // 60) % 24:02d}:{minutes % 60:02d}"


def _kpis(lines, today):
    out = {"load": [], "adherence": None, "frozen_to": {}, "changeovers_next": 0, "changeover_min_next": 0,
           "mrp_cut_next": 0, "mrp_cut_days": []}
    done = plan = 0
    for ln in lines:
        nxt = [d for d in ln["days"] if d["date"] >= today.isoformat() and d["working"]][:5]
        prev = [d for d in ln["days"] if d["date"] < today.isoformat() and d["working"]][-5:]
        cap = sum(d["capacity"] for d in nxt)
        out["load"].append({"line": ln["line"], "name": ln["name"], "planned": sum(d["planned"] for d in nxt),
                            "capacity": cap, "load": round(sum(d["planned"] for d in nxt) / cap, 3) if cap else None})
        done += sum(d["actual"] or 0 for d in prev)
        plan += sum(d["commit"] for d in prev)
        out["frozen_to"][ln["site_id"]] = ln["fence"]["frozen_to"]
        out["changeovers_next"] += sum(d["changeovers"] for d in nxt)
        out["changeover_min_next"] += sum(d["changeover_min"] for d in nxt)
        for d in ln["days"]:
            if d["mrp_cut"]:
                out["mrp_cut_next"] += d["mrp_cut"]
                out["mrp_cut_days"].append(d["date"])
    out["adherence"] = round(done / plan, 3) if plan else None
    return out
