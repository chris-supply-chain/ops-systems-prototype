"""CM MES normalizer + supplier ASN normalizer, processed together in arrival order.

The mapping from the CM's payload to our model is data (mapping_version), not
code: field names, time format and zone, result codes, defect codes and the
Chinese defect text, station aliases and model numbers. When the CM upgraded its
MES the mapping got a new version, and the messages quarantined in between were
replayed. Nothing is silently dropped. Every message ends OK, WARN, DUPLICATE,
QUARANTINED or REPLAYED, with a reason.
"""
import json

from .common import Run, bump_status, iso, local_to_utc, parse_iso

POSITION = {"FRM-1": "FRAME", "DU-B": "DRIVE_UNIT", "DU-C": "DRIVE_UNIT", "PU-1": "PEDAL_UNIT", "HMI-1": "HMI",
            "HRN-VH": "HARNESS", "WHL-F": "WHEEL_F", "WHL-R": "WHEEL_R", "TIR-1": "TIRES", "BRK-1": "BRAKES"}


class CmMes:
    def __init__(self, conn, run):
        self.conn, self.run = conn, run
        self.mappings = [(r["version"], r["effective_from"], json.loads(r["spec_json"])) for r in conn.execute(
            "SELECT version, effective_from, spec_json FROM mapping_version WHERE source='CM_MES' ORDER BY effective_from")]
        self.stations = {(r["line"], r["code"]): r["station_id"] for r in conn.execute(
            "SELECT station_id, line, code FROM station WHERE site_id='CM-TXG'")}
        self.items = {r["item_id"]: r for r in conn.execute("SELECT item_id, primary_supplier_id, kind FROM item")}
        self.units = {r["serial"]: r["origin"] for r in conn.execute("SELECT serial, origin FROM unit")}
        self.lots = {r["lot_id"] for r in conn.execute("SELECT lot_id FROM lot")}
        self.seen = {}
        for r in conn.execute("SELECT serial, station_id, event_ts, result, raw_id FROM station_event WHERE source='CM_FEED'"):
            self.seen[(r["serial"], r["station_id"], r["event_ts"], r["result"])] = r["raw_id"]
        self.active_version = None

    def mapping_at(self, received_at):
        current = self.mappings[0]
        for m in self.mappings:
            if m[1] <= received_at:
                current = m
        return current

    # ------------------------------------------------------------------ one message
    def process(self, raw_id, received_at, payload, replay=False):
        version, _, mp = self.mapping_at(received_at) if not replay else self.mappings[-1]
        ref = f"raw_cm_mes_event:{raw_id}"
        notes = []
        try:
            p = json.loads(payload)
        except json.JSONDecodeError as e:
            return self._quarantine(raw_id, version, f"invalid JSON: {e}")
        f = mp["fields"]
        missing = [f[k] for k in ("serial", "station", "event_time", "result") if f[k] not in p]
        if missing:
            return self._quarantine(raw_id, version, f"schema mismatch under mapping {version}: missing "
                                    f"{', '.join(missing)}; payload has {', '.join(sorted(p)[:6])}")
        serial, code, line = p[f["serial"]], p[f["station"]], p.get(f["line"])
        code = mp["stations"].get(code, code) if code in mp["stations"] else code
        station_id = self.stations.get((line, code))
        if station_id is None:
            return self._quarantine(raw_id, version, f"unknown station {p[f['station']]} on {line} "
                                    f"(not in station master or mapping {version})")
        # time: the CM sends local Taipei time; one week it labeled local time as UTC
        raw_ts = p[f["event_time"]]
        try:
            if mp["time"]["format"] == "iso8601" or raw_ts.endswith("Z") or "T" in raw_ts:
                ts = parse_iso(raw_ts)
                if raw_ts.endswith("Z") and ts > parse_iso(received_at).replace(microsecond=0) + _minutes(5):
                    ts = local_to_utc(raw_ts[:-1].replace("T", " "), "%Y-%m-%d %H:%M:%S", mp["time"]["tz"])
                    notes.append("TZ_CORRECTED: timestamp labeled UTC was Taipei local (8h ahead of receipt)")
            else:
                ts = local_to_utc(raw_ts, mp["time"]["format"], mp["time"]["tz"])
        except ValueError:
            return self._quarantine(raw_id, version, f"unparseable event time {raw_ts!r}")
        ts_s = iso(ts)
        result = mp["result_map"].get(p[f["result"]])
        if result is None:
            return self._quarantine(raw_id, version, f"unknown result code {p[f['result']]!r}")
        key = (serial, station_id, ts_s, result)
        if key in self.seen:
            self.conn.execute("UPDATE raw_cm_mes_event SET ingest_status='DUPLICATE', ingest_note=?, mapping_version=?,"
                              " run_id=? WHERE raw_id=?",
                              (f"same event already ingested from raw_id {self.seen[key]}", version, self.run.run_id, raw_id))
            self.run.counts["duplicate"] += 1
            self.run.step(ref, 1, "VALIDATED", "SKIPPED", received_at, "duplicate of an ingested event")
            return "DUPLICATE"
        defect = None
        code_raw = p.get(f["defect_code"]) or ""
        text_raw = p.get(f["defect_text"]) or ""
        if code_raw:
            defect = mp["defect_codes"].get(code_raw)
        elif text_raw:
            defect = mp["defect_text"].get(text_raw)
            if defect:
                notes.append(f"defect mapped from Chinese text '{text_raw}'")
        if result == "FAIL" and defect is None:
            notes.append(f"unmapped defect {code_raw or text_raw!r}")

        model = p.get(f.get("model", "model")) or p.get("modelNo")
        sku = mp.get("models", {}).get(model)
        if serial not in self.units:
            if sku is None:
                return self._quarantine(raw_id, version, f"first sight of {serial} without a known model ({model!r})")
            wo = p.get(f.get("work_order", "wo")) or p.get("workOrder")
            wo_ok = wo and self.conn.execute("SELECT 1 FROM work_order WHERE wo_id=?", (wo,)).fetchone()
            self.conn.execute("INSERT INTO unit(serial, item_id, origin, supplier_id, build_site_id, line, wo_id, status,"
                              " location_site_id) VALUES (?,?,?,?,?,?,?,?,?)",
                              (serial, sku, "CM_FEED", "FAP", "CM-TXG", line, wo if wo_ok else None, "WIP", "CM-TXG"))
            self.units[serial] = "CM_FEED"
        meas = p.get(f["measurements"]) or {}
        self.conn.execute(
            "INSERT INTO station_event(serial, station_id, event_ts, result, defect_code, operator_id, measurements,"
            " source, raw_id) VALUES (?,?,?,?,?,?,?,?,?)",
            (serial, station_id, ts_s, result, defect, p.get(f["operator"]), json.dumps(meas) if meas else None,
             "CM_FEED", raw_id))
        self.seen[key] = raw_id
        pk = mp["part_keys"]
        for part in p.get(f["parts_out"]) or []:
            self.conn.execute("UPDATE genealogy SET removed_at=?, removal_reason=? WHERE parent_serial=? AND child_serial=?"
                              " AND removed_at IS NULL", (ts_s, f"Removed at {code} rework ({defect or 'n/a'})",
                                                          serial, part[pk["serial"]]))
        for part in p.get(f["parts"]) or []:
            child, item = part[pk["serial"]], part[pk["item"]]
            if child not in self.units:
                self.conn.execute("INSERT INTO unit(serial, item_id, origin, supplier_id, status) VALUES (?,?,?,?,?)",
                                  (child, item, "PROVISIONAL" if item != "FRM-1" else "CM_FEED",
                                   self.items[item]["primary_supplier_id"], "INSTALLED"))
                self.units[child] = "PROVISIONAL" if item != "FRM-1" else "CM_FEED"
                if item != "FRM-1":
                    notes.append(f"component {child} not yet known (no supplier ASN): created provisional unit")
            active = self.conn.execute("SELECT 1 FROM genealogy WHERE parent_serial=? AND child_serial=? AND removed_at IS NULL",
                                       (serial, child)).fetchone()
            if not active:
                self.conn.execute(
                    "INSERT INTO genealogy(parent_serial, child_serial, child_item_id, qty, relation, position, station_id,"
                    " installed_at, source) VALUES (?,?,?,?,?,?,?,?,?)",
                    (serial, child, item, 1, "INSTALLED", POSITION.get(item), station_id, ts_s, "CM_FEED"))
        lk = mp["lot_keys"]
        for lot in p.get(f["lots"]) or []:
            lot_id = lot[lk["lot"]]
            if lot_id not in self.lots:
                notes.append(f"unknown lot {lot_id}; edge skipped")
                continue
            added = self.conn.execute(
                "INSERT INTO genealogy(parent_serial, child_lot_id, child_item_id, qty, relation, position, station_id,"
                " installed_at, source) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                (serial, lot_id, lot[lk["item"]], lot[lk["qty"]], "INSTALLED", POSITION.get(lot[lk["item"]]), station_id,
                 ts_s, "CM_FEED")).rowcount
            if not added:        # a retry the event dedup missed (its timestamp changed): the link is already current
                notes.append(f"lot {lot_id} already recorded on {serial}; not counted twice")
        if code == "S60" and result == "PASS" and meas.get("fw"):
            self.conn.execute("UPDATE unit SET firmware=? WHERE serial=?", (meas["fw"], serial))
        if code == "S80" and result == "PASS":
            self.conn.execute("UPDATE unit SET built_at=? WHERE serial=?", (ts_s, serial))
            bump_status(self.conn, serial, "BUILT", "CM-TXG")
        if result == "SCRAP":
            self.conn.execute("UPDATE unit SET status='SCRAPPED', location_site_id='CM-TXG' WHERE serial=?", (serial,))
        status = "REPLAYED" if replay else ("WARN" if notes else "OK")
        self.conn.execute("UPDATE raw_cm_mes_event SET ingest_status=?, ingest_note=?, mapping_version=?, run_id=? "
                          "WHERE raw_id=?", (status, "; ".join(notes) or None, version, self.run.run_id, raw_id))
        self.run.counts["warn" if notes else "ok"] += 1
        if notes or replay or raw_id % 50 == 0:
            self.run.step(ref, 1, "PARSED", "OK", received_at, f"mapping {version}")
            self.run.step(ref, 2, "VALIDATED", "WARN" if notes else "OK", received_at, "; ".join(notes) or "contracts ok")
            self.run.step(ref, 3, "LOADED", "OK", received_at, f"station_event for {serial} at {code}")
            if replay:
                self.run.step(ref, 4, "REPLAYED", "OK", received_at, f"replayed under mapping {version}")
        return status

    def _quarantine(self, raw_id, version, reason):
        self.conn.execute("UPDATE raw_cm_mes_event SET ingest_status='QUARANTINED', ingest_note=?, mapping_version=?, run_id=?"
                          " WHERE raw_id=?", (reason, version, self.run.run_id, raw_id))
        self.run.counts["quarantined"] += 1
        self.run.step(f"raw_cm_mes_event:{raw_id}", 1, "QUARANTINED", "FAILED",
                      self.conn.execute("SELECT received_at FROM raw_cm_mes_event WHERE raw_id=?", (raw_id,)).fetchone()["received_at"],
                      reason)
        return "QUARANTINED"

    def replay_quarantined(self, reason_prefix="schema mismatch"):
        rows = self.conn.execute("SELECT raw_id, received_at, payload FROM raw_cm_mes_event WHERE ingest_status='QUARANTINED'"
                                 " AND ingest_note LIKE ? ORDER BY received_at", (reason_prefix + "%",)).fetchall()
        n = 0
        for r in rows:
            self.run.counts["quarantined"] -= 1
            if self.process(r["raw_id"], r["received_at"], r["payload"], replay=True) == "REPLAYED":
                n += 1
        return n


def _minutes(n):
    import datetime as dt
    return dt.timedelta(minutes=n)


class Asn:
    def __init__(self, conn, run, units):
        self.conn, self.run = conn, run
        self.units = units          # shared with the MES normalizer: one view of which serials exist

    def process(self, raw_id, received_at, payload):
        p = json.loads(payload)
        notes, created = [], 0
        site = p.get("ship_to")
        for it in p["items"]:
            serial, part = it["serial"], it["part"]
            row = self.conn.execute("SELECT origin, item_id FROM unit WHERE serial=?", (serial,)).fetchone()
            if row and row["origin"] == "PROVISIONAL":
                self.conn.execute("UPDATE unit SET origin='SUPPLIER_ASN', supplier_id=?, asn_no=?, item_id=? WHERE serial=?",
                                  (p["supplier"], p["asn_no"], part, serial))
                self.units[serial] = "SUPPLIER_ASN"
                notes.append(f"resolved provisional {serial}")
            elif row is None:
                self.conn.execute("INSERT INTO unit(serial, item_id, origin, supplier_id, status, location_site_id, asn_no)"
                                  " VALUES (?,?,?,?,?,?,?)", (serial, part, "SUPPLIER_ASN", p["supplier"], "COMPONENT",
                                                              site, p["asn_no"]))
                self.units[serial] = "SUPPLIER_ASN"
                created += 1
            else:
                continue
            for ch in it.get("children", []):
                if ch["serial"] not in self.units:
                    self.conn.execute("INSERT INTO unit(serial, item_id, origin, supplier_id, status, asn_no) VALUES (?,?,?,?,?,?)",
                                      (ch["serial"], ch["part"], "SUPPLIER_ASN", p["supplier"], "INSTALLED", p["asn_no"]))
                    self.units[ch["serial"]] = "SUPPLIER_ASN"
                self.conn.execute("INSERT INTO genealogy(parent_serial, child_serial, child_item_id, qty, relation, position,"
                                  " installed_at, source) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                                  (serial, ch["serial"], ch["part"], 1, "INSTALLED",
                                   "MOTOR" if ch["part"] == "MTR-1" else "CONTROLLER", p["ship_date"] + "T00:00:00Z",
                                   "SUPPLIER_ASN"))
                if ch.get("magnet_lot"):
                    self.conn.execute("INSERT INTO genealogy(parent_serial, child_lot_id, child_item_id, qty, relation, position,"
                                      " installed_at, source) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                                      (ch["serial"], ch["magnet_lot"], "MAG-NDFEB", 0.42, "INSTALLED", "MAGNETS",
                                       p["ship_date"] + "T00:00:00Z", "SUPPLIER_ASN"))
        status = "WARN" if notes else "OK"
        self.conn.execute("UPDATE raw_supplier_asn SET ingest_status=?, ingest_note=? WHERE raw_id=?",
                          (status, (f"{created} units; " + "; ".join(notes[:5]) + (f" (+{len(notes) - 5} more)" if len(notes) > 5 else ""))
                           if notes else f"{created} units", raw_id))
        self.run.counts["warn" if notes else "ok"] += 1
        ref = f"raw_supplier_asn:{raw_id}"
        self.run.step(ref, 1, "PARSED", "OK", received_at, f"ASN {p['asn_no']}: {len(p['items'])} serials")
        self.run.step(ref, 2, "LOADED", "WARN" if notes else "OK", received_at,
                      f"{created} new units" + (f"; {len(notes)} provisional resolved" if notes else ""))


def run_cm_and_asn(conn, now_s):
    """Process CM MES events and supplier ASNs together, in the order they arrived."""
    run_mes = Run(conn, "CM_MES", now_s, "v1->v2")
    run_asn = Run(conn, "SUPPLIER_ASN", now_s)
    mes = CmMes(conn, run_mes)
    asn = Asn(conn, run_asn, mes.units)
    stream = [("M", r["raw_id"], r["received_at"], r["payload"]) for r in conn.execute(
        "SELECT raw_id, received_at, payload FROM raw_cm_mes_event WHERE ingest_status='PENDING'")]
    stream += [("A", r["raw_id"], r["received_at"], r["payload"]) for r in conn.execute(
        "SELECT raw_id, received_at, payload FROM raw_supplier_asn WHERE ingest_status='PENDING'")]
    stream.sort(key=lambda s: (s[2], s[0]))
    replayed = 0
    for kind, raw_id, received_at, payload in stream:
        version = mes.mapping_at(received_at)[0]
        if mes.active_version is not None and version != mes.active_version:
            replayed += mes.replay_quarantined()
        mes.active_version = version
        if kind == "M":
            run_mes.counts["in"] += 1
            mes.process(raw_id, received_at, payload)
        else:
            run_asn.counts["in"] += 1
            asn.process(raw_id, received_at, payload)
    run_mes.finish(now_s, f"{replayed} quarantined messages replayed after mapping change")
    run_asn.finish(now_s)
    return {"cm_mes": run_mes.counts, "asn": run_asn.counts, "replayed": replayed}
