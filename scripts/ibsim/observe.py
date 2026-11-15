"""Parsers for what the diagnostic tools and OpenSM print. Each turns raw text into a typed value.

The formats are those of infiniband-diags 50.0 and OpenSM 3.3.23 under ibsim 0.10. The fixtures
under tests/fixtures/b-ibsim/ are captured from those tools.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from .topology import Port

LINK_LINE = re.compile(
    r'^0x[0-9a-f]+\s+"\s*(?P<node>[^"]*)"\s+(?P<lid>\d+)\s+(?P<port>\d+)\[\s*\]\s+==\(\s*'
    r'(?:\S+\s+\S+\s+Gbps\s+)?(?P<state>\w+)/\s*(?P<phys>\w+)\)==>\s+'
    r'(?:0x[0-9a-f]+\s+(?P<rlid>\d+)\s+(?P<rport>\d+))?\[\s*\]\s+"(?P<remote>[^"]*)"'
)
SWITCH_LINE = re.compile(r'^Switch\s*:\s*0x[0-9a-f]+\s+ports\s+\d+\s+"(?P<name>[^"]*)".*\blid (?P<lid>\d+) lmc (?P<lmc>\d+)')
HOST_LINE = re.compile(r'^Ca\s*:\s*0x[0-9a-f]+\s+ports\s+\d+\s+"(?P<name>[^"]*)"')
ROUTE_HEADER = re.compile(r"^Unicast lids \[[^\]]*\] of switch Lid (?P<lid>\d+) guid 0x[0-9a-f]+ \((?P<name>.*)\):$")
ROUTE_LINE = re.compile(r"^0x(?P<lid>[0-9a-f]{4}) (?P<port>\d{3}) :")
SMINFO = re.compile(r"sm lid (?P<lid>\d+) sm guid 0x[0-9a-f]+, activity count \d+ priority (?P<priority>\d+) state \d+ (?P<state>\w+)")
LOG_STAMP = re.compile(r"^(?P<stamp>\w{3} [ \d]\d \d\d:\d\d:\d\d) (?P<usec>\d{6}) ")

ACTIVE = ("Active", "LinkUp")
DOWN = ("Down", "Polling")
SUBNET_UP = "SUBNET UP"


class ParseError(ValueError):
    pass


@dataclass(frozen=True)
class PortState:
    state: str
    phys: str
    remote: Port | None


def link_states(text: str) -> dict[Port, PortState]:
    """iblinkinfo -l: the logical and physical state of every port it lists, and the port at the other end."""
    states: dict[Port, PortState] = {}
    for line in text.splitlines():
        found = LINK_LINE.match(line.strip())
        if not found:
            continue
        remote = Port(found["remote"], int(found["rport"])) if found["rport"] else None
        states[Port(found["node"], int(found["port"]))] = PortState(found["state"], found["phys"], remote)
    if not states:
        raise ParseError("iblinkinfo listed no port")
    return states


def switch_lids(text: str) -> dict[str, tuple[int, int]]:
    """ibswitches: switch name -> (LID, LMC) of its management port."""
    found = {m["name"]: (int(m["lid"]), int(m["lmc"])) for m in map(SWITCH_LINE.match, text.splitlines()) if m}
    if not found:
        raise ParseError("ibswitches listed no switch")
    return found


def host_names(text: str) -> list[str]:
    return [m["name"] for m in map(HOST_LINE.match, text.splitlines()) if m]


def routes(text: str) -> tuple[str, dict[int, int]]:
    """ibroute <lid>: the switch name and its unicast forwarding table, destination LID -> output port."""
    lines = text.splitlines()
    header = next((ROUTE_HEADER.match(line) for line in lines if ROUTE_HEADER.match(line)), None)
    if header is None:
        raise ParseError("ibroute printed no unicast table header")
    table = {int(m["lid"], 16): int(m["port"]) for m in map(ROUTE_LINE.match, lines) if m}
    return header["name"], table


@dataclass(frozen=True)
class SmInfo:
    lid: int
    priority: int
    state: str


def sminfo(text: str) -> SmInfo:
    found = SMINFO.search(text)
    if not found:
        raise ParseError("sminfo printed no SM record")
    return SmInfo(int(found["lid"]), int(found["priority"]), found["state"])


def log_time(line: str, year: int) -> float | None:
    """Seconds since the epoch of one OpenSM log line, read as UTC. OpenSM writes no year."""
    found = LOG_STAMP.match(line)
    if not found:
        return None
    stamp = dt.datetime.strptime(f"{year} {found['stamp']}", "%Y %b %d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
    return stamp.timestamp() + int(found["usec"]) / 1e6


def subnet_up_times(log: str, year: int) -> list[float]:
    return [t for line in log.splitlines() if SUBNET_UP in line and (t := log_time(line, year)) is not None]


def first_log_time(log: str, year: int) -> float | None:
    return next((t for line in log.splitlines() if (t := log_time(line, year)) is not None), None)


def configured_engine(log: str) -> list[str]:
    """Every routing engine the log says configured the switches, in order."""
    return re.findall(r"osm_ucast_mgr_process: (\S+) tables configured on all switches", log)


NODEDESC = re.compile(r"^Node Description:\.*(?P<name>.*)$", re.M)
PROBE_BLOCK = re.compile(r"^== (?P<lid>\d+)\n(?P<body>.*?)^rc=(?P<rc>\d+)$", re.M | re.S)


def nodedesc_answers(text: str) -> dict[int, str | None]:
    """Output of the LID probe loop: LID -> the NodeDescription it answered with, or None when the query failed."""
    answers: dict[int, str | None] = {}
    for block in PROBE_BLOCK.finditer(text):
        found = NODEDESC.search(block["body"])
        answers[int(block["lid"])] = found["name"].strip() if block["rc"] == "0" and found else None
    return answers
