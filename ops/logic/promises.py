"""Customer promise dates.

Open orders were promised by ATP when the plan still expected the delayed vessel
on time (eta_mode='planned'). Re-running ATP against current ETAs shows which
promises are now at risk. Re-promising them is a closed-loop decision, not a
silent overwrite. Orders already delivered carry the promise they were given, so
on-time delivery can be backtested.
"""
import datetime as dt
import hashlib

from ..db import as_of as get_as_of
from .atp import TRANSIT_MAX, promise_all, skip_sundays


def _noise(order_id, lo, hi):
    h = int(hashlib.md5(order_id.encode()).hexdigest()[:8], 16)
    return lo + h % (hi - lo + 1)


def initial_promises(conn):
    as_of = dt.date.fromisoformat(get_as_of(conn))
    from .mrp import constrained_pack_plan
    constrained = constrained_pack_plan(conn)
    planned, _, _ = promise_all(conn, eta_mode="planned", constrained=constrained)
    rows = conn.execute("SELECT order_id, status, ordered_at, shipped_at, delivered_at, ship_to_region FROM customer_order"
                        " WHERE status != 'CANCELLED'").fetchall()
    hist = []
    for o in rows:
        oid = o["order_id"]
        if o["status"] == "DELIVERED":
            d = dt.date.fromisoformat(o["delivered_at"][:10])
            promise = d + dt.timedelta(days=_noise(oid, -1, 9))          # ~90% of historical promises were kept
        elif o["status"] == "SHIPPED":
            d = skip_sundays(dt.date.fromisoformat(o["shipped_at"][:10]), TRANSIT_MAX[o["ship_to_region"]])
            promise = d + dt.timedelta(days=_noise(oid, 0, 5))
        elif o["status"] == "ALLOCATED":
            promise = skip_sundays(as_of, TRANSIT_MAX[o["ship_to_region"]]) + dt.timedelta(days=_noise(oid, 0, 4))
        else:
            r = planned.get(oid)
            promise = dt.date.fromisoformat(r["promise"]) if r and r["promise"] else as_of + dt.timedelta(days=150)
        p = promise.isoformat()
        conn.execute("UPDATE customer_order SET promised_date=?, first_promised_date=? WHERE order_id=?", (p, p, oid))
        pegged = None
        if o["status"] == "OPEN" and planned.get(oid):
            srcs = [pg["source"] for pegs in planned[oid]["pegs"].values() for pg in pegs]
            pegged = " + ".join(dict.fromkeys(srcs))
        hist.append((oid, p, "INITIAL", o["ordered_at"], pegged, None))
    conn.executemany("INSERT INTO order_promise(order_id, promised_date, reason, decided_at, pegged_to, decision_id)"
                     " VALUES (?,?,?,?,?,?)", hist)


def at_risk(conn, current=None):
    """Open orders whose ATP date today is later than what the customer was promised."""
    current = current or promise_all(conn)[0]
    out = []
    for o in conn.execute("SELECT order_id, promised_date, ship_to_region, ship_to_state, channel FROM customer_order"
                          " WHERE status='OPEN'"):
        r = current.get(o["order_id"])
        if not r or not r["promise"] or not o["promised_date"]:
            continue
        slip = (dt.date.fromisoformat(r["promise"]) - dt.date.fromisoformat(o["promised_date"])).days
        if slip > 0:
            srcs = [pg["source"] for pegs in r["pegs"].values() for pg in pegs]
            out.append({"order_id": o["order_id"], "promised": o["promised_date"], "new_promise": r["promise"],
                        "slip_days": slip, "region": o["ship_to_region"], "state": o["ship_to_state"],
                        "channel": o["channel"], "pegged_to": " + ".join(dict.fromkeys(srcs))})
    return out
