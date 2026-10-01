# Security Policy

Thank you for helping keep Focus Core and its users safe.

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Report it privately through GitHub:

- https://github.com/ahtisham482/focus-core/security/advisories/new

(GitHub account required. This is the only reporting channel for now;
please do not send security reports to personal email addresses or
social media.)

Please include, as far as you can:

- what the problem is and what an attacker could do with it;
- the Focus Core version and Windows version where you saw it;
- steps to reproduce, or a short proof of concept;
- any logs or screenshots that help (please remove personal data).

## What to expect

Focus Core is maintained by one person, part-time. Honest targets:

- acknowledgement of your report within **7 days**;
- a status update at least every **30 days** while it is open;
- a fix in a new release, with credit in the release notes if you
  would like it (tell us your preferred name/handle).

Please give us a reasonable chance to fix the problem before
publishing details. We will not take legal action against anyone
who reports in good faith and follows this policy.

## Supported versions

Only the **latest release** on GitHub receives security fixes.
If you are on an older version, update first:

- https://github.com/ahtisham482/focus-core/releases

## Scope

In scope:

- the Focus Core application (dashboard, tray, shield, launcher);
- the Windows installer and the built-in updater;
- backup encryption and restore;
- the local web server (it listens on 127.0.0.1 only).

Good to know when assessing impact:

- Focus Core is local-first: no account, no telemetry, no cloud
  copy of your activity data. What we collect and how it is stored
  is documented in [PRIVACY.md](PRIVACY.md).
- Window titles and URLs stored in the local database are encrypted
  with Windows DPAPI in the **current Windows user's** scope. Anyone
  who can log in as that Windows user (or run code as them) can read
  that data — this is a known design boundary, not a vulnerability.
- The single-instance mutex, the loopback CSRF guard, and the
  update-signature/checksum verification are part of the security
  design; bypasses of them are in scope.

Out of scope:

- problems that need an attacker to already control the machine or
  the Windows user account (see the DPAPI boundary above);
- missing security headers or similar findings on third-party sites;
- denial of service that only affects the attacker's own machine.

## A note on this file

This policy is versioned with the repository. The current version
is the one on the `main` branch.
