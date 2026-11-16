"""Parsers for what one container run of the Track C image writes: rfl-star's TSV files and GNU time."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Lines of run.tsv that measure the simulator process, not the modeled network.
SIMULATOR_COST_KEYS = ("simulator_wall_ns", "simulator_max_rss_kib")


class ResultError(ValueError):
    pass


def rows(text: str, columns: tuple[str, ...]) -> list[dict[str, str]]:
    """Rows of a TSV whose header must be exactly these columns."""
    lines = text.splitlines()
    if not lines or tuple(lines[0].split("\t")) != columns:
        found = lines[0].split("\t") if lines else []
        raise ResultError(f"header {found} is not {list(columns)}")
    out = []
    for number, line in enumerate(lines[1:], 2):
        cells = line.split("\t")
        if len(cells) != len(columns):
            raise ResultError(f"line {number} has {len(cells)} cells, want {len(columns)}")
        out.append(dict(zip(columns, cells)))
    return out


@dataclass(frozen=True)
class FlowOut:
    flow: int
    src: int
    dst: int
    sport: int
    payload_bytes: int
    start_ns: int
    completed: bool
    finish_ns: int | None
    cnp_acks: int
    rx_in_order_bytes: int

    @property
    def fct_ns(self) -> int | None:
        return None if self.finish_ns is None else self.finish_ns - self.start_ns


@dataclass(frozen=True)
class LinkOut:
    a: int
    b: int
    a_if: int
    b_if: int
    a_bps: int
    b_bps: int
    delay_ns: int


@dataclass
class StarOut:
    """Everything rfl-star wrote for one run, typed."""

    run: dict[str, str]
    flows: list[FlowOut]
    links: list[LinkOut]
    resolved: dict[tuple[str, str], str]
    counters: dict[tuple[int, int, str, str], tuple[int, int]] = field(default_factory=dict)
    events: dict[tuple[int, int, str], int] = field(default_factory=dict)

    @property
    def simulated_ns(self) -> int:
        return int(self.run["simulated_ns"])

    def packets(self, node: int, direction: str, kind: str) -> int:
        return sum(p for (n, _, d, k), (p, _) in self.counters.items() if n == node and d == direction and k == kind)

    def bytes(self, node: int, direction: str, kind: str) -> int:
        return sum(b for (n, _, d, k), (_, b) in self.counters.items() if n == node and d == direction and k == kind)

    def total(self, direction: str, kind: str) -> int:
        return sum(p for (_, _, d, k), (p, _) in self.counters.items() if d == direction and k == kind)

    def event(self, name: str, node: int | None = None) -> int:
        return sum(c for (n, _, e), c in self.events.items() if e == name and (node is None or n == node))


FLOW_COLUMNS = ("flow", "src", "dst", "sport", "dport", "pg", "bytes", "start_ns", "completed", "finish_ns", "cnp_acks",
                "rx_in_order_bytes")
LINK_COLUMNS = ("link", "a", "b", "a_if", "b_if", "a_bps", "b_bps", "delay_ns")
COUNTER_COLUMNS = ("node", "if", "direction", "kind", "packets", "bytes")
EVENT_COLUMNS = ("node", "if", "event", "count")


def parse_star(out: Path) -> StarOut:
    """The files of out/work, as rfl-star writes them."""
    read = lambda name: (out / name).read_text()  # noqa: E731
    run = {r["key"]: r["value"] for r in rows(read("run.tsv"), ("key", "value"))}
    flows = [FlowOut(int(r["flow"]), int(r["src"]), int(r["dst"]), int(r["sport"]), int(r["bytes"]), int(r["start_ns"]),
                     r["completed"] == "1", int(r["finish_ns"]) if r["finish_ns"] else None, int(r["cnp_acks"]),
                     int(r["rx_in_order_bytes"]))
             for r in rows(read("flows.tsv"), FLOW_COLUMNS)]
    links = [LinkOut(int(r["a"]), int(r["b"]), int(r["a_if"]), int(r["b_if"]), int(r["a_bps"]), int(r["b_bps"]), int(r["delay_ns"]))
             for r in rows(read("links.tsv"), LINK_COLUMNS)]
    resolved = {(r["object"], r["attribute"]): r["value"] for r in rows(read("resolved.tsv"), ("object", "attribute", "value"))}
    counters = {(int(r["node"]), int(r["if"]), r["direction"], r["kind"]): (int(r["packets"]), int(r["bytes"]))
                for r in rows(read("counters.tsv"), COUNTER_COLUMNS)}
    events = {(int(r["node"]), int(r["if"]), r["event"]): int(r["count"]) for r in rows(read("events.tsv"), EVENT_COLUMNS)}
    for flow in flows:
        if flow.completed != (flow.finish_ns is not None):
            raise ResultError(f"flow {flow.flow}: completed={flow.completed} but finish_ns={flow.finish_ns}")
        if flow.finish_ns is not None and flow.finish_ns < flow.start_ns:
            raise ResultError(f"flow {flow.flow} finishes at {flow.finish_ns} ns, before its start {flow.start_ns} ns")
    return StarOut(run, flows, links, resolved, counters, events)


def modeled_figures(work: Path) -> dict[str, str]:
    """The files that hold modeled figures, with run.tsv cut to the simulator's own time.

    Wall time and RSS of the simulator process vary between equal runs and say nothing about the model.
    """
    figures = {name: (work / name).read_text() for name in ("flows.tsv", "links.tsv", "resolved.tsv", "counters.tsv", "events.tsv")}
    run = (work / "run.tsv").read_text().splitlines()
    figures["run.tsv"] = "\n".join(line for line in run if line.split("\t")[0] not in SIMULATOR_COST_KEYS) + "\n"
    return figures


TIME_FIELDS = {
    "Elapsed (wall clock) time (h:mm:ss or m:ss)": "wall",
    "Maximum resident set size (kbytes)": "max_rss_kib",
    "Exit status": "exit_status",
}


def gnu_time(text: str) -> dict[str, float]:
    """Wall seconds, max RSS in KiB and exit status from /usr/bin/time -v."""
    found: dict[str, float] = {}
    for line in text.splitlines():
        key, sep, value = line.strip().rpartition(": ")
        if not sep or key not in TIME_FIELDS:
            continue
        name = TIME_FIELDS[key]
        if name == "wall":
            parts = [float(p) for p in value.split(":")]
            found["wall_s"] = sum(p * 60 ** i for i, p in enumerate(reversed(parts)))
        else:
            found[name] = float(value)
    if "wall_s" not in found or "max_rss_kib" not in found:
        raise ResultError("GNU time output lacks the wall clock or the maximum resident set size")
    return found


MMU_DROP = re.compile(r"^\d+ \d+ Drop: queue:", re.MULTILINE)


def mmu_drop_lines(stdout: str) -> int:
    """SwitchMmu::CheckIngressAdmission prints one such line for each packet it refuses."""
    return len(MMU_DROP.findall(stdout))


def last_number_after(text: str, prefix: str) -> float | None:
    """The number that follows the last occurrence of prefix, as in "total time,389404.030000"."""
    at = text.rfind(prefix)
    if at < 0:
        return None
    match = re.match(r"\s*([0-9]+(?:\.[0-9]+)?)", text[at + len(prefix):])
    return float(match.group(1)) if match else None
