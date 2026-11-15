"""The QEMU harness profile that simulates a site model on this host.

One guest per host of the model, with one NIC per interface of its class under the interface's own
name, and one switched network per site network. The model names its syslog server only by address,
so a stand-in guest holds that address and stays out of the site inventory. The InfiniBand rails
carry Ethernet frames in the simulation. guests.py of the harness documents the profile format, and
the same model always gives the same bytes.
"""

from __future__ import annotations

from dataclasses import dataclass

from .site import Host, Site


@dataclass(frozen=True)
class Size:
    vcpus: int
    mem_mib: int
    disk_gib: int


# Sized for 13 nodes and the stand-in on a host with 24 threads and about 91 GiB available.
SIZES = {"control_plane": Size(2, 3072, 20), "node": Size(2, 2560, 20)}
SYSLOG_SIZE = Size(1, 768, 10)
SSH_PORT_BASE = 22300
API_FORWARD_PORT = 22340
MAC_PREFIX = "52:54:00:c1"
SYSLOG_GROUP = "simulation_syslog"
COLUMNS = "# name\tssh_port\tvcpus\tmem_mib\tdisk_gib\tnics"
HEADER = """\
# Rendered by `make fleet-sim-profile` from the {site} site model. Edit the model, not this file.
# The {site} simulation: one guest per host with the NIC names of its class, one switched network per
# site network, and {syslog} at the syslog address of the model. The rails carry Ethernet
# frames here, not InfiniBand. A guest that panics ends its QEMU process and leaves its console.
#inventory {inventory}
#qemu -no-reboot
"""


class SimError(ValueError):
    pass


def syslog_guest(site: Site) -> str:
    return f"{site.name}-sim-syslog"


def _mac(guest: int, nic: int) -> str:
    return f"{MAC_PREFIX}:{guest:02x}:{nic:02x}"


def _host_nics(site: Site, host: Host, index: int, first_cp: str) -> list[str]:
    user = f"mac={_mac(index, 0)},net=user"
    if host.name == first_cp:
        user += f",fwd={API_FORWARD_PORT}:{site.api_port}"
    nics = [user]
    for n, iface in enumerate(site.classes[host.node_class].interfaces, 1):
        spec = f"mac={_mac(index, n)},net=switch:{iface.network},name={iface.name}"
        spec += ",ip=k8s_node_ip" if iface.network == site.node_network else f",addr={host.addresses[iface.name]}"
        nics.append(spec)
    return nics


def _syslog_nics(site: Site, index: int) -> list[str]:
    homes = [n for n in site.networks.values() if site.syslog_address in n.cidr]
    if len(homes) != 1:
        raise SimError(f"services.syslog.address {site.syslog_address} lies in {len(homes)} site networks, want 1")
    names = {i.name for c in site.classes.values() for i in c.interfaces if i.network == homes[0].name}
    if len(names) != 1:
        raise SimError(f"the classes name their {homes[0].name} interface {sorted(names)}, the stand-in takes one name")
    return [f"mac={_mac(index, 0)},net=user", f"mac={_mac(index, 1)},net=switch:{homes[0].name},name={names.pop()},addr={site.syslog_address}"]


def _row(name: str, port: int, size: Size, nics: list[str]) -> str:
    return "\t".join([name, str(port), str(size.vcpus), str(size.mem_mib), str(size.disk_gib), " ".join(nics)])


def profile(site: Site, inventory: str) -> str:
    """Text of the harness profile of a valid site model whose inventory lives at inventory."""
    first_cp = site.members("control_plane")[0].name
    used = {i.network for c in site.classes.values() for i in c.interfaces}
    lines = [HEADER.format(site=site.name, syslog=syslog_guest(site), inventory=inventory).rstrip("\n")]
    lines += [f"#network {n.name} {n.cidr}" for n in site.networks.values() if n.name in used]
    lines += [f"#group {SYSLOG_GROUP} {syslog_guest(site)}", COLUMNS]
    hosts = list(site.hosts.values())
    for index, host in enumerate(hosts, 1):
        size = SIZES[site.classes[host.node_class].kube_role]
        lines.append(_row(host.name, SSH_PORT_BASE + index, size, _host_nics(site, host, index, first_cp)))
    index = len(hosts) + 1
    lines.append(_row(syslog_guest(site), SSH_PORT_BASE + index, SYSLOG_SIZE, _syslog_nics(site, index)))
    if index > 0xFF or SSH_PORT_BASE + index >= API_FORWARD_PORT:
        raise SimError(f"{index} guests do not fit the MAC and port plan")
    return "\n".join(lines) + "\n"


def footprint(site: Site) -> dict[str, int]:
    """Total vCPUs, memory and virtual disk of the profile's guests."""
    sizes = [SIZES[site.classes[h.node_class].kube_role] for h in site.hosts.values()] + [SYSLOG_SIZE]
    return {"guests": len(sizes), "vcpus": sum(s.vcpus for s in sizes), "mem_mib": sum(s.mem_mib for s in sizes),
            "disk_gib": sum(s.disk_gib for s in sizes)}

