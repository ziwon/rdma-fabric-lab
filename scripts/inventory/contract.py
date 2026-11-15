"""The inventory contract of ADR 0001 section 4, checked on `ansible-inventory --list` output.

ansible-inventory prints values as written, without templating. A value that is exactly
`{{ other_variable }}` is resolved against the same host. Any other template cannot be
checked offline and is reported.
"""

from __future__ import annotations

import ipaddress
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable

CONTRACT_GROUPS = ("kube_control_plane", "kube_node", "k8s_cluster")
RESERVED_LABEL_DOMAINS = ("kubernetes.io", "k8s.io")
LABEL_NAME = re.compile(r"^[A-Za-z0-9]([-A-Za-z0-9_.]{0,61}[A-Za-z0-9])?$")
LABEL_PREFIX = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$")
REFERENCE = re.compile(r"^\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}$")
REQUIRED = object()
CONTAINER_RUNTIMES = ("runc", "nvidia")


@dataclass(frozen=True)
class Violation:
    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


def _text(value) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("is not a non-empty string")
    return value


def _ipv4(value) -> ipaddress.IPv4Address:
    try:
        return ipaddress.IPv4Address(str(value))
    except ValueError:
        raise ValueError("is not an IPv4 address") from None


def _optional_ipv4(value) -> ipaddress.IPv4Address | None:
    return None if value is None else _ipv4(value)


def _cidr(value) -> ipaddress.IPv4Network:
    try:
        return ipaddress.IPv4Network(str(value), strict=True)
    except ValueError:
        raise ValueError("is not an IPv4 CIDR") from None


def _port(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise ValueError("is not a port number")
    return value


def _bool(value) -> bool:
    if not isinstance(value, bool):
        raise ValueError("is not true or false")
    return value


def _runtime(value) -> str:
    if value not in CONTAINER_RUNTIMES:
        raise ValueError(f"is not one of {', '.join(CONTAINER_RUNTIMES)}")
    return value


def _labels(value) -> dict:
    if not isinstance(value, dict):
        raise ValueError("is not a map")
    return value


@dataclass(frozen=True)
class Variable:
    name: str
    cluster_wide: bool
    parse: Callable[[Any], Any]
    default: Any = REQUIRED


# ADR 0001 section 4, in its order. default is what the roles assume when a host omits it.
VARIABLES = (
    Variable("rfl_inventory", True, _text),
    Variable("k8s_node_ip", False, _ipv4),
    Variable("k8s_api_endpoint", True, _text),
    Variable("k8s_api_port", True, _port, 6443),
    Variable("k8s_api_vip", True, _optional_ipv4, None),
    Variable("k8s_api_vip_interface", True, _text, None),
    Variable("k8s_pod_cidr", True, _cidr),
    Variable("k8s_service_cidr", True, _cidr),
    Variable("k8s_kube_proxy", True, _bool),
    Variable("k8s_control_plane_schedulable", False, _bool, False),
    Variable("k8s_node_labels", False, _labels, {}),
    Variable("k8s_container_runtime_default", False, _runtime, "runc"),
    Variable("k8s_admin_kubeconfig", True, _text, None),
)


def members(inventory: dict, group: str) -> list[str]:
    """Hosts of a group and of its descendants, in ansible-inventory order."""
    seen: list[str] = []
    stack = [group]
    visited = set()
    while stack:
        name = stack.pop(0)
        if name in visited:
            continue
        visited.add(name)
        entry = inventory.get(name) or {}
        seen += [h for h in entry.get("hosts", []) if h not in seen]
        stack += entry.get("children", [])
    return seen


def _resolve(hostvars: dict, value, depth: int = 0):
    if isinstance(value, str) and "{{" in value:
        match = REFERENCE.match(value)
        if not match or match.group(1) not in hostvars or depth > 8:
            raise LookupError(value)
        return _resolve(hostvars, hostvars[match.group(1)], depth + 1)
    return value


def _label_problems(host: str, labels: dict) -> list[Violation]:
    found = []
    for key, value in labels.items():
        prefix, _, name = str(key).rpartition("/")
        where = f"{host}: k8s_node_labels {key}"
        domain = prefix.lower()
        if any(domain == d or domain.endswith("." + d) for d in RESERVED_LABEL_DOMAINS):
            found.append(Violation("reserved-label-prefix", f"{where}: kubelet may not set labels under {prefix}"))
            continue
        if (prefix and (len(prefix) > 253 or not LABEL_PREFIX.match(prefix))) or not LABEL_NAME.match(name):
            found.append(Violation("invalid-label", f"{where}: not a valid label key"))
        if not isinstance(value, str):
            found.append(Violation("invalid-label", f"{where}: value {value!r} is not a string, kubelet would get {str(value)!r}"))
        elif value and not LABEL_NAME.match(value):
            found.append(Violation("invalid-label", f"{where}: value {value!r} is not a valid label value"))
    return found


def _cilium_problems(cluster: dict, values: dict) -> list[Violation]:
    found = []

    def mismatch(message: str) -> None:
        found.append(Violation("cilium-mismatch", message))

    replacement = str(values.get("kubeProxyReplacement", "false")).lower() == "true"
    if replacement == cluster["k8s_kube_proxy"]:
        state = "on" if replacement else "off"
        mismatch(f"kube-proxy and the Cilium kube-proxy replacement are both {state}")
    if replacement:
        host, port = values.get("k8sServiceHost"), values.get("k8sServicePort")
        if host != "auto" and host != cluster["k8s_api_endpoint"]:
            mismatch(f"k8sServiceHost {host!r} is not k8s_api_endpoint {cluster['k8s_api_endpoint']}")
        if host != "auto" and str(port) != str(cluster["k8s_api_port"]):
            mismatch(f"k8sServicePort {port!r} is not k8s_api_port {cluster['k8s_api_port']}")
    ipam = values.get("ipam") or {}
    pod = cluster["k8s_pod_cidr"]
    if ipam.get("mode") == "cluster-pool":
        pools = (ipam.get("operator") or {}).get("clusterPoolIPv4PodCIDRList")
        if [str(p) for p in (pools if isinstance(pools, list) else [pools])] != [str(pod)]:
            mismatch(f"ipam.operator.clusterPoolIPv4PodCIDRList {pools!r} is not [k8s_pod_cidr {pod}]")
    elif ipam.get("mode") != "kubernetes":
        mismatch(f"ipam.mode {ipam.get('mode')!r} takes pod addresses from neither kubeadm nor k8s_pod_cidr")
    if values.get("routingMode") == "native":
        native = values.get("ipv4NativeRoutingCIDR")
        try:
            covers = pod.subnet_of(ipaddress.IPv4Network(str(native), strict=False))
        except ValueError:
            covers = False
        if not covers:
            mismatch(f"native routing needs ipv4NativeRoutingCIDR to cover k8s_pod_cidr {pod}, it is {native!r}")
    return found


def problems(
    inventory: dict,
    environments: set[str],
    cilium_values: Callable[[str], dict | None] = lambda env: None,
    explicit: bool = False,
) -> list[Violation]:
    """Every way the inventory breaks the contract. An empty list means it holds.

    environments are the environment names of helm/releases.yaml. cilium_values maps an
    environment to its merged Cilium values, or None when it has no Cilium release.
    explicit demands that every contract variable is set on every host, so no host relies
    on a role default.
    """
    found: list[Violation] = []
    for group in CONTRACT_GROUPS:
        if group not in inventory:
            found.append(Violation("missing-group", f"group {group} is not defined"))
    control_planes = members(inventory, "kube_control_plane")
    nodes = members(inventory, "kube_node")
    cluster_hosts = members(inventory, "k8s_cluster")
    if "kube_control_plane" in inventory and not control_planes:
        found.append(Violation("missing-group", "group kube_control_plane has no host"))
    for host in sorted(set(control_planes) & set(nodes)):
        found.append(Violation("group-membership", f"{host} is in both kube_control_plane and kube_node"))
    if "k8s_cluster" in inventory and set(cluster_hosts) != set(control_planes) | set(nodes):
        odd = sorted(set(cluster_hosts) ^ (set(control_planes) | set(nodes)))
        found.append(Violation("group-membership", f"k8s_cluster is not kube_control_plane plus kube_node: {', '.join(odd)} differ"))

    hostvars = (inventory.get("_meta") or {}).get("hostvars") or {}
    values: dict[str, dict[str, Any]] = defaultdict(dict)
    for host in cluster_hosts:
        own = hostvars.get(host) or {}
        for var in VARIABLES:
            if var.name not in own:
                needed = var.default is REQUIRED or explicit
                if var.name == "k8s_api_vip_interface" and not explicit:
                    needed = _resolvable_vip(own)
                if needed:
                    found.append(Violation("missing-variable", f"{host}: {var.name} is not set"))
                elif var.default is not REQUIRED:
                    values[host][var.name] = var.default
                continue
            try:
                values[host][var.name] = var.parse(_resolve(own, own[var.name]))
            except LookupError:
                found.append(Violation("unresolved-template", f"{host}: {var.name} is a template this offline check cannot evaluate"))
            except ValueError as exc:
                found.append(Violation("bad-value", f"{host}: {var.name} {own[var.name]!r} {exc}"))

    cluster: dict[str, Any] = {}
    for var in (v for v in VARIABLES if v.cluster_wide):
        seen = {repr(values[h][var.name]) for h in cluster_hosts if var.name in values[h]}
        if len(seen) > 1:
            found.append(Violation("inconsistent-variable", f"{var.name} differs between hosts: {', '.join(sorted(seen))}"))
        elif seen:
            cluster[var.name] = next(values[h][var.name] for h in cluster_hosts if var.name in values[h])

    holders = defaultdict(list)
    for host in cluster_hosts:
        if "k8s_node_ip" in values[host]:
            holders[values[host]["k8s_node_ip"]].append(host)
    for address, hosts in sorted(holders.items()):
        if len(hosts) > 1:
            found.append(Violation("duplicate-node-ip", f"{address} is k8s_node_ip of {', '.join(hosts)}"))

    pod, service = cluster.get("k8s_pod_cidr"), cluster.get("k8s_service_cidr")
    ranges = [(n, c) for n, c in (("k8s_pod_cidr", pod), ("k8s_service_cidr", service)) if c is not None]
    if pod and service and pod.overlaps(service):
        found.append(Violation("range-overlap", f"k8s_pod_cidr {pod} overlaps k8s_service_cidr {service}"))
    named = [(f"k8s_node_ip of {', '.join(h)}", a) for a, h in sorted(holders.items())]
    if cluster.get("k8s_api_vip"):
        named.append(("k8s_api_vip", cluster["k8s_api_vip"]))
    for where, address in named:
        for name, cidr in ranges:
            if address in cidr:
                found.append(Violation("range-overlap", f"{where} {address} lies inside {name} {cidr}"))

    vip, endpoint = cluster.get("k8s_api_vip"), cluster.get("k8s_api_endpoint")
    if len(control_planes) > 1 and vip is None:
        found.append(Violation("missing-vip", f"{len(control_planes)} control planes need k8s_api_vip"))
    if vip is not None and endpoint is not None and str(vip) != endpoint:
        found.append(Violation("vip-endpoint-mismatch", f"k8s_api_vip {vip} is not k8s_api_endpoint {endpoint}"))

    env = cluster.get("rfl_inventory")
    if env is not None and env not in environments:
        found.append(Violation("unknown-environment", f"rfl_inventory {env} is not an environment of helm/releases.yaml ({', '.join(sorted(environments))})"))

    for host in cluster_hosts:
        found += _label_problems(host, values[host].get("k8s_node_labels") or {})

    cilium = cilium_values(env) if env in environments else None
    needed = ("k8s_kube_proxy", "k8s_api_endpoint", "k8s_api_port", "k8s_pod_cidr")
    if cilium is not None and all(k in cluster for k in needed):
        found += _cilium_problems(cluster, cilium)
    return found


def _resolvable_vip(own: dict) -> bool:
    try:
        return _resolve(own, own.get("k8s_api_vip")) is not None
    except LookupError:
        return True
