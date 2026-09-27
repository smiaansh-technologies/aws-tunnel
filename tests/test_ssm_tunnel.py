from unittest.mock import MagicMock, patch

import pytest

from tunnel import ssm_tunnel


def test_start_builds_safe_port_forward_command():
    process = MagicMock()
    with patch("tunnel.ssm_tunnel.subprocess.Popen", return_value=process) as popen:
        assert ssm_tunnel.start("dev", "i-12345678", "db.internal", 5432, 15432, "ap-south-1") is process
    command = popen.call_args.args[0]
    assert command[:6] == ["aws", "ssm", "start-session", "--profile", "dev", "--target"]
    assert 'host=["db.internal"]' in command[-1]
    if ssm_tunnel.platform.system() == "Windows":
        assert popen.call_args.kwargs["creationflags"] == (
            ssm_tunnel.subprocess.CREATE_NEW_PROCESS_GROUP | ssm_tunnel.subprocess.CREATE_NO_WINDOW
        )
    else:
        assert popen.call_args.kwargs["start_new_session"] is True


def test_background_launch_has_no_console_window():
    """The port-forward tunnel must not open a visible console window."""
    with patch("tunnel.ssm_tunnel.subprocess.Popen", return_value=MagicMock()) as popen:
        ssm_tunnel.start("dev", "i-12345678", "db.internal", 5432, 15432)
    if ssm_tunnel.platform.system() != "Windows":
        # POSIX has no console windows; the child just gets its own session.
        assert popen.call_args.kwargs["start_new_session"] is True
        return
    flags = popen.call_args.kwargs["creationflags"]
    assert flags & ssm_tunnel.subprocess.CREATE_NO_WINDOW
    assert not flags & ssm_tunnel.subprocess.CREATE_NEW_CONSOLE


def test_interactive_session_gets_new_console():
    """The interactive shell session intentionally opens a console window."""
    # The CREATE_* constants only exist on Windows, so inject the real
    # Windows values to exercise the Windows branch from any platform.
    windows_flags = {
        "CREATE_NEW_CONSOLE": 0x10,
        "CREATE_NEW_PROCESS_GROUP": 0x200,
        "CREATE_NO_WINDOW": 0x08000000,
    }
    with patch("tunnel.ssm_tunnel.platform.system", return_value="Windows"), \
         patch.multiple(
             ssm_tunnel.subprocess,
             CREATE_NEW_CONSOLE=windows_flags["CREATE_NEW_CONSOLE"],
             CREATE_NEW_PROCESS_GROUP=windows_flags["CREATE_NEW_PROCESS_GROUP"],
             CREATE_NO_WINDOW=windows_flags["CREATE_NO_WINDOW"],
             create=True,
         ):
        kwargs = ssm_tunnel._process_start_kwargs(new_console=True)
    flags = kwargs["creationflags"]
    assert flags & windows_flags["CREATE_NEW_CONSOLE"]
    assert not flags & windows_flags["CREATE_NO_WINDOW"]


@pytest.mark.parametrize("kwargs", [
    {"profile_name": "", "instance_id": "i-12345678", "target_host": "db", "target_port": 1, "local_port": 2},
    {"profile_name": "dev", "instance_id": "bad", "target_host": "db", "target_port": 1, "local_port": 2},
])
def test_start_rejects_invalid_values(kwargs):
    with pytest.raises(ssm_tunnel.SsmTunnelError):
        ssm_tunnel.start(**kwargs)


def test_start_wraps_missing_aws_cli():
    with patch("tunnel.ssm_tunnel.subprocess.Popen", side_effect=FileNotFoundError):
        with pytest.raises(ssm_tunnel.SsmTunnelError, match="not found"):
            ssm_tunnel.start("dev", "i-12345678", "db", 1, 2)


def test_stop_uses_process_group_and_waits_on_posix():
    process = MagicMock(pid=123)
    process.poll.return_value = None
    with patch("tunnel.ssm_tunnel.platform.system", return_value="Linux"), \
         patch("tunnel.ssm_tunnel.os.getpgid", return_value=123, create=True), \
         patch("tunnel.ssm_tunnel.os.killpg", create=True) as killpg, \
         patch.object(ssm_tunnel.signal, "SIGKILL", 9, create=True):
        ssm_tunnel.stop(process)
    assert killpg.called
    process.wait.assert_called_once_with(timeout=5)


def test_stop_escalates_to_kill_on_timeout():
    process = MagicMock(pid=123)
    process.poll.return_value = None
    process.wait.side_effect = [ssm_tunnel.subprocess.TimeoutExpired(["aws"], 5), None]
    with patch("tunnel.ssm_tunnel.platform.system", return_value="Linux"), \
         patch("tunnel.ssm_tunnel.os.getpgid", return_value=123, create=True), \
         patch("tunnel.ssm_tunnel.os.killpg", create=True) as killpg, \
         patch.object(ssm_tunnel.signal, "SIGKILL", 9, create=True):
        ssm_tunnel.stop(process)
    assert killpg.call_count == 2


def test_stop_uses_taskkill_on_windows():
    process = MagicMock(pid=123)
    process.poll.return_value = None
    with patch("tunnel.ssm_tunnel.platform.system", return_value="Windows"), \
         patch("tunnel.ssm_tunnel.subprocess.run") as run:
        ssm_tunnel.stop_session(process)
    assert run.call_args.args[0] == ["taskkill", "/PID", "123", "/T", "/F"]


def test_start_session_validation_and_platform_dispatch():
    with pytest.raises(ssm_tunnel.SsmSessionError):
        ssm_tunnel.start_session("dev", "not-an-id")
    with patch("tunnel.ssm_tunnel.platform.system", return_value="Windows"), \
         patch("tunnel.ssm_tunnel._start_session_windows", return_value=MagicMock()) as start:
        ssm_tunnel.start_session("dev", "i-12345678", "ap-south-1")
    assert "--region" in start.call_args.args[0]


def test_session_launchers_wrap_missing_cli():
    with patch("tunnel.ssm_tunnel.subprocess.Popen", side_effect=FileNotFoundError):
        with pytest.raises(ssm_tunnel.SsmSessionError):
            ssm_tunnel._start_session_posix(["aws"])
        with pytest.raises(ssm_tunnel.SsmSessionError):
            ssm_tunnel._start_session_windows(["aws"])
