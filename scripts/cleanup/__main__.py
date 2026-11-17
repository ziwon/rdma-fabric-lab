"""make cleanup RUN=<run-id>: remove what one run left, on the guests of the inventories and on this host.

Every guest: the processes whose environment carries RFL_RUN=<run-id>, and /var/tmp/rfl/<run-id>.
The Track B and Track C guests: the containers labeled with the run and their run files, removed by
guest.sh stop-run inside the guest. A cluster's first control plane: the namespaces labeled with the
run, which hold its pods and its NetworkAttachmentDefinition, and any pod labeled with the run elsewhere.
This host: the processes of this user that carry RFL_RUN=<run-id>, and its directories rfl-*-<run-id>-* in the temp directory.

It runs no kubeadm reset, changes no network definition and removes no container, namespace or
process that does not carry the run id. The RDMA device, rfl-rxe.service, the images and the guests stay.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ibsim import guest as track_b
from ns3 import guest as track_c
from run.inventory import ssh_argv
from run.k8s import KUBECTL, RUN_LABEL, group_hosts
from run.remote import CommandLog, Done, Remote, guest_run_dir

REPO_ROOT = Path(__file__).resolve().parents[2]

# Inventory group -> the guest.sh that removes a run's containers on its hosts.
TRACK_GUESTS = {track_b.GUEST_GROUP: track_b, track_c.GUEST_GROUP: track_c}
CLUSTER_GROUP = "kube_control_plane"

Sh = Callable[[str, float], Done]


@dataclass(frozen=True)
class Host:
    name: str
    ssh: tuple[str, ...]


@dataclass
class Outcome:
    """What cleanup did on one place, and whether anything of the run is left there."""

    where: str
    lines: list[str] = field(default_factory=list)
    left: list[str] = field(default_factory=list)


def hosts_of(listing: dict) -> dict[str, Host]:
    hostvars = listing["_meta"]["hostvars"]
    return {name: Host(name, ssh_argv(vars_)) for name, vars_ in hostvars.items()}


def clean_guest_processes(remote: Remote) -> Outcome:
    out = Outcome(remote.guest.name)
    reach = remote.sh("true")
    if reach.rc != 0:
        out.left.append(f"unreachable over SSH (rc {reach.rc}): {reach.stderr.strip()}")
        return out
    had_dir = remote.sh(f"test -d {remote.dir}").rc == 0
    stopped = remote.kill_run()
    remote.remove_dir()
    out.lines += [f"stopped {pid} {cmd}" for pid, cmd in stopped]
    out.lines.append(f"{len(stopped)} processes stopped, {remote.dir} {'removed' if had_dir else 'was absent'}")
    out.left += [f"process {pid} {cmd}" for pid, cmd in remote.run_processes()]
    if remote.sh(f"test -d {remote.dir}").rc == 0:
        out.left.append(f"{remote.dir} still present")
    return out


def clean_track_guest(module, host: Host, run_id: str, log: CommandLog) -> Outcome:
    """guest.sh stop-run inside the Track B or Track C guest, after the guest gets this checkout's guest.sh."""
    out = Outcome(f"{host.name} {module.GUEST_GROUP}")
    guest = module.Guest(host.name, host.ssh, log)
    synced = guest.sync()
    if synced.rc != 0:
        out.left.append(f"could not copy guest.sh into the guest (rc {synced.rc}): {synced.stderr.strip()}")
        return out
    done = guest.guest_sh("stop-run", run_id, timeout_s=300)
    out.lines += done.stdout.splitlines()
    if done.rc != 0:
        out.left.append(f"guest.sh stop-run exited {done.rc}: {done.stderr.strip()}")
    return out


def clean_cluster(sh: Sh, where: str, run_id: str) -> Outcome:
    """Deletes the namespaces labeled with the run, then any pod labeled with it in another namespace."""
    out = Outcome(f"{where} cluster")
    if sh("sudo test -f /etc/kubernetes/admin.conf", 30).rc != 0:
        out.lines.append("no cluster, no namespace to remove")
        return out
    selector = f"{RUN_LABEL}={run_id}"
    namespaces = sh(f"{KUBECTL} get namespace -l {selector} -o name", 60).stdout.split()
    for ns in namespaces:
        name = ns.split("/", 1)[1]
        held = sh(f"{KUBECTL} -n {name} get pods,network-attachment-definitions -o name", 60)
        out.lines.append(f"{ns} holds: {' '.join(held.stdout.split()) or 'nothing'}")
    if namespaces:
        sh(f"{KUBECTL} delete namespace -l {selector} --ignore-not-found --wait --timeout=180s", 240)
    pods = sh(f"{KUBECTL} get pods -A -l {selector} -o name", 60).stdout.split()
    if pods:
        sh(f"{KUBECTL} delete pods -A -l {selector} --ignore-not-found --wait --timeout=120s", 180)
        out.lines.append(f"pods labeled {selector} outside its namespaces: {' '.join(pods)}")
    out.lines.append(f"namespaces labeled {selector}: {', '.join(namespaces) or 'none'}")
    out.left += sh(f"{KUBECTL} get namespace -l {selector} -o name", 60).stdout.split()
    out.left += sh(f"{KUBECTL} get pods -A -l {selector} -o name", 60).stdout.split()
    return out


def carries_run(environ: bytes, run_id: str) -> bool:
    return f"RFL_RUN={run_id}".encode() in environ.split(b"\0")


def host_processes(run_id: str, proc: Path = Path("/proc")) -> list[tuple[int, str]]:
    """Processes of this user on this host whose environment holds RFL_RUN=<run-id>."""
    found = []
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            if entry.stat().st_uid != os.getuid() or not carries_run((entry / "environ").read_bytes(), run_id):
                continue
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
        except OSError:
            continue
        found.append((int(entry.name), cmdline))
    return found


def host_dirs(run_id: str, root: Path | None = None) -> list[Path]:
    """Directories of this user that a run made in the temp directory, named rfl-<what>-<run-id>-<random>."""
    root = Path(tempfile.gettempdir()) if root is None else root
    return sorted(p for p in root.glob(f"rfl-*-{run_id}-*") if p.is_dir() and not p.is_symlink() and p.stat().st_uid == os.getuid())


def clean_host(run_id: str, temp_root: Path | None = None) -> Outcome:
    out = Outcome("this host")
    found = host_processes(run_id)
    for pid, _ in found:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 10
    while (left := host_processes(run_id)) and time.monotonic() < deadline:
        time.sleep(0.2)
    out.lines += [f"stopped {pid} {cmd}" for pid, cmd in found]
    out.lines.append(f"{len(found)} processes of this user carried RFL_RUN={run_id}")
    out.left += [f"process {pid} {cmd}" for pid, cmd in left]
    for path in host_dirs(run_id, temp_root):
        shutil.rmtree(path)
        out.lines.append(f"removed {path}")
    out.left += [f"directory {p}" for p in host_dirs(run_id, temp_root)]
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cleanup", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("-i", "--inventory", type=Path, action="append", required=True)
    args = parser.parse_args(argv)
    guest_run_dir(args.run_id)

    run_root = REPO_ROOT / "results" / args.run_id
    log = CommandLog(run_root / "cleanup.log" if run_root.is_dir() else None)
    listing = json.loads(subprocess.run(["ansible-inventory", "--list", *[a for i in args.inventory for a in ("-i", str(i))]],
                                        capture_output=True, text=True, check=True).stdout)
    hosts = hosts_of(listing)
    outcomes = []
    for host in hosts.values():
        outcomes.append(clean_guest_processes(Remote(host, args.run_id, log)))
    for group, module in TRACK_GUESTS.items():
        outcomes += [clean_track_guest(module, hosts[name], args.run_id, log) for name in group_hosts(listing, group)]
    control_planes = group_hosts(listing, CLUSTER_GROUP)
    if control_planes:
        cp = Remote(hosts[control_planes[0]], args.run_id, log)
        outcomes.append(clean_cluster(lambda command, timeout: cp.sh(command, timeout_s=timeout), cp.guest.name, args.run_id))
    outcomes.append(clean_host(args.run_id))

    for outcome in outcomes:
        for line in outcome.lines:
            print(f"{outcome.where}: {line}")
        for line in outcome.left:
            print(f"{outcome.where}: LEFT {line}", file=sys.stderr)
    left = sum(len(o.left) for o in outcomes)
    print(f"cleanup of run {args.run_id}: {len(outcomes)} places checked, "
          f"{'nothing of the run is left' if not left else f'{left} things left'}")
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())
