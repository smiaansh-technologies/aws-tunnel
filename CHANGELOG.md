# Changelog

All notable changes to this project are documented in this file. The format
is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
version bumps follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Each release correlates to a git tag `vX.Y.Z` and a published GitHub Release
at `https://github.com/<owner>/aws-tunnel/releases/tag/vX.Y.Z`.

---

## [Unreleased]

### Renamed

- **App renamed from `aws-sso-connector` to `aws-tunnel`**:
  - `APP_NAME` in `config.py` changed from `"aws-sso-connector"` to `"aws-tunnel"`,
    shifting the default data directory from `~/.aws-sso-connector/` to
    `~/.aws-tunnel/` for new runs.
  - Added `_migrate_old_app_data_dir()` in `config.py` that automatically
    renames or copies the existing `~/.aws-sso-connector/` directory to
    `~/.aws-tunnel/` so existing user data is preserved.
  - `GITHUB_REPO` updated to `"smianshsmiaansh-technologies/aws-tunnel"`.
  - PyInstaller spec renamed from `aws-sso-connector.spec` to `aws-tunnel.spec`.
  - All source code, documentation, and config references updated:
    `aws-sso-connector` → `aws-tunnel`, `aws_sso_connector` → `aws_tunnel`.

---

## [1.1.0] — 2026-09-26

### Added

- **Check-for-Updates feature**: GitHub Releases-based update checking with
  two modes:
  - Manual via **Help > Check for Updates…** menu item.
  - Automatic on app startup + periodic with user-selectable interval
    (`Off / Daily / Weekly / Biweekly / Monthly`) stored in
    **File > Preferences**.
  - New `update_check_interval` and `last_update_check` fields on the
    persisted `Settings` object.
  - New module `update_checker.py` using stdlib `urllib` (no new deps)
    against `api.github.com/repos/<owner>/<repo>/releases/latest`.
  - Thread-safe GUI dispatch: HTTP runs on a daemon worker thread, all
    Tk widget interactions marshalled to the main thread via
    `root.after(0, …)`.
  - "Update Available" modal dialog with **Open Download Page** button that
    links to `https://github.com/<owner>/<repo>/releases/latest`.
  - Publisher-configurable `GITHUB_REPO` in `config.py` (leave as `None`
    until the repo is published; the feature gracefully degrades when
    unset).
- **Subprocess flag helper**: new `process_utils.no_window_kwargs()` module
  centralizes the Windows `CREATE_NO_WINDOW` flag so all background AWS
  CLI / `taskkill` spawns reuse one implementation.
- **New tests**:
  - `tests/test_update_checker.py` (parametrized semver comparisons,
    `latest_release` HTTP-wrapper behavior, interval math, check-due
    logic, timestamp persistence).
  - `tests/test_process_utils.py` (Windows flag presence, extra-flags
    combining, non-Windows no-op return).
- **Comprehensive `.gitignore`** based on the official GitHub
  `Python.gitignore` template plus security exclusions for private keys,
  coverage, build artifacts, and the per-user local override
  `start_aws_tunnel.local.ps1`.
- **Auto-loaded agent rules**: `.trae/rules/project_rules.md` governing
  version bumps, changelog discipline, secret/ID sanitization, GUI
  thread-safety, subprocess flags, PyInstaller spec hygiene, and test
  requirements for all future code-modification tasks.

### Changed

- `config.py`:
  - Bumped `APP_VERSION` from `1.0.0` to `1.1.0`.
  - Added `GITHUB_REPO: str | None = None` (update-check target).
  - `Settings` dataclass added `update_check_interval: str = "weekly"`
    and `last_update_check: str = ""` with full backward-compatibility
    (old settings files load fine thanks to the unknown-key ignore logic).
- `main.py`:
  - Preferences dialog now includes a **Check for updates** combobox
    row; saving preferences rearms the next scheduled auto-check.
  - Help menu gains **Check for Updates…** item.
  - Startup hook calls `auto_update_tick()` before `root.mainloop()` so
    periodic checks respect the last-check timestamp.
- **README.md**: added "Automatic updates" section, expanded the Help
  menu list, updated the Configuration section with the two new Settings
  fields, and refreshed the Project layout inventory.

### Fixed

- **Stray AWS CLI console windows on Windows (PyInstaller `console=False`)**:
  background subprocess spawns (`aws sso login`, `aws ssm start-session`,
  `taskkill`) now pass `CREATE_NO_WINDOW` via `no_window_kwargs()` so the
  windowed EXE no longer leaves orphan AWS CLI consoles on the taskbar.
  Interactive SSM shell sessions (`new_console=True`) intentionally keep
  their own console and opt out.
  - Touched: `tunnel/ssm_tunnel.py:_process_start_kwargs` and
    `_stop_process_tree`, `aws/sso_login.py:_run`,
    `gui/profile_dialog.py:_run_sso_login`.
  - Updated `tests/test_ssm_tunnel.py` assertions to assert the combined
    `CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW` flags; added two new
    tests covering the background-vs-interactive distinction.
- **PyInstaller spec bundling**: the versioned `aws-tunnel.spec` now
  declares `datas=[('docs', 'docs')]` so builds that go through the spec
  (instead of the ad-hoc CLI command) still ship the user-guide HTML and
  screenshots required by the **Help > User Guide** menu.

### Security

- **Repo sanitization pass before public GitHub publish**:
  - Removed real AWS account ID `886436937316` → placeholder
    `111122223333`.
  - Removed real SSO directory ID `d-9f676e80c8` → placeholder
    `d-xxxxxxxxxx`.
  - Removed real bastion instance ID `i-0fceb6b5025641e8b` → fictional
    `i-0123456789abcdef0` (fixtures) / `i-0feedfacefeedfac0`
    (capture script).
  - Removed real RDS hostnames / project naming and replaced with generic
    placeholders.
  - Sanitized `start_aws_tunnel.ps1`; added warning comment and a
    `.gitignore`d local-override path `start_aws_tunnel.local.ps1` for
    users to fill in real IDs without committing them.
  - Regenerated all 10 docs screenshots via `scripts/capture_screenshots.py`
    (using the now-sanitized fixture data).
  - Regenerated `docs/AWS-Tunnel-User-Guide.pdf` from the updated
    HTML + screenshots.
  - Grep-verified zero matches in the tree (outside `.venv` / `build` /
    `dist`) for the original real identifiers and for high-risk secret
    patterns (`AKIA`, `ASIA`, PEM headers, etc.).

---

## [1.0.0] — YYYY-MM-DD

Initial public release:

- Tkinter system-tray GUI (minimize-to-tray with Quit via tray menu).
- SSO profile setup wizard, profile list with login/logout and token
  status display.
- Bastion discovery by configurable EC2 tag (default `Role=bastion`),
  fallback to listing all running instances.
- SSM Session Manager port-forwarding tunnels (start / stop / status /
  live port monitoring, auto-close on app exit).
- Saved tunnel profiles (create / edit / delete / reopen from the
  Bastion Hosts panel or File > Tunnel Profiles).
- Logging to timestamped files under `~/.aws-tunnel/logs/` with
  an Open Logs Folder menu shortcut.
- Built-in illustrated user guide (Help > User Guide, bundled into the
  PyInstaller EXE via `--add-data docs`).
