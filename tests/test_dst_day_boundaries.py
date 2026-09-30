"""Roadmap 1.8: DST-boundary tests for timesheet / activity day logic.

2026 US transitions (America/New_York): spring-forward Sun 2026-03-08
(02:00 EST -> 03:00 EDT; the 02:00 hour does not exist as wall time)
and fall-back Sun 2026-11-01 (02:00 EDT -> 01:00 EST; the 01:00 hour
happens twice).

No ``time.tzset`` (absent on Windows, where CI also runs) and no
host-zone dependence: the seams under test consume *aware instants*.
``timesheet._parse_ts`` converts an aware timestamp to a naive local
datetime via ``dt.astimezone()``; on the CI hosts (UTC) and in the
user's fixed-offset zone (Asia/Karachi) that conversion is exact, so
the pins below hold identically in both. A host actually living in a
DST zone gets host-local wall semantics BY DESIGN -- the entry day is
the local wall date of the entry's start, and spans crossing a
transition keep their absolute elapsed minutes in fixed zones.

Pinned seams:

* ``timesheet._parse_ts`` -- aware instants land on the local date;
  naive timestamps keep their wall time verbatim (no guessing).
* ``timesheet.edit_entry`` -- the day-sync branch recomputes
  ``entry.day`` from the start instant and derives minutes from the
  absolute elapsed time across both transitions.
* ``pipeline.run_day`` + ``store.save_events`` -- a pipeline day is
  stored whole under the caller's date, never re-derived row-by-row
  from ``ts``; neighboring days stay empty.
"""

from datetime import date, datetime, timedelta

import pytest

from focuscore import pipeline, store, timesheet

SPRING_FORWARD = "2026-03-08"
FALL_BACK = "2026-11-01"


# ------------------------------------------------------------ _parse_ts ---

def test_parse_ts_aware_instants_land_on_their_day():
    # The two 01:30s of fall-back night are different instants (EDT,
    # then EST); both belong to 2026-11-01 in UTC / Asia/Karachi.
    edt = timesheet._parse_ts("2026-11-01T01:30:00-04:00")
    est = timesheet._parse_ts("2026-11-01T01:30:00-05:00")
    assert edt.date().isoformat() == FALL_BACK
    assert est.date().isoformat() == FALL_BACK
    assert est - edt == timedelta(hours=1)
    # The wall hour that spring-forward skips (02:30 in New York) is
    # still a perfectly valid instant; it lands on the transition day.
    skipped = timesheet._parse_ts("2026-03-08T02:30:00-05:00")
    assert skipped.date().isoformat() == SPRING_FORWARD


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
    out = timesheet.edit_entry(
        entry_id, start_ts="2026-11-01T00:30:00-04:00",
        end_ts="2026-11-01T01:30:00-05:00", db_path=db)
    assert out["minutes"] == 120.0
    assert out["day"] == FALL_BACK


def test_entry_span_across_spring_forward_keeps_absolute_minutes(tmp_path):
    db = str(tmp_path / "dst-spring.db")
    entry_id = _make_entry(db)
    # 01:30 EST -> 03:30 EDT spans the skipped hour: 1 real hour.
    out = timesheet.edit_entry(
        entry_id, start_ts="2026-03-08T01:30:00-05:00",
        end_ts="2026-03-08T03:30:00-04:00", db_path=db)
    assert out["minutes"] == 60.0
    assert out["day"] == SPRING_FORWARD


def test_entry_day_follows_local_start_instant_not_offset_date(tmp_path):
    db = str(tmp_path / "dst-midnight.db")
    entry_id = _make_entry(db)
    out = timesheet.edit_entry(
        entry_id, start_ts="2026-10-31T20:00:00-04:00",
        end_ts="2026-10-31T21:00:00-04:00", db_path=db)
    assert out["minutes"] == 60.0
    # 20:00 at UTC-4 is 00:00 UTC the next day: the day-sync branch
    # follows the computing host's wall date of the start instant,
    # not the date written in the offset timestamp itself.
    assert out["day"] == "2026-11-01"


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
