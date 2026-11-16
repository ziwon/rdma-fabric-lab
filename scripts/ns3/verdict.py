"""Pure checks of Track C: each takes what a run declared and what the model reported, never a guest."""

from __future__ import annotations

from dataclasses import dataclass

from .compare import CaseOut, bottleneck_port, figures
from .incast import Case, traffic_differences
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


# RdmaHw attribute -> (Dcqcn field, parser of the serialized value). DataRate serializes as "<n>bps".
DCQCN_ATTRIBUTES = {
    "RateAI": ("rate_ai_bps", lambda v: int(v.removesuffix("bps"))),
    "RateHAI": ("rate_hai_bps", lambda v: int(v.removesuffix("bps"))),
    "MinRate": ("min_rate_bps", lambda v: int(v.removesuffix("bps"))),
    "EwmaGain": ("ewma_gain", float),
    "RPTimer": ("rp_timer_us", float),
    "RateDecreaseInterval": ("rate_decrease_interval_us", float),
    "AlphaResumInterval": ("alpha_resume_interval_us", float),
    "FastRecoveryTimes": ("fast_recovery_times", int),
    "ClampTargetRate": ("clamp_target_rate", lambda v: v == "true"),
    "RateOnFirstCnp": ("rate_on_first_cnp", float),
}


def applied_settings(net: Star, out: StarOut) -> list[str]:
    """Every declared value that the model objects do not hold, read back from links.tsv and resolved.tsv."""
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
        pmax = out.resolved.get((mmu, "pmax"))
        _expect(problems, f"{mmu} pmax", float(pmax) if pmax is not None else None, net.pmax)
    _expect(problems, "switch buffer_bytes", out.resolved.get((f"{switch}/mmu", "buffer_bytes")), str(net.buffer_bytes))
    _expect(problems, "RngSeed", out.resolved.get(("global", "RngSeed")), str(net.seed))
    _expect(problems, "RngRun", out.resolved.get(("global", "RngRun")), str(net.run))
    for node, name in enumerate(net.hosts):
        for attribute, (field, parse) in DCQCN_ATTRIBUTES.items():
            value = out.resolved.get((f"node/{node}/rdma-hw", attribute))
            _expect(problems, f"{name} RdmaHw {attribute}", parse(value) if value is not None else None, getattr(net.dcqcn, field))
    return problems


def config_applied(net: Star, out: StarOut) -> Verdict:
    """The model objects hold every declared value, and the sender's wire bytes follow from MTU and headers."""
    problems = applied_settings(net, out)
    for name in net.hosts:
        node = net.node(name)
        payloads = [p for f in net.flows if f.src == name for p in packet_payloads(f.payload_bytes, net.mtu_bytes)]
        _expect(problems, f"{name} data packets sent", out.packets(node, "tx", "data"), len(payloads))
        _expect(problems, f"{name} data bytes on the wire", out.bytes(node, "tx", "data"), sum(payloads) + DATA_HEADER_BYTES * len(payloads))
        acks = out.packets(node, "rx", "ack")
        _expect(problems, f"{name} ACK bytes received", out.bytes(node, "rx", "ack"), ACK_BYTES * acks)
    return verdict(problems, (
        f"links.tsv and resolved.tsv hold every declared rate, delay, MTU {net.mtu_bytes} B, CcMode {net.cc_mode}, "
        f"QbbEnabled {str(net.pfc).lower()}, EcnEnabled {str(net.ecn).lower()}, ECN thresholds in bytes, buffer, seed "
        f"{net.seed} and the ten DCQCN attributes; each sender put payload + {DATA_HEADER_BYTES} B per data packet on the wire "
        f"and got {ACK_BYTES} B ACKs"))


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


# ---------------------------------------------------------------- incast


def by_case(problems: list[tuple[str, str]], limit: int = 12) -> str:
    """The first problems, each prefixed by its case step, and how many more there were."""
    text = "; ".join(f"{step}: {problem}" for step, problem in problems[:limit])
    return text + (f"; and {len(problems) - limit} more" if len(problems) > limit else "")


def incast_config_applied(cases: list[CaseOut]) -> Verdict:
    problems = [(c.case.step, p) for c in cases for p in applied_settings(c.case.net, c.out)]
    profiles = sorted({c.case.profile for c in cases})
    return verdict([by_case(problems)] if problems else [], (
        f"all {len(cases)} cases hold their declared rates, delays, MTU, buffer, ECN thresholds and pmax, seed, the profile's "
        f"QbbEnabled, EcnEnabled and CcMode, and the ten DCQCN attributes, read back from the model objects; profiles "
        f"{', '.join(profiles)}"))


def same_traffic(cases: list[Case], files: dict[str, str]) -> Verdict:
    """Within a fan-in, the network files the simulator got differ only in the profile keys and the seed."""
    problems = []
    groups: dict[str, list[Case]] = {}
    for case in cases:
        groups.setdefault(case.group, []).append(case)
    for group, members in groups.items():
        first = members[0]
        for case in members[1:]:
            diff = traffic_differences(files[first.step], files[case.step])
            if diff:
                problems.append((case.step, f"differs from {first.step} in {', '.join(diff)}"))
        for case in members:
            lines = {line.split()[0]: line.split()[1] for line in files[case.step].splitlines()
                     if line and not line.startswith("#") and len(line.split()) >= 2}
            want = {"pfc": str(int(case.net.pfc)), "ecn": str(int(case.net.ecn)), "cc_mode": str(case.net.cc_mode)}
            wrong = [f"{k} {lines.get(k)} want {v}" for k, v in want.items() if lines.get(k) != v]
            if wrong:
                problems.append((case.step, ", ".join(wrong)))
    return verdict([by_case(problems)] if problems else [], (
        f"in each of {len(groups)} fan-in groups every case's network file has the same topology, links, flows, buffer, "
        f"thresholds and DCQCN lines as the first; only pfc, ecn, cc_mode and seed differ, as each profile defines"))


def flows_accounted(cases: list[CaseOut]) -> Verdict:
    """Every declared flow is in flows.tsv, finished or not, and every refused packet is counted two ways."""
    problems, unfinished, total = [], [], 0
    for c in cases:
        net, out = c.case.net, c.out
        total += len(out.flows)
        if len(out.flows) != len(net.flows):
            problems.append((c.case.step, f"flows.tsv lists {len(out.flows)} flows, the case has {len(net.flows)}"))
        for declared, found in zip(net.flows, out.flows):
            if found.completed and found.rx_in_order_bytes != declared.payload_bytes:
                problems.append((c.case.step, f"flow {found.flow} completed with {found.rx_in_order_bytes} B in order, want "
                                              f"{declared.payload_bytes}"))
            if not found.completed:
                unfinished.append(f"{c.case.step}:flow{found.flow}")
        f = figures(c)
        if f["mmu_drops_trace"] != f["mmu_drops_stdout"]:
            problems.append((c.case.step, f"the switch refused {f['mmu_drops_trace']} data packets by its traces but the MMU "
                                          f"printed {f['mmu_drops_stdout']} drop lines"))
    return verdict([by_case(problems)] if problems else [], (
        f"{total} flows in {len(cases)} cases are all in flows.tsv; {total - len(unfinished)} completed with every byte "
        f"in order and {len(unfinished)} did not finish ({', '.join(unfinished[:8]) or 'none'}"
        f"{', ...' if len(unfinished) > 8 else ''}); in every case the switch's refusals counted from MacRx minus "
        f"QbbEnqueue equal the MMU's drop lines"))


def fct_bounds(cases: list[CaseOut]) -> Verdict:
    """No completed flow beats its own serialization bound, and no case moves its payload faster than the receiver link."""
    problems, flows, tightest = [], 0, None
    for c in cases:
        net = c.case.net
        for declared, found in zip(net.flows, c.out.flows):
            if not found.completed:
                continue
            flows += 1
            bound = fct_bound(net, declared).bound_ns
            ratio = found.fct_ns / bound
            tightest = ratio if tightest is None else min(tightest, ratio)
            if found.fct_ns < bound:
                problems.append((c.case.step, f"flow {found.flow} FCT {found.fct_ns} ns below its bound {bound:.0f} ns"))
        done = [f for f in c.out.flows if f.completed]
        if done:
            receiver = net.link(net.flows[0].dst)
            need = 8e9 * sum(f.payload_bytes for f in done) / receiver.bps
            span = max(f.finish_ns for f in done) - min(f.start_ns for f in done)
            if span < need:
                problems.append((c.case.step, f"the completed flows took {span} ns, below the {need:.0f} ns the receiver link "
                                              f"needs for their payload"))
    return verdict([by_case(problems)] if problems else [], (
        f"all {flows} completed flows took at least 8 x payload / bottleneck + propagation (the closest at "
        f"{tightest:.3f} x its bound), and every case took at least as long as the receiver link needs for the completed "
        f"payload" if tightest is not None else "no flow completed"))


def host_pauses(c: CaseOut) -> tuple[int, int, int, int]:
    """PAUSE and RESUME frames hosts received, and how often the host's pause handler ran for each."""
    hosts = range(len(c.case.net.hosts))
    frames = [(t, pt) for t, node, _, d, _, pt in c.series.pfcs if d == "rx" and node in hosts]
    return (sum(1 for _, pt in frames if pt > 0), sum(1 for _, pt in frames if pt == 0),
            c.out.event("pause_rx"), c.out.event("resume_rx"))


def pfc_as_configured(cases: list[CaseOut]) -> Verdict:
    """PFC on: hosts act on every PAUSE and RESUME and the switch drops nothing. PFC off: hosts never act."""
    problems = []
    on_paused = off_received = 0
    for c in cases:
        pauses, resumes, handled_p, handled_r = host_pauses(c)
        drops = figures(c)["mmu_drops_trace"]
        if c.case.net.pfc:
            if (handled_p, handled_r) != (pauses, resumes):
                problems.append((c.case.step, f"hosts got {pauses} PAUSE and {resumes} RESUME but the handler ran "
                                              f"{handled_p} and {handled_r} times"))
            if drops:
                problems.append((c.case.step, f"PFC is on and the switch refused {drops} data packets"))
            on_paused += handled_p > 0
        else:
            if handled_p or handled_r:
                problems.append((c.case.step, f"PFC is off and the host pause handler ran {handled_p}+{handled_r} times"))
            off_received += pauses > 0
    if not on_paused:
        problems.append(("scenario", "no PFC-on case paused, so the check saw PFC do nothing"))
    if not off_received:
        problems.append(("scenario", "no PFC-off case received a PAUSE, so the check never saw the hosts ignore one"))
    on = sum(c.case.net.pfc for c in cases)
    return verdict([by_case(problems)] if problems else [], (
        f"in the {on} PFC-on cases the host pause handler ran once for every PAUSE and RESUME received and the switch refused "
        f"no packet; {on_paused} of them paused. In the {len(cases) - on} PFC-off cases the handler never ran, although the "
        f"switch still sent PAUSE and hosts received it in {off_received} cases"))


def ecn_as_configured(cases: list[CaseOut]) -> Verdict:
    """ECN off: no mark, no CNP, no rate change. ECN on: marks only above kmin, always above kmax, then CNP and a rate cut."""
    problems = []
    reacted = 0
    for c in cases:
        f = figures(c)
        above = [(d, m) for (node, _, _, band), (d, m) in c.series.bands.items()
                 if node == c.case.net.switch and band == "above_kmax"]
        low = [m for (node, _, _, band), (_, m) in c.series.bands.items() if node == c.case.net.switch and band == "at_most_kmin"]
        if not c.case.net.ecn:
            seen = {"marks": f["marks"], "CNP-flagged ACKs": f["cnps"], "rate reductions": f["rate_reductions"]}
            bad = [f"{k} {v}" for k, v in seen.items() if v]
            if bad:
                problems.append((c.case.step, "ECN is off but " + ", ".join(bad)))
            continue
        if sum(low):
            problems.append((c.case.step, f"{sum(low)} marks at or below kmin"))
        unmarked = sum(d - m for d, m in above)
        if unmarked:
            problems.append((c.case.step, f"{unmarked} dequeues above kmax went unmarked"))
        if f["marks"] and not f["cnps"]:
            problems.append((c.case.step, f"{f['marks']} marks but no CNP-flagged ACK reached a sender"))
        reacted += f["marks"] > 0 and f["cnps"] > 0 and f["rate_reductions"] > 0
    if not reacted:
        problems.append(("scenario", "no ECN-on case marked, fed back a CNP and cut a rate"))
    on = sum(c.case.net.ecn for c in cases)
    return verdict([by_case(problems)] if problems else [], (
        f"in the {len(cases) - on} ECN-off cases the switch marked nothing, no CNP flag came back and no rate changed; in "
        f"the {on} ECN-on cases every mark fell above kmin, every dequeue above kmax was marked, and {reacted} cases showed "
        f"marks, CNP-flagged ACKs and DCQCN rate cuts"))


PFC_PAIRS = (("c-none", "c-pfc"), ("c-ecn", "c-both"))
ECN_PAIRS = (("c-none", "c-ecn"), ("c-pfc", "c-both"))


def outputs(c: CaseOut) -> tuple:
    return (tuple((f.completed, f.finish_ns) for f in c.out.flows), tuple(sorted(c.out.counters.items())))


def profiles_differ(cases: list[CaseOut]) -> Verdict:
    """For the same traffic and seed, profiles that differ in a triggered feature give different results.

    PFC is triggered when the switch sent a PAUSE in either case of the pair. ECN is triggered when the
    bottleneck's egress queue rose above kmin in either case.
    """
    index = {(c.case.group, c.case.seed, c.case.profile): c for c in cases}
    problems, triggered, quiet = [], {"pfc": 0, "ecn": 0}, []
    for (group, seed, profile), c in sorted(index.items()):
        for feature, pairs in (("pfc", PFC_PAIRS), ("ecn", ECN_PAIRS)):
            for off, on in pairs:
                if profile != off or (group, seed, on) not in index:
                    continue
                a, b = c, index[(group, seed, on)]
                if feature == "pfc":
                    fired = a.out.total("tx", "pfc") > 0 or b.out.total("tx", "pfc") > 0
                else:
                    fired = any(d for x in (a, b) for (node, interface, _, band), (d, _) in x.series.bands.items()
                                if node == x.case.net.switch and interface == bottleneck_port(x) and band != "at_most_kmin")
                if not fired:
                    quiet.append(f"{group}-s{seed} {off}/{on}")
                    continue
                triggered[feature] += 1
                if outputs(a) == outputs(b):
                    problems.append((f"{group}-s{seed}", f"{off} and {on} differ in {feature}, which was triggered, but gave the "
                                                          f"same flows and counters"))
    for feature, count in triggered.items():
        if not count:
            problems.append(("scenario", f"no pair differing in {feature} saw its trigger"))
    return verdict([by_case(problems)] if problems else [], (
        f"{triggered['pfc']} pairs differing in PFC and {triggered['ecn']} pairs differing in ECN and DCQCN saw their trigger "
        f"and each gave different flow results and counters; {len(quiet)} pairs saw no trigger and may match "
        f"({', '.join(quiet[:6]) or 'none'}{', ...' if len(quiet) > 6 else ''})"))


def incast_rerun_identical(first: dict[str, dict[str, str]], second: dict[str, dict[str, str]]) -> Verdict:
    problems = []
    for step in sorted(set(first) | set(second)):
        if step not in first or step not in second:
            problems.append((step, "ran in only one of the two runs"))
            continue
        same = same_contents(first[step], second[step], "files")
        if not same.ok:
            problems.append((step, same.detail))
    files = sum(len(v) for v in first.values())
    return verdict([by_case(problems, 4)] if problems else [], (
        f"run2 repeated all {len(first)} cases with every seed and gave the same {files} modeled figure files"))
