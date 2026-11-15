"""Pure parsers from raw tool output to the values checks and metrics need."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass


class ParseError(ValueError):
    pass


@dataclass(frozen=True)
class Perftest:
    test: str
    device: str | None
    gid_index: int | None
    connection: str | None
    bytes: int
    iterations: int
    bw_peak: float
    bw_average: float
    bw_unit: str
    msg_rate_mpps: float


_RESULT_HEADER = re.compile(
    r"^\s*#bytes\s+#iterations\s+BW peak\[(?P<unit>[^\]]+)\]\s+BW average\[[^\]]+\]\s+MsgRate\[Mpps\]\s*$"
)
_RESULT_ROW = re.compile(r"^\s*(\d+)\s+(\d+)\s+([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s*$")
_FIELD = {
    "device": re.compile(r"Device\s+:\s+(\S+)"),
    "gid_index": re.compile(r"GID index\s+:\s+(\d+)"),
    "connection": re.compile(r"Data ex\. method\s+:\s+(\S+)"),
}
_TITLE = re.compile(r"^\s+(\S.* BW Test)\s*$")


def perftest_bw(text: str) -> Perftest:
    """The result row of an ib_{read,write,send}_bw run, client or server side."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        header = _RESULT_HEADER.match(line)
        if not header:
            continue
        for row_line in lines[i + 1 :]:
            row = _RESULT_ROW.match(row_line)
            if row:
                found = {k: (m.group(1) if (m := p.search(text)) else None) for k, p in _FIELD.items()}
                title = next((m.group(1) for ln in lines if (m := _TITLE.match(ln))), "unknown")
                return Perftest(
                    test=title,
                    device=found["device"],
                    gid_index=int(found["gid_index"]) if found["gid_index"] else None,
                    connection=found["connection"],
                    bytes=int(row.group(1)),
                    iterations=int(row.group(2)),
                    bw_peak=float(row.group(3)),
                    bw_average=float(row.group(4)),
                    bw_unit=header.group("unit"),
                    msg_rate_mpps=float(row.group(5)),
                )
            if row_line.strip() and not row_line.strip().startswith("-"):
                break
    raise ParseError("no perftest result row after a '#bytes #iterations' header")


_RPING_DATA = re.compile(r"^(server )?ping data: rdma-ping-(\d+): ")


def rping_iterations(text: str) -> list[int]:
    """Iteration numbers rping -v printed, in order. -V makes rping exit non-zero on a data mismatch."""
    return [int(m.group(2)) for line in text.splitlines() if (m := _RPING_DATA.match(line))]


def rdma_links(text: str) -> list[dict]:
    """rdma -j link show."""
    links = json.loads(text or "[]")
    if not isinstance(links, list):
        raise ParseError("rdma -j link show did not print a list")
    return links


def cm_listening(text: str, comm: str, port: int) -> bool:
    """True when rdma -j resource show cm_id lists a LISTEN cm_id of comm on port."""
    try:
        entries = json.loads(text or "[]")
    except json.JSONDecodeError:
        return False
    return any(
        e.get("state") == "LISTEN" and e.get("comm") == comm and str(e.get("src-addr", "")).endswith(f":{port}")
        for e in entries
    )


_RECAP = re.compile(r"^(?P<host>\S+)\s+:\s+(?P<counts>(?:\w+=\d+\s*)+)$")


def ansible_recap(text: str) -> dict[str, dict[str, int]]:
    """Per-host counters of the last PLAY RECAP in ansible-playbook output."""
    marker = text.rfind("PLAY RECAP")
    if marker < 0:
        raise ParseError("no PLAY RECAP in the ansible-playbook output")
    recap = {}
    for line in text[marker:].splitlines()[1:]:
        m = _RECAP.match(line.strip())
        if m:
            recap[m.group("host")] = {k: int(v) for k, v in re.findall(r"(\w+)=(\d+)", m.group("counts"))}
    if not recap:
        raise ParseError("PLAY RECAP has no host lines")
    return recap


# Exit codes of GNU timeout: 124 after SIGTERM, 137 when it had to send SIGKILL.
TIMEOUT_EXIT_CODES = {124, 137}
_TIMEOUT_TEXT = re.compile(r"error -110\b|ETIMEDOUT|[Tt]imed? ?out")


def failure_class(rc: int | None, output: str) -> str | None:
    """The manifest failure class of a finished, failed or unfinished process."""
    if rc == 0:
        return None
    if rc is None or rc in TIMEOUT_EXIT_CODES or _TIMEOUT_TEXT.search(output):
        return "timeout"
    return "command-failed"
