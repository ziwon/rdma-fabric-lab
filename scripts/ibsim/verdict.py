"""Pure checks of one observed fabric state against the topology it should have.

Every function takes parsed observations and an expected Topology and returns a Verdict. The
expected topology of a phase is the topology file, minus a failed link, reduced to what the
subnet manager can reach.
"""

from __future__ import annotations

from dataclasses import dataclass

from .observe import ACTIVE, DOWN, PortState, SmInfo
from .topology import HCA, SWITCH, Discovered, Port, Topology

DETAIL_ITEMS = 6


@dataclass(frozen=True)
class Verdict:
    ok: bool
    detail: str


def _listed(items: list[str]) -> str:
    shown = sorted(items)[:DETAIL_ITEMS]
    more = len(items) - len(shown)
    return ", ".join(shown) + (f" and {more} more" if more > 0 else "")


def _from_problems(problems: list[str], good: str) -> Verdict:
    if problems:
        return Verdict(False, f"{len(problems)} problems: {_listed(problems)}")
    return Verdict(True, good)


def link_name(link: frozenset) -> str:
    a, b = sorted(link)
    return f"{a}-{b}"


def topology_matches(expected: Topology, found: Topology) -> Verdict:
    """The discovered nodes, with kind and port count, and the discovered links equal the expected ones."""
    want_nodes, got_nodes = set(expected.nodes), set(found.nodes)
    problems = [f"missing node {n.kind} {n.name} ({n.ports} ports)" for n in want_nodes - got_nodes]
    problems += [f"extra node {n.kind} {n.name} ({n.ports} ports)" for n in got_nodes - want_nodes]
    problems += [f"missing link {link_name(link)}" for link in expected.links - found.links]
    problems += [f"extra link {link_name(link)}" for link in found.links - expected.links]
    counts = (f"{len(expected.of_kind(SWITCH))} switches, {len(expected.of_kind(HCA))} HCAs and "
              f"{len(expected.links)} links, as expected")
    return _from_problems(problems, counts)


def endpoint_ports(topology: Topology) -> list[Port]:
    """HCA ports that have a link. Each must carry a LID once the subnet is up."""
    return [Port(n.name, p) for n in topology.of_kind(HCA) for p in range(1, n.ports + 1) if topology.peer(Port(n.name, p))]


def lids_unique(expected: Topology, found: Discovered) -> Verdict:
    """Every linked HCA port has a non-zero LID with LMC 0, and no two ports share a LID."""
    problems = []
    for port in endpoint_ports(expected):
        lid = found.lids.get(port)
        if lid is None:
            problems.append(f"{port} has no LID")
        elif lid[0] == 0 or lid[1] != 0:
            problems.append(f"{port} has LID {lid[0]} LMC {lid[1]}")
    owners: dict[int, list[str]] = {}
    for port, (lid, _) in found.lids.items():
        owners.setdefault(lid, []).append(str(port))
    problems += [f"LID {lid} is held by {' and '.join(sorted(ports))}" for lid, ports in owners.items() if len(ports) > 1]
    return _from_problems(problems, f"{len(endpoint_ports(expected))} endpoint LIDs, non-zero, unique, LMC 0")


def port_states(expected: Topology, states: dict[Port, PortState]) -> Verdict:
    """Linked ports are Active/LinkUp and face their expected peer. Unlinked switch ports are Down/Polling."""
    problems, checked = [], 0
    for node in expected.nodes:
        for number in range(1, node.ports + 1):
            port = Port(node.name, number)
            peer = expected.peer(port)
            seen = states.get(port)
            if peer is None and node.kind == HCA:
                continue
            checked += 1
            if seen is None:
                problems.append(f"{port} is not listed")
            elif peer is None and (seen.state, seen.phys) != DOWN:
                problems.append(f"{port} has no link but is {seen.state}/{seen.phys}")
            elif peer is not None and ((seen.state, seen.phys) != ACTIVE or seen.remote != peer):
                problems.append(f"{port} is {seen.state}/{seen.phys} to {seen.remote}, want Active/LinkUp to {peer}")
    known = {n.name for n in expected.nodes}
    problems += [f"{port} of a node outside the expected fabric is listed" for port in states if port.node not in known]
    active = sum(1 for p in states.values() if (p.state, p.phys) == ACTIVE)
    return _from_problems(problems, f"{checked} ports as expected, {active} of them Active/LinkUp")


def lid_owners(found: Discovered) -> dict[int, Port]:
    return {lid: port for port, (lid, _) in found.lids.items()}


def routes_shortest(expected: Topology, tables: dict[str, dict[int, int]], found: Discovered) -> Verdict:
    """Every switch forwards every endpoint LID out of a port one hop closer to it, and keeps no LID nobody holds.

    Routes to switch LIDs are left out: up/down routing gives one spine no route to another spine's
    LID, since that path would turn down and then up.
    """
    owners = lid_owners(found)
    endpoints = {lid: port for lid, port in owners.items() if expected.node(port.node).kind == HCA}
    hops = {port.node: expected.hops_from(port.node) for port in endpoints.values()}
    problems, checked = [], 0
    for switch in expected.of_kind(SWITCH):
        table = tables.get(switch.name)
        if table is None:
            problems.append(f"no forwarding table of {switch.name}")
            continue
        problems += [f"{switch.name} routes LID {lid}, which no port holds" for lid in sorted(set(table) - set(owners))]
        for lid, port in sorted(endpoints.items()):
            checked += 1
            out = table.get(lid)
            distance = hops[port.node].get(switch.name)
            if out is None:
                problems.append(f"{switch.name} has no route to LID {lid} {port}")
                continue
            peer = expected.peer(Port(switch.name, out))
            if peer is None or distance is None or hops[port.node].get(peer.node) != distance - 1:
                problems.append(f"{switch.name} sends LID {lid} {port} out of port {out}, not toward it")
    return _from_problems(problems, f"{checked} switch routes to endpoint LIDs, each one hop closer to its endpoint")


def routes_through(tables: dict[str, dict[int, int]], ports: list[Port]) -> set[tuple[str, int]]:
    """(switch, LID) of every route that leaves through one of these switch ports."""
    on = {(p.node, p.port) for p in ports}
    return {(switch, lid) for switch, table in tables.items() for lid, out in table.items() if (switch, out) in on}


def rerouted(before: dict[str, dict[int, int]], after: dict[str, dict[int, int]], failed: list[Port],
             found_after: Discovered) -> Verdict:
    """The failed link carried routes before the fault, and each of them now leaves by another port or is gone with its LID."""
    used = routes_through(before, failed)
    if not used:
        return Verdict(False, f"no route used {' or '.join(map(str, failed))} before the fault, so the fault tested nothing")
    held = set(lid_owners(found_after))
    on = {(p.node, p.port) for p in failed}
    problems, moved, dropped = [], 0, 0
    for switch, lid in sorted(used):
        out = after.get(switch, {}).get(lid)
        if lid not in held:
            dropped += out is None
            if out is not None:
                problems.append(f"{switch} still routes LID {lid}, whose port is gone")
        elif out is None or (switch, out) in on:
            problems.append(f"{switch} LID {lid} now {'has no route' if out is None else 'still uses the failed port'}")
        else:
            moved += 1
    return _from_problems(problems, f"{len(used)} routes used the failed link: {moved} moved to another port, "
                                    f"{dropped} removed with their LID")


def subnet_manager(info: SmInfo, sm_port: Port, found: Discovered, engines: list[str], engine: str, ups: int) -> Verdict:
    """OpenSM routed with the configured engine, logged SUBNET UP, and is master at the SM host's LID."""
    problems = []
    lid = found.lids.get(sm_port, (None, None))[0]
    if info.state != "SMINFO_MASTER" or info.lid != lid:
        problems.append(f"sminfo reports {info.state} at LID {info.lid}, want SMINFO_MASTER at {sm_port} LID {lid}")
    if not engines or set(engines) != {engine}:
        problems.append(f"the log names routing engines {engines or 'none'}, want only {engine}")
    if ups < 1:
        problems.append("the log has no SUBNET UP")
    return _from_problems(problems, f"master at LID {lid}, {ups} SUBNET UP, every sweep routed by {engine}")


def reachability(answers: dict[int, str | None], want: dict[int, str], silent: set[int]) -> Verdict:
    """Each LID in want answers a LID-routed NodeDescription query with that name. Each LID in silent does not answer."""
    problems = [f"LID {lid} answered {answers.get(lid)!r}, want {name!r}" for lid, name in sorted(want.items())
                if answers.get(lid) != name]
    problems += [f"LID {lid} still answers as {answers[lid]!r}" for lid in sorted(silent) if answers.get(lid) is not None]
    good = f"{len(want)} LIDs answer with their node name"
    return _from_problems(problems, good + (f", {len(silent)} cut-off LIDs stay silent" if silent else ""))
