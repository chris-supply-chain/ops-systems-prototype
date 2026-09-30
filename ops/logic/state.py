"""Derive current state from events: shipment status and component unit status."""

SHIP_STATUS = [  # (event code, status) in lifecycle order
    ("BOOKED", "BOOKED"), ("GATE_IN", "GATED_IN"), ("LOADED", "GATED_IN"), ("DEPARTED", "ON_WATER"),
    ("PICKED_UP", "IN_TRANSIT"), ("ARRIVED", "AT_PORT"), ("DISCHARGED", "AT_PORT"), ("CUSTOMS_HOLD", "CUSTOMS_HOLD"),
    ("CUSTOMS_RELEASED", "AT_PORT"), ("OUT_GATE", "IN_TRANSIT"), ("OUT_FOR_DELIVERY", "OUT_FOR_DELIVERY"),
    ("EXCEPTION", "EXCEPTION"), ("DELIVERED", "DELIVERED"), ("RECEIVED", "RECEIVED"),
]
RANK = {code: i for i, (code, _) in enumerate(SHIP_STATUS)}
STATUS_OF = dict(SHIP_STATUS)


def derive(conn):
    # shipments: status follows the furthest milestone reached (a later release clears a hold)
    for s in conn.execute("SELECT shipment_id, leg, status FROM shipment").fetchall():
        codes = [r["code"] for r in conn.execute("SELECT code FROM shipment_event WHERE shipment_id=? ORDER BY event_ts",
                                                   (s["shipment_id"],))]
        codes = [c for c in codes if c in RANK]
        if not codes:
            continue
        last = max(codes, key=lambda c: RANK[c])
        if last == "EXCEPTION" and "DELIVERED" in codes:
            last = "DELIVERED"
        status = STATUS_OF[last]
        if s["leg"] == "3PL_TO_CUSTOMER" and status == "RECEIVED":
            status = "DELIVERED"
        if status == "AT_PORT" and "CUSTOMS_HOLD" in codes and "CUSTOMS_RELEASED" not in codes:
            status = "CUSTOMS_HOLD"
        conn.execute("UPDATE shipment SET status=? WHERE shipment_id=?", (status, s["shipment_id"]))
    # components: installed if an active edge holds them, returned if they were removed and never reinstalled
    conn.execute("""UPDATE unit SET status='INSTALLED', location_site_id=NULL
                    WHERE status IN ('COMPONENT','INSTALLED') AND serial IN
                      (SELECT child_serial FROM genealogy WHERE relation='INSTALLED' AND removed_at IS NULL
                       AND child_serial IS NOT NULL)""")
    conn.execute("""UPDATE unit SET status='RETURNED'
                    WHERE status IN ('COMPONENT','INSTALLED') AND serial IN
                      (SELECT child_serial FROM genealogy WHERE relation='INSTALLED' AND removed_at IS NOT NULL)
                    AND serial NOT IN (SELECT child_serial FROM genealogy WHERE removed_at IS NULL AND child_serial IS NOT NULL)""")
    # on-hold flag mirrors the active holds
    conn.execute("UPDATE unit SET on_hold = CASE WHEN serial IN (SELECT serial FROM hold WHERE released_at IS NULL"
                 " AND serial IS NOT NULL) THEN 1 ELSE 0 END")
