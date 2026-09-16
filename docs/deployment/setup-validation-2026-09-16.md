# Windows controls and connection setup validation — 16 September 2026

These **1.0.0-setup-validation** packages include the Windows controls and simplified
Master/Lite connection setup. They supersede the earlier **1.0.0-validation**
pilot packages. Tally remains on Master only.

## What changed

- Both source checkouts and installed copies include `setuora.bat`. Double-click
  it for Open in browser, Start, Stop, Check status, Setup / repair, Update,
  View recent logs, Check configuration, and Exit. Closing the menu leaves the
  application running. Commands also return success/failure exit codes.
- Setup and maintenance request Windows Administrator access when needed.
  Setup password prompts remain visible. Cancelled elevation and failed actions
  report the problem; the menu remains available for another action.
- Status checks application identity and database health. Open in browser first
  checks status. Setup finds the installed Python environment or installs Python
  when supported; its startup health check catches configuration failures.
- Installed Update selects a downloaded installer for the matching edition.
  Source Update checks configuration, a clean worktree, and a possible
  fast-forward before stopping the service. A failed stop prevents replacement.
  Complete automatic upgrade rollback is not provided.
- On Master, save the public HTTPS address once, add a franchise, then select
  **Copy connection details**. The first credential is created with the franchise
  and shown once. Replacement and disabling access require clear confirmations.
- On Lite, paste those details under **Admin → Master connection** and select
  **Connect to Master**. Lite verifies the address, credential, franchise record,
  and accepted-event sequence before saving. It initializes inventory once and
  attempts the first sync. Incorrect details preserve the saved connection.
- Reconnecting the same installation supports credential replacement and restart
  without duplicating initialization. Initialized installations reject a different
  franchise or Master identity. Queued operations and recovery controls remain
  visible. Advanced connection fields are collapsed.
- The copy button has a Ctrl+C fallback when clipboard access is unavailable.
  A browser-discovered form failure was fixed by using a same-origin referrer
  policy with noncached setup responses. The complete button label remains visible.

## Verification completed

| Check | Result |
| --- | --- |
| Master full automated suite | 203 passed |
| Lite full automated suite | 400 passed |
| Controller runtime checks included above | 7 per edition passed |
| Master-to-Lite setup and restart integration | Passed with real application handlers and isolated databases |
| Chromium desktop and 390-pixel mobile setup checks | Passed |
| Changed Python lint and whitespace checks | Passed |

The full suites ran on Linux with portable PowerShell 7.6.6 enabled:

```sh
SETUORA_TEST_PWSH=/path/to/pwsh .venv/bin/python -m pytest -q
```

The controller tests parse and execute the actual controller functions with
Windows effects mocked. They cover menu selections, invalid input, recovery from
errors, elevation cancellation, exit codes, source/installed update routing,
update ordering, and restart after a failed Git merge. They also run actual help
and invalid-command invocations. Without PowerShell these seven checks per
edition are explicitly skipped; set `SETUORA_TEST_PWSH` to `powershell.exe` or
`pwsh` to run them.

Cross-edition setup tests exercise copying Master's bundle into Lite, identity
verification, baseline acceptance, restart persistence, credential rotation,
wrong-franchise rejection, and rejection of a replacement Master franchise record.
Transport is routed through the test application's API; these checks do not
contact a production server or Tally company.

The browser check started both actual applications with fresh temporary SQLite
databases. It exercised login, saving the Master address, creating a franchise,
copying to the clipboard, denied-clipboard recovery, secret absence on a later
page request, Lite's invalid-paste recovery, manual options, and desktop/mobile
layout. No JavaScript page errors were reported. It did not establish a real
public HTTPS connection. Lite's existing 102 test warnings concern deprecated
HTTP test-client APIs; they are not test failures.

## Packages and first use

Use the matching installer and SHA-256 manifest from each edition's `dist` folder:

- `Setuora-Master-1.0.0-setup-validation-windows.cmd`
- `Setuora-Lite-1.0.0-setup-validation-windows.cmd`

Install or upgrade **Master before Lite**. Open the installed `setuora.bat` for
routine controls. Follow the [installation guide](installation-guide.md) for the
server address, first administrator account, and connection setup.

Use one Lite server/database per independent warehouse or franchise. Staff PCs
normally open that Lite server in a browser with their own accounts. The packaged
Master and Lite both use port 8000 and require separate hosts or VMs.

## Acceptance still required on Windows

These are checked pilot builds, not a certification of the target machines.
Native BAT execution, Windows PowerShell 5.1, UAC, installer dialogs, Task
Scheduler, firewall rules, reboot without login, scanners, printers, and real
Tally imports were not exercised on Windows in this environment.

Before staff use, test the installer, every menu action, cancellation of prompts,
reboot, and an upgrade on the actual Windows machines. Establish the public
Master HTTPS address, valid certificate, and proxy separately; saving the address
in the application does not configure them. Verify a test operation from Lite
through Master into a Tally test company, recovery after a network outage, and
backup restoration. No live deployment or production-data changes were performed.
