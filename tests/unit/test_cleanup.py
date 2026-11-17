"""make cleanup removes only what carries the run id: by label in the cluster, by RFL_RUN on this host."""

import os
import subprocess
import sys

from cleanup import __main__ as cleanup
from run.k8s import RUN_LABEL
from run.remote import Done

RUN_ID = "20261117T010203Z"
OTHER = "20261117T020304Z"
FORBIDDEN = ("kubeadm", "reset", "delete node", "netplan", "ip link", "ip addr", "virsh", "rdma link", "docker", "systemctl")


class FakeCluster:
    """A control plane whose kubectl answers from a table of label selectors and records every command."""

    def __init__(self, namespaces: list[str], stray_pods: list[str]):
        self.namespaces, self.stray_pods, self.commands = list(namespaces), list(stray_pods), []

    def __call__(self, command: str, timeout: float) -> Done:
        self.commands.append(command)
        if command.startswith("sudo test -f"):
            return Done(0, "", "")
        if " get namespace -l " in command:
            return Done(0, "".join(f"{n}\n" for n in self.namespaces), "")
        if " get pods -A -l " in command:
            return Done(0, "".join(f"{p}\n" for p in self.stray_pods), "")
        if " delete namespace -l " in command:
            self.namespaces = []
        if " delete pods -A -l " in command:
            self.stray_pods = []
        if "get pods,network-attachment-definitions" in command:
            return Done(0, "pod/server\nnetwork-attachment-definition.k8s.cni.cncf.io/rdma-lab-net\n", "")
        return Done(0, "", "")


def test_cluster_cleanup_deletes_only_by_the_run_label():
    cluster = FakeCluster(["namespace/rdma-lab"], ["pod/stray"])
    outcome = cleanup.clean_cluster(cluster, "cp1", RUN_ID)
    assert outcome.left == []
    changes = [c for c in cluster.commands if " delete " in c]
    assert changes == [c for c in changes if f"-l {RUN_LABEL}={RUN_ID} " in c] and len(changes) == 2
    assert not [c for c in cluster.commands for word in FORBIDDEN if word in c]
    assert any("rdma-lab-net" in line for line in outcome.lines)


def test_cluster_cleanup_reports_what_is_left():
    class Stuck(FakeCluster):
        def __call__(self, command, timeout):
            done = super().__call__(command, timeout)
            self.namespaces = ["namespace/rdma-lab"]
            return done

    outcome = cleanup.clean_cluster(Stuck(["namespace/rdma-lab"], []), "cp1", RUN_ID)
    assert outcome.left == ["namespace/rdma-lab"]


def test_without_a_cluster_nothing_is_deleted():
    commands = []

    def no_cluster(command, timeout):
        commands.append(command)
        return Done(1, "", "")

    outcome = cleanup.clean_cluster(no_cluster, "ibsim1", RUN_ID)
    assert outcome.left == [] and commands == ["sudo test -f /etc/kubernetes/admin.conf"]


def test_the_run_marker_must_match_exactly():
    environ = b"HOME=/home/u\0RFL_RUN=" + RUN_ID.encode() + b"\0PATH=/bin\0"
    assert cleanup.carries_run(environ, RUN_ID)
    assert not cleanup.carries_run(environ, OTHER)
    assert not cleanup.carries_run(b"RFL_RUN=" + RUN_ID.encode() + b"X\0", RUN_ID)
    assert not cleanup.carries_run(b"NOT_RFL_RUN=" + RUN_ID.encode() + b"\0", RUN_ID)


def test_host_cleanup_stops_only_the_processes_of_the_run():
    def sleeper(run_id: str) -> subprocess.Popen:
        return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], env={**os.environ, "RFL_RUN": run_id})

    mine, other = sleeper(RUN_ID), sleeper(OTHER)
    try:
        found = dict(cleanup.host_processes(RUN_ID))
        assert mine.pid in found and other.pid not in found
        outcome = cleanup.clean_host(RUN_ID)
        assert outcome.left == []
        assert mine.wait(timeout=10) == -9
        assert other.poll() is None
    finally:
        for process in (mine, other):
            process.kill()
            process.wait()


def test_host_cleanup_removes_only_the_temp_directories_named_after_the_run(tmp_path):
    mine, other, similar = tmp_path / f"rfl-ns3-decoy-{RUN_ID}-abc", tmp_path / f"rfl-ns3-decoy-{OTHER}-abc", tmp_path / f"x-{RUN_ID}-abc"
    for d in (mine, other, similar):
        d.mkdir()
        (d / "SimAI_simulator").write_text("")
    outcome = cleanup.clean_host(RUN_ID, temp_root=tmp_path)
    assert outcome.left == [] and f"removed {mine}" in outcome.lines
    assert not mine.exists() and other.exists() and similar.exists()
