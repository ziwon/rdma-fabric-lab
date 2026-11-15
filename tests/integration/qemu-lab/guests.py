"""Guest profiles of the QEMU harness: which guests boot, with which NICs on which networks.

A profile is a TSV file under profiles/. Header lines name the inventory the profile belongs to,
the CIDR of each switched network and, optionally, extra QEMU flags for every guest of the profile:

    #inventory ansible/inventories/lab
    #network mgmt 192.168.140.0/24
    #qemu -no-reboot

Each guest row is: name, ssh_port, vcpus, mem_mib, disk_gib, then NIC specs separated by spaces.
A NIC spec is comma separated key=value pairs:

    mac=52:54:00:6d:00:12  the guest MAC, which also finds the NIC inside the guest
    net=user               QEMU user networking: DHCP, the default route and the SSH forward
    net=switch:<name>      a shared L2 network through switch.py, AF_UNIX datagrams only
    net=cable:<peer>       a point-to-point cable to the NIC of <peer> that names this guest
    vars=a|b               inventory variables the harness sets to the NIC name it finds by MAC
    name=<ifname>          rename the NIC at first boot; then no variable is written for it
    ip=<hostvar>           a static address from that host variable, prefixed by the network CIDR
    fwd=<host>:<guest>     extra TCP forwards from 127.0.0.1 on a user NIC

lab.sh calls the subcommands below; every rendering is a pure function of the profile.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

USER_NET = ipaddress.ip_network("10.0.2.0/24")
MAC = re.compile(r"^52:54:00(:[0-9a-f]{2}){3}$")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
IFNAME = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.-]{0,14}$")
VAR = re.compile(r"^[a-z_][a-z0-9_]*$")
FIRST_PCI_SLOT = 3
# -no-reboot makes a guest that panics or reboots end its QEMU process, so serial.log keeps the
# console of the crash. A profile whose guests must survive a reboot leaves it out.
QEMU_FLAGS = frozenset({"-no-reboot"})


class ProfileError(ValueError):
    pass


@dataclass(frozen=True)
class Nic:
    mac: str
    kind: str
    target: str | None
    vars: tuple[str, ...] = ()
    name: str | None = None
    ip_var: str | None = None
    forwards: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class Guest:
    name: str
    ssh_port: int
    vcpus: int
    mem_mib: int
    disk_gib: int
    nics: tuple[Nic, ...]


@dataclass
class Profile:
    inventory: str
    networks: dict[str, ipaddress.IPv4Network] = field(default_factory=dict)
    guests: list[Guest] = field(default_factory=list)
    qemu_flags: tuple[str, ...] = ()

    def guest(self, name: str) -> Guest:
        for g in self.guests:
            if g.name == name:
                return g
        raise ProfileError(f"unknown guest {name}. Known: {', '.join(g.name for g in self.guests)}")


def parse_nic(text: str, where: str) -> Nic:
    pairs = {}
    for part in text.split(","):
        key, sep, value = part.partition("=")
        if not sep or key in pairs:
            raise ProfileError(f"{where}: NIC spec {text!r} needs unique key=value pairs")
        pairs[key] = value
    unknown = set(pairs) - {"mac", "net", "vars", "name", "ip", "fwd"}
    if unknown:
        raise ProfileError(f"{where}: unknown NIC keys {sorted(unknown)}")
    mac = pairs.get("mac", "").lower()
    if not MAC.match(mac):
        raise ProfileError(f"{where}: {mac!r} is not a 52:54:00 QEMU MAC")
    kind, _, target = pairs.get("net", "").partition(":")
    if kind not in ("user", "switch", "cable") or (kind == "user") != (target == ""):
        raise ProfileError(f"{where}: net={pairs.get('net')!r} is not user, switch:<name> or cable:<peer>")
    variables = tuple(v for v in pairs.get("vars", "").split("|") if v)
    bad = [v for v in variables if not VAR.match(v)]
    name = pairs.get("name") or None
    if bad or (name and not IFNAME.match(name)):
        raise ProfileError(f"{where}: bad variable or interface name in {text!r}")
    forwards = []
    for item in filter(None, pairs.get("fwd", "").split("|")):
        host, _, guest = item.partition(":")
        if not (host.isdigit() and guest.isdigit()) or kind != "user":
            raise ProfileError(f"{where}: fwd={item!r} needs <host port>:<guest port> on a user NIC")
        forwards.append((int(host), int(guest)))
    return Nic(mac, kind, target or None, variables, name, pairs.get("ip") or None, tuple(forwards))


def parse(text: str) -> Profile:
    profile = Profile(inventory="")
    for number, line in enumerate(text.splitlines(), 1):
        where = f"line {number}"
        if line.startswith("#inventory "):
            profile.inventory = line.split(None, 1)[1].strip()
        elif line.startswith("#network "):
            _, name, cidr = line.split()
            profile.networks[name] = ipaddress.ip_network(cidr)
        elif line.startswith("#qemu "):
            flags = tuple(line.split()[1:])
            unknown = sorted(set(flags) - QEMU_FLAGS)
            if unknown:
                raise ProfileError(f"{where}: QEMU flags {unknown} are not allowed. Allowed: {sorted(QEMU_FLAGS)}")
            profile.qemu_flags += flags
        elif line.strip() and not line.startswith("#"):
            cols = line.split("\t")
            if len(cols) != 6:
                raise ProfileError(f"{where}: want 6 tab-separated columns, got {len(cols)}")
            name, port, vcpus, mem, disk, nics = cols
            if not SLUG.match(name) or not all(c.isdigit() for c in (port, vcpus, mem, disk)):
                raise ProfileError(f"{where}: bad guest name or number in {line!r}")
            parsed = tuple(parse_nic(spec, f"{where} {name}") for spec in nics.split())
            profile.guests.append(Guest(name, int(port), int(vcpus), int(mem), int(disk), parsed))
    validate(profile)
    return profile


def validate(profile: Profile) -> None:
    if not profile.inventory:
        raise ProfileError("the profile names no #inventory")
    names = [g.name for g in profile.guests]
    macs = [n.mac for g in profile.guests for n in g.nics]
    ports = [g.ssh_port for g in profile.guests] + [h for g in profile.guests for n in g.nics for h, _ in n.forwards]
    for label, values in (("guest", names), ("MAC", macs), ("host port", ports)):
        twins = sorted({v for v in values if values.count(v) > 1})
        if twins:
            raise ProfileError(f"{label} used twice: {', '.join(map(str, twins))}")
    for g in profile.guests:
        users = [n for n in g.nics if n.kind == "user"]
        if len(users) != 1 or g.nics[0] is not users[0]:
            raise ProfileError(f"{g.name}: the first NIC, and only it, must be net=user")
        for n in g.nics:
            if n.kind == "switch" and n.target not in profile.networks:
                raise ProfileError(f"{g.name}: switch network {n.target} has no #network line")
            if n.kind == "cable":
                peer = profile.guest(n.target)
                if not any(p.kind == "cable" and p.target == g.name for p in peer.nics):
                    raise ProfileError(f"{g.name}: cable to {n.target} has no NIC on {n.target} that names {g.name}")
            if n.ip_var and n.kind != "switch":
                raise ProfileError(f"{g.name}: ip= needs a switch NIC, whose #network gives the prefix")
    for name, net in profile.networks.items():
        if net.overlaps(USER_NET):
            raise ProfileError(f"network {name} {net} overlaps the user network {USER_NET} inside the guests")


def switches(profile: Profile) -> dict[str, list[str]]:
    """Switched network name -> guests on it, in profile order."""
    out: dict[str, list[str]] = {}
    for g in profile.guests:
        for n in g.nics:
            if n.kind == "switch":
                out.setdefault(n.target, []).append(g.name)
    return out


def socket_paths(lab_dir: str, guest: str, nic: Nic) -> tuple[str, str]:
    """(path QEMU binds, path QEMU sends to) of a switch or cable NIC."""
    if nic.kind == "switch":
        base = f"{lab_dir}/net/{nic.target}"
        return f"{base}/{guest}.nic", f"{base}/{guest}.port"
    return f"{lab_dir}/net/cable-{guest}-{nic.target}.sock", f"{lab_dir}/net/cable-{nic.target}-{guest}.sock"


def qemu_nic_args(guest: Guest, lab_dir: str) -> list[str]:
    args = []
    for index, nic in enumerate(guest.nics):
        netdev = f"n{index}"
        if nic.kind == "user":
            forwards = [(guest.ssh_port, 22), *nic.forwards]
            spec = f"user,id={netdev}," + ",".join(f"hostfwd=tcp:127.0.0.1:{h}-:{p}" for h, p in forwards)
        else:
            local, remote = socket_paths(lab_dir, guest.name, nic)
            spec = f"dgram,id={netdev},local.type=unix,local.path={local},remote.type=unix,remote.path={remote}"
        args += ["-netdev", spec, "-device", f"virtio-net-pci,netdev={netdev},mac={nic.mac},addr={FIRST_PCI_SLOT + index:#x}"]
    return args


def network_config(profile: Profile, guest: Guest, hostvars: dict) -> dict:
    """netplan v2 for cloud-init. Only the user NIC, static switch addresses and declared names appear."""
    ethernets = {}
    for index, nic in enumerate(guest.nics):
        entry: dict = {"match": {"macaddress": nic.mac}}
        if nic.name:
            entry["set-name"] = nic.name
        if nic.kind == "user":
            entry["dhcp4"] = True
        elif nic.ip_var:
            if nic.ip_var not in hostvars:
                raise ProfileError(f"{guest.name}: ip={nic.ip_var}, but the inventory gives {guest.name} no {nic.ip_var}")
            address = ipaddress.ip_address(str(hostvars[nic.ip_var]))
            net = profile.networks[nic.target]
            if address not in net:
                raise ProfileError(f"{guest.name}: {nic.ip_var} {address} lies outside {nic.target} {net}")
            entry |= {"dhcp4": False, "dhcp6": False, "accept-ra": False, "addresses": [f"{address}/{net.prefixlen}"]}
        elif not nic.name:
            continue
        ethernets[nic.name or f"nic{index}"] = entry
    return {"version": 2, "ethernets": ethernets}


def override_inventory(profile: Profile, discovered: dict[str, dict[str, str]], key: str, known_hosts: str,
                       user: str = "ubuntu") -> dict:
    """Connection variables and found NIC names. discovered maps guest -> MAC -> interface name."""
    hosts = {}
    for g in profile.guests:
        found = discovered.get(g.name, {})
        entry = {
            "ansible_host": "127.0.0.1",
            "ansible_port": g.ssh_port,
            "ansible_user": user,
            "ansible_ssh_private_key_file": key,
            "ansible_ssh_common_args": f"-o IdentitiesOnly=yes -o UserKnownHostsFile={known_hosts}",
        }
        for nic in g.nics:
            if nic.name:
                if found.get(nic.mac) != nic.name:
                    raise ProfileError(f"{g.name}: {nic.mac} is {found.get(nic.mac)}, not the declared {nic.name}")
                continue
            if nic.vars and nic.mac not in found:
                raise ProfileError(f"{g.name}: no interface carries {nic.mac}")
            entry |= {var: found[nic.mac] for var in nic.vars}
        hosts[g.name] = entry
    return {"all": {"hosts": hosts}}


def api_url(profile: Profile, listing: dict) -> str | None:
    """https://127.0.0.1:<port> of the forward to the API port of the first control plane, if any."""
    first = (listing.get("kube_control_plane", {}).get("hosts") or [None])[0]
    if first is None:
        return None
    api_port = int(listing["_meta"]["hostvars"][first].get("k8s_api_port", 6443))
    for nic in profile.guest(first).nics:
        for host, guest_port in nic.forwards:
            if guest_port == api_port:
                return f"https://127.0.0.1:{host}"
    return None


def _flatten(listing: dict, group: str) -> list[str]:
    node = listing.get(group, {})
    hosts = list(node.get("hosts", []))
    for child in node.get("children", []):
        hosts += [h for h in _flatten(listing, child) if h not in hosts]
    return hosts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="guests.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["rows", "inventory-path", "switches", "qemu-flags", "qemu-nics",
                                            "network-config", "override", "api-url"])
    parser.add_argument("profile", type=Path)
    parser.add_argument("--guest")
    parser.add_argument("--lab-dir")
    parser.add_argument("--listing", type=Path, help="ansible-inventory --list output of the profile inventory")
    parser.add_argument("--discovered", type=Path, help="JSON guest -> MAC -> interface name")
    parser.add_argument("--key")
    parser.add_argument("--known-hosts")
    args = parser.parse_args(argv)
    try:
        profile = parse(args.profile.read_text())
        listing = json.loads(args.listing.read_text()) if args.listing else None
        if args.command == "rows":
            for g in profile.guests:
                print("\t".join(map(str, (g.name, g.ssh_port, g.vcpus, g.mem_mib, g.disk_gib))))
        elif args.command == "inventory-path":
            print(profile.inventory)
        elif args.command == "switches":
            for name, members in switches(profile).items():
                print(name, *members)
        elif args.command == "qemu-flags":
            for flag in profile.qemu_flags:
                print(flag)
        elif args.command == "qemu-nics":
            print("\n".join(qemu_nic_args(profile.guest(args.guest), args.lab_dir)))
        elif args.command == "network-config":
            hostvars = listing["_meta"]["hostvars"].get(args.guest, {})
            print(yaml.safe_dump(network_config(profile, profile.guest(args.guest), hostvars), sort_keys=False), end="")
        elif args.command == "override":
            members = set(_flatten(listing, "all"))
            stray = [g.name for g in profile.guests if g.name not in members]
            if stray:
                raise ProfileError(f"guests {', '.join(stray)} are not hosts of {profile.inventory}")
            discovered = json.loads(args.discovered.read_text())
            header = f"# Written by tests/integration/qemu-lab/lab.sh up. Pass it as a second -i after {profile.inventory}.\n"
            print(header + yaml.safe_dump(override_inventory(profile, discovered, args.key, args.known_hosts), sort_keys=False), end="")
        elif args.command == "api-url":
            print(api_url(profile, listing) or "")
    except ProfileError as exc:
        print(f"guests.py: {args.profile}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
