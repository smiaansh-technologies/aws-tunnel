# AWS Tunnel

A small cross-platform system-tray GUI that fills the gap AWS doesn't:
manage SSO profiles, log in, discover bastion hosts, and
open/monitor/close tunnels to them — all in one place, fully logged.
Runs on Windows, macOS, and Linux desktops.

## Requirements

- Python 3.10+
- **AWS CLI v2** installed and on `PATH` (used for `aws sso login`)
- **session-manager-plugin** installed (required by AWS for
  `aws ssm start-session`; see AWS's install docs for your OS)
- IAM/SSO permissions for: `sso:*` (login), `ec2:DescribeInstances`,
  `ssm:StartSession`, `ssm:DescribeInstanceInformation`, and the
  Session Manager messaging actions required by port forwarding.

For SSM remote-host port forwarding, the assigned SSO role must allow
`ssm:StartSession` on both the bastion instance and the managed document
`AWS-StartPortForwardingSessionToRemoteHost`. A typical scoped policy also
allows `ssm:TerminateSession` for the caller's sessions and the required
`ssmmessages` channel actions. The AWS administrator must attach this policy
to the role used by the selected AWS profile; the application cannot grant
these permissions itself.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

## Running

```bash
python main.py            # normal run
python main.py --debug    # also logs to the console
```

On first run with no SSO profiles, a setup wizard walks you through
creating one before the main window appears.

By default, closing the window minimizes it to the system tray; use the
tray menu's "Quit" to actually exit (this also cleanly stops any open
tunnels). This is controlled by the `minimize_to_tray` setting — when
disabled, closing the window quits the app.

## Help & user documentation

The app ships with built-in help, available from the **Help** menu:

- **User Guide** — opens the full illustrated guide (setup wizard,
  profiles, bastions, tunnels, troubleshooting) in your default browser
- **Open Logs Folder** — jumps straight to `~/.aws-tunnel/logs/`
- **Check for Updates…** — checks GitHub Releases for a newer version
  (see "Automatic updates" below)
- **About** — version info

## Automatic updates

The app includes a Check-for-Updates feature that watches GitHub
Releases for new published versions. It works in two modes:

- **Manual**: choose **Help > Check for Updates…** at any time.
- **Automatic (periodic)**: on app launch, and then at a configurable
  interval, the app silently checks in the background. If a newer
  release is found, a modal dialog appears with an **Open Download
  Page** button that jumps straight to the GitHub Releases page in
  your browser.

The preference is configured in **File > Preferences** under
**Check for updates**:

| Option | Meaning |
|---|---|
| **Off** | No automatic checks (manual Help menu only) |
| **Daily** | Check at most once every 24 hours |
| **Weekly** (default) | Check at most once every 7 days |
| **Biweekly** | Check at most once every 14 days |
| **Monthly** | Check at most once every 30 days |

A timestamp of the last successful check is stored in
`~/.aws-tunnel/settings.json` (`last_update_check`), so the
interval is measured from the *actual* last check, not from each app
start.

### Enabling the feature (publisher step)

The feature is disabled until the app is published. To activate it:

1. Publish the repo to GitHub.
2. Open [config.py](file:///d:/Kamal/08_Smiansh/Projects/aws-tunnel/config.py#L19-L26)
   and set `GITHUB_REPO = "owner/aws-tunnel"` where
   `owner/aws-tunnel` is the actual repo slug.
3. When cutting a new release:
   - Bump `APP_VERSION` in `config.py` (see "Versioning & changelog").
   - Push a matching git tag `vX.Y.Z`.
   - Publish a GitHub Release against that tag (non-draft so it shows
     as `/latest` in the API).

Until `GITHUB_REPO` is set, the automatic check is a no-op, and the
manual **Check for Updates…** menu shows an informational dialog
explaining that the feature hasn't been configured yet.

The guide lives in `docs/user_guide.html` with screenshots in
`docs/screenshots/`; a PDF version can be rendered from it (see
"Developer tooling" below).

## Running the tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

## Packaging into a single executable

Binaries can be built for Windows, macOS, and Linux. PyInstaller does
not cross-compile, so each platform's binary must be built on that
platform:

```bash
uv run --group dev pyinstaller --clean aws-tunnel.spec
```

The versioned spec pins `console=False` (no stray console window) and
bundles `docs/` so **Help > User Guide** works in the packaged app.
GitHub Actions (`.github/workflows/release.yml`) builds Windows, Linux,
and macOS binaries on each `vX.Y.Z` tag.

- **Windows** — produces `dist/aws-tunnel.exe`.
- **macOS** — produces a single executable in `dist/` (arm64 on Apple
  Silicon, x86_64 on Intel).
- **Linux** — produces an ELF executable in `dist/`; the tray icon
  needs a desktop environment with AppIndicator/Xorg support at runtime.

The binary bundles only the Python code and its dependencies — the
external prerequisites (AWS CLI v2, session-manager-plugin) must still
be installed on the target machine.

App data (logs, settings) is stored under `~/.aws-tunnel/` —
never inside the install directory — so it survives reinstalls/updates
on all platforms.

## Configuration

Settings live in `~/.aws-tunnel/settings.json` (created on first
run with defaults) and cover:

- `bastion_tag_key` / `bastion_tag_value` — how bastions are discovered
  (default: `Role=bastion`). Change these if your environment tags
  jump hosts differently. If no running instance matches the configured
  tag, running instances are shown as candidates as a fallback.
- `preferred_tunnel_method` — `"ssm"` (default, recommended) or `"ssh"`.
- `profile_refresh_minutes` — how often profile connection status is refreshed
  (`1`, `5` default, or `10` minutes; configurable from File > Preferences).
- `minimize_to_tray` — whether closing the window hides it to the system
  tray (`true`, default) or quits the app; toggle in File > Preferences.
- `update_check_interval` — how often the app automatically checks for
  updates on GitHub Releases (`"off"`, `"daily"`, `"weekly"` default,
  `"biweekly"`, `"monthly"`; configurable from File > Preferences).
- `last_update_check` — ISO timestamp of the last successful release
  check (auto or manual). Used together with `update_check_interval` to
  decide whether the next launch should check again. Written by the app
  — do not edit by hand.
- `window_x` / `window_y` — last window position, restored on the next launch.
- `tunnel_profiles` — named local connection profiles containing the AWS
  profile, bastion instance ID, target host, target port, and local port
  (plus method/enabled/description fields). These are filled after a
  successful tunnel and can be created, edited, deleted, or opened from
  File > Tunnel Profiles or the Bastion Hosts panel.
- `presets` — reserved for named bastion+target+port combinations.

## Design notes

- **Tunnels use SSM Session Manager port forwarding by default** — no
  SSH keys, no open port 22 on the bastion, and it reuses your existing
  SSO credentials. A classic SSH/paramiko path (`tunnel/ssh_tunnel.py`)
  is included for bastions that only support plain SSH, but it isn't
  wired into the GUI yet — see "Next steps" below.
- Every module has one job (see the file layout below); the GUI never
  talks to AWS directly, only through `aws/` and `tunnel/`, so the
  logic is unit-testable without a display or real AWS credentials.
- Nothing sensitive is ever stored by this app — tunnel profiles contain
  connection metadata only, never credentials or tokens. Profile creation
  writes plain (non-secret) config to `~/.aws/config`, exactly like the
  AWS CLI does, and login relies entirely on the AWS CLI's own token
  cache.

## Project layout

```
main.py                 entry point (GUI wiring + menus + update-check scheduling)
config.py               paths + persisted settings + APP_VERSION + GITHUB_REPO
process_utils.py        shared subprocess flags helper (CREATE_NO_WINDOW on Windows)
update_checker.py       Check-for-Updates logic: GitHub Releases API, semver compare, scheduling
logging_setup.py        one place that configures logging
requirements.txt        runtime dependencies
requirements-dev.txt    test/docs dev dependencies (pytest, playwright)
docs/
  user_guide.html       illustrated user guide (Help > User Guide)
  screenshots/          generated by scripts/capture_screenshots.py
scripts/
  capture_screenshots.py   dev tool: regenerate guide screenshots (mocked AWS)
  build_docs_pdf.py        dev tool: render the guide to PDF via Playwright
aws/
  profiles.py           list/create/delete SSO profiles, SSO token status
  sso_login.py          wraps `aws sso login` / `aws sso logout`
  bastions.py           EC2/SSM bastion discovery
tunnel/
  ssm_tunnel.py         SSM port-forwarding (primary)
  ssh_tunnel.py         paramiko SSH tunnel (fallback)
  manager.py            tracks active tunnels, start/stop/status
  validation.py         input validation for connection parameters
gui/
  main_window.py        profiles + bastions + tunnels panels
  profile_dialog.py     "new profile" form / first-run setup wizard
  bastion_dialog.py     "new tunnel" form
  tunnel_profiles_dialog.py  manage saved tunnel profiles
  clipboard.py          clipboard helpers for form fields
  tray.py               system tray icon/menu
tests/                  unit tests for aws/, tunnel/, update_checker, and GUI helpers
```

## Developer tooling (documentation)

The guide's screenshots are generated automatically — no manual
screenshotting. The capture script launches the real GUI against mocked
AWS data (no network, nothing in the real `~/.aws` touched) and captures
each screen into `docs/screenshots/`:

```bash
python scripts/capture_screenshots.py
```

A PDF manual is rendered from the HTML guide with Playwright — a dev
dependency only, never bundled into the app executable:

```bash
pip install -r requirements-dev.txt
playwright install chromium        # one-time browser download
python scripts/build_docs_pdf.py   # -> docs/AWS-Tunnel-User-Guide.pdf
```

## Platform support

The app is platform independent: the GUI is Tkinter and the tray icon
uses `pystray`, which picks a native backend per OS at import time.

- **Windows** — native tray icon; works out of the box.
- **macOS** — native menu-bar (tray) icon; works out of the box.
- **Linux/Unix** — GTK/AppIndicator or Xorg tray backend. A desktop
  environment with a system tray is required (headless servers have
  none). Debian/Ubuntu users may need `python3-tk` and the
  AppIndicator/GI libraries (e.g. `gir1.2-ayatanaappindicator3-0.1`).

AWS CLI v2 and session-manager-plugin install per OS as well — see
AWS's install docs for each platform.

## Suggested next steps (not yet wired into the GUI)

- A "Save as preset" button in the tunnel dialog, using the
  `TunnelPreset`/`Settings.presets` model already in `config.py`.
- Token-expiry countdown/notification next to each profile.
- A settings screen to edit `bastion_tag_key`/`bastion_tag_value`
  instead of hand-editing `settings.json`.
- Wiring `tunnel/ssh_tunnel.py` into the "New Tunnel" dialog as a
  fallback option when a bastion doesn't report SSM online.
