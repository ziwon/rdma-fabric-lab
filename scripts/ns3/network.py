"""The star network of a Track C scenario: human units in, the rfl-star network file out.

Every simulator input is an integer in the unit the model consumes: bit/s, ns, bytes. The scenario
file states rates and delays with their unit, and sizes in bytes only, so KB and KiB never mix.
The comments on each constant name the code at the pinned ns-3-alibabacloud commit that fixes it.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace

RATE_UNITS = {"bps": 1, "Kbps": 10**3, "Mbps": 10**6, "Gbps": 10**9}
TIME_UNITS = {"ns": 1, "us": 10**3, "ms": 10**6, "s": 10**9}
QUANTITY = re.compile(r"^([0-9]+)([A-Za-z]+)$")

# This fork's PppHeader writes 14 bytes, the protocol and 12 zero bytes, the size of an Ethernet header
# (ppp-header.cc PppHeader::Serialize).
L2_HEADER_BYTES = 14
# GetNxtPacket in rdma-hw.cc adds SimpleSeqTsHeader (u64 seq + u16 pg + IntHeader::GetStaticSize(),
# 0 in mode NONE), UDP 8, IPv4 20 and the L2 header to every payload.
DATA_HEADER_BYTES = 10 + 8 + 20 + L2_HEADER_BYTES
# ReceiveUdp in rdma-hw.cc pads the payload to max(60 - 14 - 20 - 16, 0) = 10 bytes, then adds
# qbbHeader 16 (u16 sport, dport, flags, pg and u64 seq), IPv4 20 and the L2 header.
ACK_BYTES = 10 + 16 + 20 + L2_HEADER_BYTES
# SwitchMmu::ConfigEcn in switch-mmu.cc multiplies kmin and kmax by 1000.
ECN_THRESHOLD_UNIT_BYTES = 1000
# The model's CcMode values in use. 1 is the Mellanox DCQCN of RdmaHw::cnp_received_mlx. RdmaHw and
# SwitchNode compare CcMode only with 1, 3, 7, 8 and 10, so 0 runs no congestion control at all.
CC_MODES = {"dcqcn": 1, "none": 0}


class NetworkError(ValueError):
    pass


def quantity(text: str, units: dict[str, int], what: str) -> int:
    """An integer count of the smallest unit, from text like 100Gbps or 1us. No fractions."""
    match = QUANTITY.match(str(text))
    if not match or match.group(2) not in units:
        raise NetworkError(f"{what} {text!r} needs an integer and one of {', '.join(units)}")
    return int(match.group(1)) * units[match.group(2)]


def rate_bps(text: str) -> int:
    return quantity(text, RATE_UNITS, "rate")


def time_ns(text: str) -> int:
    return quantity(text, TIME_UNITS, "time")


@dataclass(frozen=True)
class Link:
    host: str
    bps: int
    delay_ns: int


@dataclass(frozen=True)
class Flow:
    src: str
    dst: str
    payload_bytes: int
    start_ns: int
    priority: int


@dataclass(frozen=True)
class Dcqcn:
    """The RdmaHw attributes of the Mellanox DCQCN, in the units the model takes."""

    rate_ai_bps: int
    rate_hai_bps: int
    min_rate_bps: int
    ewma_gain: float
    rp_timer_us: int
    rate_decrease_interval_us: int
    alpha_resume_interval_us: int
    fast_recovery_times: int
    clamp_target_rate: bool
    rate_on_first_cnp: float


def whole_us(text: str, what: str) -> int:
    ns = time_ns(text)
    if ns % 1000:
        raise NetworkError(f"{what} must be whole microseconds, the unit RdmaHw schedules it in")
    return ns // 1000


def dcqcn(params: dict) -> Dcqcn:
    return Dcqcn(
        rate_ai_bps=rate_bps(params["rate_ai"]), rate_hai_bps=rate_bps(params["rate_hai"]),
        min_rate_bps=rate_bps(params["min_rate"]), ewma_gain=float(params["ewma_gain"]),
        rp_timer_us=whole_us(params["rp_timer"], "rp_timer"),
        rate_decrease_interval_us=whole_us(params["rate_decrease_interval"], "rate_decrease_interval"),
        alpha_resume_interval_us=whole_us(params["alpha_resume_interval"], "alpha_resume_interval"),
        fast_recovery_times=int(params["fast_recovery_times"]), clamp_target_rate=bool(params["clamp_target_rate"]),
        rate_on_first_cnp=float(params["rate_on_first_cnp"]),
    )


@dataclass(frozen=True)
class Star:
    """Hosts around one switch, in the simulator's units. Host i is node i, the switch is the last node."""

    hosts: tuple[str, ...]
    links: tuple[Link, ...]
    flows: tuple[Flow, ...]
    seed: int
    run: int
    stop_ns: int
    mtu_bytes: int
    cc_mode: int
    pfc: bool
    pause_time_us: int
    ecn: bool
    kmin_kb: int
    kmax_kb: int
    pmax: float
    buffer_bytes: int
    headroom_factor: int
    pfc_alpha_shift: int
    l2_chunk_bytes: int
    l2_ack_interval: int
    window_bytes: int
    dcqcn: Dcqcn
    sample_ns: int
    error_rate: float = 0.0

    def node(self, host: str) -> int:
        return self.hosts.index(host)

    def link(self, host: str) -> Link:
        return next(link for link in self.links if link.host == host)

    @property
    def switch(self) -> int:
        return len(self.hosts)


def ecn_threshold_kb(byte_count: int, what: str) -> int:
    if byte_count % ECN_THRESHOLD_UNIT_BYTES:
        raise NetworkError(f"{what} {byte_count} B is not a multiple of {ECN_THRESHOLD_UNIT_BYTES} B, the unit SwitchMmu::ConfigEcn takes")
    return byte_count // ECN_THRESHOLD_UNIT_BYTES


def star(params: dict) -> Star:
    """Parse the scenario parameters. Every key is required and no other key is allowed."""
    hosts = tuple(params["hosts"])
    if len(set(hosts)) != len(hosts) or len(hosts) < 2:
        raise NetworkError(f"hosts {hosts} must be at least two distinct names")
    links = tuple(Link(item["host"], rate_bps(item["rate"]), time_ns(item["delay"])) for item in params["links"])
    if sorted(link.host for link in links) != sorted(hosts):
        raise NetworkError("every host needs exactly one link to the switch")
    flows = tuple(Flow(f["src"], f["dst"], int(f["payload_bytes"]), time_ns(f["start"]), int(f["priority"]))
                  for f in params["flows"])
    for f in flows:
        if f.src not in hosts or f.dst not in hosts or f.src == f.dst or f.payload_bytes <= 0 or not 1 <= f.priority <= 7:
            raise NetworkError(f"flow {f} needs two different hosts, a positive size and a priority 1 to 7")
    if params["cc"] not in CC_MODES:
        raise NetworkError(f"cc {params['cc']!r} is not one of {', '.join(CC_MODES)}")
    pause_ns = time_ns(params["pfc_pause_time"])
    if pause_ns % 1000:
        raise NetworkError("pfc_pause_time must be whole microseconds, the unit of QbbNetDevice::PauseTime")
    return Star(
        hosts=hosts, links=links, flows=flows, seed=int(params["seed"]), run=int(params["run"]),
        stop_ns=time_ns(params["stop_after"]), mtu_bytes=int(params["mtu_bytes"]), cc_mode=CC_MODES[params["cc"]],
        pfc=bool(params["pfc"]), pause_time_us=pause_ns // 1000, ecn=bool(params["ecn"]),
        kmin_kb=ecn_threshold_kb(int(params["ecn_kmin_bytes"]), "ecn_kmin_bytes"),
        kmax_kb=ecn_threshold_kb(int(params["ecn_kmax_bytes"]), "ecn_kmax_bytes"),
        pmax=float(params["ecn_pmax"]), buffer_bytes=int(params["buffer_bytes"]),
        headroom_factor=int(params["pfc_headroom_bdp_factor"]), pfc_alpha_shift=int(params["pfc_alpha_shift"]),
        l2_chunk_bytes=int(params["l2_chunk_bytes"]), l2_ack_interval=int(params["l2_ack_interval"]),
        window_bytes=int(params["window_bytes"]), dcqcn=dcqcn(params["dcqcn"]), sample_ns=time_ns(params["sample_interval"]),
    )


def render(net: Star, header: str) -> str:
    """The rfl-star network file. Order and spelling are fixed, so equal networks give equal bytes."""
    lines = [f"# {line}" for line in header.splitlines()]
    settings = {
        "seed": net.seed, "run": net.run, "stop_ns": net.stop_ns, "mtu_bytes": net.mtu_bytes, "cc_mode": net.cc_mode,
        "pfc": int(net.pfc), "ecn": int(net.ecn), "pause_time_us": net.pause_time_us, "buffer_bytes": net.buffer_bytes,
        "kmin_kb": net.kmin_kb, "kmax_kb": net.kmax_kb, "pmax": repr(net.pmax), "headroom_factor": net.headroom_factor,
        "pfc_alpha_shift": net.pfc_alpha_shift, "l2_chunk_bytes": net.l2_chunk_bytes,
        "l2_ack_interval": net.l2_ack_interval, "window_bytes": net.window_bytes, "error_rate": repr(net.error_rate),
        "rate_ai_bps": net.dcqcn.rate_ai_bps, "rate_hai_bps": net.dcqcn.rate_hai_bps, "min_rate_bps": net.dcqcn.min_rate_bps,
        "ewma_gain": repr(net.dcqcn.ewma_gain), "rp_timer_us": net.dcqcn.rp_timer_us,
        "rate_decrease_interval_us": net.dcqcn.rate_decrease_interval_us,
        "alpha_resume_interval_us": net.dcqcn.alpha_resume_interval_us, "fast_recovery_times": net.dcqcn.fast_recovery_times,
        "clamp_target_rate": int(net.dcqcn.clamp_target_rate), "rate_on_first_cnp": repr(net.dcqcn.rate_on_first_cnp),
        "sample_ns": net.sample_ns,
    }
    lines += [f"{key} {value}" for key, value in settings.items()]
    lines += [f"host {i}  # {name}" for i, name in enumerate(net.hosts)]
    lines.append(f"switch {net.switch}")
    lines += [f"link {net.node(link.host)} {net.switch} {link.bps} {link.delay_ns}" for link in net.links]
    lines += [f"flow {net.node(f.src)} {net.node(f.dst)} {f.payload_bytes} {f.start_ns} {f.priority}" for f in net.flows]
    return "\n".join(lines) + "\n"


def with_link_rate(net: Star, host: str, factor: float) -> Star:
    links = tuple(replace(link, bps=int(link.bps * factor)) if link.host == host else link for link in net.links)
    return replace(net, links=links)


def tx_ns(wire_bytes: int, bps: int) -> int:
    """DataRate::CalculateBytesTxTime: Seconds(bytes * 8) / m_bps, an integer division of nanoseconds."""
    return wire_bytes * 8 * 10**9 // bps


def packet_payloads(payload_bytes: int, mtu_bytes: int) -> list[int]:
    full, rest = divmod(payload_bytes, mtu_bytes)
    return [mtu_bytes] * full + ([rest] if rest else [])


@dataclass(frozen=True)
class FctBound:
    """The payload lower bound of one flow and how the model's mechanics add to it."""

    bound_ns: float
    payload_serialization_ns: float
    propagation_ns: int
    expected_ns: int
    header_ns: int
    store_and_forward_ns: int
    ack_return_ns: int


def fct_bound(net: Star, flow: Flow) -> FctBound:
    """8 x payload / bottleneck plus the one-way propagation delay, and the model's expected FCT.

    The expected FCT holds for one flow alone on its path: the sender sends back to back at its line
    rate, the switch forwards each packet once it has fully arrived, and the last ACK returns with
    nothing ahead of it. The QP completes when that ACK reaches the sender (RdmaHw::ReceiveAck).
    """
    up, down = net.link(flow.src), net.link(flow.dst)
    bottleneck = min(up.bps, down.bps)
    payload_ns = 8 * flow.payload_bytes * 10**9 / bottleneck
    propagation = up.delay_ns + down.delay_ns
    payloads = packet_payloads(flow.payload_bytes, net.mtu_bytes)
    sender_done = sum(tx_ns(p + DATA_HEADER_BYTES, up.bps) for p in payloads)
    last_hop = tx_ns(payloads[-1] + DATA_HEADER_BYTES, down.bps)
    ack = tx_ns(ACK_BYTES, down.bps) + tx_ns(ACK_BYTES, up.bps) + propagation
    expected = sender_done + propagation + last_hop + ack
    return FctBound(
        bound_ns=payload_ns + propagation, payload_serialization_ns=payload_ns, propagation_ns=propagation,
        expected_ns=expected, header_ns=sender_done - math.floor(8 * flow.payload_bytes * 10**9 / up.bps),
        store_and_forward_ns=last_hop, ack_return_ns=ack,
    )
