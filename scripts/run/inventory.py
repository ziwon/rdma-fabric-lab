"""Lab workers as the runner sees them, read from the same inventories Ansible uses."""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Guest:
    name: str
    ssh: tuple[str, ...]
    mgmt_nic: str
    rdma_nic: str
    rdma_ip: str
    rxe_device: str
    uplink_nic: str
    mgmt_ip: str | None


REQUIRED = ("lab_mgmt_nic", "lab_rdma_nic", "lab_rdma_ip", "lab_rxe_device")


def ssh_argv(hostvars: dict) -> tuple[str, ...]:
    """ssh command up to and including the destination, from Ansible connection variables."""
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=yes"]
    if "ansible_port" in hostvars:
        argv += ["-p", str(hostvars["ansible_port"])]
    if "ansible_ssh_private_key_file" in hostvars:
        argv += ["-i", str(hostvars["ansible_ssh_private_key_file"])]
    argv += shlex.split(str(hostvars.get("ansible_ssh_common_args", "")))
    user = hostvars.get("ansible_user")
    host = hostvars.get("ansible_host", hostvars.get("inventory_hostname"))
    argv.append(f"{user}@{host}" if user else str(host))
    return tuple(argv)


def guest_from_hostvars(name: str, hostvars: dict) -> Guest:
    missing = [k for k in REQUIRED if k not in hostvars]
    if missing:
        raise ValueError(f"{name}: inventory lacks {', '.join(missing)}. Pass the override inventory that names the NICs.")
    return Guest(
        name=name,
        ssh=ssh_argv(hostvars),
        mgmt_nic=str(hostvars["lab_mgmt_nic"]),
        rdma_nic=str(hostvars["lab_rdma_nic"]),
        rdma_ip=str(hostvars["lab_rdma_ip"]),
        rxe_device=str(hostvars["lab_rxe_device"]),
        uplink_nic=str(hostvars.get("lab_uplink_nic", hostvars["lab_mgmt_nic"])),
        mgmt_ip=str(hostvars["k8s_node_ip"]) if "k8s_node_ip" in hostvars else None,
    )


def guests_from_listing(listing: dict, group: str = "workers") -> dict[str, Guest]:
    hosts = listing.get(group, {}).get("hosts", [])
    if not hosts:
        raise ValueError(f"inventory group {group} has no hosts")
    hostvars = listing["_meta"]["hostvars"]
    return {name: guest_from_hostvars(name, hostvars[name]) for name in hosts}


def load(inventories: list[Path], group: str = "workers") -> dict[str, Guest]:
    argv = ["ansible-inventory", "--list"]
    for inventory in inventories:
        argv += ["-i", str(inventory)]
    out = subprocess.run(argv, capture_output=True, text=True, check=True)
    return guests_from_listing(json.loads(out.stdout), group)
