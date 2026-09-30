"""3PL WMS feed and carrier feeds (ocean EDI 315, DG truck API, last-mile APIs)."""
import json

from .common import Run, bump_status, iso, parse_iso

EDI315_CODES = {"I": "GATE_IN", "AE": "LOADED", "VD": "DEPARTED", "VA": "ARRIVED", "UV": "DISCHARGED",
                "CT": "CUSTOMS_RELEASED", "OA": "OUT_GATE"}
LOC = {"TWTXG": "Taichung", "USOAK": "Oakland"}


def run_3pl(conn, now_s):
    run = Run(conn, "3PL_WMS", now_s)
    by_container = {r["container_no"]: r["shipment_id"] for r in conn.execute(
        "SELECT shipment_id, container_no FROM shipment WHERE container_no IS NOT NULL")}
    by_pro = {r["tracking_no"]: r["shipment_id"] for r in conn.execute(
        "SELECT shipment_id, tracking_no FROM shipment WHERE leg='PLANT_TO_3PL'")}
    rows = conn.execute("SELECT raw_id, received_at, msg_type, payload FROM raw_3pl_message WHERE ingest_status='PENDING'"
                        " ORDER BY received_at, raw_id").fetchall()
    for r in rows:
        run.counts["in"] += 1
        p = json.loads(r["payload"])
        ref = f"raw_3pl_message:{r['raw_id']}"
        note, status = None, "OK"
        if r["msg_type"] == "RECEIPT":
            sid = (by_container if p["ref_type"] == "CONTAINER" else by_pro).get(p["ref"])
            if sid is None:
                _q(conn, run, r, "receipt for unknown container/trailer " + p["ref"])
                continue
            expected = {x["serial"] for x in conn.execute("SELECT serial FROM shipment_unit WHERE shipment_id=?", (sid,))}
            got = [ln["serial"] for ln in p["lines"]]
            unknown = [s for s in got if s not in expected]
            for s in got:
                bump_status(conn, s, "AT_3PL", "3PL-RNO")
            for ex in p.get("exceptions", []):
                hid = f"HOLD-3PL-{ex['serial']}"
                conn.execute("INSERT OR IGNORE INTO hold(hold_id, scope_type, serial, site_id, reason, placed_at)"
                             " VALUES (?,?,?,?,?,?)", (hid, "SERIAL", ex["serial"], "3PL-RNO",
                                                       f"Freight damage on receipt: {ex['note']}", p["received_at"]))
                conn.execute("UPDATE unit SET on_hold=1 WHERE serial=?", (ex["serial"],))
            missing = len(expected) - len(set(got) & expected)
            conn.execute("UPDATE shipment SET received_at=? WHERE shipment_id=?", (p["received_at"], sid))
            detail = f"{len(got)} units received against ASN of {len(expected)}"
            if missing:
                detail += f"; {missing} on the ASN were not received"
                status, note = "WARN", detail
            if unknown:
                status, note = "WARN", (note or "") + f"; {len(unknown)} serials not on the ASN"
            conn.execute("INSERT INTO shipment_event(shipment_id, event_ts, code, location, detail, source, raw_ref)"
                         " VALUES (?,?,?,?,?,?,?)", (sid, p["received_at"], "RECEIVED", "Reno", detail, "3PL_FEED", ref))
        elif r["msg_type"] == "ALLOCATION":
            oid = p["order_id"]
            was = {x["line_no"]: x for x in conn.execute(
                "SELECT line_no, vehicle_serial, pack_serial FROM order_line WHERE order_id=?", (oid,))}
            for ln in p["lines"]:
                conn.execute("UPDATE order_line SET vehicle_serial=?, pack_serial=? WHERE order_id=? AND line_no=?",
                             (ln.get("vehicle"), ln.get("pack"), oid, ln["line"]))
            v = next((ln.get("vehicle") for ln in p["lines"] if ln.get("vehicle")), None)
            # A re-allocation (the 3PL swapped a pack or vehicle before shipping) is a change, not a contradiction: the
            # latest allocation is the truth, so the pairing it replaces is closed and kept as history, and whatever it
            # frees goes back to available stock.
            old_v = next((x["vehicle_serial"] for x in was.values() if x["vehicle_serial"]), None)
            old_packs = {was[ln["line"]]["pack_serial"] for ln in p["lines"] if ln["line"] in was} - {None}
            for ln in p["lines"]:
                old_pack = was[ln["line"]]["pack_serial"] if ln["line"] in was else None
                if old_pack and (old_pack, old_v) != (ln.get("pack"), v):
                    conn.execute("UPDATE genealogy SET removed_at=?, removal_reason=? WHERE parent_serial=? AND child_serial=?"
                                 " AND relation='SHIPPED_WITH' AND removed_at IS NULL",
                                 (p["allocated_at"], f"Re-allocated by the 3PL ({oid})", old_v, old_pack))
            for s in ({old_v} | old_packs) - {v, *(ln.get("pack") for ln in p["lines"])} - {None}:
                conn.execute("UPDATE unit SET status='AT_3PL', location_site_id='3PL-RNO' WHERE serial=? AND status='ALLOCATED'",
                             (s,))
            for ln in p["lines"]:
                if v and ln.get("pack"):
                    conn.execute("INSERT INTO genealogy(parent_serial, child_serial, child_item_id, qty, relation, position,"
                                 " installed_at, source) SELECT ?, ?, item_id, 1, 'SHIPPED_WITH', ?, ?, '3PL_FEED'"
                                 " FROM unit WHERE serial=? ON CONFLICT DO NOTHING",      # a re-sent allocation
                                 (v, ln["pack"], "PACK" if ln["line"] == 1 else "EXTRA_PACK", p["allocated_at"], ln["pack"]))
                for s in (ln.get("vehicle"), ln.get("pack")):
                    if s:
                        bump_status(conn, s, "ALLOCATED", "3PL-RNO")
            conn.execute("UPDATE customer_order SET allocated_at=?, status='ALLOCATED' WHERE order_id=? AND status='OPEN'",
                         (p["allocated_at"], oid))
        elif r["msg_type"] == "SHIP_CONFIRM":
            oid = p["order_id"]
            sid = f"LM-{oid[3:]}"
            conn.execute("INSERT OR IGNORE INTO shipment(shipment_id, leg, mode, origin_site_id, order_id, carrier, tracking_no,"
                         " etd_planned, atd, status, asn_qty) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (sid, "3PL_TO_CUSTOMER", "WHITE_GLOVE" if p["service"] == "WHITE_GLOVE" else "PARCEL", "3PL-RNO",
                          oid, p["carrier"], p["tracking"], p["shipped_at"], p["shipped_at"], "IN_TRANSIT", len(p["serials"])))
            for s in p["serials"]:
                conn.execute("INSERT OR IGNORE INTO shipment_unit VALUES (?,?)", (sid, s))
                bump_status(conn, s, "SHIPPED", None)
            conn.execute("UPDATE customer_order SET shipped_at=?, status='SHIPPED' WHERE order_id=?", (p["shipped_at"], oid))
        elif r["msg_type"] == "INVENTORY_SNAPSHOT":
            merged = {}
            for cnt in p["counts"]:
                m = merged.setdefault(cnt["sku"], [0, 0, 0])
                m[0] += cnt["available"]
                m[1] += cnt["allocated"]
                m[2] += cnt["hold"]
            for sku, (a, al, h) in merged.items():
                conn.execute("INSERT OR REPLACE INTO wms_snapshot VALUES (?,?,?,?,?,?)", ("3PL-RNO", sku, p["snapshot_at"], a, al, h))
        conn.execute("UPDATE raw_3pl_message SET ingest_status=?, ingest_note=? WHERE raw_id=?", (status, note, r["raw_id"]))
        run.counts["warn" if status == "WARN" else "ok"] += 1
        if status != "OK" or r["raw_id"] % 40 == 0 or r["msg_type"] == "RECEIPT":
            run.step(ref, 1, "PARSED", "OK", r["received_at"], r["msg_type"])
            run.step(ref, 2, "LOADED", status, r["received_at"], note or "applied")
    run.finish(now_s)
    return run.counts


def _q(conn, run, r, reason):
    conn.execute("UPDATE raw_3pl_message SET ingest_status='QUARANTINED', ingest_note=? WHERE raw_id=?", (reason, r["raw_id"]))
    run.counts["quarantined"] += 1
    run.step(f"raw_3pl_message:{r['raw_id']}", 1, "QUARANTINED", "FAILED", r["received_at"], reason)


def run_carrier(conn, now_s):
    run = Run(conn, "CARRIER", now_s)
    by_container = {r["container_no"]: r["shipment_id"] for r in conn.execute(
        "SELECT shipment_id, container_no FROM shipment WHERE container_no IS NOT NULL")}
    by_tracking = {r["tracking_no"]: r["shipment_id"] for r in conn.execute(
        "SELECT shipment_id, tracking_no FROM shipment WHERE tracking_no IS NOT NULL")}
    seen = set()
    rows = conn.execute("SELECT raw_id, carrier, received_at, format, payload FROM raw_carrier_event WHERE ingest_status='PENDING'"
                        " ORDER BY received_at, raw_id").fetchall()
    for r in rows:
        run.counts["in"] += 1
        ref = f"raw_carrier_event:{r['raw_id']}"
        if r["format"] == "EDI315":
            parts = r["payload"].split("|")
            if len(parts) != 6 or parts[1] not in EDI315_CODES:
                _qc(conn, run, r, f"unparseable EDI 315 segment: {r['payload'][:60]}")
                continue
            container, q, ts_s, loc, vessel, voyage = parts
            sid = by_container.get(container)
            if sid is None:
                _qc(conn, run, r, f"container {container} not on any shipment")
                continue
            code, ts = EDI315_CODES[q], iso(parse_iso(ts_s))
            key = (sid, code, ts)
            if key in seen:
                conn.execute("UPDATE raw_carrier_event SET ingest_status='DUPLICATE', ingest_note='repeat of an ingested milestone'"
                             " WHERE raw_id=?", (r["raw_id"],))
                run.counts["duplicate"] += 1
                continue
            seen.add(key)
            conn.execute("INSERT INTO shipment_event(shipment_id, event_ts, code, location, detail, source, raw_ref)"
                         " VALUES (?,?,?,?,?,?,?)", (sid, ts, code, LOC.get(loc, loc), f"{vessel} {voyage}", "CARRIER", ref))
            if code == "DEPARTED":
                conn.execute("UPDATE shipment SET atd=? WHERE shipment_id=?", (ts, sid))
                for u in conn.execute("SELECT serial FROM shipment_unit WHERE shipment_id=?", (sid,)).fetchall():
                    bump_status(conn, u["serial"], "IN_TRANSIT", None)
            if code == "ARRIVED":
                conn.execute("UPDATE shipment SET ata=? WHERE shipment_id=?", (ts, sid))
            if code == "CUSTOMS_RELEASED":
                conn.execute("UPDATE customs_entry SET status='RELEASED', released_at=COALESCE(released_at, ?) WHERE shipment_id=?"
                             " AND status!='EXAM'", (ts, sid))
        else:
            p = json.loads(r["payload"])
            if p.get("type") == "ETA_UPDATE":
                sid = by_container.get(p["container"])
                if sid is None:
                    _qc(conn, run, r, "ETA update for unknown container")
                    continue
                conn.execute("UPDATE shipment SET eta_current=? WHERE shipment_id=?", (p["eta"], sid))
                conn.execute("INSERT INTO shipment_event(shipment_id, event_ts, code, location, detail, source, raw_ref)"
                             " VALUES (?,?,?,?,?,?,?)", (sid, r["received_at"], "ETA_UPDATE", "Pacific",
                                                         f"New ETA {p['eta'][:10]}: {p['reason']}", "CARRIER", ref))
            elif "pro" in p:
                sid = by_tracking.get(p["pro"])
                if sid is None:
                    _qc(conn, run, r, "unknown PRO number")
                    continue
                code = "PICKED_UP" if p["status"] == "PICKED_UP" else "DELIVERED"
                conn.execute("INSERT INTO shipment_event(shipment_id, event_ts, code, location, detail, source, raw_ref)"
                             " VALUES (?,?,?,?,?,?,?)", (sid, p["ts"], code, p["location"], "DG LTL (UN3480, class 9)",
                                                         "CARRIER", ref))
                if code == "PICKED_UP":
                    conn.execute("UPDATE shipment SET atd=? WHERE shipment_id=?", (p["ts"], sid))
                    for u in conn.execute("SELECT serial FROM shipment_unit WHERE shipment_id=?", (sid,)).fetchall():
                        bump_status(conn, u["serial"], "IN_TRANSIT", None)
                else:
                    conn.execute("UPDATE shipment SET ata=? WHERE shipment_id=?", (p["ts"], sid))
            else:
                sid = by_tracking.get(p["tracking"])
                if sid is None:
                    sid = conn.execute("SELECT shipment_id FROM shipment WHERE tracking_no=?", (p["tracking"],)).fetchone()
                    sid = sid["shipment_id"] if sid else None
                    if sid:
                        by_tracking[p["tracking"]] = sid
                if sid is None:
                    _qc(conn, run, r, f"tracking {p['tracking']} not on any shipment")
                    continue
                conn.execute("INSERT INTO shipment_event(shipment_id, event_ts, code, location, detail, source, raw_ref)"
                             " VALUES (?,?,?,?,?,?,?)", (sid, p["ts"], p["status"], p.get("note"), None, "CARRIER", ref))
                if p["status"] == "DELIVERED":
                    conn.execute("UPDATE shipment SET ata=?, received_at=? WHERE shipment_id=?", (p["ts"], p["ts"], sid))
                    order = conn.execute("SELECT order_id FROM shipment WHERE shipment_id=?", (sid,)).fetchone()["order_id"]
                    conn.execute("UPDATE customer_order SET delivered_at=?, status='DELIVERED' WHERE order_id=?", (p["ts"], order))
                    for u in conn.execute("SELECT serial FROM shipment_unit WHERE shipment_id=?", (sid,)).fetchall():
                        bump_status(conn, u["serial"], "DELIVERED", None)
        conn.execute("UPDATE raw_carrier_event SET ingest_status='OK' WHERE raw_id=?", (r["raw_id"],))
        run.counts["ok"] += 1
    run.finish(now_s)
    return run.counts


def _qc(conn, run, r, reason):
    conn.execute("UPDATE raw_carrier_event SET ingest_status='QUARANTINED', ingest_note=? WHERE raw_id=?", (reason, r["raw_id"]))
    run.counts["quarantined"] += 1
    run.step(f"raw_carrier_event:{r['raw_id']}", 1, "QUARANTINED", "FAILED", r["received_at"], reason)
