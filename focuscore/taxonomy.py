"""Category taxonomy and activity matching.

Mirrors RescueTime's category model: a fixed set of generic top-level
categories, each with a default productivity score. Activities inherit
their score from their category unless the user overrides them.
Users may add custom sub-categories (parent + name + score).
"""

from urllib.parse import urlsplit

from .ingest import BROWSER_APPS
from .seed_data import SEED_RULES, KEYWORD_FALLBACKS

# Generic top-level categories mirroring RescueTime's defaults.
DEFAULT_CATEGORIES = [
    {"name": "Business", "default_score": 1},
    {"name": "Communication & Scheduling", "default_score": 0},
    {"name": "Social Networking", "default_score": -2},
    {"name": "Design & Composition", "default_score": 2},
    {"name": "Entertainment", "default_score": -2},
    {"name": "News", "default_score": -1},
    {"name": "Software Development", "default_score": 2},
    {"name": "Reference & Learning", "default_score": 1},
    {"name": "Shopping", "default_score": -1},
    {"name": "Utilities", "default_score": 0},
    {"name": "Uncategorized", "default_score": 0},
]

_DEFAULT_SCORES = {c["name"]: c["default_score"] for c in DEFAULT_CATEGORIES}

# Rules are checked most-specific-first: longer patterns win over shorter
# ones (e.g. "mail.google.com" beats a bare "google.com" if both existed).
_DOMAIN_RULES = sorted(
    (r for r in SEED_RULES if r[0] == "domain"), key=lambda r: len(r[1]), reverse=True
)
_APP_EXACT_RULES = [r for r in SEED_RULES if r[0] == "app_exact"]
_APP_CONTAINS_RULES = sorted(
    (r for r in SEED_RULES if r[0] == "app_contains"),
    key=lambda r: len(r[1]),
    reverse=True,
)
_TITLE_RULES = sorted(
    (r for r in SEED_RULES if r[0] == "title_keyword"),
    key=lambda r: len(r[1]),
    reverse=True,
)
_FALLBACKS = sorted(KEYWORD_FALLBACKS, key=lambda kv: len(kv[0]), reverse=True)


def host_of(url):
    """Extract the lowercase host from a URL, or "" if there is none."""
    if not url:
        return ""
    try:
        return (urlsplit(url).hostname or "").lower()
    except Exception:
        return ""


def match_key(app, url):
    """Stable key identifying "the same activity" for overrides.

    A URL-bearing event is keyed by its domain ("domain:youtube.com");
    anything else is keyed by its app name ("app:code"). The dashboard
    and the overrides table both use this key.
    """
    host = host_of(url)
    if host:
        return "domain:" + host
    return "app:" + (app or "unknown").lower()


def get_category_score(name, custom=None):
    """Score for a category name.

    ``custom`` is an optional mapping of custom sub-category name ->
    {"score": int|None, "parent": str|None}. A sub-category with
    score=None inherits its parent's score.
    """
    if custom and name in custom:
        entry = custom[name]
        score = entry.get("score")
        if score is not None:
            return score
        parent = entry.get("parent")
        if parent:
            return get_category_score(parent, custom)
    return _DEFAULT_SCORES.get(name, 0)


def _match_title_rules(title_l):
    """First matching seed title-keyword rule, or None."""
    for _, pattern, category, score in _TITLE_RULES:
        if pattern in title_l:
            return category, score, "title_keyword:" + pattern
    return None


def categorize(app, title, url=None):
    """Categorize one activity.

    Returns (category_name, score, matched_rule_or_None) where the rule
    is a short string like "domain:youtube.com" describing what matched.

    Check order: seed domain rules -> seed app rules -> seed title
    keywords -> generic keyword fallbacks -> Uncategorized (Neutral, 0).

    One exception: for known browsers the process name carries no signal
    on its own ("chrome" is Neutral), so the window title is checked
    BEFORE the browser's own app rule. Otherwise the neutral "chrome"
    rule would shadow every title keyword (e.g. a YouTube tab) whenever
    no URL was logged.
    """
    app_l = (app or "").lower()
    title_l = (title or "").lower()

    # Windows process names almost always end in ".exe" ("code.exe"),
    # while seed rules are written bare ("code"). Strip the suffix so
    # app_exact / app_contains rules actually match on Windows.
    app_norm = app_l[:-4] if app_l.endswith(".exe") else app_l

    host = host_of(url)
    if host:
        for _, pattern, category, score in _DOMAIN_RULES:
            if host == pattern or host.endswith("." + pattern):
                return category, score, "domain:" + pattern

    if app_norm in BROWSER_APPS and title_l:
        hit = _match_title_rules(title_l)
        if hit:
            return hit

    if app_norm:
        for _, pattern, category, score in _APP_EXACT_RULES:
            if app_norm == pattern:
                return category, score, "app_exact:" + pattern
        for _, pattern, category, score in _APP_CONTAINS_RULES:
            if pattern in app_norm:
                return category, score, "app_contains:" + pattern

    if title_l:
        hit = _match_title_rules(title_l)
        if hit:
            return hit

    haystack = (app_l + " " + title_l).strip()
    if haystack:
        for keyword, category in _FALLBACKS:
            if keyword in haystack:
                return category, _DEFAULT_SCORES[category], "fallback:" + keyword

    return "Uncategorized", 0, None
