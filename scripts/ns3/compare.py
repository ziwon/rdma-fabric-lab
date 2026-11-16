"""Figures of one incast case and the comparison of the four profiles, derived only from rfl-star output.

Every FCT figure travels with the number of flows it covers, the completion ratio and the flows that
did not finish. A median or a maximum of a few flows is reported as that, never as a tail statistic.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from .incast import Case
from .network import packet_payloads
from .results import Series, StarOut


@dataclass(frozen=True)
class CaseOut:
    case: Case
    out: StarOut
    series: Series


def bottleneck_port(c: CaseOut) -> int:
    """The switch interface toward the receiver, whose egress queue the incast fills."""
    receiver = c.case.net.node(c.case.net.flows[0].dst)
    return next(link.b_if for link in c.out.links if link.a == receiver)


def pause_intervals(c: CaseOut) -> list[tuple[int, int, int, int, int | None]]:
    """(node, if, prio, start ns, end ns or None) for each PAUSE a host received, closed by the next RESUME.

    With PFC off the host ignores these frames, so the intervals are pauses the switch asked for, not
    pauses that happened. figures() counts pause time only for hosts with PFC on.

    The model's host keeps a priority paused until a RESUME arrives. It never expires a PAUSE by its
    pause time (QbbNetDevice::Receive), so the RESUME is the end of the interval.
    """
    hosts = range(len(c.case.net.hosts))
    open_at: dict[tuple[int, int, int], int] = {}
    out = []
    for t, node, interface, direction, prio, pause_time in c.series.pfcs:
        if direction != "rx" or node not in hosts:
            continue
        key = (node, interface, prio)
        if pause_time > 0 and key not in open_at:
            open_at[key] = t
        elif pause_time == 0 and key in open_at:
            out.append((*key, open_at.pop(key), t))
    out += [(*key, start, None) for key, start in open_at.items()]
    return sorted(out, key=lambda row: (row[3], row[0]))


def rate_reductions(c: CaseOut) -> dict[int, int]:
    """Per flow, how often its sampled rate fell below the previous sample."""
    last: dict[int, int] = {}
    count: dict[int, int] = {}
    for _, flow, bps in c.series.rates:
        if flow in last and bps < last[flow]:
            count[flow] = count.get(flow, 0) + 1
        last[flow] = bps
    return count


def figures(c: CaseOut) -> dict[str, float | int | None]:
    net, out = c.case.net, c.out
    done = [f for f in out.flows if f.completed]
    fcts = {label: [f.fct_ns for f, cls in zip(out.flows, c.case.classes) if f.completed and cls == label]
            for label in ("large", "small")}
    hosts = range(len(net.hosts))
    finish = max((f.finish_ns for f in done), default=None)
    start = min(f.start_ns for f in out.flows)
    goodput = None
    if done and finish > start:
        goodput = 8e9 * sum(f.payload_bytes for f in done) / (finish - start)
    sent = sum(p for p, _, _, _ in c.series.packets.values())
    segments = sum(len(packet_payloads(f.payload_bytes, net.mtu_bytes)) for f in net.flows)
    port = bottleneck_port(c)
    intervals = pause_intervals(c)
    end = out.simulated_ns
    switch_rx = out.packets(net.switch, "rx", "data")
    return {
        "flows": len(out.flows), "completed": len(done), "completion_ratio": len(done) / len(out.flows),
        "large_fct": fcts["large"], "small_fct": fcts["small"],
        "goodput_bps": goodput, "makespan_ns": None if finish is None else finish - start,
        "peak_queue_bytes": max((b for (node, interface, _), (b, _) in c.series.peaks.items()
                                 if node == net.switch and interface == port), default=0),
        "pause_frames_sent": out.total("tx", "pfc"),
        "pauses_received": sum(1 for _, node, _, d, _, t in c.series.pfcs if d == "rx" and node in hosts and t > 0),
        "pauses_handled": out.event("pause_rx"), "resumes_handled": out.event("resume_rx"),
        "pause_ns": sum((e if e is not None else end) - s for _, _, _, s, e in intervals) if net.pfc else 0,
        "mid_band_mark_fraction": mid_band_fraction(c),
        "marks": len(c.series.marks), "cnps": len(c.series.cnps),
        "rate_reductions": sum(rate_reductions(c).values()),
        "min_rate_bps": min((bps for _, _, bps in c.series.rates), default=None),
        "mmu_drops_trace": switch_rx - out.event("data_enqueued"),
        "mmu_drops_stdout": c.series.stdout.get("mmu_drop_lines", 0),
        "retransmitted_packets": sent - segments, "nacks": sum(n for _, _, n, _ in c.series.packets.values()),
        "simulated_ns": end,
    }


def mid_band_fraction(c: CaseOut) -> float | None:
    """Marked share of the bottleneck's data dequeues while its egress queue sat between kmin and kmax."""
    port = bottleneck_port(c)
    band = [v for (node, interface, _, name), v in c.series.bands.items()
            if node == c.case.net.switch and interface == port and name == "kmin_to_kmax"]
    dequeued = sum(d for d, _ in band)
    return sum(m for _, m in band) / dequeued if dequeued else None


def spread(values: list[float]) -> str:
    """min, median, max and the sample count, or 'none' for no sample."""
    if not values:
        return "none (n=0)"
    return f"{min(values):g}/{statistics.median(values):g}/{max(values):g} (n={len(values)})"


SUMMARY_COLUMNS = ("group", "profile", "seeds", "flows_per_seed", "completion_ratio_min", "unfinished_flows",
                   "large_fct_ns", "small_fct_ns", "goodput_gbps", "peak_queue_bytes_max", "pauses_received",
                   "pauses_handled", "pause_us", "marks", "mid_band_mark_fraction", "cnps", "rate_reductions",
                   "mmu_drops", "retransmitted_packets", "distinct_outcomes")


def summary(cases: list[CaseOut]) -> list[dict[str, str]]:
    """One row per fan-in and profile over its seeds. Counts give min/median/max with the sample size."""
    groups: dict[tuple[int, str], list[CaseOut]] = {}
    for c in cases:
        groups.setdefault((c.case.fan_in, c.case.profile), []).append(c)
    rows = []
    for (fan_in, profile), members in groups.items():
        f = [figures(c) for c in members]
        unfinished = [f"s{c.case.seed}:flow{o.flow}" for c in members for o in c.out.flows if not o.completed]
        outcomes = {tuple((o.completed, o.finish_ns) for o in c.out.flows) for c in members}
        rows.append({
            "group": f"n{fan_in}", "profile": profile, "seeds": str(len(members)),
            "flows_per_seed": str(f[0]["flows"]),
            "completion_ratio_min": f"{min(x['completion_ratio'] for x in f):.3f}",
            "unfinished_flows": ",".join(unfinished) or "none",
            "large_fct_ns": spread([v for x in f for v in x["large_fct"]]),
            "small_fct_ns": spread([v for x in f for v in x["small_fct"]]),
            "goodput_gbps": spread([x["goodput_bps"] / 1e9 for x in f if x["goodput_bps"] is not None]),
            "peak_queue_bytes_max": str(max(x["peak_queue_bytes"] for x in f)),
            "pauses_received": spread([x["pauses_received"] for x in f]),
            "pauses_handled": spread([x["pauses_handled"] for x in f]),
            "pause_us": spread([x["pause_ns"] / 1000 for x in f]),
            "marks": spread([x["marks"] for x in f]),
            "mid_band_mark_fraction": spread([round(x["mid_band_mark_fraction"], 4) for x in f
                                              if x["mid_band_mark_fraction"] is not None]),
            "cnps": spread([x["cnps"] for x in f]),
            "rate_reductions": spread([x["rate_reductions"] for x in f]),
            "mmu_drops": spread([x["mmu_drops_trace"] for x in f]),
            "retransmitted_packets": spread([x["retransmitted_packets"] for x in f]),
            "distinct_outcomes": str(len(outcomes)),
        })
    return rows


def summary_tsv(rows: list[dict[str, str]]) -> str:
    return "\t".join(SUMMARY_COLUMNS) + "\n" + "".join("\t".join(row[c] for c in SUMMARY_COLUMNS) + "\n" for row in rows)


def pause_intervals_tsv(cases: list[CaseOut]) -> str:
    rows = ["step\tnode\tif\tprio\tstart_ns\tend_ns\thost_acted"]
    for c in cases:
        rows += [f"{c.case.step}\t{node}\t{interface}\t{prio}\t{start}\t{'' if end is None else end}\t{int(c.case.net.pfc)}"
                 for node, interface, prio, start, end in pause_intervals(c)]
    return "\n".join(rows) + "\n"
