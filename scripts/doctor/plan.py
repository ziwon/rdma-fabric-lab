"""Lab address plan against the networks a host already uses.

Every range is an inclusive interval of integer addresses, so a CIDR, a single
address and an "a-b" pool compare the same way.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

PLAN_KEYS = ("management", "rdma", "pod", "service", "multus_pool")

# Plan ranges that must not share a single address with each other. The Multus
# pool is carved out of the RDMA network on purpose, so that pair is checked by
# containment instead.
DISJOINT_PAIRS = (
    ("management", "rdma"),
    ("management", "pod"),
    ("management", "service"),
    ("rdma", "pod"),
    ("rdma", "service"),
    ("pod", "service"),
)


@dataclass(frozen=True)
class Span:
    label: str
    version: int
    first: int
    last: int

    def overlaps(self, other: Span) -> bool:
        return self.version == other.version and self.first <= other.last and other.first <= self.last

    def contains(self, other: Span) -> bool:
        return self.version == other.version and self.first <= other.first and other.last <= self.last


def networks(s: Span) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """The CIDR blocks that exactly cover a span."""
    make = ipaddress.IPv4Address if s.version == 4 else ipaddress.IPv6Address
    return list(ipaddress.summarize_address_range(make(s.first), make(s.last)))


def span(label: str, text: str) -> Span:
    """A CIDR ("10.77.0.0/24"), an address ("10.77.0.12") or a pool ("10.77.0.100-10.77.0.199")."""
    text = text.strip()
    if "-" in text:
        low, high = (ipaddress.ip_address(p.strip()) for p in text.split("-", 1))
        if low.version != high.version or int(low) > int(high):
            raise ValueError(f"{label}: {text} is not an ascending range of one address family")
        return Span(label, low.version, int(low), int(high))
    net = ipaddress.ip_network(text, strict=False)
    return Span(label, net.version, int(net.network_address), int(net.broadcast_address))


def plan_spans(plan: dict) -> dict[str, Span]:
    missing = [k for k in PLAN_KEYS if k not in plan]
    if missing:
        raise ValueError(f"address plan lacks {', '.join(missing)}")
    return {k: span(f"plan {k} {plan[k]}", str(plan[k])) for k in PLAN_KEYS}


# Inventory variable of a node address -> the plan range that must hold it.
NODE_ADDRESSES = (("k8s_node_ip", "management"), ("lab_rdma_ip", "rdma"))
# Plan ranges that come from the inventory contract of docs/adr/0001-kubernetes-stack.md.
CONTRACT_RANGES = (("pod", "k8s_pod_cidr"), ("service", "k8s_service_cidr"))


def from_hostvars(hostvars: dict[str, dict]) -> tuple[dict, dict[str, dict]]:
    """(address plan, node addresses) from ansible-inventory --list hostvars of the lab inventory."""
    first = next(iter(hostvars.values()))
    address_plan = dict(first["lab_address_plan"])
    address_plan |= {key: first[var] for key, var in CONTRACT_RANGES if var in first}
    names = [var for var, _ in NODE_ADDRESSES]
    nodes = {h: {k: v for k, v in hv.items() if k in names} for h, hv in hostvars.items()}
    return address_plan, nodes


def plan_problems(plan: dict, nodes: dict[str, dict]) -> list[str]:
    """Contradictions inside the plan itself. nodes maps a host to its k8s_node_ip and lab_rdma_ip."""
    spans = plan_spans(plan)
    found = [
        f"{spans[a].label} overlaps {spans[b].label}"
        for a, b in DISJOINT_PAIRS
        if spans[a].overlaps(spans[b])
    ]
    if not spans["rdma"].contains(spans["multus_pool"]):
        found.append(f"{spans['multus_pool'].label} lies outside {spans['rdma'].label}")
    seen: dict[str, str] = {}
    for host, values in sorted(nodes.items()):
        for key, network in NODE_ADDRESSES:
            if key not in values:
                continue
            address = span(f"{host} {key} {values[key]}", str(values[key]))
            if not spans[network].contains(address):
                found.append(f"{address.label} lies outside {spans[network].label}")
            if spans["multus_pool"].overlaps(address):
                found.append(f"{address.label} falls inside {spans['multus_pool'].label}")
            if str(values[key]) in seen:
                found.append(f"{address.label} is also assigned to {seen[str(values[key])]}")
            seen[str(values[key])] = f"{host} {key}"
    return found


def overlaps(plan: dict, host_spans: list[Span]) -> list[tuple[Span, Span]]:
    """Every (plan range, host range) pair that shares at least one address."""
    return [
        (mine, theirs)
        for mine in plan_spans(plan).values()
        for theirs in host_spans
        if mine.overlaps(theirs)
    ]
