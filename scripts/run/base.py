"""What every Track A runner shares: the run directory, check records, and the summary."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import rfl
from .remote import CommandLog


@dataclass
class Outcome:
    status: str
    failure_class: str | None = None
    exit_code: int | None = None
    evidence: list[str] = field(default_factory=list)
    detail: str | None = None


def passed(evidence: list[str], detail: str, exit_code: int | None = 0) -> Outcome:
    return Outcome("passed", None, exit_code, evidence, detail)


def failed(failure_class: str, evidence: list[str], detail: str, exit_code: int | None = None) -> Outcome:
    return Outcome("failed", failure_class, exit_code, evidence, detail)


class RunBase:
    """A run started from a scenario spec. Subclasses add the guests and their parameters."""

    def __init__(self, spec: Path, run_id: str | None, deliberate_break: dict | None = None):
        self.dir = rfl.init(spec, run_id, deliberate_break)
        self.run_id = self.dir.parts[-3]
        self.log = CommandLog(self.dir / "commands.log")
        self.started = time.monotonic()
        self.metrics: list[dict] = []
        self.recorded: dict[str, Outcome] = {}
        self.remote: dict = {}
        self.metrics_note = "Software RDMA measurement of this host and guest pair. No pass threshold applies."

    def say(self, message: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {message}"
        print(line, flush=True)
        with (self.dir / "stdout.log").open("a") as handle:
            handle.write(line + "\n")

    def warn(self, message: str) -> None:
        print(message, file=sys.stderr, flush=True)
        with (self.dir / "stderr.log").open("a") as handle:
            handle.write(message + "\n")

    def raw(self, rel: str, content: str | bytes) -> str:
        path = self.dir / "raw" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)
        return f"raw/{rel}"

    def record(self, check_id: str, outcome: Outcome) -> None:
        evidence = [e for e in outcome.evidence if (self.dir / e).is_file() and (self.dir / e).stat().st_size > 0]
        rfl.check(
            self.dir,
            check_id,
            outcome.status,
            exit_code=outcome.exit_code,
            failure_class=outcome.failure_class,
            evidence=evidence,
            detail=outcome.detail,
        )
        self.recorded[check_id] = outcome
        self.say(f"{check_id}: {outcome.status}{' (' + outcome.failure_class + ')' if outcome.failure_class else ''}"
                 f"{' - ' + outcome.detail if outcome.detail else ''}")


def check_leftovers(run: RunBase) -> Outcome:
    lines, totals, remaining_all = [], [], []
    for name, remote in run.remote.items():
        killed = remote.kill_run()
        remaining = remote.run_processes()
        lines += [f"{name} stopped {pid} {cmd}" for pid, cmd in killed]
        lines += [f"{name} remains {pid} {cmd}" for pid, cmd in remaining]
        totals.append(f"{name}: {len(killed)} stopped by the runner, {len(remaining)} remaining")
        remaining_all += [f"{name} {pid}" for pid, _ in remaining]
    evidence = [run.raw("no-process-left/processes.txt", "\n".join(lines + totals) + "\n")]
    if remaining_all:
        return failed("assertion-failed", evidence, f"still running: {', '.join(remaining_all)}")
    return passed(evidence, "; ".join(totals))


def write_summary(run: RunBase) -> None:
    manifest = json.loads((run.dir / "manifest.json").read_text())
    lines = [
        f"# {manifest['track']} / {manifest['scenario']} / {manifest['run_id']}",
        "",
        f"Status: **{manifest['status']}**. {manifest['description']}",
        "",
        "| check | status | failure class | detail |",
        "|---|---|---|---|",
    ]
    for c in manifest["checks"]:
        detail = (c["detail"] or "").replace("|", "\\|")
        lines.append(f"| {c['id']} | {c['status']} | {c['failure_class'] or ''} | {detail} |")
    if run.metrics:
        lines += ["", run.metrics_note, "",
                  "| metric | value | unit | absent reason |", "|---|---|---|---|"]
        lines += [f"| {m['name']} | {m['value'] if m['value'] is not None else ''} | {m['unit']} | {m['absent_reason'] or ''} |"
                  for m in run.metrics]
    env = manifest["environment"]
    lines += ["", f"Host: {env['host'].get('os')} {env['host'].get('kernel')}."]
    lines += [f"Guest {g['name']}: {g['os']} {g['kernel']}." for g in env.get("guests", [])]
    (run.dir / "summary.md").write_text("\n".join(lines) + "\n")
