"""Writes the simulated world into the database in the order a real platform
would see it: master and transactional data OEM owns directly, and every outside
system's view (CM MES, supplier ASNs, carriers, 3PL, CRM, EDI, email) into the
raw_* landing tables. The ingest normalizers then build the rest of the core.
"""
import datetime as dt
import hashlib
import json

from ..dates import iso
from .demand import EXTRA_PACK_PRICE, KIT_PRICE
from .emails import CM_DEFECT, CM_PN
from .master import CARRIER_BY_NAME, ITEMS
from .platform import ACTIVITIES, CAPABILITIES, FEATURES, FIT, OPTIONS, SUITES, SYSTEMS, VENDORS
from .util import PT, TPE, UTC, add_days, at

CM_RESULT_V1 = {"PASS": "OK", "FAIL": "NG", "REWORK": "RWK", "SCRAP": "SCR"}
EDI315 = {"GATE_IN": ("I", "TWTXG"), "LOADED": ("AE", "TWTXG"), "DEPARTED": ("VD", "TWTXG"),
          "ARRIVED": ("VA", "USOAK"), "DISCHARGED": ("UV", "USOAK"), "CUSTOMS_RELEASED": ("CT", "USOAK"),
          "OUT_GATE": ("OA", "USOAK")}


def _d(x):
    # datetime is a subclass of date: only plain dates become 'YYYY-MM-DD' here; datetimes go through _t
    return x.isoformat() if isinstance(x, dt.date) and not isinstance(x, dt.datetime) else x


def _t(x):
    return iso(x) if isinstance(x, dt.datetime) else x


def _ins(conn, table, rows, cols=None):
    if not rows:
        return
    n = len(rows[0])
    if cols:
        sql = f"INSERT INTO {table}({','.join(cols)}) VALUES ({','.join('?' * n)})"
    else:
        sql = f"INSERT INTO {table} VALUES ({','.join('?' * n)})"
    conn.executemany(sql, [tuple(_t(_d(v)) for v in r) for r in rows])


# ======================================================================== before ingest

def pre_ingest(w):
    c = w.conn
    # ---- demand
    _ins(c, "customer", [(x["customer_id"], x["kind"], x["display_name"], x["city"], x["state"], x["region"], x["zip3"])
                         for x in w.customers])
    orders, lines = [], []
    for o in w.orders:
        status = "CANCELLED" if o["cancelled"] else "OPEN"
        orders.append((o["order_id"], o["customer_id"], o["channel"], o["reserved_at"], o["ordered_at"],
                       o["requested_date"], o["region"], o["state"], status, None, None, None, None, None, o["total_usd"]))
        lines.append((o["order_id"], 1, o["kit"], 1, KIT_PRICE[o["kit"][-1]], None, None))
        if o["extra_pack"]:
            lines.append((o["order_id"], 2, o["extra_pack"], 1, EXTRA_PACK_PRICE[o["extra_pack"]],
                          None, None))
    _ins(c, "customer_order", orders)
    _ins(c, "order_line", lines)
    _ins(c, "demand_forecast", w.forecast_rows)

    # ---- make + plan
    _ins(c, "work_order", w.work_orders)
    _ins(c, "build_plan", w.build_plan_rows, ["site_id", "line", "item_id", "plan_date", "qty", "plan_type", "version"])
    _ins(c, "forecast_release", w.releases)
    _ins(c, "forecast_line", w.release_lines, ["release_id", "supplier_id", "item_id", "tier", "week_start", "qty"])
    _ins(c, "supplier_commit", w.commits)
    _ins(c, "downtime_event", w.downtime_rows,
         ["site_id", "line", "station_id", "start_ts", "end_ts", "category", "reason", "source"])

    # ---- source (ERP): POs, lines (promises arrive through ingest), lots, receipts, invoices
    _ins(c, "purchase_order", w.po_rows)
    pl_rows = []
    for ln in w.po_lines + w.cm_po_lines:
        received = ln["received"]
        rq = ln.get("received_qty", ln["qty"] if received else 0)
        status = "RECEIVED" if received else "OPEN"
        pid = c.execute("SELECT price_id FROM price WHERE item_id=? AND supplier_id=? AND unit_price=? AND min_qty=0"
                        " ORDER BY eff_from DESC LIMIT 1", (ln["item"], ln["supplier"], ln["unit_price"])).fetchone()
        pl_rows.append((ln["po_id"], ln["line_no"], ln["item"], ln["qty"], ln["unit_price"],
                        None if ln.get("price_mismatch") else (pid["price_id"] if pid else None),
                        ln["need"], None, "UNCONFIRMED", None, rq, status))
    _ins(c, "po_line", pl_rows)

    lot_rows, gr_rows, inv_rows = [], [], []
    po_ref_of_lot = {}
    for item, dlvs in w.deliveries.items():
        for dlv in dlvs:
            for lot_id in dlv.get("lots", []):
                po_ref_of_lot[lot_id] = dlv.get("po_ref")
    for lot in sorted(w.lots.values(), key=lambda l: l["received_at"]):
        ref = po_ref_of_lot.get(lot["lot_id"]) or (None, None)
        lot_rows.append((lot["lot_id"], lot["item"], lot["supplier"], lot["site"], lot["qty"], lot["mfg"],
                         lot["received_at"], ref[0], ref[1], lot["iqc"], lot["origin"]))
    _ins(c, "lot", lot_rows)
    _ins(c, "lot_link", getattr(w, "lot_links", []))
    n = 0
    terms = {r["supplier_id"]: r["payment_terms"] for r in c.execute("SELECT supplier_id, payment_terms FROM supplier")}
    rng = w.rng
    for item, dlvs in w.deliveries.items():
        for dlv in dlvs:
            ref = dlv.get("po_ref")
            if not ref or dlv["arrival"] > w.now:
                continue
            if dlv.get("lots"):
                for lot_id in dlv["lots"]:
                    n += 1
                    gr_rows.append((f"GR-{n:06d}", ref[0], ref[1], w.lots[lot_id]["site"], dlv["arrival"],
                                    w.lots[lot_id]["qty"], lot_id, None))
            else:
                n += 1
                gr_rows.append((f"GR-{n:06d}", ref[0], ref[1], dlv["site"], dlv["arrival"], dlv["qty"], None,
                                dlv.get("asn_no")))
    _ins(c, "goods_receipt", gr_rows)
    k = 0
    for ln in w.po_lines + w.cm_po_lines:
        qty = ln.get("received_qty", ln["qty"]) if ln["received"] else 0
        if not qty:
            continue
        k += 1
        inv_date = add_days(ln["need"], rng.randint(0, 5)) if ln.get("arrival") is None else \
            add_days(ln["arrival"].astimezone(PT).date(), rng.randint(0, 5))
        price, status, variance = ln["unit_price"], "MATCHED", 0.0
        if ln.get("price_mismatch") and k % 3 == 0:
            price, status = 2.95, "PRICE_VARIANCE"
            variance = round((ln["unit_price"] - price) * qty, 2)
        rejected = any(w.lots[l]["iqc"] == "REJECTED" for l in (ln.get("dlv") or {}).get("lots", []))
        if rejected:
            status, variance = "QTY_VARIANCE", round(price * qty, 2)
        due = add_days(inv_date, {"Net 30": 30, "Net 45": 45, "Net 60": 60}.get(terms.get(ln["supplier"]), 45))
        pay = "BLOCKED" if status != "MATCHED" else ("PAID" if due <= w.as_of else
                                                       ("SCHEDULED" if due <= add_days(w.as_of, 14) else "OPEN"))
        inv_rows.append((f"INV-{ln['supplier']}-{k:05d}", ln["supplier"], ln["po_id"], ln["line_no"], inv_date, qty,
                         price, round(price * qty, 2), status, variance, pay, due))
    _ins(c, "supplier_invoice", inv_rows)

    # ---- quality: deviations, IQC, NCRs, CAPA
    _ins(c, "deviation", [d[:9] + (d[9], d[10], d[11], d[12], d[13], None, d[14]) for d in w.deviations],
         ["deviation_id", "title", "item_id", "supplier_id", "site_id", "reason", "qty_limit", "qty_used",
          "valid_from", "valid_to", "status", "risk", "requested_by", "approved_by", "approved_at", "eco_id"])
    c.execute("UPDATE deviation SET approved_at = valid_from WHERE status IN ('APPROVED','CLOSED')")
    dev_lot = w.stories.get("dev15_lot")
    qe = []
    for r in w.iqc:
        qe.append((r["qe_id"], "IQC", r["site"], r["item"], r["lot"], None, r["supplier"], r["defect"], r["n"], r["bad"],
                   r["result"], r["disposition"], r["status"], r["detected_at"], r["closed_at"], r["root_cause"],
                   r["ncr"], None, r["cost"]))
    for r in w.ncrs:
        qe.append((r["qe_id"], "IQC" if r["qe_id"] == "NCR-0031" else "INLINE", r["site"], r["item"], r.get("lot"), None,
                   r["supplier"], r["defect"], r["n"], r["bad"], r["result"], r["disposition"], r["status"],
                   r["detected_at"], r["closed_at"], r["root_cause"], r["qe_id"], None, r["cost"]))
    if dev_lot:
        lot = w.lots[dev_lot]
        qe.append(("QE-DEV15", "AUDIT", "CM-TXG", "TIR-1", dev_lot, None, "NTR", "IQC-COS", 50, 50, "CONDITIONAL",
                   "USE_AS_IS", "CLOSED", lot["received_at"], lot["received_at"] + dt.timedelta(days=1),
                   "Print head offset at NTR; cosmetic only", None, "DEV-0015", 0.0))
    _ins(c, "quality_event", qe)
    a = w.as_of
    _ins(c, "capa", [
        ("CAPA-0012", "NCR-0019", "BAY", "Seal leak on cold mornings (EPDM)", "D8",
         "100% leak test kept; heated staging for gaskets", "EPDM compression set below 10°C",
         "ECO-0036 silicone gasket", "Low-temp validation added to gasket spec", "Battery eng.",
         at(add_days(a, -66), 12, 0, PT), add_days(a, -30), at(w.eco36, 12, 0, PT), "CLOSED", None),
        ("CAPA-0014", "NCR-0031", "SMT", "Enclosure sealing-face flatness reject", "D6",
         "Lot RTV; 100% CMM on next 3 lots", "Die wear; in-process check skipped on shift 3",
         "Die insert replaced; flatness check made a hard stop in SMT's MES", None, "SQE",
         at(add_days(a, -34), 16, 0, PT), add_days(a, 10), None, "CONTAINED", None),
        ("CAPA-0015", "NCR-0027", None, "Drive-unit connector not seated at S20", "D4",
         "Pull test added at S20", "Latch not fully engaged; no click detection", None, None, "FAP quality",
         at(add_days(a, -8), 11, 0, TPE), add_days(a, 20), None, "OPEN", None),
    ])

    _ins(c, "rfq", w.rfq_rows)
    _ins(c, "rfq_quote", w.rfq_quotes)
    _ins(c, "replenishment_policy", w.policy_rows)
    _ins(c, "freight_rate", w.rate_rows, ["carrier", "lane", "basis", "rate_usd", "valid_from", "valid_to", "contract"])

    # ---- platform
    _ins(c, "process_activity", ACTIVITIES)
    _ins(c, "process_event", w.process_rows, ["process", "case_id", "activity", "ts", "actor_role"])
    _ins(c, "app_telemetry", w.telemetry_rows, ["app", "ts", "user_role", "feature", "duration_s", "outcome"])
    feat_of = {cap: f for _, f, cap, _, _ in FEATURES}
    _ins(c, "capability", [(cid, dom, name, desc, wgt, feat_of.get(cid)) for cid, dom, name, desc, wgt in CAPABILITIES])
    _ins(c, "vendor", VENDORS)
    _ins(c, "vendor_capability", [(v, cap, f) for v, caps in FIT.items() for cap, f in caps.items()])
    _ins(c, "source_system", SYSTEMS)
    _ins(c, "integration_option", OPTIONS)
    _ins(c, "eval_suite", SUITES)
    _ins(c, "eval_golden", w.golden_rows)

    # ---- landing zone
    _mapping_versions(w)
    _raw_cm_mes(w)
    _raw_asn(w)
    _raw_carrier(w)
    _raw_3pl(w)
    _raw_confirmations(w)
    _raw_emails(w)
    _raw_warranty(w)


def _mapping_versions(w):
    codes = {v[0]: k for k, v in CM_DEFECT.items()}
    desc = {v[1]: k for k, v in CM_DEFECT.items()}
    v1 = {"fields": {"serial": "sn", "station": "stn_cd", "line": "line", "event_time": "evt_ts", "result": "rslt",
                     "defect_code": "ng_cd", "defect_text": "ng_desc", "operator": "op", "parts": "parts",
                     "parts_out": "parts_out", "lots": "lots", "measurements": "meas", "message_id": "msg_id"},
          "part_keys": {"item": "pn", "serial": "sn"}, "lot_keys": {"item": "pn", "lot": "lot", "qty": "qty"},
          "time": {"format": "%Y-%m-%d %H:%M:%S", "tz": "Asia/Taipei"},
          "result_map": {"OK": "PASS", "NG": "FAIL", "RWK": "REWORK", "SCR": "SCRAP"},
          "defect_codes": codes, "defect_text": desc,
          "stations": {f"S{n}0": f"S{n}0" for n in range(1, 9)},
          "models": {v: k for k, v in CM_PN.items()}}
    v1["fields"].update({"model": "model", "work_order": "wo"})
    v2 = json.loads(json.dumps(v1))
    v2["fields"] = {"serial": "serial", "station": "stationCode", "line": "lineId", "event_time": "eventTime",
                    "result": "result", "defect_code": "defectCode", "defect_text": "defectDesc",
                    "operator": "operatorId", "parts": "components", "parts_out": "componentsRemoved",
                    "lots": "lotConsumption", "measurements": "measurements", "message_id": "messageId",
                    "model": "modelNo", "work_order": "workOrder"}
    v2["part_keys"] = {"item": "partNo", "serial": "serialNo"}
    v2["lot_keys"] = {"item": "partNo", "lot": "lotNo", "qty": "qty"}
    v2["time"] = {"format": "iso8601", "tz": "Asia/Taipei"}
    v2["result_map"] = {"PASS": "PASS", "FAIL": "FAIL", "REWORK": "REWORK", "SCRAP": "SCRAP"}
    _ins(w.conn, "mapping_version", [
        ("CM_MES", "v1", _t(at(add_days(w.cm_sop, -14), 9, 0, TPE).astimezone(UTC)), json.dumps(v1, ensure_ascii=False),
         "Initial mapping for FAP MES JSON (flat keys, local time without offset)"),
        ("CM_MES", "v2", _t(w.mapping_v2_at), json.dumps(v2, ensure_ascii=False),
         "FAP MES upgrade: camelCase keys, ISO-8601 with offset, English result codes"),
    ])


def _slot_item(slot, w):
    if slot.get("item"):
        return slot["item"]
    return w.units.get(slot["serial"], {}).get("item") or {"PU": "PU-1", "HMI": "HMI-1", "FRAME": "FRM-1"}.get(slot["kind"])


def _raw_cm_mes(w):
    rng = w.rng
    msgs = []
    seq = {"L1": 0, "L2": 0}
    for v in w.vehicles:
        line = v["line"]
        for ev in v["events"]:
            t = ev["t"]
            seq[line] += 1
            local = t.astimezone(TPE)
            v2 = t >= w.schema_cutover
            meas = dict(ev["meas"])
            if meas.get("du_sn") == "@DU":
                meas["du_sn"] = [s for (ts, s) in v["du_slots"] if ts <= t][-1]["serial"]
            parts = [{"item": _slot_item(p, w), "serial": p["serial"]} for p in ev["parts"]]
            parts_out = [{"item": _slot_item(p, w), "serial": p["serial"]} for p in ev["parts_out"]]
            lots = [{"item": l["item"], "lot": l["lot"], "qty": l["qty"]} for l in ev.get("lots", [])]
            dcode, dtext = CM_DEFECT.get(ev["defect"], ("", "")) if ev["defect"] else ("", "")
            op = f"A{1000 + (hash_int(v['serial']) % 90)}"
            if not v2:
                ts = local.strftime("%Y-%m-%d %H:%M:%S")
                if w.tz_bug[0] <= local.date() <= w.tz_bug[1]:
                    ts = local.strftime("%Y-%m-%dT%H:%M:%SZ")        # local clock labeled as UTC
                if dcode and rng.random() < 0.15:
                    dcode = ""                                          # only the Chinese description is sent
                payload = {"msg_id": f"FAP{line}-{seq[line]:07d}", "sn": v["serial"], "stn_cd": ev["code"],
                           "line": line, "model": CM_PN[v["sku"]], "wo": v["wo_id"], "evt_ts": ts, "rslt": CM_RESULT_V1[ev["result"]], "ng_cd": dcode,
                           "ng_desc": dtext, "op": op,
                           "parts": [{"pn": p["item"], "sn": p["serial"]} for p in parts],
                           "parts_out": [{"pn": p["item"], "sn": p["serial"]} for p in parts_out],
                           "lots": [{"pn": l["item"], "lot": l["lot"], "qty": l["qty"]} for l in lots], "meas": meas}
                version = "v1"
            else:
                payload = {"messageId": hashlib.md5(f"{v['serial']}{ev['code']}{iso(t)}".encode()).hexdigest()[:16],
                           "serial": v["serial"], "stationCode": ev["code"], "lineId": line,
                           "modelNo": CM_PN[v["sku"]], "workOrder": v["wo_id"],
                           "eventTime": local.isoformat(), "result": ev["result"], "defectCode": dcode,
                           "defectDesc": dtext, "operatorId": op,
                           "components": [{"partNo": p["item"], "serialNo": p["serial"]} for p in parts],
                           "componentsRemoved": [{"partNo": p["item"], "serialNo": p["serial"]} for p in parts_out],
                           "lotConsumption": [{"partNo": l["item"], "lotNo": l["lot"], "qty": l["qty"]} for l in lots],
                           "measurements": meas, "schemaVersion": "2.0"}
                version = "v2"
            latency = rng.lognormvariate(3.4, 0.55)
            if rng.random() < 0.01:
                latency += rng.uniform(300, 1800)
            received = t + dt.timedelta(seconds=latency)
            if w.cm_outage[0] <= t < w.cm_outage[1]:
                received = w.cm_outage[1] + dt.timedelta(minutes=5, seconds=0.4 * len(msgs) % 600)
            msgs.append((received, payload, version, t))
    # gateway retry storm: messages from the previous three hours resent with new ids
    s0, s1 = w.retry_storm
    window = [m for m in msgs if s0 - dt.timedelta(hours=3) <= m[3] < s0 and m[2] == "v1"]
    for i, (rec, payload, version, t) in enumerate(window[:350]):
        dup = dict(payload, msg_id=payload["msg_id"] + "-R1")
        msgs.append((s0 + (s1 - s0) * (i / max(1, len(window[:350]))), dup, version, t))
    msgs.sort(key=lambda m: m[0])
    _ins(w.conn, "raw_cm_mes_event",
         [("FAP-MES", m[0], json.dumps(m[1], ensure_ascii=False)) for m in msgs],
         ["source_system", "received_at", "payload"])


def hash_int(s):
    return int(hashlib.md5(s.encode()).hexdigest()[:8], 16)


def _raw_asn(w):
    rows = []
    for item, dlvs in w.deliveries.items():
        if item not in ("DU-B", "DU-C", "PU-1", "HMI-1", "BMS-A", "BMS-B"):
            continue
        for dlv in dlvs:
            if dlv.get("no_asn") or dlv["asn_received_at"] > w.now:
                continue
            items = []
            for s in dlv["serials"]:
                u = w.units[s]
                rec = {"part": u["item"], "serial": s, "rev": u["item"][-1]}
                if item.startswith("DU"):
                    rec["children"] = [{"part": "MTR-1", "serial": u["motor"], "magnet_lot": u["magnet_lot"]},
                                       {"part": w.units[u["ctl"]]["item"], "serial": u["ctl"]}]
                items.append(rec)
            ship_local = (dlv["arrival"] - dt.timedelta(days=1)).astimezone(TPE if dlv["site"] == "CM-TXG" else PT)
            payload = {"asn_no": dlv["asn_no"], "supplier": dlv["supplier"], "ship_date": ship_local.date().isoformat(),
                       "ship_to": dlv["site"], "po": (dlv.get("po_ref") or (None, None))[0],
                       "po_line": (dlv.get("po_ref") or (None, None))[1], "eta": iso(dlv["arrival"]),
                       "qty": dlv["qty"], "items": items}
            rows.append((dlv["supplier"], dlv["asn_received_at"], json.dumps(payload)))
    rows.sort(key=lambda r: r[1])
    _ins(w.conn, "raw_supplier_asn", rows, ["supplier_id", "received_at", "payload"])
    # magnet lots arrive with the drive-unit ASNs; the lot master comes from the supplier's lot list
    mags = [l for l in w.lots.values() if l["item"] == "MAG-NDFEB"]
    w.conn.executemany("UPDATE lot SET origin='SUPPLIER_ASN' WHERE lot_id=?", [(l["lot_id"],) for l in mags])


def _raw_carrier(w):
    rng = w.rng
    rows = []
    for ctn in w.containers:
        for e in ctn["events"]:
            code = e[0]
            t = e[1]
            if t > w.now:
                continue
            if code == "ETA_UPDATE":
                payload = json.dumps({"container": ctn["container_no"], "type": "ETA_UPDATE", "eta": iso(e[2]),
                                      "reason": e[3], "vessel": ctn["vessel"], "voyage": ctn["voyage"]})
                rows.append(("PLL", t + dt.timedelta(minutes=rng.randint(1, 20)), "JSON", payload))
                continue
            if code not in EDI315:
                continue
            q, loc = EDI315[code]
            tz = TPE if loc == "TWTXG" else PT
            payload = f"{ctn['container_no']}|{q}|{t.astimezone(tz).isoformat()}|{loc}|{ctn['vessel']}|{ctn['voyage']}"
            rec = t + dt.timedelta(minutes=rng.randint(5, 60))
            rows.append(("PLL", rec, "EDI315", payload))
            if rng.random() < 0.03:
                rows.append(("PLL", rec + dt.timedelta(minutes=rng.randint(30, 300)), "EDI315", payload))
    for tr in w.trucks:
        for status, t in (("PICKED_UP", tr["pickup"]), ("DELIVERED", tr["arrival"])):
            if t <= w.now:
                rows.append(("SDG", t + dt.timedelta(minutes=rng.randint(1, 15)), "JSON",
                             json.dumps({"pro": tr["pro"], "status": status, "ts": iso(t),
                                         "location": "Fremont, CA" if status == "PICKED_UP" else "Reno, NV"})))
    for lm in w.last_mile:
        cid = "CWF" if lm["carrier"].startswith("Crossway") else "PPG"
        events = [("PICKED_UP", lm["ship"], "Reno, NV")]
        if lm["exception"]:
            events.append(("EXCEPTION", lm["exception"], "Delivery attempted: customer unavailable"))
        events += [("OUT_FOR_DELIVERY", lm["ofd"], lm["order"]["state"]),
                   ("DELIVERED", lm["delivered"], lm["order"]["state"])]
        for status, t, note in events:
            if t <= w.now:
                rows.append((cid, t + dt.timedelta(minutes=rng.randint(1, 30)), "JSON",
                             json.dumps({"tracking": lm["tracking"], "carrier": cid, "status": status, "ts": iso(t),
                                         "note": note})))
    rows.sort(key=lambda r: r[1])
    _ins(w.conn, "raw_carrier_event", rows, ["carrier", "received_at", "format", "payload"])


def _raw_3pl(w):
    rng = w.rng
    rows = []
    for ctn in w.containers:
        if ctn["received_at"] > w.now:
            continue
        payload = {"receipt_no": f"RCV-{w.nid('rcv'):05d}", "ref_type": "CONTAINER", "ref": ctn["container_no"],
                   "received_at": iso(ctn["received_at"]),
                   "lines": [{"sku": k, "serial": s} for s, k in zip(ctn["units"], ctn["skus"])],
                   "exceptions": [{"serial": s, "code": "DAMAGED", "note": "Carton crushed; frame paint damage"}
                                  for s in ctn["damaged"]]}
        rows.append((ctn["received_at"] + dt.timedelta(minutes=rng.randint(20, 90)), "RECEIPT", json.dumps(payload)))
    for tr in w.trucks:
        if tr["received_at"] > w.now:
            continue
        payload = {"receipt_no": f"RCV-{w.nid('rcv'):05d}", "ref_type": "TRAILER", "ref": tr["pro"],
                   "received_at": iso(tr["received_at"]),
                   "lines": [{"sku": k, "serial": s} for s, k in zip(tr["units"], tr["skus"])], "exceptions": []}
        rows.append((tr["received_at"] + dt.timedelta(minutes=rng.randint(10, 60)), "RECEIPT", json.dumps(payload)))
    for o in w.orders:
        if not o.get("allocated_at"):
            continue
        lines = [{"line": 1, "sku": o["kit"], "vehicle": o["vehicle_serial"], "pack": o["pack_serial"], "charger": "CHG-1"}]
        if o["extra_pack_serial"]:
            lines.append({"line": 2, "sku": o["extra_pack"], "pack": o["extra_pack_serial"]})
        rows.append((o["allocated_at"] + dt.timedelta(minutes=rng.randint(1, 10)), "ALLOCATION",
                     json.dumps({"order_id": o["order_id"], "allocated_at": iso(o["allocated_at"]), "lines": lines})))
        lm = o.get("last_mile")
        if lm:
            cid = "CWF" if lm["carrier"].startswith("Crossway") else "PPG"
            rows.append((lm["ship"] + dt.timedelta(minutes=rng.randint(5, 40)), "SHIP_CONFIRM",
                         json.dumps({"order_id": o["order_id"], "shipped_at": iso(lm["ship"]), "carrier": cid,
                                     "service": lm["mode"], "tracking": lm["tracking"], "serials": lm["serials"]})))
    last = len(w.wms_snapshots) - 1
    for i, (t, counts) in enumerate(w.wms_snapshots):
        items = []
        for sku, qty in sorted(counts.items()):
            avail = qty + (1 if sku == "LV1-FERN" and i >= last - 2 else 0)     # a unit on the floor, not on a scan
            items.append({"sku": sku, "available": avail, "allocated": 0, "hold": 0})
        for sku in ("LV1-DUNE", "LV1-EMBER", "LV1-FERN", "LV1-SLATE"):
            held = sum(1 for s in w.damaged if w.receipt_time.get(s) and w.receipt_time[s] <= t
                       and s.startswith("LV1") and _sku_of(w, s) == sku)
            if held:
                items.append({"sku": sku, "available": 0, "allocated": 0, "hold": held})
        rows.append((t + dt.timedelta(minutes=rng.randint(5, 25)), "INVENTORY_SNAPSHOT",
                     json.dumps({"snapshot_at": iso(t), "site": "RNO", "counts": items})))
    rows.sort(key=lambda r: r[0])
    _ins(w.conn, "raw_3pl_message", rows, ["received_at", "msg_type", "payload"])


def _sku_of(w, serial):
    cache = w.__dict__.setdefault("_sku", {v["serial"]: v["sku"] for v in w.vehicles})
    return cache.get(serial)


def _raw_confirmations(w):
    rows = []
    for r in w.raw_confirmations:
        if r["t"] <= w.now:
            rows.append((r["supplier"], r["t"] + dt.timedelta(minutes=w.rng.randint(1, 20)), r["channel"], r["payload"]))
    _ins(w.conn, "raw_supplier_confirmation", rows, ["supplier_id", "received_at", "channel", "payload"])
    golden = []
    for i, r in enumerate([r for r in w.raw_confirmations if r["t"] <= w.now]):
        golden.append(("EV-PROMISE-PARSE", f"conf:{i + 1}", f"raw_supplier_confirmation:{i + 1}", json.dumps(r["truth"])))
    _ins(w.conn, "eval_golden", golden)


def _raw_emails(w):
    rows, golden = [], []
    for i, e in enumerate([e for e in w.emails if e["received_at"] <= w.now]):
        rid = i + 1
        rows.append((rid, e["mailbox"], e["received_at"], e["from"], e["to"], e["subject"], e["message_id"], e["body"],
                     e["mime"]))
        truth = e.get("truth", {})
        if truth.get("kind") == "PROMISE_TEXT":
            golden.append(("EV-PROMISE-PARSE", f"email:{rid}", f"raw_email:{rid}", json.dumps(truth)))
        elif truth.get("kind") == "PNC_OPEN_ORDERS":
            for p in truth["promises"]:
                golden.append(("EV-PROMISE-PARSE", f"xlsx:{rid}:{p['po']}-{p['line']}", f"raw_email:{rid}", json.dumps(p)))
    _ins(w.conn, "raw_email", rows, ["raw_id", "mailbox", "received_at", "from_addr", "to_addr", "subject", "message_id",
                                     "body_text", "mime"])
    _ins(w.conn, "eval_golden", golden)


def _raw_warranty(w):
    rows = []
    for cse in w.claims:
        o = cse["order"]
        parts = []
        if cse["part_item"]:
            parts.append({"part": cse["part_item"], "removed_serial": cse["removed"], "installed_serial": cse["installed"]})
        payload = {"case_no": cse["case_no"], "opened_at": iso(cse["reported_at"]), "order_ref": o["order_id"],
                   "asset_serial": cse["vehicle"], "category": cse["category"], "symptom": cse["symptom"],
                   "resolution": cse["resolution"] if cse["status"] in ("REPAIRED", "CLOSED") else "",
                   "parts_replaced": parts if cse["status"] in ("REPAIRED", "CLOSED") else [],
                   "labor_hours": cse["labor_h"], "parts_cost": cse["parts_usd"], "logistics_cost": cse["logistics"],
                   "status": cse["status"]}
        cse["payload_parts"] = payload["parts_replaced"]
        rows.append((cse["reported_at"] + dt.timedelta(minutes=w.rng.randint(1, 5)), json.dumps(payload)))
    _ins(w.conn, "raw_warranty_case", rows, ["received_at", "payload"])


# ======================================================================== the OEM's own MES (pack line)

def _installed(use, cell_rejects):
    """What each lot drawn put into the pack. A cell rejected at P10 was drawn but never installed: take it off the lot
    that supplied the most, so the pack holds its BOM count even when its cells straddle two lots, and no lot it drew
    from drops out of its genealogy."""
    alloc = [list(a) for a in use["alloc"]]
    if use["family"] == "CEL-21700" and cell_rejects:
        max(alloc, key=lambda a: a[1])[1] -= cell_rejects
    return [tuple(a) for a in alloc if a[1] > 0]


def oem_mes(w):
    c = w.conn
    units, events, gen = [], [], []
    for p in w.packs:
        if not p["events"]:
            continue
        units.append((p["serial"], p["sku"], "OEM_MES", None, "OEM-FRE", "P1", p["wo_id"], p["built_at"],
                      "SCRAPPED" if p["scrapped"] else ("BUILT" if p["built_at"] else "WIP"), "OEM-FRE", 0, None,
                      "BMS fw 4.2.0"))
        for ev in p["events"]:
            events.append((p["serial"], f"FRE-P1-{ev['code']}", ev["t"], ev["result"], ev["defect"],
                           f"F{200 + hash_int(p['serial']) % 40}", json.dumps(ev["meas"]) if ev["meas"] else None,
                           "OEM_MES", None))
            for part in ev["parts"]:
                gen.append((p["serial"], part["serial"], None, w.units[part["serial"]]["item"], 1, "INSTALLED", "BMS",
                            f"FRE-P1-{ev['code']}", ev["t"], None, None, "OEM_MES"))
            for part in ev["parts_out"]:
                c_row = [g for g in gen if g[0] == p["serial"] and g[1] == part["serial"]]
                if c_row:
                    idx = gen.index(c_row[-1])
                    g = list(gen[idx])
                    g[9], g[10] = ev["t"], "Replaced at rework (" + (ev["defect"] or "") + ")"
                    gen[idx] = tuple(g)
            for use in ev.get("uses", []):
                for lot_id, q, item in _installed(use, p.get("cell_rejects", 0)):
                    pos = {"CEL-21700": "CELLS", "BUS-S": "BUSBAR", "BUS-L": "BUSBAR", "HRN-PK": "HARNESS",
                           "ENC-STD": "ENCLOSURE", "ENC-LRG": "ENCLOSURE", "GSK-A": "GASKET", "GSK-B": "GASKET"}[item]
                    gen.append((p["serial"], None, lot_id, item, q, "INSTALLED", pos, f"FRE-P1-{ev['code']}", use["t"],
                                None, None, "OEM_MES"))
    # a reseal replaces the gasket: close the first gasket edge
    by_pack = {}
    for i, g in enumerate(gen):
        if g[6] == "GASKET":
            by_pack.setdefault(g[0], []).append(i)
    for serial, idxs in by_pack.items():
        for i in idxs[:-1]:
            g = list(gen[i])
            g[9], g[10] = gen[idxs[-1]][8], "Resealed after leak-test failure"
            gen[i] = tuple(g)
    _ins(c, "unit", units)
    _ins(c, "station_event", events, ["serial", "station_id", "event_ts", "result", "defect_code", "operator_id",
                                      "measurements", "source", "raw_id"])
    _ins(c, "genealogy", gen, ["parent_serial", "child_serial", "child_lot_id", "child_item_id", "qty", "relation",
                               "position", "station_id", "installed_at", "removed_at", "removal_reason", "source"])
    # BMS boards not installed (on hand at Fremont) are already units from the ASN feed


# ======================================================================== shipments, customs

def shipments(w):
    c = w.conn
    rng = w.rng
    rows, su, ev, entries = [], [], [], []
    for ctn in w.containers:
        arrived = ctn["ata_truth"] <= w.now
        rows.append((ctn["shipment_id"], "CM_TO_3PL", "OCEAN", "CM-TXG", "3PL-RNO", None, "PLL", ctn["booking_ref"],
                     ctn["container_no"], ctn["vessel"], ctn["voyage"], ctn["bol_no"], None, "PORT-TWTXG",
                     "PORT-USOAK", ctn["etd"], ctn["eta_planned"], ctn["eta_planned"], None, None, None, "BOOKED",
                     len(ctn["asn_serials"]), None, 3400.0 if ctn["load_day"] >= add_days(w.as_of, -20) else 2850.0))
        for s in ctn["asn_serials"]:
            su.append((ctn["shipment_id"], s))
        booked = next(e[1] for e in ctn["events"] if e[0] == "BOOKED")
        ev.append((ctn["shipment_id"], booked, "BOOKED", "Taichung", f"Booking {ctn['booking_ref']} confirmed", "OEM", None))
        loaded = next(e[1] for e in ctn["events"] if e[0] == "LOADED")
        units = len(ctn["asn_serials"])
        value = 0.0
        for s in ctn["asn_serials"]:
            value += 1265.0 if loaded.date() < w.cm_price_step else 1248.0
            value += 188.40 + 64.0 + 58.0                               # assists: consigned DU, HMI, PU
        value = round(value, 2)
        entry_filed = ctn["entry_filed_at"]
        released = ctn["released_at"]
        if ctn["exam"]:
            status = "EXAM"
        elif released <= w.now:
            status = "RELEASED"
        elif entry_filed <= w.now:
            status = "ENTRY_FILED"
        else:
            status = "ISF_FILED"
        if ctn["isf_filed_at"] > w.now:
            continue
        entries.append((f"G7K-{w.nid('entry'):07d}-{rng.randint(0, 9)}", ctn["shipment_id"], "Bayline Customs Brokerage",
                        ctn["isf_filed_at"], entry_filed if entry_filed <= w.now else None, "8711.60.00", value,
                        0.15, round(value * 0.15, 2), round(min(max(value * 0.003464, 33.58), 651.50), 2),
                        round(value * 0.00125, 2), status, "CET intensive exam" if ctn["exam"] else None,
                        released if (released <= w.now and not ctn["exam"]) else None))
        ev.append((ctn["shipment_id"], ctn["isf_filed_at"], "CUSTOMS_FILED", "Oakland", "ISF (10+2) filed", "BROKER", None))
        if entry_filed <= w.now:
            ev.append((ctn["shipment_id"], entry_filed, "CUSTOMS_FILED", "Oakland", "Entry summary (7501) filed", "BROKER", None))
        for e in ctn["events"]:
            if e[0] == "CUSTOMS_HOLD" and e[1] <= w.now:
                ev.append((ctn["shipment_id"], e[1], "CUSTOMS_HOLD", "Oakland", "CBP CET intensive exam ordered", "BROKER", None))
    for tr in w.trucks:
        rows.append((tr["shipment_id"], "PLANT_TO_3PL", "TRUCK_LTL_DG", "OEM-FRE", "3PL-RNO", None, "SDG", None, None,
                     None, None, None, tr["pro"], None, None, tr["pickup"], tr["arrival"], tr["arrival"], None, None, None,
                     "BOOKED", len(tr["units"]), "9", 1180.0))
        for s in tr["units"]:
            su.append((tr["shipment_id"], s))
    # inbound supplier shipments currently in transit (cells on the water, enclosures on a truck, BMS by air)
    for ln in w.po_lines:
        if ln["received"] or not ln.get("history"):
            continue
        promise = ln["history"][-1]["promise"]
        lead = {"KES": 18, "SMT": 4, "PNC": 4, "VPC": 18}.get(ln["supplier"])
        if not lead or promise > add_days(w.as_of, lead):
            continue
        sid = f"IB-{ln['po_id']}-{ln['line_no']}"
        mode = {"KES": "OCEAN", "SMT": "TRUCK_FTL", "PNC": "AIR", "VPC": "OCEAN"}[ln["supplier"]]
        carrier = {"OCEAN": "PLL", "TRUCK_FTL": "SDG", "AIR": "SKA"}[mode]
        etd = at(add_days(promise, -lead), 10, 0, PT).astimezone(UTC)
        dest = "3PL-RNO" if ln["item"] == "CHG-1" else ("CM-TXG" if ln["site"] == "CM-TXG" else "OEM-FRE")
        rows.append((sid, "SUPPLIER_TO_PLANT", mode, f"SUP-{ln['supplier']}", dest, None, carrier, None, None, None,
                     None, None, None, None, None, etd, at(promise, 9, 0, PT).astimezone(UTC),
                     at(promise, 9, 0, PT).astimezone(UTC), etd if etd <= w.now else None, None, None,
                     "IN_TRANSIT" if etd <= w.now else "BOOKED", int(ln["qty"]), "9" if ln["item"] == "CEL-21700" else None,
                     None))
    cols = ["shipment_id", "leg", "mode", "origin_site_id", "dest_site_id", "order_id", "carrier", "booking_ref",
            "container_no", "vessel", "voyage", "bol_no", "tracking_no", "pol_site_id", "pod_site_id", "etd_planned",
            "eta_planned", "eta_current", "atd", "ata", "received_at", "status", "asn_qty", "dg_class", "freight_usd"]
    _ins(c, "shipment", rows, cols)
    _ins(c, "shipment_unit", su)
    _ins(c, "shipment_event", ev, ["shipment_id", "event_ts", "code", "location", "detail", "source", "raw_ref"])
    _ins(c, "customs_entry", entries)
