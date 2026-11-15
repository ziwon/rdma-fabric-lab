"""InfiniBand topologies as ibsim reads them and as ibnetdiscover prints them.

A topology is a set of nodes, each a switch or an HCA with a port count, and a set of links, each
joining two ports. Node names are what ibsim takes as node ids and reports as NodeDescription, so
an expected topology and a discovered one compare by name.

The net file format is the one ibsim reads and ibnetdiscover writes. One record per node, each
record a header line and one line per connected port, both ends of every link listed:

    Switch	64 "cel1-ib-leaf-r0"
    [1]	"cel1-dgx-01 mlx5_0"[1]

    Hca	1 "cel1-dgx-01 mlx5_0"
    [1]	"cel1-ib-leaf-r0"[1]
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field

from fleet.site import Fabric, Site

SWITCH = "Switch"
HCA = "Hca"
# ibnetdiscover writes Ca where ibsim also accepts Hca. Both mean a channel adapter.
NODE_KINDS = {"Switch": SWITCH, "Hca": HCA, "Ca": HCA}

HEADER = re.compile(r'^(Switch|Hca|Ca)\s+(\d+)\s+"([^"]+)"(.*)$')
PORT_LINE = re.compile(r'^\[(\d+)\](?:\([0-9a-fA-F]+\))?\s*"([^"]+)"\[(\d+)\](.*)$')
COMMENT_NAME = re.compile(r'#\s*"([^"]*)"')
SWITCH_LID = re.compile(r"\blid (\d+) lmc (\d+)")
CA_LID = re.compile(r"#\s*lid (\d+) lmc (\d+)")


class TopologyError(ValueError):
    pass


@dataclass(frozen=True, order=True)
class Port:
    node: str
    port: int

    def __str__(self) -> str:
        return f'"{self.node}"[{self.port}]'


@dataclass(frozen=True)
class Node:
    name: str
    kind: str
    ports: int


Link = frozenset  # of exactly two Port


@dataclass(frozen=True)
class Topology:
    nodes: tuple[Node, ...]
    links: frozenset[Link]
    _by_name: dict[str, Node] = field(init=False, repr=False, compare=False)
    _peer: dict[Port, Port] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        by_name = {n.name: n for n in self.nodes}
        if len(by_name) != len(self.nodes):
            raise TopologyError("a node name appears twice")
        peer: dict[Port, Port] = {}
        for link in self.links:
            if len(link) != 2:
                raise TopologyError(f"link {sorted(link)} does not join two ports")
            a, b = sorted(link)
            for end in (a, b):
                node = by_name.get(end.node)
                if node is None:
                    raise TopologyError(f"link {a} - {b} names an unknown node {end.node!r}")
                if not 1 <= end.port <= node.ports:
                    raise TopologyError(f"link {a} - {b}: {end.node} has ports 1 to {node.ports}")
                if end in peer:
                    raise TopologyError(f"port {end} is cabled twice")
            peer[a], peer[b] = b, a
        object.__setattr__(self, "_by_name", by_name)
        object.__setattr__(self, "_peer", peer)

    def node(self, name: str) -> Node:
        return self._by_name[name]

    def peer(self, port: Port) -> Port | None:
        return self._peer.get(port)

    def of_kind(self, kind: str) -> list[Node]:
        return [n for n in self.nodes if n.kind == kind]

    def without_link_at(self, port: Port) -> Topology:
        peer = self.peer(port)
        if peer is None:
            raise TopologyError(f"port {port} has no link to remove")
        return Topology(self.nodes, self.links - {Link({port, peer})})

    def hops_from(self, name: str) -> dict[str, int]:
        """Hop count from one node to every node it reaches. Hops cross links, never a node's own ports."""
        neighbours: dict[str, list[str]] = {n.name: [] for n in self.nodes}
        for a, b in (sorted(link) for link in self.links):
            neighbours[a.node].append(b.node)
            neighbours[b.node].append(a.node)
        dist = {name: 0}
        queue = deque([name])
        while queue:
            here = queue.popleft()
            for there in neighbours[here]:
                if there not in dist:
                    dist[there] = dist[here] + 1
                    queue.append(there)
        return dist

    def reachable_from(self, name: str) -> Topology:
        """The part of the fabric a subnet manager attached at this node can discover."""
        keep = set(self.hops_from(name))
        return Topology(
            tuple(n for n in self.nodes if n.name in keep),
            frozenset(link for link in self.links if all(p.node in keep for p in link)),
        )


def counts(topology: Topology) -> dict[str, int]:
    return {
        "switches": len(topology.of_kind(SWITCH)),
        "hcas": len(topology.of_kind(HCA)),
        "switch_ports": sum(n.ports for n in topology.of_kind(SWITCH)),
        "hca_ports": sum(n.ports for n in topology.of_kind(HCA)),
        "links": len(topology.links),
    }


# ---------------------------------------------------------------- net files


@dataclass
class _Record:
    kind: str
    ports: int
    ident: str
    description: str
    lines: list[tuple[int, str, int, str]]
    header_rest: str


def _records(text: str) -> list[_Record]:
    records: list[_Record] = []
    current: _Record | None = None
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            current = None if not line else current
            continue
        header = HEADER.match(line)
        if header:
            kind, ports, ident, rest = header.groups()
            name = COMMENT_NAME.search(rest)
            current = _Record(NODE_KINDS[kind], int(ports), ident, name.group(1) if name else ident, [], rest)
            records.append(current)
            continue
        port_line = PORT_LINE.match(line)
        if port_line and current is not None:
            local, remote, remote_port, rest = port_line.groups()
            current.lines.append((int(local), remote, int(remote_port), rest))
            continue
        if current is not None and "=" not in line:
            raise TopologyError(f"line {number}: {raw!r} is neither a node header nor a port line")
    return records


def _topology(records: list[_Record], names: dict[str, str]) -> Topology:
    nodes = tuple(Node(names[r.ident], r.kind, r.ports) for r in records)
    said: dict[Port, Port] = {}
    for r in records:
        for local, remote, remote_port, _ in r.lines:
            if remote not in names:
                raise TopologyError(f"{names[r.ident]}[{local}] names an unknown node {remote!r}")
            said[Port(names[r.ident], local)] = Port(names[remote], remote_port)
    for here, there in said.items():
        if said.get(there) != here:
            raise TopologyError(f"{here} lists {there}, but {there} does not list {here}")
    return Topology(nodes, frozenset(Link({a, b}) for a, b in said.items()))


def parse_netfile(text: str) -> Topology:
    """A net file in the ibsim input format, node ids as names."""
    records = _records(text)
    return _topology(records, {r.ident: r.ident for r in records})


@dataclass(frozen=True)
class Discovered:
    """ibnetdiscover output: the topology by NodeDescription, and the LID and LMC of each LID-bearing port."""

    topology: Topology
    lids: dict[Port, tuple[int, int]]


def parse_ibnetdiscover(text: str) -> Discovered:
    records = _records(text)
    names = {r.ident: r.description for r in records}
    if len(set(names.values())) != len(names):
        raise TopologyError("two discovered nodes carry the same NodeDescription")
    lids: dict[Port, tuple[int, int]] = {}
    for r in records:
        if r.kind == SWITCH:
            found = SWITCH_LID.search(r.header_rest)
            if found:
                lids[Port(names[r.ident], 0)] = (int(found.group(1)), int(found.group(2)))
        else:
            for local, _, _, rest in r.lines:
                found = CA_LID.search(rest)
                if found:
                    lids[Port(names[r.ident], local)] = (int(found.group(1)), int(found.group(2)))
    return Discovered(_topology(records, names), lids)


def render_netfile(topology: Topology, header: str = "") -> str:
    lines = [f"# {line}" if line else "#" for line in header.splitlines()]
    for node in topology.nodes:
        if lines:
            lines.append("")
        lines.append(f'{node.kind}\t{node.ports} "{node.name}"')
        for port in range(1, node.ports + 1):
            peer = topology.peer(Port(node.name, port))
            if peer is not None:
                lines.append(f'[{port}]\t"{peer.node}"[{peer.port}]')
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- site model


def hca_name(host: str, hca: str) -> str:
    """NodeDescription of one HCA, host name and device name as the mlx5 driver reports them."""
    return f"{host} {hca}"


def from_fabric(site: Site, fabric_name: str) -> Topology:
    """The ibsim topology of one site fabric: every switch, and one single-port HCA per cabled host interface."""
    fabric: Fabric = site.fabrics[fabric_name]
    switches = [Node(s.name, SWITCH, fabric.switch_ports[s.model]) for s in fabric.switches.values()]
    hcas: dict[str, Node] = {}
    links = set()
    for a, b in fabric.links:
        ends = []
        for end in (a, b):
            if end.node in fabric.switches:
                ends.append(Port(end.node, int(end.port)))
                continue
            host = site.hosts[end.node]
            interface = next(i for i in site.classes[host.node_class].interfaces if i.name == end.port)
            if interface.hca is None:
                raise TopologyError(f"{end}: interface {end.port} of {host.node_class} names no HCA")
            name = hca_name(end.node, interface.hca)
            hcas[name] = Node(name, HCA, 1)
            ends.append(Port(name, 1))
        links.add(Link(ends))
    return Topology(tuple(switches) + tuple(hcas[n] for n in sorted(hcas)), frozenset(links))
