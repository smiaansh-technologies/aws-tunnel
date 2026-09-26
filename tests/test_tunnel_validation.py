import pytest

from tunnel.validation import TunnelValidationError, validate_host, validate_port


@pytest.mark.parametrize("host", ["db.internal", "10.0.1.5", "2001:db8::1"])
def test_accepts_dns_and_ip_hosts(host):
    assert validate_host(host) == host


@pytest.mark.parametrize("host", ["", 'db.internal\"],host=[\"evil', "db internal", "host\nnext"])
def test_rejects_host_values_that_cannot_be_one_ssm_parameter(host):
    with pytest.raises(TunnelValidationError):
        validate_host(host)


@pytest.mark.parametrize("port", [0, -1, 65536, True])
def test_rejects_out_of_range_ports(port):
    with pytest.raises(TunnelValidationError):
        validate_port(port)
