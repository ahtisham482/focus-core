"""Roadmap 1.15 -- accessibility quick wins, pinned.

Audit + gap-close, not greenfield. Each test names the win it pins:

1. :focus-visible -- a global rule exists AND the two shipped
   ``outline: none`` overrides on interactive selectors (base form
   fields in style.css, .lv-label-row inputs in living.css) restore a
   visible outline for keyboard focus.
2. Escape -- zen-mode exits on Escape (focus.js + Focus page script,
   pre-existing) and open <details> disclosures close on Escape
   (nav.js, added in 1.15; the app ships no menus/drawers/modals).
3. aria-labels -- no rendered <button>/<a> has an empty accessible
   name (text + aria-label + title), including data-dependent rows.
4. Labels -- every non-hidden, non-submit/button field on served
   pages has an associated label: <label for=> matching its id, or a
   wrapping <label>. (aria-label alone does NOT count here; fields
   that relied on it gained visually-hidden <label for=> in 1.15.)
5. prefers-reduced-motion -- both stylesheets collapse shipped
   transitions/animations under reduce, smooth scrolling is off, and
   nav.js consults matchMedia before animating.
6. Contrast -- WCAG ratios computed from the shipped CSS custom
   properties for the named semantic pairs (ink / muted / accent as
   text / button text) on bg / bg-raised / bg-sunken, light + dark,
   for both style.css and living.css tokens. All >= 4.5:1.
   1.15 repair: token resolution models the real cascade (:root
   merged under each theme block, var() chains resolved), and the
   covered pairs extend to the status badges, invoice pills,
   step indicator, tired-state notes, week filters, living --ink3
   meta text and the living break-phase state text.

Seeding mirrors tests/test_ui_ux_v15.py (tmp DB only; the repo
focuscore.db is never touched).
"""

import re
import sqlite3
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

import pytest

import dashboard.app as dash_app
from focuscore import store

ROOT = Path(__file__).resolve().parent.parent
STYLE_CSS = (ROOT / "dashboard/static/style.css").read_text(encoding="utf-8")
LIVING_CSS = (ROOT / "dashboard/static/living.css").read_text(encoding="utf-8")
NAV_JS = (ROOT / "dashboard/static/nav.js").read_text(encoding="utf-8")
FOCUS_JS = (ROOT / "dashboard/static/focus.js").read_text(encoding="utf-8")
FOCUS_PY = (ROOT / "dashboard/routes/focus.py").read_text(encoding="utf-8")
APP_PY = (ROOT / "dashboard/app.py").read_text(encoding="utf-8")

SEEDED_DAY = "2026-09-29"


# ------------------------------------------------------------- seeding ---

@pytest.fixture()
def seeded_client(tmp_path, monkeypatch):
    """Test client over a tmp DB seeded with data-dependent rows."""
    db = str(tmp_path / "a11y.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)

    pid = store.add_project("Acme", client="Acme Corp", path=db)
    eid = store.create_entry(
        SEEDED_DAY, SEEDED_DAY + "T09:00", SEEDED_DAY + "T10:00", 60.0,
        "Work", project_id=pid, task="Dev", status="accepted", path=db)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE timesheet_entries SET hourly_rate_minor = 10000, "
            "rate_currency = 'USD', rate_status = 'confirmed' WHERE id = ?",
            (eid,))
        conn.commit()
    finally:
        conn.close()
    from focuscore import invoices as inv_mod
    inv_id = inv_mod.create_invoice(pid, SEEDED_DAY, SEEDED_DAY, path=db)
    store.add_goal("Ship it", "at_least", "activity", target_name="Dev",
                   threshold_minutes=60, path=db)
    store.add_alert("Watch Dev", "activity", "Dev", 30, path=db)
    store.create_block_rule("Block X", "app", "x.exe", "firm", path=db)
    store.save_events(SEEDED_DAY, [{
        "app": "Code", "title": "main.py", "duration": 600, "score": 2,
    }], path=db)

    flag = tmp_path / ".onboarded"
    flag.write_text("2026-09-29")
    monkeypatch.setattr(dash_app, "ONBOARDED_FLAG", flag)
    monkeypatch.setattr(dash_app, "run_day", lambda *a, **kw: None)
    dash_app.app.config["TESTING"] = True
    client = dash_app.app.test_client()
    return client, inv_id


SEEDED_ROUTES = [
    "/",
    "/day/" + SEEDED_DAY,
    "/activities?day=" + SEEDED_DAY,
    "/goals",
    "/alerts",
    "/focus",
    "/timesheet?day=" + SEEDED_DAY,
    "/invoices",
    "/invoices/new",
    "/report",
    "/coaching",
    "/intelligence",
    "/shield",
    "/backup",
    "/update",
    "/help",
    "/welcome",
    "/setup/activitywatch",
]


# ------------------------------------------------------------ parsing ---

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
         "link", "meta", "param", "source", "track", "wbr"}


class _Page(HTMLParser):
    """Collects buttons, links, fields, labels, and every id."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.buttons = []
        self.links = []
        self.fields = []
        self.labels_for = set()
        self.labels = []  # {"for": str | None, "text": str}
        self.ids = []
        self._open = None  # (list, record) currently collecting text
        self._open_label = None

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if d.get("id"):
            self.ids.append(d["id"])
        if tag == "label":
            rec = {"for": d.get("for"), "text": ""}
            self.labels.append(rec)
            self._open_label = rec
            if "for" in d:
                self.labels_for.add(d["for"])
        if tag == "button":
            rec = {"attrs": d, "text": ""}
            self.buttons.append(rec)
            self._open = rec
        elif tag == "a":
            rec = {"attrs": d, "text": ""}
            self.links.append(rec)
            self._open = rec
        if tag in ("input", "select", "textarea"):
            self.fields.append({
                "tag": tag, "attrs": d,
                "wrapped": "label" in self.stack,
            })
        if tag not in _VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        d = dict(attrs)
        if d.get("id"):
            self.ids.append(d["id"])
        if tag == "label" and "for" in d:
            self.labels_for.add(d["for"])
        if tag in ("input", "select", "textarea"):
            self.fields.append({
                "tag": tag, "attrs": d,
                "wrapped": "label" in self.stack,
            })

    def handle_endtag(self, tag):
        if tag in ("button", "a"):
            self._open = None
        if tag == "label":
            self._open_label = None
        if tag in self.stack:
            while self.stack and self.stack[-1] != tag:
                self.stack.pop()
            if self.stack:
                self.stack.pop()

    def handle_data(self, data):
        if self._open is not None:
            self._open["text"] += data
        if self._open_label is not None:
            self._open_label["text"] += data


def _parse(html):
    page = _Page()
    page.feed(html)
    return page


def _acc_name(rec):
    a = rec["attrs"]
    return (rec["text"].strip()
            or (a.get("aria-label") or "").strip()
            or (a.get("title") or "").strip())


# ------------------------------------------------------ served sweeps ---

def test_no_empty_accessible_names_on_served_pages(seeded_client):
    """Win 4: every rendered <button>/<a> has a non-empty name."""
    client, inv_id = seeded_client
    routes = SEEDED_ROUTES + [f"/invoices/{inv_id}"]
    for route in routes:
        res = client.get(route)
        assert res.status_code == 200, route
        page = _parse(res.get_data(as_text=True))
        for rec in page.buttons:
            assert _acc_name(rec), (route, "button", rec["attrs"])
        for rec in page.links:
            assert _acc_name(rec), (route, "link", rec["attrs"])


def test_every_field_has_associated_label_on_served_pages(seeded_client):
    """Win 5: label for= (matching id) or a wrapping <label>."""
    client, inv_id = seeded_client
    routes = SEEDED_ROUTES + [f"/invoices/{inv_id}"]
    for route in routes:
        res = client.get(route)
        assert res.status_code == 200, route
        page = _parse(res.get_data(as_text=True))
        for field in page.fields:
            a = field["attrs"]
            if field["tag"] == "input":
                ftype = (a.get("type") or "text").lower()
                if ftype in ("hidden", "submit", "button", "reset", "image"):
                    continue
            has_for = bool(a.get("id") and a["id"] in page.labels_for)
            assert has_for or field["wrapped"], (route, field)


def test_no_duplicate_ids_or_empty_labels_on_served_pages(seeded_client):
    """Win 4/5 hardening: ids are unique; <label for=> has real text.

    A duplicate id makes a label point at the wrong field, and an
    empty <label for=> gives a field no usable name -- neither is
    visible to the name/label sweeps above, so pin both directly.
    """
    client, inv_id = seeded_client
    routes = SEEDED_ROUTES + [f"/invoices/{inv_id}"]
    for route in routes:
        res = client.get(route)
        assert res.status_code == 200, route
        page = _parse(res.get_data(as_text=True))
        dups = sorted(i for i, n in Counter(page.ids).items() if n > 1)
        assert not dups, (route, dups)
        for lab in page.labels:
            if lab["for"]:
                assert lab["text"].strip(), (route, lab["for"])


def test_focus_active_page_names_and_labels(seeded_client):
    """The running-session page only renders with a live session."""
    client, _inv_id = seeded_client
    res = client.post("/focus/start", data={
        "label": "Deep work", "preset": "50", "mode": "classic",
        "block_level": "strict", "enforcement_mode": "strict",
    })
    assert res.status_code == 302
    res = client.get("/focus")
    assert res.status_code == 200
    page = _parse(res.get_data(as_text=True))
    for rec in page.buttons:
        assert _acc_name(rec), ("active focus button", rec["attrs"])
    for field in page.fields:
        a = field["attrs"]
        if field["tag"] == "input" and (
                (a.get("type") or "text").lower()
                in ("hidden", "submit", "button", "reset", "image")):
            continue
        has_for = bool(a.get("id") and a["id"] in page.labels_for)
        assert has_for or field["wrapped"], ("active focus", field)
    client.post("/focus/abort")


def test_icon_only_theme_buttons_have_aria_labels(seeded_client):
    """Win 4 targeted pin: the only icon-only buttons in the shell."""
    client, _inv_id = seeded_client
    html = client.get("/goals").get_data(as_text=True)
    assert "aria-label='Light mode'" in html
    assert "aria-label='Dark mode'" in html


# --------------------------------------------------------- static CSS ---

def test_focus_visible_global_rule_and_field_override():
    """Win 1: global rule + restored outline on base form fields."""
    assert re.search(
        r":focus-visible \{\s*outline: 2px solid var\(--accent\);",
        STYLE_CSS)
    # The base field rule kills the outline; the restore must follow it.
    base = STYLE_CSS.index("outline: none;")
    restore = STYLE_CSS.index("input:focus-visible, textarea:focus-visible")
    assert base < restore
    assert "outline: 2px solid var(--accent);" in STYLE_CSS[restore:]


def test_focus_visible_living_label_row_override():
    """Win 1: living.css .lv-label-row outline:none is restored too."""
    base = LIVING_CSS.index(".lv-label-row input:focus {")
    restore = LIVING_CSS.index(".lv-label-row input:focus-visible")
    assert base < restore
    assert "outline: 2px solid var(--ember);" in LIVING_CSS[restore:]
    # The slice-9 living focus ring stays.
    assert ".lv-chip:focus-visible" in LIVING_CSS


def test_sr_only_utility_exists():
    """Win 5 support: the visually-hidden utility the labels use."""
    assert re.search(r"\.sr-only \{\s*position: absolute; width: 1px;",
                     STYLE_CSS)


def _reduce_blocks(css):
    """Bodies of the @media (prefers-reduced-motion: reduce) blocks."""
    out = []
    marker = "@media (prefers-reduced-motion: reduce)"
    pos = 0
    while True:
        try:
            start = css.index(marker, pos)
        except ValueError:
            return out
        open_brace = css.index("{", start)
        depth = 0
        j = open_brace
        while True:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        out.append(css[open_brace + 1:j])
        pos = j


def _rules(block):
    """(selector-list, declarations) per flat rule in a CSS block."""
    return [(sel.strip(), decls.strip()) for sel, decls
            in re.findall(r"([^{}]+)\{([^{}]*)\}", block)]


def test_reduced_motion_collapses_shipped_motion():
    """Win 6: both stylesheets neutralize transitions/animations."""
    for css in (STYLE_CSS, LIVING_CSS):
        blocks = _reduce_blocks(css)
        assert blocks
        flat = "\n".join(blocks)
        assert "animation-duration:" in flat
        assert "animation-iteration-count: 1" in flat
        assert "transition-duration:" in flat
        # Entrance delays are motion too: without this, delayed
        # entrances sit in their hidden `from` state, then snap.
        assert "animation-delay:" in flat
    # Smooth scrolling is motion; reduce turns it off (style.css).
    assert "html { scroll-behavior: auto; }" in STYLE_CSS
    # JS honors the same preference before animating.
    assert "prefers-reduced-motion" in NAV_JS


def test_reduced_motion_covers_living_body_pseudo_elements():
    """Win 6 repair: the body's own pseudo-elements are neutralized.

    `body.living *::after` matches descendants only -- the 18s
    lv-drift on `body.living::after` itself kept running under
    reduce. Pin the pseudo-element selectors themselves, not a
    substring of the block.
    """
    covered = {}
    for block in _reduce_blocks(LIVING_CSS):
        for selectors, decls in _rules(block):
            for sel in selectors.split(","):
                covered[sel.strip()] = decls
    for pseudo in ("body.living::after", "body.living::before"):
        assert pseudo in covered, pseudo
        decls = covered[pseudo]
        assert ("animation-duration" in decls
                or "animation-name" in decls
                or re.search(r"animation\s*:", decls)), pseudo
        assert "animation-delay" in decls, pseudo
    # The drift rule itself is the one being neutralized.
    assert "animation: lv-drift" in LIVING_CSS


def test_escape_closes_details_and_zen_mode():
    """Win 2: disclosures (nav.js) + zen-mode (pre-existing handlers)."""
    assert "e.key !== 'Escape'" in NAV_JS
    assert "details[open]" in NAV_JS
    assert "removeAttribute('open')" in NAV_JS
    assert "Escape" in FOCUS_JS  # classic Focus page zen exit
    assert 'e.key === "Escape"' in FOCUS_PY  # living Focus zen exit


# ------------------------------------------------------------- contrast ---

def _tokens(block):
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", block))


def _block(css, marker):
    return css.split(marker, 1)[1].split("}", 1)[0]


def _rgb(value, bg_hex=None):
    value = value.strip()
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", value)
    if m:
        h = m.group(1)
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    m = re.fullmatch(
        r"rgba\(\s*(\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)", value)
    if m and bg_hex is not None:
        fg = tuple(int(m.group(i)) / 255 for i in (1, 2, 3))
        alpha = float(m.group(4))
        bg = bg_hex if isinstance(bg_hex, tuple) else _rgb(bg_hex)
        return tuple(fg[i] * alpha + bg[i] * (1 - alpha) for i in range(3))
    raise AssertionError(f"unparseable color: {value!r}")


def _lum(rgb):
    def f(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (f(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ratio(fg_value, bg_hex):
    fg = _rgb(fg_value, bg_hex)
    bg = _rgb(bg_hex)
    hi, lo = max(_lum(fg), _lum(bg)), min(_lum(fg), _lum(bg))
    return (hi + 0.05) / (lo + 0.05)


# ---- cascade model (1.15 repair) ----
#
# Real CSS custom-property cascade: a theme block only overrides the
# tokens it names; everything else keeps the base value. The first
# version of these tests parsed each theme block in isolation, so a
# token defined ONLY in :root at a dark-failing luminance (exactly
# what --accent-strong shipped as) was invisible to the dark check.
# Merge base-under-theme and resolve var() chains before measuring.

def _val(tokens, key):
    """Resolve a token's value through any var() chain."""
    seen = set()
    value = tokens[key]
    while True:
        m = re.fullmatch(r"var\((--[\w-]+)\)", value.strip())
        if not m:
            return value.strip()
        key = m.group(1)
        assert key not in seen, f"cyclic var() at {key}"
        seen.add(key)
        value = tokens[key]


def _ratio_on(fg_value, bg_value, surface_hex):
    """Contrast of fg on bg, bg composited over the page surface."""
    bg_rgb = _rgb(bg_value, surface_hex)
    fg_rgb = _rgb(fg_value, bg_rgb)
    hi = max(_lum(fg_rgb), _lum(bg_rgb))
    lo = min(_lum(fg_rgb), _lum(bg_rgb))
    return (hi + 0.05) / (lo + 0.05)


def _pair(tokens, fg_key, bg_key, surface_key):
    return _ratio_on(_val(tokens, fg_key), _val(tokens, bg_key),
                     _val(tokens, surface_key))


def _style_theme(dark):
    root = _tokens(_block(STYLE_CSS, ":root {"))
    if not dark:
        return root
    merged = dict(root)
    merged.update(_tokens(_block(
        STYLE_CSS, "@media (prefers-color-scheme: dark) {")))
    merged.update(_tokens(_block(STYLE_CSS, '[data-theme="dark"] {')))
    return merged


def _living_theme(light):
    base = _tokens(_block(LIVING_CSS, "body.living {"))
    if not light:
        return base
    merged = dict(base)
    merged.update(_tokens(_block(
        LIVING_CSS, 'html[data-theme="light"] body.living {')))
    merged.update(_tokens(_block(
        LIVING_CSS, 'html:not([data-theme]) body.living {')))
    return merged


def test_contrast_named_pairs_style_css():
    """Win 7: ink / muted / accent-text / button text, light + dark.

    Tokens resolve through the cascade (:root under the dark blocks),
    so a text token that only exists in :root is measured at its real
    dark value -- the 1.15 ship regressed exactly there.
    """
    surfaces = ("--bg", "--bg-raised", "--bg-sunken")
    for dark in (False, True):
        tokens = _style_theme(dark)
        for key in ("--ink", "--ink-muted", "--accent-strong"):
            for surf in surfaces:
                ratio = _pair(tokens, key, surf, surf)
                assert ratio >= 4.5, (dark, key, surf, ratio)
        # Button text on the accent fill (both themes).
        ratio = _pair(tokens, "--ink-on-accent", "--accent", "--bg")
        assert ratio >= 4.5, (dark, "button", ratio)


def test_contrast_status_and_pill_pairs_style_css():
    """Win 7 repair: badges, tired notes, pills, step indicator.

    Normal-size informational text the first pass left under 4.5:1
    behind text-only -strong tokens / smallest same-hue steps.
    """
    surfaces = ("--bg", "--bg-raised", "--bg-sunken")
    for dark in (False, True):
        tokens = _style_theme(dark)
        # Status badges: -strong text on the -soft fill, over any
        # surface the badge can sit on.
        for fg_key, bg_key in (
                ("--success-strong", "--success-soft"),
                ("--warn-strong", "--warn-soft"),
                ("--danger-strong", "--danger-soft")):
            for surf in surfaces:
                ratio = _pair(tokens, fg_key, bg_key, surf)
                assert ratio >= 4.5, (dark, fg_key, surf, ratio)
        # Tired-state sentences sit directly on page surfaces.
        for surf in surfaces:
            ratio = _pair(tokens, "--warn-strong", surf, surf)
            assert ratio >= 4.5, (dark, "tired", surf, ratio)
        # Invoice pills (foreground/background pairs from
        # _invoice_status_badge) + the neutral default pill.
        for fg, bg in (("--ink-muted", "--bg-sunken"),
                       ("--ink-on-accent", "--accent"),
                       ("#ffffff", "--success"),
                       ("#ffffff", "--danger")):
            lit = dict(tokens)
            lit["#ffffff"] = "#ffffff"
            fg_v = lit[fg] if fg.startswith("#") else _val(tokens, fg)
            ratio = _ratio_on(fg_v, _val(tokens, bg),
                              _val(tokens, "--bg"))
            assert ratio >= 4.5, (dark, fg, bg, ratio)
    # Wiring: the shipped rules actually use the fixed pairings.
    for marker, needle in (
            (".badge--success", "var(--success-strong"),
            (".badge--warn", "var(--warn-strong"),
            (".badge--danger", "var(--danger-strong"),
            (".badge--protected", "var(--success-strong"),
            (".badge--enforcing", "var(--danger-strong"),
            (".sug-tired", "var(--warn-strong"),
            (".fc-tired", "var(--warn-strong")):
        i = STYLE_CSS.index(marker)
        assert needle in STYLE_CSS[i:i + 220], marker
    i = STYLE_CSS.index(".wk-filters")
    assert "color: var(--ink-muted)" in STYLE_CSS[i:i + 120]
    i = STYLE_CSS.index(".steps .step.now")
    assert "color: var(--ink-on-accent)" in STYLE_CSS[i:i + 120]
    i = STYLE_CSS.index(".pill {")
    pill = STYLE_CSS[i:i + 320]
    assert "color: var(--ink-muted)" in pill
    assert "background: var(--bg-sunken)" in pill
    i = APP_PY.index("_invoice_status_badge")
    badge_fn = APP_PY[i:APP_PY.index("\ndef ", i + 1)]
    for needle in ("var(--bg-sunken)", "var(--ink-muted)",
                   "var(--accent)", "var(--ink-on-accent)",
                   "var(--success)", "var(--danger)"):
        assert needle in badge_fn, needle


def test_contrast_named_pairs_living_css():
    """Win 7: living tokens, dark base + light overrides (cascade)."""
    surfaces = ("--bg", "--bg2")
    for light in (False, True):
        tokens = _living_theme(light)
        ember_text = ("--ember-strong" if "--ember-strong" in tokens
                      else "--ember")
        warn_text = ("--warn-strong" if "--warn-strong" in tokens
                     else "--warn")
        for key in ("--ink", "--ink2", "--ink3", ember_text, warn_text):
            for surf in surfaces:
                ratio = _pair(tokens, key, surf, surf)
                assert ratio >= 4.5, (light, key, surf, ratio)
        ratio = _pair(tokens, "--ember-ink", "--ember", "--bg")
        assert ratio >= 4.5, (light, "ember button", ratio)
    # Wiring: the break-phase state text uses the strong variant.
    i = LIVING_CSS.index('#lv-orb[data-phase="break"] #lv-orb-state')
    assert "var(--warn-strong" in LIVING_CSS[i:i + 120]


def test_contrast_spot_fix_is_smallest_same_hue_change():
    """Win 7 guard: the text-accent tokens are the shipped hue, darker.

    #bd7c1b (shipped fill accent) is 3.21:1 on --bg; the text variant
    must be a darker amber that reaches 4.5:1 on every surface.
    """
    assert _ratio("#bd7c1b", "#faf6ee") < 4.5  # the gap being pinned
    light = _tokens(_block(STYLE_CSS, ":root {"))
    assert light["--accent"] == "#bd7c1b"  # fills did not move
    assert light["--accent-strong"] == "#905f15"
    for bg in ("#faf6ee", "#fffdf8", "#f2ebdb"):
        assert _ratio("#905f15", bg) >= 4.5
