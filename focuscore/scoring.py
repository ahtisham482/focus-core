"""Productivity scoring — mirrors RescueTime's official mechanics exactly.

Scale (verified against RescueTime's help docs):
    +2  Very Productive   (UI label: "Focus Work")
    +1  Productive        (UI label: "Other Work")
     0  Neutral           (UI label: "Neutral")
    -1  Distracting       (UI label: "Personal")
    -2  Very Distracting  (UI label: "Distracting")

Rules:
- Every activity inherits its score from its category by default.
- The user may override any single activity's score; the override always wins.
- The daily Productivity Pulse is a WEIGHTED formula over scored seconds
  (not a simple productive-vs-total percentage).
- AFK/idle seconds are excluded from the Pulse entirely.
- Uncategorized activities count as Neutral (0) in the Pulse.
"""

SCORE_LEVELS = {
    2: "Very Productive",
    1: "Productive",
    0: "Neutral",
    -1: "Distracting",
    -2: "Very Distracting",
}

UI_LABELS = {
    2: "Focus Work",
    1: "Other Work",
    0: "Neutral",
    -1: "Personal",
    -2: "Distracting",
}

VALID_SCORES = (2, 1, 0, -1, -2)


def resolve_activity_score(activity_key, category_score, override):
    """Return the effective score for one activity.

    The per-activity override (set by the user) always wins over the
    score inherited from the activity's category. ``override`` is None
    when the user has not set one. ``activity_key`` is only used for
    error messages.
    """
    if override is not None:
        if override not in VALID_SCORES:
            raise ValueError(
                "Invalid override score %r for %r; must be one of %s"
                % (override, activity_key, VALID_SCORES)
            )
        return override
    if category_score not in VALID_SCORES:
        raise ValueError(
            "Invalid category score %r for %r; must be one of %s"
            % (category_score, activity_key, VALID_SCORES)
        )
    return category_score


def productivity_pulse(seconds_by_level):
    """Daily Productivity Pulse (0-100): RescueTime's exact weighted formula.

    ``seconds_by_level`` maps score level (-2..2) to seconds spent there.
    With vd=seconds at -2, d=seconds at -1, n=seconds at 0,
    p=seconds at +1, vp=seconds at +2, total=all scored seconds:

        ((vd*0 + d*1 + n*2 + p*3 + vp*4) / (total*4)) * 100

    The weighting means neutral/personal time pulls the score toward the
    middle instead of dragging it to zero: 100% Distracting -> 0,
    100% Personal -> 25, 100% Neutral -> 50, 100% Other Work -> 75,
    100% Focus Work -> 100.

    Result is rounded to 1 decimal. When total is 0 (no tracked time),
    returns 0.0 -- a documented choice so an empty day shows 0 rather
    than crashing on division by zero.
    """
    vd = seconds_by_level.get(-2, 0)
    d = seconds_by_level.get(-1, 0)
    n = seconds_by_level.get(0, 0)
    p = seconds_by_level.get(1, 0)
    vp = seconds_by_level.get(2, 0)
    total = vd + d + n + p + vp
    if total <= 0:
        return 0.0
    weighted = vd * 0 + d * 1 + n * 2 + p * 3 + vp * 4
    return round((weighted / (total * 4)) * 100, 1)
