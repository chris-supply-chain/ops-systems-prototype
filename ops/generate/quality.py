"""Quality: field failures (as the CRM sees them), IQC results, deviations, NCRs."""
import datetime as dt
import math

from .util import PT, TPE, UTC, add_days, at

# code, failed part, hazard/day, labor h, logistics usd, CRM category, symptom phrasings, resolution
MODES = [
    ("FLD-PIXEL", "HMI", 0.00011, 1.2, 65.0, "Display",
     ["Dead pixels in the upper left of the display", "Display has a vertical line through it",
      "Screen shows black spots near the speed readout", "Dash display flickers and has a stuck line"],
     "Replaced HMI display"),
    ("FLD-SQUEAL", "BRK", 0.00008, 1.0, 65.0, "Brakes",
     ["Rear brake squeals loudly when stopping", "Grinding noise from the front brake",
      "Brakes squeak at low speed", "Rotor rubbing sound on every wheel turn"],
     "Bedded in pads, trued rotor, re-torqued caliper"),
    ("FLD-NOISE", "DU", 0.00005, 2.0, 140.0, "Drive",
     ["Whining noise from the rear hub under load", "Clicking from the motor when accelerating",
      "Loud hum from the drive when climbing hills", ("Clicking noise when braking hard downhill", 0.6)],
     "Replaced drive unit"),
    ("FLD-FW", None, 0.00010, 0.5, 0.0, "Software",
     ["Bike won't connect to the app", "Display froze and needed a reboot to ride",
      "Ride modes missing after the latest update", "Bluetooth pairing fails with my phone"],
     "Reflashed firmware over the air"),
    ("FLD-CHG", "BMS", 0.00004, 0.5, 140.0, "Battery",
     ["Battery won't charge, charger light stays green", "Pack not charging at all",
      "Battery shows error E07 when plugged in", "Bike won't turn on, battery dead even after charging overnight"],
     "Replaced battery pack"),
    ("FLD-CAPFADE", "PACK", 0.00008, 0.5, 140.0, "Battery",
     ["Battery drops from 80% to 20% within a few miles", "Range dropped by half in a month",
      "Battery won't hold a charge", "Battery percentage falls quickly then the bike shuts off",
      ("Battery dies fast and won't charge past 60%", 0.6)],
     "Replaced battery pack"),
]
def _rotation(symptoms):
    """Symptom phrasings in a smooth weighted rotation: over any run of claims each phrasing appears in proportion to
    its weight (ambiguous ones are rarer, weight < 1). A random draw would let the ambiguous share, and with it the
    classifier eval, swing with the sample; this keeps it steady whatever the dataset date."""
    opts = [(x, 1.0) if isinstance(x, str) else x for x in symptoms]
    total = sum(wt for _, wt in opts)
    credit = [0.0] * len(opts)
    while True:
        for i, (_, wt) in enumerate(opts):
            credit[i] += wt
        k = max(range(len(opts)), key=lambda i: credit[i])
        credit[k] -= total
        yield opts[k][0]


PART_COST = {"HMI": 70.40, "BRK": 50.60, "DU": 207.24, "PACK_STD": 235.40, "PACK_LRG": 315.70, None: 0.0}


def _pack_bad_share(w, pack):
    bad = set(w.stories.get("bad_cell_lots") or [w.stories["bad_cell_lot"]])
    total = bad_q = 0
    for ev in pack.get("lot_uses", []):
        if ev["family"] != "CEL-21700":
            continue
        for lot_id, q, _ in ev["alloc"]:
            total += q
            if lot_id in bad:
                bad_q += q
    return bad_q / total if total else 0.0


def warranty_cases(w):
    rng = w.rng
    packs = {p["serial"]: p for p in w.packs}
    vehicles = {v["serial"]: v for v in w.vehicles}
    service_pool = {sku: list(q) for sku, q in w.stock_3pl.items() if sku.startswith("PK-")}
    cases = []
    for o in w.orders:
        if o.get("status") != "DELIVERED":
            continue
        days = (w.now - o["delivered_at"]).total_seconds() / 86400.0
        if days <= 0.5:
            continue
        pack = packs[o["pack_serial"]]
        share = _pack_bad_share(w, pack)
        for code, part, hazard, labor, logi, cat, symptoms, resolution in MODES:
            h = hazard
            start = 0.0
            if code == "FLD-CAPFADE" and share > 0:
                h, start = 0.0013 * (1.0 if share >= 0.5 else 0.5), 6.0
            t_fail = start + (-math.log(1.0 - rng.random()) / h)
            if t_fail >= days:
                continue
            reported = o["delivered_at"] + dt.timedelta(days=t_fail, hours=rng.randint(0, 8))
            if reported > w.now:
                continue
            case = {"case_no": f"CS-{200000 + w.nid('case')}", "reported_at": reported, "order": o,
                    "vehicle": o["vehicle_serial"], "code": code, "category": cat, "symptom": None,
                    "resolution": resolution, "labor_h": labor, "logistics": logi, "removed": None,
                    "installed": None, "part_item": None}
            v = vehicles[o["vehicle_serial"]]
            if part == "HMI":
                case["removed"] = v["hmi_slots"][-1][1]["serial"]
                case["part_item"] = "HMI-1"
                case["installed"] = f"SVC-HM-{w.nid('svc'):05d}"
            elif part == "DU":
                case["removed"] = v["du_slots"][-1][1]["serial"]
                case["part_item"] = "DU-" + v.get("du_rev", "C")
                case["installed"] = f"SVC-DU-{w.nid('svc'):05d}"
            elif part == "BRK":
                case["part_item"] = "BRK-1"
            elif part in ("BMS", "PACK"):
                case["removed"] = o["pack_serial"]
                case["part_item"] = o["pack_sku"]
                pool = service_pool.get(o["pack_sku"], [])
                pick = next((s for s in pool if w.receipt_time.get(s) and w.receipt_time[s] < reported), None)
                if pick:
                    pool.remove(pick)
                    w.stock_3pl[o["pack_sku"]].remove(pick)
                    case["installed"] = pick
            age = (w.now - reported).days
            case["status"] = ("REJECTED" if rng.random() < 0.03 else
                              "OPEN" if age < 2 else "DIAGNOSED" if age < 6 else "REPAIRED" if age < 15 else "CLOSED")
            key = "PACK_STD" if o["pack_sku"] == "PK-STD" else "PACK_LRG"
            case["parts_usd"] = PART_COST[key] if part in ("BMS", "PACK") else PART_COST.get(part, 0.0)
            cases.append(case)
            break      # at most one claim per vehicle in this window
    cases.sort(key=lambda c: c["reported_at"])
    phrasing = {m[0]: _rotation(m[6]) for m in MODES}
    for c in cases:
        c["symptom"] = next(phrasing[c["code"]])
    w.claims = cases


IQC_SAMPLE = [(500, 32), (3200, 50), (10000, 80), (35000, 125), (10 ** 9, 200)]


def records(w):
    rng = w.rng
    iqc = []
    for lot in sorted(w.lots.values(), key=lambda l: l["received_at"]):
        if lot["origin"] != "OEM_RECEIPT":
            continue
        n = next(s for lim, s in IQC_SAMPLE if lot["qty"] <= lim)
        rec = {"qe_id": f"IQC-{w.nid('iqc'):05d}", "kind": "IQC", "site": "OEM-FRE", "item": lot["item"],
               "lot": lot["lot_id"], "supplier": lot["supplier"], "n": n, "bad": 0, "defect": None,
               "result": "ACCEPT", "disposition": "ACCEPT", "status": "CLOSED",
               "detected_at": lot["iqc_done"], "closed_at": lot["iqc_done"], "root_cause": None, "ncr": None,
               "cost": 0.0}
        if lot["iqc"] == "PENDING":
            rec.update(status="OPEN", detected_at=lot["received_at"], closed_at=None, result="CONDITIONAL",
                       disposition=None)
        if lot.get("iqc_defect") == "IQC-FLAT":
            rec.update(bad=18, defect="IQC-FLAT", result="REJECT", disposition="RTV", ncr="NCR-0031",
                       root_cause="Die wear on sealing face; supplier skipped in-process flatness check on shift 3",
                       cost=640.0)
        if lot.get("iqc_defect") == "IQC-OCV":
            rec.update(bad=3, defect="IQC-OCV", result="CONDITIONAL", disposition="SORT",
                       root_cause="Formation-aging window shortened at supplier; 60 outliers removed in 100% sort",
                       cost=1320.0)
        iqc.append(rec)
    w.iqc = iqc

    tire_lot = None
    for lot in w.lots.values():
        if lot["item"] == "TIR-1" and lot["received_at"].astimezone(TPE).date() <= add_days(w.as_of, -40):
            if tire_lot is None or lot["received_at"] > tire_lot["received_at"]:
                tire_lot = lot
    if tire_lot:
        tire_lot["iqc"] = "ACCEPTED_UNDER_DEVIATION"
        w.stories["dev15_lot"] = tire_lot["lot_id"]
    a = w.as_of
    w.deviations = [
        ("DEV-0009", "Widen S50 caliper torque window to 7-9 Nm during tool recalibration", "BRK-1", "TYB",
         "CM-TXG", "Torque tool drift at S50; recalibration parts on order", 500, 500, add_days(a, -95),
         add_days(a, -80), "CLOSED", "MEDIUM", "FAP quality", "OEM SQE", None),
        ("DEV-0012", "Use BMS rev A (pre ECO-0031) in lieu of rev B while rev B is constrained", "BMS-A", "PNC",
         "OEM-FRE", "Rev B deliveries short on AFE IC allocation; rev A safe with firmware reverse-polarity lockout",
         300, w.stories.get("dev12_used", 0), w.dev12_start, w.dev12_end, "APPROVED", "LOW", "OEM planning",
         "OEM battery eng.", "ECO-0031"),
        ("DEV-0015", "Accept tire lot with sidewall print offset (cosmetic)", "TIR-1", "NTR", "CM-TXG",
         "Print offset 2 mm; no functional impact; customer-visible only on close inspection", 900,
         (tire_lot or {}).get("used", 0), add_days(a, -42), add_days(a, -20), "CLOSED", "LOW", "FAP quality",
         "OEM SQE", None),
        ("DEV-0017", "Use up EPDM gaskets after ECO-0036 cut-in (warm-weather builds only)", "GSK-A", "BAY",
         "OEM-FRE", "Avoid scrapping 1,600 good EPDM gaskets; leak test at P40 remains 100%", 1600,
         w.stories.get("dev17_used", 0), w.eco36, add_days(w.eco36, 30),
         "CLOSED" if add_days(w.eco36, 30) < a else "APPROVED", "MEDIUM", "OEM manufacturing", "OEM battery eng.",
         "ECO-0036"),
    ]
    spike = w.fw_spike_day
    w.ncrs = [
        {"qe_id": "NCR-0019", "site": "OEM-FRE", "item": "GSK-A", "supplier": "BAY", "defect": "SEAL-LEAK",
         "n": 1450, "bad": 43, "result": "REJECT", "disposition": "REWORK", "status": "CLOSED",
         "detected_at": at(add_days(a, -66), 9, 30, PT), "closed_at": at(w.eco36, 12, 0, PT),
         "root_cause": "EPDM compression set below 10°C on early shifts; fixed by ECO-0036 (silicone)",
         "cost": 2150.0},
        {"qe_id": "NCR-0023", "site": "CM-TXG", "item": "DU-B", "supplier": None, "defect": "EOL-FW",
         "n": 30, "bad": 5, "result": "REJECT", "disposition": "REWORK", "status": "CLOSED",
         "detected_at": at(spike, 15, 0, TPE), "closed_at": at(add_days(spike, 1), 10, 0, TPE),
         "root_cause": "L2 EOL tester re-imaged with the wrong flash package; restored golden image and locked it",
         "cost": 380.0},
        {"qe_id": "NCR-0027", "site": "CM-TXG", "item": "DU-C", "supplier": None, "defect": "DU-CONN",
         "n": 900, "bad": 7, "result": "CONDITIONAL", "disposition": "REWORK", "status": "CONTAINED",
         "detected_at": at(add_days(a, -8), 11, 0, TPE), "closed_at": None,
         "root_cause": "Connector latch not fully seated; poka-yoke click sensor on order (CM action)", "cost": 0.0},
        {"qe_id": "NCR-0031", "site": "OEM-FRE", "item": "ENC-STD", "supplier": "SMT", "defect": "IQC-FLAT",
         "lot": w.stories.get("enc_reject_lot"), "n": 32, "bad": 18, "result": "REJECT", "disposition": "RTV",
         "status": "CLOSED", "detected_at": at(add_days(a, -34), 15, 0, PT), "closed_at": at(add_days(a, -31), 9, 0, PT),
         "root_cause": "Die wear on sealing face; supplier skipped in-process flatness check on shift 3",
         "cost": 4940.0},
    ]
