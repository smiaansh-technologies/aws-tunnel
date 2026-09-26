"""
Dev-only tool: regenerate the user-guide screenshots in docs/screenshots/.

Launches the real Tkinter GUI against fully mocked AWS data (no network,
no real ~/.aws/config touched — everything is redirected to a temp dir)
and captures window/dialog regions with PIL.ImageGrab.

Run from the project root:
    python scripts/capture_screenshots.py

Windows is the primary target. On macOS/Linux it needs a desktop session.
"""

from __future__ import annotations

import ctypes
import json
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SHOTS_DIR = ROOT / "docs" / "screenshots"

# ---------------------------------------------------------------- imports
import tkinter as tk  # noqa: E402

import config  # noqa: E402
import aws.profiles as aws_profiles  # noqa: E402
import gui.profile_dialog as profile_dialog_mod  # noqa: E402
import gui.main_window as main_window_mod  # noqa: E402
import logging_setup  # noqa: E402
import main as app_main  # noqa: E402

from aws.bastions import Bastion  # noqa: E402
from aws.profiles import AWSProfile  # noqa: E402
from tunnel.manager import ActiveTunnel  # noqa: E402
from PIL import ImageGrab  # noqa: E402


# ---------------------------------------------------------------- fixtures
PROFILES = [
    AWSProfile(
        name="dev-admin",
        sso_start_url="https://d-xxxxxxxxxx.awsapps.com/start",
        sso_region="us-east-1",
        sso_account_id="111122223333",
        sso_role_name="AdministratorAccess",
        region="ap-south-1",
    ),
    AWSProfile(
        name="staging-readonly",
        sso_start_url="https://d-xxxxxxxxxx.awsapps.com/start",
        sso_region="us-east-1",
        sso_account_id="222233334444",
        sso_role_name="ReadOnly",
        region="eu-west-1",
    ),
    AWSProfile(
        name="prod-admin",
        sso_start_url="https://d-xxxxxxxxxx.awsapps.com/start",
        sso_region="us-east-1",
        sso_account_id="333344445555",
        sso_role_name="AdministratorAccess",
        region="us-east-1",
    ),
]
CONNECTED_PROFILES = {"dev-admin"}

BASTIONS = [
    Bastion(
        instance_id="i-0feedfacefeedfac0",
        name="bastion-dev",
        private_ip="[IP_ADDRESS]",
        state="running",
        ssm_online=True,
    ),
    Bastion(
        instance_id="i-0a1b2c3d4e5f6a7b8",
        name="bastion-staging",
        private_ip="10.0.2.14",
        state="running",
        ssm_online=True,
    ),
    Bastion(
        instance_id="i-0deadbeef1234567",
        name="bastion-prod",
        private_ip="10.1.0.7",
        state="running",
        ssm_online=False,
    ),
]

TUNNEL_PROFILES = [
    {
        "profile_id": "tp1",
        "name": "dev-postgres",
        "aws_profile": "dev-admin",
        "bastion_instance_id": "i-0feedfacefeedfac0",
        "bastion_name": "bastion-dev",
        "target_host": "dev-db.internal.local",
        "target_port": 5432,
        "local_port": 15432,
        "method": "ssm",
        "enabled": True,
        "description": "Dev RDS PostgreSQL",
    },
    {
        "profile_id": "tp2",
        "name": "staging-api",
        "aws_profile": "staging-readonly",
        "bastion_instance_id": "i-0a1b2c3d4e5f6a7b8",
        "bastion_name": "bastion-staging",
        "target_host": "api.staging.internal",
        "target_port": 8443,
        "local_port": 18443,
        "method": "ssm",
        "enabled": True,
        "description": "Staging internal API",
    },
]

SSO_ACCOUNTS = [
    {"accountId": "111122223333", "accountName": "Development"},
    {"accountId": "222233334444", "accountName": "Staging"},
    {"accountId": "333344445555", "accountName": "Production"},
]
SSO_ROLES = [
    {"account_id": "111122223333", "role_name": "AdministratorAccess"},
    {"account_id": "111122223333", "role_name": "ReadOnly"},
    {"account_id": "222233334444", "role_name": "ReadOnly"},
    {"account_id": "333344445555", "role_name": "AdministratorAccess"},
]

REGIONS = [
    "us-east-1", "us-east-2", "us-west-2", "eu-west-1", "eu-central-1",
    "ap-south-1", "ap-southeast-1", "ap-northeast-1", "sa-east-1",
]


# ------------------------------------------------------------- fake AWS
def fake_list_profiles():
    return PROFILES


def fake_is_logged_in(profile):
    return profile.name in CONNECTED_PROFILES


def fake_discover_bastions(profile_name, tag_key, tag_value):
    return list(BASTIONS)


def fake_login(profile_name):
    return None


def fake_logout(profile_name):
    return None


def fake_fetch_regions():
    return list(REGIONS)


def fake_list_sso_accounts(self):
    return [dict(a) for a in SSO_ACCOUNTS]


def fake_list_sso_roles(self, account_id):
    return [{"roleName": r["role_name"]} for r in SSO_ROLES if r["account_id"] == account_id]


class _FakeHandle:
    def poll(self):
        return None

    def communicate(self):
        return ("", "")


class FakeTunnelManager:
    """Stand-in for TunnelManager showing one live SSM tunnel."""

    def __init__(self) -> None:
        self._tunnel = ActiveTunnel(
            id="demo-ssm-1",
            method="ssm",
            bastion_label="bastion-dev",
            target_host="dev-db.internal.local",
            target_port=5432,
            local_port=15432,
            started_at=datetime.now() - timedelta(minutes=42, seconds=7),
            handle=_FakeHandle(),
        )

    def list_tunnels(self):
        return [self._tunnel]

    def take_recently_dead(self):
        return []

    def stop_tunnel(self, tunnel_id):
        pass

    def stop_all(self):
        pass


class _DummyTray:
    """No-op tray so no real tray icon is created during capture."""

    def __init__(self, root, on_quit):
        pass

    def start(self):
        pass

    def stop(self):
        pass


# ------------------------------------------------------------- helpers
def enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def pump(root: tk.Tk, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.02)


def wait_for(root: tk.Tk, predicate, timeout: float = 10.0, what: str = "condition"):
    end = time.time() + timeout
    while time.time() < end:
        root.update()
        result = predicate()
        if result:
            return result
        time.sleep(0.05)
    raise RuntimeError(f"Timed out waiting for {what}")


def capture(widget: tk.Misc, name: str) -> None:
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    widget.update_idletasks()
    widget.update()
    time.sleep(0.2)
    x, y = widget.winfo_rootx(), widget.winfo_rooty()
    w, h = widget.winfo_width(), widget.winfo_height()
    image = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
    out = SHOTS_DIR / name
    image.save(out)
    print(f"  saved {out.name}  ({w}x{h})")


def toplevel_by_title(root: tk.Tk, title: str):
    """Find a Toplevel by its title, searching nested children too
    (dialogs opened from other dialogs are parented to them, not root)."""
    stack = [root]
    while stack:
        widget = stack.pop()
        for child in widget.winfo_children():
            if isinstance(child, tk.Toplevel) and child.title() == title:
                return child
            stack.append(child)
    return None


def menu_widget(root: tk.Tk, cascade_label: str):
    bar = root.nametowidget(root["menu"])
    for i in range(0, int(bar.index("end")) + 1):
        if bar.type(i) == "cascade" and bar.entrycget(i, "label") == cascade_label:
            return root.nametowidget(str(bar.entrycget(i, "menu")))
    raise KeyError(cascade_label)


def menu_invoke(root: tk.Tk, cascade_label: str, item_label: str) -> None:
    menu = menu_widget(root, cascade_label)
    for i in range(0, int(menu.index("end")) + 1):
        if menu.type(i) == "command" and menu.entrycget(i, "label") == item_label:
            menu.invoke(i)
            return
    raise KeyError(item_label)


# ------------------------------------------------------------- session A
def capture_wizard() -> None:
    """Capture the 3-step first-run setup wizard (no profiles configured)."""
    print("Session A: setup wizard")
    root = tk.Tk()
    root.withdraw()

    wizard = profile_dialog_mod.ProfileDialog(root, on_created=lambda: None)
    pump(root, 0.5)
    wizard._entries["sso_start_url"].insert(0, "https://d-xxxxxxxxxx.awsapps.com/start")
    wizard._entries["sso_region"].set("us-east-1")
    pump(root, 0.2)
    capture(wizard, "01_setup_wizard_step1.png")

    # Run mocked authentication -> discovery -> step 2
    wizard._validate_step1()
    wait_for(
        root,
        lambda: wizard.title() == "Step 2 of 3 — Select Account & Role",
        what="wizard step 2",
    )
    pump(root, 0.3)
    capture(wizard, "02_setup_wizard_step2.png")

    # First role is auto-selected; advance to step 3
    wizard._validate_step2()
    pump(root, 0.2)
    wizard._entries["name"].insert(0, "dev-admin")
    capture(wizard, "03_setup_wizard_step3.png")

    wizard._cancel_and_cleanup()
    root.destroy()


# ------------------------------------------------------------- session B
def drive_app(root: tk.Tk) -> None:
    """Runs in place of mainloop(): captures the main app screens."""
    print("Session B: main application")

    def find_main_window():
        for child in root.winfo_children():
            if isinstance(child, main_window_mod.MainWindow):
                return child
        return None

    main_window = wait_for(root, find_main_window, what="main window")

    # Swap in the fake tunnel manager (one live SSM tunnel for screenshots)
    main_window.tunnel_manager = FakeTunnelManager()

    # Wait for (mocked) bastion discovery to populate the panel
    wait_for(root, lambda: main_window.bastion_tree.get_children(), what="bastion discovery")
    main_window.bastion_tree.selection_set(main_window.bastion_tree.get_children()[0])
    pump(root, 0.5)

    capture(root, "04_main_window.png")
    capture(main_window._bastion_frame, "05_bastions_panel.png")

    # Populate the Active Tunnels panel from the fake manager
    main_window._refresh_tunnels()
    pump(root, 0.5)
    capture(main_window._tunnel_frame, "08_active_tunnels.png")

    # Tunnel Profiles manager + editor
    main_window._open_tunnel_profiles()
    manager_dlg = wait_for(
        root, lambda: toplevel_by_title(root, "Tunnel Profiles"), what="tunnel profiles dialog"
    )
    pump(root, 0.3)
    capture(manager_dlg, "07_tunnel_profiles_dialog.png")

    manager_dlg._new_profile()
    editor = wait_for(
        root, lambda: toplevel_by_title(root, "New Tunnel Profile"), what="profile editor"
    )
    pump(root, 0.3)
    capture(editor, "06_tunnel_profile_editor.png")
    editor.destroy()
    manager_dlg.destroy()
    pump(root, 0.2)

    # Preferences (File menu)
    menu_invoke(root, "File", "Preferences...")
    prefs = wait_for(root, lambda: toplevel_by_title(root, "Preferences"), what="preferences")
    pump(root, 0.3)
    capture(prefs, "09_preferences.png")
    prefs.destroy()

    # About (Help menu)
    menu_invoke(root, "Help", "About AWS Tunnel")
    about = wait_for(root, lambda: toplevel_by_title(root, "About"), what="about dialog")
    pump(root, 0.3)
    capture(about, "10_about.png")
    about.destroy()

    main_window.shutdown()
    root.destroy()


# ------------------------------------------------------------- setup
def patch_environment() -> Path:
    """Redirect all app data paths to a temp dir and mock the AWS layer."""
    temp_dir = Path(tempfile.mkdtemp(prefix="aws-tunnel-docs-"))
    app_data = temp_dir / "app-data"
    logs = app_data / "logs"
    settings_file = app_data / "settings.json"
    aws_dir = temp_dir / "aws"
    aws_config = aws_dir / "config"
    sso_cache = aws_dir / "sso" / "cache"

    # config module globals (read at call time by Settings/ensure_app_dirs)
    config.APP_DATA_DIR = app_data
    config.LOG_DIR = logs
    config.SETTINGS_FILE = settings_file
    # logging_setup captured LOG_FILE at import time — repoint it too
    logging_setup.LOG_FILE = logs / "app.log"
    # aws.profiles captured these at import time — repoint them as well
    aws_profiles.AWS_CONFIG_FILE = aws_config
    aws_profiles.AWS_SSO_CACHE_DIR = sso_cache

    # Seed settings so the Tunnel Profiles dialog has content.
    app_data.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(json.dumps({
        "bastion_tag_key": "Role",
        "bastion_tag_value": "bastion",
        "preferred_tunnel_method": "ssm",
        "profile_refresh_minutes": 5,
        "minimize_to_tray": True,
        "window_x": None,
        "window_y": None,
        "presets": [],
        "tunnel_profiles": TUNNEL_PROFILES,
    }))

    # Mock the AWS layer. Modules that did `from aws.profiles import X`
    # captured the original function, so patch both namespaces.
    aws_profiles.list_profiles = fake_list_profiles
    main_window_mod.list_profiles = fake_list_profiles
    main_window_mod.is_logged_in = fake_is_logged_in
    main_window_mod.discover_bastions = fake_discover_bastions
    main_window_mod.login = fake_login
    main_window_mod.logout = fake_logout

    # Setup wizard: no real AWS config writes, no network, no browser.
    profile_dialog_mod.fetch_aws_regions = fake_fetch_regions
    profile_dialog_mod.ProfileDialog._create_temp_sso_session = lambda self: None
    profile_dialog_mod.ProfileDialog._run_sso_login = lambda self: None
    profile_dialog_mod.ProfileDialog._get_sso_access_token = lambda self: "mock-access-token"
    profile_dialog_mod.ProfileDialog._cleanup_temp_profile = lambda self: None
    profile_dialog_mod.ProfileDialog._list_sso_accounts = fake_list_sso_accounts
    profile_dialog_mod.ProfileDialog._list_sso_roles = fake_list_sso_roles

    # The capture driver pumps events with root.update() instead of a real
    # mainloop, so Tkinter raises "main thread is not in main loop" when
    # background threads call .after().  Run the wizard's multi-step flow
    # and bastion discovery synchronously on the main thread instead.
    profile_dialog_mod.ProfileDialog._start_authentication = (
        lambda self: self._on_auth_success()
    )

    def _sync_discover_accounts(self):
        self._accounts = [dict(a) for a in SSO_ACCOUNTS]
        self._roles = [
            {
                "account_id": role["account_id"],
                "account_name": next(
                    a["accountName"] for a in self._accounts
                    if a["accountId"] == role["account_id"]
                ),
                "role_name": role["role_name"],
            }
            for role in SSO_ROLES
        ]
        self._build_step2()

    profile_dialog_mod.ProfileDialog._discover_accounts = _sync_discover_accounts

    def _sync_discover_bastions(self, profile, automatic=False):
        bastions = main_window_mod.discover_bastions(
            profile.name, self.settings.bastion_tag_key, self.settings.bastion_tag_value
        )
        self._finish_bastion_discovery(profile, bastions)

    main_window_mod.MainWindow._discover_bastions_for = _sync_discover_bastions

    # No real tray icon during capture.
    app_main.TrayIcon = _DummyTray

    return temp_dir


def main() -> None:
    enable_dpi_awareness()
    temp_dir = patch_environment()
    print(f"Mock data dir: {temp_dir}")

    capture_wizard()

    # Session B: run the real app entry point, but replace mainloop()
    # with our capture driver so we can step through the screens.
    original_mainloop = tk.Tk.mainloop

    def capturing_mainloop(self, *args, **kwargs):
        try:
            drive_app(self)
        finally:
            tk.Tk.mainloop = original_mainloop

    tk.Tk.mainloop = capturing_mainloop
    app_main.main()

    print(f"\nDone. Screenshots in {SHOTS_DIR}")


if __name__ == "__main__":
    main()
