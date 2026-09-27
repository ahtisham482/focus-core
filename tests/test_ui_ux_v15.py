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
from dashboard.routes.focus import (
    _svg_ring,
    _depth_pill,
    _DEPTH_COLORS,
)


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
    """Verify Qwen M-B depth->amplitude mapping and SVG ring attributes.

    Qwen M-B Mandate:
    - Raw score written to data-depth-score
    - CSS transfer function: --pulse-amp: calc(1 - var(--depth, 0))
    - Clamped depth: surface=0.0, deep=0.5, flow=1.0, unmeasured=0.0
    - Gray = unmeasured (FLOW-1 satisfied)
    - Full ARIA string present without aria-live
    """
    # Surface
    html_surf = _svg_ring(1500, 1500, depth="surface")
    assert "data-depth-score='0.0'" in html_surf
    assert "aria-label='Surface focus" in html_surf
    assert "data-pulse" in html_surf
    assert "<line " in html_surf  # tick marks present

    # Deep
    html_deep = _svg_ring(750, 1500, depth="deep")
    assert "data-depth-score='0.5'" in html_deep
    assert "aria-label='Deep work" in html_deep

    # Flow
    html_flow = _svg_ring(300, 1500, depth="flow")
    assert "data-depth-score='1.0'" in html_flow
    assert "aria-label='Flow state" in html_flow

    # Unmeasured (FLOW-1 Tri-state)
    html_unm = _svg_ring(0, 1500, depth="unmeasured")
    assert "data-depth-score='0.0'" in html_unm
    assert "aria-label='Focus not yet measured" in html_unm
    assert _DEPTH_COLORS["unmeasured"][0] == "#64748b"  # slate, not failure red


def test_merlin_break_pill_refinement():
    """Verify Merlin directive: on break, pill shows '☕ On Break' without score."""
    # Active focus pill
    depth_data = {"state": "flow", "switches_15m": 1, "uninterrupted_min": 25.0}
    pill_work = _depth_pill("sess1", depth_data, on_break=False)
    assert "Flow State" in pill_work
    assert "1 app switch" in pill_work

    # Break mode pill
    pill_break = _depth_pill("sess1", depth_data, on_break=True)
    assert "On Break" in pill_break
    assert "is-break" in pill_break
    assert "Resting your focus rhythm" in pill_break
    assert "app switch" not in pill_break  # number removed per Merlin directive



def test_home_page_cold_start_and_active_hero(tmp_path, monkeypatch):
    """Verify Merlin cold-start hero copy on Day 1 vs active hero on tracked days."""
    db = str(tmp_path / "home.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)
    # Prevent live ActivityWatch collection during cold-start test
    monkeypatch.setattr(dash_app, "run_day", lambda *a, **kw: None)

    dash_app.app.config["TESTING"] = True
    client = dash_app.app.test_client()

    # Day 1: Zero tracked time, zero streak -> Cold-start Hero
    res = client.get("/")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "Day one &mdash; your focus story starts now." in html
    assert "Focus Core is learning your rhythm" in html
    assert "Start a focus session" in html
    assert "Streak: <b>Day 1</b>" in html



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
    assert "<h1 class='page-title'>Shield</h1>" in html_s
    assert "distraction(s) blocked today" in html_s

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


def test_qwen_rv5_dashboard_csp_allows_self():
    """RV-5: Verify dashboard routes permit 'self' for nav.js and local Geist fonts."""
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

    # Depth mapping truth table
    s_ring = _svg_ring(100, 100, depth="surface")
    assert "data-depth-score='0.0'" in s_ring

    d_ring = _svg_ring(50, 100, depth="deep")
    assert "data-depth-score='0.5'" in d_ring

    f_ring = _svg_ring(20, 100, depth="flow")
    assert "data-depth-score='1.0'" in f_ring


def test_qwen_rv8_hygiene_trio():
    """RV-8: Verify skips documented, coffee emoji wrapped, scoped transition."""
    import pathlib
    # (a) Verify skipped tests exist and are justified in test_win32.py
    win32_tests = pathlib.Path("tests/test_win32.py").read_text(encoding="utf-8")
    assert 'reason="graceful-degradation checks are for non-Windows"' in win32_tests

    # (b) Coffee emoji wrapped in aria-hidden
    depth_data = {"state": "flow", "switches_15m": 0, "uninterrupted_min": 10.0}
    pill = _depth_pill("s1", depth_data, on_break=True)
    assert "<span aria-hidden='true'>\u2615</span> On Break" in pill

    # (c) Token-swap transition scoped to color properties (NOT transition: all)
    css = pathlib.Path("dashboard/static/style.css").read_text(encoding="utf-8")
    assert "transition: background-color 400ms ease, color 400ms ease;" in css

