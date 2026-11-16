"""The lab profile and the lab inventory turned into VMware guests for Vagrant.

The QEMU harness profile tests/integration/qemu-lab/profiles/lab.tsv names the guests, their sizes
and their NICs. ansible/inventories/lab gives every address. Everything the Vagrantfile, the
cloud-init seeds and the override inventory need is derived from those two here.

Each profile NIC becomes one VMware NIC with the same role:

    net=user          the NAT adapter in slot 0. DHCP, the default route, and Vagrant's SSH session
    net=switch:<name> a host-only vmnet with the CIDR of the #network line and a static address
    net=cable:<peer>  the host-only vmnet of the lab network lab_address_plan.rdma, no address
                      until the network role sets lab_rdma_ip
"""

from __future__ import annotations

import dataclasses
import importlib.util
import ipaddress
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS_GUESTS = REPO_ROOT / "tests" / "integration" / "qemu-lab" / "guests.py"
PROFILE = REPO_ROOT / "tests" / "integration" / "qemu-lab" / "profiles" / "lab.tsv"
GUEST_USER = "ubuntu"
LAB_ADDRESS_VAR = "lab_rdma_ip"
VMWARE_OUI = ("00", "50", "56")
# VMware Workstation takes a static MAC only from 00:50:56:00:00:00 to 00:50:56:3f:ff:ff.
STATIC_FOURTH_OCTET_MASK = 0x3F


class PlanError(ValueError):
    pass


def harness():
    """The profile parser of the QEMU harness, so both hypervisors read one profile format."""
    name = "rfl_qemu_lab_guests"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, HARNESS_GUESTS)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


@dataclass(frozen=True)
class PrivateNetwork:
    """One config.vm.network :private_network. The provider finds or creates the vmnet by ip and netmask."""

    ip: str
    netmask: str
    mac: str


@dataclass(frozen=True)
class Machine:
    name: str
    cpus: int
    memory_mib: int
    disk_gib: int
    nat_mac: str
    private_networks: tuple[PrivateNetwork, ...]


def vmware_mac(qemu_mac: str) -> str:
    """The VMware static MAC of a profile NIC: OUI 00:50:56, the fourth octet masked into the static range."""
    octets = qemu_mac.lower().split(":")
    fourth = int(octets[3], 16) & STATIC_FOURTH_OCTET_MASK
    return ":".join((*VMWARE_OUI, f"{fourth:02x}", *octets[4:]))


def vmware_profile(profile):
    """The profile with every NIC MAC replaced by its VMware MAC. Refuses two NICs that would share one."""
    seen: dict[str, str] = {}
    guests = []
    for guest in profile.guests:
        nics = []
        for nic in guest.nics:
            mac = vmware_mac(nic.mac)
            if mac in seen:
                raise PlanError(f"{guest.name} {nic.mac} and {seen[mac]} both map to the VMware MAC {mac}. Change one in the profile.")
            seen[mac] = f"{guest.name} {nic.mac}"
            nics.append(dataclasses.replace(nic, mac=mac))
        guests.append(dataclasses.replace(guest, nics=tuple(nics)))
    return dataclasses.replace(profile, guests=guests)


def _cable_pairs(profile) -> set[frozenset[str]]:
    return {frozenset((g.name, n.target)) for g in profile.guests for n in g.nics if n.kind == "cable"}


def machines(profile, hostvars: dict[str, dict], lab_cidr: str) -> list[Machine]:
    """The Vagrant machines of a VMware profile. hostvars is the _meta.hostvars of the lab inventory."""
    if profile.groups:
        raise PlanError(f"#group lines put guests outside {profile.inventory}. The Vagrant lab takes only inventory hosts.")
    if len(_cable_pairs(profile)) > 1:
        raise PlanError("VMware joins every cable to the one lab network, so the profile may hold one cable pair only")
    lab_net = ipaddress.ip_network(lab_cidr)
    out = []
    for guest in profile.guests:
        if guest.name not in hostvars:
            raise PlanError(f"{guest.name} is not a host of {profile.inventory}")
        found = hostvars[guest.name]
        networks = []
        for nic in guest.nics[1:]:
            if nic.kind == "switch":
                net = profile.networks[nic.target]
                if not (nic.ip_var or nic.address):
                    raise PlanError(f"{guest.name}: switch NIC {nic.mac} needs ip= or addr=, which picks its vmnet")
                if nic.ip_var and nic.ip_var not in found:
                    raise PlanError(f"{guest.name}: ip={nic.ip_var}, but the inventory gives {guest.name} no {nic.ip_var}")
                address = ipaddress.ip_address(nic.address or str(found[nic.ip_var]))
            else:
                net = lab_net
                if LAB_ADDRESS_VAR not in found:
                    raise PlanError(f"{guest.name} has a cable NIC, but the inventory gives it no {LAB_ADDRESS_VAR}")
                address = ipaddress.ip_address(str(found[LAB_ADDRESS_VAR]))
            if address not in net:
                raise PlanError(f"{guest.name}: {address} lies outside {net}")
            networks.append(PrivateNetwork(str(address), str(net.netmask), nic.mac))
        out.append(Machine(guest.name, guest.vcpus, guest.mem_mib, guest.disk_gib, guest.nics[0].mac, tuple(networks)))
    return out


def vagrantfile_plan(machine_list: list[Machine], box: str, state_dir: str) -> dict:
    """What the Vagrantfile reads: the box, the SSH identity and one entry per machine."""
    return {
        "box": box,
        "ssh": {"user": GUEST_USER, "private_key": f"{state_dir}/ssh/id_ed25519"},
        "machines": [
            {
                "name": m.name,
                "cpus": m.cpus,
                "memory_mib": m.memory_mib,
                "disk_gib": m.disk_gib,
                "nat_mac": m.nat_mac.upper(),
                "seed": seed_path(state_dir, m.name),
                "private_networks": [{"ip": n.ip, "netmask": n.netmask, "mac": n.mac.replace(":", "")}
                                     for n in m.private_networks],
            }
            for m in machine_list
        ],
    }


def seed_path(state_dir: str, name: str) -> str:
    return f"{state_dir}/guests/{name}/seed.iso"


def user_data(name: str, authorized_key: str, host_key: str, host_key_pub: str) -> str:
    """cloud-config of one guest: the harness seed, with the host key the known_hosts file already names."""
    lines = [
        "#cloud-config",
        f"hostname: {name}",
        "manage_etc_hosts: localhost",
        f"users:\n  - name: {GUEST_USER}",
        '    sudo: "ALL=(ALL) NOPASSWD:ALL"\n    shell: /bin/bash\n    lock_passwd: true',
        f"    ssh_authorized_keys:\n      - {authorized_key.strip()}",
        "ssh_pwauth: false",
        "ssh_genkeytypes: [ed25519]",
        "ssh_keys:\n  ed25519_private: |",
        *(f"    {line}" for line in host_key.strip().splitlines()),
        f"  ed25519_public: {host_key_pub.strip()}",
        # The kernel stays the one the image shipped, so it matches lab.guest_kernel in the lock.
        "package_update: false",
        "package_upgrade: false",
    ]
    return "\n".join(lines) + "\n"


def meta_data(name: str) -> str:
    return f"instance-id: rfl-vagrant-{name}\nlocal-hostname: {name}\n"


def known_hosts(host_keys: dict[str, str]) -> str:
    """One line per guest under its name. ssh reaches it with HostKeyAlias=<name>, so a new DHCP address keeps the key."""
    return "".join(f"{name} {' '.join(pub.split()[:2])}\n" for name, pub in host_keys.items())


def ssh_config_hosts(text: str) -> dict[str, dict[str, str]]:
    """Host -> option -> value from `vagrant ssh-config`. Quotes around a value are dropped."""
    hosts: dict[str, dict[str, str]] = {}
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        key, _, value = line.partition(" ")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        if key == "Host":
            current = hosts.setdefault(value, {})
        elif current is not None:
            current.setdefault(key, value)
    return hosts


def connection(name: str, ssh: dict[str, str], known_hosts_file: str) -> dict:
    """Ansible connection variables of one guest from its `vagrant ssh-config` entry."""
    missing = [k for k in ("HostName", "Port", "User", "IdentityFile") if k not in ssh]
    if missing:
        raise PlanError(f"vagrant ssh-config of {name} lacks {', '.join(missing)}")
    return {
        "ansible_host": ssh["HostName"],
        "ansible_port": int(ssh["Port"]),
        "ansible_user": ssh["User"],
        "ansible_ssh_private_key_file": ssh["IdentityFile"],
        "ansible_ssh_common_args": f"-o IdentitiesOnly=yes -o UserKnownHostsFile={known_hosts_file} -o HostKeyAlias={name}",
    }


def override_inventory(profile, connections: dict[str, dict], discovered: dict[str, dict[str, str]]) -> dict:
    """Connection variables and NIC names of a VMware profile. discovered maps guest -> MAC -> interface name."""
    hosts = {}
    for guest in profile.guests:
        if guest.name not in connections:
            raise PlanError(f"vagrant ssh-config names no {guest.name}. Is it running?")
        found = discovered.get(guest.name, {})
        entry = dict(connections[guest.name])
        for nic in guest.nics:
            if nic.name and found.get(nic.mac) != nic.name:
                raise PlanError(f"{guest.name}: {nic.mac} is {found.get(nic.mac)}, not the declared {nic.name}")
            if nic.vars and nic.mac not in found:
                raise PlanError(f"{guest.name}: no interface carries {nic.mac}")
            entry |= {var: found[nic.mac] for var in nic.vars}
        hosts[guest.name] = entry
    return {"all": {"hosts": hosts}}
