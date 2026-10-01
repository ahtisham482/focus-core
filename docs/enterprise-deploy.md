# Enterprise deployment

Notes for IT administrators who deploy Focus Core to managed Windows
machines. This page currently covers one topic: pinning the fleet to a
fixed version by disabling the app's self-updater.

## Pin the fleet: disable self-updates (machine policy)

> "IT can pin the fleet to version X and the app's self-updater will
> never fight SCCM/Intune."

### Set the policy

Create this registry value on each managed machine (or push it with
Group Policy, SCCM, or Intune):

- Key: `HKEY_LOCAL_MACHINE\Software\Focus Core\Features`
- Value: `updates_disabled` (DWORD) = `1`

For evaluation on a single machine, the same switch exists as
`updates_disabled = true` in the `[features]` table of `config.toml`,
or as the `FOCUSCORE_FEATURE_UPDATES_DISABLED=1` environment variable.
On managed machines the registry value is the one that counts: the
machine tier beats the user's config file and the in-app automatic-
checks toggle, so a user cannot turn updates back on.

The policy is read fresh at every update check — set it before or
after install, no reinstall needed.

### What the app does while the policy is on

- The daily background update check never happens.
- The Updates page shows **"Updates are disabled by your IT
  policy"** and offers the user no way to check or update.
- The manual "Check again" action is refused the same way — it goes
  through the same code path as the automatic check.
- Starting an update (the "Update now" flow) is refused before
  anything is downloaded or staged.
- "Revert to previous version" (the rollback the Updates page offers
  after an update) is refused too — while the pin is on, the app does
  not change versions in either direction.
- An update that was downloaded and queued before the pin is held,
  not applied. If the policy is later removed, normal behavior
  resumes, including that queued update.
- Nothing about the rest of the app changes. Focus Core keeps
  working locally exactly as before; the policy touches only
  self-updates.

### Acceptance procedure (run by IT)

1. Deploy the chosen version with the policy set as above.
2. Confirm the pin took effect: open the Updates page on one
   machine — it shows "Updates are disabled by your IT policy."
3. Run the fleet normally for 7 days.
4. Review the firewall or proxy log for those machines.
5. **Pass:** zero update-check traffic from Focus Core in the log.
   **Fail:** any traffic to the update endpoints — treat it as a
   defect and report it.

For reference, Focus Core's only update endpoints are the GitHub
Releases API (`https://api.github.com/repos/<repo>/releases/latest`,
user agent `FocusCore-Updater`) and the release-asset download used
when a user clicks "Update now". Under the policy, neither is
contacted.

### Honest limits of this proof

The 7-day firewall log is evidence produced at your site; no test
running on our side can generate it for you. What ships with the app
is the code-level proof underneath it: the acceptance test suite
(`tests/test_update_kill_switch_121.py`) drives every self-update
path with the policy on and asserts the app never reaches a network
function, and a positive control with the policy off proves the test
would catch real traffic. Those tests exercise the registry tier
through a test double — the live HKLM read is the same code path the
app itself runs, and step 2 above confirms it on a real machine.
