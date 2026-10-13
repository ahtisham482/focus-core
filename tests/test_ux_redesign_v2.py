"""Tests for UX redesign v2 (warm premium)."""
import pathlib
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
