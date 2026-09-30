"""Supply: what suppliers physically delivered, sized to what the lines consumed.

Serialized modules (drive units, pedal units, HMIs to the CM; BMS boards to
Fremont) arrive on ASNs and are consumed first-in-first-out. Lot-controlled
components arrive at Fremont as lots, pass incoming inspection, and are
consumed FIFO. That consumption becomes the lot side of the pack genealogy.
"""
import datetime as dt

from .util import PT, TPE, UTC, add_days, at, iso, yymm

LOT_PLAN = {
    # item: (lot prefix, supplier, weekday, interval days, cover days, max lot qty, mfg lag days, iqc hours)
    "CEL-21700": ("CL", "KES", 1, 7, 14, 20000, (22, 35), (20, 30)),
    "ENC-STD": ("ES", "SMT", 0, 7, 10, None, (8, 14), (4, 12)),
    "ENC-LRG": ("EL", "SMT", 0, 7, 10, None, (8, 14), (4, 12)),
    "BUS-S": ("BS", "CSP", 3, 7, 10, None, (5, 9), (3, 8)),
    "BUS-L": ("BL", "CSP", 3, 7, 10, None, (5, 9), (3, 8)),
    "HRN-PK": ("HP", "NRT", 2, 14, 18, None, (7, 12), (3, 8)),
    "GSK-A": ("GA", "BAY", 4, 14, 18, None, (4, 8), (2, 6)),
    "GSK-B": ("GB", "BAY", 4, 14, 18, None, (4, 8), (2, 6)),
}
ITEM_POLICY = {  # safety stock, order multiple, moq (mirrors master.ITEMS)
    "CEL-21700": (15000, 5000, 20000), "ENC-STD": (200, 200, 400), "ENC-LRG": (160, 200, 400),
    "BUS-S": (250, 250, 500), "BUS-L": (200, 250, 500), "HRN-PK": (300, 300, 600),
    "GSK-A": (0, 500, 1000), "GSK-B": (400, 500, 1000),
}


def _round_up(q, mult, moq):
    if q <= 0:
        return 0
    q = max(q, moq)
    return int(-(-q // mult) * mult)


# ---------------------------------------------------------------- serialized modules

def _schedule(w, events, site_tz, weekday, hour, first, last, cover=9, buffer=150, round_to=50,
              extra_last=0, shares=None):
    """Weekly deliveries sized to cover the next `cover` days of consumption plus a buffer."""
    events = sorted(events, key=lambda e: e["t"])
    times = [e["t"] for e in events]
    out = []
    d = first - dt.timedelta(days=(first.weekday() - weekday) % 7)
    delivered = 0
    idx = 0
    while d <= last:
        arrival = at(d, hour, 0, site_tz).astimezone(UTC)
        window_end = arrival + dt.timedelta(days=cover)
        while idx < len(times) and times[idx] <= arrival:
            idx += 1
        consumed = idx
        upcoming = sum(1 for t in times[idx:] if t <= window_end)
        qty = upcoming + buffer - (delivered - consumed)
        qty = int(-(-qty // round_to) * round_to) if qty > 0 else 0
        if qty:
            out.append({"arrival": arrival, "qty": qty})
            delivered += qty
        d += dt.timedelta(days=7)
    if out and extra_last:
        out[-1]["qty"] += extra_last
    return out


def _assign(w, events, deliveries, make_serial):
    """Create serials per delivery and hand them out FIFO to consumption events."""
    pool = []
    for dlv in sorted(deliveries, key=lambda d: d["arrival"]):
        dlv["serials"] = [make_serial(dlv) for _ in range(dlv["qty"])]
        pool.extend((dlv["arrival"], s) for s in dlv["serials"])
    pool.sort(key=lambda x: x[0])
    early = 0
    i = 0
    for ev in sorted(events, key=lambda e: e["t"]):
        if i >= len(pool):
            raise RuntimeError("supply schedule ran out of serials")
        arrival, serial = pool[i]
        if arrival > ev["t"]:
            early += 1
        ev["slot"]["serial"] = serial
        i += 1
    return early


def serialized_supply(w):
    rng = w.rng
    early_total = 0

    def du_maker(rev):
        def make(dlv):
            n = w.nid("du")
            yy = dlv["arrival"].year % 100
            serial = f"DU{rev}{yy}-{n:06d}"
            motor = f"MT{yy}-{n:06d}"
            ctl = f"CT{rev}{yy}-{n:06d}"
            mag = _magnet_lot(w, dlv["arrival"])
            w.units[serial] = {"serial": serial, "item": f"DU-{rev}", "supplier": "TNM", "site": "CM-TXG",
                               "arrival": dlv["arrival"], "asn": dlv, "motor": motor, "ctl": ctl, "magnet_lot": mag}
            w.units[motor] = {"serial": motor, "item": "MTR-1", "supplier": "TNM", "parent": serial}
            w.units[ctl] = {"serial": ctl, "item": f"CTL-{rev}", "supplier": "TNM", "parent": serial}
            return serial
        return make

    def simple_maker(prefix, item, supplier, site):
        def make(dlv):
            n = w.nid(prefix)
            serial = f"{prefix}{dlv['arrival'].year % 100:02d}-{n:06d}"
            w.units[serial] = {"serial": serial, "item": item, "supplier": supplier, "site": site,
                               "arrival": dlv["arrival"], "asn": dlv}
            return serial
        return make

    # Drive units: rev B until the ECO-0042 cut-in (+140 residual stranded at the CM), then rev C
    cut_in = at(w.eco42, 0, 0, TPE).astimezone(UTC)
    ev_b, ev_c = w.consume["DU-B"], w.consume["DU-C"]
    first = min(e["t"] for e in ev_b).astimezone(TPE).date() - dt.timedelta(days=10)
    sched_b = _schedule(w, ev_b, TPE, 2, 10, first, add_days(w.eco42, -1), extra_last=140)
    sched_c = _schedule(w, ev_c, TPE, 2, 10, add_days(w.eco42, -8), w.as_of) if ev_c else []
    late_dlv = None
    for dlv in sched_b + sched_c:
        d = dlv["arrival"].astimezone(TPE).date()
        if add_days(w.as_of, -62) <= d <= add_days(w.as_of, -59):
            dlv["arrival"] += dt.timedelta(days=3)          # the late shipment behind the L1 slowdown
            dlv["note"] = "shipped 3 days late"
    # a regular delivery in the last week whose ASN the supplier transmitted two days after the parts landed
    if not any(add_days(w.as_of, -4) <= d["arrival"].astimezone(TPE).date() <= add_days(w.as_of, -2) for d in sched_c):
        sched_c.append({"arrival": at(add_days(w.as_of, -3), 10, 0, TPE).astimezone(UTC), "qty": 150})
    # emergency hand-carry of 30 rev C with no ASN, and one ASN transmitted days after the parts landed
    emergency = {"arrival": at(add_days(w.as_of, -6), 10, 0, TPE).astimezone(UTC), "qty": 30, "no_asn": True}
    sched_c.append(emergency)
    for dlv in sched_c:
        d = dlv["arrival"].astimezone(TPE).date()
        if not dlv.get("no_asn") and add_days(w.as_of, -4) <= d <= add_days(w.as_of, -2):
            late_dlv = dlv
    if late_dlv:
        late_dlv["asn_received_at"] = at(add_days(w.as_of, -1), 17, 0, TPE).astimezone(UTC)
        w.stories["late_asn"] = True
    for rev, sched, events in (("B", sched_b, ev_b), ("C", sched_c, ev_c)):
        for dlv in sched:
            dlv.update(item=f"DU-{rev}", supplier="TNM", site="CM-TXG")
        early_total += _assign(w, events, sched, du_maker(rev))
        w.deliveries[f"DU-{rev}"] = sched
    if late_dlv:
        _prefer(ev_c, emergency, emergency["arrival"], late_dlv["arrival"], 18)
        _prefer(ev_c, late_dlv, late_dlv["arrival"], late_dlv["asn_received_at"], 20)
    else:
        _prefer(ev_c, emergency, emergency["arrival"], w.now, 18)

    for item, prefix, supplier in (("PU-1", "PU", "TNM"), ("HMI-1", "HM", "HDS")):
        events = w.consume[item]
        first = min(e["t"] for e in events).astimezone(TPE).date() - dt.timedelta(days=10)
        sched = _schedule(w, events, TPE, 2, 10, first, w.as_of, buffer=180)
        for dlv in sched:
            dlv.update(item=item, supplier=supplier, site="CM-TXG")
        early_total += _assign(w, events, sched, simple_maker(prefix, item, supplier, "CM-TXG"))
        w.deliveries[item] = sched

    # BMS boards to Fremont: rev A until ECO-0031 with 300 extra (later used under DEV-0012), then rev B.
    # Pinecrest shipped short in the last three weeks (AFE allocation) - the root of the coming shortage.
    ev_a, ev_bb = w.consume["BMS-A"], w.consume["BMS-B"]
    first = min(e["t"] for e in ev_a).astimezone(PT).date() - dt.timedelta(days=10)
    pre_dev_a = [e for e in ev_a if e["t"].astimezone(PT).date() < w.eco31]
    sched_a = _schedule(w, pre_dev_a, "America/Los_Angeles", 1, 10, first, add_days(w.eco31, -1),
                        buffer=120, extra_last=300)
    sched_bb = _schedule(w, ev_bb, "America/Los_Angeles", 1, 10, add_days(w.eco31, -7), w.as_of, buffer=260)
    for dlv in sched_bb:
        if dlv["arrival"].astimezone(PT).date() >= add_days(w.as_of, -21):
            dlv["qty"] = max(50, int(dlv["qty"] * 0.72 // 50 * 50))
            dlv["note"] = "partial: AFE IC allocation"
    for rev, sched, events in (("A", sched_a, ev_a), ("B", sched_bb, ev_bb)):
        for dlv in sched:
            dlv.update(item=f"BMS-{rev}", supplier="PNC", site="OEM-FRE")
        early_total += _assign(w, events, sched, simple_maker(f"BM{rev}", f"BMS-{rev}", "PNC", "OEM-FRE"))
        w.deliveries[f"BMS-{rev}"] = sched

    # ASN numbers + transmission times
    for item, sched in w.deliveries.items():
        for dlv in sched:
            if dlv.get("no_asn"):
                continue
            dlv["asn_no"] = f"ASN-{dlv['supplier']}-{dlv['arrival'].year % 100:02d}{w.nid('asn:' + dlv['supplier']):04d}"
            dlv.setdefault("asn_received_at", dlv["arrival"] - dt.timedelta(hours=rng.randint(20, 60)))
    w.stories["serial_supply_early_picks"] = early_total


def _prefer(events, dlv, start, end, n):
    """The line used this batch first (it was the one on the rack): hand its serials to the latest
    installs in the window, swapping with whatever FIFO had assigned."""
    cand = [e for e in events if start <= e["t"] <= end and not e.get("swap")][-n:]
    holder = {e["slot"]["serial"]: e for e in events}
    for e, s in zip(cand, dlv["serials"]):
        other = holder.get(s)
        if other is e:
            continue
        old = e["slot"]["serial"]
        e["slot"]["serial"] = s
        holder[s] = e
        if other is not None:
            other["slot"]["serial"] = old
            holder[old] = other


def _magnet_lot(w, when):
    state = w.__dict__.setdefault("_mag", {"lot": None, "left": 0})
    if state["left"] <= 0:
        mfg = add_days(when.astimezone(TPE).date(), -w.rng.randint(35, 50))
        lot_id = f"MG{yymm(mfg)}-{w.nid('lot:MG', 100)}"
        w.lots[lot_id] = {"lot_id": lot_id, "item": "MAG-NDFEB", "supplier": "KSM", "site": "SUP-TNM",
                          "qty": round(700 * 0.42, 1), "mfg": mfg,
                          "received_at": when - dt.timedelta(days=w.rng.randint(18, 26)),
                          "iqc": "NOT_INSPECTED", "origin": "SUPPLIER_ASN", "po": None, "used": 0}
        state.update(lot=lot_id, left=700)
    state["left"] -= 1
    return state["lot"]


# ---------------------------------------------------------------- lot-controlled components at Fremont

def lot_supply(w):
    rng = w.rng
    # gasket family -> which item each use draws from is decided during FIFO allocation below
    fam = {"GSK": ["GSK-A", "GSK-B"]}
    uses_by_item = {k: list(v) for k, v in w.lot_use.items() if k != "GSK"}
    gsk = sorted(w.lot_use["GSK"], key=lambda e: e["t"])
    eco36 = at(w.eco36, 0, 0, "America/Los_Angeles").astimezone(UTC)
    uses_by_item["GSK-A"] = [e for e in gsk if e["t"] < eco36]
    uses_by_item["GSK-B"] = [e for e in gsk if e["t"] >= eco36]

    for item, (prefix, supplier, weekday, interval, cover, max_lot, mfg_lag, iqc_h) in LOT_PLAN.items():
        events = sorted(uses_by_item.get(item, []), key=lambda e: e["t"])
        if item == "GSK-B":
            first = add_days(w.eco36, -10)
        elif events:
            first = events[0]["t"].astimezone(PT).date() - dt.timedelta(days=12)
        else:
            continue
        last = add_days(w.eco36, -1) if item == "GSK-A" else w.as_of
        ss, mult, moq = ITEM_POLICY[item]
        receipts = []
        d = first - dt.timedelta(days=(first.weekday() - weekday) % 7)
        delivered = consumed_total = 0.0
        times = [(e["t"], e["qty"]) for e in events]
        k = 0
        n_receipt = 0
        while d <= last:
            arrival = at(d, 10, 0, "America/Los_Angeles").astimezone(UTC)
            while k < len(times) and times[k][0] <= arrival:
                consumed_total += times[k][1]
                k += 1
            window_end = arrival + dt.timedelta(days=interval + cover)
            upcoming = sum(q for t, q in times[k:] if t <= window_end)
            if item == "GSK-B" and not events:
                upcoming = 0
            need = upcoming + ss - (delivered - consumed_total)
            qty = _round_up(need, mult, moq)
            n_receipt += 1
            if qty:
                receipts.append({"arrival": arrival, "qty": qty})
                delivered += qty
            d += dt.timedelta(days=interval)
        if item == "GSK-A" and receipts:
            receipts[-1]["qty"] += 900          # overbuy used up after the ECO under DEV-0017
        # storylines
        if item == "ENC-STD":
            target = add_days(w.as_of, -34)
            rej = min(receipts, key=lambda r: abs((r["arrival"].astimezone(PT).date() - target).days))
            rej["reject"] = True
            receipts.append({"arrival": at(add_days(w.as_of, -31), 9, 0, "America/Los_Angeles").astimezone(UTC),
                             "qty": rej["qty"], "expedited": True})
        if item == "CEL-21700":
            target = add_days(w.as_of, -90)
            cond = min(receipts, key=lambda r: abs((r["arrival"].astimezone(PT).date() - target).days))
            cond["conditional"] = True
        receipts.sort(key=lambda r: r["arrival"])

        lots = []
        for r in receipts:
            chunks = []
            q = r["qty"]
            while q > 0:
                c = min(q, max_lot) if max_lot else q
                chunks.append(c)
                q -= c
            for c in chunks:
                mfg = add_days(r["arrival"].astimezone(PT).date(), -rng.randint(*mfg_lag))
                lot_id = f"{prefix}{yymm(mfg)}-{w.nid('lot:' + prefix, 100)}"
                iqc_done = r["arrival"] + dt.timedelta(hours=rng.uniform(*iqc_h))
                lot = {"lot_id": lot_id, "item": item, "supplier": supplier, "site": "OEM-FRE", "qty": c,
                       "mfg": mfg, "received_at": r["arrival"], "iqc_done": iqc_done, "iqc": "ACCEPTED",
                       "origin": "OEM_RECEIPT", "po": None, "used": 0, "usable": c,
                       "expedited": r.get("expedited", False)}
                if r.get("reject"):
                    lot.update(iqc="REJECTED", usable=0, iqc_defect="IQC-FLAT")
                    w.stories["enc_reject_lot"] = lot_id
                if r.get("conditional"):
                    lot.update(usable=c - 60, iqc_defect="IQC-OCV", sorted=True)
                    w.stories["cell_sort_lot"] = lot_id
                if iqc_done > w.now:
                    lot["iqc"] = "PENDING"
                w.lots[lot_id] = lot
                lots.append(lot)
        w.deliveries[item] = [{"item": item, "supplier": supplier, "site": "OEM-FRE", "arrival": r["arrival"],
                               "qty": r["qty"], "lots": [l["lot_id"] for l in lots if l["received_at"] == r["arrival"]],
                               "expedited": r.get("expedited", False)} for r in receipts]

    # FIFO allocation of every use to accepted lots that were through IQC at the time
    by_item = {}
    for lot in w.lots.values():
        if lot["origin"] == "OEM_RECEIPT":
            by_item.setdefault(lot["item"], []).append(lot)
    for lots in by_item.values():
        lots.sort(key=lambda l: (l["iqc_done"], l["lot_id"]))
    early = 0

    def take(item, ev):
        nonlocal early
        need = ev["qty"]
        for lot in by_item.get(item, []):
            if need <= 0:
                break
            if lot["iqc"] == "REJECTED":
                continue
            left = lot["usable"] - lot["used"]
            if left <= 0:
                continue
            if lot["iqc_done"] > ev["t"]:
                early += 1
            q = min(left, need)
            lot["used"] += q
            ev["alloc"].append((lot["lot_id"], q, item))
            need -= q
        return need

    dev17 = 0
    for family, events in w.lot_use.items():
        for ev in sorted(events, key=lambda e: e["t"]):
            if family == "GSK":
                if ev["t"] < eco36:
                    rest = take("GSK-A", ev)
                else:
                    before = len(ev["alloc"])
                    rest = take("GSK-A", ev)            # use-up of EPDM stock after the ECO (DEV-0017)
                    if len(ev["alloc"]) > before:
                        dev17 += 1
                    if rest > 0:
                        ev["qty"], saved = rest, ev["qty"]
                        rest = take("GSK-B", ev)
                        ev["qty"] = saved
            else:
                rest = take(family, ev)
            if rest > 0:
                raise RuntimeError(f"lot supply short for {family} at {ev['t']}")
    w.stories["lot_supply_early_picks"] = early
    w.stories["dev17_used"] = dev17

    # the cell lot whose packs will start failing in the field
    first_use = {}
    for ev in w.lot_use["CEL-21700"]:
        for lot_id, q, _ in ev["alloc"]:
            first_use[lot_id] = min(first_use.get(lot_id, ev["t"]), ev["t"])
    cell_lots = [l for l in by_item["CEL-21700"] if l["lot_id"] in first_use and not l.get("sorted")]
    target = at(add_days(w.as_of, -55), 10, 0, "America/Los_Angeles").astimezone(UTC)
    bad = min(cell_lots, key=lambda l: abs((first_use[l["lot_id"]] - target).total_seconds()))
    w.stories["bad_cell_lot"] = bad["lot_id"]
    _cathode_batches(w, by_item["CEL-21700"], first_use, bad)


def _cathode_batches(w, cell_lots, first_use, bad):
    """Kestrel builds cells from Northwind cathode batches; one batch fed several consecutive cell lots.
    The defect is in that batch, so every sibling cell lot carries it (the youngest have not failed yet)."""
    rng = w.rng
    end = at(add_days(w.as_of, -4), 23, 0, "America/Los_Angeles").astimezone(UTC)
    ordered = sorted(cell_lots, key=lambda l: l["received_at"])
    batch_of = {}
    bad_siblings = [l["lot_id"] for l in ordered if l["received_at"] >= bad["received_at"]
                    and l["lot_id"] in first_use and first_use[l["lot_id"]] <= end]
    k = 0
    i = 0
    while i < len(ordered):
        lot = ordered[i]
        if lot["lot_id"] in bad_siblings:
            group = [l for l in ordered if l["lot_id"] in bad_siblings]
            i = ordered.index(group[-1]) + 1
        else:
            group = ordered[i:i + 2]
            i += 2
        k += 1
        mfg = add_days(min(l["mfg"] for l in group), -rng.randint(18, 25))
        ca_id = f"CA{yymm(mfg)}-{100 + k}"
        kg = round(sum(l["qty"] for l in group) * 0.021 * 1.02, 1)
        w.lots[ca_id] = {"lot_id": ca_id, "item": "CAM-NMC", "supplier": "NWC", "site": "SUP-NWC", "qty": kg, "mfg": mfg,
                         "received_at": at(add_days(mfg, 12), 9, 0, "Asia/Tokyo").astimezone(UTC),
                         "iqc": "NOT_INSPECTED", "origin": "SUPPLIER_ASN", "po": None, "used": kg}
        for l in group:
            batch_of[l["lot_id"]] = (ca_id, round(l["qty"] * 0.021, 1))
        if any(l["lot_id"] == bad["lot_id"] for l in group):
            w.stories["bad_cathode_lot"] = ca_id
            w.stories["bad_cell_lots"] = [l["lot_id"] for l in group]
    # lithium carbonate lots (tier 3) behind the cathode batches
    cathodes = sorted({v[0] for v in batch_of.values()})
    links = [(cell, ca, kg, "SUPPLIER_COA") for cell, (ca, kg) in batch_of.items()]
    for j in range(0, len(cathodes), 3):
        grp = cathodes[j:j + 3]
        mfg = add_days(min(w.lots[c]["mfg"] for c in grp), -rng.randint(30, 45))
        lc_id = f"LC{yymm(mfg)}-{100 + j // 3 + 1}"
        kg = round(sum(w.lots[c]["qty"] for c in grp) * 0.39, 1)
        w.lots[lc_id] = {"lot_id": lc_id, "item": "LI2CO3", "supplier": "ANL", "site": "SUP-ANL", "qty": kg, "mfg": mfg,
                         "received_at": at(add_days(mfg, 35), 9, 0, "Asia/Tokyo").astimezone(UTC),
                         "iqc": "NOT_INSPECTED", "origin": "SUPPLIER_ASN", "po": None, "used": kg}
        links += [(c, lc_id, round(w.lots[c]["qty"] * 0.39, 1), "SUPPLIER_COA") for c in grp]
    w.lot_links = links
