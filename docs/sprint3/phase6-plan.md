# Sprint 3 — Phase 6: Deep Time Intelligence (architectural plan)

**Version:** v1.7.0 · **Status:** planned → implementing · **Date:** 2026-09-26

## Goal

Answer three questions the current Coaching page cannot:

1. **When am I at my best?** — per-weekday chronotype curves, not one
   blended "average day" (a Saturday curve is not a Tuesday curve).
2. **How deep do I go?** — longest uninterrupted productive stretches,
   context-switch rate, time-to-first-focus.
3. **What breaks my focus?** — top distractors and the "entry points":
   which apps most often precede a distraction spiral.

Plus an **interactive day timeline**: every hour expandable to the
activities inside it.

## Non-negotiable design constraints (product standards)

1. **Plain documented arithmetic, no ML.** Every threshold is a named
   module constant (the `coaching.py` precedent). Each card on the page
   gets a `<details>` "How we compute this" section — no hidden magic.
2. **No JavaScript required.** Interactivity = CSS hover tooltips
   (existing pattern) + `<details>`/`<summary>` expanders + anchors.
   The page must be fully usable with JS disabled.
3. **Established code pattern:** pure computation module
   (`focuscore/intelligence.py`) + thin Flask route + `tests/`
   coverage. The route does no math; the module does no HTML.
4. **AFK stays excluded** — ingest already excludes it upstream; all
   metrics read the same stored events the Pulse uses.
5. **Performance:** the page does one 28-day grid build (same cost class
   as the existing 7-day coaching grid, ~4x), one 14-day summary pass,
   and one day's activity read. Secondary page, not home — acceptable.

## New module: `focuscore/intelligence.py`

Reuses `coaching.daily_hourly()` (the exact per-hour bucketing,
including hour-boundary splitting) and `store.get_day_activities()` /
`store.get_day_summary()`. All functions take `db_path=None` and are
pure over stored data.

### Named constants

| Constant | Value | Meaning |
|---|---|---|
| `CHRONOTYPE_DAYS` | 28 | Curve window: 4 full weeks, 4 samples per weekday |
| `MORNING_START, MORNING_END` | 5, 12 | Morning focus window (local hours) |
| `EVENING_START` | 18 | Evening focus window starts (to 24) |
| `CHRONOTYPE_THRESHOLD` | 0.55 | ≥55% of focus minutes in a window → that chronotype |
| `STRETCH_GAP_MINUTES` | 5 | Gaps ≤ this merge into one productive stretch (matches the timesheet block-merge rule) |
| `FOCUS_STRETCH_MINUTES` | 25 | "Real focus" = a productive stretch of at least this long |
| `PEAK_WINDOW_HOURS` | 2 | Peak protection windows are 2 hours |
| `TIMELINE_SLOT_MINUTES` | 15 | Day-timeline granularity (96 slots) |
| `PRODUCTIVE_SCORES` | (2, 1) | (same as coaching) |
| `DISTRACTING_SCORES` | (-1, -2) | (same as coaching) |

### Functions

1. **`chronotype_curves(day_from, day_to, db_path=None)`**
   → `{weekday 0..6: {hour 0..23: {"minutes", "pulse", "days",
   "focus_minutes"}}}`.
   Built from `daily_hourly()`; `pulse` uses the exact
   `productivity_pulse()` weighted formula; `days` = days with any
   tracked time (so thin cells are visibly marked, not silently
   averaged); `focus_minutes` = time at scores +2/+1 (drives
   classification and peak windows, so no second grid pass is needed).

2. **`classify_chronotype(curves)`**
   → `{"type": "morning"|"evening"|"balanced", "morning_share": float,
   "evening_share": float, "peak_hour": int|None}`.
   Shares are computed over focus minutes (scores +2/+1) across the
   whole curve set. Documented rule: morning_share ≥ 0.55 → morning;
   evening_share ≥ 0.55 → evening; else balanced. `peak_hour` = the
   hour with the most focus minutes (None when no data).

3. **`weekday_peak_windows(curves, n=1, window_hours=2)`**
   → `{weekday: {"start_hour", "end_hour", "focus_minutes"}}`.
   Per weekday, the best non-overlapping 2-hour window ranked by focus
   minutes from the aggregated curves. (Complements coaching's
   single blended `best_windows()`.)

4. **`focus_stretches(day_str, db_path=None)`**
   → list of `{"start": ts, "minutes": float}` productive stretches.
   Walk the day's events in `ts` order; a stretch continues while
   events score > 0 and gaps between consecutive events are ≤
   `STRETCH_GAP_MINUTES`. Returns stretches sorted by start.

5. **`depth_summary(day_from, day_to, db_path=None)`**
   → `{"avg_longest": float|None, "best_day": str|None,
   "best_minutes": float, "days": int}` — average of each day's
   longest stretch; days with no productive stretch are skipped
   (never counted as zero).

6. **`switch_rate(day_str, db_path=None)`**
   → `{"switches": int, "per_hour": float|None}`. Counts transitions
   between different `app` values in `ts` order; `per_hour` = switches
   ÷ tracked hours (None when no tracked time). Documented as an
   attention-fragmentation proxy, not a judgment.

7. **`time_to_first_focus(day_str, db_path=None)`**
   → `float|None` minutes from the day's first tracked event to the
   start of its first stretch ≥ `FOCUS_STRETCH_MINUTES`; None when the
   day has no such stretch. Callers take the median over a range.

8. **`distraction_anatomy(day_from, day_to, db_path=None, n=5)`**
   → `{"top": [{"app", "minutes", "share"}], "entry_points":
   [{"app", "count"}]}`. `top`: minutes with score in (-1,-2)
   grouped by `app` (title shown as example via the longest event).
   `entry_points`: for each distraction block start (an event with
   score < 0 whose predecessor scores ≥ 0), count the predecessor's
   app — "what pulls you in".

9. **`week_trends(db_path=None, today=None)`**
   → `{"this_week": {...}, "last_week": {...}, "deltas": {...}}`
   where each week dict has `hours, avg_pulse, focus_minutes,
   switches_per_hour, longest_stretch_avg`. Weeks are Mon–Sun; "this
   week" runs Mon→today. `deltas` holds simple differences
   (this − last); the route renders ▲/▼/=. Rules with insufficient
   data return None fields, never guesses.

10. **`day_timeline(day_str, db_path=None)`**
    → `{"hours": [{hour, minutes, pulse, quarters: [score|None × 4],
    "activities": [{app, title, minutes, score}]}]}`. Quarters split
    each hour into 15-minute slots; a quarter's score = the level with
    the most seconds (None when empty). Activities are the hour's
    events collapsed by (app, title).

## New page: `/intelligence` ("Deep time" in the nav)

Route `intelligence_page()` in `dashboard/app.py`, `active="intelligence"`,
`help_key="intelligence"`. Cards, in order:

1. **Your chronotype** — type, morning/evening shares, peak hour,
   one plain-English line ("You do 63% of your focused work before
   noon — schedule hard things in the morning.").
2. **Rhythm by weekday** — 7 rows × 24 cells, cell color = Pulse
   (reuse `_pulse_cell_color`), tooltip = minutes + days sampled.
3. **Protect these hours** — per-weekday best 2-hour windows
   (weekdays with data only).
4. **Focus depth** — avg longest stretch, best day, median
   time-to-first-focus, avg switches/hour.
5. **What breaks your focus** — top 5 distractors (bar + share),
   top 3 entry points.
6. **This week vs last week** — 5 metrics with deltas.
7. **Day timeline** — `?day=` selector (prev/today links); 24 hour
   rows; hours with data are `<details>`: summary = quarter-strip +
   "HH:00 — 47 min · Pulse 62"; expanded = activity table.
8. Every card carries `<details class="how">How we compute this</details>`.

Empty-data behavior: each card degrades to a one-line note
("Not enough data yet — keep tracking."), never a blank page or a
crash. The page works on day one with zero history.

## Nav, help, docs

- `NAV_LINKS` += `("intelligence", "Deep time", "/intelligence")`
  after Coaching.
- `focuscore/help.py` += `"intelligence"` article (what/do/trouble);
  `NAV_HELP` += `"intelligence": "intelligence"`.
- `USER_GUIDE.md` "What each page is for" += Deep time entry.

## Tests: `tests/test_intelligence.py` (pure, tmp_path DBs)

- Curves: synthetic 28 days with known morning-heavy pattern →
  `classify_chronotype` returns "morning" with expected shares;
  evening-heavy → "evening"; flat → "balanced".
- Peak windows: best window found; non-overlapping.
- Stretches: gap ≤ 5 min merges, gap > 5 min splits; longest
  identified.
- Switch rate: known app sequence → exact switch count.
- Time to first focus: exact minutes on synthetic day; None when no
  25-min stretch exists.
- Anatomy: top distractor correct; entry point = the app that
  preceded the distraction block.
- Trends: two synthetic weeks → exact deltas.
- Timeline: quarter scores correct; hour-boundary splitting
  respected (reuse coaching's chunking via `daily_hourly`).

## Audit notes (for Qwen 3.8 Max)

- No new dependencies. No schema changes (reads existing tables).
- No background threads, no network calls, no writes except none —
  the module is read-only over the DB.
- Privacy posture unchanged: 127.0.0.1 only, no data leaves the PC.
- Risk areas to probe: (a) `_parse_local` timezone handling on
  DST boundaries; (b) `switch_rate` double-counting when ingest
  emits adjacent same-app events (check ingest bucketing);
  (c) 28-day grid cost on very large DBs (years of data);
  (d) `entry_points` attribution when a distraction block starts
  the day (no predecessor — must be skipped, not attributed).

## Out of scope (later phases)

- Phase 7 (blocker) may consume `distraction_anatomy` entry points
  for pre-emptive warnings — the function is built for that reuse.
- Phase 8 (flowtime) may consume `focus_stretches` and
  `time_to_first_focus` for adaptive session lengths.
- No per-day "score" gamification beyond the existing Pulse.
- No export of intelligence data yet (Phase 9 covers exports).
