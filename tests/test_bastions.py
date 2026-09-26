from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from aws import bastions


def test_discover_bastions_uses_tagged_instances_and_online_status():
    ec2 = MagicMock()
    ssm = MagicMock()
    session = MagicMock()
    session.client.side_effect = [ec2, ssm]
    instance = {
        "InstanceId": "i-123", "PrivateIpAddress": "10.0.0.4",
        "State": {"Name": "running"}, "Tags": [{"Key": "Name", "Value": "jump"}],
    }
    with patch("aws.bastions.boto3.Session", return_value=session), \
         patch("aws.bastions._describe_tagged_instances", return_value=[instance]), \
         patch("aws.bastions._ssm_online_instance_ids", return_value={"i-123"}):
        found = bastions.discover_bastions("dev", "Role", "bastion")
    assert found == [bastions.Bastion("i-123", "jump", "10.0.0.4", "running", True)]


def test_discover_bastions_falls_back_and_wraps_aws_errors():
    session = MagicMock()
    session.client.side_effect = ClientError({"Error": {"Code": "Denied", "Message": "no"}}, "Describe")
    with patch("aws.bastions.boto3.Session", return_value=session):
        with pytest.raises(bastions.BastionDiscoveryError, match="no"):
            bastions.discover_bastions("dev", "Role", "bastion")


def test_describe_helpers_and_name_tag():
    ec2 = MagicMock()
    ec2.get_paginator.return_value.paginate.return_value = [
        {"Reservations": [{"Instances": [{"InstanceId": "i-1"}]}]}
    ]
    assert bastions._describe_tagged_instances(ec2, "Role", "b") == [{"InstanceId": "i-1"}]
    assert bastions._describe_running_instances(ec2) == [{"InstanceId": "i-1"}]
    assert bastions._name_tag({"InstanceId": "i-1", "Tags": []}) == "i-1"
    assert bastions._name_tag({"InstanceId": "i-1", "Tags": [{"Key": "Name", "Value": "n"}]}) == "n"


def test_ssm_online_instance_ids():
    ssm = MagicMock()
    ssm.get_paginator.return_value.paginate.return_value = [{"InstanceInformationList": [
        {"InstanceId": "i-up", "PingStatus": "Online"}, {"InstanceId": "i-down", "PingStatus": "Offline"}
    ]}]
    assert bastions._ssm_online_instance_ids(ssm) == {"i-up"}
