"""The NetBox objects a site model asks for (ADR 0008).

Each kind maps a natural key to the fields NetBox must hold. A field that names another object holds
a Ref to that object's kind and key, and the sync turns it into an id. Every object carries the
managed tag, so the sync touches exactly these objects and nothing a person adds by hand.

    tag           the managed tag itself
    manufacturer  one, for the device types the model invents
    device-type   one per class, and one per switch model of a fabric
    role          one per class, and one per switch role
    site          the site
    device        every host and switch
    interface     every class interface of every host, and every port of every switch
    prefix        every network, every rail pod range, the pod and the Service range
    ip            every host address on its interface, the API VIP and the syslog address
    cable         every link of every fabric

The same model always gives the same objects.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from fleet.site import Site

TAG = "rfl-site-model"
MANUFACTURER = "site-model"
KINDS = ("tag", "manufacturer", "device-type", "role", "site", "device", "interface", "prefix", "ip", "cable")
# Ethernet ports of the model have no speed, so NetBox records them as type other.
ETHERNET_TYPE = "other"
ROLE_COLORS = {"control_plane": "607d8b", "leaf": "8bc34a", "spine": "4caf50"}
DEFAULT_ROLE_COLOR = "2196f3"


@dataclass(frozen=True)
class Ref:
    kind: str
    key: str


def slug(name: str) -> str:
    return name.lower().replace("_", "-")


def interface_key(device: str, name: str) -> str:
    return f"{device}/{name}"


def cable_key(a: str, b: str) -> str:
    """The two interface keys of a cable in sorted order, so either direction names the same cable."""
    return " = ".join(sorted((a, b)))


def ib_type(speed: str) -> str:
    return f"infiniband-{speed.lower()}"


def desired(site: Site) -> dict[str, dict[str, dict]]:
    """kind -> key -> fields."""
    out: dict[str, dict[str, dict]] = {k: {} for k in KINDS}
    out["tag"][TAG] = {"name": TAG, "color": "8bc34a",
                       "description": f"Kept equal to sites/{site.name}.yaml by make netbox-sync"}
    out["manufacturer"][MANUFACTURER] = {"name": "Site model", "description": "Device types the site model invents"}
    site_ref = Ref("site", site.name)
    out["site"][site.name] = {"name": site.name, "status": "active",
                              "description": f"Invented GPU region, sites/{site.name}.yaml"}

    def add_type(name: str, description: str) -> Ref:
        out["device-type"][slug(name)] = {"model": name, "manufacturer": Ref("manufacturer", MANUFACTURER),
                                           "description": description}
        return Ref("device-type", slug(name))

    def add_role(name: str, description: str) -> Ref:
        out["role"][slug(name)] = {"name": name, "color": ROLE_COLORS.get(name, DEFAULT_ROLE_COLOR),
                                    "description": description}
        return Ref("role", slug(name))

    for cls in site.classes.values():
        type_ref = add_type(cls.name, f"Class {cls.name} of the site model, {cls.os}")
        role_ref = add_role(cls.name, f"Hosts of class {cls.name}")
        for host in (h for h in site.hosts.values() if h.node_class == cls.name):
            out["device"][host.name] = {"device_type": type_ref, "role": role_ref, "site": site_ref, "status": "active"}
            for iface in cls.interfaces:
                network = site.networks[iface.network]
                kind = ib_type(next(iter(site.fabrics.values())).link_speed) if network.kind == "infiniband" and site.fabrics \
                    else ETHERNET_TYPE
                out["interface"][interface_key(host.name, iface.name)] = {
                    "device": Ref("device", host.name), "name": iface.name, "type": kind, "label": iface.hca or "",
                    "description": f"network {network.name}"}
                address = host.addresses[iface.name]
                out["ip"][f"{address}/{network.cidr.prefixlen}"] = {
                    "interface": Ref("interface", interface_key(host.name, iface.name)), "role": None, "status": "active",
                    "description": f"{host.name} {iface.name}"}

    node_network = site.networks[site.node_network]
    out["ip"][f"{site.api_vip}/{node_network.cidr.prefixlen}"] = {
        "interface": None, "role": "vip", "status": "active", "description": "Kubernetes API, kube-vip"}
    syslog_net = next(n for n in site.networks.values() if site.syslog_address in n.cidr)
    out["ip"][f"{site.syslog_address}/{syslog_net.cidr.prefixlen}"] = {
        "interface": None, "role": None, "status": "active", "description": "Central syslog server, outside the model"}

    for fabric in site.fabrics.values():
        port_type = ib_type(fabric.link_speed)
        for model, ports in fabric.switch_ports.items():
            add_type(model, f"Switch model of fabric {fabric.name}, {ports} ports")
        for sw in fabric.switches.values():
            out["device"][sw.name] = {"device_type": Ref("device-type", slug(sw.model)),
                                      "role": add_role(sw.role, f"InfiniBand {sw.role} switches"), "site": site_ref,
                                      "status": "active"}
            for port in range(1, fabric.switch_ports[sw.model] + 1):
                out["interface"][interface_key(sw.name, str(port))] = {
                    "device": Ref("device", sw.name), "name": str(port), "type": port_type, "label": "",
                    "description": f"rail {sw.rail}" if sw.rail is not None else ""}
        for a, b in fabric.links:
            ka, kb = interface_key(a.node, a.port), interface_key(b.node, b.port)
            rail_of = [site.networks[i.network].rail for i in
                       site.classes[site.hosts[a.node].node_class].interfaces if i.name == a.port] if a.node in site.hosts else []
            out["cable"][cable_key(ka, kb)] = {"a": Ref("interface", ka), "b": Ref("interface", kb), "status": "connected",
                                               "label": f"rail{rail_of[0]}" if rail_of else "uplink"}

    for network in site.networks.values():
        out["prefix"][str(network.cidr)] = {"scope": site_ref, "status": "active",
                                            "description": f"network {network.name}, {network.kind}"}
        if network.pod_cidr is not None:
            out["prefix"][str(network.pod_cidr)] = {"scope": site_ref, "status": "active",
                                                    "description": f"pods on {network.name}"}
    out["prefix"][str(site.pod_cidr)] = {"scope": site_ref, "status": "active", "description": "Kubernetes pods"}
    out["prefix"][str(site.service_cidr)] = {"scope": site_ref, "status": "active", "description": "Kubernetes Services"}
    return out


def counts(objects: dict[str, dict[str, dict]]) -> dict[str, int]:
    return {kind: len(objects[kind]) for kind in KINDS}


def check_addresses(objects: dict[str, dict[str, dict]]) -> list[str]:
    """Every address lies in a desired prefix, which NetBox does not demand but the model does."""
    nets = [ipaddress.ip_network(p) for p in objects["prefix"]]
    return [a for a in objects["ip"] if not any(ipaddress.ip_interface(a).ip in n for n in nets)]
