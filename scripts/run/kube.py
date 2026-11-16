"""kubectl on the first control plane, and run-scoped processes inside a pod.

PodExec has the calls of remote.Remote, so the RXE checks of run.rxe drive a pod the same way they
drive a guest over SSH. Every process it starts carries RFL_RUN=<run-id>, because the pod sets it
for the whole container, and the node sees those processes in its own /proc.
"""

from __future__ import annotations

import base64
import json
import shlex

from .remote import Done, Remote

KUBECTL = "sudo kubectl --kubeconfig /etc/kubernetes/admin.conf"
RUN_LABEL = "rdma-fabric-lab/run"


class Kubectl:
    def __init__(self, cp: Remote):
        self.cp = cp

    def __call__(self, args: str, manifest: list[dict] | dict | None = None, timeout_s: float = 60) -> Done:
        if manifest is None:
            return self.cp.sh(f"{KUBECTL} {args}", timeout_s=timeout_s)
        docs = manifest if isinstance(manifest, list) else [manifest]
        payload = base64.b64encode(json.dumps({"apiVersion": "v1", "kind": "List", "items": docs}).encode()).decode()
        return self.cp.sh(f"echo {payload} | base64 -d | {KUBECTL} {args} -f -", timeout_s=timeout_s)

    def json(self, args: str, timeout_s: float = 60) -> tuple[dict, Done]:
        done = self(f"{args} -o json", timeout_s=timeout_s)
        try:
            return json.loads(done.stdout or "{}"), done
        except json.JSONDecodeError:
            return {}, done

    def exec_argv(self, namespace: str, pod: str, container: str, command: str) -> str:
        return f"-n {namespace} exec {pod} -c {container} -- sh -c {shlex.quote(command)}"


def save(raw, rel: str, done: Done, command: str) -> str:
    """Write one command, its exit code and its output as a raw file of the run. raw is RunBase.raw."""
    return raw(rel, f"$ {command}\nexit {done.rc}\n--- stdout\n{done.stdout}--- stderr\n{done.stderr}")


class PodExec(Remote):
    """Processes of a run inside one container of one pod on guest's node, through kubectl exec."""

    def __init__(self, kubectl: Kubectl, namespace: str, pod: str, container: str, guest, run_id: str, log):
        super().__init__(guest, run_id, log)
        self.kubectl = kubectl
        self.namespace = namespace
        self.pod = pod
        self.container = container

    def sh(self, command: str, timeout_s: float = 60, binary: bool = False) -> Done:
        return self.kubectl(self.kubectl.exec_argv(self.namespace, self.pod, self.container, command), timeout_s=timeout_s)

    def where(self) -> dict:
        g = self.guest
        return {"node": g.name, "pod": f"{self.namespace}/{self.pod}", "container": self.container,
                "rdma_device": getattr(g, "rxe_device", None), "lab_address": getattr(g, "rdma_ip", None),
                "runs_as": "the container user of the pod"}

    def stop_session(self, name: str) -> None:
        self.sh(f"sid=$(cat {self.dir}/{name}.pid 2>/dev/null) && [ -n \"$sid\" ] && "
                "{ kill -TERM -- -\"$sid\"; sleep 1; kill -KILL -- -\"$sid\"; }; true")
