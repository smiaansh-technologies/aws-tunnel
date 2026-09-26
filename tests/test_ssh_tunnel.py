from unittest.mock import MagicMock, patch

import pytest

from tunnel import ssh_tunnel


def _client_and_transport():
    client = MagicMock()
    transport = MagicMock()
    transport.is_active.return_value = True
    client.get_transport.return_value = transport
    return client, transport


def test_start_verifies_host_key_and_starts_local_server():
    client, _transport = _client_and_transport()
    server = MagicMock()
    with patch("tunnel.ssh_tunnel.paramiko.SSHClient", return_value=client), \
         patch("tunnel.ssh_tunnel._LocalOnlyTCPServer", return_value=server), \
         patch("tunnel.ssh_tunnel.threading.Thread") as thread:
        handle = ssh_tunnel.start("bastion.example", 22, "ubuntu", "key.pem", "db.internal", 5432, 15432)
    client.load_system_host_keys.assert_called_once()
    assert isinstance(client.set_missing_host_key_policy.call_args.args[0], ssh_tunnel.paramiko.RejectPolicy)
    thread.return_value.start.assert_called_once()
    assert handle.server is server


def test_start_wraps_connection_and_validation_errors():
    with pytest.raises(ssh_tunnel.SshTunnelError):
        ssh_tunnel.start("bad host!", 22, "u", "k", "db", 1, 2)
    client = MagicMock()
    client.connect.side_effect = OSError("refused")
    with patch("tunnel.ssh_tunnel.paramiko.SSHClient", return_value=client):
        with pytest.raises(ssh_tunnel.SshTunnelError, match="refused"):
            ssh_tunnel.start("bastion", 22, "u", "k", "db", 1, 2)


def test_start_closes_client_when_server_setup_fails():
    client, _transport = _client_and_transport()
    with patch("tunnel.ssh_tunnel.paramiko.SSHClient", return_value=client), \
         patch("tunnel.ssh_tunnel._LocalOnlyTCPServer", side_effect=OSError("in use")):
        with pytest.raises(OSError):
            ssh_tunnel.start("bastion", 22, "u", "k", "db", 1, 2)
    client.close.assert_called_once()


def test_stop_closes_server_and_client():
    handle = ssh_tunnel.SshTunnelHandle(MagicMock(), MagicMock(), MagicMock())
    ssh_tunnel.stop(handle)
    handle.server.shutdown.assert_called_once()
    handle.server.server_close.assert_called_once()
    handle.client.close.assert_called_once()


def test_forward_handler_opens_channel_and_closes_resources():
    transport = MagicMock()
    channel = MagicMock()
    transport.open_channel.return_value = channel
    handler_class = ssh_tunnel._make_forwarder_class(transport, "db", 5432)
    handler = handler_class.__new__(handler_class)
    handler.request = MagicMock()
    handler.request.getpeername.return_value = ("127.0.0.1", 50000)
    with patch("tunnel.ssh_tunnel._pump") as pump:
        handler.handle()
    pump.assert_called_once_with(handler.request, channel)
    channel.close.assert_called_once()
    handler.request.close.assert_called_once()


def test_forward_handler_handles_channel_failure_and_empty_channel():
    transport = MagicMock()
    handler_class = ssh_tunnel._make_forwarder_class(transport, "db", 5432)
    handler = handler_class.__new__(handler_class)
    handler.request = MagicMock()
    transport.open_channel.return_value = None
    handler.handle()
    transport.open_channel.side_effect = ssh_tunnel.paramiko.SSHException("no channel")
    handler.handle()


def test_pump_relays_both_directions_and_stops_on_eof():
    sock = MagicMock()
    channel = MagicMock()
    sock.recv.return_value = b""
    with patch("tunnel.ssh_tunnel.select.select", return_value=([sock], [], [])):
        ssh_tunnel._pump(sock, channel)
    sock.settimeout.assert_called_once_with(30)


def test_pump_sends_data_in_both_directions():
    sock = MagicMock()
    channel = MagicMock()
    sock.recv.side_effect = [b"out", b""]
    channel.recv.return_value = b"in"
    with patch("tunnel.ssh_tunnel.select.select", side_effect=[([sock, channel], [], []), ([sock], [], [])]):
        ssh_tunnel._pump(sock, channel)
    channel.sendall.assert_called_once_with(b"out")
    sock.sendall.assert_called_once_with(b"in")
