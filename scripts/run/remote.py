"""Run-scoped processes on the lab guests.

Every process a run starts carries RFL_RUN=<run-id> in its environment and writes
<name>.pid, .out, .err and .rc under /var/tmp/rfl/<run-id>/ on the guest. Cleanup
finds a run's processes by that marker alone, so it never touches anything else.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .inventory import Guest

REMOTE_ROOT = "/var/tmp/rfl"
RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")
KILL_AFTER_S = 5


def guest_run_dir(run_id: str, root: str = REMOTE_ROOT) -> str:
    if not RUN_ID.match(run_id):
        raise ValueError(f"{run_id!r} is not a run id like 20261115T010203Z")
    return f"{root}/{run_id}"


def launch_command(run_id: str, name: str, command: str, timeout_s: int, sudo: bool = False, root: str = REMOTE_ROOT) -> str:
    """Shell line that starts command in the background, bounded by timeout, and returns at once."""
    d = guest_run_dir(run_id, root)
    inner = (
        f"echo $$ > {name}.pid; timeout -k {KILL_AFTER_S} {timeout_s} {command} > {name}.out 2> {name}.err; "
        f"echo $? > {name}.rc.tmp; mv {name}.rc.tmp {name}.rc"
    )
    starter = f"{'sudo ' if sudo else ''}env RFL_RUN={run_id} setsid sh -c {shlex.quote(inner)}"
    # Only the starter goes to the background. Backgrounding the whole list would keep the
    # SSH session's stdout open in a subshell until the process ends.
    return f"mkdir -p {d} && cd {d} && rm -f {name}.rc && {{ {starter} < /dev/null > /dev/null 2>&1 & }}"


def find_run_processes_command(run_id: str) -> str:
    """Prints "pid cmdline" for every process whose environment holds RFL_RUN=<run-id>."""
    guest_run_dir(run_id)
    return (
        "for p in /proc/[0-9]*; do "
        f"if tr '\\0' '\\n' 2>/dev/null < $p/environ | grep -qxF RFL_RUN={run_id}; then "
        "echo \"${p#/proc/} $(tr '\\0' ' ' < $p/cmdline)\"; fi; done"
    )


def parse_process_list(text: str) -> list[tuple[int, str]]:
    found = []
    for line in text.splitlines():
        pid, _, cmdline = line.strip().partition(" ")
        if pid.isdigit():
            found.append((int(pid), cmdline.strip()))
    return found


@dataclass(frozen=True)
class Done:
    """What one guest command returned."""

    rc: int
    stdout: str
    stderr: str


class CommandLog:
    """commands.log of a run: one line per command, with its host, exit code and duration."""

    def __init__(self, path: Path | None):
        self.path = path

    def write(self, where: str, command: str, rc: int | str, seconds: float) -> None:
        if self.path is None:
            return
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self.path.open("a") as handle:
            handle.write(f"{stamp} {where} rc={rc} {seconds:.1f}s $ {command}\n")


class Remote:
    def __init__(self, guest: Guest, run_id: str, log: CommandLog):
        self.guest = guest
        self.run_id = run_id
        self.dir = guest_run_dir(run_id)
        self.log = log

    def sh(self, command: str, timeout_s: float = 60, binary: bool = False) -> Done:
        start = time.monotonic()
        try:
            out = subprocess.run(
                [*self.guest.ssh, command], capture_output=True, timeout=timeout_s, stdin=subprocess.DEVNULL
            )
        except subprocess.TimeoutExpired:
            self.log.write(self.guest.name, command, "timeout", time.monotonic() - start)
            return Done(124, "", f"ssh command exceeded {timeout_s} s")
        self.log.write(self.guest.name, command, out.returncode, time.monotonic() - start)
        decode = (lambda b: b) if binary else (lambda b: b.decode(errors="replace"))
        return Done(out.returncode, decode(out.stdout), out.stderr.decode(errors="replace"))

    def start(self, name: str, command: str, timeout_s: int, sudo: bool = False) -> Done:
        return self.sh(launch_command(self.run_id, name, command, timeout_s, sudo))

    def rc(self, name: str) -> int | None:
        done = self.sh(f"cat {self.dir}/{name}.rc 2>/dev/null")
        text = done.stdout.strip()
        return int(text) if done.rc == 0 and text.lstrip("-").isdigit() else None

    def wait(self, name: str, timeout_s: float, poll_s: float = 0.5) -> int | None:
        """Exit code of a started process, or None when it is still running at the deadline."""
        deadline = time.monotonic() + timeout_s
        while True:
            rc = self.rc(name)
            if rc is not None or time.monotonic() >= deadline:
                return rc
            time.sleep(poll_s)

    def read(self, filename: str, binary: bool = False) -> str | bytes:
        done = self.sh(f"cat {self.dir}/{filename}", binary=binary)
        return done.stdout if done.rc == 0 else (b"" if binary else "")

    def where(self) -> dict:
        """Where this executor runs a process, for the run's raw logs."""
        g = self.guest
        return {"node": g.name, "pod": None, "rdma_device": getattr(g, "rxe_device", None),
                "lab_address": getattr(g, "rdma_ip", None), "runs_as": "the SSH user of the node"}

    def stop_session(self, name: str) -> None:
        """Stop every process in the session that <name> started."""
        self.sh(f"sid=$(cat {self.dir}/{name}.pid 2>/dev/null) && [ -n \"$sid\" ] && "
                "{ sudo pkill -TERM -s \"$sid\"; sleep 1; sudo pkill -KILL -s \"$sid\"; }; true")

    def signal(self, name: str, signal: str, process: str) -> Done:
        """Send signal to the named process inside the session that <name> started."""
        return self.sh(f"sudo pkill -{signal} -s \"$(cat {self.dir}/{name}.pid)\" -x {shlex.quote(process)}")

    def run_processes(self) -> list[tuple[int, str]]:
        return parse_process_list(self.sh(f"sudo sh -c {shlex.quote(find_run_processes_command(self.run_id))}").stdout)

    def kill_run(self) -> list[tuple[int, str]]:
        """Stop every process of this run. Returns what was running before the first signal."""
        found = self.run_processes()
        if found:
            pids = " ".join(str(pid) for pid, _ in found)
            self.sh(f"sudo kill -TERM {pids} 2>/dev/null; sleep 2; sudo kill -KILL {pids} 2>/dev/null; true")
        return found

    def remove_dir(self) -> Done:
        return self.sh(f"sudo rm -rf -- {self.dir}")
