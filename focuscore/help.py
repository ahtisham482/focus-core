"""In-app help: one plain-English article per dashboard page.

Pure content + HTML builders, no Flask — dashboard/app.py wraps these
bodies in its page layout and serves them at /help and /help/<key>.

Each article answers three questions a first-time user has:
  what  — what is this page for?
  do    — what do I do here?
  trouble — if something looks wrong, what is it probably?
"""

# Article key -> content. "href" is where the "back" link points.
ARTICLES = {
    "home": {
        "title": "Home",
        "href": "/",
        "what": ("Your command center. Today's Productivity Pulse (0-100), "
                 "your tracked and focused hours, and a \"What needs your "
                 "attention\" list. Each item on the list has exactly one "
                 "button, so you always know the next step."),
        "do": [
            "Glance at your Pulse: green (60+) is a good day, amber (40-59) "
            "is okay, red (below 40) is a distracted day.",
            "Work through the attention list from top to bottom — it is "
            "ordered by importance.",
            "Pin your most important goals on the Goals page to see them "
            "here.",
        ],
        "trouble": [
            "No data at all? ActivityWatch isn't running — the attention "
            "list will say so, with a button to set it up.",
            "Pulse shows \"--\"? Nothing has been tracked yet today.",
        ],
    },
    "day": {
        "title": "Day details",
        "href": "/",
        "what": ("The full breakdown of one day: Pulse, time by category "
                 "and score level, and the uncategorized queue — activities "
                 "Focus Core hasn't learned to score yet."),
        "do": [
            "Check which categories ate your hours.",
            "Work the uncategorized queue: set the real score once and "
            "Focus Core remembers it forever.",
            "Click any activity's score to override it — your override "
            "always wins.",
        ],
        "trouble": [
            "\"No tracked data for this day yet\" and ActivityWatch isn't "
            "running? Nothing was recorded — set it up first.",
        ],
    },
    "activities": {
        "title": "Review activities",
        "href": "/activities",
        "what": ("Every tracked activity for the day, with its score. New "
                 "apps and websites start as Neutral until you teach Focus "
                 "Core what they really are."),
        "do": [
            "Give new apps their real score with the dropdown on each row.",
            "Your scores are remembered: the same app is scored "
            "automatically next time.",
            "Come here whenever Home says activities need categories.",
        ],
        "trouble": [
            "An app shows as \"unknown\"? ActivityWatch must run in your "
            "normal Windows session to see window titles.",
        ],
    },
    "goals": {
        "title": "Goals",
        "href": "/goals",
        "what": ("Daily targets you set for yourself. Each goal is \"more "
                 "than X\" (do at least this much) or \"less than Y\" (stay "
                 "under this) — measured in minutes of a category or in "
                 "Pulse points."),
        "do": [
            "Add a goal: choose more/less than, pick a category or Pulse, "
            "set the target.",
            "Pin the important ones — pinned goals appear on Home.",
            "Progress is live: the page re-reads today's tracked data "
            "every time it loads.",
        ],
        "trouble": [
            "A goal sits at 0? Nothing tracked yet today, or ActivityWatch "
            "isn't running.",
        ],
    },
    "alerts": {
        "title": "Alerts",
        "href": "/alerts",
        "what": ("Pop-up warnings when today's time on a category or a "
                 "single activity passes your limit. For example: warn me "
                 "after 30 minutes of Entertainment."),
        "do": [
            "Add an alert: pick a category or an activity, set the minutes.",
            "Each alert waits a cooldown before it can fire again, so it "
            "never spams you.",
            "Toggle alerts on and off without deleting them.",
        ],
        "trouble": [
            "No pop-ups? Make sure Focus Core is running and Windows "
            "notifications are turned on (open Settings, then System, "
            "then Notifications).",
        ],
    },
    "focus": {
        "title": "Focus sessions",
        "href": "/focus",
        "what": ("Timed work blocks with distraction blocking. Pick what "
                 "you're working on and how long, work, and get a summary: "
                 "focus minutes, Pulse, blocked distractions. Completed "
                 "sessions build your streak — days in a row with at least "
                 "one finished session. Three modes: Classic (fixed timer), "
                 "Flowtime (no fixed end — work until a natural break), "
                 "and Smart Pomodoro (work/break cycles whose lengths "
                 "adapt to your own focus history)."),
        "do": [
            "Type your task, pick 25, 50 or 90 minutes (or any custom "
            "length), choose a mode, and start. The suggestion card "
            "above the form shows lengths learned from your history.",
            "Strict mode blocks -1 (Personal) and -2 (Distracting) apps; "
            "lenient mode blocks only -2.",
            "In Pomodoro, blocking rests during breaks (only gentle "
            "reminders); breaks end by themselves after 30 minutes at "
            "most. You can always end or skip a break early.",
            "End the session to see your summary.",
            "Press Z any time to toggle zen mode; Escape leaves it. "
            + "All shortcuts are listed under Help, Keyboard shortcuts.",
        ],
        "trouble": [
            "Blocking didn't trigger? Keep Focus Core running during the "
            "session — the guard watches while you work.",
            "An app you need got blocked? Re-score it as productive on "
            "the Review page. Your override always wins and it will never "
            "be blocked again.",
            "Can't start a session? Finish or end the one already running "
            "first.",
            "Woke the PC from sleep during a session? Blocking waits 60 "
            "seconds before coming back, so you are never ambushed.",
            "Transition sounds annoy you? Untick 'Sound cues' on the "
            "Focus page — they are optional and never block anything.",
        ],
    },
    "shield": {
        "title": "Shield",
        "href": "/shield",
        "what": ("The hardcore distraction blocker. It watches which "
                 "window is active and steps in at three levels: Soft "
                 "reminds you, Firm reminds and minimizes the window, "
                 "Hardcore minimizes and locks a full-screen note for "
                 "30 seconds. Rules keep working outside focus sessions "
                 "-- for example, no social media on weekday work hours. "
                 "The small HUD in the corner always shows the shield's "
                 "state."),
        "do": [
            "Add a rule: name it, pick app/website or category, choose "
            "Soft/Firm/Hardcore, and set when it applies (days and "
            "hours, or leave it always on).",
            "Need a few minutes for something urgent? Start an "
            "emergency pass — it pauses the shield and is always "
            "logged, so use it honestly.",
            "Use the tray menu to turn the shield on or off and to "
            "show or hide the HUD.",
        ],
        "trouble": [
            "Shield not stopping anything? Make sure it is running on "
            "the Shield page, and check the rule's schedule — a rule "
            "set to 09:00-18:00 does nothing at 20:00.",
            "Windows system apps, the lock screen and installers are "
            "never touched, on purpose.",
            "Stuck in a Hardcore lock? Wait out the 30 seconds, end "
            "the session, or start an emergency pass from the tray. "
            "Ctrl+Alt+Del always works.",
        ],
    },
    "timesheet": {
        "title": "Timesheet",
        "href": "/timesheet",
        "what": ("Turns your tracked day into clean time entries with "
                 "project, client and task tags. The top of the page "
                 "suggests blocks on a visual timeline — click a block to "
                 "highlight its row. Projects can have hourly rates and "
                 "weekly/monthly budgets, and any date range can be "
                 "exported for clients."),
        "do": [
            "Review the suggested blocks, then Accept the good ones.",
            "Tag each entry with project, client, task and a note.",
            "Add entries by hand for time the tracker missed.",
            "Set an hourly rate on a project — new entries use it "
            "automatically, and old entries keep whatever rate they had.",
            "Set weekly/monthly budgets (hours and/or money) per project. "
            "Bars show burndown; budgets are advisory only and every "
            "change is kept in history.",
            "Lock the day when it looks right — locked days can't be "
            "changed, so your records stay trustworthy.",
            "Export any date range: client CSV (safe by default), "
            "structured JSON, or a printable Timesheet Statement.",
        ],
        "trouble": [
            "Tiny blocks missing? Anything under 5 minutes is skipped as "
            "noise — that's normal.",
            "A gap of more than 5 minutes starts a new block.",
            "Billed total looks low? Time with no confirmed rate is listed "
            "separately as unrated — set a rate or apply it to old "
            "entries from the project card.",
            "Client exports never include app names or window titles "
            "unless you explicitly choose the detailed internal CSV.",
        ],
    },
    "backup": {
        "title": "Backup",
        "href": "/backup",
        "what": ("Your safety net. Focus Core makes an automatic backup "
                 "every day (using SQLite's safe backup, so it works while "
                 "the app is running). If Google Drive for Desktop is "
                 "installed, backups go to a \"Focus Core Backups\" folder "
                 "in your Drive; otherwise they stay in a local folder."),
        "do": [
            "Check where your backups go and when the last one was made.",
            "Click \"Back up now\" any time.",
            "Restore any backup from the list — your current data is "
            "first copied to a safety file, so a restore can never lose "
            "anything.",
        ],
        "trouble": [
            "A restore was refused? That backup's checksum didn't match "
            "(damaged file) — pick the next-newest backup instead.",
            "Moving to a new laptop? Install Focus Core there, copy your "
            "newest backup over, and restore it from this page.",
        ],
    },
    "report": {
        "title": "Weekly report",
        "href": "/report",
        "what": ("Any week's summary, Monday to Sunday: total tracked "
                 "hours, average Pulse, per-day hours and Pulse, your 5 "
                 "biggest categories, each goal's hit-rate, and your focus "
                 "sessions."),
        "do": [
            "Pick a week — it defaults to the current one.",
            "Compare weeks to see trends, not single days.",
        ],
        "trouble": [
            "A week looks empty? No tracked data that week — check "
            "ActivityWatch was running.",
            "Days with no data are skipped in averages, never counted as "
            "zero.",
        ],
    },
    "coaching": {
        "title": "Focus coaching",
        "href": "/coaching",
        "what": ("Shows WHEN you focus best: a 7-day hourly heatmap (green "
                 "= focused, red = distracted), your top 3 two-hour focus "
                 "windows, and gentle warnings — late nights, marathon "
                 "days, distraction creep, low recovery."),
        "do": [
            "Find your green hours and protect them for hard work.",
            "Read warnings as nudges, not grades.",
        ],
        "trouble": [
            "Heatmap mostly empty? It needs several days of tracked data "
            "before it's useful.",
            "Some warnings need 14 days of data and stay silent until "
            "then.",
        ],
    },
    "intelligence": {
        "title": "Deep time",
        "href": "/intelligence",
        "what": ("Answers three deeper questions: WHEN you are at your best "
                 "(your chronotype — morning person, night owl, or balanced "
                 "-- plus a rhythm grid for each weekday), HOW DEEP you go "
                 "(your longest unbroken focus stretches, how fast you get "
                 "into focus, how often you switch apps), and WHAT breaks "
                 "your focus (top distractors and the apps that pull you "
                 "into them). It also has an interactive timeline: click "
                 "any hour of the day to see the activities inside it."),
        "do": [
            "Read your chronotype, then schedule hard work inside your "
            "peak hours.",
            "Check \"Protect these hours\" for each weekday's best 2-hour "
            "block and guard it like a meeting.",
            "Use \"What breaks your focus\" to spot your biggest "
            "distractors — the entry points show what pulls you in.",
            "Click an hour in the Day timeline to inspect what actually "
            "happened inside it.",
        ],
        "trouble": [
            "\"Not enough data yet\"? The rhythm, peaks, and depth cards "
            "need several days of tracked history before they're useful.",
            "Week trends look flat? They compare this week (Monday to "
            "today) with last week — early in the week there is less to "
            "compare.",
        ],
    },
    "update": {
        "title": "Updates",
        "href": "/update",
        "what": ("Checks whether a newer Focus Core is available and "
                 "installs it in one click. The app also checks by itself "
                 "once a day — this page is for checking right now."),
        "do": [
            "Press \"Check now\" any time.",
            "\"Update now\" backs up your data first, then downloads and "
            "installs the new version silently and reopens the app.",
        ],
        "trouble": [
            "\"Couldn't check for updates\"? The check needs internet "
            "access to the releases page — it fails gracefully and "
            "nothing breaks.",
            "Update refused? Finish or end your active focus session "
            "first — updating mid-session is blocked on purpose.",
        ],
    },
    "setup": {
        "title": "Set up ActivityWatch",
        "href": "/setup/activitywatch",
        "what": ("Connects Focus Core to ActivityWatch — the free, "
                 "open-source tracker Focus Core reads your activity from. "
                 "Without it running, there is nothing to score."),
        "do": [
            "Follow the 3 steps: download, run the installer, start it "
            "from the Start menu.",
            "Press \"Check again\" — the page re-checks live.",
        ],
        "trouble": [
            "\"Installed but not running\"? Open it from the Start menu.",
            "Tip: let ActivityWatch start with Windows (in its own "
            "settings) so you never think about it again.",
        ],
    },
    "welcome": {
        "title": "Welcome tour",
        "href": "/welcome",
        "what": ("Three short screens that explain the whole idea: your "
                 "data stays on this PC, ActivityWatch does the tracking, "
                 "and scores run from -2 to +2."),
        "do": [
            "Retake it any time from the \"Take the tour again\" link in "
            "the footer of every page.",
        ],
        "trouble": [],
    },
    "invoices": {
        "title": "Invoices",
        "href": "/invoices",
        "what": ("Turns your timesheet entries into client invoices. "
                 "Pick uninvoiced entries for a project and a date range, "
                 "get a draft you can still edit, then send it — sending "
                 "numbers it (INV-2026-0001) and freezes it forever. "
                 "Sent invoices can only be paid or voided, never edited."),
        "do": [
            "Click New invoice, pick a project and date range, tick the "
            "entries, and create the draft.",
            "Check the draft lines, tax and discount, then Send invoice. "
            "It gets its number only at this moment.",
            "Record payments on a sent invoice. A full payment marks it "
            "paid automatically.",
            "Print / save PDF for a clean invoice to send to your client.",
            "Made a mistake on a sent invoice? Void it (with a reason) and "
            "reissue a corrected draft in one step.",
        ],
        "trouble": [
            "An entry says it has no rate? Set an hourly rate on the "
            "project first (Timesheet page), then try again.",
            "Entries in different currencies can't share one invoice — "
            "make one invoice per currency.",
            "A sent invoice can't be edited, by design. Void and reissue "
            "instead; the old invoice stays in history.",
            "Overpaid? The balance shows 0 and the extra is flagged as an "
            "informational warning — the full payment amount is still "
            "recorded.",
        ],
    },
    "shortcuts": {
        "title": "Keyboard shortcuts",
        "href": "/help/shortcuts",
        # Explicit + joins: implicit multi-line concatenation inside a
        # collection trips the ruff ratchet (ISC004).
        "what": ("The few keyboard shortcuts Focus Core has. "
                 + "Everything else is buttons and links, so a mouse or "
                 + "touch works everywhere."),
        "do": [
            "Press Z to switch zen mode on or off. "
            + "Zen hides everything except the work in front of you. "
            + "It does nothing while you are typing in a text field.",
            "Press Escape to leave zen mode, or to close any open "
            + "collapsible section and jump back to its heading.",
            "On option chips (for example the focus-mode picker), use "
            + "the Left and Right arrow keys to move between choices. "
            + "Up and Down work too.",
            "Charts with more than one data point have a View as table "
            + "button that opens the same numbers as a plain table. "
            + "Single-number indicators don't need one - their numbers "
            + "are already shown as text.",
            "Text size (Small, Default, Large) is in the top bar next "
            + "to the light and dark mode buttons.",
        ],
        "trouble": [
            "Pressed Z and nothing happened? Click outside any text "
            + "field first — shortcuts stay quiet while you type.",
        ],
    },
}

# Dashboard nav key -> help article key (most are identical).
NAV_HELP = {
    "home": "home",
    "timesheet": "timesheet",
    "invoices": "invoices",
    "report": "report",
    "coaching": "coaching",
    "intelligence": "intelligence",
    "focus": "focus",
    "shield": "shield",
    "goals": "goals",
    "alerts": "alerts",
    "backup": "backup",
    "review": "activities",
}


def get_article(key):
    """The article dict for ``key``, or None."""
    return ARTICLES.get(key)


def _one_liner(text):
    """First sentence of a help blurb, for the scannable index."""
    head = text.split(". ", 1)[0].strip()
    return head if head.endswith(".") else head + "."


def article_html(key):
    """Full body HTML for one help article, or None for unknown keys."""
    article = get_article(key)
    if article is None:
        return None
    do_html = "".join("<li>%s</li>" % item for item in article["do"])
    trouble_html = "".join("<li>%s</li>" % item for item in article["trouble"])
    trouble_section = (
        "<h3>If something looks wrong</h3><ul>%s</ul>" % trouble_html
        if trouble_html else "")
    # Answer first (the "what" as a lead), details after.
    return (
        "<p><a href='%s'>&larr; Back to %s</a></p>"
        "<article class='wk-help'>"
        "<p class='wk-help-lead'>%s</p>"
        "<h3>What to do here</h3><ul>%s</ul>%s</article>"
        "<p><a href='/help'>All help articles</a></p>"
        % (article["href"], article["title"],
           article["what"], do_html, trouble_section))


def index_html():
    """Body HTML listing every help article, one plain-English line each."""
    items = "".join(
        "<li class='wk-help-item'><a href='/help/%s'><b>%s</b></a>"
        "<span>%s</span></li>" % (
            key, article["title"], _one_liner(article["what"]))
        for key, article in ARTICLES.items())
    return (
        "<section class='wk-section'>"
        "<p class='wk-help-lead'>Every page in Focus Core has its own short "
        "guide \u2014 what the page is for, what to do there, and what to "
        "check when something looks wrong. The footer of every page links "
        "straight to its guide.</p>"
        "<ul class='wk-help-list'>%s</ul></section>" % items)
