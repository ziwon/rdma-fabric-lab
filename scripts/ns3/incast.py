"""Incast cases of Track C: fan-in x profile x seed, each one star network for rfl-star.

All cases of one fan-in share the topology and the flow list. They differ only in the profile's
control features and in the seed, which same_traffic checks on the files the simulator got.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import network

# The keys a profile sets. Everything else of a case comes from the shared incast network.
PROFILE_KEYS = ("pfc", "ecn", "cc")
# Lines of the rendered network file that a profile or the seed may change.
CASE_SETTINGS = ("pfc", "ecn", "cc_mode", "seed")


@dataclass(frozen=True)
class Case:
    fan_in: int
    profile: str
    seed: int
    net: network.Star
    classes: tuple[str, ...]

    @property
    def group(self) -> str:
        return f"n{self.fan_in}"

    @property
    def id(self) -> str:
        return f"n{self.fan_in}-{self.profile}"

    @property
    def step(self) -> str:
        return f"{self.id}-s{self.seed}"


def senders(fan_in: int) -> list[str]:
    return [f"s{i}" for i in range(1, fan_in + 1)]


RECEIVER = "r"


def flows(workload: dict, fan_in: int) -> list[tuple[dict, str]]:
    """The flows of one workload at one fan-in, as scenario flow entries with a class label."""
    kind, pg = workload["kind"], workload["priority"]
    hosts = senders(fan_in)
    if kind == "simultaneous":
        return [({"src": s, "dst": RECEIVER, "payload_bytes": workload["payload_bytes"], "start": "0ns", "priority": pg}, "large")
                for s in hosts]
    if kind == "staggered":
        gap = network.time_ns(workload["gap"])
        return [({"src": s, "dst": RECEIVER, "payload_bytes": workload["payload_bytes"], "start": f"{k * gap}ns", "priority": pg},
                 "large") for k, s in enumerate(hosts)]
    if kind == "mixed":
        large = math.ceil(fan_in / 2)
        out = [({"src": s, "dst": RECEIVER, "payload_bytes": workload["large_bytes"], "start": "0ns", "priority": pg}, "large")
               for s in hosts[:large]]
        out += [({"src": s, "dst": RECEIVER, "payload_bytes": workload["small_bytes"], "start": start, "priority": pg}, "small")
                for s in hosts[large:] for start in workload["small_starts"]]
        return out
    raise network.NetworkError(f"workload kind {kind!r} is not simultaneous, staggered or mixed")


def merged(shared: dict, overrides: dict) -> dict:
    """The shared incast block with a scenario's overrides, one level deep for network."""
    out = {**shared, **{k: v for k, v in overrides.items() if k != "network"}}
    out["network"] = {**shared["network"], **overrides.get("network", {})}
    return out


def cases(shared: dict, workload: dict) -> list[Case]:
    """Every case of a scenario, in a fixed order: fan-in, then profile, then seed."""
    out = []
    for fan_in in shared["fan_in"]:
        entries = flows(workload, fan_in)
        hosts = [*senders(fan_in), RECEIVER]
        for name, profile in shared["profiles"].items():
            if set(profile) != set(PROFILE_KEYS):
                raise network.NetworkError(f"profile {name} must set exactly {', '.join(PROFILE_KEYS)}")
            for seed in shared["seeds"]:
                params = {**shared["network"], **profile, "seed": seed, "run": 1, "hosts": hosts,
                          "links": [{"host": h, **shared["link"]} for h in hosts], "flows": [flow for flow, _ in entries]}
                out.append(Case(fan_in, name, seed, network.star(params), tuple(label for _, label in entries)))
    return out


def setting_lines(text: str) -> dict[str, str]:
    """Each non-comment line of a rendered network file, keyed by its first word, numbered for the keys that repeat."""
    lines: dict[str, str] = {}
    seen: dict[str, int] = {}
    for line in text.splitlines():
        body = line.split("#", 1)[0].strip()
        if not body:
            continue
        key = body.split()[0]
        seen[key] = seen.get(key, 0) + 1
        lines[f"{key}:{seen[key]}" if key in ("host", "switch", "link", "flow") else key] = body
    return lines


def traffic_differences(first: str, second: str) -> list[str]:
    """Keys whose lines differ between two rendered network files, other than the profile and the seed."""
    a, b = setting_lines(first), setting_lines(second)
    return sorted(k for k in set(a) | set(b) if k not in CASE_SETTINGS and a.get(k) != b.get(k))
