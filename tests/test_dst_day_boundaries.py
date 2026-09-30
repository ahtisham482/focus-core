"""Roadmap 1.8: DST-boundary tests for timesheet / activity day logic.

2026 US transitions (America/New_York): spring-forward Sun 2026-03-08
(02:00 EST -> 03:00 EDT; the 02:00 hour does not exist as wall time)
and fall-back Sun 2026-11-01 (02:00 EDT -> 01:00 EST; the 01:00 hour
happens twice).

No ``time.tzset`` (absent on Windows, where CI also runs). The tests
are zone-hermetic: they pass in any host zone. The seams under test
consume *aware instants* -- ``timesheet._parse_ts`` converts an aware
timestamp to a naive local datetime via ``dt.astimezone()`` -- so
every expectation that depends on the host zone is derived in the
test from the aware input instant with that same stdlib primitive.
A host living in a DST zone gets host-local wall semantics BY DESIGN:
the entry day is the local wall date of the entry's start, and spans
crossing a transition keep the elapsed time of their host-local wall
readings (which equals the absolute elapsed time in fixed zones).
Each conversion-sensitive case also states the same instant(s) in a
second offset notation: a correct seam normalizes both notations to
the same local result, so dropping the ``astimezone()`` conversion
cannot hide in a host whose offset matches the first notation.

Pinned seams:

* ``timesheet._parse_ts`` -- aware instants land on the local date;
  naive timestamps keep their wall time verbatim (no guessing).
* ``timesheet.edit_entry`` -- the day-sync branch recomputes
  ``entry.day`` from the start instant and derives minutes from the
  host-local elapsed time across both transitions.
* ``pipeline.run_day`` + ``store.save_events`` -- a pipeline day is
  stored whole under the caller's date, never re-derived row-by-row
  from ``ts``; neighboring days stay empty.
"""

from datetime import date, datetime, timedelta

import pytest

from focuscore import pipeline, store, timesheet

SPRING_FORWARD = "2026-03-08"
FALL_BACK = "2026-11-01"


def _local_naive(ts):
    """Host-local naive reading of an aware timestamp string."""
    return datetime.fromisoformat(ts).astimezone().replace(tzinfo=None)


def _local_day(ts):
    """Host-local calendar day of an aware timestamp string."""
    return _local_naive(ts).date().isoformat()


def _local_minutes(start_ts, end_ts):
    """Minutes between two aware timestamps, in host-local wall time."""
    elapsed = _local_naive(end_ts) - _local_naive(start_ts)
    return round(elapsed.total_seconds() / 60.0, 1)


# ------------------------------------------------------------ _parse_ts ---

def test_parse_ts_aware_instants_land_on_their_day():
    # The two 01:30s of fall-back night are different instants (EDT,
    # then EST); each belongs to its host-local start day.
    edt_ts = "2026-11-01T01:30:00-04:00"
    est_ts = "2026-11-01T01:30:00-05:00"
    edt = timesheet._parse_ts(edt_ts)
    est = timesheet._parse_ts(est_ts)
    assert edt.date().isoformat() == _local_day(edt_ts)
    assert est.date().isoformat() == _local_day(est_ts)
    assert est - edt == _local_naive(est_ts) - _local_naive(edt_ts)
    # The wall hour that spring-forward skips (02:30 in New York) is
    # still a perfectly valid instant; it lands on its host-local day.
    skipped_ts = "2026-03-08T02:30:00-05:00"
    skipped = timesheet._parse_ts(skipped_ts)
    assert skipped.date().isoformat() == _local_day(skipped_ts)
    # The same instants written in UTC must normalize to the same
    # host-local readings: the offset notation must not change the
    # result, in any host zone.
    assert timesheet._parse_ts("2026-11-01T05:30:00+00:00") == edt
    assert timesheet._parse_ts("2026-11-01T06:30:00+00:00") == est
    assert timesheet._parse_ts("2026-03-08T07:30:00+00:00") == skipped


def test_parse_ts_naive_wall_time_is_kept_verbatim():
    naive = timesheet._parse_ts("2026-11-01T01:30:00")
    assert naive == datetime(2026, 11, 1, 1, 30)
    assert naive.tzinfo is None


# ------------------------------------------------------------ edit_entry ---

def _make_entry(db):
    return store.create_entry("2026-10-31", "2026-10-31T23:00",
                              "2026-10-31T23:30", 30.0, "Work", path=db)


def test_entry_span_across_fall_back_keeps_absolute_minutes(tmp_path):
    db = str(tmp_path / "dst-fall.db")
    entry_id = _make_entry(db)
    # 00:30 EDT -> 01:30 EST spans the repeated hour: 2 real hours.
    start_ts = "2026-11-01T00:30:00-04:00"
    end_ts = "2026-11-01T01:30:00-05:00"
    out = timesheet.edit_entry(
        entry_id, start_ts=start_ts, end_ts=end_ts, db_path=db)
    assert out["minutes"] == _local_minutes(start_ts, end_ts)
    assert out["day"] == _local_day(start_ts)
    # The same span written in UTC must give the same result.
    utc_start = "2026-11-01T04:30:00+00:00"
    utc_end = "2026-11-01T06:30:00+00:00"
    out_utc = timesheet.edit_entry(
        _make_entry(db), start_ts=utc_start, end_ts=utc_end, db_path=db)
    assert out_utc["minutes"] == _local_minutes(utc_start, utc_end)
    assert out_utc["day"] == _local_day(utc_start)
    assert out_utc["minutes"] == out["minutes"]
    assert out_utc["day"] == out["day"]


def test_entry_span_across_spring_forward_keeps_absolute_minutes(tmp_path):
    db = str(tmp_path / "dst-spring.db")
    entry_id = _make_entry(db)
    # 01:30 EST -> 03:30 EDT spans the skipped hour: 1 real hour.
    start_ts = "2026-03-08T01:30:00-05:00"
    end_ts = "2026-03-08T03:30:00-04:00"
    out = timesheet.edit_entry(
        entry_id, start_ts=start_ts, end_ts=end_ts, db_path=db)
    assert out["minutes"] == _local_minutes(start_ts, end_ts)
    assert out["day"] == _local_day(start_ts)
    # The same span written in UTC must give the same result.
    utc_start = "2026-03-08T06:30:00+00:00"
    utc_end = "2026-03-08T07:30:00+00:00"
    out_utc = timesheet.edit_entry(
        _make_entry(db), start_ts=utc_start, end_ts=utc_end, db_path=db)
    assert out_utc["minutes"] == _local_minutes(utc_start, utc_end)
    assert out_utc["day"] == _local_day(utc_start)
    assert out_utc["minutes"] == out["minutes"]
    assert out_utc["day"] == out["day"]


def test_entry_day_follows_local_start_instant_not_offset_date(tmp_path):
    db = str(tmp_path / "dst-midnight.db")
    entry_id = _make_entry(db)
    start_ts = "2026-10-31T20:00:00-04:00"
    end_ts = "2026-10-31T21:00:00-04:00"
    out = timesheet.edit_entry(
        entry_id, start_ts=start_ts, end_ts=end_ts, db_path=db)
    assert out["minutes"] == _local_minutes(start_ts, end_ts)
    # 20:00 at UTC-4 is 00:00 UTC the next day: the day-sync branch
    # follows the computing host's wall date of the start instant,
    # not the date written in the offset timestamp itself.
    assert out["day"] == _local_day(start_ts)
    # The same span written in UTC must give the same result.
    utc_start = "2026-11-01T00:00:00+00:00"
    utc_end = "2026-11-01T01:00:00+00:00"
    out_utc = timesheet.edit_entry(
        _make_entry(db), start_ts=utc_start, end_ts=utc_end, db_path=db)
    assert out_utc["minutes"] == _local_minutes(utc_start, utc_end)
    assert out_utc["day"] == _local_day(utc_start)
    assert out_utc["minutes"] == out["minutes"]
    assert out_utc["day"] == out["day"]


# ------------------------------------------------------ pipeline / store ---

@pytest.mark.parametrize("day", [SPRING_FORWARD, FALL_BACK])
def test_pipeline_demo_day_is_bucketed_wholly(tmp_path, day):
    db = str(tmp_path / "dst-pipe.db")
    summary = pipeline.run_day(date.fromisoformat(day), demo=True,
                               db_path=db)
    assert summary["day"] == day
    # The demo day is fixed (10 events, 480 tracked minutes, 1h AFK)
    # on EVERY calendar day -- DST transition days included.
    assert summary["total_seconds"] == 28800.0
    assert summary["afk_seconds"] == 3600.0
    assert len(store.get_day_activities(day, path=db)) == 10

    day_date = date.fromisoformat(day)
    for neighbor in (day_date - timedelta(days=1),
                     day_date + timedelta(days=1)):
        assert store.get_day_activities(neighbor.isoformat(),
                                        path=db) == []


@pytest.mark.parametrize("day, stamps", [
    # Both 01:30 wall readings of fall-back night, as UTC instants.
    (FALL_BACK, ["2026-11-01T05:30:00Z", "2026-11-01T06:30:00Z"]),
    # Instants straddling the skipped 02:00 wall hour.
    (SPRING_FORWARD, ["2026-03-08T06:30:00Z", "2026-03-08T07:30:00Z"]),
])
def test_save_events_stores_caller_day_verbatim(tmp_path, day, stamps):
    db = str(tmp_path / "dst-save.db")
    events = [{"ts": ts, "duration": 600, "app": "Code", "title": "t",
               "category": "Software Development", "score": 2,
               "match_key": "app:Code"} for ts in stamps]
    # activities.day is the caller's day, stored verbatim -- never
    # re-derived from ts, even for UTC-stamped events.
    store.save_events(day, events, path=db)
    rows = store.get_day_activities(day, path=db)
    assert [row["ts"] for row in rows] == stamps
    day_date = date.fromisoformat(day)
    for neighbor in (day_date - timedelta(days=1),
                     day_date + timedelta(days=1)):
        assert store.get_day_activities(neighbor.isoformat(),
                                        path=db) == []
