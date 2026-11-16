"""Pure checks of Track C: each takes what a run declared and what the model reported, never a guest."""

from __future__ import annotations

from dataclasses import dataclass

from .network import ACK_BYTES, DATA_HEADER_BYTES, ECN_THRESHOLD_UNIT_BYTES, Star, fct_bound, packet_payloads
from .results import StarOut

CONTAINER_USER = "10001:10001"
WRITABLE_MOUNT = "/run/rfl/out"


@dataclass(frozen=True)
class Verdict:
    ok: bool
    detail: str


def verdict(problems: list[str], success: str) -> Verdict:
    return Verdict(not problems, "; ".join(problems) if problems else success)


def _expect(problems: list[str], what: str, found: object, want: object) -> None:
    if found != want:
        problems.append(f"{what} is {found!r}, want {want!r}")


def config_applied(net: Star, out: StarOut) -> Verdict:
    """The model objects hold every declared value, and the sender's wire bytes follow from MTU and headers."""
    problems: list[str] = []
    by_host = {link.a: link for link in out.links}
    for declared in net.links:
        node = net.node(declared.host)
        found = by_host.get(node)
        if found is None:
            problems.append(f"no link of {declared.host} (node {node}) in links.tsv")
            continue
        _expect(problems, f"{declared.host} link rate at the host end, bit/s", found.a_bps, declared.bps)
        _expect(problems, f"{declared.host} link rate at the switch end, bit/s", found.b_bps, declared.bps)
        _expect(problems, f"{declared.host} channel delay, ns", found.delay_ns, declared.delay_ns)
    for node, name in enumerate(net.hosts):
        hw = f"node/{node}/rdma-hw"
        for attribute, want in (("Mtu", net.mtu_bytes), ("CcMode", net.cc_mode), ("L2ChunkSize", net.l2_chunk_bytes),
                                ("L2AckInterval", net.l2_ack_interval)):
            _expect(problems, f"{name} RdmaHw {attribute}", out.resolved.get((hw, attribute)), str(want))
    for (where, attribute), value in sorted(out.resolved.items()):
        if "/dev/" in where and attribute == "QbbEnabled":
            _expect(problems, f"{where} QbbEnabled", value, str(net.pfc).lower())
        if "/dev/" in where and attribute == "PauseTime":
            _expect(problems, f"{where} PauseTime", value, str(net.pause_time_us))
    switch = f"node/{net.switch}"
    _expect(problems, "switch EcnEnabled", out.resolved.get((f"{switch}/switch", "EcnEnabled")), str(net.ecn).lower())
    for port in range(1, len(net.links) + 1):
        mmu = f"{switch}/mmu/{port}"
        _expect(problems, f"{mmu} kmin_bytes", out.resolved.get((mmu, "kmin_bytes")), str(net.kmin_kb * ECN_THRESHOLD_UNIT_BYTES))
        _expect(problems, f"{mmu} kmax_bytes", out.resolved.get((mmu, "kmax_bytes")), str(net.kmax_kb * ECN_THRESHOLD_UNIT_BYTES))
    _expect(problems, "switch buffer_bytes", out.resolved.get((f"{switch}/mmu", "buffer_bytes")), str(net.buffer_bytes))
    _expect(problems, "RngSeed", out.resolved.get(("global", "RngSeed")), str(net.seed))
    _expect(problems, "RngRun", out.resolved.get(("global", "RngRun")), str(net.run))
    for name in net.hosts:
        node = net.node(name)
        payloads = [p for f in net.flows if f.src == name for p in packet_payloads(f.payload_bytes, net.mtu_bytes)]
        _expect(problems, f"{name} data packets sent", out.packets(node, "tx", "data"), len(payloads))
        _expect(problems, f"{name} data bytes on the wire", out.bytes(node, "tx", "data"), sum(payloads) + DATA_HEADER_BYTES * len(payloads))
        acks = out.packets(node, "rx", "ack")
        _expect(problems, f"{name} ACK bytes received", out.bytes(node, "rx", "ack"), ACK_BYTES * acks)
    return verdict(problems, (
        f"links.tsv and resolved.tsv hold every declared rate, delay, MTU {net.mtu_bytes} B, CcMode {net.cc_mode}, "
        f"QbbEnabled {str(net.pfc).lower()}, EcnEnabled {str(net.ecn).lower()}, ECN thresholds in bytes, buffer and seed "
        f"{net.seed}; each sender put payload + {DATA_HEADER_BYTES} B per data packet on the wire and got {ACK_BYTES} B ACKs"))


def flow_completes(net: Star, out: StarOut) -> Verdict:
    problems = []
    if len(out.flows) != len(net.flows):
        problems.append(f"flows.tsv lists {len(out.flows)} flows, the scenario has {len(net.flows)}")
    for declared, found in zip(net.flows, out.flows):
        if not found.completed:
            problems.append(f"flow {found.flow} {declared.src}->{declared.dst} did not complete; the simulator stopped at "
                            f"{out.simulated_ns} ns")
        elif found.rx_in_order_bytes != declared.payload_bytes:
            problems.append(f"flow {found.flow}: the receiver holds {found.rx_in_order_bytes} B in order, want {declared.payload_bytes}")
    if out.run.get("stopped_by") != "last-flow-completed":
        problems.append(f"the simulator stopped by {out.run.get('stopped_by')}")
    return verdict(problems, f"all {len(out.flows)} flow(s) completed: the QpComplete trace fired and the receiver holds every "
                             f"byte in order; the simulator stopped at {out.simulated_ns} ns after the last completion")


def fct_lower_bound(net: Star, out: StarOut) -> tuple[Verdict, list[tuple[str, float | None, str]]]:
    """FCT >= 8 x payload / bottleneck + propagation, with the excess explained. Also returns its figures."""
    problems, notes, figures = [], [], []
    for declared, found in zip(net.flows, out.flows):
        bound = fct_bound(net, declared)
        fct = found.fct_ns
        label = f"flow {found.flow}"
        figures += [("bound_ns", bound.bound_ns, label), ("fct_ns", fct, label), ("expected_ns", bound.expected_ns, label)]
        if fct is None:
            problems.append(f"{label} has no completion time")
            continue
        if fct < bound.bound_ns:
            problems.append(f"{label} FCT {fct} ns is below the bound {bound.bound_ns:.1f} ns "
                            f"(8 x {declared.payload_bytes} B at the bottleneck + {bound.propagation_ns} ns propagation)")
            continue
        excess = fct - bound.bound_ns
        notes.append(
            f"{label} FCT {fct} ns is {excess:.1f} ns ({100 * excess / bound.bound_ns:.2f}%) above the bound {bound.bound_ns:.1f} ns "
            f"= {bound.payload_serialization_ns:.1f} ns payload serialization + {bound.propagation_ns} ns propagation. "
            f"The model's mechanics give {bound.expected_ns} ns: {bound.header_ns} ns for {DATA_HEADER_BYTES} B of headers per "
            f"packet and integer-ns transmit times, {bound.store_and_forward_ns} ns to forward the last packet after it fully "
            f"arrived at the switch, {bound.ack_return_ns} ns for the {ACK_BYTES} B ACK to return. Unexplained: "
            f"{fct - bound.expected_ns} ns")
    return verdict(problems, " ".join(notes)), figures


def drop_figures(net: Star, out: StarOut, mmu_drops: int) -> dict[str, int]:
    """Every place the model can lose a data packet, as the trace sources and counters saw it."""
    lost_on_links = 0
    for link in out.links:
        lost_on_links += out_packets(out, link.a, link.a_if, "tx") - out_packets(out, link.b, link.b_if, "rx")
        lost_on_links += out_packets(out, link.b, link.b_if, "tx") - out_packets(out, link.a, link.a_if, "rx")
    switch_rx, switch_tx = out.packets(net.switch, "rx", "data"), out.packets(net.switch, "tx", "data")
    sent = sum(out.packets(net.node(h), "tx", "data") for h in net.hosts)
    wanted = sum(len(packet_payloads(f.payload_bytes, net.mtu_bytes)) for f in net.flows)
    return {
        "data packets lost on links": lost_on_links,
        "data packets the switch received and did not send": switch_rx - switch_tx,
        "PhyRxDrop events": out.event("phy_rx_drop"),
        "QbbDrop events": out.event("qbb_drop"),
        "MMU drop lines on stdout": mmu_drops,
        "NACKs sent": out.total("tx", "nack"),
        "data packets sent beyond one per segment": sent - wanted,
    }


def out_packets(out: StarOut, node: int, interface: int, direction: str) -> int:
    return out.counters.get((node, interface, direction, "data"), (0, 0))[0]


def zero_drops(net: Star, out: StarOut, mmu_drops: int) -> Verdict:
    figures = drop_figures(net, out, mmu_drops)
    problems = [f"{name}: {value}" for name, value in figures.items() if value != 0]
    return verdict(problems, "no drop anywhere: " + ", ".join(f"{name} {value}" for name, value in figures.items()))


def no_pfc_pause(net: Star, out: StarOut) -> Verdict:
    figures = {"PFC frames sent": out.total("tx", "pfc"), "PAUSE received (QbbPfc 1)": out.event("pause_rx"),
               "RESUME received (QbbPfc 0)": out.event("resume_rx")}
    problems = [f"{name}: {value}" for name, value in figures.items() if value != 0]
    ports = sorted({v for (where, a), v in out.resolved.items() if "/dev/" in where and a == "QbbEnabled"})
    return verdict(problems, f"QbbEnabled reads {'/'.join(ports) or 'nothing'} on every port and the model recorded no pause: "
                   + ", ".join(f"{name} {value}" for name, value in figures.items()))


def no_ecn_mark(net: Star, out: StarOut) -> Verdict:
    hosts = range(len(net.hosts))
    figures = {"CE-marked data packets the switch sent": out.event("ce_data_tx", net.switch),
               "CE-marked data packets hosts received": sum(out.event("ce_data_rx", h) for h in hosts),
               "ACKs with the CNP flag hosts received": sum(out.event("cnp_flag_ack_rx", h) for h in hosts),
               "CNP packets sent": out.total("tx", "cnp"),
               "CNPs RdmaHw counted per QP": sum(f.cnp_acks for f in out.flows)}
    problems = [f"{name}: {value}" for name, value in figures.items() if value != 0]
    ecn = out.resolved.get((f"node/{net.switch}/switch", "EcnEnabled"), "nothing")
    return verdict(problems, f"the switch's EcnEnabled reads {ecn} and the model marked nothing: "
                   + ", ".join(f"{name} {value}" for name, value in figures.items()))


def same_contents(first: dict[str, str], second: dict[str, str], what: str) -> Verdict:
    """Equal names and equal contents. A difference names the file and its first differing line."""
    problems = []
    for name in sorted(set(first) | set(second)):
        if name not in first or name not in second:
            problems.append(f"{name} exists in only one run")
            continue
        if first[name] != second[name]:
            a, b = first[name].splitlines(), second[name].splitlines()
            line = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
            problems.append(f"{name} differs at line {line + 1}: {a[line] if line < len(a) else '(end)'!r} vs "
                            f"{b[line] if line < len(b) else '(end)'!r}")
    return verdict(problems, f"the two runs gave the same {len(first)} {what}: {', '.join(sorted(first))}")


# Field of docker inspect -> (what it shows, the value a locked-down Track C container has).
LOCKED_DOWN = {
    ("HostConfig", "Privileged"): ("not privileged", False),
    ("HostConfig", "NetworkMode"): ("no network", "none"),
    ("HostConfig", "Devices"): ("no device node", []),
    ("HostConfig", "CapDrop"): ("every capability dropped", ["ALL"]),
    ("HostConfig", "ReadonlyRootfs"): ("read-only root file system", True),
    ("HostConfig", "PidMode"): ("own PID namespace", ""),
    ("HostConfig", "IpcMode"): ("own IPC namespace", "private"),
    ("Config", "User"): ("non-root user", CONTAINER_USER),
    ("AppArmorProfile",): ("AppArmor docker-default", "docker-default"),
}


def container_problems(inspect: dict, image: str, identity: dict[str, str]) -> list[str]:
    problems = []
    for path, (meaning, want) in LOCKED_DOWN.items():
        value = inspect
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        if value != want:
            problems.append(f"{'.'.join(path)} is {value!r}, want {want!r} ({meaning})")
    host = inspect.get("HostConfig", {})
    if host.get("CapAdd"):
        problems.append(f"HostConfig.CapAdd is {host['CapAdd']!r}")
    if "no-new-privileges" not in (host.get("SecurityOpt") or []):
        problems.append("no-new-privileges is not set")
    if inspect.get("Image") != image:
        problems.append(f"the container ran image {inspect.get('Image')}, not the locked {image}")
    writable = [m.get("Destination") for m in inspect.get("Mounts", []) if m.get("RW")]
    if writable != [WRITABLE_MOUNT]:
        problems.append(f"writable mounts are {writable}, want only {WRITABLE_MOUNT}")
    uid, gid = CONTAINER_USER.split(":")
    if (identity.get("uid"), identity.get("gid")) != (uid, gid):
        problems.append(f"the entry point ran as uid {identity.get('uid')} gid {identity.get('gid')}, want {uid}:{gid}")
    return problems


# Executables of the simulator. ps prints at most 15 characters of a command name.
SIMULATOR_PROGRAMS = ("SimAI_simulator", "ns3.36.1-AstraSimNetwork-debug", "ns3.36.1-rfl-star-debug", "rfl-star")


def host_simulator_processes(ps_text: str) -> list[str]:
    """Lines of ps -eo pid=,comm=,args= whose command name is one of the simulator programs."""
    names = {name[:15] for name in SIMULATOR_PROGRAMS}
    found = []
    for line in ps_text.splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[1] in names:
            found.append(line.strip())
    return found
