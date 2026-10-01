"""Date helpers every layer shares. The database stores dates as 'YYYY-MM-DD' text and
timestamps as ISO-8601 text, so reading one back, labelling it for a planner and
counting delivery days are done here, once, for the simulator, the logic and the API.
"""
import datetime as dt

UTC = dt.timezone.utc
_MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def to_date(value):
    """A stored date or timestamp (or a date) as a date: its first ten characters are the day."""
    return dt.date.fromisoformat(str(value)[:10])


def month_day(value):
    """Short label such as 'Sep 26', from a date or stored date text. English month names whatever the locale."""
    d = value if isinstance(value, dt.date) else to_date(value)
    return f"{_MON[d.month - 1]} {d.day}"


def skip_sundays(day, n):
    """Add n delivery days, skipping Sundays (carriers deliver Mon-Sat)."""
    d = day
    while n > 0:
        d += dt.timedelta(days=1)
        if d.weekday() != 6:
            n -= 1
    return d


def iso(t):
    """Aware datetime -> 'YYYY-MM-DDTHH:MM:SSZ' (UTC), the form every timestamp is stored in."""
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
