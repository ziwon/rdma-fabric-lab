"""make cleanup RUN=<run-id>: remove what one run left on the lab guests.

Stops the processes whose environment carries RFL_RUN=<run-id> and deletes
/var/tmp/rfl/<run-id> on each guest. When a cluster runs, it also deletes the
namespaces labeled with the run id, which a k8s-baseline run creates. The RDMA
device, rfl-rxe.service, the network and cluster settings, other runs and the
guests stay as they are.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from run.k8s import KUBECTL, RUN_LABEL, cluster_from_listing, group_hosts
from run.remote import CommandLog, Remote, guest_run_dir

REPO_ROOT = Path(__file__).resolve().parents[2]


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
    nodes, first_cp, _ = cluster_from_listing(listing)
    dirty = 0
    for name, guest in nodes.items():
        remote = Remote(guest, args.run_id, log)
        had_dir = remote.sh(f"test -d {remote.dir}").rc == 0
        stopped = remote.kill_run()
        remote.remove_dir()
        left = remote.run_processes()
        still_dir = remote.sh(f"test -d {remote.dir}").rc == 0
        for pid, cmd in stopped:
            print(f"{name}: stopped {pid} {cmd}")
        print(f"{name}: {len(stopped)} processes of run {args.run_id} stopped, "
              f"{remote.dir} {'removed' if had_dir and not still_dir else 'was absent' if not had_dir else 'still present'}")
        for pid, cmd in left:
            print(f"{name}: still running {pid} {cmd}", file=sys.stderr)
        dirty += bool(left) or still_dir
    if group_hosts(listing, "kube_control_plane"):
        dirty += remove_namespaces(Remote(nodes[first_cp], args.run_id, log), args.run_id)
    return 1 if dirty else 0


def remove_namespaces(cp: Remote, run_id: str) -> bool:
    """Deletes the namespaces labeled with the run id. True when one is left."""
    if cp.sh("sudo test -f /etc/kubernetes/admin.conf").rc != 0:
        print(f"{cp.guest.name}: no cluster, no namespace to remove")
        return False
    selector = f"{RUN_LABEL}={run_id}"
    found = cp.sh(f"{KUBECTL} get namespace -l {selector} -o name").stdout.split()
    cp.sh(f"{KUBECTL} delete namespace -l {selector} --ignore-not-found --wait --timeout=120s", timeout_s=180)
    left = cp.sh(f"{KUBECTL} get namespace -l {selector} -o name").stdout.split()
    print(f"{cp.guest.name}: namespaces of run {run_id}: {', '.join(found) or 'none'}; left {', '.join(left) or 'none'}")
    return bool(left)


if __name__ == "__main__":
    sys.exit(main())
