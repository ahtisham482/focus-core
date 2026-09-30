"""Tests for World-Class UI/UX Redesign (v1.15.0).

Verifies consensus directives from Quad-Agent Council:
- GLM-5.3 Adversarial Stress Audit (UI-8 through UI-14)
- Muse AI Merlin Design Directives (Pulse fade, Break scope, Cold-start hero)
- Qwen 3.8 Max Lead Architectural Audit (M-A trigger taxonomy,
  M-B pulse transfer function, M-C forced-colors)
"""


from focuscore import store
import dashboard.app as dash_app
from dashboard.app import layout, NAV_LINKS
from dashboard.routes.focus import _fc_depth_strip


def test_nav_links_have_icons():
    """Verify all NAV_LINKS tuples have 4 elements including valid SVG icon key."""
    assert len(NAV_LINKS) >= 10
    valid_icons = {
        "home", "timesheet", "invoice", "report", "coaching",
        "brain", "timer", "shield", "target", "bell", "sun", "moon"
    }
    for item in NAV_LINKS:
        assert len(item) == 4, f"Nav item {item} must be a 4-tuple"
        key, label, href, icon = item
        assert icon in valid_icons, f"Icon {icon} for {key} not in sprite icons"


def test_layout_renders_tokens_and_assets(tmp_path, monkeypatch):
    """Verify layout() injects preloaded fonts, theme attribute, nav.js, and hero."""
    db = str(tmp_path / "ui.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)

    # 1. Default system theme
    html = layout("Dashboard", "<p>Hello</p>", active="home")
    assert "<link rel='preload' href='/static/fonts/GeistVF.woff2'" in html
    assert "<link rel='stylesheet' href='/static/style.css'>" in html
    assert "<script src='/static/nav.js'></script>" in html
    assert "<main class='page-main'>" in html
    assert "class='theme-toggle'" in html
    assert "data-theme=" not in html  # system theme has no explicit attribute

    # 2. Explicit dark theme
    store.set_setting("ui_theme", "dark")
    html_dark = layout("Dashboard", "<p>Hello</p>", active="home")
    assert "data-theme='dark'" in html_dark

    # 3. Context-aware Hero header injection
    hero = "<div class='page-hero'><h1>Custom Hero</h1></div>"
    html_hero = layout("Dashboard", "<p>Hello</p>", hero=hero)
    assert "<div class='page-hero'><h1>Custom Hero</h1></div>" in html_hero


def test_theme_post_route(tmp_path, monkeypatch):
    """Verify /settings/theme POST endpoint saves valid themes and falls back safely."""
    db = str(tmp_path / "theme.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)

    dash_app.app.config["TESTING"] = True
    client = dash_app.app.test_client()

    # Toggle to dark
    res = client.post(
        "/settings/theme", data={"theme": "dark"}, follow_redirects=False
    )
    assert res.status_code == 302
    assert store.get_setting("ui_theme") == "dark"

    # Toggle to light
    res = client.post(
        "/settings/theme", data={"theme": "light"}, follow_redirects=False
    )
    assert res.status_code == 302
    assert store.get_setting("ui_theme") == "light"

    # Invalid theme falls back to system
    res = client.post(
        "/settings/theme", data={"theme": "neon-glow"}, follow_redirects=False
    )
    assert res.status_code == 302
    assert store.get_setting("ui_theme") == "system"


def test_qwen_mb_pulse_inversion_and_depth_mapping():
    """Verify the Focus craft depth strip: plain-English label per state,
    state color carried by the meter bar only (never the text), and the
    unmeasured state reads as 'Measuring focus' — never a failure red.
    """
    depth = {"state": "surface", "switches_15m": 0, "uninterrupted_min": 2.0}

    surf = _fc_depth_strip("s1", {**depth, "state": "surface"})
    assert "Surface focus" in surf
    assert "#94a3b8" in surf  # slate state color lives in the meter

    deep = _fc_depth_strip("s1", {**depth, "state": "deep"})
    assert "Deep work" in deep

    flow = _fc_depth_strip("s1", {**depth, "state": "flow"})
    assert "Flow state" in flow
    assert "#22c55e" in flow  # accent meter for flow

    unm = _fc_depth_strip("s1", {**depth, "state": "unmeasured"})
    assert "Measuring focus" in unm
    assert "#64748b" in unm  # slate, not failure red
    assert "fc-depth-label" in unm
    # No aria-live chatter: the poll only swaps the label text.
    assert "aria-live" not in unm


def test_merlin_break_pill_refinement():
    """Verify the craft directive: on break, the depth strip shows a plain
    'On break' label with no scores and no emoji; work mode shows the
    plain-English state plus the switch count."""
    # Active focus strip
    depth_data = {"state": "flow", "switches_15m": 1, "uninterrupted_min": 25.0}
    strip_work = _fc_depth_strip("sess1", depth_data, on_break=False)
    assert "Flow state" in strip_work
    assert "1 app switch" in strip_work

    # Break mode strip
    strip_break = _fc_depth_strip("sess1", depth_data, on_break=True)
    assert "On break" in strip_break
    assert "Resting your focus rhythm" in strip_break
    assert "app switch" not in strip_break  # number removed per directive
    # No emoji icons anywhere on the craft page.
    for glyph in ("\u2615", "\U0001f3c5", "\U0001f389", "\U0001f507"):
        assert glyph not in strip_break, glyph



def test_home_page_cold_start_and_active_hero(tmp_path, monkeypatch):
    """Verify Merlin cold-start hero copy on Day 1 vs active hero on tracked days."""
    db = str(tmp_path / "home.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)
    # Prevent live ActivityWatch collection during cold-start test
    monkeypatch.setattr(dash_app, "run_day", lambda *a, **kw: None)
    # Onboarded: / redirects to /welcome without the flag file.
    flag = tmp_path / ".onboarded"
    flag.write_text("2026-09-29")
    monkeypatch.setattr(dash_app, "ONBOARDED_FLAG", flag)

    dash_app.app.config["TESTING"] = True
    client = dash_app.app.test_client()

    # Day 1: Zero tracked time, zero streak -> Cold-start Hero
    res = client.get("/")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # Living Instrument cold-start hero: masked headline lines + subcopy.
    assert "Day one." in html
    assert "Your focus story" in html
    assert "starts now." in html
    assert "Focus Core is learning your rhythm" in html
    assert "Begin focus session" in html
    assert ">Day 1</span>" in html



def test_shield_and_intelligence_heroes(tmp_path, monkeypatch):
    """Verify context-aware heroes on Shield and Deep Time pages."""
    db = str(tmp_path / "sys.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)

    dash_app.app.config["TESTING"] = True
    client = dash_app.app.test_client()

    # Shield hero
    res_shield = client.get("/shield")
    assert res_shield.status_code == 200
    html_s = res_shield.get_data(as_text=True)
    assert "Shield is " in html_s  # Guard batch: dominant on/off hero
    assert "blocked today" in html_s

    # Deep Time hero (FLOW-1 Tri-state)
    res_intel = client.get("/intelligence")
    assert res_intel.status_code == 200
    html_i = res_intel.get_data(as_text=True)
    assert "<h1 class='page-title'>Deep Time</h1>" in html_i
    assert "tracking begins now" in html_i  # FLOW-1: no data -> tracking begins now
    assert "context switches" in html_i


# ─────────────────────────────────────────────────────────────────────────────
# QWEN RESIDUAL VERIFICATION LEDGER (RV-1 through RV-8)
# ─────────────────────────────────────────────────────────────────────────────

def test_qwen_rv1_reduced_motion_collapse():
    """RV-1: Verify prefers-reduced-motion collapses transitions and animations."""
    import pathlib
    css = pathlib.Path("dashboard/static/style.css").read_text(encoding="utf-8")
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert ("animation-duration: 0.001ms !important;" in css
            or "animation: none !important;" in css)
    assert "body, .card, .topnav { transition: none !important; }" in css
    assert ".fc-ring-time[data-pulse] { animation: none !important; }" in css


def test_qwen_rv2_print_styles_and_js_fallback():
    """RV-2: Verify @media print disables motion, and nav.js has print fallback."""
    import pathlib
    css = pathlib.Path("dashboard/static/style.css").read_text(encoding="utf-8")
    assert "@media print" in css
    assert "animation: none !important;" in css
    assert "transition: none !important;" in css

    js = pathlib.Path("dashboard/static/nav.js").read_text(encoding="utf-8")
    assert "window.addEventListener('beforeprint'" in js
    assert "window.matchMedia('print')" in js


def test_qwen_rv3_pathname_only_fingerprint():
    """RV-3: Verify nav.js stores pathname only (stripped of query and hash)."""
    import pathlib
    js = pathlib.Path("dashboard/static/nav.js").read_text(encoding="utf-8")
    assert "var currentPath = window.location.pathname;" in js
    assert "sessionStorage.setItem('fc_nav_fingerprint'" in js


def test_qwen_rv4_report_route_isolation_and_csp(tmp_path, monkeypatch):
    """RV-4: Verify /intelligence/report has zero <script> and locked CSP header."""
    db = str(tmp_path / "intel_rep.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)

    client = dash_app.app.test_client()
    res = client.get("/intelligence/report")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    # 1. Zero scripts
    assert "<script" not in html.lower()

    # 2. Strict CSP in HTTP headers
    csp = res.headers.get("Content-Security-Policy", "")
    assert "default-src 'none'" in csp
    assert "style-src 'unsafe-inline'" in csp
    assert "img-src data:" in csp


def test_qwen_rv5_dashboard_csp_allows_self(monkeypatch, tmp_path):
    """RV-5: Verify dashboard routes permit 'self' for nav.js and local Geist fonts."""
    flag = tmp_path / ".onboarded"
    flag.write_text("2026-09-29")
    monkeypatch.setattr(dash_app, "ONBOARDED_FLAG", flag)
    client = dash_app.app.test_client()
    res = client.get("/")
    assert res.status_code == 200
    csp = res.headers.get("Content-Security-Policy", "")
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "font-src 'self'" in csp


def test_qwen_rv6_data_financial_ci_guard(tmp_path, monkeypatch):
    """RV-6: CI guard asserting rendered currency nodes carry data-financial."""
    import sqlite3
    from focuscore import invoices as inv_mod
    db = str(tmp_path / "fin.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)

    # 1. Seed project and billable timesheet entry
    pid = store.add_project("Acme", client="Acme Corp", path=db)
    eid = store.create_entry("2026-09-20", "2026-09-20T09:00",
                             "2026-09-20T10:00", 60.0, "Work",
                             project_id=pid, task="Dev",
                             status="accepted", path=db)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE timesheet_entries SET hourly_rate_minor = 10000, "
            "rate_currency = 'USD', rate_status = 'confirmed' "
            "WHERE id = ?", (eid,))
        conn.commit()
    finally:
        conn.close()

    # 2. Create invoice
    inv_id = inv_mod.create_invoice(pid, "2026-09-20", "2026-09-20", path=db)

    dash_app.app.config["TESTING"] = True
    c = dash_app.app.test_client()

    # 3. Verify /invoices list has data-financial attribute
    res_list = c.get("/invoices")
    assert res_list.status_code == 200
    html_list = res_list.get_data(as_text=True)
    assert "data-financial" in html_list

    # 4. Verify /invoices/<id> detail has data-financial attribute
    res_detail = c.get(f"/invoices/{inv_id}")
    assert res_detail.status_code == 200
    html_detail = res_detail.get_data(as_text=True)
    assert "data-financial" in html_detail


def test_qwen_rv7_whcm_and_depth_truth_table():
    """RV-7: Verify WHCM system keywords and depth mapping truth table."""
    import pathlib
    css = pathlib.Path("dashboard/static/style.css").read_text(encoding="utf-8")
    assert "@media (forced-colors: active)" in css
    # Verify mandatory system color keywords
    assert "stroke: ButtonText;" in css
    assert "stroke: Highlight;" in css
    assert "stroke: GrayText;" in css
    assert "fill: CanvasText;" in css

    # Depth mapping truth table (craft strip, not the old SVG ring)
    s_ring = _fc_depth_strip("s", {"state": "surface", "switches_15m": 0,
                                  "uninterrupted_min": 1.0})
    assert "Surface focus" in s_ring

    d_ring = _fc_depth_strip("s", {"state": "deep", "switches_15m": 0,
                                  "uninterrupted_min": 10.0})
    assert "Deep work" in d_ring

    f_ring = _fc_depth_strip("s", {"state": "flow", "switches_15m": 0,
                                  "uninterrupted_min": 20.0})
    assert "Flow state" in f_ring


def test_qwen_rv8_hygiene_trio():
    """RV-8: Verify skips documented, coffee emoji wrapped, scoped transition."""
    import pathlib
    # (a) Verify skipped tests exist and are justified in test_win32.py
    win32_tests = pathlib.Path("tests/test_win32.py").read_text(encoding="utf-8")
    assert 'reason="graceful-degradation checks are for non-Windows"' in win32_tests

    # (b) No emoji icons on the craft Focus page — break strip included
    depth_data = {"state": "flow", "switches_15m": 0, "uninterrupted_min": 10.0}
    strip = _fc_depth_strip("s1", depth_data, on_break=True)
    assert "On break" in strip
    for glyph in ("\u2615", "\U0001f3c5", "\U0001f389", "\U0001f507"):
        assert glyph not in strip, glyph

    # (c) Token-swap transition scoped to color properties (NOT transition: all)
    css = pathlib.Path("dashboard/static/style.css").read_text(encoding="utf-8")
    assert "transition: background-color 400ms ease, color 400ms ease;" in css

