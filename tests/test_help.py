"""Tests for the in-app help system (focuscore/help.py). Pure content
tests -- no Flask needed."""
from focuscore import help as help_mod


def test_every_nav_page_has_help():
    missing = [k for k in help_mod.NAV_HELP
               if help_mod.get_article(help_mod.NAV_HELP[k]) is None]
    assert not missing, "nav pages without help: %s" % missing


def test_every_article_is_complete():
    for key, article in help_mod.ARTICLES.items():
        assert article["title"], key
        assert article["href"].startswith("/"), key
        assert article["what"], key
        assert isinstance(article["do"], list) and len(article["do"]) >= 1, key
        assert isinstance(article["trouble"], list), key


def test_article_html_renders_known_key():
    body = help_mod.article_html("home")
    assert body is not None
    assert "Home" in body
    assert "What to do here" in body
    assert "If something looks wrong" in body
    assert "Back to Home" in body
    assert "/help" in body


def test_article_html_unknown_key_returns_none():
    assert help_mod.article_html("no-such-page") is None


def test_article_without_trouble_omits_section():
    body = help_mod.article_html("welcome")
    assert body is not None
    assert "If something looks wrong" not in body


def test_index_lists_every_article():
    body = help_mod.index_html()
    for key, article in help_mod.ARTICLES.items():
        assert "/help/%s" % key in body, key
        assert article["title"] in body, key


def test_help_content_is_plain_text_friendly():
    # No raw HTML tags smuggled into content fields (builders add the tags).
    for key, article in help_mod.ARTICLES.items():
        for field in ("title", "what"):
            assert "<" not in article[field] and ">" not in article[field], (key, field)
        for item in article["do"] + article["trouble"]:
            assert "<" not in item and ">" not in item, (key, item[:40])
