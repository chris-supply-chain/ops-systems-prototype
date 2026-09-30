"""Make: the CM's two vehicle lines in Taichung and the OEM's pack line in Fremont,
simulated unit by unit through every station, with defects, rework, part swaps,
scrap, learning curves and line fill carried across shifts (that carry-over is
the WIP you see on the Production page).
"""
import datetime as dt
import math

from .util import PT, TPE, UTC, add_days, at, week_code, yymm

CM_FLOW = [("S10", 0), ("S20", 15), ("S30", 28), ("S40", 44), ("S50", 62), ("S60", 85), ("S70", 100), ("S80", 114)]
CM_DEFECTS = {
    "S20": [("DU-CONN", 0.006)],
    "S40": [("HRN-PINCH", 0.005), ("HMI-PIXEL", 0.004)],
    "S50": [("BRK-BLEED", 0.008)],
    "S60": [("EOL-MOTOR", 0.010), ("EOL-FW", 0.008), ("EOL-BRK", 0.004)],
    "S70": [("WTR-LEAK", 0.007)],
    "S80": [("COS-SCR", 0.006)],
}
PACK_FLOW = [("P10", 0), ("P20", 18), ("P30", 36), ("P40", 52), ("P50", 72), ("P60", 165)]
PACK_DEFECTS = {
    "P10": [("CELL-SORT", 0.004)],
    "P20": [("WELD-RES", 0.008)],
    "P30": [("BMS-COMM", 0.005)],
    "P40": [("SEAL-LEAK", 0.009)],
    "P50": [("EOL-CAP", 0.006), ("EOL-HIPOT", 0.002)],
    "P60": [("LBL-MIS", 0.001)],
}
CM_LOTS = {"WHL-F": ("WF", 450, "CHW"), "WHL-R": ("WR", 450, "CHW"), "TIR-1": ("TR", 900, "NTR"),
           "BRK-1": ("BK", 380, "TYB"), "HRN-VH": ("HV", 520, "TCW")}
COLOR_ORDER = ["LV1-DUNE", "LV1-SLATE", "LV1-FERN", "LV1-EMBER"]
COLOR_W = {"LV1-DUNE": 0.30, "LV1-SLATE": 0.34, "LV1-FERN": 0.21, "LV1-EMBER": 0.15}


def learning(t_days):
    """Defect-rate multiplier that decays as a line matures."""
    return 1.0 + 1.6 * math.exp(-t_days / 25.0)


def split(n, weights, rng):
    """Largest-remainder split of n units across weighted SKUs (with a little noise)."""
    raw = {k: n * v * rng.uniform(0.85, 1.15) for k, v in weights.items()}
    scale = n / sum(raw.values())
    raw = {k: v * scale for k, v in raw.items()}
    out = {k: int(v) for k, v in raw.items()}
    for k in sorted(raw, key=lambda k: raw[k] - out[k], reverse=True)[: n - sum(out.values())]:
        out[k] += 1
    return out


def fw_version(w, t):
    d = t.astimezone(TPE).date()
    if d < add_days(w.as_of, -80):
        return "2.1.0"
    if d < add_days(w.as_of, -40):
        return "2.2.3"
    return "2.3.1"


def _slot(kind):
    return {"kind": kind, "item": None, "serial": None}


def _cm_lot(w, item, when, qty=1):
    state = w.__dict__.setdefault("_cm_lot_state", {})
    prefix, size, supplier = CM_LOTS[item]
    cur = state.get(item)
    if cur is None or cur["left"] < qty:
        rng = w.rng
        mfg = add_days(when.astimezone(TPE).date(), -rng.randint(10, 20))
        seq = w.nid(f"lot:{prefix}", 100)
        lot_id = f"{prefix}{yymm(mfg)}-{seq}"
        received = when - dt.timedelta(days=rng.randint(2, 5), hours=rng.randint(0, 8))
        w.lots[lot_id] = {"lot_id": lot_id, "item": item, "supplier": supplier, "site": "CM-TXG", "qty": size,
                          "mfg": mfg, "received_at": received, "iqc": "NOT_INSPECTED", "origin": "CM_FEED",
                          "po": None, "used": 0}
        cur = state[item] = {"lot": lot_id, "left": size}
    cur["left"] -= qty
    w.lots[cur["lot"]]["used"] += qty
    return cur["lot"]


def cm_production(w):
    rng, clock = w.rng, w.cm_clock
    entries = []
    w.cm_output_plan = {}
    for line, start in (("L1", w.cm_sop), ("L2", w.line2_start)):
        for day in w.cm_days:
            if day < start or day > w.as_of:
                continue
            plan = w.cm_plan(line, day)
            if not plan:
                continue
            eff = rng.uniform(0.90, 1.03)
            if line == "L1" and add_days(w.as_of, -58) <= day <= add_days(w.as_of, -56):
                eff *= 0.45            # consigned drive units arrived late (ASN slipped 3 days)
            n = max(1, int(round(plan * eff)))
            w.cm_output_plan[(line, day)] = (plan, n)
            counts = split(n, COLOR_W, rng)
            rot = clock.index[day] % 4
            order = COLOR_ORDER[rot:] + COLOR_ORDER[:rot]
            seq = [sku for sku in order for _ in range(counts[sku])]
            for k, sku in enumerate(seq):
                entries.append((line, day, k * 570.0 / n, sku, start))

    def s10_time(e):
        return clock.wall(clock.index[e[1]], e[2])

    entries.sort(key=s10_time)
    week_seq = {}
    cut_in = at(w.eco42, 0, 0, TPE).astimezone(UTC)
    w.stories["eco42_serial"] = None
    for line, day, minute, sku, start in entries:
        t0 = s10_time((line, day, minute, sku, start))
        if t0 is None or t0 > w.now:
            continue
        wk = week_code(t0.astimezone(TPE).date())
        week_seq[wk] = week_seq.get(wk, 0) + 1
        serial = f"LV1-{wk}-{week_seq[wk]:04d}"
        v = {"serial": serial, "sku": sku, "line": line, "day": day, "events": [], "built_at": None,
             "scrapped": False, "du_slots": [], "hmi_slots": [], "status": "WIP"}
        _run_cm_unit(w, v, clock.index[day], minute, clock.index[day] - clock.index[start], cut_in)
        w.vehicles.append(v)


def _run_cm_unit(w, v, day_idx, minute, t_line, cut_in):
    rng, clock = w.rng, w.cm_clock
    extra = 0.0
    for code, off in CM_FLOW:
        t = clock.wall(day_idx, minute + off + extra)
        if t is None or t > w.now:
            return
        local_day = t.astimezone(TPE).date()
        parts, lots, meas = [], [], {}
        if code == "S10":
            n = w.nid("frame")
            frame = f"FR{t.year % 100:02d}-{n:06d}"
            w.units[frame] = {"serial": frame, "item": "FRM-1", "supplier": "CYF", "origin": "CM_FEED"}
            parts.append({"kind": "FRAME", "item": "FRM-1", "serial": frame})
            v["frame"] = frame
        elif code == "S20":
            rev = "C" if t >= cut_in else "B"
            if rev == "C" and w.stories["eco42_serial"] is None:
                w.stories["eco42_serial"] = v["serial"]
            slot = _slot("DU")
            w.consume["DU-" + rev].append({"t": t, "slot": slot, "unit": v["serial"]})
            v["du_slots"].append((t, slot))
            v["du_rev"] = rev
            parts.append(slot)
        elif code == "S30":
            slot = _slot("PU")
            w.consume["PU-1"].append({"t": t, "slot": slot, "unit": v["serial"]})
            v["pu_slot"] = slot
            parts.append(slot)
        elif code == "S40":
            slot = _slot("HMI")
            w.consume["HMI-1"].append({"t": t, "slot": slot, "unit": v["serial"]})
            v["hmi_slots"].append((t, slot))
            parts.append(slot)
            lots.append({"item": "HRN-VH", "lot": _cm_lot(w, "HRN-VH", t), "qty": 1})
        elif code == "S50":
            lots += [{"item": "WHL-F", "lot": _cm_lot(w, "WHL-F", t), "qty": 1},
                     {"item": "WHL-R", "lot": _cm_lot(w, "WHL-R", t), "qty": 1},
                     {"item": "TIR-1", "lot": _cm_lot(w, "TIR-1", t, 2), "qty": 2},
                     {"item": "BRK-1", "lot": _cm_lot(w, "BRK-1", t), "qty": 1}]
        if code == "S60":
            meas = {"fw": fw_version(w, t), "motor_db": round(rng.gauss(38.0, 1.1), 1),
                    "brake_nm": round(rng.gauss(28.6, 0.7), 1), "du_sn": "@DU"}
        elif code == "S70":
            meas = {"leak_ccm": round(abs(rng.gauss(0.35, 0.12)), 2)}

        defect = None
        for dcode, p in CM_DEFECTS.get(code, []):
            p *= learning(t_line)
            if dcode == "EOL-FW" and v["line"] == "L2" and local_day == w.fw_spike_day:
                p = 0.16           # mis-set flash image on the L2 tester (NCR-0023)
            if rng.random() < p:
                defect = dcode
                break
        if defect is None:
            v["events"].append({"code": code, "t": t, "result": "PASS", "defect": None, "parts": parts,
                                "parts_out": [], "lots": lots, "meas": meas})
        else:
            fail_meas = dict(meas)
            if defect == "EOL-MOTOR":
                fail_meas["motor_db"] = round(rng.uniform(42.3, 46.0), 1)
            if defect == "EOL-BRK":
                fail_meas["brake_nm"] = round(rng.uniform(22.0, 24.8), 1)
            v["events"].append({"code": code, "t": t, "result": "FAIL", "defect": defect, "parts": parts,
                                "parts_out": [], "lots": lots, "meas": fail_meas})
            if rng.random() < 0.015:
                ts = t + dt.timedelta(minutes=20)
                if ts <= w.now:
                    v["events"].append({"code": code, "t": ts, "result": "SCRAP", "defect": defect, "parts": [],
                                        "parts_out": [], "lots": [], "meas": {}})
                    v["scrapped"] = True
                    v["status"] = "SCRAPPED"
                return
            rw = rng.uniform(25, 60)
            t_rw = clock.wall(day_idx, minute + off + extra + rw)
            if t_rw is None or t_rw > w.now:
                return                     # waiting in rework: WIP
            rw_code = "S65" if (code == "S60" and t_rw.astimezone(TPE).date() >= w.s65_start) else code
            new_parts, out_parts = [], []
            if defect == "EOL-MOTOR":
                old = v["du_slots"][-1][1]
                slot = _slot("DU")
                w.consume["DU-" + v["du_rev"]].append({"t": t_rw, "slot": slot, "unit": v["serial"], "swap": True})
                v["du_slots"].append((t_rw, slot))
                new_parts.append(slot)
                out_parts.append(old)
            elif defect == "HMI-PIXEL":
                old = v["hmi_slots"][-1][1]
                slot = _slot("HMI")
                w.consume["HMI-1"].append({"t": t_rw, "slot": slot, "unit": v["serial"], "swap": True})
                v["hmi_slots"].append((t_rw, slot))
                new_parts.append(slot)
                out_parts.append(old)
            v["events"].append({"code": rw_code, "t": t_rw, "result": "REWORK", "defect": defect, "parts": new_parts,
                                "parts_out": out_parts, "lots": [], "meas": {}})
            extra += rw + 8
            t_rt = clock.wall(day_idx, minute + off + extra)
            if t_rt is None or t_rt > w.now:
                return
            v["events"].append({"code": code, "t": t_rt, "result": "PASS", "defect": None, "parts": [],
                                "parts_out": [], "lots": [], "meas": meas})
            t = t_rt
        if code == "S80":
            v["built_at"] = v["events"][-1]["t"]
            v["status"] = "BUILT"


def pack_production(w):
    rng, clock = w.rng, w.pack_clock
    entries = []
    w.pack_output_plan = {}
    gasket_short = add_days(w.as_of, -72)
    enc_reject = {add_days(w.as_of, -33), add_days(w.as_of, -32)}
    for day in w.us_days:
        if day < w.pack_sop or day > w.as_of:
            continue
        plan = w.pack_plan(day)
        if not plan:
            continue
        eff = rng.uniform(0.90, 1.02)
        if day == gasket_short:
            eff *= 0.5
        if day in enc_reject:
            eff *= 0.7
        n = max(1, int(round(plan * eff)))
        w.pack_output_plan[day] = (plan, n)
        counts = split(n, {"PK-STD": 0.54, "PK-LRG": 0.46}, rng)
        seq = ["PK-STD"] * counts["PK-STD"] + ["PK-LRG"] * counts["PK-LRG"]
        if clock.index[day] % 2:
            seq.reverse()
        for k, sku in enumerate(seq):
            entries.append((day, k * 630.0 / n, sku))
    entries.sort(key=lambda e: clock.wall(clock.index[e[0]], e[1]))
    week_seq = {}
    t_sop = clock.index[w.pack_sop]
    dev_used = 0
    for day, minute, sku in entries:
        t0 = clock.wall(clock.index[day], minute)
        if t0 > w.now:
            continue
        wk = week_code(t0.astimezone(PT).date())
        week_seq[wk] = week_seq.get(wk, 0) + 1
        size = "S" if sku == "PK-STD" else "L"
        p = {"serial": f"PK{size}-{wk}-{week_seq[wk]:04d}", "sku": sku, "day": day, "events": [],
             "built_at": None, "scrapped": False, "bms_slots": [], "status": "WIP", "lot_uses": []}
        d0 = t0.astimezone(PT).date()
        if d0 < w.eco31:
            rev = "A"
        elif w.dev12_start <= d0 and dev_used < 188 and rng.random() < 0.30:
            rev, dev_used = "A", dev_used + 1
            p["deviation"] = "DEV-0012"
        else:
            rev = "B"
        p["bms_rev"] = rev
        _run_pack_unit(w, p, clock.index[day], minute, clock.index[day] - t_sop)
        w.packs.append(p)
    w.stories["dev12_used"] = dev_used


def _lot_use(w, p, family, t, qty, station):
    ev = {"t": t, "qty": qty, "unit": p["serial"], "family": family, "station": station, "alloc": []}
    w.lot_use[family].append(ev)
    p["lot_uses"].append(ev)
    return ev


def _run_pack_unit(w, p, day_idx, minute, t_line):
    rng, clock = w.rng, w.pack_clock
    extra = 0.0
    cells = 40 if p["sku"] == "PK-STD" else 60
    for code, off in PACK_FLOW:
        t = clock.wall(day_idx, minute + off + extra)
        if t is None or t > w.now:
            return
        local_day = t.astimezone(PT).date()
        parts, uses, meas = [], [], {}
        if code == "P10":
            rejects = 1 if rng.random() < 0.25 else 0
            uses.append(_lot_use(w, p, "CEL-21700", t, cells + rejects, code))
            p["cell_rejects"] = rejects
        elif code == "P20":
            uses.append(_lot_use(w, p, "BUS-S" if p["sku"] == "PK-STD" else "BUS-L", t, 1, code))
        elif code == "P30":
            slot = _slot("BMS")
            w.consume["BMS-" + p["bms_rev"]].append({"t": t, "slot": slot, "unit": p["serial"]})
            p["bms_slots"].append((t, slot))
            parts.append(slot)
            uses.append(_lot_use(w, p, "HRN-PK", t, 1, code))
        elif code == "P40":
            uses.append(_lot_use(w, p, "ENC-STD" if p["sku"] == "PK-STD" else "ENC-LRG", t, 1, code))
            uses.append(_lot_use(w, p, "GSK", t, 1, code))
        elif code == "P50":
            nominal = 5.0 * (8 if p["sku"] == "PK-STD" else 12)
            meas = {"capacity_ah": round(rng.gauss(nominal * 1.01, nominal * 0.006), 2),
                    "ir_mohm": round(rng.gauss(58 if p["sku"] == "PK-STD" else 39, 1.6), 1),
                    "hipot": "PASS"}
        defect = None
        for dcode, prob in PACK_DEFECTS.get(code, []):
            prob *= learning(t_line)
            if dcode == "SEAL-LEAK" and add_days(w.as_of, -72) <= local_day <= add_days(w.as_of, -48):
                prob = 0.035       # EPDM gasket on cold mornings (NCR-0019 -> ECO-0036)
            if rng.random() < prob:
                defect = dcode
                break
        ev = {"code": code, "t": t, "result": "PASS" if defect is None else "FAIL", "defect": defect,
              "parts": parts, "parts_out": [], "uses": uses, "meas": meas}
        p["events"].append(ev)
        if defect is None:
            if code == "P60":
                p["built_at"] = t
                p["status"] = "BUILT"
            continue
        if rng.random() < 0.02 or (defect == "EOL-CAP" and rng.random() < 0.5):
            ts = t + dt.timedelta(minutes=15)
            if ts <= w.now:
                p["events"].append({"code": code, "t": ts, "result": "SCRAP", "defect": defect, "parts": [],
                                    "parts_out": [], "uses": [], "meas": {}})
                p["scrapped"] = True
                p["status"] = "SCRAPPED"
            return
        rw = rng.uniform(20, 45)
        t_rw = clock.wall(day_idx, minute + off + extra + rw)
        if t_rw is None or t_rw > w.now:
            return
        new_parts, out_parts, rw_uses = [], [], []
        if defect == "BMS-COMM":
            old = p["bms_slots"][-1][1]
            slot = _slot("BMS")
            w.consume["BMS-" + p["bms_rev"]].append({"t": t_rw, "slot": slot, "unit": p["serial"], "swap": True})
            p["bms_slots"].append((t_rw, slot))
            new_parts.append(slot)
            out_parts.append(old)
        elif defect == "SEAL-LEAK":
            rw_uses.append(_lot_use(w, p, "GSK", t_rw, 1, code))     # a new gasket; the old one is scrapped
        p["events"].append({"code": code, "t": t_rw, "result": "REWORK", "defect": defect, "parts": new_parts,
                            "parts_out": out_parts, "uses": rw_uses, "meas": {}})
        extra += rw + 6
        t_rt = clock.wall(day_idx, minute + off + extra)
        if t_rt is None or t_rt > w.now:
            return
        p["events"].append({"code": code, "t": t_rt, "result": "PASS", "defect": None, "parts": [],
                            "parts_out": [], "uses": [], "meas": meas})
        if code == "P60":
            p["built_at"] = t_rt
            p["status"] = "BUILT"
