"""Calendar feed: today's rhythm from a Google Calendar secret iCal URL.

The user pastes the URL once (Home -> Rhythm section); this module fetches
the ICS, parses VEVENTs, expands recurrences, and returns today's events.
Local-first:

- Fetch at most every 15 minutes; the raw ICS is cached in the settings
  table between fetches.
- A failed fetch falls back to the last synced copy, reported as the
  ``"stale"`` state -- never silently presented as fresh.
- Stdlib only (urllib, zoneinfo) -- no new dependencies.

``today_events`` returns ``(events, state)`` where state is
``"unconfigured"`` | ``"ok"`` | ``"stale"`` | ``"unreachable"``.
Each event: ``{"time", "end", "summary", "all_day", "state"}``; ``state``
is ``"done"`` / ``"now"`` / ``"next"`` for timed events and ``"allday"``
for all-day banners, so the UI can style them differently.

What the parser does and does not do
------------------------------------
It is not a general-purpose ICS library. It handles the subset real
calendar subscriptions emit: VEVENTs with DTSTART/DTEND (date or floating
local datetime, UTC ``Z`` suffix, or TZID), multi-day events, folded
lines, escaped TEXT (``\\,`` ``\\;`` ``\\n`` ``\\\\``), RRULE FREQ
DAILY/WEEKLY/MONTHLY/YEARLY with INTERVAL, COUNT, UNTIL, BYDAY (including
ordinals like ``3TU`` for MONTHLY), BYMONTHDAY, EXDATE, and RECURRENCE-ID
overrides (moved or deleted occurrences, including STATUS:CANCELLED).

Known limits: YEARLY supports the plain same-month/day anniversary form
only (BYMONTH/BYDAY variants are not expanded); VALARM/VTODO/VJOURNAL are
ignored; RDATE is not supported.
"""

import calendar as _calendar
import re
import time
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import store

SETTING_URL = "calendar_ical_url"
SETTING_CACHE = "calendar_ical_cache"
SETTING_CACHED_AT = "calendar_ical_cached_at"
CACHE_TTL = 15 * 60
FETCH_TIMEOUT = 8
_MAX_EXPAND_DAYS = 400

_WDAY = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


# ------------------------------------------------------------------ settings

def get_ical_url(db_path=None):
    """Return the stored secret iCal URL, or '' when not configured."""
    return (store.get_setting(SETTING_URL, "", path=db_path) or "").strip()


def set_ical_url(url, db_path=None):
    """Store (or clear) the iCal URL. Raises ValueError on a bad scheme."""
    url = (url or "").strip()
    if url and not re.match(r"^https?://", url, re.IGNORECASE):
        raise ValueError("Calendar URL must start with http:// or https://")
    store.set_setting(SETTING_URL, url, path=db_path)
    # Invalidate the cache so the next read refetches immediately.
    store.set_setting(SETTING_CACHED_AT, "", path=db_path)
    return url


# --------------------------------------------------------------------- fetch

def _fetch(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": "FocusCore/1.15"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        raw = resp.read()
    charset = resp.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


def _cached_text(db_path):
    try:
        at = float(store.get_setting(SETTING_CACHED_AT, "0", path=db_path)
                    or 0)
    except (TypeError, ValueError):
        at = 0
    if time.time() - at >= CACHE_TTL:
        return None
    return store.get_setting(SETTING_CACHE, "", path=db_path) or None


def _fetch_fresh(db_path):
    """Return ``(ics_text, is_fresh)``.

    ``is_fresh`` is False when the network fetch failed (or returned
    garbage) and we fell back to an older cached copy.
    """
    url = get_ical_url(db_path)
    if not url:
        return "", True
    text = _cached_text(db_path)
    if text is not None:
        return text, True
    try:
        text = _fetch(url)
    except Exception:
        old = store.get_setting(SETTING_CACHE, "", path=db_path) or ""
        return old, False
    if not text.strip().upper().startswith("BEGIN:VCALENDAR"):
        old = store.get_setting(SETTING_CACHE, "", path=db_path) or ""
        return old, False
    store.set_setting(SETTING_CACHE, text, path=db_path)
    store.set_setting(SETTING_CACHED_AT, str(time.time()), path=db_path)
    return text, True


def get_cached_text(db_path=None):
    """Return ICS text, refreshing when the cache is stale.

    On network failure, falls back to the stale cache (which may be '').
    """
    return _fetch_fresh(db_path)[0]


# --------------------------------------------------------------------- parse

def _unfold(text):
    out = []
    for line in (text or "").splitlines():
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _unescape_text(value):
    """Unescape an RFC 5545 TEXT property value (SUMMARY, ...)."""
    return (value.replace("\\\\", "\x00")
                 .replace("\\n", "\n").replace("\\N", "\n")
                 .replace("\\,", ",").replace("\\;", ";")
                 .replace("\x00", "\\"))


def _parse_dt(value, params):
    """Return (datetime | date, is_all_day); (None, False) when unparsable."""
    value = (value or "").strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        try:
            return datetime.strptime(value, "%Y%m%d").date(), True
        except ValueError:
            return None, False
    tz = None
    if value.endswith("Z"):
        tz = ZoneInfo("UTC")
        value = value[:-1]
    elif params.get("TZID"):
        try:
            tz = ZoneInfo(params["TZID"])
        except Exception:
            tz = None
    dt = None
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
        try:
            dt = datetime.strptime(value, fmt)
            break
        except ValueError:
            continue
    if dt is None:
        return None, False
    if tz is not None:
        dt = dt.replace(tzinfo=tz)
    return dt, False


def _parse_duration(value):
    m = re.fullmatch(
        r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?",
        (value or "").strip())
    if not m or not any(m.groups()):
        return None
    days, hours, mins, secs = (int(x) if x else 0 for x in m.groups())
    return timedelta(days=days, hours=hours, minutes=mins, seconds=secs)


def _parse_rrule(value):
    parts = {}
    for chunk in (value or "").split(";"):
        if "=" in chunk:
            key, val = chunk.split("=", 1)
            parts[key.strip()] = val.strip()
    return parts


def parse_ical(text):
    """Parse ICS text into a list of event dicts."""
    events = []
    in_event = False
    props = None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            in_event = True
            props = {}
            continue
        if line == "END:VEVENT":
            if in_event and props is not None:
                ev = _build_event(props)
                if ev is not None:
                    events.append(ev)
            in_event = False
            props = None
            continue
        if not in_event or props is None or ":" not in line:
            continue
        left, value = line.split(":", 1)
        bits = left.split(";")
        params = {}
        for bit in bits[1:]:
            if "=" in bit:
                key, val = bit.split("=", 1)
                params[key] = val
        if bits[0] == "EXDATE" and bits[0] in props:
            # Several EXDATE lines may occur; together they are one
            # comma-joined list.
            prev_params, prev_val = props[bits[0]]
            props[bits[0]] = (params or prev_params,
                              prev_val + "," + value)
        else:
            props[bits[0]] = (params, value)
    return events


def _build_event(props):
    status = props.get("STATUS", ({}, ""))[1].strip().upper()
    cancelled = status == "CANCELLED"
    if "DTSTART" not in props:
        return None
    params, value = props["DTSTART"]
    start, all_day = _parse_dt(value, params)
    if start is None:
        return None
    end = None
    if "DTEND" in props:
        end_params, end_value = props["DTEND"]
        end, _ = _parse_dt(end_value, end_params)
    elif "DURATION" in props and not all_day:
        dur = _parse_duration(props["DURATION"][1])
        if dur is not None:
            end = start + dur
    summary = (_unescape_text(props.get("SUMMARY", ({}, ""))[1].strip())
               or "(No title)")
    rrule = _parse_rrule(props["RRULE"][1]) if "RRULE" in props else None
    exdates = []
    if "EXDATE" in props:
        ex_params, ex_value = props["EXDATE"]
        for tok in ex_value.split(","):
            parsed, _ = _parse_dt(tok.strip(), ex_params)
            if parsed is not None:
                exdates.append(parsed)
    recurrence_id = None
    if "RECURRENCE-ID" in props:
        rid_params, rid_value = props["RECURRENCE-ID"]
        recurrence_id, _ = _parse_dt(rid_value, rid_params)
    uid = props.get("UID", ({}, ""))[1].strip()
    return {"summary": summary, "start": start, "end": end,
            "all_day": all_day, "rrule": rrule, "exdates": exdates,
            "uid": uid, "recurrence_id": recurrence_id,
            "cancelled": cancelled}


# --------------------------------------------------------------- recurrence

def _excluded(ev, occ_start):
    """True when occ_start matches one of the event's EXDATEs."""
    if not ev["exdates"]:
        return False
    if isinstance(occ_start, datetime):
        occ = _as_local(occ_start).replace(tzinfo=None)
        for x in ev["exdates"]:
            if isinstance(x, datetime):
                if _as_local(x).replace(tzinfo=None) == occ:
                    return True
            elif x == occ.date():
                return True
        return False
    for x in ev["exdates"]:
        xd = x.date() if isinstance(x, datetime) else x
        if xd == occ_start:
            return True
    return False


def _span_occurrences(start, end):
    """Split one occurrence into per-day ``(start, end)`` tuples.

    The first day keeps the real start/end; continuation days of a timed
    multi-day event yield ``(date, None)`` so they render as all-day
    banners. All-day date spans honour the exclusive DTEND. A DTEND
    exactly at midnight does not spill into the next day.
    """
    if isinstance(start, date) and not isinstance(start, datetime):
        if not (isinstance(end, date)
                and not isinstance(end, datetime)):
            return [(start, None)]
        days = []
        day = start
        while day < end:
            days.append((day, None))
            day += timedelta(days=1)
            if (day - start).days > _MAX_EXPAND_DAYS:
                break
        return days or [(start, None)]
    if not isinstance(end, datetime) or end.date() <= start.date():
        return [(start, end)]
    last = end.date()
    if end.time() == datetime.min.time():
        last -= timedelta(days=1)
    days = [(start, end)]
    day = start.date() + timedelta(days=1)
    while day <= last:
        days.append((day, None))
        day += timedelta(days=1)
    return days


def _nth_weekdays(year, month, weekday, n, ndays):
    """Dates of the nth (n>0) or nth-from-last (n<0) weekday in a month."""
    if n > 0:
        day = date(year, month, 1)
        while day.weekday() != weekday:
            day += timedelta(days=1)
        day += timedelta(days=7 * (n - 1))
        return [day] if day.month == month else []
    day = date(year, month, ndays)
    while day.weekday() != weekday:
        day -= timedelta(days=1)
    day -= timedelta(days=7 * (-n - 1))
    return [day] if day.month == month else []


def _month_candidates(year, month, rrule, start):
    """Candidate dates in a month for a MONTHLY RRULE."""
    ndays = _calendar.monthrange(year, month)[1]
    days = set()
    bymonthday = (rrule.get("BYMONTHDAY") or "").strip()
    byday = (rrule.get("BYDAY") or "").strip().upper()
    if bymonthday:
        for tok in bymonthday.split(","):
            try:
                day_no = int(tok.strip())
            except (TypeError, ValueError):
                continue
            if day_no < 0:
                day_no = ndays + 1 + day_no  # -1 == last day of month
            if 1 <= day_no <= ndays:
                days.add(date(year, month, day_no))
    elif byday:
        ordinals, plains = [], []
        for tok in (t.strip() for t in byday.split(",")):
            if not tok:
                continue
            name, prefix = tok[-2:], tok[:-2]
            if (name in _WDAY and prefix
                    and prefix.lstrip("+-").isdigit()):
                ordinals.append((name, int(prefix)))
            elif tok in _WDAY:
                plains.append(tok)
        for name, num in ordinals:
            days.update(_nth_weekdays(year, month, _WDAY[name], num,
                                      ndays))
        for name in plains:
            day = date(year, month, 1)
            while day.weekday() != _WDAY[name]:
                day += timedelta(days=1)
            while day.month == month:
                days.add(day)
                day += timedelta(days=7)
    else:
        if start.day <= ndays:
            days.add(date(year, month, start.day))
    return sorted(days)


def _expand(ev, window_start, window_end):
    """Return ``[(start, end)]`` occurrences of `ev` in the date window."""
    start, end = ev["start"], ev["end"]
    rrule = ev["rrule"] or {}
    base = start.date() if isinstance(start, datetime) else start
    freq = (rrule.get("FREQ") or "").upper()
    if freq not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"):
        freq = ""  # unknown frequency: single base event only
    try:
        interval = max(1, int(rrule.get("INTERVAL") or 1))
    except (TypeError, ValueError):
        interval = 1
    try:
        count = int(rrule.get("COUNT") or 0)
    except (TypeError, ValueError):
        count = 0
    until = None
    if rrule.get("UNTIL"):
        parsed, _ = _parse_dt(rrule["UNTIL"], {})
        if isinstance(parsed, datetime):
            until = parsed.date()
        elif isinstance(parsed, date):
            until = parsed

    occurrences = []  # [(occ_start, occ_end)] before per-day spanning

    def add_occurrence(day):
        if isinstance(start, datetime):
            occ = datetime(day.year, day.month, day.day,
                           start.hour, start.minute, start.second,
                           tzinfo=start.tzinfo)
            dur = (end - start) if isinstance(end, datetime) else None
            occurrences.append((occ, occ + dur if dur else None))
        else:
            occ_end = None
            if (isinstance(end, date)
                    and not isinstance(end, datetime)):
                occ_end = day + (end - start)
            occurrences.append((day, occ_end))

    def past_until(day):
        return until is not None and day > until

    if not freq:
        if not past_until(base):
            add_occurrence(base)
    elif freq == "DAILY":
        day, n = base, 0
        while (day - base).days <= _MAX_EXPAND_DAYS:
            if (count and n >= count) or past_until(day):
                break
            if not count and not until and day > window_end:
                break
            if (day - base).days % interval == 0:
                add_occurrence(day)
                n += 1
            day += timedelta(days=1)
    elif freq == "WEEKLY":
        bydays = {_WDAY[b] for b in
                   (rrule.get("BYDAY") or "").upper().split(",")
                   if b in _WDAY}
        if not bydays:
            bydays = {base.weekday()}
        day, n = base, 0
        while (day - base).days <= _MAX_EXPAND_DAYS:
            if (count and n >= count) or past_until(day):
                break
            if not count and not until and day > window_end:
                break
            week_idx = (day - base).days // 7
            if day.weekday() in bydays and week_idx % interval == 0:
                add_occurrence(day)
                n += 1
            day += timedelta(days=1)
    elif freq == "MONTHLY":
        base_mi = start.year * 12 + (start.month - 1)
        k, n = 0, 0
        while k < 1200:
            mi = base_mi + k * interval
            year, month = mi // 12, mi % 12 + 1
            if date(year, month, 1) > window_end:
                break
            if until is not None and date(year, month, 1) > until:
                break
            for day in _month_candidates(year, month, rrule, start):
                if day < base or past_until(day):
                    continue
                if count and n >= count:
                    break
                if day > window_end:
                    break
                add_occurrence(day)
                n += 1
            if count and n >= count:
                break
            k += 1
    elif freq == "YEARLY":
        # Plain anniversary form: same month/day each year.
        k, n = 0, 0
        while k < 200:
            year = start.year + k * interval
            try:
                day = date(year, start.month, start.day)
            except ValueError:
                k += 1
                continue  # e.g. Feb 29 in a non-leap year: no occurrence
            if year > window_end.year + 1:
                break
            if day < base or past_until(day):
                k += 1
                continue
            if count and n >= count:
                break
            if day <= window_end:
                add_occurrence(day)
                n += 1
            k += 1

    out = []
    for occ_start, occ_end in occurrences:
        if _excluded(ev, occ_start):
            continue
        for s_day, e_day in _span_occurrences(occ_start, occ_end):
            s_date = (s_day.date() if isinstance(s_day, datetime)
                      else s_day)
            if window_start <= s_date <= window_end:
                out.append((s_day, e_day))
    return out


def _apply_overrides(events):
    """Split out RECURRENCE-ID overrides.

    Returns ``(base_events, overrides)`` where overrides maps
    ``uid -> {date: event or None}``; None marks a deleted occurrence.
    """
    bases = []
    overrides = {}
    for ev in events:
        rid = ev.get("recurrence_id")
        uid = ev.get("uid") or ""
        if rid is not None and uid:
            rday = rid.date() if isinstance(rid, datetime) else rid
            overrides.setdefault(uid, {})[rday] = (
                None if ev.get("cancelled") else ev)
        else:
            bases.append(ev)
    return bases, overrides


def _as_local(dt):
    local = datetime.now().astimezone().tzinfo
    if dt.tzinfo is None:
        return dt.replace(tzinfo=local)
    return dt.astimezone(local)


def _fmt_time(dt):
    hour = dt.hour % 12 or 12
    suffix = "AM" if dt.hour < 12 else "PM"
    return "%d:%02d %s" % (hour, dt.minute, suffix)


# ------------------------------------------------------------------ public

def _event_row(ev, start, end, now):
    allday = ev["all_day"] or not isinstance(start, datetime)
    if allday:
        return {"time": "All day", "end": "", "summary": ev["summary"],
                "all_day": True, "state": "allday",
                "sort": (1, 0, ev["summary"].lower())}
    local = _as_local(start).replace(tzinfo=None)
    local_end = (_as_local(end).replace(tzinfo=None)
                 if isinstance(end, datetime) else None)
    if local_end is not None and local_end <= now:
        state = "done"
    elif local <= now:
        state = "now"
    else:
        state = "next"
    return {"time": _fmt_time(local),
            "end": _fmt_time(local_end) if local_end is not None else "",
            "summary": ev["summary"], "all_day": False, "state": state,
            "sort": (0, local.hour * 60 + local.minute,
                     ev["summary"].lower())}


def today_events(day=None, db_path=None, now=None):
    """Return ``(events, state)`` for `day` (``YYYY-MM-DD``, default today).

    events: [{"time", "end", "summary", "all_day", "state"}] sorted by
    start time. state: "unconfigured" | "ok" | "stale" | "unreachable".
    ``now`` (a datetime) pins the done/now/next classification in tests.
    Never raises.
    """
    day = day or date.today().isoformat()
    if not get_ical_url(db_path):
        return [], "unconfigured"
    try:
        text, fresh = _fetch_fresh(db_path)
        if text:
            # Validate the feed parses (result discarded; parsed again
            # below). Keeps the "Never raises" contract, e.g. on machines
            # without tzdata where zoneinfo raises.
            parse_ical(text)
    except Exception:
        return [], "unreachable"
    if not text:
        return [], "unreachable"
    try:
        target = date.fromisoformat(day)
    except ValueError:
        return [], "unreachable"
    now = now or datetime.now()
    now = now.replace(tzinfo=None)
    out = []
    bases, overrides = _apply_overrides(parse_ical(text))
    for ev in bases:
        if ev.get("cancelled"):
            continue
        ovr = overrides.get(ev.get("uid") or "", {})
        for start, end in _expand(ev, target, target):
            occ_day = (start.date() if isinstance(start, datetime)
                       else start)
            if occ_day in ovr:
                repl = ovr[occ_day]
                if repl is None:
                    continue  # deleted occurrence
                for r_start, r_end in _expand(repl, target, target):
                    out.append(_event_row(repl, r_start, r_end, now))
                continue
            out.append(_event_row(ev, start, end, now))
    out.sort(key=lambda e: e["sort"])
    return ([{"time": e["time"], "end": e["end"], "summary": e["summary"],
              "all_day": e["all_day"], "state": e["state"]} for e in out],
            "ok" if fresh else "stale")
