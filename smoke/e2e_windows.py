"""Human-like UI test for the installed Focus Core app.

Drives the real running app in a real Chromium like a person would:
navigate, read, fill forms, accept dialogs, assert what is visible,
and screenshot every step. Also fails on any JavaScript console error
or page error -- the closest thing to "the app looked broken" that a
script can catch.

Runs on the Windows smoke-test runner against the INSTALLED app
(http://127.0.0.1:5000), but also works locally against a dev server:

    python smoke/e2e_windows.py --base-url http://127.0.0.1:5000

Safe flows only: nothing here starts blocking, sends notifications,
or touches the user's real data -- the runner is a throwaway machine,
and the goal it creates is deleted again at the end.
"""

import argparse
import sys
from pathlib import Path

MARKER = "E2E-MARKER-GOAL"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:5000")
    ap.add_argument("--shots-dir", default="e2e-shots")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright, expect

    shots = Path(args.shots_dir)
    shots.mkdir(parents=True, exist_ok=True)
    failures = []
    js_errors = []

    def check(name, fn):
        try:
            fn()
            print("PASS: %s" % name)
        except Exception as exc:  # noqa: BLE001 -- collect, report all
            failures.append("%s: %s" % (name, exc))
            print("FAIL: %s: %s" % (name, exc))

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.on("console", lambda m: js_errors.append(m.text)
                if m.type == "error" else None)
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        # The delete-goal form uses a JS confirm(); a person clicks OK.
        page.on("dialog", lambda d: d.accept())

        def shot(name):
            page.screenshot(path=str(shots / name))
            print("shot: %s" % name)

        def home():
            page.goto(args.base_url + "/", wait_until="networkidle")
            expect(page.get_by_text("Focus Core").first).to_be_visible(
                timeout=15000)
            shot("01-home.png")

        def focus_page():
            page.goto(args.base_url + "/focus",
                      wait_until="networkidle")
            expect(page.locator("h1")).to_contain_text(
                "Focus sessions", timeout=15000)
            shot("02-focus.png")

        def goals_crud():
            page.goto(args.base_url + "/goals",
                      wait_until="networkidle")
            # Create a goal the way a person does: fill the form, submit.
            page.fill("input[name=name]", MARKER)
            page.fill("input[name=threshold]", "70")
            page.click("button:has-text('Add goal')")
            page.wait_for_url("**/goals", timeout=15000)
            expect(page.get_by_text(MARKER)).to_be_visible(timeout=15000)
            shot("03-goal-created.png")
            # Delete it again via its own Delete button (confirm dialog
            # is auto-accepted above, like a person clicking OK).
            row = page.locator(".goal-row", has_text=MARKER)
            expect(row).to_have_count(1, timeout=15000)
            row.get_by_role("button", name="Delete").click()
            page.wait_for_url("**/goals", timeout=15000)
            expect(page.get_by_text(MARKER)).to_have_count(0,
                                                           timeout=15000)
            shot("04-goal-deleted.png")

        def timesheet():
            page.goto(args.base_url + "/timesheet",
                      wait_until="networkidle")
            expect(page.get_by_text("Timesheet").first).to_be_visible(
                timeout=15000)
            shot("05-timesheet.png")

        def help_article():
            page.goto(args.base_url + "/help", wait_until="networkidle")
            links = page.locator("a[href^='/help/']")
            n = links.count()
            assert n > 0, "no help articles found"
            links.first.click()
            page.wait_for_url("**/help/*", timeout=15000)
            shot("06-help-article.png")

        for name, fn in [("home loads", home),
                         ("focus page loads", focus_page),
                         ("goals create+delete", goals_crud),
                         ("timesheet loads", timesheet),
                         ("help article opens", help_article)]:
            check(name, fn)

        browser.close()

    if js_errors:
        failures.append("JS errors on page: %s" % "; ".join(js_errors[:5]))
        print("FAIL: JS errors: %s" % js_errors[:5])

    if failures:
        print("\n%d FAILURE(S):" % len(failures))
        for f in failures:
            print(" - %s" % f)
        return 1
    print("\nAll UI checks passed, no JS errors.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
