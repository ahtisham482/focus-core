"""Tests for focuscore/calendar_feed.py: ICS parsing, recurrence, cache."""
import time

import pytest

from focuscore import calendar_feed as cal
from focuscore import store

# 2026-09-29 is a Tuesday; 2026-09-28 is a Monday.
SAMPLE = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:standup-1
DTSTART:20260929T090000
DTEND:20260929T093000
SUMMARY:Team standup
END:VEVENT
BEGIN:VEVENT
UID:utc-call-1
DTSTART:20260929T100000Z
DTEND:20260929T110000Z
SUMMARY:UTC call
END:VEVENT
BEGIN:VEVENT
UID:tzid-1
DTSTART;TZID=Asia/Karachi:20260929T140000
DTEND;TZID=Asia/Karachi:20260929T150000
SUMMARY:Karachi lunch
END:VEVENT
BEGIN:VEVENT
UID:dayoff-1
DTSTART;VALUE=DATE:20260929
DTEND;VALUE=DATE:20260930
SUMMARY:Day off
END:VEVENT
BEGIN:VEVENT
UID:walk-1
DTSTART:20260928T080000
DURATION:PT1H
RRULE:FREQ=DAILY;COUNT=3
SUMMARY:Daily walk
END:VEVENT
BEGIN:VEVENT
UID:gym-1
DTSTART:20260928T180000
DTEND:20260928T190000
RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=2
SUMMARY:Gym
END:VEVENT
BEGIN:VEVENT
UID:cancelled-1
DTSTART:20260929T120000
DTEND:20260929T130000
STATUS:CANCELLED
SUMMARY:Should not appear
END:VEVENT
BEGIN:VEVENT
UID:folded-1
DTSTART:20260929T160000
DTEND:20260929T163000
SUMMARY:Long ti
 tle that was folded
END:VEVENT
END:VCALENDAR"""


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "cal.db")
    store.init_db(path)
    return path


def _by_summary(events):
    return {e["summary"]: e for e in events}


def test_unconfigured_state(db):
    events, state = cal.today_events("2026-09-29", db_path=db)
    assert state == "unconfigured"
    assert events == []


def test_set_ical_url_validation(db):
    with pytest.raises(ValueError):
        cal.set_ical_url("not-a-url", db_path=db)
    with pytest.raises(ValueError):
        cal.set_ical_url("ftp://example.com/x.ics", db_path=db)
    cal.set_ical_url("https://example.com/cal.ics", db_path=db)
    assert cal.get_ical_url(db_path=db) == "https://example.com/cal.ics"
    cal.set_ical_url("", db_path=db)
    assert cal.get_ical_url(db_path=db) == ""


def test_parse_single_allday_cancelled_folded(db, monkeypatch):
    monkeypatch.setattr(cal, "_fetch", lambda url: SAMPLE)
    cal.set_ical_url("https://example.com/cal.ics", db_path=db)
    events, state = cal.today_events("2026-09-29", db_path=db)
    assert state == "ok"
    by = _by_summary(events)
    # Floating local time stays as-is.
    assert by["Team standup"]["time"] == "9:00 AM"
    assert not by["Team standup"]["all_day"]
    # All-day event.
    assert by["Day off"]["time"] == "All day"
    assert by["Day off"]["all_day"]
    # Cancelled event is skipped.
    assert "Should not appear" not in by
    # Folded lines are unfolded.
    assert by["Long title that was folded"]["time"] == "4:00 PM"


def test_utc_and_tzid_converted_to_local(db, monkeypatch):
    monkeypatch.setattr(cal, "_fetch", lambda url: SAMPLE)
    cal.set_ical_url("https://example.com/cal.ics", db_path=db)
    events, _ = cal.today_events("2026-09-29", db_path=db)
    by = _by_summary(events)
    from datetime import datetime
    from zoneinfo import ZoneInfo
    local = datetime.now().astimezone().tzinfo
    utc_local = datetime(2026, 9, 29, 10, 0,
                         tzinfo=ZoneInfo("UTC")).astimezone(local)
    expected = cal._fmt_time(utc_local)
    assert by["UTC call"]["time"] == expected
    khi_local = datetime(2026, 9, 29, 14, 0,
                         tzinfo=ZoneInfo("Asia/Karachi")).astimezone(local)
    assert by["Karachi lunch"]["time"] == cal._fmt_time(khi_local)


def test_daily_recurrence_window(db, monkeypatch):
    monkeypatch.setattr(cal, "_fetch", lambda url: SAMPLE)
    cal.set_ical_url("https://example.com/cal.ics", db_path=db)
    # COUNT=3 from Mon 2026-09-28 -> 28th, 29th, 30th.
    for day in ("2026-09-28", "2026-09-29", "2026-09-30"):
        events, _ = cal.today_events(day, db_path=db)
        assert "Daily walk" in _by_summary(events), day
    events, _ = cal.today_events("2026-10-01", db_path=db)
    assert "Daily walk" not in _by_summary(events)


def test_weekly_byday_recurrence(db, monkeypatch):
    monkeypatch.setattr(cal, "_fetch", lambda url: SAMPLE)
    cal.set_ical_url("https://example.com/cal.ics", db_path=db)
    # BYDAY=MO,COUNT=2 from Mon 2026-09-28 -> 9/28 and 10/5.
    events, _ = cal.today_events("2026-09-28", db_path=db)
    assert "Gym" in _by_summary(events)
    events, _ = cal.today_events("2026-09-29", db_path=db)
    assert "Gym" not in _by_summary(events)
    events, _ = cal.today_events("2026-10-05", db_path=db)
    assert "Gym" in _by_summary(events)
    events, _ = cal.today_events("2026-10-12", db_path=db)
    assert "Gym" not in _by_summary(events)


def test_cache_ttl_avoids_refetch(db, monkeypatch):
    calls = []
    monkeypatch.setattr(cal, "_fetch",
                        lambda url: calls.append(url) or SAMPLE)
    cal.set_ical_url("https://example.com/cal.ics", db_path=db)
    cal.today_events("2026-09-29", db_path=db)
    cal.today_events("2026-09-29", db_path=db)
    assert len(calls) == 1
    # Stale the cache -> refetch happens.
    store.set_setting(cal.SETTING_CACHED_AT,
                      str(time.time() - cal.CACHE_TTL - 1), path=db)
    cal.today_events("2026-09-29", db_path=db)
    assert len(calls) == 2


def test_fetch_failure_falls_back_to_stale_cache(db, monkeypatch):
    monkeypatch.setattr(cal, "_fetch", lambda url: SAMPLE)
    cal.set_ical_url("https://example.com/cal.ics", db_path=db)
    events, state = cal.today_events("2026-09-29", db_path=db)
    assert state == "ok" and events

    def _boom(url):
        raise OSError("network down")
    monkeypatch.setattr(cal, "_fetch", _boom)
    store.set_setting(cal.SETTING_CACHED_AT,
                      str(time.time() - cal.CACHE_TTL - 1), path=db)
    events, state = cal.today_events("2026-09-29", db_path=db)
    assert state == "stale"
    assert "Team standup" in _by_summary(events)


def test_fetch_failure_no_cache_is_unreachable(db, monkeypatch):
    def _boom(url):
        raise OSError("network down")
    monkeypatch.setattr(cal, "_fetch", _boom)
    cal.set_ical_url("https://example.com/cal.ics", db_path=db)
    events, state = cal.today_events("2026-09-29", db_path=db)
    assert state == "unreachable"
    assert events == []


def test_events_sorted_by_time(db, monkeypatch):
    monkeypatch.setattr(cal, "_fetch", lambda url: SAMPLE)
    cal.set_ical_url("https://example.com/cal.ics", db_path=db)
    events, _ = cal.today_events("2026-09-29", db_path=db)
    times = [e["summary"] for e in events if not e["all_day"]]
    assert times.index("Team standup") < times.index("Long title that was folded")


def _feed(db, monkeypatch, ics):
    monkeypatch.setattr(cal, "_fetch", lambda url: ics)
    cal.set_ical_url("https://example.com/cal.ics", db_path=db)


def test_rrule_interval(db, monkeypatch):
    _feed(db, monkeypatch, """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:alt-1
DTSTART:20260928T090000
DTEND:20260928T093000
RRULE:FREQ=DAILY;INTERVAL=2;COUNT=3
SUMMARY:Every other day
END:VEVENT
END:VCALENDAR""")
    # 9/28, 9/30, 10/2 -- never 9/29.
    for day in ("2026-09-28", "2026-09-30", "2026-10-02"):
        events, _ = cal.today_events(day, db_path=db)
        assert "Every other day" in _by_summary(events), day
    events, _ = cal.today_events("2026-09-29", db_path=db)
    assert "Every other day" not in _by_summary(events)


def test_escaped_text_unescaped(db, monkeypatch):
    _feed(db, monkeypatch, """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:esc-1
DTSTART:20260929T090000
DTEND:20260929T093000
SUMMARY:Team standup\\, weekly\\nBring notes
END:VEVENT
END:VCALENDAR""")
    events, _ = cal.today_events("2026-09-29", db_path=db)
    assert _by_summary(events)["Team standup, weekly\nBring notes"]


def test_timed_multiday_spans_days(db, monkeypatch):
    _feed(db, monkeypatch, """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:conf-1
DTSTART:20260929T090000
DTEND:20261001T170000
SUMMARY:Conference
END:VEVENT
END:VCALENDAR""")
    by = _by_summary(cal.today_events("2026-09-29", db_path=db)[0])
    assert by["Conference"]["time"] == "9:00 AM"
    assert not by["Conference"]["all_day"]
    # Continuation days render as all-day banners.
    for day in ("2026-09-30", "2026-10-01"):
        by = _by_summary(cal.today_events(day, db_path=db)[0])
        assert by["Conference"]["time"] == "All day", day
    events, _ = cal.today_events("2026-10-02", db_path=db)
    assert "Conference" not in _by_summary(events)


def test_monthly_bymonthday(db, monkeypatch):
    _feed(db, monkeypatch, """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:pay-1
DTSTART:20260915T090000
DTEND:20260915T093000
RRULE:FREQ=MONTHLY;BYMONTHDAY=15;COUNT=2
SUMMARY:Payday
END:VEVENT
END:VCALENDAR""")
    for day in ("2026-09-15", "2026-10-15"):
        events, _ = cal.today_events(day, db_path=db)
        assert "Payday" in _by_summary(events), day
    events, _ = cal.today_events("2026-11-15", db_path=db)
    assert "Payday" not in _by_summary(events)


def test_monthly_ordinal_byday(db, monkeypatch):
    # 3rd Tuesday of Sep 2026 is 9/15; of Oct 2026 is 10/20.
    _feed(db, monkeypatch, """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:mtg-1
DTSTART:20260915T090000
DTEND:20260915T100000
RRULE:FREQ=MONTHLY;BYDAY=3TU;COUNT=2
SUMMARY:Board meeting
END:VEVENT
END:VCALENDAR""")
    for day in ("2026-09-15", "2026-10-20"):
        events, _ = cal.today_events(day, db_path=db)
        assert "Board meeting" in _by_summary(events), day
    events, _ = cal.today_events("2026-10-15", db_path=db)
    assert "Board meeting" not in _by_summary(events)


def test_yearly_anniversary_and_feb29_skip(db, monkeypatch):
    _feed(db, monkeypatch, """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:ann-1
DTSTART:20250929T090000
DTEND:20250929T100000
RRULE:FREQ=YEARLY
SUMMARY:Anniversary
END:VEVENT
BEGIN:VEVENT
UID:leap-1
DTSTART:20240229T090000
DTEND:20240229T100000
RRULE:FREQ=YEARLY
SUMMARY:Leap day
END:VEVENT
END:VCALENDAR""")
    events, _ = cal.today_events("2026-09-29", db_path=db)
    assert "Anniversary" in _by_summary(events)
    # 2026 is not a leap year: Feb 29 has no occurrence.
    events, _ = cal.today_events("2026-02-28", db_path=db)
    assert "Leap day" not in _by_summary(events)
    events, _ = cal.today_events("2026-03-01", db_path=db)
    assert "Leap day" not in _by_summary(events)


def test_exdate_skips_occurrence(db, monkeypatch):
    _feed(db, monkeypatch, """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:gymx-1
DTSTART:20260928T180000
DTEND:20260928T190000
RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=3
EXDATE:20261005T180000
SUMMARY:Gym
END:VEVENT
END:VCALENDAR""")
    for day in ("2026-09-28", "2026-10-12"):
        events, _ = cal.today_events(day, db_path=db)
        assert "Gym" in _by_summary(events), day
    events, _ = cal.today_events("2026-10-05", db_path=db)
    assert "Gym" not in _by_summary(events)


def test_recurrence_id_override_and_deletion(db, monkeypatch):
    _feed(db, monkeypatch, """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:stand-1
DTSTART:20260928T090000
DTEND:20260928T093000
RRULE:FREQ=DAILY;COUNT=3
SUMMARY:Standup
END:VEVENT
BEGIN:VEVENT
UID:stand-1
RECURRENCE-ID:20260929T090000
DTSTART:20260929T110000
DTEND:20260929T113000
SUMMARY:Standup (moved)
END:VEVENT
BEGIN:VEVENT
UID:stand-1
RECURRENCE-ID:20260930T090000
DTSTART:20260930T090000
DTEND:20260930T093000
STATUS:CANCELLED
SUMMARY:Standup
END:VEVENT
END:VCALENDAR""")
    by = _by_summary(cal.today_events("2026-09-28", db_path=db)[0])
    assert "Standup" in by
    by = _by_summary(cal.today_events("2026-09-29", db_path=db)[0])
    assert "Standup (moved)" in by
    assert "Standup" not in by
    events, _ = cal.today_events("2026-09-30", db_path=db)
    assert "Standup" not in _by_summary(events)
    assert "Standup (moved)" not in _by_summary(events)


def test_event_done_now_next_states(db, monkeypatch):
    from datetime import datetime
    _feed(db, monkeypatch, """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:m1
DTSTART:20260929T090000
DTEND:20260929T093000
SUMMARY:Morning
END:VEVENT
BEGIN:VEVENT
UID:m2
DTSTART:20260929T115500
DTEND:20260929T120500
SUMMARY:Noon
END:VEVENT
BEGIN:VEVENT
UID:m3
DTSTART:20260929T150000
DTEND:20260929T160000
SUMMARY:Afternoon
END:VEVENT
END:VCALENDAR""")
    events, state = cal.today_events("2026-09-29", db_path=db,
                                     now=datetime(2026, 9, 29, 12, 0))
    assert state == "ok"
    by = _by_summary(events)
    assert by["Morning"]["state"] == "done"
    assert by["Noon"]["state"] == "now"
    assert by["Afternoon"]["state"] == "next"
    assert by["Noon"]["end"] == "12:05 PM"


def test_invalid_url_post_redirects_with_error(db, monkeypatch, tmp_path):
    import dashboard.app as dash_app
    from focuscore import store as store_mod
    monkeypatch.setattr(store_mod, "DEFAULT_DB_PATH", db)
    client = dash_app.app.test_client()
    resp = client.post("/settings/calendar",
                       data={"ical_url": "not-a-url"})
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/?cal_error=1")
    # The home page surfaces the error.
    flag = tmp_path / ".onboarded"
    flag.write_text("2026-09-29")
    monkeypatch.setattr(dash_app, "ONBOARDED_FLAG", flag)
    monkeypatch.setattr(dash_app, "run_day", lambda day: None)
    html = client.get("/?cal_error=1").data.decode()
    assert "doesn" in html and "http" in html
