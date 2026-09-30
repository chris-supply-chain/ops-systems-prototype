"""Demand: reservations converted at launch, daily web orders, and fleet releases."""
import datetime as dt

from .master import COLORS, KITS, REGIONS
from .util import PT, UTC, add_days, at, poisson, weighted

PACK_MIX = [("S", 0.56), ("L", 0.44)]
KIT_PRICE = {"S": 3895.0, "L": 4295.0}
EXTRA_PACK_PRICE = {"PK-STD": 749.0, "PK-LRG": 999.0}

FLEETS = [
    # name, state, city, units, kit, ordered (days vs as_of), requested delivery (days vs as_of)
    ("Campus Mobility Group", "CA", "Berkeley", 60, "LV1-SLATE-L", -70, -10),
    ("Mission Street Deliveries", "CA", "San Francisco", 40, "LV1-DUNE-L", -35, 20),
    ("Harbor Parks Dept.", "WA", "Tacoma", 24, "LV1-FERN-S", -12, 45),
]


def _pick_kit(rng):
    color = weighted(rng, [(c[0], c[2]) for c in COLORS])
    size = weighted(rng, PACK_MIX)
    return f"{color}-{size}"


def build(w):
    rng = w.rng
    states = [(s, v[1]) for s, v in REGIONS.items()]
    orders = []

    def customer(kind="CONSUMER", name=None, state=None, city=None):
        n = w.nid("customer")
        cid = f"C{100000 + n}"
        state = state or weighted(rng, states)
        region = REGIONS[state][0]
        city = city or rng.choice(REGIONS[state][2])
        w.customers.append({"customer_id": cid, "kind": kind, "display_name": name or f"Customer {100000 + n}",
                            "city": city, "state": state, "region": region, "zip3": f"{rng.randint(900, 999)}"})
        return cid, state, region

    def order(ordered_at, reserved_at=None, channel="D2C", cust=None, requested=None, kit=None, extra=None):
        cid, state, region = cust or customer()
        kit = kit or _pick_kit(rng)
        vehicle, pack = KITS[kit]
        if extra is None and channel == "D2C" and rng.random() < 0.08:
            extra = "PK-LRG" if rng.random() < 0.6 else "PK-STD"
        total = KIT_PRICE[kit[-1]] + (EXTRA_PACK_PRICE[extra] if extra else 0)
        o = {"customer_id": cid, "channel": channel, "reserved_at": reserved_at, "ordered_at": ordered_at,
             "requested_date": requested, "region": region, "state": state, "kit": kit, "vehicle_sku": vehicle,
             "pack_sku": pack, "extra_pack": extra, "total_usd": total, "cancelled": False}
        if channel == "FLEET":
            o["priority"] = at(add_days(requested, -14), 0, 0, PT).astimezone(UTC)
        else:
            o["priority"] = reserved_at or ordered_at
        orders.append(o)
        return o

    # 1) Reservations since launch, configured into orders when the configurator opened
    span = (w.orders_open - w.res_start).days
    for _ in range(1500):
        d = add_days(w.res_start, int(span * (rng.random() ** 1.8)))
        reserved = at(d, rng.randint(6, 22), rng.randint(0, 59), PT).astimezone(UTC)
        od = add_days(w.orders_open, rng.randint(0, 20))
        ordered = at(od, rng.randint(6, 22), rng.randint(0, 59), PT).astimezone(UTC)
        o = order(ordered, reserved_at=reserved)
        if rng.random() < 0.03:
            o["cancelled"] = True

    # 2) Daily web orders, growing as launch coverage spreads
    total_days = (w.as_of - w.orders_open).days
    for k in range(total_days + 1):
        day = add_days(w.orders_open, k)
        lam = (8 + 18 * k / max(1, total_days)) * (1.3 if day.weekday() >= 5 else 1.0)
        for _ in range(poisson(rng, lam)):
            t = at(day, rng.randint(6, 23), rng.randint(0, 59), PT).astimezone(UTC)
            if t < w.now:
                order(t)

    # 3) Fleet customers: one order per unit (releases against a fleet agreement)
    for name, state, city, units, kit, ordered_d, requested_d in FLEETS:
        cust = customer("FLEET", name, state, city)
        ordered = at(add_days(w.as_of, ordered_d), 10, 0, PT).astimezone(UTC)
        for _ in range(units):
            order(ordered, channel="FLEET", cust=cust, requested=add_days(w.as_of, requested_d), kit=kit, extra=False)

    orders.sort(key=lambda o: o["ordered_at"])
    for i, o in enumerate(orders):
        o["order_id"] = f"SO-{100001 + i}"
        if o["extra_pack"] is False:
            o["extra_pack"] = None
    w.orders = orders
