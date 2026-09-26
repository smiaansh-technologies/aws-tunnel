"""Three-step SSO profile setup wizard mirroring `aws configure sso`.

Step 1: SSO session details (start URL + region) → authenticate via browser
Step 2: Discover and select account + role from SSO portal
Step 3: Profile name + default region → save
"""

from __future__ import annotations

import configparser
import json
import logging
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable
from urllib.parse import urlparse
from uuid import uuid4

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from aws.profiles import AWSProfile, create_profile
from process_utils import no_window_kwargs

log = logging.getLogger(__name__)

_AWS_REGIONS = [
    "us-east-1", "us-east-2", "us-west-1", "us-west-2",
    "eu-west-1", "eu-west-2", "eu-west-3", "eu-central-1", "eu-north-1",
    "ap-south-1", "ap-southeast-1", "ap-southeast-2", "ap-northeast-1",
    "ap-northeast-2", "sa-east-1", "ca-central-1", "me-south-1",
]

# Module-level cache so the SSM query runs at most once per app run.
_REGIONS_CACHE: list[str] | None = None


def fetch_aws_regions() -> list[str]:
    """Fetch the current AWS region list from the public SSM parameter
    `/aws/service/global-infrastructure/regions`.

    This parameter is maintained by AWS and requires no credentials.
    Falls back to the static list if the query fails (e.g. offline).
    """
    global _REGIONS_CACHE
    if _REGIONS_CACHE is not None:
        return _REGIONS_CACHE
    try:
        ssm = boto3.client("ssm", region_name="us-east-1")
        response = ssm.get_parameter(Name="/aws/service/global-infrastructure/regions")
        regions = sorted(response["Parameter"]["Value"])
        if regions:
            _REGIONS_CACHE = regions
            log.info("Fetched %d AWS regions from SSM public parameter", len(regions))
            return regions
    except Exception:
        log.debug("Could not fetch live AWS region list; using static fallback", exc_info=True)
    _REGIONS_CACHE = list(_AWS_REGIONS)
    return _REGIONS_CACHE


class ProfileDialog(tk.Toplevel):
    """Three-step wizard for creating a new SSO profile.

    Step 1: SSO session details (start URL + region) → authenticate
    Step 2: Select account + role from discovered SSO portal data
    Step 3: Profile name + default region → save
    """

    def __init__(self, parent: tk.Widget, on_created: Callable[[], None]) -> None:
        super().__init__(parent)
        self.title("AWS SSO Profile Setup")
        self.resizable(False, False)
        # Only make transient if the parent is actually visible — a transient
        # window is withdrawn automatically when its master is withdrawn,
        # which would hide the wizard when the main window is hidden.
        if parent.winfo_ismapped():
            self.transient(parent)
        self.grab_set()

        self._on_created = on_created
        self._sso_start_url = ""
        self._sso_region = ""
        self._temp_profile_name = ""
        self._accounts: list[dict] = []
        self._roles: list[str] = []
        self._entries: dict[str, tk.Widget] = {}
        self._progress: ttk.Progressbar | None = None
        self.protocol("WM_DELETE_WINDOW", self._cancel_and_cleanup)

        self._build_step1()
        self.after_idle(lambda: self._center_on_screen_if_parent_hidden(parent))

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _center_over_parent(self, parent: tk.Widget) -> None:
        parent_window = parent.winfo_toplevel()
        parent_window.update_idletasks()
        self.update_idletasks()
        width = self.winfo_reqwidth()
        height = self.winfo_reqheight()
        x = parent_window.winfo_rootx() + (parent_window.winfo_width() - width) // 2
        y = parent_window.winfo_rooty() + (parent_window.winfo_height() - height) // 2
        self.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 0)}")

    def _center_on_screen_if_parent_hidden(self, parent: tk.Widget) -> None:
        """Center over the parent window, or on the screen if it's hidden."""
        if parent.winfo_toplevel().winfo_ismapped():
            self._center_over_parent(parent)
            return
        self.update_idletasks()
        width = self.winfo_reqwidth()
        height = self.winfo_reqheight()
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        x = max((screen_w - width) // 2, 0)
        y = max((screen_h - height) // 2, 0)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _clear_form(self) -> None:
        for widget in self.winfo_children():
            widget.destroy()
        self._entries.clear()

    # ------------------------------------------------------------------
    # Step 1: SSO Session + Authenticate
    # ------------------------------------------------------------------

    def _build_step1(self) -> None:
        self.title("Step 1 of 3 — SSO Session")
        self._clear_form()

        container = ttk.Frame(self, padding=16)
        container.pack(fill="both", expand=True)

        header = ttk.Label(container, text="Configure SSO Session", font=("", 11, "bold"))
        header.pack(anchor="w")

        subtitle = ttk.Label(
            container,
            text="Enter your IAM Identity Center start URL and region,\n"
                 "then authenticate in the browser.",
            foreground="gray",
        )
        subtitle.pack(anchor="w", pady=(2, 12))

        form = ttk.Frame(container)
        form.pack(fill="x")

        regions = fetch_aws_regions()

        ttk.Label(form, text="SSO start URL:").grid(row=0, column=0, padx=(0, 8), pady=6, sticky="w")
        url_entry = ttk.Entry(form, width=44)
        url_entry.grid(row=0, column=1, pady=6, sticky="w")
        url_entry.bind("<FocusIn>", lambda _e: self._on_focus_in(url_entry))
        self._entries["sso_start_url"] = url_entry

        ttk.Label(form, text="SSO region:").grid(row=1, column=0, padx=(0, 8), pady=6, sticky="w")
        sso_region_combo = ttk.Combobox(form, values=regions, width=42, state="readonly")
        sso_region_combo.set("us-east-1")
        sso_region_combo.grid(row=1, column=1, pady=6, sticky="w")
        self._entries["sso_region"] = sso_region_combo

        self._progress = ttk.Progressbar(container, mode="indeterminate")
        self._progress.pack(fill="x", pady=(12, 0))

        self._status_label = ttk.Label(container, text="", foreground="gray")
        self._status_label.pack(anchor="w", pady=(4, 0))

        btn_frame = ttk.Frame(container)
        btn_frame.pack(fill="x", pady=(16, 0))
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(side="right", padx=4)
        self._auth_button = ttk.Button(
            btn_frame, text="Authenticate →", command=self._validate_step1
        )
        self._auth_button.pack(side="right", padx=4)

    def _validate_step1(self) -> None:
        url = self._entries["sso_start_url"].get().strip()
        region = self._entries["sso_region"].get().strip()

        if not url:
            messagebox.showerror("Missing field", "SSO start URL is required.", parent=self)
            return
        parsed_url = urlparse(url)
        if parsed_url.scheme != "https" or not parsed_url.netloc or parsed_url.username or parsed_url.password:
            messagebox.showerror(
                "Invalid URL",
                "SSO start URL must be a valid HTTPS URL without embedded credentials.",
                parent=self,
            )
            return
        if not region:
            messagebox.showerror("Missing field", "SSO region is required.", parent=self)
            return

        self._sso_start_url = url
        self._sso_region = region
        # Avoid a predictable name that could overwrite another temporary
        # session in a concurrent app instance.
        self._temp_profile_name = f"_aws_tunnel_setup_{uuid4().hex}"
        self._start_authentication()

    def _start_authentication(self) -> None:
        """Create a temp profile and run SSO login in a background thread."""
        self._auth_button.configure(state="disabled")
        self._progress.start(10)
        self._status_label.configure(text="Creating temporary SSO session...")

        def worker() -> None:
            try:
                self._create_temp_sso_session()
                self.after(0, lambda: self._status_label.configure(
                    text="Opening browser for authentication..."
                ))
                self._run_sso_login()
                self.after(0, self._on_auth_success)
            except Exception as exc:
                log.exception("SSO authentication failed")
                error_message = str(exc)
                self.after(0, lambda: self._on_auth_failure(error_message))

        threading.Thread(target=worker, daemon=True).start()

    def _create_temp_sso_session(self) -> None:
        """Write a temporary [sso-session] + [profile] to ~/.aws/config."""
        import configparser
        from pathlib import Path

        aws_config = Path.home() / ".aws" / "config"
        aws_config.parent.mkdir(parents=True, exist_ok=True)

        parser = configparser.ConfigParser()
        if aws_config.exists():
            parser.read(aws_config)

        session_section = f"sso-session {self._temp_profile_name}"
        profile_section = f"profile {self._temp_profile_name}"

        if not parser.has_section(session_section):
            parser.add_section(session_section)
        parser.set(session_section, "sso_start_url", self._sso_start_url)
        parser.set(session_section, "sso_region", self._sso_region)
        parser.set(session_section, "sso_registration_scopes", "sso:account:access")

        if not parser.has_section(profile_section):
            parser.add_section(profile_section)
        parser.set(profile_section, "sso_session", self._temp_profile_name)
        parser.set(profile_section, "region", self._sso_region)
        parser.set(profile_section, "output", "json")

        with aws_config.open("w") as fh:
            parser.write(fh)

    def _run_sso_login(self) -> None:
        """Run `aws sso login` for the temporary profile."""
        result = subprocess.run(
            ["aws", "sso", "login", "--profile", self._temp_profile_name],
            capture_output=True,
            text=True,
            timeout=180,
            **no_window_kwargs(),
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "SSO login failed")

    def _on_auth_success(self) -> None:
        """Called on the main thread after successful SSO login."""
        self._progress.stop()
        self._status_label.configure(text="Authentication successful! Discovering accounts...")
        self._discover_accounts()

    def _on_auth_failure(self, error: str) -> None:
        """Called on the main thread after failed SSO login."""
        self._progress.stop()
        self._auth_button.configure(state="normal")
        self._status_label.configure(text="Authentication failed.")
        messagebox.showerror("Authentication failed", error, parent=self)

    # ------------------------------------------------------------------
    # Step 2: Discover accounts + roles
    # ------------------------------------------------------------------

    def _discover_accounts(self) -> None:
        """Query SSO for available accounts and roles via AWS CLI."""
        def worker() -> None:
            try:
                accounts = self._list_sso_accounts()
                self._accounts = accounts
                if not accounts:
                    self.after(0, lambda: self._on_discovery_empty())
                    return

                all_roles = []
                for account in accounts:
                    roles = self._list_sso_roles(account["accountId"])
                    for role in roles:
                        all_roles.append({
                            "account_id": account["accountId"],
                            "account_name": account.get("accountName", account["accountId"]),
                            "role_name": role["roleName"],
                        })
                self._roles = all_roles
                self.after(0, self._build_step2)
            except Exception as exc:
                log.exception("Account discovery failed")
                error_message = str(exc)
                self.after(0, lambda: self._on_discovery_failure(error_message))

        threading.Thread(target=worker, daemon=True).start()

    def _list_sso_accounts(self) -> list[dict]:
        """List accounts accessible via the temporary SSO profile using boto3."""
        access_token = self._get_sso_access_token()
        sso = boto3.client("sso", region_name=self._sso_region)
        accounts = []
        paginator = sso.get_paginator("list_accounts")
        for page in paginator.paginate(accessToken=access_token):
            accounts.extend(page.get("accountList", []))
        return accounts

    def _list_sso_roles(self, account_id: str) -> list[dict]:
        """List roles accessible in a given account via SSO using boto3."""
        access_token = self._get_sso_access_token()
        sso = boto3.client("sso", region_name=self._sso_region)
        roles = []
        paginator = sso.get_paginator("list_account_roles")
        for page in paginator.paginate(accessToken=access_token, accountId=account_id):
            roles.extend(page.get("roleList", []))
        return roles

    def _get_sso_access_token(self) -> str:
        """Read the SSO access token from the AWS CLI cache."""
        cache_dir = Path.home() / ".aws" / "sso" / "cache"
        if not cache_dir.exists():
            raise RuntimeError("SSO cache directory not found. Please authenticate first.")

        for cache_file in cache_dir.glob("*.json"):
            try:
                data = json.loads(cache_file.read_text())
                if data.get("startUrl") == self._sso_start_url:
                    token = data.get("accessToken")
                    if token:
                        return token
            except (json.JSONDecodeError, OSError):
                continue

        raise RuntimeError(
            f"No valid SSO token found for {self._sso_start_url}.\n"
            "Please authenticate first."
        )

    def _on_discovery_empty(self) -> None:
        self._progress.stop()
        self._status_label.configure(text="No accounts found for this SSO session.")
        messagebox.showinfo(
            "No accounts",
            "No AWS accounts or roles were found for this SSO session.\n"
            "Check your SSO start URL and ensure you have access.",
            parent=self,
        )
        self._cleanup_temp_profile()
        self._auth_button.configure(state="normal")

    def _on_discovery_failure(self, error: str) -> None:
        self._progress.stop()
        self._status_label.configure(text="Discovery failed.")
        messagebox.showerror("Discovery failed", error, parent=self)
        self._cleanup_temp_profile()
        self._auth_button.configure(state="normal")

    def _cleanup_temp_profile(self) -> None:
        """Remove the temporary profile from ~/.aws/config."""
        import configparser
        from pathlib import Path

        aws_config = Path.home() / ".aws" / "config"
        if not aws_config.exists():
            return

        parser = configparser.ConfigParser()
        parser.read(aws_config)

        session_section = f"sso-session {self._temp_profile_name}"
        profile_section = f"profile {self._temp_profile_name}"

        if parser.has_section(session_section):
            parser.remove_section(session_section)
        if parser.has_section(profile_section):
            parser.remove_section(profile_section)

        with aws_config.open("w") as fh:
            parser.write(fh)

    # ------------------------------------------------------------------
    # Step 2: Account + Role Selection
    # ------------------------------------------------------------------

    def _build_step2(self) -> None:
        self._clear_form()
        self.title("Step 2 of 3 — Select Account & Role")

        container = ttk.Frame(self, padding=16)
        container.pack(fill="both", expand=True)

        header = ttk.Label(container, text="Select Account & Role", font=("", 11, "bold"))
        header.pack(anchor="w")

        subtitle = ttk.Label(
            container,
            text=f"Found {len(self._roles)} role(s) across {len(self._accounts)} account(s).",
            foreground="gray",
        )
        subtitle.pack(anchor="w", pady=(2, 12))

        form = ttk.Frame(container)
        form.pack(fill="x")

        # Account dropdown
        ttk.Label(form, text="Account:").grid(row=0, column=0, padx=(0, 8), pady=(8, 4), sticky="w")
        account_labels = [f"{a.get('accountName', a['accountId'])} ({a['accountId']})" for a in self._accounts]
        self._account_var = tk.StringVar(value=account_labels[0] if account_labels else "")
        account_combo = ttk.Combobox(
            form, textvariable=self._account_var, values=account_labels,
            state="readonly", width=42,
        )
        account_combo.grid(row=0, column=1, pady=(8, 4), sticky="w")
        account_combo.bind("<<ComboboxSelected>>", self._on_account_selected)

        # Role listbox
        ttk.Label(form, text="Role:").grid(row=1, column=0, padx=(0, 8), pady=(4, 8), sticky="nw")
        roles_for_first = [r["role_name"] for r in self._roles
                           if r["account_id"] == (self._accounts[0]["accountId"] if self._accounts else "")]
        listbox_height = min(len(roles_for_first) or 1, 8)
        self._role_listbox = tk.Listbox(form, height=listbox_height, width=44, exportselection=False)
        self._role_listbox.grid(row=1, column=1, pady=(4, 8), sticky="w")

        # Populate roles for first account
        self._populate_roles_for_account(self._accounts[0]["accountId"] if self._accounts else "")

        btn_frame = ttk.Frame(container)
        btn_frame.pack(fill="x", pady=(16, 0))
        ttk.Button(btn_frame, text="Cancel", command=self._cancel_and_cleanup).pack(side="right", padx=4)
        ttk.Button(btn_frame, text="Next →", command=self._validate_step2).pack(side="right", padx=4)
        ttk.Button(btn_frame, text="← Back", command=self._go_back_to_step1).pack(side="right", padx=4)

    def _on_account_selected(self, _event: tk.Event) -> None:
        label = self._account_var.get()
        # Extract account ID from label like "Name (123456789012)"
        account_id = label.split("(")[-1].rstrip(")") if "(" in label else label
        self._populate_roles_for_account(account_id)

    def _populate_roles_for_account(self, account_id: str) -> None:
        self._role_listbox.delete(0, tk.END)
        roles_for_account = [r["role_name"] for r in self._roles if r["account_id"] == account_id]
        for role in roles_for_account:
            self._role_listbox.insert(tk.END, role)
        if roles_for_account:
            self._role_listbox.selection_set(0)

    def _go_back_to_step1(self) -> None:
        self._cleanup_temp_profile()
        self._build_step1()
        self._entries["sso_start_url"].insert(0, self._sso_start_url)
        self._entries["sso_region"].set(self._sso_region)

    def _validate_step2(self) -> None:
        account_label = self._account_var.get()
        role_selection = self._role_listbox.curselection()

        if not account_label:
            messagebox.showerror("No selection", "Please select an account.", parent=self)
            return
        if not role_selection:
            messagebox.showerror("No selection", "Please select a role.", parent=self)
            return

        account_id = account_label.split("(")[-1].rstrip(")") if "(" in account_label else account_label
        role_name = self._role_listbox.get(role_selection[0])

        self._selected_account_id = account_id
        self._selected_account_name = account_label.split(" (")[0] if " (" in account_label else account_id
        self._selected_role_name = role_name

        self._build_step3()

    # ------------------------------------------------------------------
    # Step 3: Profile Name + Default Region
    # ------------------------------------------------------------------

    def _build_step3(self) -> None:
        self._clear_form()
        self.title("Step 3 of 3 — Profile Details")

        container = ttk.Frame(self, padding=16)
        container.pack(fill="both", expand=True)

        header = ttk.Label(container, text="Profile Details", font=("", 11, "bold"))
        header.pack(anchor="w")

        subtitle = ttk.Label(
            container,
            text=f"Account: {self._selected_account_name} ({self._selected_account_id})\n"
                 f"Role: {self._selected_role_name}",
            foreground="gray",
        )
        subtitle.pack(anchor="w", pady=(2, 12))

        form = ttk.Frame(container)
        form.pack(fill="x")

        # Profile name
        ttk.Label(form, text="Profile name:").grid(row=0, column=0, padx=(0, 8), pady=6, sticky="w")
        name_entry = ttk.Entry(form, width=44)
        name_entry.grid(row=0, column=1, pady=6, sticky="w")
        name_entry.bind("<FocusIn>", lambda _e, w=name_entry: self._on_focus_in(w))
        self._entries["name"] = name_entry

        # Default region
        ttk.Label(form, text="Default region:").grid(row=1, column=0, padx=(0, 8), pady=6, sticky="w")
        region_combo = ttk.Combobox(form, values=fetch_aws_regions(), width=41, state="readonly")
        region_combo.set(self._sso_region)
        region_combo.grid(row=1, column=1, pady=6, sticky="w")
        self._entries["region"] = region_combo

        btn_frame = ttk.Frame(container)
        btn_frame.pack(fill="x", pady=(16, 0))
        ttk.Button(btn_frame, text="Cancel", command=self._cancel_and_cleanup).pack(side="right", padx=4)
        ttk.Button(btn_frame, text="Create", command=self._validate_step3).pack(side="right", padx=4)
        ttk.Button(btn_frame, text="← Back", command=self._go_back_to_step2).pack(side="right", padx=4)

    def _go_back_to_step2(self) -> None:
        self._build_step2()

    def _validate_step3(self) -> None:
        profile_name = self._entries["name"].get().strip()
        region = self._entries["region"].get().strip()

        if not profile_name:
            messagebox.showerror("Missing field", "Profile name is required.", parent=self)
            return

        profile = AWSProfile(
            name=profile_name,
            sso_start_url=self._sso_start_url,
            sso_region=self._sso_region,
            sso_account_id=self._selected_account_id,
            sso_role_name=self._selected_role_name,
            region=region,
        )

        try:
            create_profile(profile)
        except OSError as exc:
            log.exception("Failed to write profile")
            messagebox.showerror("Error", f"Could not save profile: {exc}", parent=self)
            return

        # Clean up the temporary profile now that the real one is created
        self._cleanup_temp_profile()

        messagebox.showinfo(
            "Profile created",
            f"Profile '{profile_name}' created successfully.\n"
            f"Account: {self._selected_account_name} ({self._selected_account_id})\n"
            f"Role: {self._selected_role_name}",
            parent=self,
        )
        self._on_created()
        self.destroy()

    def _cancel_and_cleanup(self) -> None:
        self._cleanup_temp_profile()
        self.destroy()

    @staticmethod
    def _on_focus_in(entry: tk.Widget) -> None:
        """Select all text when an entry gains focus."""
        if isinstance(entry, (ttk.Entry, tk.Entry)):
            entry.after(10, lambda: entry.select_range(0, tk.END))
