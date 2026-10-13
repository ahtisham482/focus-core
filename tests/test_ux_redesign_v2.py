"""Tests for UX redesign v2 (warm premium)."""
import pathlib
import re
from dashboard.app import layout, NAV_GROUPS, NAV_LINKS
from focuscore.taxonomy import DEFAULT_CATEGORIES

TOKENS = (pathlib.Path(__file__).resolve().parent.parent
          / "dashboard" / "static" / "tokens.css")


def _slug(name):
    return name.lower().replace(" & ", "-").replace(" ", "-")


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
    assert len(DEFAULT_CATEGORIES) == 11
    for cat in DEFAULT_CATEGORIES:
        slug = _slug(cat["name"])
        assert f"--cat-{slug}:" in css, f"tokens.css missing --cat-{slug}"


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


def test_nav_group_primary_href_matches_first_member():
    # guards against silent drift between a group label's href and its members
    by_key = {k: href for k, _, href, _ in NAV_LINKS}
    for gkey, _, ghref, members in NAV_GROUPS:
        assert ghref == by_key[members[0]], f"group {gkey} label href drifted"


def test_layout_renders_grouped_nav():
    html = layout("Dashboard", "<p>Hello</p>", active="focus")
    # exactly 4 group divs
    # (label links use class='nav-group-label' — excluded by the <div prefix)
    assert len(re.findall(r"<div class='nav-group(?: |')", html)) == 4
    assert html.count("class='nav-group-label'") == 4
    assert "<a class='nav-group-label' href='/focus'>Focus</a>" in html
    # dropdown holds every member; icons preserved
    assert "class='nav-dropdown'" in html
    assert "href='/timesheet'" in html and "href='/activities'" in html
    # the active page's group is highlighted
    assert "nav-group active-group" in html
    # every member href lives inside a dropdown; group labels are the only
    # other links (each primary href appears twice: label + its member link)
    expected_counts = {}
    for _, _, ghref, members in NAV_GROUPS:
        expected_counts[ghref] = expected_counts.get(ghref, 0) + 1
    by_key = {k: href for k, _, href, _ in NAV_LINKS}
    for mkey in [m for _, _, _, ms in NAV_GROUPS for m in ms]:
        href = by_key[mkey]
        expected_counts[href] = expected_counts.get(href, 0) + 1
    for href, n in expected_counts.items():
        assert html.count("href='%s'" % href) == n, f"{href}: expected {n}"


def test_review_link_keeps_day_param():
    html = layout("Dashboard", "<p>Hello</p>", day="2026-10-13", active="review")
    assert "href='/activities?day=2026-10-13'" in html


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


def test_nav_js_has_dropdown_behavior():
    js = (pathlib.Path(__file__).resolve().parent.parent
          / "dashboard" / "static" / "nav.js").read_text()
    assert "nav-caret" in js and "Escape" in js


def test_button_styles_use_tokens():
    css = (pathlib.Path(__file__).resolve().parent.parent
           / "dashboard" / "static" / "style.css").read_text()
    section = css.split("UX v2: buttons")[1]
    assert ".btn-primary" in section and "var(--gold)" in section
    assert ".btn-secondary" in section and ".btn-danger" in section
    assert "var(--radius-pill)" in section and "min-height: 44px" in section


def test_button_size_modifiers_survive_new_base():
    # Regression: the new .btn (0,1,0) ties the old .btn-sm/.btn-xl (0,1,0);
    # without a stronger rule the modifiers are silently defeated by source order.
    css = (pathlib.Path(__file__).resolve().parent.parent
           / "dashboard" / "static" / "style.css").read_text()
    section = css.split("UX v2: buttons")[1]
    assert ".btn.btn-sm" in section and ".btn.btn-xl" in section
    assert "min-height: 32px" in section and "min-height: 56px" in section


def test_card_styles_use_tokens():
    css = (pathlib.Path(__file__).resolve().parent.parent
           / "dashboard" / "static" / "style.css").read_text()
    section = css.split("UX v2: cards")[1]
    assert ".card-v2" in section and "var(--surface)" in section
    assert "var(--radius-card)" in section and "var(--shadow-card)" in section
    assert ".card-v2-title" in section and ".card-v2-body" in section


def test_input_styles_use_tokens():
    css = (pathlib.Path(__file__).resolve().parent.parent
           / "dashboard" / "static" / "style.css").read_text()
    section = css.split("UX v2: inputs")[1]
    # full element-scoped selector: plain .input-v2 (0,1,0) would lose to the
    # old input[type=...] rule (0,1,1); input.input-v2 ties and wins by order
    assert "input.input-v2" in section and "var(--radius-input)" in section
    assert ":focus" in section and "var(--gold)" in section
    assert "min-height: 44px" in section
    assert "box-shadow: none" in section


def test_empty_state_styles_use_tokens():
    css = (pathlib.Path(__file__).resolve().parent.parent
           / "dashboard" / "static" / "style.css").read_text()
    section = css.split("UX v2: empty")[1]
    assert ".empty-v2" in section and "var(--muted)" in section
    assert ".empty-v2-icon" in section


def test_type_styles_use_tokens():
    css = (pathlib.Path(__file__).resolve().parent.parent
           / "dashboard" / "static" / "style.css").read_text()
    section = css.split("UX v2: type")[1]
    assert ".page-title-v2" in section and "var(--fs-display)" in section
    assert ".section-title-v2" in section and "var(--fs-h2)" in section
    assert ".prose-v2" in section and "line-height: 1.6" in section


def test_home_uses_stat_cards_and_ring(tmp_path, monkeypatch):
    import dashboard.app as dash_app
    from focuscore import store
    db = str(tmp_path / "home.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)
    # Prevent live ActivityWatch collection
    monkeypatch.setattr(dash_app, "run_day", lambda *a, **kw: None)
    # Onboarded: / redirects to /welcome without the flag file.
    flag = tmp_path / ".onboarded"
    flag.write_text("2026-09-29")
    monkeypatch.setattr(dash_app, "ONBOARDED_FLAG", flag)
    dash_app.app.config["TESTING"] = True
    client = dash_app.app.test_client()
    res = client.get("/")
    html = res.get_data(as_text=True)
    assert res.status_code == 200
    # stat cards use the shared component class
    assert "stat-card" in html and "stat-num" in html
    # Deep Focus card renders a real SVG progress ring
    assert "progress-ring" in html and "<svg" in html


def test_day_view_timeline_uses_category_tokens(tmp_path, monkeypatch):
    import dashboard.app as dash_app
    from focuscore import store
    db = str(tmp_path / "day.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.init_db(db)
    # Seed categorized activity + day stats: an empty DB takes the
    # empty-state branch, which renders no rows at all.
    store.save_events("2026-10-13", [
        {"app": "Code", "title": "main.py", "duration": 3600,
         "score": 2, "category": "Software Development"},
        {"app": "Tube", "title": "vid", "duration": 1800,
         "score": -2, "category": "Entertainment"},
    ], path=db)
    store.save_day_stats("2026-10-13", 0.0, 5400.0, path=db)
    # Prevent live ActivityWatch collection.
    monkeypatch.setattr(dash_app, "run_day", lambda *a, **kw: None)
    # Onboarded: some routes redirect to /welcome without the flag file.
    flag = tmp_path / ".onboarded"
    flag.write_text("2026-09-29")
    monkeypatch.setattr(dash_app, "ONBOARDED_FLAG", flag)
    dash_app.app.config["TESTING"] = True
    client = dash_app.app.test_client()
    res = client.get("/day/2026-10-13")
    html = res.get_data(as_text=True)
    assert res.status_code == 200
    assert "timeline-row" in html
    assert "data-category='software-development'" in html
    assert "data-category='entertainment'" in html
