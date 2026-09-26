from subprocess import TimeoutExpired
from unittest.mock import MagicMock, patch

import pytest

from aws import sso_login


def test_login_and_logout_invoke_aws_cli():
    result = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch("aws.sso_login.subprocess.run", return_value=result) as run:
        sso_login.login("dev")
        sso_login.logout("dev")
    assert run.call_args_list[0].args[0] == ["aws", "sso", "login", "--profile", "dev"]
    assert run.call_args_list[1].args[0] == ["aws", "sso", "logout", "--profile", "dev"]


@pytest.mark.parametrize("side_effect, message", [
    (FileNotFoundError(), "AWS CLI not found"),
    (TimeoutExpired(["aws"], 1), "Timed out"),
])
def test_run_reports_cli_start_and_timeout_errors(side_effect, message):
    with patch("aws.sso_login.subprocess.run", side_effect=side_effect):
        with pytest.raises(sso_login.SsoLoginError, match=message):
            sso_login._run(["aws"])


def test_run_reports_cli_failure_and_logs_output():
    result = MagicMock(returncode=1, stdout="debug output", stderr="access denied")
    with patch("aws.sso_login.subprocess.run", return_value=result):
        with pytest.raises(sso_login.SsoLoginError, match="access denied"):
            sso_login._run(["aws", "sso", "login"])


def test_run_uses_unknown_error_when_cli_omits_stderr():
    result = MagicMock(returncode=1, stdout="", stderr="")
    with patch("aws.sso_login.subprocess.run", return_value=result):
        with pytest.raises(sso_login.SsoLoginError, match="Unknown AWS CLI error"):
            sso_login._run(["aws"])
