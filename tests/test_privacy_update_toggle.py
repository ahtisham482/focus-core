"""Roadmap 0.2: PRIVACY.md honesty + update-check toggle (TDD)."""

from datetime import date

import pytest

from focuscore import store


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _fake_status(available=False):
    return {
        "status": "ok",
        "current": "1.0.0",
        "latest": "1.1.0" if available else "1.0.0",
        "update_available": available,
        "asset": None,
        "error": "",
    }


@pytest.fixture()
def no_update(monkeypatch):
    import focuscore.updater as updater_mod
    monkeypatch.setattr(updater_mod, "check_for_update",
                        lambda force=False: _fake_status(False))


# --------------------------------------------------------------------------
# toggle endpoint
# --------------------------------------------------------------------------

def test_toggle_endpoint_turns_off(dash):
    resp = dash.post("/update/check-toggle",
                     data={"update_check_enabled": "0"})
    assert resp.status_code == 302
    assert store.get_setting("update_check_enabled") == "0"


def test_toggle_endpoint_turns_on(dash):
    store.set_setting("update_check_enabled", "0")
    resp = dash.post("/update/check-toggle",
                     data={"update_check_enabled": "1"})
    assert resp.status_code == 302
    assert store.get_setting("update_check_enabled") == "1"


def test_toggle_endpoint_rejects_garbage(dash):
    # Hand-crafted garbage input fails safe: checks go off, never on.
    resp = dash.post("/update/check-toggle",
                     data={"update_check_enabled": "yes-please"})
    assert resp.status_code == 302
    assert store.get_setting("update_check_enabled") == "0"


def test_toggle_redirects_back_to_update_page(dash):
    resp = dash.post("/update/check-toggle",
                     data={"update_check_enabled": "0"})
    assert resp.headers["Location"].endswith("/update")


def test_toggle_missing_field_fails_safe_to_off(dash):
    # LOW-1: a field-less POST must resolve to OFF -- the endpoint's
    # documented contract is "garbage input fails safe to off". Before the
    # fix, request.form.get defaulted to "1" and silently turned checks ON.
    resp = dash.post("/update/check-toggle", data={})
    assert resp.status_code == 302
    assert store.get_setting("update_check_enabled") == "0"


def test_toggle_empty_field_fails_safe_to_off(dash):
    resp = dash.post("/update/check-toggle",
                     data={"update_check_enabled": ""})
    assert resp.status_code == 302
    assert store.get_setting("update_check_enabled") == "0"


# --------------------------------------------------------------------------
# page reflects toggle state
# --------------------------------------------------------------------------

def test_update_page_shows_toggle_on_by_default(dash, no_update):
    html = dash.get("/update").data.decode()
    assert "Automatic update checks" in html
    assert "update-check-toggle" in html
    assert "Turn off" in html


def test_update_page_shows_toggle_off_after_disabling(dash, monkeypatch):
    # LOW-3: after disabling, opening /update must show the toggle again
    # WITHOUT any updater network call -- with no cached check, the page
    # renders the "not checked yet" state from the cache alone.
    from focuscore import updater as updater_mod
    monkeypatch.setattr(updater_mod, "get_update_info",
                        lambda: {"repo": "o/n", "version": "1.0.0"})
    monkeypatch.setattr(updater_mod, "read_cached_check", lambda: None)
    dash.post("/update/check-toggle",
              data={"update_check_enabled": "0"})
    html = dash.get("/update").data.decode()
    assert "Automatic update checks" in html
    assert "Not checked yet" in html
    assert "Turn on" in html


# --------------------------------------------------------------------------
# background check honors the toggle
# --------------------------------------------------------------------------

def test_background_check_runs_when_enabled(tmp_path, monkeypatch):
    db = str(tmp_path / "b.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    import focuscore.launcher as launcher_mod
    import focuscore.updater as updater_mod
    calls = []
    monkeypatch.setattr(updater_mod, "check_for_update",
                        lambda: calls.append(1))
    launcher_mod._background_update_check()
    assert calls == [1]


def test_background_check_skips_when_disabled(tmp_path, monkeypatch):
    db = str(tmp_path / "b.db")
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", db)
    store.set_setting("update_check_enabled", "0")
    import focuscore.launcher as launcher_mod
    import focuscore.updater as updater_mod
    calls = []
    monkeypatch.setattr(updater_mod, "check_for_update",
                        lambda: calls.append(1))
    launcher_mod._background_update_check()
    assert calls == []


def test_background_check_never_raises_on_bad_db(tmp_path, monkeypatch):
    # A corrupted settings path must not break app launch.
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", "/nonexistent/dir/x.db")
    import focuscore.launcher as launcher_mod
    launcher_mod._background_update_check()  # must not raise


# --------------------------------------------------------------------------
# LOW-3: opening /update with checks OFF never touches the network
# --------------------------------------------------------------------------

@pytest.fixture()
def urlopen_spy(monkeypatch):
    """Every updater network entry point recorded; none may fire unless
    a test explicitly sets ``allow = True``."""
    import json
    import urllib.request

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, *args):
            return json.dumps({
                "tag_name": "1.0.0",
                "assets": [
                    {"name": "FocusCore-Setup-1.0.0.exe",
                     "browser_download_url": "u", "size": 1},
                    {"name": "SHA256SUMS",
                     "browser_download_url": "c"},
                ],
            }).encode()

    class _Spy:
        def __init__(self):
            self.calls = []
            self.allow = False

        def __call__(self, *args, **kwargs):
            self.calls.append(args)
            if not self.allow:
                raise AssertionError(
                    "urlopen must not be called on a plain page load "
                    "while automatic checks are off")
            return _Resp()

    spy = _Spy()
    monkeypatch.setattr(urllib.request, "urlopen", spy)
    return spy


def test_update_page_load_checks_off_fresh_cache_no_network(
        dash, urlopen_spy, monkeypatch):
    # Toggle OFF + fresh cached "ok" -> served from cache, zero network.
    import time

    from focuscore import updater as updater_mod
    monkeypatch.setattr(updater_mod, "get_update_info",
                        lambda: {"repo": "o/n", "version": "1.0.0"})
    monkeypatch.setattr(updater_mod, "read_cached_check", lambda: {
        "status": "ok", "current": "1.0.0", "latest": "1.0.0",
        "update_available": False,
        "asset": {"checksums_url": "u"},
        "checked_at": time.time()})
    store.set_setting("update_check_enabled", "0")
    resp = dash.get("/update")
    assert resp.status_code == 200
    assert "Automatic checks are off" in resp.data.decode()
    assert urlopen_spy.calls == []


def test_update_page_load_checks_off_stale_cache_no_network(
        dash, urlopen_spy, monkeypatch):
    # Toggle OFF + STALE cached "ok" -> still rendered from the last known
    # state, zero network (regression test for LOW-3).
    from focuscore import updater as updater_mod
    monkeypatch.setattr(updater_mod, "get_update_info",
                        lambda: {"repo": "o/n", "version": "1.0.0"})
    monkeypatch.setattr(updater_mod, "read_cached_check", lambda: {
        "status": "ok", "current": "1.0.0", "latest": "1.0.0",
        "update_available": False,
        "asset": {"checksums_url": "u"},
        "checked_at": 0})
    store.set_setting("update_check_enabled", "0")
    resp = dash.get("/update")
    assert resp.status_code == 200
    assert "Automatic checks are off" in resp.data.decode()
    assert urlopen_spy.calls == []


def test_update_page_load_checks_off_no_cache_no_network(
        dash, urlopen_spy, monkeypatch):
    # Toggle OFF + no cache at all -> "not checked yet" state, zero network.
    from focuscore import updater as updater_mod
    monkeypatch.setattr(updater_mod, "get_update_info",
                        lambda: {"repo": "o/n", "version": "1.0.0"})
    monkeypatch.setattr(updater_mod, "read_cached_check", lambda: None)
    store.set_setting("update_check_enabled", "0")
    resp = dash.get("/update")
    assert resp.status_code == 200
    assert "Not checked yet" in resp.data.decode()
    assert urlopen_spy.calls == []


def test_update_page_load_checks_off_error_cache_shows_last_known(
        dash, urlopen_spy, monkeypatch):
    # Toggle OFF + cached error outcome (e.g. offline at last check) ->
    # shown honestly as last known, never re-fetched on a page load.
    from focuscore import updater as updater_mod
    monkeypatch.setattr(updater_mod, "get_update_info",
                        lambda: {"repo": "o/n", "version": "1.0.0"})
    monkeypatch.setattr(updater_mod, "read_cached_check", lambda: {
        "status": "error", "error": "No internet connection.",
        "current": "1.0.0", "checked_at": 0})
    store.set_setting("update_check_enabled", "0")
    resp = dash.get("/update")
    assert resp.status_code == 200
    assert "Couldn't check for updates" in resp.data.decode()
    assert urlopen_spy.calls == []


def test_update_page_load_checks_off_update_available_no_network(
        dash, urlopen_spy, monkeypatch):
    # Toggle OFF + cached "update available" -> still offered from the last
    # known state, zero network.
    from focuscore import updater as updater_mod
    monkeypatch.setattr(updater_mod, "get_update_info",
                        lambda: {"repo": "o/n", "version": "1.0.0"})
    monkeypatch.setattr(updater_mod, "read_cached_check", lambda: {
        "status": "ok", "current": "1.0.0", "latest": "1.1.0",
        "update_available": True,
        "asset": {"name": "FocusCore-Setup-1.1.0.exe", "url": "u",
                  "size": 1, "checksums_url": "u"},
        "checked_at": 0})
    store.set_setting("update_check_enabled", "0")
    resp = dash.get("/update")
    assert resp.status_code == 200
    assert "Version 1.1.0 is available" in resp.data.decode()
    assert urlopen_spy.calls == []


def test_update_page_refresh_forces_check_when_toggle_off(dash, urlopen_spy,
                                                         monkeypatch):
    # The manual "Check again" action (force path) still works when OFF:
    # it is the user's explicit click, not a plain page load.
    from focuscore import updater as updater_mod
    monkeypatch.setattr(updater_mod, "get_update_info",
                        lambda: {"repo": "o/n", "version": "1.0.0"})
    monkeypatch.setattr(updater_mod, "read_cached_check", lambda: None)
    called = []

    def _latest(repo):
        called.append(repo)
        return ("1.0.0", "FocusCore-Setup-1.0.0.exe", "u", 1, "c")

    monkeypatch.setattr(updater_mod, "latest_release", _latest)
    store.set_setting("update_check_enabled", "0")
    resp = dash.get("/update?refresh=1")
    assert resp.status_code == 200
    assert called == ["o/n"]


def test_update_page_load_checks_on_stale_cache_still_checks(
        dash, urlopen_spy, monkeypatch):
    # Toggle ON: today's behavior is unchanged -- a stale cache on a page
    # load refreshes via check_for_update (the spy proves the network call).
    from focuscore import updater as updater_mod
    monkeypatch.setattr(updater_mod, "get_update_info",
                        lambda: {"repo": "o/n", "version": "1.0.0"})
    monkeypatch.setattr(updater_mod, "read_cached_check", lambda: {
        "status": "ok", "current": "1.0.0", "latest": "1.0.0",
        "update_available": False,
        "asset": {"checksums_url": "u"},
        "checked_at": 0})
    store.set_setting("update_check_enabled", "1")
    urlopen_spy.allow = True
    resp = dash.get("/update")
    assert resp.status_code == 200
    assert len(urlopen_spy.calls) == 1


# --------------------------------------------------------------------------
# PRIVACY.md honesty
# --------------------------------------------------------------------------

def _privacy_text():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    return (root / "PRIVACY.md").read_text(encoding="utf-8")


def test_privacy_no_longer_claims_no_internet_calls():
    text = _privacy_text().lower()
    assert "makes no internet calls" not in text
    assert "no internet calls" not in text
    assert "check for updates over the network" not in text


def test_privacy_documents_the_three_outbound_flows():
    text = _privacy_text()
    assert "api.github.com" in text
    assert "once a day" in text.lower()
    assert "calendar" in text.lower()
    assert "installer" in text.lower()


def test_privacy_documents_the_toggle():
    text = _privacy_text().lower()
    assert "update_check_enabled" not in text  # plain English, no key names
    assert "turn" in text and "automatic" in text


def test_privacy_date_is_current():
    text = _privacy_text()
    marker = "Last updated "
    assert marker in text
    doc_date = date.fromisoformat(text.split(marker, 1)[1].split()[0].strip("."))
    today = date.today()
    # PRIVACY.md is re-dated by hand when edited, in the editor's
    # timezone (Asia/Karachi), while CI runners use UTC — so allow a
    # day of skew. The guard's real job: the date exists, parses, is
    # not from the future, and is not left stale for over a year.
    # (The previous same-day equality version went red on every
    # UTC/PKT day boundary — reproduced on CI for 8c77098.)
    assert (doc_date - today).days <= 1
    assert (today - doc_date).days <= 366


def test_privacy_does_not_claim_version_is_sent():
    # MEDIUM-1: the update check sends only IP + the updater User-Agent
    # (User-Agent: FocusCore-Updater). No app version ever leaves the PC.
    text = _privacy_text().lower()
    assert "the app version" not in text
    assert "ip address" in text
    assert "user-agent" in text


def test_privacy_crash_report_sentence_is_honest():
    # Roadmap 2.5 promise change (owner-mandated): after an unclean
    # shutdown the app may prepare a three-fact crash report LOCALLY and
    # offer it, but never sends it. The old absolute "no crash
    # reporting" sentence cannot survive that feature honestly, so the
    # qualified sentence below is the promise now -- pin it.
    text = _privacy_text()
    flat = " ".join(text.split())  # the sentence may wrap across lines
    low = flat.lower()
    assert "no crash reporting" not in low
    assert "nothing is ever sent automatically" in flat
    assert "error type only" in flat
    assert "choose to send yourself" in flat


def test_privacy_drive_backup_described_truthfully():
    # MEDIUM-2: no in-app Drive toggle exists -- whenever Google Drive for
    # Desktop is installed, backups are copied into the Drive folder
    # automatically (startup / pre-update / update-start backups included).
    text = _privacy_text().lower()
    assert "drive for desktop" in text
    assert "automatically" in text
    assert "pre-update" in text
    assert "no in-app" in text


def test_privacy_blanket_claims_qualify_drive_backup():
    # MEDIUM-2: the short-version blanket sentence must point at the Drive
    # backup section instead of implying only three internet uses exist.
    text = _privacy_text().lower()
    assert "besides the google drive backup" in text
    assert "optional drive backup" in text


def test_toggle_card_copy_matches_what_is_sent(monkeypatch, tmp_path):
    # MEDIUM-1 (card copy): the Updates-page toggle card must describe the
    # same request as PRIVACY.md -- IP + the updater User-Agent, no version.
    monkeypatch.setattr(store, "DEFAULT_DB_PATH", str(tmp_path / "u.db"))
    import dashboard.routes.system as system_mod
    card = system_mod._update_toggle_card_html().lower()
    assert "the app version" not in card
    assert "ip address" in card
    assert "user-agent" in card
