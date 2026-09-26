"""
Finds candidate bastion/jump hosts for a given profile.

Discovery is tag-based by default (e.g. Role=bastion) since that's the
most common convention; the tag key/value are configurable in Settings
so this doesn't need code changes if your environment tags things
differently.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import boto3
from botocore.exceptions import BotoCoreError, ClientError

log = logging.getLogger(__name__)


@dataclass
class Bastion:
    instance_id: str
    name: str
    private_ip: str
    state: str
    ssm_online: bool


class BastionDiscoveryError(RuntimeError):
    """Raised when EC2/SSM lookups fail (bad profile, no permissions, etc.)."""


def discover_bastions(profile_name: str, tag_key: str, tag_value: str) -> list[Bastion]:
    """
    Return running EC2 instances tagged `tag_key=tag_value` for this
    profile, annotated with whether the SSM Agent currently reports them
    online (required for SSM-based tunnels).
    """
    try:
        session = boto3.Session(profile_name=profile_name)
        ec2 = session.client("ec2")
        ssm = session.client("ssm")

        instances = _describe_tagged_instances(ec2, tag_key, tag_value)
        if not instances:
            log.warning(
                "No running instances matched %s=%s for profile '%s'; "
                "falling back to all running instances",
                tag_key,
                tag_value,
                profile_name,
            )
            instances = _describe_running_instances(ec2)
        online_ids = _ssm_online_instance_ids(ssm)

        bastions = [
            Bastion(
                instance_id=inst["InstanceId"],
                name=_name_tag(inst),
                private_ip=inst.get("PrivateIpAddress", ""),
                state=inst["State"]["Name"],
                ssm_online=inst["InstanceId"] in online_ids,
            )
            for inst in instances
        ]
    except (BotoCoreError, ClientError) as exc:
        log.error("Bastion discovery failed for profile '%s': %s", profile_name, exc)
        raise BastionDiscoveryError(str(exc)) from exc

    log.info("Found %d bastion candidate(s) for profile '%s'", len(bastions), profile_name)
    return bastions


def _describe_tagged_instances(ec2, tag_key: str, tag_value: str) -> list[dict]:
    paginator = ec2.get_paginator("describe_instances")
    filters = [
        {"Name": f"tag:{tag_key}", "Values": [tag_value]},
        {"Name": "instance-state-name", "Values": ["running"]},
    ]
    instances = []
    for page in paginator.paginate(Filters=filters):
        for reservation in page["Reservations"]:
            instances.extend(reservation["Instances"])
    return instances


def _describe_running_instances(ec2) -> list[dict]:
    paginator = ec2.get_paginator("describe_instances")
    instances = []
    for page in paginator.paginate(
        Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
    ):
        for reservation in page["Reservations"]:
            instances.extend(reservation["Instances"])
    return instances


def _ssm_online_instance_ids(ssm) -> set[str]:
    paginator = ssm.get_paginator("describe_instance_information")
    online = set()
    for page in paginator.paginate():
        for info in page["InstanceInformationList"]:
            if info.get("PingStatus") == "Online":
                online.add(info["InstanceId"])
    return online


def _name_tag(instance: dict) -> str:
    for tag in instance.get("Tags", []):
        if tag["Key"] == "Name":
            return tag["Value"]
    return instance["InstanceId"]
