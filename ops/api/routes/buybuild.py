"""Buy vs Build: thin-app telemetry turned into capability weights and vendor scores."""
import datetime as dt
from collections import defaultdict

from ...db import now, q
from ...logic import buyvsbuild as bb
from ..router import get

APP_LABEL = {"QUALITY_THIN": "Quality thin app (QMS)", "MOVE_THIN": "Move thin app (TMS)", "DOCK_THIN": "Dock thin app (WMS)"}
APP_DOMAIN = {"QUALITY_THIN": "QMS", "MOVE_THIN": "TMS", "DOCK_THIN": "WMS"}


@get(r"^/api/buy-build$")
def buy_build(req):
    conn = req.conn
    caps = q(conn, "SELECT * FROM capability ORDER BY domain, capability_id")
    usage_rows = q(conn, "SELECT app, feature, COUNT(*) AS n, COUNT(DISTINCT user_role) AS roles,"
                         " SUM(CASE WHEN outcome='completed' THEN 1 ELSE 0 END) AS completed, AVG(duration_s) AS avg_s"
                         " FROM app_telemetry GROUP BY app, feature")
    usage = {r["feature"]: r["n"] for r in usage_rows}
    vendors = q(conn, "SELECT * FROM vendor ORDER BY domain, vendor_id")
    fit = {(r["vendor_id"], r["capability_id"]): r["fit"] for r in q(conn, "SELECT * FROM vendor_capability")}
    domains = bb.evaluate(caps, usage, vendors, fit)
    feat = {r["feature"]: r for r in usage_rows}
    for d in domains:
        for c in d["capabilities"]:
            f = feat.get(c["feature_key"], {})
            c["completion"] = (f.get("completed", 0) / f["n"]) if f.get("n") else None
            c["avg_seconds"] = f.get("avg_s")
    # weekly usage per feature (Monday weeks) and by role
    weekly = defaultdict(lambda: defaultdict(int))
    for r in q(conn, "SELECT feature, date(ts, '-6 days', 'weekday 1') AS wk, COUNT(*) AS n FROM app_telemetry GROUP BY 1, 2"):
        weekly[r["feature"]][r["wk"]] += r["n"]
    weeks = sorted({w for f in weekly.values() for w in f})
    by_role = q(conn, "SELECT app, user_role, COUNT(*) AS n FROM app_telemetry GROUP BY app, user_role ORDER BY app, n DESC")
    apps = q(conn, "SELECT app, COUNT(*) AS events, COUNT(DISTINCT user_role) AS roles, COUNT(DISTINCT feature) AS features,"
                   " MIN(ts) AS first, MAX(ts) AS last FROM app_telemetry GROUP BY app")
    for a in apps:
        a["label"] = APP_LABEL.get(a["app"], a["app"])
        a["domain"] = APP_DOMAIN.get(a["app"])
    return {"now": now(conn), "domains": domains, "weeks": weeks,
            "weekly": {k: [v.get(w, 0) for w in weeks] for k, v in weekly.items()},
            "by_role": by_role, "apps": apps,
            "total_events": sum(usage.values()),
            "savings": {d["domain"]: {"assumed": next(v for v in d["vendors"] if v["vendor_id"] == d["assumed_winner"]),
                                      "recommended": next(v for v in d["vendors"] if v["vendor_id"] == d["recommended"])}
                        for d in domains}}
