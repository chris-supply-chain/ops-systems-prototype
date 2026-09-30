"""Move: containers from the CM across the Pacific, DG trucks from Fremont, the 3PL
kitting orders FIFO by priority, and last-mile carriers delivering them.
"""
import datetime as dt
from collections import deque

from .master import TRANSIT_DAYS
from .util import PT, TPE, UTC, add_days, at, skip_sundays, week_code

VESSELS = ["Pacific Meridian", "Pacific Horizon", "Pacific Lodestar", "Pacific Solstice", "Pacific Tradewind",
           "Pacific Aurora"]
OCEAN_CARRIER = "Pacific Link Lines"
CONTAINER_CAP = 150


def _digits(rng, n):
    return "".join(str(rng.randint(0, 9)) for _ in range(n))


def _sailings(containers):
    seen = {}
    for c in containers:
        seen.setdefault((c["vessel"], c["voyage"]), []).append(c)
    return list(seen.values())


def ocean(w):
    rng = w.rng
    ready = sorted([v for v in w.vehicles if v["built_at"] and not v["scrapped"]], key=lambda v: v["built_at"])
    first = ready[0]["built_at"].astimezone(TPE).date()
    d = first + dt.timedelta(days=(3 - first.weekday()) % 7 or 7)       # first Thursday after first build
    pending, i, week_i = [], 0, 0
    containers = []
    while True:
        load_t = at(d, 14, 0, TPE).astimezone(UTC)
        if load_t > w.now:
            break
        cutoff = at(add_days(d, -1), 23, 59, TPE).astimezone(UTC)
        while i < len(ready) and ready[i]["built_at"] <= cutoff:
            pending.append(ready[i])
            i += 1
        n = len(pending)
        k = n // CONTAINER_CAP + (1 if n % CONTAINER_CAP >= 60 else 0)
        if k:
            take = min(n, k * CONTAINER_CAP)
            loaded, pending = pending[:take], pending[take:]
            etd_day = add_days(d, 3)
            etd = at(etd_day, 18, 0, TPE).astimezone(UTC)
            vessel = VESSELS[week_i % len(VESSELS)]
            voyage = f"{40 + week_i:03d}E"
            eta_planned = at(add_days(etd_day, 15), 8, 0, PT).astimezone(UTC)
            transit = 15 + rng.choice([-1, 0, 0, 0, 0, 1, 2])
            ata = at(add_days(etd_day, transit), rng.randint(5, 13), rng.choice([0, 15, 30, 45]), PT).astimezone(UTC)
            for c in range(k):
                units = loaded[c * CONTAINER_CAP:(c + 1) * CONTAINER_CAP]
                containers.append({
                    "shipment_id": f"OC-{week_code(etd_day)}-{c + 1}", "container_no": f"PLLU{_digits(rng, 7)}",
                    "booking_ref": f"PLL{_digits(rng, 8)}", "bol_no": f"PLLTXGOAK{_digits(rng, 5)}",
                    "vessel": vessel, "voyage": voyage, "load_day": d, "etd": etd, "eta_planned": eta_planned,
                    "ata_truth": ata, "units": [u["serial"] for u in units], "skus": [u["sku"] for u in units],
                    "asn_serials": [u["serial"] for u in units], "short": [], "damaged": [], "delay_days": 0,
                    "exam": False, "late_isf": False, "eta_updates": []})
            week_i += 1
        d = add_days(d, 7)
    w.cm_dock = pending          # built, waiting for a container fill

    # ---- storylines on specific sailings
    def sailing_where(pred):
        return next((cs for cs in _sailings(containers) if pred(cs[0])), [])

    def near(cands, day_of, lo, hi, lo_floor, hi_cap=None):
        """Candidates whose day falls in [as_of+lo, as_of+hi]. Sailings are weekly and arrive Sunday to Wednesday, so a
        narrow window can miss on some weekdays; widen it a day at a time (never past hi_cap or below lo_floor) until one
        qualifies. The story then lands whatever weekday the dataset's today is."""
        hi_cap = hi if hi_cap is None else hi_cap
        for k in range(lo - lo_floor + 1):
            a, b = add_days(w.as_of, max(lo - k, lo_floor)), add_days(w.as_of, min(hi + k, hi_cap))
            got = [c for c in cands if a <= day_of(c) <= b]
            if got:
                return got
        return []

    eta_day = lambda c: c["eta_planned"].astimezone(PT).date()
    ata_day = lambda c: c["ata_truth"].astimezone(PT).date()
    first = near([cs[0] for cs in _sailings(containers)], eta_day, -1, 3, lo_floor=-3, hi_cap=5)
    delayed = sailing_where(lambda c: c is first[0]) if first else []
    for c in delayed:
        c["delay_days"] = 6
        c["ata_truth"] = at(add_days(c["eta_planned"].astimezone(PT).date(), 6), 7, 30, PT).astimezone(UTC)
        c["eta_updates"] = [(at(add_days(w.as_of, -6), 9, 12, PT).astimezone(UTC), c["eta_planned"] + dt.timedelta(days=3),
                             "Weather routing, North Pacific low"),
                            (at(add_days(w.as_of, -2), 16, 40, PT).astimezone(UTC), c["eta_planned"] + dt.timedelta(days=6),
                             "Berth congestion at Oakland; revised berthing window")]
    if delayed:
        w.stories["delayed_sailing"] = (delayed[0]["vessel"], delayed[0]["voyage"])

    minor = sailing_where(lambda c: add_days(w.as_of, -44) <= c["eta_planned"].astimezone(PT).date() <= add_days(w.as_of, -37))
    for c in minor:
        c["ata_truth"] = c["eta_planned"] + dt.timedelta(days=2, hours=3)
        c["eta_updates"] = [(c["eta_planned"] - dt.timedelta(days=4), c["eta_planned"] + dt.timedelta(days=2),
                             "Weather delay departing Taichung")]

    arrived = [c for c in containers if c["ata_truth"] <= w.now]
    # arrived at least 3 days ago, so the hold, the release and the 3PL receipt are all in the past
    exam = near(arrived, ata_day, -5, -3, lo_floor=-14)
    if exam:
        exam[0]["exam"] = True
        w.stories["exam_container"] = exam[0]["shipment_id"]
    short = near([c for c in arrived if len(c["units"]) == CONTAINER_CAP and not c["exam"]], ata_day, -13, -7,
                 lo_floor=-24, hi_cap=-4)
    if short:
        c = short[0]
        c["short"] = c["units"][-4:]            # on the CM's ASN, never loaded, still in the CM warehouse
        c["units"] = c["units"][:-4]
        w.stories["short_container"] = c["shipment_id"]
    late = [c for c in containers if add_days(w.as_of, -55) <= c["load_day"] <= add_days(w.as_of, -45)]
    if late:
        late[0]["late_isf"] = True
        w.stories["late_isf_container"] = late[0]["shipment_id"]
    dmg = near([c for c in arrived if c["shipment_id"] != w.stories.get("short_container") and not c["exam"]],
               ata_day, -18, -14, lo_floor=-30)
    if dmg:
        dmg[0]["damaged"] = dmg[0]["units"][10:13]

    # ---- event timelines (truth); anything after `now` has not happened yet
    for c in containers:
        ev = []
        booked = at(add_days(c["load_day"], -3), 10, 0, TPE).astimezone(UTC)
        gate_in = at(add_days(c["load_day"], 1), rng.randint(9, 15), rng.randint(0, 59), TPE).astimezone(UTC)
        loaded = at(add_days(c["load_day"], 2), rng.randint(6, 20), rng.randint(0, 59), TPE).astimezone(UTC)
        departed = c["etd"] + dt.timedelta(minutes=rng.randint(0, 180))
        ev += [("BOOKED", booked), ("GATE_IN", gate_in), ("LOADED", loaded), ("DEPARTED", departed)]
        for t, new_eta, why in c["eta_updates"]:
            ev.append(("ETA_UPDATE", t, new_eta, why))
        arr = c["ata_truth"]
        discharged = arr + dt.timedelta(hours=rng.randint(8, 30))
        ev += [("ARRIVED", arr), ("DISCHARGED", discharged)]
        if c["exam"]:
            hold = discharged + dt.timedelta(hours=2)
            release = at(add_days(w.as_of, 2), 11, 0, PT).astimezone(UTC)
            ev += [("CUSTOMS_HOLD", hold)]
        else:
            release = discharged + dt.timedelta(hours=rng.randint(0, 6))
        ev.append(("CUSTOMS_RELEASED", release))
        out_gate = at(add_days(release.astimezone(PT).date(), 1), rng.randint(7, 11), rng.randint(0, 59), PT).astimezone(UTC)
        received = out_gate + dt.timedelta(hours=rng.uniform(5, 8))
        ev += [("OUT_GATE", out_gate), ("RECEIVED", received)]
        c["events"] = ev
        c["isf_filed_at"] = loaded - dt.timedelta(hours=10 if c["late_isf"] else rng.randint(50, 80))
        c["entry_filed_at"] = c["eta_planned"] - dt.timedelta(days=3, hours=rng.randint(0, 10))
        c["released_at"] = release
        c["received_at"] = received
    w.containers = containers


def pack_trucks(w):
    rng = w.rng
    built = sorted([p for p in w.packs if p["built_at"] and not p["scrapped"]], key=lambda p: p["built_at"])
    first = built[0]["built_at"].astimezone(PT).date()
    d, i, trucks = first, 0, []
    pending = []
    while True:
        if d.weekday() in (0, 3):
            pickup = at(d, 15, 0, PT).astimezone(UTC)
            if pickup > w.now:
                break
            while i < len(built) and built[i]["built_at"] <= pickup - dt.timedelta(hours=1):
                pending.append(built[i])
                i += 1
            if pending:
                arrival = pickup + dt.timedelta(hours=18, minutes=rng.randint(0, 50))
                trucks.append({"shipment_id": f"TR-{week_code(d)}-{1 if d.weekday() == 0 else 2}",
                               "pro": f"SDG{_digits(rng, 8)}", "pickup": pickup, "arrival": arrival,
                               "received_at": arrival + dt.timedelta(hours=rng.uniform(1, 3)),
                               "units": [p["serial"] for p in pending], "skus": [p["sku"] for p in pending]})
                pending = []
        d = add_days(d, 1)
    w.fre_dock = [p["serial"] for p in built[i:]] + [p["serial"] for p in pending]
    w.trucks = trucks


def fulfillment(w):
    rng = w.rng
    receipts = []
    damaged = set()
    for c in w.containers:
        if c["received_at"] <= w.now:
            damaged.update(c["damaged"])
            receipts += [(c["received_at"], s, k, "V") for s, k in zip(c["units"], c["skus"])
                         if s not in c["short"]]
    for t in w.trucks:
        if t["received_at"] <= w.now:
            receipts += [(t["received_at"], s, k, "P") for s, k in zip(t["units"], t["skus"])]
    receipts.sort(key=lambda r: r[0])
    w.receipt_time = {s: t for t, s, _, _ in receipts}
    w.damaged = damaged

    orders = [o for o in w.orders if not o["cancelled"]]
    for o in orders:
        o.update(allocated_at=None, shipped_at=None, delivered_at=None, vehicle_serial=None, pack_serial=None,
                 extra_pack_serial=None, status="OPEN")
    pools = {}
    ri = 0
    start = receipts[0][0].astimezone(PT).date()
    open_orders = sorted(orders, key=lambda o: o["ordered_at"])
    snapshots = []
    kits_by_day = {}
    d = start
    while d <= w.as_of:
        t_alloc = at(d, 6, 0, PT).astimezone(UTC)
        while ri < len(receipts) and receipts[ri][0] <= t_alloc:
            t, serial, sku, kind = receipts[ri]
            if serial not in damaged:
                pools.setdefault(sku, deque()).append(serial)
            ri += 1
        if d.weekday() != 6:
            cap = 160 if d.weekday() < 5 else 80
            eligible = [o for o in open_orders if o["status"] == "OPEN" and o["ordered_at"] <= t_alloc
                        and o["priority"] <= t_alloc]
            eligible.sort(key=lambda o: (0 if o["channel"] == "FLEET" else 1, o["priority"]))
            n = 0
            for o in eligible:
                if n >= cap:
                    break
                need = [o["vehicle_sku"], o["pack_sku"]] + ([o["extra_pack"]] if o["extra_pack"] else [])
                have = {}
                for sku in need:
                    have[sku] = have.get(sku, 0) + 1
                if any(len(pools.get(sku, ())) < q for sku, q in have.items()):
                    continue
                o["vehicle_serial"] = pools[o["vehicle_sku"]].popleft()
                o["pack_serial"] = pools[o["pack_sku"]].popleft()
                if o["extra_pack"]:
                    o["extra_pack_serial"] = pools[o["extra_pack"]].popleft()
                o["allocated_at"] = t_alloc + dt.timedelta(seconds=20 * n)
                o["status"] = "ALLOCATED"
                n += 1
                ship = at(d, 14, 0, PT).astimezone(UTC) + dt.timedelta(minutes=rng.randint(0, 150))
                if ship > w.now:
                    continue
                kits_by_day[d] = kits_by_day.get(d, 0) + 1
                _last_mile(w, o, d, ship)
            open_orders = [o for o in open_orders if o["status"] == "OPEN"]
        if (w.as_of - d).days < 14:
            snap_t = at(d, 23, 30, PT).astimezone(UTC)
            if snap_t <= w.now:
                while ri < len(receipts) and receipts[ri][0] <= snap_t:
                    t, serial, sku, kind = receipts[ri]
                    if serial not in damaged:
                        pools.setdefault(sku, deque()).append(serial)
                    ri += 1
                snapshots.append((snap_t, {sku: len(q) for sku, q in pools.items()}))
        d = add_days(d, 1)
    # receipts after the last allocation run (today, after 06:00) still land in stock
    while ri < len(receipts):
        t, serial, sku, kind = receipts[ri]
        if serial not in damaged:
            pools.setdefault(sku, deque()).append(serial)
        ri += 1
    w.stock_3pl = pools
    w.wms_snapshots = snapshots
    w.kits_by_day = kits_by_day
    charger_supply(w)


def _last_mile(w, o, day, ship):
    rng = w.rng
    white_glove = o["channel"] == "FLEET" or o["region"] == "WEST"
    carrier = "Crossway Freight" if white_glove else "ParcelPro Ground"
    tracking = f"CW{_digits(rng, 10)}" if white_glove else f"1PP{_digits(rng, 13)}"
    lo, hi = TRANSIT_DAYS[o["region"]]
    ddate = skip_sundays(day, rng.randint(lo, hi))
    exception = rng.random() < 0.015
    ex_t = None
    if exception:
        ex_t = at(ddate, 15, rng.randint(0, 59), PT).astimezone(UTC)
        ddate = skip_sundays(ddate, 2)
    delivered = at(ddate, rng.randint(9, 18), rng.randint(0, 59), PT).astimezone(UTC)
    ofd = at(ddate, 7, 30, PT).astimezone(UTC)
    lm = {"shipment_id": f"LM-{o['order_id'][3:]}", "order": o, "carrier": carrier,
          "mode": "WHITE_GLOVE" if white_glove else "PARCEL", "tracking": tracking, "ship": ship,
          "ofd": ofd, "delivered": delivered, "exception": ex_t,
          "serials": [s for s in (o["vehicle_serial"], o["pack_serial"], o["extra_pack_serial"]) if s]}
    o["shipped_at"] = ship
    o["status"] = "SHIPPED"
    o["last_mile"] = lm
    if delivered <= w.now:
        o["delivered_at"] = delivered
        o["status"] = "DELIVERED"
    w.last_mile.append(lm)


def charger_supply(w):
    """Chargers are bought straight into the 3PL; weekly receipts cover two weeks of kitting."""
    days = sorted(w.kits_by_day)
    if not days:
        return
    d = add_days(days[0], -10)
    d -= dt.timedelta(days=d.weekday())
    delivered = consumed = 0
    receipts = []
    while d <= w.as_of:
        arrival = at(d, 11, 0, PT).astimezone(UTC)
        if arrival > w.now:
            break
        consumed = sum(q for k, q in w.kits_by_day.items() if k < d)
        upcoming = sum(q for k, q in w.kits_by_day.items() if d <= k < add_days(d, 14))
        need = upcoming + 300 - (delivered - consumed)
        if need > 0:
            qty = max(1000, -(-need // 500) * 500)
            receipts.append({"item": "CHG-1", "supplier": "VPC", "site": "3PL-RNO", "arrival": arrival, "qty": qty})
            delivered += qty
        d = add_days(d, 7)
    w.deliveries["CHG-1"] = receipts
    w.charger_on_hand = delivered - sum(w.kits_by_day.values())
