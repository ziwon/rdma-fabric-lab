"""A site model: one file that is the source of truth for a GPU region.

    site: <name>                       also the rfl_inventory of the rendered inventory
    kubernetes:
      node_network: <network>          carries k8s_node_ip and the API address
      api: {vip: <ipv4>, port: <int>}
      pod_cidr: <cidr>
      service_cidr: <cidr>
      kube_proxy: <bool>
      control_plane_schedulable: <bool>
    services:
      syslog: {address: <ipv4>, port: <int>}
    networks:                          one L2 segment each
      <network>: {cidr: <cidr>, kind: ethernet|infiniband, rail: <int>}   rail only on compute rails
    classes:                           one inventory group each
      <class>:
        kube_role: control_plane|node
        os: <text>
        interfaces:                    Linux names after the systemd .link rename
          <iface>: <network> | {network: <network>, hca: <device>}
        labels: {<key>: <string>}      k8s_node_labels of every host of the class
    hosts:
      <host>: {class: <class>, addresses: {<iface>: <ipv4>}}
    fabrics:
      <fabric>:
        kind: infiniband
        topology: rail-optimized
        link: {width: <n>x, speed: <SDR..NDR>}
        switch_models: {<model>: {ports: <int>}}
        switches: {<switch>: {model: <model>, role: leaf|spine, rail: <int>}}   rail only on leaves
        links: [[<host>/<iface>|<switch>/<port>, <switch>/<port>]]

Parsing turns the YAML into the frozen types below and fails on a wrong shape. problems()
then names every way a well-shaped model breaks the address plan or the cabling.
"""

from __future__ import annotations

import ipaddress
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml

KUBE_ROLES = ("control_plane", "node")
NETWORK_KINDS = ("ethernet", "infiniband")
SWITCH_ROLES = ("leaf", "spine")
TOPOLOGIES = ("rail-optimized",)
LINK_SPEEDS = ("SDR", "DDR", "QDR", "FDR", "EDR", "HDR", "NDR")
LINK_WIDTH = re.compile(r"^(1|2|4|8|12)x$")
# IFNAMSIZ is 16 with the terminating NUL.
INTERFACE_NAME = re.compile(r"^[a-z][a-z0-9]{0,14}$")
# systemd.link(5): a Name= the kernel may also assign races with the kernel. These are the
# kernel's own prefixes and udev's predictable-name prefixes.
KERNEL_PREFIXES = ("eth", "en", "ib", "wl", "ww", "usb", "sl")
# A class becomes an Ansible group, a host a Kubernetes node name (an RFC 1123 label).
GROUP_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
CONTRACT_GROUPS = ("all", "ungrouped", "k8s_cluster", "kube_control_plane", "kube_node")
HOST_NAME = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")


class SiteError(Exception):
    pass


@dataclass(frozen=True)
class Network:
    name: str
    cidr: ipaddress.IPv4Network
    kind: str
    rail: int | None


@dataclass(frozen=True)
class Interface:
    name: str
    network: str
    hca: str | None


@dataclass(frozen=True)
class NodeClass:
    name: str
    kube_role: str
    os: str
    interfaces: tuple[Interface, ...]
    labels: dict[str, str]


@dataclass(frozen=True)
class Host:
    name: str
    node_class: str
    addresses: dict[str, ipaddress.IPv4Address]


@dataclass(frozen=True)
class Switch:
    name: str
    model: str
    role: str
    rail: int | None


@dataclass(frozen=True)
class Endpoint:
    node: str
    port: str

    def __str__(self) -> str:
        return f"{self.node}/{self.port}"


@dataclass(frozen=True)
class Fabric:
    name: str
    kind: str
    topology: str
    link_width: str
    link_speed: str
    switch_ports: dict[str, int]
    switches: dict[str, Switch]
    links: tuple[tuple[Endpoint, Endpoint], ...]


@dataclass(frozen=True)
class Site:
    name: str
    node_network: str
    api_vip: ipaddress.IPv4Address
    api_port: int
    pod_cidr: ipaddress.IPv4Network
    service_cidr: ipaddress.IPv4Network
    kube_proxy: bool
    control_plane_schedulable: bool
    syslog_address: ipaddress.IPv4Address
    syslog_port: int
    networks: dict[str, Network]
    classes: dict[str, NodeClass]
    hosts: dict[str, Host]
    fabrics: dict[str, Fabric]

    def node_ip(self, host: Host) -> ipaddress.IPv4Address:
        return host.addresses[self.node_interface(self.classes[host.node_class]).name]

    def node_interface(self, node_class: NodeClass) -> Interface | None:
        return next((i for i in node_class.interfaces if i.network == self.node_network), None)

    def members(self, kube_role: str) -> list[Host]:
        return [h for h in self.hosts.values() if h.node_class in self.classes and self.classes[h.node_class].kube_role == kube_role]


class _UniqueKeyLoader(yaml.SafeLoader):
    """PyYAML keeps the last of two equal keys. A site model must name each thing once."""


def _construct_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    seen = Counter(loader.construct_object(k, deep=deep) for k, _ in node.value)
    twice = sorted(str(k) for k, n in seen.items() if n > 1)
    if twice:
        raise SiteError(f"line {node.start_mark.line + 1}: {', '.join(twice)} defined twice")
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def load(path: Path) -> Site:
    try:
        raw = yaml.load(path.read_text(), Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise SiteError(str(exc)) from exc
    return parse(raw)


def _map(raw, where: str) -> dict:
    if not isinstance(raw, dict):
        raise SiteError(f"{where} must be a mapping")
    return raw


def _keys(raw: dict, where: str, required: Iterable[str], optional: Iterable[str] = ()) -> None:
    required = set(required)
    missing = sorted(required - set(raw))
    unknown = sorted(str(k) for k in set(raw) - required - set(optional))
    if missing:
        raise SiteError(f"{where}: missing {', '.join(missing)}")
    if unknown:
        raise SiteError(f"{where}: unknown {', '.join(unknown)}")


def _address(value, where: str) -> ipaddress.IPv4Address:
    try:
        return ipaddress.IPv4Address(str(value))
    except ValueError as exc:
        raise SiteError(f"{where}: {value!r} is not an IPv4 address") from exc


def _network(value, where: str) -> ipaddress.IPv4Network:
    try:
        return ipaddress.IPv4Network(str(value), strict=True)
    except ValueError as exc:
        raise SiteError(f"{where}: {value!r} is not an IPv4 network: {exc}") from exc


def _int(value, where: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise SiteError(f"{where}: {value!r} is not an integer")
    return value


def _bool(value, where: str) -> bool:
    if not isinstance(value, bool):
        raise SiteError(f"{where}: {value!r} is not true or false")
    return value


def _choice(value, choices: tuple[str, ...], where: str) -> str:
    if value not in choices:
        raise SiteError(f"{where}: {value!r} is not one of {', '.join(choices)}")
    return value


def _endpoint(value, where: str) -> Endpoint:
    node, sep, port = str(value).rpartition("/")
    if not sep or not node or not port:
        raise SiteError(f"{where}: {value!r} is not <node>/<port>")
    return Endpoint(node, port)


def _labels(raw, where: str) -> dict[str, str]:
    raw = _map(raw or {}, where)
    wrong = sorted(str(k) for k, v in raw.items() if not isinstance(v, str))
    if wrong:
        raise SiteError(f"{where}: {', '.join(wrong)} must be quoted strings, kubelet takes labels as text")
    return dict(raw)


def _fabric(name: str, raw) -> Fabric:
    where = f"fabrics.{name}"
    raw = _map(raw, where)
    _keys(raw, where, ("kind", "topology", "link", "switch_models", "switches", "links"))
    link = _map(raw["link"], f"{where}.link")
    _keys(link, f"{where}.link", ("width", "speed"))
    if not LINK_WIDTH.match(str(link["width"])):
        raise SiteError(f"{where}.link.width: {link['width']!r} is not 1x, 2x, 4x, 8x or 12x")
    ports = {}
    for model, spec in _map(raw["switch_models"], f"{where}.switch_models").items():
        spec = _map(spec, f"{where}.switch_models.{model}")
        _keys(spec, f"{where}.switch_models.{model}", ("ports",))
        ports[model] = _int(spec["ports"], f"{where}.switch_models.{model}.ports")
    switches = {}
    for sw, spec in _map(raw["switches"], f"{where}.switches").items():
        at = f"{where}.switches.{sw}"
        spec = _map(spec, at)
        _keys(spec, at, ("model", "role"), ("rail",))
        switches[sw] = Switch(
            name=sw,
            model=str(spec["model"]),
            role=_choice(spec["role"], SWITCH_ROLES, f"{at}.role"),
            rail=_int(spec["rail"], f"{at}.rail") if "rail" in spec else None,
        )
    links = []
    if not isinstance(raw["links"], list):
        raise SiteError(f"{where}.links must be a list")
    for n, pair in enumerate(raw["links"]):
        if not isinstance(pair, list) or len(pair) != 2:
            raise SiteError(f"{where}.links[{n}] must be a pair of endpoints")
        links.append((_endpoint(pair[0], f"{where}.links[{n}]"), _endpoint(pair[1], f"{where}.links[{n}]")))
    return Fabric(
        name=name,
        kind=_choice(raw["kind"], ("infiniband",), f"{where}.kind"),
        topology=_choice(raw["topology"], TOPOLOGIES, f"{where}.topology"),
        link_width=str(link["width"]),
        link_speed=_choice(link["speed"], LINK_SPEEDS, f"{where}.link.speed"),
        switch_ports=ports,
        switches=switches,
        links=tuple(links),
    )


def parse(raw) -> Site:
    raw = _map(raw, "site model")
    _keys(raw, "site model", ("site", "kubernetes", "services", "networks", "classes", "hosts", "fabrics"))
    kube = _map(raw["kubernetes"], "kubernetes")
    _keys(kube, "kubernetes", ("node_network", "api", "pod_cidr", "service_cidr", "kube_proxy", "control_plane_schedulable"))
    api = _map(kube["api"], "kubernetes.api")
    _keys(api, "kubernetes.api", ("vip", "port"))
    services = _map(raw["services"], "services")
    _keys(services, "services", ("syslog",))
    syslog = _map(services["syslog"], "services.syslog")
    _keys(syslog, "services.syslog", ("address", "port"))

    networks = {}
    for name, spec in _map(raw["networks"], "networks").items():
        spec = _map(spec, f"networks.{name}")
        _keys(spec, f"networks.{name}", ("cidr", "kind"), ("rail",))
        networks[name] = Network(
            name=name,
            cidr=_network(spec["cidr"], f"networks.{name}.cidr"),
            kind=_choice(spec["kind"], NETWORK_KINDS, f"networks.{name}.kind"),
            rail=_int(spec["rail"], f"networks.{name}.rail") if "rail" in spec else None,
        )

    classes = {}
    for name, spec in _map(raw["classes"], "classes").items():
        at = f"classes.{name}"
        spec = _map(spec, at)
        _keys(spec, at, ("kube_role", "os", "interfaces"), ("labels",))
        interfaces = []
        for iface, attach in _map(spec["interfaces"], f"{at}.interfaces").items():
            attach = {"network": attach} if isinstance(attach, str) else _map(attach, f"{at}.interfaces.{iface}")
            _keys(attach, f"{at}.interfaces.{iface}", ("network",), ("hca",))
            interfaces.append(Interface(name=str(iface), network=str(attach["network"]), hca=attach.get("hca")))
        classes[name] = NodeClass(
            name=name,
            kube_role=_choice(spec["kube_role"], KUBE_ROLES, f"{at}.kube_role"),
            os=str(spec["os"]),
            interfaces=tuple(interfaces),
            labels=_labels(spec.get("labels"), f"{at}.labels"),
        )

    hosts = {}
    for name, spec in _map(raw["hosts"], "hosts").items():
        spec = _map(spec, f"hosts.{name}")
        _keys(spec, f"hosts.{name}", ("class", "addresses"))
        addresses = {
            str(iface): _address(value, f"hosts.{name}.addresses.{iface}")
            for iface, value in _map(spec["addresses"], f"hosts.{name}.addresses").items()
        }
        hosts[name] = Host(name=name, node_class=str(spec["class"]), addresses=addresses)

    return Site(
        name=str(raw["site"]),
        node_network=str(kube["node_network"]),
        api_vip=_address(api["vip"], "kubernetes.api.vip"),
        api_port=_int(api["port"], "kubernetes.api.port"),
        pod_cidr=_network(kube["pod_cidr"], "kubernetes.pod_cidr"),
        service_cidr=_network(kube["service_cidr"], "kubernetes.service_cidr"),
        kube_proxy=_bool(kube["kube_proxy"], "kubernetes.kube_proxy"),
        control_plane_schedulable=_bool(kube["control_plane_schedulable"], "kubernetes.control_plane_schedulable"),
        syslog_address=_address(syslog["address"], "services.syslog.address"),
        syslog_port=_int(syslog["port"], "services.syslog.port"),
        networks=networks,
        classes=classes,
        hosts=hosts,
        fabrics={name: _fabric(name, spec) for name, spec in _map(raw["fabrics"], "fabrics").items()},
    )


# Lab inventory variables that hold address ranges, besides lab_address_plan itself.
LAB_K8S_RANGES = {"pod": "k8s_pod_cidr", "service": "k8s_service_cidr"}


def lab_plan(group_vars: dict) -> dict[str, str]:
    """Every address range the lab inventory claims, from its merged group_vars/all."""
    plan = dict(group_vars.get("lab_address_plan") or {})
    plan.update({key: group_vars[var] for key, var in LAB_K8S_RANGES.items() if var in group_vars})
    return plan


def lab_ranges(lab_plan: dict) -> dict[str, list[ipaddress.IPv4Network]]:
    """The lab address plan of ansible/inventories/lab/group_vars/all/lab.yml as networks.
    A range a-b becomes the networks that cover exactly a to b."""
    found = {}
    for key, value in (lab_plan or {}).items():
        text = str(value)
        if "-" in text:
            first, last = (ipaddress.IPv4Address(p.strip()) for p in text.split("-", 1))
            found[key] = list(ipaddress.summarize_address_range(first, last))
        else:
            found[key] = [ipaddress.IPv4Network(text, strict=False)]
    return found


def _interface_name_problems(site: Site) -> list[str]:
    found = []
    for node_class in site.classes.values():
        for iface in node_class.interfaces:
            where = f"classes.{node_class.name}.interfaces.{iface.name}"
            if not INTERFACE_NAME.match(iface.name):
                found.append(f"{where}: not a Linux interface name of at most 15 lowercase letters and digits")
            elif iface.name.startswith(KERNEL_PREFIXES):
                found.append(f"{where}: a .link rename to a kernel or udev name prefix races with the kernel")
    return found


def _reference_problems(site: Site) -> list[str]:
    found = []
    for node_class in site.classes.values():
        where = f"classes.{node_class.name}"
        if not GROUP_NAME.match(node_class.name) or node_class.name in CONTRACT_GROUPS:
            found.append(f"{where}: a class name must be a new Ansible group name of lowercase letters, digits and underscores")
        for iface in node_class.interfaces:
            if iface.network not in site.networks:
                found.append(f"{where}.interfaces.{iface.name}: unknown network {iface.network}")
        on_node_network = [i.name for i in node_class.interfaces if i.network == site.node_network]
        if len(on_node_network) != 1:
            found.append(f"{where}: needs exactly one interface on the node network {site.node_network}, has {len(on_node_network)}")
    if site.node_network not in site.networks:
        found.append(f"kubernetes.node_network: unknown network {site.node_network}")
    for host in site.hosts.values():
        if not HOST_NAME.match(host.name):
            found.append(f"hosts.{host.name}: not a Kubernetes node name, which is an RFC 1123 label")
        node_class = site.classes.get(host.node_class)
        if node_class is None:
            found.append(f"hosts.{host.name}: unknown class {host.node_class}")
            continue
        wanted = {i.name for i in node_class.interfaces}
        for iface in sorted(wanted - set(host.addresses)):
            found.append(f"hosts.{host.name}: no address for interface {iface}")
        for iface in sorted(set(host.addresses) - wanted):
            found.append(f"hosts.{host.name}: address for {iface}, which class {node_class.name} does not have")
    names = Counter([*site.hosts, *(s for f in site.fabrics.values() for s in f.switches)])
    for name in sorted(n for n, count in names.items() if count > 1):
        found.append(f"name {name} is used by more than one host or switch")
    return found


def _address_problems(site: Site, lab: dict[str, list[ipaddress.IPv4Network]]) -> list[str]:
    found = []
    ranges = [(f"networks.{n.name}", n.cidr) for n in site.networks.values()]
    ranges += [("kubernetes.pod_cidr", site.pod_cidr), ("kubernetes.service_cidr", site.service_cidr)]
    for i, (where_a, a) in enumerate(ranges):
        for where_b, b in ranges[i + 1:]:
            if a.overlaps(b):
                found.append(f"{where_a} {a} overlaps {where_b} {b}")
        for key, nets in lab.items():
            if any(a.overlaps(n) for n in nets):
                found.append(f"{where_a} {a} overlaps the lab address plan entry {key}")

    held: list[tuple[str, ipaddress.IPv4Address]] = [
        ("kubernetes.api.vip", site.api_vip),
        ("services.syslog.address", site.syslog_address),
    ]
    node_net = site.networks.get(site.node_network)
    if node_net and site.api_vip not in node_net.cidr:
        found.append(f"kubernetes.api.vip {site.api_vip} is outside the node network {node_net.cidr}")
    for host in site.hosts.values():
        node_class = site.classes.get(host.node_class)
        attach = {i.name: i.network for i in node_class.interfaces} if node_class else {}
        for iface, address in host.addresses.items():
            where = f"hosts.{host.name}.addresses.{iface}"
            held.append((where, address))
            network = site.networks.get(attach.get(iface, ""))
            if network is None:
                continue
            if address not in network.cidr or address in (network.cidr.network_address, network.cidr.broadcast_address):
                found.append(f"{where}: {address} is not a host address of {network.name} {network.cidr}")
    counts = Counter(address for _, address in held)
    for address in sorted(a for a, n in counts.items() if n > 1):
        found.append(f"address {address} is held by {', '.join(w for w, a in held if a == address)}")
    return found


def _kubernetes_problems(site: Site) -> list[str]:
    found = []
    control_planes = site.members("control_plane")
    if not control_planes:
        found.append("no host has a control_plane class")
    vip_ifaces = {
        iface.name
        for h in control_planes
        if (iface := site.node_interface(site.classes[h.node_class])) is not None
    }
    if len(vip_ifaces) > 1:
        found.append(f"control plane classes name the node network interface differently ({', '.join(sorted(vip_ifaces))}), kube-vip takes one")
    return found


def _fabric_problems(site: Site, fabric: Fabric) -> list[str]:
    where = f"fabrics.{fabric.name}"
    found = []
    rails = {n.name: n.rail for n in site.networks.values() if n.rail is not None}
    for sw in fabric.switches.values():
        if sw.model not in fabric.switch_ports:
            found.append(f"{where}.switches.{sw.name}: unknown switch model {sw.model}")
        if (sw.role == "leaf") != (sw.rail is not None):
            found.append(f"{where}.switches.{sw.name}: a leaf names its rail and a spine does not")

    rail_ports = {
        Endpoint(h.name, i.name): rails[i.network]
        for h in site.hosts.values()
        if h.node_class in site.classes
        for i in site.classes[h.node_class].interfaces
        if i.network in rails
    }
    used: dict[Endpoint, int] = Counter()
    uplinks: dict[str, set[str]] = defaultdict(set)
    for n, (a, b) in enumerate(fabric.links):
        at = f"{where}.links[{n}] {a} - {b}"
        kinds = []
        for end in (a, b):
            used[end] += 1
            if end.node in fabric.switches:
                sw = fabric.switches[end.node]
                kinds.append(sw.role)
                radix = fabric.switch_ports.get(sw.model)
                if not end.port.isdigit():
                    found.append(f"{at}: switch port {end.port} is not a number")
                elif radix is not None and not 1 <= int(end.port) <= radix:
                    found.append(f"{at}: {sw.name} has ports 1 to {radix}, not {end.port}")
            elif end in rail_ports:
                kinds.append("rail")
            elif end.node in site.hosts:
                found.append(f"{at}: {end} is not a compute rail interface")
                kinds.append("other")
            else:
                found.append(f"{at}: unknown node {end.node}")
                kinds.append("other")
        if "other" in kinds:
            continue
        pair = sorted(kinds)
        if pair == ["leaf", "rail"]:
            rail_end, leaf_end = (a, b) if a in rail_ports else (b, a)
            leaf = fabric.switches[leaf_end.node]
            if leaf.rail != rail_ports[rail_end]:
                found.append(f"{at}: rail {rail_ports[rail_end]} port cabled to {leaf.name} of rail {leaf.rail}")
        elif pair == ["leaf", "spine"]:
            leaf_end, spine_end = (a, b) if fabric.switches[a.node].role == "leaf" else (b, a)
            uplinks[leaf_end.node].add(spine_end.node)
        else:
            found.append(f"{at}: a rail-optimized fabric cables rail ports to leaves and leaves to spines, not {' to '.join(kinds)}")

    for end, count in sorted(used.items(), key=lambda kv: str(kv[0])):
        if count > 1:
            found.append(f"{where}: port {end} is used by {count} links")
    for end in sorted(set(rail_ports) - set(used), key=str):
        found.append(f"{where}: rail port {end} is not cabled")
    spines = sorted(s.name for s in fabric.switches.values() if s.role == "spine")
    for leaf in sorted(s.name for s in fabric.switches.values() if s.role == "leaf"):
        missing = [s for s in spines if s not in uplinks[leaf]]
        if missing:
            found.append(f"{where}: leaf {leaf} has no uplink to {', '.join(missing)}")
    return found


def problems(site: Site, lab_plan: dict | None = None) -> list[str]:
    """Every way the model breaks its own rules. An empty list means it is valid.

    lab_plan maps each lab address range by name. No site range may overlap one.
    """
    found = _interface_name_problems(site) + _reference_problems(site)
    found += _address_problems(site, lab_ranges(lab_plan or {}))
    found += _kubernetes_problems(site)
    for fabric in site.fabrics.values():
        found += _fabric_problems(site, fabric)
    return found
