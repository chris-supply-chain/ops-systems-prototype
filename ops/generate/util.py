"""Time, id and sampling helpers shared by the generator."""
import datetime as dt
import math
from zoneinfo import ZoneInfo

UTC = dt.timezone.utc
TPE = ZoneInfo("Asia/Taipei")
PT = ZoneInfo("America/Los_Angeles")

# Plant holidays inside the simulated window (no production on these days)
TW_HOLIDAYS = {dt.date(2026, 6, 19), dt.date(2026, 9, 25), dt.date(2026, 10, 9), dt.date(2026, 10, 10)}
US_HOLIDAYS = {dt.date(2026, 5, 25), dt.date(2026, 7, 3), dt.date(2026, 9, 7), dt.date(2026, 11, 26),
               dt.date(2026, 11, 27), dt.date(2026, 12, 25)}


def iso(t):
    """Aware datetime -> 'YYYY-MM-DDTHH:MM:SSZ' (UTC)."""
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def at(day, hour, minute=0, tz=PT, second=0):
    if isinstance(tz, str):
        tz = ZoneInfo(tz)
    return dt.datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=tz)


def dstr(day):
    return day.isoformat()


def add_days(day, n):
    return day + dt.timedelta(days=n)


def is_workday(day, holidays):
    return day.weekday() < 5 and day not in holidays


def workdays(start, end, holidays):
    """Workdays in [start, end]."""
    out, d = [], start
    while d <= end:
        if is_workday(d, holidays):
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def next_workday(day, holidays, inclusive=True):
    d = day if inclusive else day + dt.timedelta(days=1)
    while not is_workday(d, holidays):
        d += dt.timedelta(days=1)
    return d


def add_workdays(day, n, holidays):
    d = day
    step = 1 if n >= 0 else -1
    remaining = abs(n)
    while remaining:
        d += dt.timedelta(days=step)
        if is_workday(d, holidays):
            remaining -= 1
    return d


def week_start(day):
    return day - dt.timedelta(days=day.weekday())


def week_code(day):
    y, w, _ = day.isocalendar()
    return f"{y % 100:02d}W{w:02d}"


def yymm(day):
    return f"{day.year % 100:02d}{day.month:02d}"


def poisson(rng, lam):
    """Knuth for small lambda, normal approximation above 30."""
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def weighted(rng, pairs):
    """pairs: [(value, weight), ...]"""
    total = sum(w for _, w in pairs)
    r = rng.random() * total
    for value, w in pairs:
        r -= w
        if r <= 0:
            return value
    return pairs[-1][0]


def skip_sundays(day, n):
    """Add n delivery days, skipping Sundays (carriers deliver Mon-Sat)."""
    d = day
    while n > 0:
        d += dt.timedelta(days=1)
        if d.weekday() != 6:
            n -= 1
    return d
