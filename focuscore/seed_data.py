"""Pre-seeded app/website -> (category, score) rules.

Generic defaults only -- no personal data. Everything here is
user-overridable: a per-activity override always wins over these seeds
(see focuscore/scoring.py).

Rule tuple: (match_type, pattern, category, score)
match_type:
    "domain"        -- URL host equals pattern, or is a subdomain of it
                       ("www.youtube.com" matches "youtube.com")
    "app_exact"     -- process/app name equals pattern (case-insensitive)
    "app_contains"  -- pattern appears in the app name (case-insensitive)
    "title_keyword" -- pattern appears in the window title (case-insensitive)
"""

SEED_RULES = [
    # ---------------- Entertainment ----------------
    ("domain", "youtube.com", "Entertainment", -2),
    ("domain", "youtu.be", "Entertainment", -2),
    # Fallback for machines without the aw-watcher-web browser extension:
    # ActivityWatch then logs url=None, so the domain rules above never
    # fire. The window title still says e.g. "YouTube - Google Chrome".
    ("title_keyword", "youtube", "Entertainment", -2),
    ("domain", "netflix.com", "Entertainment", -2),
    ("domain", "twitch.tv", "Entertainment", -2),
    ("domain", "hulu.com", "Entertainment", -2),
    ("domain", "disneyplus.com", "Entertainment", -2),
    ("domain", "primevideo.com", "Entertainment", -2),
    ("domain", "spotify.com", "Entertainment", -1),
    # ---------------- Social Networking ----------------
    ("domain", "facebook.com", "Social Networking", -2),
    ("domain", "instagram.com", "Social Networking", -2),
    ("domain", "x.com", "Social Networking", -2),
    ("domain", "twitter.com", "Social Networking", -2),
    ("domain", "tiktok.com", "Social Networking", -2),
    ("domain", "reddit.com", "Social Networking", -2),
    ("domain", "pinterest.com", "Social Networking", -2),
    ("domain", "snapchat.com", "Social Networking", -2),
    ("domain", "linkedin.com", "Social Networking", -1),
    # ---------------- Communication & Scheduling ----------------
    ("domain", "gmail.com", "Communication & Scheduling", 0),
    ("domain", "mail.google.com", "Communication & Scheduling", 0),
    ("domain", "outlook.live.com", "Communication & Scheduling", 0),
    ("domain", "outlook.office.com", "Communication & Scheduling", 0),
    ("domain", "calendar.google.com", "Communication & Scheduling", 0),
    ("domain", "meet.google.com", "Communication & Scheduling", 0),
    ("domain", "zoom.us", "Communication & Scheduling", 0),
    ("domain", "slack.com", "Communication & Scheduling", 0),
    ("domain", "discord.com", "Communication & Scheduling", 0),
    ("domain", "teams.microsoft.com", "Communication & Scheduling", 0),
    ("domain", "web.whatsapp.com", "Communication & Scheduling", 0),
    # ---------------- Software Development ----------------
    ("domain", "github.com", "Software Development", 2),
    ("domain", "gitlab.com", "Software Development", 2),
    ("domain", "stackoverflow.com", "Software Development", 1),
    ("domain", "stackexchange.com", "Software Development", 1),
    ("domain", "developer.mozilla.org", "Software Development", 1),
    ("domain", "docs.python.org", "Software Development", 1),
    ("domain", "npmjs.com", "Software Development", 1),
    # ---------------- Reference & Learning ----------------
    ("domain", "wikipedia.org", "Reference & Learning", 1),
    ("domain", "khanacademy.org", "Reference & Learning", 1),
    ("domain", "coursera.org", "Reference & Learning", 1),
    ("domain", "udemy.com", "Reference & Learning", 1),
    ("domain", "edx.org", "Reference & Learning", 1),
    ("domain", "medium.com", "Reference & Learning", 1),
    ("domain", "arxiv.org", "Reference & Learning", 1),
    # ---------------- News ----------------
    ("domain", "bbc.com", "News", -1),
    ("domain", "cnn.com", "News", -1),
    ("domain", "nytimes.com", "News", -1),
    ("domain", "theguardian.com", "News", -1),
    ("domain", "reuters.com", "News", -1),
    ("domain", "news.google.com", "News", -1),
    # ---------------- Shopping ----------------
    ("domain", "amazon.com", "Shopping", -1),
    ("domain", "ebay.com", "Shopping", -1),
    ("domain", "aliexpress.com", "Shopping", -1),
    ("domain", "etsy.com", "Shopping", -1),
    # ---------------- Business ----------------
    ("domain", "trello.com", "Business", 1),
    ("domain", "asana.com", "Business", 1),
    ("domain", "atlassian.net", "Business", 1),
    ("domain", "notion.so", "Business", 1),
    ("domain", "sheets.google.com", "Business", 1),
    # ---------------- Design & Composition ----------------
    ("domain", "docs.google.com", "Design & Composition", 2),
    ("domain", "figma.com", "Design & Composition", 2),
    ("domain", "canva.com", "Design & Composition", 2),
    ("domain", "adobe.com", "Design & Composition", 2),
    ("domain", "dribbble.com", "Design & Composition", 1),
    # ---------------- Utilities ----------------
    ("domain", "dropbox.com", "Utilities", 0),
    # ---------------- Desktop apps (exact process names) ----------------
    ("app_exact", "code", "Software Development", 2),
    ("app_exact", "devenv", "Software Development", 2),
    ("app_exact", "excel", "Business", 1),
    ("app_exact", "winword", "Design & Composition", 2),
    ("app_exact", "powerpnt", "Design & Composition", 2),
    ("app_exact", "outlook", "Communication & Scheduling", 0),
    ("app_exact", "thunderbird", "Communication & Scheduling", 0),
    ("app_exact", "sublime_text", "Software Development", 1),
    ("app_exact", "vim", "Software Development", 1),
    ("app_exact", "emacs", "Software Development", 1),
    ("app_exact", "notepad++", "Software Development", 1),
    ("app_exact", "powershell", "Software Development", 1),
    ("app_exact", "alacritty", "Software Development", 1),
    ("app_exact", "kitty", "Software Development", 1),
    ("app_exact", "wezterm", "Software Development", 1),
    ("app_exact", "gnome-terminal", "Software Development", 1),
    # Browsers without a captured URL stay Uncategorized so the user can
    # review them; domain rules above take precedence whenever a URL exists.
    ("app_exact", "chrome", "Uncategorized", 0),
    ("app_exact", "firefox", "Uncategorized", 0),
    ("app_exact", "msedge", "Uncategorized", 0),
    ("app_exact", "brave", "Uncategorized", 0),
    ("app_exact", "safari", "Uncategorized", 0),
    # ---------------- Desktop apps (name contains) ----------------
    ("app_contains", "visual studio code", "Software Development", 2),
    ("app_contains", "pycharm", "Software Development", 2),
    ("app_contains", "intellij", "Software Development", 2),
    ("app_contains", "webstorm", "Software Development", 2),
    ("app_contains", "android studio", "Software Development", 2),
    ("app_contains", "xcode", "Software Development", 2),
    ("app_contains", "slack", "Communication & Scheduling", 0),
    ("app_contains", "discord", "Communication & Scheduling", 0),
    ("app_contains", "zoom", "Communication & Scheduling", 0),
    ("app_contains", "whatsapp", "Communication & Scheduling", 0),
    ("app_contains", "telegram", "Communication & Scheduling", 0),
    ("app_contains", "steam", "Entertainment", -2),
    # ---------------- Window-title keywords ----------------
    ("title_keyword", "pull request", "Software Development", 2),
    ("title_keyword", "meeting", "Communication & Scheduling", 0),
    ("title_keyword", "webinar", "Communication & Scheduling", 0),
    ("title_keyword", "invoice", "Business", 1),
    ("title_keyword", "tutorial", "Reference & Learning", 1),
    ("title_keyword", "lecture", "Reference & Learning", 1),
    ("title_keyword", "gameplay", "Entertainment", -2),
    ("title_keyword", "checkout", "Shopping", -1),
]

# Generic safety-net keywords, checked only when no seed rule matched.
# Each maps a keyword to a category; the category's default score applies.
KEYWORD_FALLBACKS = [
    ("invoice", "Business"),
    ("receipt", "Business"),
    ("budget", "Business"),
    ("spreadsheet", "Business"),
    ("email", "Communication & Scheduling"),
    ("inbox", "Communication & Scheduling"),
    ("chat", "Communication & Scheduling"),
    ("call", "Communication & Scheduling"),
    ("code", "Software Development"),
    ("debug", "Software Development"),
    ("commit", "Software Development"),
    ("server", "Software Development"),
    ("design", "Design & Composition"),
    ("mockup", "Design & Composition"),
    ("presentation", "Design & Composition"),
    ("document", "Design & Composition"),
    ("course", "Reference & Learning"),
    ("documentation", "Reference & Learning"),
    ("wiki", "Reference & Learning"),
    ("headlines", "News"),
    ("discount", "Shopping"),
    ("cart", "Shopping"),
    ("movie", "Entertainment"),
    ("music", "Entertainment"),
]
