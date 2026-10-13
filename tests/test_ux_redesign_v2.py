"""Tests for UX redesign v2 (warm premium)."""
import pathlib
from dashboard.app import layout, NAV_GROUPS, NAV_LINKS
from focuscore.taxonomy import DEFAULT_CATEGORIES

TOKENS = pathlib.Path(__file__).resolve().parent.parent / "dashboard" / "static" / "tokens.css"


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
