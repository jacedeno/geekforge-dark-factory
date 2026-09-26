"""Local wall-clock times, time zones and daylight-saving rules (spec section 9, D10)."""

import re
from datetime import date, datetime, timedelta, timezone

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

_LOCAL_RE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2})")
_DATE_RE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")
_HHMM_RE = re.compile(r"([0-9]{2}):([0-9]{2})")


def now_utc():
    return datetime.now(timezone.utc)


def parse_local(text):
    """A bare local `YYYY-MM-DDTHH:MM` with a real calendar date, or None."""
    if not isinstance(text, str):
        return None
    m = _LOCAL_RE.fullmatch(text)
    if not m:
        return None
    try:
        return datetime(*(int(g) for g in m.groups()))
    except ValueError:
        return None


def parse_date(text):
    if not isinstance(text, str):
        return None
    m = _DATE_RE.fullmatch(text)
    if not m:
        return None
    try:
        return date(*(int(g) for g in m.groups()))
    except ValueError:
        return None


def parse_hhmm(text, allow_midnight_end=False):
    """Minutes since local midnight for `HH:MM`, or None."""
    if not isinstance(text, str):
        return None
    m = _HHMM_RE.fullmatch(text)
    if not m:
        return None
    hours, minutes = int(m.group(1)), int(m.group(2))
    if minutes > 59:
        return None
    if hours == 24 and minutes == 0 and allow_midnight_end:
        return 24 * 60
    if hours > 23:
        return None
    return hours * 60 + minutes


def format_local(dt):
    return dt.strftime("%Y-%m-%dT%H:%M")


def resolve(zone, local):
    """Resolve a naive local time to (utc_instant, exists).

    Repeated times resolve to their first occurrence (fold=0). A nonexistent time resolves with the
    offset in force before the gap, which is the wall time shifted forward by the gap.
    """
    utc = local.replace(tzinfo=zone, fold=0).astimezone(timezone.utc)
    exists = utc.astimezone(zone).replace(tzinfo=None) == local
    return utc, exists


def render(instant, zone):
    """RFC 3339 with the zone's offset at that instant, seconds precision."""
    return instant.astimezone(zone).isoformat(timespec="seconds")


def render_utc(instant):
    return instant.astimezone(timezone.utc).isoformat(timespec="seconds")


def day_start(day):
    return datetime(day.year, day.month, day.day)


def windows_for(terms, day):
    """Opening-hour entries of a policy for the weekday of `day`."""
    weekday = WEEKDAYS[day.weekday()]
    return [h for h in terms.hours if h.weekday == weekday]


def close_instant(zone, day, window):
    utc, _ = resolve(zone, day_start(day) + timedelta(minutes=window.closes))
    return utc


def slots(zone, terms, day):
    """Bookable slot starts for a local date under a policy: list of (local_naive, utc_instant)."""
    duration = timedelta(minutes=terms.duration)
    found = {}
    for window in windows_for(terms, day):
        closes = close_instant(zone, day, window)
        minute = window.opens
        while minute <= window.closes:
            local = day_start(day) + timedelta(minutes=minute)
            utc, exists = resolve(zone, local)
            if exists and utc + duration <= closes:
                found[minute] = (local, utc)
            minute += terms.slot_minutes
    return [found[m] for m in sorted(found)]


def check_start(zone, terms, local):
    """Validate a requested start under a policy. Returns (utc_instant, None) or (None, code)."""
    utc, exists = resolve(zone, local)
    if not exists:
        return None, "invalid_local_time"
    day = local.date()
    minute = local.hour * 60 + local.minute
    end = utc + timedelta(minutes=terms.duration)
    within = [w for w in windows_for(terms, day)
              if minute >= w.opens and end <= close_instant(zone, day, w)]
    if not within:
        return None, "outside_opening_hours"
    if not any((minute - w.opens) % terms.slot_minutes == 0 for w in within):
        return None, "not_on_slot_grid"
    return utc, None
