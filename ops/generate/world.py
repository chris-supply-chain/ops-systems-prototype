"""The mock world: one deterministic simulation of making and moving LV-1s.

Truth is simulated in memory first (what physically happened). Then each outside
system's view of that truth is emitted into the raw_* landing tables, noise and
all, and the ingest normalizers rebuild the canonical core from those payloads,
the same way the real platform would.
"""
import datetime as dt
import random
from collections import defaultdict

from .util import (PT, TPE, TW_HOLIDAYS, US_HOLIDAYS, UTC, add_days, at, iso, next_workday, workdays)


class ShiftClock:
    """Maps (workday index, work-minute) to wall-clock time; work carries across shifts."""

    def __init__(self, days, start_h, start_m, minutes, tz):
        self.days = days
        self.start_h, self.start_m, self.minutes, self.tz = start_h, start_m, minutes, tz
        self.index = {d: i for i, d in enumerate(days)}

    def wall(self, day_idx, minute):
        while minute >= self.minutes:
            minute -= self.minutes
            day_idx += 1
        if day_idx >= len(self.days):
            return None
        return (at(self.days[day_idx], self.start_h, self.start_m, self.tz)
                + dt.timedelta(minutes=minute)).astimezone(UTC)


class World:
    def __init__(self, conn, as_of, seed):
        self.conn = conn
        self.seed = seed
        self.rng = random.Random(seed)
        self.as_of = as_of
        self.now = at(as_of, 8, 0, PT).astimezone(UTC)
        self.now_s = iso(self.now)

        # anchors (all relative to as_of so the story holds for any date)
        self.cm_sop = next_workday(add_days(as_of, -145), TW_HOLIDAYS)
        self.line2_start = next_workday(add_days(self.cm_sop, 70), TW_HOLIDAYS)
        self.pack_sop = next_workday(add_days(self.cm_sop, 22), US_HOLIDAYS)
        self.eco31 = add_days(as_of, -62)
        self.eco36 = add_days(as_of, -41)
        self.eco42 = add_days(as_of, -26)
        self.cell_price_step = add_days(as_of, -45)
        self.cm_price_step = add_days(as_of, -30)
        self.res_start = add_days(as_of, -340)
        self.orders_open = add_days(self.cm_sop, -30)
        self.horizon_end = add_days(as_of, 120)
        cut_day = next_workday(add_days(as_of, -30), TW_HOLIDAYS)
        self.schema_cutover = at(cut_day, 12, 30, TPE).astimezone(UTC)
        self.mapping_v2_at = self.schema_cutover + dt.timedelta(minutes=135)
        self.tz_bug = (add_days(as_of, -72), add_days(as_of, -69))
        storm_day = next_workday(add_days(as_of, -45), TW_HOLIDAYS)
        self.retry_storm = (at(storm_day, 14, 0, TPE).astimezone(UTC), at(storm_day, 14, 40, TPE).astimezone(UTC))
        outage_day = next_workday(add_days(as_of, -12), TW_HOLIDAYS)
        self.cm_outage = (at(outage_day, 10, 0, TPE).astimezone(UTC), at(outage_day, 16, 0, TPE).astimezone(UTC))
        self.s65_start = add_days(as_of, -9)
        self.fw_spike_day = next_workday(add_days(as_of, -19), TW_HOLIDAYS)
        self.dev12_start = add_days(as_of, -12)
        self.dev12_end = add_days(as_of, 4)

        # calendars
        self.cm_days = workdays(add_days(self.cm_sop, -10), self.horizon_end, TW_HOLIDAYS)
        self.us_days = workdays(add_days(self.cm_sop, -10), self.horizon_end, US_HOLIDAYS)
        self.cm_clock = ShiftClock(self.cm_days, 8, 0, 570, TPE)
        self.pack_clock = ShiftClock(self.us_days, 6, 0, 630, PT)

        # in-memory truth, filled by the domain modules
        self.customers, self.orders = [], []
        self.vehicles, self.packs = [], []
        self.consume = defaultdict(list)        # serialized item family -> consumption events
        self.lot_use = defaultdict(list)        # lot item -> consumption events (qty)
        self.lots = {}                          # lot_id -> lot dict
        self.deliveries = defaultdict(list)     # item -> deliveries (serialized + lot items)
        self.units = {}                         # serial -> component unit dict (DU, MTR, CTL, PU, HMI, BMS, FRM)
        self.asns = []                          # supplier ASNs (serialized components)
        self.containers, self.trucks, self.last_mile = [], [], []
        self.holds = []
        self.claims = []
        self.iqc = []
        self.plans = {}
        self.pos, self.po_lines = [], []
        self.confirmations = []
        self.stories = {}                       # story key -> ids, for the logic/tests to find
        self.ids = defaultdict(int)

    def nid(self, key, start=0):
        self.ids[key] += 1
        return start + self.ids[key]

    def cm_plan(self, line, day):
        """Planned CM output for a line on a day (the CM's commit)."""
        if day not in self.cm_clock.index or day < self.cm_sop:
            return 0
        if line == "L1":
            t = self.cm_clock.index[day] - self.cm_clock.index[self.cm_sop]
            return int(round(min(32, 8 + 24 * t / 50)))
        if day < self.line2_start:
            return 0
        t = self.cm_clock.index[day] - self.cm_clock.index[self.line2_start]
        cap = 30 if day >= add_days(self.as_of, 35) else 26
        return int(round(min(cap, 8 + 18 * t / 30)))

    def cm_plan_total(self, day):
        return self.cm_plan("L1", day) + self.cm_plan("L2", day)

    def pack_plan(self, day):
        """Pack MPS: pegged to vehicles landing at the 3PL (CM build ~25 days earlier) plus 10%."""
        if day < self.pack_sop or day not in self.pack_clock.index:
            return 0
        lagged = add_days(day, -25)
        # average CM plan over the surrounding week so the pack line runs level
        window = [self.cm_plan_total(add_days(lagged, k)) for k in range(-3, 4)]
        cm_rate = sum(window) / 5.0
        return int(round(min(66, max(8, 1.1 * cm_rate))))

    def run(self):
        from . import demand, emit, make, move, plan, platform, quality, supply
        from .master import write_master
        write_master(self)
        demand.build(self)
        make.cm_production(self)
        make.pack_production(self)
        supply.serialized_supply(self)
        supply.lot_supply(self)
        move.ocean(self)
        move.pack_trucks(self)
        move.fulfillment(self)
        quality.warranty_cases(self)
        plan.build(self)
        quality.records(self)
        platform.build(self)
        from . import emails
        emails.build(self)
        emit.pre_ingest(self)
