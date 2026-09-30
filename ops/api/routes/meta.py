"""Shell endpoints: dataset clock + nav badges, global search, demo reset."""
import sqlite3
from urllib.parse import quote

from ... import config
from ..router import HttpError, get, post
from ...db import q

SEVERITY_RANK = {"CRITICAL": 4, "SERIOUS": 3, "WARNING": 2, "INFO": 1}
SEVERITY_TONE = {"CRITICAL": "critical", "SERIOUS": "serious", "WARNING": "warning", "INFO": "info"}


def _route_key(route):
    """'#/genealogy?q=CL2607-118' -> 'genealogy'."""
    r = (route or "").lstrip("#").lstrip("/")
    for sep in ("?", "/"):
        r = r.split(sep, 1)[0]
    return r


@get(r"^/api/meta$")
def meta_info(req):
    values = {r["key"]: r["value"] for r in q(req.conn, "SELECT key, value FROM meta")}
    badges = {}
    try:
        rows = q(req.conn, "SELECT route, severity, COUNT(*) AS n FROM ops_exception "
                           "WHERE status != 'RESOLVED' AND route IS NOT NULL GROUP BY route, severity")
    except sqlite3.OperationalError:
        rows = []
    for r in rows:
        key = _route_key(r["route"])
        if not key:
            continue
        b = badges.setdefault(key, {"count": 0, "rank": 0})
        b["count"] += r["n"]
        rank = SEVERITY_RANK.get(r["severity"], 0)
        if rank > b["rank"]:
            b["rank"] = rank
            b["tone"] = SEVERITY_TONE.get(r["severity"], "info")
    for b in badges.values():
        b.pop("rank", None)
        b.setdefault("tone", "info")
    try:                                   # the notification bell: open exceptions, worst first
        alerts = q(req.conn, """SELECT exception_id, rule_id, severity, title, route, domain FROM ops_exception WHERE status = 'OPEN'
                                ORDER BY CASE severity WHEN 'CRITICAL' THEN 0 WHEN 'SERIOUS' THEN 1 WHEN 'WARNING' THEN 2
                                         ELSE 3 END, COALESCE(impact_usd, 0) DESC, detected_at DESC""")
    except sqlite3.OperationalError:
        alerts = []
    return {
        "app": "Ops OS",
        "as_of": values.get("as_of"),
        "now_utc": values.get("now_utc"),
        "seed": values.get("seed"),
        "generated_at": values.get("generated_at"),
        "values": values,
        "nav_badges": badges,
        "alerts": alerts[:8],
        "alerts_open": len(alerts),
    }


def _hit(kind, ident, label, sub, href):
    return {"type": kind, "id": ident, "label": label, "sub": sub, "href": href}


@get(r"^/api/search$")
def search(req):
    term = (req.arg("q") or "").strip()
    if len(term) < 2:
        return {"q": term, "hits": []}
    if len(term) > 80:
        raise HttpError(400, "search term too long")
    conn = req.conn
    pre = term.replace("%", r"\%").replace("_", r"\_") + "%"
    mid = "%" + term.replace("%", r"\%").replace("_", r"\_") + "%"
    hits = []

    def safe(sql, params):
        try:
            return q(conn, sql, params)
        except sqlite3.OperationalError:
            return []

    for r in safe("SELECT u.serial, u.status, i.name FROM unit u JOIN item i ON i.item_id = u.item_id "
                  "WHERE u.serial LIKE ? ESCAPE '\\' ORDER BY length(u.serial), u.serial LIMIT 6", (pre,)):
        hits.append(_hit("serial", r["serial"], r["serial"], f'{r["name"]} · {r["status"].replace("_", " ").lower()}',
                         f'#/genealogy?q={quote(r["serial"])}'))
    for r in safe("SELECT l.lot_id, l.iqc_status, i.name FROM lot l JOIN item i ON i.item_id = l.item_id "
                  "WHERE l.lot_id LIKE ? ESCAPE '\\' ORDER BY l.lot_id LIMIT 5", (pre,)):
        hits.append(_hit("lot", r["lot_id"], r["lot_id"], f'{r["name"]} · IQC {r["iqc_status"].replace("_", " ").lower()}',
                         f'#/genealogy?q={quote(r["lot_id"])}'))
    for r in safe("SELECT order_id, status, ship_to_region FROM customer_order "
                  "WHERE order_id LIKE ? ESCAPE '\\' ORDER BY order_id LIMIT 5", (pre,)):
        hits.append(_hit("order", r["order_id"], r["order_id"], f'{r["status"].lower()} · {r["ship_to_region"]}',
                         f'#/atp?order={quote(r["order_id"])}'))
    for r in safe("SELECT po.po_id, po.status, s.name FROM purchase_order po JOIN supplier s ON s.supplier_id = po.supplier_id "
                  "WHERE po.po_id LIKE ? ESCAPE '\\' ORDER BY po.po_id LIMIT 5", (pre,)):
        hits.append(_hit("po", r["po_id"], r["po_id"], f'{r["name"]} · {r["status"].lower()}',
                         f'#/suppliers?po={quote(r["po_id"])}'))
    for r in safe("SELECT shipment_id, container_no, tracking_no, mode, status FROM shipment "
                  "WHERE shipment_id LIKE ? ESCAPE '\\' OR container_no LIKE ? ESCAPE '\\' OR tracking_no LIKE ? ESCAPE '\\' "
                  "ORDER BY shipment_id LIMIT 5", (pre, pre, pre)):
        extra = r["container_no"] or r["tracking_no"] or ""
        hits.append(_hit("shipment", r["shipment_id"], r["shipment_id"],
                         " · ".join(x for x in (extra, r["mode"].replace("_", " ").lower(), r["status"].replace("_", " ").lower()) if x),
                         f'#/shipments?id={quote(r["shipment_id"])}'))
    for r in safe("SELECT supplier_id, name, tier, country FROM supplier "
                  "WHERE supplier_id LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\' ORDER BY tier, name LIMIT 5", (pre, mid)):
        hits.append(_hit("supplier", r["supplier_id"], r["name"], f'tier {r["tier"]} · {r["country"]} · {r["supplier_id"]}',
                         f'#/suppliers?supplier={quote(r["supplier_id"])}'))
    for r in safe("SELECT item_id, name, kind FROM item "
                  "WHERE item_id LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\' ORDER BY kind, item_id LIMIT 5", (pre, mid)):
        hits.append(_hit("item", r["item_id"], r["item_id"], f'{r["name"]} · {r["kind"].lower()}',
                         f'#/inventory?item={quote(r["item_id"])}'))
    for r in safe("SELECT name, type FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' "
                  "AND name LIKE ? ESCAPE '\\' ORDER BY length(name) LIMIT 4", (mid,)):
        hits.append(_hit("table", r["name"], r["name"], f'{r["type"]} in the data sandbox',
                         f'#/sandbox?table={quote(r["name"])}'))

    low = term.lower()
    hits.sort(key=lambda h: 0 if h["id"].lower() == low else 1)  # stable: exact ids first
    return {"q": term, "hits": hits[:20]}


@post(r"^/api/admin/reset$")
def reset(req):
    values = {r["key"]: r["value"] for r in q(req.conn, "SELECT key, value FROM meta")}
    as_of = values.get("as_of")
    try:
        seed = int(values.get("seed") or config.DEFAULT_SEED)
    except ValueError:
        seed = config.DEFAULT_SEED
    from ...generate import build_database
    summary = build_database(config.DB_PATH, as_of=as_of, seed=seed)
    return {"ok": True, "as_of": as_of, "seed": seed, "summary": summary}
