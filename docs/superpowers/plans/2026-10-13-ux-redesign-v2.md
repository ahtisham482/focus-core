# UX Redesign v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved warm-premium visual redesign of Focus Core (spec: `workspace/goals/focus-core-time-tracking-build/files/ux-redesign-v2-design-spec.md`) — grouped navigation, design tokens, component kit, and screen-by-screen polish — without changing any feature, flow, or data logic.

**Architecture:** Introduce a CSS token layer (`tokens.css`) loaded before the existing `style.css`; derive a `NAV_GROUPS` structure from the existing `NAV_LINKS` (which stays untouched for backward compatibility); restyle section-by-section in `style.css` using the tokens; make only surgical HTML-structure changes in the page builders named below. No new frameworks, no new routes, no data changes.

**Tech Stack:** Python/Flask (dashboard), vanilla CSS/JS, SQLite. Tests: pytest.

**Test strategy for visual work:** Every task has (1) HTML-structure tests via `dashboard.app.layout()` / Flask test client (following the existing pattern in `tests/test_ui_ux_v15.py`), (2) CSS-content tests that read the stylesheet and assert the expected rules exist, and (3) a Playwright render-and-inspect at 1440×900 with seeded demo data as the per-phase gate (see Task 27 for the render recipe). Run everything under `TZ=Asia/Karachi`.

---

## File structure

| File | Responsibility in this plan |
|------|------------------------------|
| Create: `dashboard/static/tokens.css` | All design tokens: colors (light + dark), type scale, spacing, radius, shadows, 11 category colors |
| Modify: `dashboard/app.py` | `layout()`: link `tokens.css`; `NAV_GROUPS` + grouped-nav builder (keep `NAV_LINKS` unchanged) |
| Modify: `dashboard/static/style.css` | Restyle nav, buttons, cards, inputs, empty states, type, page sections — always via `var(--token)` |
| Modify: `dashboard/static/nav.js` | Dropdown toggle (caret button), Escape-to-close, outside-click close |
| Modify: `dashboard/routes/core.py` | `index()` (Home), `day_view()` (Day), `alerts_page()`, `goals_page()`, `activities()` (Review), `welcome()` |
| Modify: `dashboard/routes/focus.py` | `focus_page()`, `_fc_idle_page()` (sentence builder), `_fc_ring_card()` |
| Modify: `dashboard/routes/timesheet.py` | `timesheet_page()` (form layout, human date/time) |
| Modify: `dashboard/routes/invoices.py` | `invoices_page()`, `invoice_detail_page()` (table, totals, pills) |
| Modify: `dashboard/routes/system_reports.py` | `report_page()`, `coaching_page()` (chart palette) |
| Modify: `dashboard/routes/system_intelligence.py` | `intelligence_page()`, `_svg_donut()`, `_svg_heatmap()`, `_svg_sparkline()` (category colors) |
| Modify: `dashboard/routes/system_shield.py` | `shield_page()` (toggles, status) |
| Modify: `dashboard/routes/system_backup.py` | `backup_page()` (cards) |
| Create: `tests/test_ux_redesign_v2.py` | All new tests for this plan |

---

## Phase 0 — Safety net

### Task 1: Confirm the dashboard UI baseline is green

**Files:** (none — verification only)

- [ ] **Step 1: Run the existing UI tests**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ui_ux_v15.py tests/test_a11y_31.py -q`
Expected: all pass (exit 0). If anything fails, stop and fix before continuing — the plan assumes a green baseline.

---

## Phase 1 — Design tokens

### Task 2: Create `tokens.css` and link it in `layout()`

**Files:**
- Create: `dashboard/static/tokens.css`
- Modify: `dashboard/app.py` (the `<link>` line in `layout()`)
- Test: `tests/test_ux_redesign_v2.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for UX redesign v2 (warm premium)."""
import pathlib
import dashboard.app as dash_app
from dashboard.app import layout

TOKENS = pathlib.Path(__file__).resolve().parent.parent / "dashboard" / "static" / "tokens.css"


def test_tokens_css_linked():
    html = layout("Dashboard", "<p>Hello</p>", active="home")
    assert "<link rel='stylesheet' href='/static/tokens.css'>" in html
    # tokens must load BEFORE style.css so components can use var(--token)
    assert html.index("tokens.css") < html.index("style.css")


def test_tokens_define_core_variables():
    css = TOKENS.read_text()
    for var in ["--bg:", "--surface:", "--ink:", "--muted:", "--gold:",
                "--gold-deep:", "--line:", "--danger:", "--ok:",
                "--radius-card:", "--shadow-card:", "--fs-body:"]:
        assert var in css, f"tokens.css missing {var}"
    assert "[data-theme='dark']" in css


def test_tokens_define_all_category_colors():
    css = TOKENS.read_text()
    expected = ["business", "communication-scheduling", "social-networking",
                "design-composition", "entertainment", "news",
                "software-development", "reference-learning", "shopping",
                "utilities", "uncategorized"]
    for slug in expected:
        assert f"--cat-{slug}:" in css, f"tokens.css missing --cat-{slug}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q`
Expected: FAIL — `tokens.css` does not exist yet.

- [ ] **Step 3: Create `dashboard/static/tokens.css` with the full token set**

```css
/* Focus Core design tokens — UX redesign v2 (warm premium).
   Source of truth for color, type, spacing, radius, shadow.
   Spec: workspace/goals/focus-core-time-tracking-build/files/ux-redesign-v2-design-spec.md */

:root {
  /* ── Color ── */
  --bg: #faf6ef;
  --surface: #ffffff;
  --ink: #2b2620;
  --muted: #8a8175;
  --gold: #c9a227;
  --gold-deep: #a88420;
  --line: #e8e0d0;
  --danger: #b5443c;
  --ok: #4d7c4d;

  /* ── Type scale (size / line-height) ── */
  --fs-display: 40px; --lh-display: 48px;
  --fs-h1: 30px;      --lh-h1: 38px;
  --fs-h2: 22px;      --lh-h2: 30px;
  --fs-h3: 18px;      --lh-h3: 26px;
  --fs-body: 16px;    --lh-body: 26px;
  --fs-small: 13px;   --lh-small: 20px;

  /* ── Spacing (8px base) ── */
  --sp-1: 8px; --sp-2: 16px; --sp-3: 24px;
  --sp-4: 32px; --sp-6: 48px; --sp-8: 64px;

  /* ── Radius ── */
  --radius-card: 14px;
  --radius-input: 10px;
  --radius-pill: 999px;

  /* ── Shadow ── */
  --shadow-card: 0 2px 12px rgba(60, 45, 20, 0.06);
  --shadow-lift: 0 6px 24px rgba(60, 45, 20, 0.10);

  /* ── Category colors (order matches focuscore/taxonomy.py DEFAULT_CATEGORIES) ── */
  --cat-business: #2e7d32;
  --cat-communication-scheduling: #1976d2;
  --cat-social-networking: #7b1fa2;
  --cat-design-composition: #ef6c00;
  --cat-entertainment: #c2185b;
  --cat-news: #0288d1;
  --cat-software-development: #5d4037;
  --cat-reference-learning: #0097a7;
  --cat-shopping: #f9a825;
  --cat-utilities: #616161;
  --cat-uncategorized: #9e9e9e;
}

[data-theme='dark'] {
  --bg: #1c1814;
  --surface: #26211b;
  --ink: #f0e9dc;
  --muted: #a89c88;
  --gold: #d4af37;
  --gold-deep: #b8942a;
  --line: #3a332a;
  --danger: #d4695f;
  --ok: #7cb87c;
  --shadow-card: 0 2px 12px rgba(0, 0, 0, 0.35);
  --shadow-lift: 0 6px 24px rgba(0, 0, 0, 0.45);
}
```

Then in `dashboard/app.py`, in `layout()`, change:
```python
"<link rel='stylesheet' href='/static/style.css'>"
```
to:
```python
"<link rel='stylesheet' href='/static/tokens.css'>"
"<link rel='stylesheet' href='/static/style.css'>"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
cd ~/workspace/focus-core-v1.15 && git add dashboard/static/tokens.css dashboard/app.py tests/test_ux_redesign_v2.py && git commit -m "ux-v2: add design tokens layer (tokens.css)"
```

## Phase 2 — Grouped navigation

### Task 3: Add `NAV_GROUPS` derived from `NAV_LINKS`

**Files:**
- Modify: `dashboard/app.py` (after the `NAV_LINKS` definition, ~line 63)
- Test: `tests/test_ux_redesign_v2.py`

`NAV_LINKS` stays exactly as-is (existing test `test_nav_links_have_icons` depends on it). `NAV_GROUPS` is derived from it.

- [ ] **Step 1: Write the failing test**

```python
from dashboard.app import NAV_GROUPS, NAV_LINKS


def test_nav_groups_cover_all_links():
    # 4 groups, every one of the 12 NAV_LINKS appears exactly once
    assert len(NAV_GROUPS) == 4
    members = [m for _, _, _, ms in NAV_GROUPS for m in ms]
    assert sorted(members) == sorted(k for k, _, _, _ in NAV_LINKS)


def test_nav_group_labels_link_to_primary_page():
    groups = {gkey: (glabel, ghref) for gkey, glabel, ghref, _ in NAV_GROUPS}
    assert groups["track"][1] == "/"
    assert groups["focus"][1] == "/focus"   # Focus stays one click away
    assert groups["work"][1] == "/invoices"
    assert groups["system"][1] == "/shield"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "nav_group"`
Expected: FAIL — `NAV_GROUPS` does not exist.

- [ ] **Step 3: Add `NAV_GROUPS` after `NAV_LINKS` in `dashboard/app.py`**

```python
# Grouped navigation (UX redesign v2): (group_key, group_label, primary_href,
# [member keys]). Derived from NAV_LINKS — NAV_LINKS stays the flat source.
NAV_GROUPS = [
    ("track",  "Track",  "/",         ["home", "timesheet"]),
    ("focus",  "Focus",  "/focus",    ["focus", "goals"]),
    ("work",   "Work",   "/invoices", ["invoices", "report", "coaching", "intelligence"]),
    ("system", "System", "/shield",   ["shield", "alerts", "backup", "review"]),
]

_NAV_BY_KEY = {key: (label, href, icon) for key, label, href, icon in NAV_LINKS}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "nav_group"`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
cd ~/workspace/focus-core-v1.15 && git add dashboard/app.py tests/test_ux_redesign_v2.py && git commit -m "ux-v2: add NAV_GROUPS derived from NAV_LINKS"
```

### Task 4: Render the grouped nav with dropdowns in `layout()`

**Files:**
- Modify: `dashboard/app.py` (the nav-building block in `layout()`, ~lines 141-152)
- Test: `tests/test_ux_redesign_v2.py`

- [ ] **Step 1: Write the failing test**

```python
def test_layout_renders_grouped_nav():
    import re
    html = layout("Dashboard", "<p>Hello</p>", active="focus")
    # exactly 4 group divs (label links use class='nav-group-label' — excluded by the <div prefix)
    assert len(re.findall(r"<div class='nav-group(?: |')", html)) == 4
    assert html.count("class='nav-group-label'") == 4
    assert "<a class='nav-group-label' href='/focus'>Focus</a>" in html
    # dropdown holds every member; icons preserved
    assert "class='nav-dropdown'" in html
    assert "href='/timesheet'" in html and "href='/activities'" in html
    # the active page's group is highlighted
    assert "nav-group active-group" in html
    # old flat links must be gone (no bare top-level /report link outside dropdown)
    assert html.count("<nav class='topnav'") == 1


def test_review_link_keeps_day_param():
    html = layout("Dashboard", "<p>Hello</p>", day="2026-10-13", active="review")
    assert "href='/activities?day=2026-10-13'" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "grouped_nav or day_param"`
Expected: FAIL — nav is still flat.

- [ ] **Step 3: Replace the nav-building block in `layout()`**

Replace:
```python
    # ── Build nav with icon + label ────────────────────────────────
    links = []
    for nav_item in NAV_LINKS:
        key, label, href, icon = nav_item
        url = href
        if key == "review" and day:
            url = href + "?day=" + day
        cls = " class='active'" if key == active else ""
        svg = ("<svg width='15' height='15' aria-hidden='true'>"
               "<use href='/static/icons.svg#icon-%s'/></svg>" % icon)
        links.append("<a href='%s'%s>%s%s</a>" % (url, cls, svg, label))
```
with:
```python
    # ── Build grouped nav with dropdowns (UX redesign v2) ─────────
    groups = []
    for gkey, glabel, ghref, members in NAV_GROUPS:
        drop_links = []
        for mkey in members:
            mlabel, mhref, micon = _NAV_BY_KEY[mkey]
            url = mhref
            if mkey == "review" and day:
                url = mhref + "?day=" + day
            acls = " class='active'" if mkey == active else ""
            svg = ("<svg width='15' height='15' aria-hidden='true'>"
                   "<use href='/static/icons.svg#icon-%s'/></svg>" % micon)
            drop_links.append(
                "<a href='%s'%s>%s%s</a>" % (url, acls, svg, mlabel))
        gcls = " nav-group active-group" if active in members else " nav-group"
        groups.append(
            "<div class='%s'>"
            "<a class='nav-group-label' href='%s'>%s</a>"
            "<button class='nav-caret' aria-label='Open %s menu' aria-expanded='false'>"
            "<svg width='10' height='10' aria-hidden='true'>"
            "<use href='/static/icons.svg#icon-chevron-down'/></svg></button>"
            "<div class='nav-dropdown'>%s</div>"
            "</div>" % (gcls.strip(), ghref, glabel, glabel, "".join(drop_links)))
```
and change the `nav = ...` line to use `"".join(groups)` instead of `"".join(links)`.

Note: this requires an `icon-chevron-down` symbol in `dashboard/static/icons.svg`. If it does not exist, add it next to the other symbols:
```svg
<symbol id="icon-chevron-down" viewBox="0 0 10 10"><path d="M1 3l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></symbol>
```
Check the existing symbols' format first and match it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py tests/test_ui_ux_v15.py -q`
Expected: all pass (the old `test_nav_links_have_icons` still passes because `NAV_LINKS` is unchanged).

- [ ] **Step 5: Commit**

```bash
cd ~/workspace/focus-core-v1.15 && git add dashboard/app.py dashboard/static/icons.svg tests/test_ux_redesign_v2.py && git commit -m "ux-v2: grouped top navigation with dropdowns"
```

### Task 5: Dropdown styles (replaces old nav CSS)

**Files:**
- Modify: `dashboard/static/style.css` (REPLACE the old `/* NAVIGATION */` block — do not append)
- Modify: `dashboard/static/nav.js` (guard the old sliding-indicator `initNav`)
- Test: `tests/test_ux_redesign_v2.py`

Why replace instead of append: the old `.topnav` rules survive on any property the new
block doesn't reset — notably `overflow-x: auto`, which clips absolutely-positioned
dropdowns entirely, and `.topnav a` (0,1,1) which beats `.nav-group-label` (0,1,0).
Appending cannot fix this; the old flat-nav rules must go. Keep `.theme-toggle`
and `.text-size-form` (still used). Also guard `initNav()` in `nav.js`: return early
when `.nav-group` exists, so the old sliding indicator never positions itself on
hidden dropdown links.

- [ ] **Step 1: Write the failing test**

```python
def test_nav_dropdown_styles_use_tokens():
    css = (pathlib.Path(__file__).resolve().parent.parent
           / "dashboard" / "static" / "style.css").read_text()
    # slice the new nav section so token assertions are tied to it, not the whole file
    section = css.split("UX v2: grouped")[1]
    for sel in [".nav-group", ".nav-group-label", ".nav-dropdown",
                ".nav-group.open .nav-dropdown",
                ".nav-group:hover .nav-dropdown",
                ".nav-group:focus-within .nav-dropdown",
                ".nav-caret:hover",
                ".nav-group:last-child .nav-dropdown"]:
        assert sel in section, f"grouped nav section missing {sel}"
    for token in ["var(--surface)", "var(--shadow-lift)", "var(--gold-deep)",
                  "var(--radius-card)"]:
        assert token in section, f"grouped nav section missing {token}"
    assert "overflow: visible" in section
```
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "dropdown_styles"`
Expected: FAIL.

- [ ] **Step 3: Replace the old nav block in `style.css`**

Delete the old `/* NAVIGATION */` block (`.topnav`, `.topnav::-webkit-scrollbar`,
`.topnav a`, `.topnav a svg`, `.topnav a:hover`, `.topnav a.active`, and the
`.nav-indicator` block) and put this in its place. Keep `.theme-toggle` and
`.text-size-form` untouched.

```css
/* ═══════════════════════════ NAVIGATION (UX v2: grouped) ═════════════════════════════ */
.topnav { display: flex; align-items: center; gap: var(--sp-1);
  background: var(--surface); border-bottom: 1px solid var(--line);
  padding: 0 var(--sp-3); position: sticky; top: 0; z-index: 100;
  overflow: visible; }
.nav-group { position: relative; }
.topnav .nav-group-label { display: inline-block; padding: var(--sp-2) var(--sp-1);
  color: var(--ink); text-decoration: none; font-weight: 600; }
.nav-group.active-group .nav-group-label { color: var(--gold-deep); }
.nav-caret { background: none; border: none; cursor: pointer;
  color: var(--muted); padding: var(--sp-1); }
.nav-caret:hover { background: none; box-shadow: none; color: var(--ink); }
.nav-dropdown { display: none; position: absolute; top: 100%; left: 0;
  min-width: 220px; background: var(--surface);
  border: 1px solid var(--line); border-radius: var(--radius-card);
  box-shadow: var(--shadow-lift); padding: var(--sp-1); z-index: 50; }
.nav-group:last-child .nav-dropdown { left: auto; right: 0; }
.nav-dropdown a { display: flex; align-items: center; gap: var(--sp-1);
  padding: var(--sp-1) var(--sp-2); border-radius: var(--radius-input);
  color: var(--ink); text-decoration: none; }
.nav-dropdown a:hover { background: var(--bg); }
.nav-dropdown a.active { color: var(--gold-deep); font-weight: 700; }
.nav-group:hover .nav-dropdown,
.nav-group:focus-within .nav-dropdown,
.nav-group.open .nav-dropdown { display: block; }
```

Also in `dashboard/static/nav.js`, guard the old sliding indicator at the top of
`initNav()` so it never runs on the grouped nav:
```js
if (nav.querySelector('.nav-group')) return; // UX v2 grouped nav: no sliding indicator
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "dropdown_styles"`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
cd ~/workspace/focus-core-v1.15 && git add dashboard/static/style.css dashboard/static/nav.js tests/test_ux_redesign_v2.py && git commit -m "ux-v2: grouped nav dropdown styles"
```

### Task 6: Dropdown toggle behavior in `nav.js`

**Files:**
- Modify: `dashboard/static/nav.js`
- Test: manual + Playwright render check (JS behavior; assert the handlers exist via file content)

- [ ] **Step 1: Write the failing test**

```python
def test_nav_js_has_dropdown_behavior():
    js = (pathlib.Path(__file__).resolve().parent.parent
          / "dashboard" / "static" / "nav.js").read_text()
    assert "nav-caret" in js and "Escape" in js
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "dropdown_behavior"`
Expected: FAIL.

- [ ] **Step 3: Append to `dashboard/static/nav.js`**

```js
// UX v2: grouped nav dropdowns — caret toggles .open (touch), Escape and
// outside clicks close. Hover/keyboard are handled by CSS :hover/:focus-within.
(function () {
  var groups = document.querySelectorAll('.nav-group');
  function closeAll() {
    groups.forEach(function (g) {
      g.classList.remove('open');
      var c = g.querySelector('.nav-caret');
      if (c) c.setAttribute('aria-expanded', 'false');
    });
  }
  groups.forEach(function (g) {
    var caret = g.querySelector('.nav-caret');
    if (!caret) return;
    caret.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      var wasOpen = g.classList.contains('open');
      closeAll();
      if (!wasOpen) {
        g.classList.add('open');
        caret.setAttribute('aria-expanded', 'true');
      }
    });
  });
  document.addEventListener('click', function (e) {
    if (!e.target.closest('.nav-group')) closeAll();
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closeAll();
  });
})();
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
cd ~/workspace/focus-core-v1.15 && git add dashboard/static/nav.js tests/test_ux_redesign_v2.py && git commit -m "ux-v2: nav dropdown toggle behavior"
```

## Phase 3 — Component kit

Each task appends a marked `/* UX v2: <component> */` section to `dashboard/static/style.css`, using only `var(--token)` values. Existing selectors keep working; these are additive restyles.

**SPECIFICITY RULE (learned Tasks 7 & 9):** old rules use element+attribute selectors
(`input[type=text]` = 0,1,1; `.topnav a` = 0,1,1) that BEAT plain new classes (0,1,0).
"Appended later wins" only applies on ties. Every new component selector must be scoped
to tie-or-beat the old rule it replaces (e.g. `input.input-v2`, `.btn.btn-sm`,
`.topnav .nav-group-label`), and the test should assert the FULL selector string,
not just a substring. The quality reviewer must check this on every component task.

### Task 7: Buttons

**Files:**
- Modify: `dashboard/static/style.css`
- Test: `tests/test_ux_redesign_v2.py`

- [ ] **Step 1: Write the failing test**

```python
def test_button_styles_use_tokens():
    css = (pathlib.Path(__file__).resolve().parent.parent
           / "dashboard" / "static" / "style.css").read_text()
    assert ".btn-primary" in css and "var(--gold)" in css
    assert ".btn-secondary" in css and ".btn-danger" in css
    assert "var(--radius-pill)" in css and "min-height: 44px" in css
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "button_styles"`
Expected: FAIL.

- [ ] **Step 3: Append the button section**

```css
/* UX v2: buttons */
.btn { display: inline-flex; align-items: center; justify-content: center;
  gap: var(--sp-1); min-height: 44px; padding: 0 var(--sp-3);
  border-radius: var(--radius-pill); font-weight: 600; font-size: var(--fs-body);
  cursor: pointer; text-decoration: none; border: 1px solid transparent; }
.btn-primary { background: var(--gold); color: var(--ink);
  box-shadow: var(--shadow-card); }
.btn-primary:hover { background: var(--gold-deep); color: var(--ink);
  box-shadow: var(--shadow-lift); }
/* Note: dark ink text on gold (not white) — white-on-gold fails WCAG AA contrast. */
.btn-secondary { background: transparent; color: var(--ink);
  border-color: var(--line); }
.btn-secondary:hover { border-color: var(--gold); color: var(--gold-deep); }
.btn-danger { background: transparent; color: var(--danger);
  border-color: var(--danger); }
.btn-danger:hover { background: var(--danger); color: #fff; }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "button_styles"`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
cd ~/workspace/focus-core-v1.15 && git add dashboard/static/style.css tests/test_ux_redesign_v2.py && git commit -m "ux-v2: button component styles"
```

### Task 8: Cards

**Files:**
- Modify: `dashboard/static/style.css`
- Test: `tests/test_ux_redesign_v2.py`

- [ ] **Step 1: Write the failing test**

```python
def test_card_styles_use_tokens():
    css = (pathlib.Path(__file__).resolve().parent.parent
           / "dashboard" / "static" / "style.css").read_text()
    assert "/* UX v2: cards */" in css
    assert "var(--radius-card)" in css and "var(--shadow-card)" in css
    assert ".stat-card" in css and ".stat-num" in css
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "card_styles"`
Expected: FAIL.

- [ ] **Step 3: Append the card section**

```css
/* UX v2: cards */
.card { background: var(--surface); border: 1px solid var(--line);
  border-radius: var(--radius-card); box-shadow: var(--shadow-card);
  padding: var(--sp-3); }
.stat-card { background: var(--surface); border: 1px solid var(--line);
  border-radius: var(--radius-card); box-shadow: var(--shadow-card);
  padding: var(--sp-3); }
.stat-num { font-size: var(--fs-h1); line-height: var(--lh-h1);
  font-weight: 700; color: var(--ink); }
.stat-label { font-size: var(--fs-small); color: var(--muted); }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "card_styles"`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
cd ~/workspace/focus-core-v1.15 && git add dashboard/static/style.css tests/test_ux_redesign_v2.py && git commit -m "ux-v2: card component styles"
```

### Task 9: Inputs

**Files:**
- Modify: `dashboard/static/style.css`
- Test: `tests/test_ux_redesign_v2.py`

- [ ] **Step 1: Write the failing test**

```python
def test_input_styles_use_tokens():
    css = (pathlib.Path(__file__).resolve().parent.parent
           / "dashboard" / "static" / "style.css").read_text()
    assert "/* UX v2: inputs */" in css
    assert "var(--radius-input)" in css
    # gold focus ring on all text-like inputs
    assert "var(--gold)" in css and ":focus" in css
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "input_styles"`
Expected: FAIL.

- [ ] **Step 3: Append the input section**

```css
/* UX v2: inputs */
input[type='text'], input[type='number'], input[type='date'],
input[type='time'], input[type='datetime-local'], select, textarea {
  background: var(--surface); color: var(--ink);
  border: 1px solid var(--line); border-radius: var(--radius-input);
  padding: var(--sp-1) var(--sp-2); font-size: var(--fs-body);
  min-height: 44px; }
input:focus, select:focus, textarea:focus {
  outline: none; border-color: var(--gold);
  box-shadow: 0 0 0 3px rgba(201, 162, 39, 0.25); }
label { font-weight: 600; color: var(--ink); }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "input_styles"`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
cd ~/workspace/focus-core-v1.15 && git add dashboard/static/style.css tests/test_ux_redesign_v2.py && git commit -m "ux-v2: input component styles"
```

### Task 10: Empty states

**Files:**
- Modify: `dashboard/static/style.css`
- Test: `tests/test_ux_redesign_v2.py`

- [ ] **Step 1: Write the failing test**

```python
def test_empty_state_styles():
    css = (pathlib.Path(__file__).resolve().parent.parent
           / "dashboard" / "static" / "style.css").read_text()
    assert ".empty-state" in css
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "empty_state"`
Expected: FAIL.

- [ ] **Step 3: Append the empty-state section**

```css
/* UX v2: empty states */
.empty-state { text-align: center; padding: var(--sp-8) var(--sp-3);
  color: var(--muted); }
.empty-state p { font-size: var(--fs-body); line-height: var(--lh-body);
  margin: 0 0 var(--sp-3); }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "empty_state"`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
cd ~/workspace/focus-core-v1.15 && git add dashboard/static/style.css tests/test_ux_redesign_v2.py && git commit -m "ux-v2: empty-state styles"
```

### Task 11: Type scale

**Files:**
- Modify: `dashboard/static/style.css`
- Test: `tests/test_ux_redesign_v2.py`

- [ ] **Step 1: Write the failing test**

```python
def test_type_scale_uses_tokens():
    css = (pathlib.Path(__file__).resolve().parent.parent
           / "dashboard" / "static" / "style.css").read_text()
    assert "/* UX v2: type scale */" in css
    for var in ["var(--fs-display)", "var(--fs-h1)", "var(--fs-h2)",
                "var(--fs-h3)", "var(--lh-body)", "var(--fs-small)"]:
        assert var in css, f"style.css never uses {var}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "type_scale"`
Expected: FAIL.

- [ ] **Step 3: Append the type section**

```css
/* UX v2: type scale */
.page-title { font-size: var(--fs-h1); line-height: var(--lh-h1);
  letter-spacing: -0.01em; color: var(--ink); }
h2 { font-size: var(--fs-h2); line-height: var(--lh-h2); color: var(--ink); }
h3 { font-size: var(--fs-h3); line-height: var(--lh-h3); color: var(--ink); }
body { font-size: var(--fs-body); line-height: var(--lh-body); color: var(--ink);
  background: var(--bg); }
.small, .note { font-size: var(--fs-small); line-height: var(--lh-small);
  color: var(--muted); }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q`
Expected: all pass so far.

- [ ] **Step 5: Commit**

```bash
cd ~/workspace/focus-core-v1.15 && git add dashboard/static/style.css tests/test_ux_redesign_v2.py && git commit -m "ux-v2: type scale styles"
```

## Phase 4 — Daily screens (flagship treatment)

### Task 12: Home — hero, stat cards, Deep Focus progress ring

**Files:**
- Modify: `dashboard/routes/core.py` (`index()`, ~line 37)
- Modify: `dashboard/static/style.css`
- Test: `tests/test_ux_redesign_v2.py`

- [ ] **Step 1: Write the failing test**

```python
def test_home_uses_stat_cards_and_ring(tmp_path, monkeypatch):
    from focuscore import store
    db = str(tmp_path / "home.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)
    dash_app.app.config["TESTING"] = True
    client = dash_app.app.test_client()
    res = client.get("/")
    html = res.get_data(as_text=True)
    assert res.status_code == 200
    # stat cards use the shared component class
    assert "stat-card" in html and "stat-num" in html
    # Deep Focus card renders a real SVG progress ring
    assert "progress-ring" in html and "<svg" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "home_uses"`
Expected: FAIL — home does not use those classes yet.

- [ ] **Step 3: Implement**
  1. In `dashboard/routes/core.py::index()`: wrap each stat block in `<div class='stat-card'><div class='stat-num'>…</div><div class='stat-label'>…</div></div>`. Keep every number and label identical — only the wrapping classes change.
  2. Replace the Deep Focus card's plain progress display with an SVG ring: `<svg class='progress-ring' viewBox='0 0 36 36'><circle cx='18' cy='18' r='15.9' class='ring-bg'/><circle cx='18' cy='18' r='15.9' class='ring-fg' stroke-dasharray='<PCT> 100'/></svg>` where `<PCT>` is the existing focus-progress percentage the card already computes.
  3. Append to `style.css`:
```css
/* UX v2: home */
.progress-ring { width: 120px; height: 120px; }
.progress-ring .ring-bg { fill: none; stroke: var(--line); stroke-width: 3; }
.progress-ring .ring-fg { fill: none; stroke: var(--gold); stroke-width: 3;
  stroke-linecap: round; transform: rotate(-90deg); transform-origin: center; }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "home_uses"`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
cd ~/workspace/focus-core-v1.15 && git add dashboard/routes/core.py dashboard/static/style.css tests/test_ux_redesign_v2.py && git commit -m "ux-v2: home stat cards and focus progress ring"
```

### Task 13: Day view — timeline rows and category colors

**Files:**
- Modify: `dashboard/routes/core.py` (`day_view()`, ~line 165)
- Modify: `dashboard/static/style.css`
- Test: `tests/test_ux_redesign_v2.py`

- [ ] **Step 1: Write the failing test**

```python
def test_day_view_timeline_uses_category_tokens(tmp_path, monkeypatch):
    from focuscore import store
    db = str(tmp_path / "day.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)
    dash_app.app.config["TESTING"] = True
    client = dash_app.app.test_client()
    res = client.get("/day/2026-10-13")
    html = res.get_data(as_text=True)
    assert res.status_code == 200
    assert "timeline-row" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "timeline"`
Expected: FAIL.

- [ ] **Step 3: Implement**
  1. In `day_view()`: give each activity row `class='timeline-row'` and a `data-category='<slug>'` attribute, where slug is the category name lowercased with spaces/`&` replaced by `-` (must match the `--cat-*` token slugs in `tokens.css`: `business`, `communication-scheduling`, `social-networking`, `design-composition`, `entertainment`, `news`, `software-development`, `reference-learning`, `shopping`, `utilities`, `uncategorized`). Custom sub-categories use their parent category's slug (spec rule).
  2. Append to `style.css`:
```css
/* UX v2: day timeline */
.timeline-row { display: flex; align-items: center; gap: var(--sp-2);
  padding: var(--sp-2); border-bottom: 1px solid var(--line);
  border-left: 4px solid var(--cat-uncategorized); }
.timeline-row[data-category='business'] { border-left-color: var(--cat-business); }
.timeline-row[data-category='communication-scheduling'] { border-left-color: var(--cat-communication-scheduling); }
.timeline-row[data-category='social-networking'] { border-left-color: var(--cat-social-networking); }
.timeline-row[data-category='design-composition'] { border-left-color: var(--cat-design-composition); }
.timeline-row[data-category='entertainment'] { border-left-color: var(--cat-entertainment); }
.timeline-row[data-category='news'] { border-left-color: var(--cat-news); }
.timeline-row[data-category='software-development'] { border-left-color: var(--cat-software-development); }
.timeline-row[data-category='reference-learning'] { border-left-color: var(--cat-reference-learning); }
.timeline-row[data-category='shopping'] { border-left-color: var(--cat-shopping); }
.timeline-row[data-category='utilities'] { border-left-color: var(--cat-utilities); }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "timeline"`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
cd ~/workspace/focus-core-v1.15 && git add dashboard/routes/core.py dashboard/static/style.css tests/test_ux_redesign_v2.py && git commit -m "ux-v2: day timeline rows with category colors"
```

### Task 14: Focus page — sentence builder and Begin ceremony

**Files:**
- Modify: `dashboard/routes/focus.py` (`focus_page()` ~line 628, `_fc_idle_page()` ~line 268)
- Modify: `dashboard/static/style.css`
- Test: `tests/test_ux_redesign_v2.py`

- [ ] **Step 1: Write the failing test**

```python
def test_focus_sentence_builder_classes(tmp_path, monkeypatch):
    from focuscore import store
    db = str(tmp_path / "focus.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)
    dash_app.app.config["TESTING"] = True
    client = dash_app.app.test_client()
    res = client.get("/focus")
    html = res.get_data(as_text=True)
    assert res.status_code == 200
    assert "focus-sentence" in html
    assert "btn btn-primary btn-begin" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "sentence_builder"`
Expected: FAIL.

- [ ] **Step 3: Implement**
  1. In `_fc_idle_page()`: wrap the sentence builder ("I want to focus for … min on …") in `<div class='focus-sentence'>`. Keep the exact wording and controls — only the wrapper changes.
  2. Give the "Begin session" submit the classes `btn btn-primary btn-begin`.
  3. Append to `style.css`:
```css
/* UX v2: focus */
.focus-sentence { font-size: var(--fs-h2); line-height: var(--lh-h2);
  text-align: center; padding: var(--sp-6) var(--sp-3); color: var(--ink); }
.focus-sentence select, .focus-sentence input { font-size: inherit; }
.btn-begin { min-height: 56px; padding: 0 var(--sp-8); font-size: var(--fs-h3);
  margin-top: var(--sp-4); }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q`
Expected: all pass so far.

- [ ] **Step 5: Commit**

```bash
cd ~/workspace/focus-core-v1.15 && git add dashboard/routes/focus.py dashboard/static/style.css tests/test_ux_redesign_v2.py && git commit -m "ux-v2: focus sentence builder and begin ceremony"
```

## Phase 5 — Money screens

### Task 15: Timesheet — human date/time inputs, fix wrapping

**Files:**
- Modify: `dashboard/routes/timesheet.py` (`timesheet_page()`, ~line 33)
- Modify: `dashboard/static/style.css`
- Test: `tests/test_ux_redesign_v2.py`

- [ ] **Step 1: Write the failing test**

```python
def test_timesheet_no_raw_datetime_values(tmp_path, monkeypatch):
    from focuscore import store
    db = str(tmp_path / "ts.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)
    dash_app.app.config["TESTING"] = True
    client = dash_app.app.test_client()
    res = client.get("/timesheet")
    html = res.get_data(as_text=True)
    assert res.status_code == 200
    # no raw ISO datetimes rendered as visible text/values like 2026-09-25T09:00
    import re
    assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", html), \
        "timesheet still renders raw ISO datetimes"
    assert "timesheet-form" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "raw_datetime"`
Expected: FAIL — the current form renders raw `datetime-local` values.

- [ ] **Step 3: Implement**
  1. In `timesheet_page()`: wrap the entry form in `<div class='timesheet-form'>`. Wherever a datetime is shown to the user, format it as `Oct 13, 2:30 PM` (use the existing `focuscore.format` helpers if present; otherwise `datetime.strftime("%b %-d, %-I:%M %p")` — on Windows `%-d` is unsupported, so use `"%b %d, %I:%M %p"` and strip the leading zero from day/hour in Python). Form *inputs* keep `type='datetime-local'` (the browser renders them human-friendly); only *displayed text* changes.
  2. Fix the sentence-form wrapping: put each field on its own flex row that wraps gracefully (`.timesheet-form .form-row { display:flex; flex-wrap:wrap; gap: var(--sp-2); align-items:center; }`).
  3. Append to `style.css`:
```css
/* UX v2: timesheet */
.timesheet-form .form-row { display: flex; flex-wrap: wrap;
  gap: var(--sp-2); align-items: center; margin-bottom: var(--sp-2); }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "raw_datetime"`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
cd ~/workspace/focus-core-v1.15 && git add dashboard/routes/timesheet.py dashboard/static/style.css tests/test_ux_redesign_v2.py && git commit -m "ux-v2: timesheet human dates and form layout"
```

### Task 16: Invoices — table density, totals, status pills

**Files:**
- Modify: `dashboard/routes/invoices.py` (`invoices_page()` ~line 23, `invoice_detail_page()` ~line 243)
- Modify: `dashboard/static/style.css`
- Test: `tests/test_ux_redesign_v2.py`

- [ ] **Step 1: Write the failing test**

```python
def test_invoice_status_pills(tmp_path, monkeypatch):
    from focuscore import store
    db = str(tmp_path / "inv.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)
    dash_app.app.config["TESTING"] = True
    client = dash_app.app.test_client()
    res = client.get("/invoices")
    html = res.get_data(as_text=True)
    assert res.status_code == 200
    assert "invoice-table" in html and "status-pill" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "status_pills"`
Expected: FAIL.

- [ ] **Step 3: Implement**
  1. In `invoices_page()` and `invoice_detail_page()`: give the main table `class='invoice-table'`; wrap each status word (paid/pending/overdue/draft) in `<span class='status-pill status-<word>'>`.
  2. Append to `style.css`:
```css
/* UX v2: invoices */
.invoice-table { width: 100%; border-collapse: collapse; }
.invoice-table th { text-align: left; font-size: var(--fs-small);
  color: var(--muted); padding: var(--sp-1) var(--sp-2);
  border-bottom: 2px solid var(--line); }
.invoice-table td { padding: var(--sp-2); border-bottom: 1px solid var(--line); }
.invoice-table tr.total-row td { font-weight: 700; font-size: var(--fs-h3);
  border-top: 2px solid var(--line); }
.status-pill { display: inline-block; padding: 2px var(--sp-2);
  border-radius: var(--radius-pill); font-size: var(--fs-small);
  font-weight: 600; }
.status-paid { background: rgba(77,124,77,.15); color: var(--ok); }
.status-pending { background: rgba(201,162,39,.15); color: var(--gold-deep); }
.status-overdue { background: rgba(181,68,60,.12); color: var(--danger); }
.status-draft { background: var(--bg); color: var(--muted); }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "status_pills"`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
cd ~/workspace/focus-core-v1.15 && git add dashboard/routes/invoices.py dashboard/static/style.css tests/test_ux_redesign_v2.py && git commit -m "ux-v2: invoice table density and status pills"
```

## Phase 6 — Insight screens

### Task 17: Shared chart palette (category colors in SVGs)

**Files:**
- Modify: `dashboard/static/tokens.css` (dark-mode category overrides)
- Test: `tests/test_ux_redesign_v2.py`

**CORRECTION (found during implementation, 2026-10-13):** the plan originally asked to
convert `_svg_donut()`/`_svg_heatmap()`/`_svg_sparkline()` in
`dashboard/routes/system_intelligence.py` to `--cat-*` tokens. That premise was false:
those helpers render **score buckets** (`deep`/`shallow`/`neutral`/`distraction`),
**switch-count intensity gradients**, and **score trends** — zero occurrences of
"category" in the file (verified). Painting non-category data with category colors
would violate the spec's own "each category always uses the same color" rule.
The SVG conversion is DROPPED. The real deliverable — brightened `--cat-*` overrides
for dark mode (deferred from Task 2; Task 13's timeline needed them) — is done below.

- [ ] **Step 1: Write the failing test**

```python
def test_dark_mode_has_category_overrides():
    css = (pathlib.Path(__file__).resolve().parent.parent
           / "dashboard" / "static" / "tokens.css").read_text()
    dark = css.split("[data-theme='dark']")[1]
    for slug in ["business", "entertainment", "software-development",
                 "utilities", "uncategorized"]:
        assert f"--cat-{slug}:" in dark, f"dark block missing --cat-{slug}"
```

(The plan's `test_svg_helpers_use_category_tokens` is intentionally NOT written —
see the CORRECTION above. Committing a test for a conversion that must not happen
would be dishonest.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "category_tokens"`
Expected: FAIL.

- [ ] **Step 3: Implement**
  Append brightened category variants to the `[data-theme='dark']` block in `dashboard/static/tokens.css` (dark backgrounds need brighter colors):
```css
  --cat-business: #66bb6a;
  --cat-communication-scheduling: #64b5f6;
  --cat-social-networking: #ba68c8;
  --cat-design-composition: #ffa726;
  --cat-entertainment: #ec407a;
  --cat-news: #4fc3f7;
  --cat-software-development: #a1887f;
  --cat-reference-learning: #4dd0e1;
  --cat-shopping: #ffd54f;
  --cat-utilities: #9e9e9e;
  --cat-uncategorized: #cfcfcf;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "category_tokens"`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
cd ~/workspace/focus-core-v1.15 && git add dashboard/routes/system_intelligence.py dashboard/static/tokens.css tests/test_ux_redesign_v2.py && git commit -m "ux-v2: charts use shared category color tokens"
```

### Task 18: Report page polish

**Files:**
- Modify: `dashboard/routes/system_reports.py` (`report_page()`, ~line 23)
- Modify: `dashboard/static/style.css`
- Test: `tests/test_ux_redesign_v2.py`

- [ ] **Step 1: Write the failing test**

```python
def test_report_uses_cards(tmp_path, monkeypatch):
    from focuscore import store
    db = str(tmp_path / "rep.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)
    dash_app.app.config["TESTING"] = True
    client = dash_app.app.test_client()
    res = client.get("/report")
    html = res.get_data(as_text=True)
    assert res.status_code == 200
    assert "class='card'" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "report_uses"`
Expected: FAIL (or pass already — if the report page already uses `card`, change the test to assert the chart-table `<details>` from `chart_table()` is present and keep the task as a no-op verification; do not invent work).

- [ ] **Step 3: Implement** — wrap each report section in `<div class='card'>`; keep the existing "View as table" `<details>` (accessibility rule). No new sections.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "report_uses"`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
cd ~/workspace/focus-core-v1.15 && git add dashboard/routes/system_reports.py tests/test_ux_redesign_v2.py && git commit -m "ux-v2: report page card polish"
```

### Task 19: Coaching page — keep the voice, polish the cards

**Files:**
- Modify: `dashboard/routes/system_reports.py` (`coaching_page()`, ~line 182)
- Test: `tests/test_ux_redesign_v2.py`

- [ ] **Step 1: Write the failing test**

```python
def test_coaching_keeps_plain_english(tmp_path, monkeypatch):
    from focuscore import store
    db = str(tmp_path / "coach.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)
    dash_app.app.config["TESTING"] = True
    client = dash_app.app.test_client()
    res = client.get("/coaching")
    html = res.get_data(as_text=True)
    assert res.status_code == 200
    assert "class='card'" in html or "coach-card" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "coaching_keeps"`
Expected: FAIL (adjust per Step 2 of Task 18 if already passing).

- [ ] **Step 3: Implement** — wrap coaching messages in the shared `.card`; do not change any wording (the warm conversational tone is a protected strength).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "coaching_keeps"`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
cd ~/workspace/focus-core-v1.15 && git add dashboard/routes/system_reports.py tests/test_ux_redesign_v2.py && git commit -m "ux-v2: coaching page card polish"
```

### Task 20: Goals page — premium progress visuals

**Files:**
- Modify: `dashboard/app.py` (`_goal_progress_html()`, ~line 232)
- Modify: `dashboard/routes/core.py` (`goals_page()`, ~line 270)
- Modify: `dashboard/static/style.css`
- Test: `tests/test_ux_redesign_v2.py`

- [ ] **Step 1: Write the failing test**

```python
def test_goal_rows_use_token_colors():
    from dashboard.app import _goal_progress_html
    ev = {"name": "Deep work", "pct": 50.0, "status": "on_track",
          "target_type": "time", "target_name": "Deep work",
          "direction": "more_than", "target": 120, "current": 60}
    html = _goal_progress_html(ev)
    assert "goal-row" in html
    assert "var(--ok)" in html or "var(--gold" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "goal_rows"`
Expected: FAIL — colors are hardcoded hex (`#2e7d32` etc.).

- [ ] **Step 3: Implement**
  1. In `_goal_progress_html()`: replace the hardcoded color map with tokens: `{"on_track": "var(--ok)", "achieved": "var(--ok)", "behind": "var(--gold)", "missed": "var(--danger)"}`.
  2. Append to `style.css`:
```css
/* UX v2: goals */
.goal-row { background: var(--surface); border: 1px solid var(--line);
  border-radius: var(--radius-card); padding: var(--sp-2) var(--sp-3);
  margin-bottom: var(--sp-2); box-shadow: var(--shadow-card); }
.goal-row .progress { height: 10px; border-radius: var(--radius-pill);
  background: var(--bg); overflow: hidden; }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "goal_rows"`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
cd ~/workspace/focus-core-v1.15 && git add dashboard/app.py dashboard/static/style.css tests/test_ux_redesign_v2.py && git commit -m "ux-v2: goals page premium progress visuals"
```

### Task 21: Review (activities) page polish

**Files:**
- Modify: `dashboard/routes/core.py` (`activities()`, ~line 172)
- Test: `tests/test_ux_redesign_v2.py`

- [ ] **Step 1: Write the failing test**

```python
def test_review_page_uses_cards(tmp_path, monkeypatch):
    from focuscore import store
    db = str(tmp_path / "rev.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)
    dash_app.app.config["TESTING"] = True
    client = dash_app.app.test_client()
    res = client.get("/activities")
    html = res.get_data(as_text=True)
    assert res.status_code == 200
    assert "class='card'" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "review_page"`
Expected: FAIL (adjust per Task 18 note if already passing).

- [ ] **Step 3: Implement** — wrap the review summary sections in `.card`; reuse `.timeline-row` + `data-category` from Task 13 for the activity list so Review and Day share one visual language.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q`
Expected: all pass so far.

- [ ] **Step 5: Commit**

```bash
cd ~/workspace/focus-core-v1.15 && git add dashboard/routes/core.py tests/test_ux_redesign_v2.py && git commit -m "ux-v2: review page card and timeline polish"
```

## Phase 7 — System screens

### Task 22: Shield page — calm toggles and status

**Files:**
- Modify: `dashboard/routes/system_shield.py` (`shield_page()`, ~line 18)
- Modify: `dashboard/static/style.css`
- Test: `tests/test_ux_redesign_v2.py`

- [ ] **Step 1: Write the failing test**

```python
def test_shield_uses_cards(tmp_path, monkeypatch):
    from focuscore import store
    db = str(tmp_path / "shield.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)
    dash_app.app.config["TESTING"] = True
    client = dash_app.app.test_client()
    res = client.get("/shield")
    html = res.get_data(as_text=True)
    assert res.status_code == 200
    assert "class='card'" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "shield_uses"`
Expected: FAIL (adjust per Task 18 note if already passing).

- [ ] **Step 3: Implement** — wrap shield rule rows and status blocks in `.card`; keep every toggle, label, and behavior identical. Append to `style.css`:
```css
/* UX v2: shield */
.toggle-row { display: flex; align-items: center; justify-content: space-between;
  gap: var(--sp-2); padding: var(--sp-2) 0; border-bottom: 1px solid var(--line); }
.toggle-row:last-child { border-bottom: none; }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "shield_uses"`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
cd ~/workspace/focus-core-v1.15 && git add dashboard/routes/system_shield.py dashboard/static/style.css tests/test_ux_redesign_v2.py && git commit -m "ux-v2: shield page calm toggles"
```

### Task 23: Alerts page — polished list

**Files:**
- Modify: `dashboard/routes/core.py` (`alerts_page()`, ~line 422)
- Test: `tests/test_ux_redesign_v2.py`

- [ ] **Step 1: Write the failing test**

```python
def test_alerts_page_uses_cards(tmp_path, monkeypatch):
    from focuscore import store
    db = str(tmp_path / "al.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)
    dash_app.app.config["TESTING"] = True
    client = dash_app.app.test_client()
    res = client.get("/alerts")
    html = res.get_data(as_text=True)
    assert res.status_code == 200
    assert "alert-row" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "alerts_page"`
Expected: FAIL.

- [ ] **Step 3: Implement** — give each alert row `class='alert-row'` (keep the toggle/delete forms and the "No alerts yet" empty state exactly as-is; the empty state may use `.empty-state` from Task 10). Append to `style.css`:
```css
/* UX v2: alerts */
.alert-row { display: flex; align-items: center; gap: var(--sp-2);
  background: var(--surface); border: 1px solid var(--line);
  border-radius: var(--radius-card); padding: var(--sp-2) var(--sp-3);
  margin-bottom: var(--sp-2); }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "alerts_page"`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
cd ~/workspace/focus-core-v1.15 && git add dashboard/routes/core.py dashboard/static/style.css tests/test_ux_redesign_v2.py && git commit -m "ux-v2: alerts page polished list"
```

### Task 24: Backup page — trustworthy cards

**Files:**
- Modify: `dashboard/routes/system_backup.py` (`backup_page()` ~line 137, card helpers `_retention_card_html()`, `_erase_all_card_html()`, `_backup_encryption_card_html()`)
- Test: `tests/test_ux_redesign_v2.py`

- [ ] **Step 1: Write the failing test**

```python
def test_backup_page_uses_cards(tmp_path, monkeypatch):
    from focuscore import store
    db = str(tmp_path / "bak.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)
    dash_app.app.config["TESTING"] = True
    client = dash_app.app.test_client()
    res = client.get("/backup")
    html = res.get_data(as_text=True)
    assert res.status_code == 200
    assert "class='card'" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "backup_page"`
Expected: FAIL (adjust per Task 18 note if already passing).

- [ ] **Step 3: Implement** — wrap the retention, encryption, and erase cards in `.card`; keep every warning word identical (the careful tone is protected — this page handles the user's data like money). The erase-all card keeps its danger styling via `.btn-danger` (Task 7).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q`
Expected: all pass so far.

- [ ] **Step 5: Commit**

```bash
cd ~/workspace/focus-core-v1.15 && git add dashboard/routes/system_backup.py tests/test_ux_redesign_v2.py && git commit -m "ux-v2: backup page trustworthy cards"
```

## Phase 8 — Welcome / onboarding

### Task 25: Welcome page polish

**Files:**
- Modify: `dashboard/routes/core.py` (`welcome()`, ~line 52)
- Modify: `dashboard/static/style.css`
- Test: `tests/test_ux_redesign_v2.py`

- [ ] **Step 1: Write the failing test**

```python
def test_welcome_uses_type_scale(tmp_path, monkeypatch):
    from focuscore import store
    db = str(tmp_path / "wel.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)
    dash_app.app.config["TESTING"] = True
    client = dash_app.app.test_client()
    res = client.get("/welcome")
    html = res.get_data(as_text=True)
    assert res.status_code == 200
    assert "welcome-hero" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "welcome_uses"`
Expected: FAIL.

- [ ] **Step 3: Implement**
  1. In `welcome()`: wrap the hero in `<div class='welcome-hero'>`; keep the "Day one" copy exactly.
  2. Append to `style.css`:
```css
/* UX v2: welcome */
.welcome-hero { text-align: center; padding: var(--sp-8) var(--sp-3); }
.welcome-hero h1 { font-size: var(--fs-display); line-height: var(--lh-display);
  letter-spacing: -0.02em; }
.welcome-steps { display: grid; gap: var(--sp-3); max-width: 640px;
  margin: var(--sp-6) auto; }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/test_ux_redesign_v2.py -q -k "welcome_uses"`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
cd ~/workspace/focus-core-v1.15 && git add dashboard/routes/core.py dashboard/static/style.css tests/test_ux_redesign_v2.py && git commit -m "ux-v2: welcome page polish"
```

## Phase 9 — Verification

### Task 26: Full test suite green

**Files:** (none — verification only)

- [ ] **Step 1: Clear `/tmp` and run the full suite under `TZ=Asia/Karachi`**

Run:
```bash
du -sh /tmp && cd ~/workspace/focus-core-v1.15 && TZ=Asia/Karachi ~/workspace/venvs/fc-venv/bin/pytest tests/ -q -x
```
Expected: all pass. Per standing rules: check `/tmp` first (512M tmpfs fills cause fake sqlite failures), run under `TZ=Asia/Karachi`, never weaken a test to get green. If a visual change broke an old test, fix the implementation — or if the old test asserted exact old HTML that the spec intentionally changed, update the test to the new spec and note why in the commit message.

- [ ] **Step 2: Run lint and type checks**

Run: `cd ~/workspace/focus-core-v1.15 && ~/workspace/venvs/fc-venv/bin/ruff check . && ~/workspace/venvs/fc-venv/bin/mypy focuscore dashboard 2>&1 | tail -3`
Expected: ruff clean; mypy shows no new errors (compare against the pre-plan baseline).

### Task 27: Render-and-inspect every screen

**Files:** (none — verification only)

Use the existing local Playwright setup (the same approach as the 2026-10-13 visual review): start the Flask app with an isolated demo home, seed demo activity, screenshot each page at 1440×900, and visually inspect before calling anything done.

- [ ] **Step 1: Render all screens**

```bash
# 1. Confirm the port is free first (stale servers lie about renders)
ss -tlnp | grep 5000 || echo "port 5000 free"
# 2. Start the app with an isolated demo home (see the 2026-10-13 review notes
#    for the seed script pattern), screenshot each route:
#    /, /day/<today>, /focus, /timesheet, /invoices, /report, /coaching,
#    /intelligence, /goals, /activities, /shield, /alerts, /backup, /welcome
# 3. Kill the server afterwards and confirm the port is clean again.
```

- [ ] **Step 2: Inspect every screenshot** against this checklist:
  - Nav shows 4 group labels; dropdowns open on hover/click; Focus label → `/focus` in one click.
  - Cards have visible depth (border + soft shadow), not flat.
  - Body text uses the 16/26 scale; headlines use the display scale.
  - No raw ISO datetimes anywhere (timesheet especially).
  - Category colors consistent between Day timeline and charts.
  - Dark mode: toggle to dark, re-screenshot Home and Day — tokens remapped, text readable.
  - No emoji in the UI; icons are SVG.

- [ ] **Step 3: Fix and re-render** anything that fails the checklist, then commit fixes.

---

## Self-review

1. **Spec coverage:** §3 tokens → Task 2 (+ dark). §4 nav → Tasks 3–6 (4 groups, dropdowns, label-links, Focus one click). §5 screens → Tasks 12–25 (Home, Day, Focus, Timesheet, Invoices, Report, Coaching, Deep time charts, Goals, Review, Shield, Alerts, Backup, Welcome). §6 components → Tasks 7–11 (buttons, cards, inputs, empty states, type/color). §7 constraints → Task 26 gates + no new frameworks/routes anywhere. §8 verification → Tasks 26–27. §9 build order → phase order matches. No spec section left without tasks.
2. **Placeholder scan:** no TBD/TODO/"similar to Task N"; every code step shows the code; test commands show exact paths and expected output.
3. **Type consistency:** `NAV_GROUPS` tuples are `(group_key, group_label, primary_href, [member keys])` everywhere; `_NAV_BY_KEY` maps key → `(label, href, icon)`; category slugs use one rule (lowercase, spaces/`&` → `-`) in Tasks 2, 13, 17; test file is `tests/test_ux_redesign_v2.py` throughout; venv path `~/workspace/venvs/fc-venv/bin/` throughout.

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-10-13-ux-redesign-v2.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
