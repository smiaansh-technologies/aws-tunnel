"""
Tests for tunnel/manager.py's bookkeeping, using fake handles instead of
real SSM/SSH connections.
"""

from unittest.mock import MagicMock, patch

from tunnel.manager import TunnelManager


def test_register_and_list_tunnel():
    manager = TunnelManager()
    fake_process = MagicMock()
    fake_process.poll.return_value = None  # still running

    with patch("tunnel.manager.ssm_tunnel.start", return_value=fake_process):
        tunnel_id = manager.start_ssm_tunnel(
            profile_name="dev",
            instance_id="i-1234567890abcdef0",
            bastion_label="dev-bastion",
            target_host="[IP_ADDRESS]",
            target_port=5432,
            local_port=15432,
        )

    tunnels = manager.list_tunnels()
    assert len(tunnels) == 1
    assert tunnels[0].id == tunnel_id
    assert tunnels[0].local_port == 15432


def test_saved_profile_name_is_recorded_on_the_tunnel():
    """Active Tunnels shows the saved Tunnel Profile name in its first column."""
    manager = TunnelManager()
    fake_process = MagicMock()
    fake_process.poll.return_value = None

    with patch("tunnel.manager.ssm_tunnel.start", return_value=fake_process):
        manager.start_ssm_tunnel(
            profile_name="dev",
            instance_id="i-abc",
            bastion_label="dev-bastion",
            target_host="db.internal",
            target_port=5432,
            local_port=15432,
            profile_label="dev-postgres",
        )

    assert manager.list_tunnels()[0].profile_label == "dev-postgres"


def test_adhoc_tunnel_has_no_profile_label():
    """Tunnels opened without a saved profile leave the label unset."""
    manager = TunnelManager()
    fake_process = MagicMock()
    fake_process.poll.return_value = None

    with patch("tunnel.manager.ssm_tunnel.start", return_value=fake_process):
        manager.start_ssm_tunnel(
            profile_name="dev", instance_id="i-abc", bastion_label="b",
            target_host="db.internal", target_port=80, local_port=8080,
        )

    assert manager.list_tunnels()[0].profile_label is None


def test_stop_tunnel_removes_it():
    manager = TunnelManager()
    fake_process = MagicMock()
    fake_process.poll.return_value = None

    with patch("tunnel.manager.ssm_tunnel.start", return_value=fake_process):
        tunnel_id = manager.start_ssm_tunnel(
            profile_name="dev", instance_id="i-abc", bastion_label="b",
            target_host="10.0.1.5", target_port=80, local_port=8080,
        )

    with patch("tunnel.manager.ssm_tunnel.stop") as mock_stop:
        manager.stop_tunnel(tunnel_id)
        mock_stop.assert_called_once_with(fake_process)

    assert manager.list_tunnels() == []


def test_failed_stop_keeps_tunnel_registered_for_retry():
    manager = TunnelManager()
    fake_process = MagicMock()
    fake_process.poll.return_value = None

    with patch("tunnel.manager.ssm_tunnel.start", return_value=fake_process):
        tunnel_id = manager.start_ssm_tunnel(
            profile_name="dev", instance_id="i-abc", bastion_label="b",
            target_host="10.0.1.5", target_port=80, local_port=8080,
        )

    with patch("tunnel.manager.ssm_tunnel.stop", side_effect=OSError("still running")):
        try:
            manager.stop_tunnel(tunnel_id)
        except OSError:
            pass
        else:
            raise AssertionError("Expected the stop error to reach the caller")

    assert [tunnel.id for tunnel in manager.list_tunnels()] == [tunnel_id]


def test_dead_process_is_pruned_on_list():
    manager = TunnelManager()
    fake_process = MagicMock()
    fake_process.poll.return_value = 1  # already exited

    with patch("tunnel.manager.ssm_tunnel.start", return_value=fake_process):
        manager.start_ssm_tunnel(
            profile_name="dev", instance_id="i-abc", bastion_label="b",
            target_host="10.0.1.5", target_port=80, local_port=8080,
        )

    assert manager.list_tunnels() == []
    dead = manager.take_recently_dead()
    assert len(dead) == 1
    assert dead[0].target_host == "10.0.1.5"


def test_manager_registers_and_stops_ssh_and_interactive_sessions():
    manager = TunnelManager()
    ssh_handle = MagicMock()
    session_process = MagicMock()
    session_process.poll.return_value = None
    with patch("tunnel.manager.ssh_tunnel.start", return_value=ssh_handle):
        ssh_id = manager.start_ssh_tunnel("host", 22, "user", "key", "b", "db", 5432, 15432)
    with patch("tunnel.manager.ssm_tunnel.start_session", return_value=session_process):
        session_id = manager.start_ssm_session("dev", "i-12345678", "b")
    with patch("tunnel.manager.ssh_tunnel.stop") as stop_ssh, \
         patch("tunnel.manager.ssm_tunnel.stop_session") as stop_session:
        manager.stop_tunnel(ssh_id)
        manager.stop_tunnel(session_id)
    stop_ssh.assert_called_once_with(ssh_handle)
    stop_session.assert_called_once_with(session_process)


def test_stop_unknown_and_stop_all_are_safe():
    manager = TunnelManager()
    manager.stop_tunnel("missing")
    manager.stop_all()
