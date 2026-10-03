"""Tests for the accessibility statement (roadmap 3.3). Pure content
tests -- no Flask needed."""
from focuscore import help as help_mod


def test_accessibility_article_exists_and_renders():
    article = help_mod.get_article("accessibility")
    assert article is not None, "no accessibility help article"
    html = help_mod.article_html("accessibility")
    assert html and "/help/accessibility" in html


def test_accessibility_statement_makes_no_conformance_claims():
    """The statement must never claim an earned conformance level."""
    article = help_mod.get_article("accessibility")
    text = " ".join(
        [article["what"]] + article["do"] + article["trouble"]).lower()
    for claim in ("conformant", "compliant", "certified"):
        assert claim not in text, (
            f"accessibility statement claims unearned conformance: {claim!r}")
    assert "no formal" in text and "audit" in text, (
        "statement must say plainly that no formal audit has been run")
