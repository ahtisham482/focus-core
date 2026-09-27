# Phase 12 (v1.14.0) — Deep Time Visual Analytics & Executive Reports

Second half of the Sprint 5 focus-intelligence overhaul. Extends the
Phase 6 `/intelligence` page and `focuscore/intelligence.py` module
with server-rendered visual analytics and a printable executive report.

## Constraints (binding)

- **Zero external charting/JS libraries.** All charts are pure inline
  SVG strings built server-side + plain CSS. No `<script>` anywhere
  on the new surfaces.
- **Bounded queries only.** Every new query uses
  `store.get_activities_range(start_ts, end_ts)` (backed by
  `idx_activities_ts`). No full-day scans for the new cards; the
  legacy `day_timeline`/`daily_hourly` full-day path is untouched.
- **Invariant I-1** (Enforcement Independence): the shield worker is
  not touched by this phase. Intelligence reads are dashboard-side.
- **Report security standard** (Phase 9/10): the printable report
  carries `default-src 'none'; style-src 'unsafe-inline'; img-src data:;`
  CSP, zero JS, `@media print` CSS.
- **Tests:** all 501 existing tests stay green; new suite
  `tests/test_phase12_intelligence.py`.

## Module design — `focuscore/intelligence.py` (additions)

All new functions are pure, read-only, take `db_path=None`, and use
documented named constants (no hidden magic).

### `day_hourly_depth(day_str, db_path=None)`
Per-hour `seconds_by_level` for one day via ONE bounded range query
`[day 00:00, day 23:59:59]`. Returns
`{hour: {2: s, 1: s, 0: s, -1: s, -2: s}}`. Events crossing hour
boundaries are split by seconds (same rule as coaching.daily_hourly).

### `flow_index(day_str, db_path=None)` → `{"score": int 0-100, "label", "components", "wow_delta"}`
Composite, all integer-rounded at the end:
- **Deep work ratio (40 pts):** `40 * (focus_minutes / active_minutes)`
  where focus = +1/+2 minutes, active = all scored minutes.
- **Time-to-focus (30 pts):** median TTF over trailing 28 days
  (existing `median_time_to_focus`); `30 * max(0, 1 - median_ttf/60)`.
- **Switch resilience (30 pts):** switches/hour for the day
  (existing `switch_rate`); `30 * max(0, 1 - rate/12)`.
- Labels: ≥80 "Optimal Flow", 60–79 "Strong Focus", 40–59
  "Moderate Focus", 20–39 "Fragmented", <20 "Scattered".
- `wow_delta`: today's score minus the mean of the previous 7 days
  (None when no baseline).

### `recovery_cost(day_str, db_path=None)`
- `recovery_minutes`: `switches * SWITCH_RECOVERY_MIN (1)` +
  `distraction_blocks * BLOCK_RECOVERY_MIN (10)`, integer.
- `top_friction`: top 5 (app, minutes) from -1/-2 activity
  (existing `distraction_anatomy` core, reused).
Documented constants; plain arithmetic.

### `day_ratio_buckets(day_str, db_path=None)`
`{"deep": min(+2), "shallow": min(+1), "neutral": min(0),
"distraction": min(-1/-2)}` for the donut.

### `coaching_cards(db_path=None, today=None)` → list of cards
`{"title", "body"}` derived from existing `chronotype_curves` +
`weekday_peak_windows` + Phase 11 `chronotype.get_window`:
1. Protect-your-peak card ("68% of deep work occurs 09:30–12:00…").
2. Meeting-window card (lowest-focus 2h block on weekdays).
3. Distraction card (top friction app + suggested guard), only when
   data supports it. Max 3 cards; plain language, no jargon.

### `week_flow_trends(db_path=None, today=None)`
This week (Mon–Sun) mean Flow Index vs trailing 3-week baseline mean,
plus per-day scores for the trend sparkline.

### `switch_heatmap_7x24(day_from, day_to, db_path=None)`
`{weekday: {hour: switches}}` — switches counted as app-to-app
transitions in timestamp order (bounded range query per day).

## Routes — `dashboard/routes/system.py`

### `/intelligence` (extend, not replace)
New cards appended after existing content:
1. Flow Index hero card (big number + label + WoW delta).
2. 24h SVG depth timeline (per-hour stacked bars, peak-window overlay
   band from `chronotype.get_window`, completed session blocks from
   `store` as annotations).
3. Deep/Shallow donut (inline SVG).
4. Recovery cost card (minutes lost + top friction apps).
5. Coaching cards.
6. Week trend sparkline + 7×24 switch heatmap (SVG table).
7. Link to the printable report.

### `/intelligence/report` (new)
Standalone HTML: Flow Index, donut, timeline, coaching cards,
recovery cost, week trends. Strict CSP meta, zero JS, print CSS,
`?day=` param. Reuses the same SVG builders.

## SVG builders (in `system.py`, pure string functions)

- `_svg_depth_timeline(hours, peak, sessions)`: 24 stacked bars,
  960×220 viewBox, gradient fills, `<title>` tooltips per bar.
- `_svg_donut(buckets)`: 4-segment donut via stroke-dasharray on
  circles; center shows deep-work %.
- `_svg_heatmap(grid)`: 7×24 rect grid with green→red scale.
- `_svg_sparkline(values)`: polyline week trend.

## Tests — `tests/test_phase12_intelligence.py`

- Flow index bounds (0–100 int), label thresholds, empty-day → 0/"Scattered".
- Deep ratio math on seeded activities (known 50/50 → 20 pts).
- Recovery cost arithmetic (switches × 1 + blocks × 10).
- Donut buckets sum to day active minutes.
- Coaching cards: peak card mentions the window; max 3; empty DB →
  graceful fallback card.
- Heatmap shape 7×24, counts ≥ 0.
- Week trends: baseline delta sign correct on scripted data.
- `/intelligence` returns 200 and contains the new card markers.
- `/intelligence/report` returns 200, contains CSP meta, contains no
  `<script`.
- Bounded-query check: `day_hourly_depth` issues a single range query
  (monkeypatch `store.get_activities_range`, assert call count == 1
  and ts args bound the day).
