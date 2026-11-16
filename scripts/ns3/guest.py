"""The Track C guest as the host sees it: SSH through the harness port forward and guest.sh inside.

The host never builds or runs the simulator and never starts a container. It copies the image
context and the track files into the guest, then calls tracks/c-ns3/scripts/guest.sh there through sudo.
"""

from __future__ import annotations

import io
import json
import shlex
import subprocess
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path

from run.inventory import ssh_argv
from run.remote import CommandLog, Done

REPO_ROOT = Path(__file__).resolve().parents[2]
GUEST_ROOT = "/var/tmp/rfl-track-c"
GUEST_SRC = f"{GUEST_ROOT}/src"
GUEST_SH = f"{GUEST_SRC}/tracks/c-ns3/scripts/guest.sh"
SYNCED = ("images/ns3", "tracks/c-ns3")
GUEST_GROUP = "track_c_guest"


def tar_of(files: dict[str, bytes]) -> bytes:
    """A tar archive of these files with mode 0644 and no owner or time, so equal files give equal bytes."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name, data in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def synced_tar(repo: Path) -> bytes:
    """The synced paths with their file modes, but no owner or time."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for top in SYNCED:
            for path in sorted((repo / top).rglob("*")):
                if "__pycache__" in path.parts:
                    continue
                info = tar.gettarinfo(str(path), arcname=str(path.relative_to(repo)))
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = 0
                if info.isfile():
                    with path.open("rb") as handle:
                        tar.addfile(info, handle)
                elif info.isdir():
                    tar.addfile(info)
    return buffer.getvalue()


@dataclass
class Guest:
    name: str
    ssh: tuple[str, ...]
    log: CommandLog

    def sh(self, command: str, stdin: bytes | None = None, timeout_s: float = 120, binary: bool = False) -> Done:
        start = time.monotonic()
        try:
            out = subprocess.run([*self.ssh, command], input=stdin, capture_output=True, timeout=timeout_s,
                                 stdin=None if stdin is not None else subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            self.log.write(self.name, command, "timeout", time.monotonic() - start)
            return Done(124, "", f"ssh command exceeded {timeout_s} s")
        self.log.write(self.name, command, out.returncode, time.monotonic() - start)
        stdout = out.stdout if binary else out.stdout.decode(errors="replace")
        return Done(out.returncode, stdout, out.stderr.decode(errors="replace"))

    def guest_sh(self, *args: str, stdin: bytes | None = None, timeout_s: float = 120, binary: bool = False) -> Done:
        return self.sh(f"sudo {GUEST_SH} {shlex.join(args)}", stdin=stdin, timeout_s=timeout_s, binary=binary)

    def sync(self) -> Done:
        """Replace the guest's copy of the synced paths with this checkout's files."""
        return self.sh(
            f"set -e; sudo rm -rf {GUEST_SRC}.new; sudo mkdir -p {GUEST_SRC}.new; sudo tar -x -C {GUEST_SRC}.new; "
            f"sudo rm -rf {GUEST_SRC}; sudo mv {GUEST_SRC}.new {GUEST_SRC}",
            stdin=synced_tar(REPO_ROOT),
        )


def load(inventories: list[Path], log: CommandLog) -> Guest:
    argv = ["ansible-inventory", "--list"]
    for inventory in inventories:
        argv += ["-i", str(inventory)]
    listing = json.loads(subprocess.run(argv, capture_output=True, text=True, check=True).stdout)
    hosts = listing.get(GUEST_GROUP, {}).get("hosts", [])
    if len(hosts) != 1:
        raise ValueError(f"inventory group {GUEST_GROUP} must hold exactly one host, it holds {hosts}")
    hostvars = listing["_meta"]["hostvars"][hosts[0]]
    if not hostvars.get("rfl_track_c_guest") or "ansible_port" not in hostvars:
        raise ValueError(f"{hosts[0]} lacks rfl_track_c_guest or its port forward. Pass the override inventory of lab.sh up.")
    return Guest(hosts[0], ssh_argv(hostvars), log)
