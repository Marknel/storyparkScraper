# Storypark authentication: upstream review and adoption

Reviewed 12 November 2026 against our base ef9490d and upstream release 1.1,
master a65bebc. The relevant implementation is
[b27237e, 30 October 2026](https://github.com/AustralianSimon/storyparkScraper/commit/b27237ef8c64d2bd99c51173f2dcc689fc9e3104).

## Decision

Adopt the owner-assisted authentication and session-reuse approach in our existing
package. MFA and CAPTCHA are completed by the account owner when requested.
The normal overnight job reuses the resulting profile. Authentication expiry
produces a clear failure for the existing status/MQTT monitoring path.

Before this change, the home installation's latest real runs failed while
clicking the old long sign-in CSS selector. The upstream button selector
addresses that observed failure. Home acceptance on 12 November confirmed
owner sign-in, headless session reuse and authenticated media acquisition.

## What upstream changed

- main.py checks sign-in/MFA URLs and visible password fields, attempts the usual
  credentials, then waits for the owner when authentication is incomplete.
- auth_assist.py exposes screenshots plus mouse/keyboard forwarding, while keeping
  Playwright operations on the calling thread.
- util_playwright.py saves and reloads storage_state and builds a user agent from
  the actual Chromium version. HEADLESS=0 enables direct owner interaction.
- config.py uses button[data-action='signin'], the newer Menu button and the
  child-menu header structure.

The code remains MIT licensed. The adapted helper and browser user-agent logic
credit the upstream implementation; the existing LICENSE is retained.

## Integration choices

The fork already has a persistent browser profile, secure credential loading,
SQLite state, atomic archive writes, filtering and MQTT monitoring. Those remain
the basis of the service. A wholesale merge of the older monolithic upstream
scraper would conflict with that architecture.

The login command acquires the same run lock as sync. It supports a visible
browser or an explicit headless assist session. Authentication is accepted only
after an authenticated app menu is visible on the expected origin and outside
the authentication routes. Unknown challenge pages fail closed. Background runs
do not create an assist listener or wait ten minutes for someone to respond.

Upstream's helper listens on all interfaces without authentication, accepts
unbounded input, and provides a manual continue override. Our adaptation uses
loopback by default, a new random per-session bearer token, bounded requests and
queues, same-origin checks, a limited command set, and verified completion only.
Screenshots and input stay in memory and are discarded on shutdown.

Password submission and verification input errors are sanitized before they can
reach status JSON, telemetry or application tracebacks. A Playwright timeout can
otherwise include the value passed to a fill operation.

The sign-in/menu selectors are updated with legacy-menu support. Media downloads
receive fresh cookies after child discovery and after every story fetch, and use
the same effective browser user agent. This corrects the existing ordering bug
where cookies were copied before login. Removed cookies are removed from the
download session too; cookie domain/path/secure/expiry are retained.

The existing main-pc pause behavior and MQTT status override have been incorporated
so deployment preserves those previously uncommitted improvements.

## Validation and acceptance

Automated checks cover normal and expired sessions, MFA/unknown challenge pages,
positive authentication checks, credential-error redaction, profile locking,
login CLI failure codes, real HTTP assist authorization and bounded input, and
cookie renewal before actual media requests. They do not establish that a live
Storypark account has completed verification.

A bounded live headless check on the development PC reached the expected
Storypark origin and sign-in form and correctly returned an owner-verification
requirement, with no authenticated menu. No challenge was solved, archive sync
started or home runtime state changed by that check. The authentication and
deployment regression checks pass. One pre-existing Windows Credential Manager
integration check fails with CredWriteW error 8 on this workstation; the
encrypted DPAPI fallback checks and full GitHub-hosted Windows/Ubuntu CI pass.

The owner then deployed e47291c on main-pc and completed sign-in using the
existing configured browser profile. Two capped headless runs finished
successfully at 21:08 and 21:09 Brisbane time on 12 November. Read-only SQLite
and file checks verified two new stories with narrative text and 29 new image
files, each nonempty and with a valid image signature. The second browser
process reused the saved login without further owner interaction. Status
reported success with no error, and MQTT publication succeeded.

All 60 managed source hashes matched the deployment manifest. Configuration,
archive, credentials and profile were preserved during deployment and acceptance.
After acceptance, runtime.enabled was deliberately changed from false to true
under the run lock, with a protected configuration backup. Parsed configuration
comparison confirmed that this was the only setting changed; the existing
03:00 schedule setting was retained.

Home acceptance must run under the existing scheduled-task identity with the
existing configuration/profile. Preserve the archive and pause setting during
deployment, complete owner login, run a small capped acquisition, inspect actual
media and status, and repeat to verify persisted-session reuse. Only then resume
the intended unattended schedule. If Storypark challenges every headless run,
record that evidence and revisit the supported runtime mode rather than claiming
that a saved profile guarantees indefinite access.

Relevant delivery items: authentication investigation
[#704](https://dev.azure.com/cracknell/Storypark%20Scraper/_workitems/edit/704)
and implementation
[#733](https://dev.azure.com/cracknell/Storypark%20Scraper/_workitems/edit/733).
