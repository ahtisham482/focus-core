"""Tests for the Phase 1 scoring engine, taxonomy and demo pipeline.

Every Pulse expectation below follows RescueTime's official weighted formula:
    ((vd*0 + d*1 + n*2 + p*3 + vp*4) / (total*4)) * 100
"""

from datetime import date

import pytest

from focuscore.pipeline import run_day
from focuscore.scoring import (
    SCORE_LEVELS,
    UI_LABELS,
    productivity_pulse,
    resolve_activity_score,
)
from focuscore.taxonomy import categorize


def test_pulse_all_focus_work_is_100():
    assert productivity_pulse({2: 3600}) == 100.0


def test_pulse_all_very_distracting_is_0():
    assert productivity_pulse({-2: 3600}) == 0.0


def test_pulse_all_neutral_is_50():
    assert productivity_pulse({0: 3600}) == 50.0


def test_pulse_all_personal_is_25():
    assert productivity_pulse({-1: 3600}) == 25.0


def test_pulse_all_other_work_is_75():
    assert productivity_pulse({1: 3600}) == 75.0


def test_pulse_equal_focus_and_distracting_is_50():
    assert productivity_pulse({2: 3600, -2: 3600}) == 50.0


def test_pulse_focus_plus_neutral_is_75():
    # (7200*4 + 7200*2) / (14400*4) * 100 = 75.0
    assert productivity_pulse({2: 7200, 0: 7200}) == 75.0


def test_pulse_empty_day_is_0():
    assert productivity_pulse({}) == 0.0
    assert productivity_pulse({2: 0, -2: 0}) == 0.0


def test_pulse_rounded_to_one_decimal():
    assert productivity_pulse({2: 1, -2: 2}) == 33.3


def test_score_levels_and_ui_labels():
    assert SCORE_LEVELS == {
        2: "Very Productive",
        1: "Productive",
        0: "Neutral",
        -1: "Distracting",
        -2: "Very Distracting",
    }
    assert UI_LABELS == {
        2: "Focus Work",
        1: "Other Work",
        0: "Neutral",
        -1: "Personal",
        -2: "Distracting",
    }


def test_override_beats_category_inheritance():
    # No override -> inherited score wins.
    assert resolve_activity_score("domain:youtube.com", -2, None) == -2
    # Override -> user choice wins.
    assert resolve_activity_score("domain:youtube.com", -2, 2) == 2
    assert resolve_activity_score("app:code", 2, -2) == -2


def test_invalid_scores_rejected():
    with pytest.raises(ValueError):
        resolve_activity_score("x", 5, None)
    with pytest.raises(ValueError):
        resolve_activity_score("x", 2, 5)


def test_seed_spot_checks():
    cat, score, rule = categorize(
        "chrome", "Some video - YouTube", "https://www.youtube.com/watch?v=x")
    assert (cat, score) == ("Entertainment", -2)
    assert rule == "domain:youtube.com"

    cat, score, rule = categorize(
        "chrome", "org/repo - GitHub", "https://github.com/org/repo")
    assert (cat, score) == ("Software Development", 2)

    # Subdomains match their parent domain rule.
    cat, score, _ = categorize(
        "chrome", "Inbox", "https://mail.google.com/mail/u/0/#inbox")
    assert (cat, score) == ("Communication & Scheduling", 0)

    # App rules work without a URL.
    cat, score, _ = categorize("code", "Visual Studio Code", None)
    assert (cat, score) == ("Software Development", 2)


def test_uncategorized_defaults_to_neutral():
    cat, score, rule = categorize("mystery-app-xyz", "Some random window", None)
    assert (cat, score, rule) == ("Uncategorized", 0, None)


def test_demo_pipeline_end_to_end(tmp_path):
    db = str(tmp_path / "demo.db")
    summary = run_day(date(2026, 1, 5), demo=True, db_path=db)

    # Hand-computed from the fixed demo schedule:
    # +2: 3.5h, +1: 1h, 0: 1.667h, -1: 0h, -2: 1.833h -> Pulse 63.5
    assert summary["pulse"] == 63.5
    assert summary["afk_seconds"] == 3600.0
    assert summary["total_seconds"] == 8 * 3600
    assert summary["seconds_by_level"][2] == 3.5 * 3600
    assert summary["seconds_by_level"][-2] == pytest.approx(1.8333333 * 3600)

    # The unknown app must surface in the uncategorized queue...
    assert summary["uncategorized_count"] == 1
    assert summary["uncategorized"][0]["match_key"] == "app:mystery-app-xyz"
    # ...and AFK must not leak into the scored buckets.
    assert sum(summary["seconds_by_level"].values()) == 8 * 3600


# ------ regression tests: YouTube recognized without the web extension ---

def test_youtube_recognized_from_window_title_without_url():
    # Without the optional aw-watcher-web browser extension ActivityWatch
    # logs url=None, so the youtube.com domain rules never fire. The
    # window title ("YouTube - Google Chrome") must still categorize it
    # as Entertainment (-2) so focus blocking catches it.
    category, score, rule = categorize("chrome.exe",
                                       "YouTube - Google Chrome", None)
    assert category == "Entertainment"
    assert score == -2
    assert rule == "title_keyword:youtube"


def test_app_rule_beats_youtube_title_keyword():
    # App rules are checked before title keywords: a code editor whose
    # window title merely mentions youtube stays Software Development.
    category, score, rule = categorize(
        "code.exe", "youtube-dl - Visual Studio Code", None)
    assert category == "Software Development"
    assert score == 2
    assert rule.startswith("app_")
