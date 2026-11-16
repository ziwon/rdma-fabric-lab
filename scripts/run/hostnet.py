"""make k8s-hostnet-test: the VM RXE baseline repeated between two hostNetwork pods, recorded through rfl.

    python -m run.hostnet -i ansible/inventories/lab -i .lab/inventory.yml

One pod per worker in the lab namespace runs the pinned RDMA test image with the node's network.
rping and perftest run inside the pods against the lab NIC addresses with the parameters of
vm-rxe.yaml, while tcpdump captures every NIC on the nodes, exactly as vm-rxe-baseline does.
The pods use the node's RXE device. That proves nothing about a pod-private interface, device
isolation or Cilium's pod data path.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import yaml

from . import rfl, rxe
from .base import Outcome, check_leftovers, failed, passed, write_summary
from .k8s import cluster_from_listing, precondition
from .kube import RUN_LABEL, Kubectl, PodExec, save
from .remote import Remote

REPO_ROOT = Path(__file__).resolve().parents[2]
TRACK = REPO_ROOT / "tracks" / "a-soft-roce"
SCENARIO = "k8s-hostnet-rdma"
CONFIG = TRACK / "config" / f"{SCENARIO}.yaml"
LOCK = REPO_ROOT / "versions.lock.yaml"
PSA = "pod-security.kubernetes.io"
POD_TOOLS = ("ibverbs-providers", "ibverbs-utils", "rdmacm-utils", "perftest")
TOOL_LOCK = {"ibverbs-providers": "lab.rdma_core", "ibverbs-utils": "lab.rdma_core", "rdmacm-utils": "lab.rdma_core",
             "perftest": "lab.perftest"}
ROLES = ("server", "client")


# ---------------------------------------------------------------- objects


def namespace_object(name: str, run_id: str, level: str) -> dict:
    return {"apiVersion": "v1", "kind": "Namespace",
            "metadata": {"name": name, "labels": {RUN_LABEL: run_id, f"{PSA}/enforce": level, f"{PSA}/enforce-version": "latest"}}}


def rdma_pod(ns: str, run_id: str, name: str, node: str, image: str, profile: dict, config: dict) -> dict:
    """A hostNetwork pod pinned to node by its hostname label, sleeping until the run execs into it."""
    container = {
        "name": config["container"], "image": image, "imagePullPolicy": "Never",
        "command": ["sleep", str(config["pod_lifetime_s"])],
        "env": [{"name": "RFL_RUN", "value": run_id}],
        "securityContext": copy.deepcopy(profile["securityContext"]),
    }
    spec = {
        "hostNetwork": True, "dnsPolicy": "ClusterFirstWithHostNet", "restartPolicy": "Never",
        "nodeSelector": {"kubernetes.io/hostname": node}, "terminationGracePeriodSeconds": 1,
        "automountServiceAccountToken": False, "enableServiceLinks": False, "containers": [container],
    }
    if profile.get("device_files"):
        container["volumeMounts"] = [{"name": "infiniband", "mountPath": "/dev/infiniband"}]
        spec["volumes"] = [{"name": "infiniband", "hostPath": {"path": "/dev/infiniband", "type": "Directory"}}]
    return {"apiVersion": "v1", "kind": "Pod",
            "metadata": {"name": name, "namespace": ns, "labels": {RUN_LABEL: run_id, "role": name}}, "spec": spec}


# ---------------------------------------------------------------- parsing


def image_digest_problems(pod: dict, digest: str) -> list[str]:
    """Why a running pod does not run the image by the locked digest, or nothing."""
    statuses = pod.get("status", {}).get("containerStatuses", [])
    if not statuses:
        return [f"pod {pod['metadata']['name']} reports no container"]
    return [f"pod {pod['metadata']['name']} runs {s.get('imageID')}, want {digest}" for s in statuses
            if not str(s.get("imageID", "")).endswith(f"@{digest}")]


def security_problems(pod: dict, profile: dict) -> list[str]:
    """How a pod's spec differs from the declared profile: host network only, no host PID or IPC, no volume."""
    spec, name = pod["spec"], pod["metadata"]["name"]
    problems = []
    if spec.get("hostNetwork") is not True:
        problems.append(f"{name} does not use the host network")
    for key in ("hostPID", "hostIPC"):
        if spec.get(key):
            problems.append(f"{name} sets {key}")
    volumes = [v["name"] for v in spec.get("volumes", [])]
    if volumes and not profile.get("device_files"):
        problems.append(f"{name} mounts volumes {volumes}")
    for c in spec["containers"]:
        got = c.get("securityContext", {})
        missing = {k: v for k, v in profile["securityContext"].items() if got.get(k) != v}
        if missing:
            problems.append(f"{name}/{c['name']} securityContext lacks {missing}")
    return problems


def podsecurity_refused(stderr: str) -> int:
    """How many existing pods the warnings of a dry-run PodSecurity relabel name, as in "pod-a (and 1 other pod): reasons"."""
    return sum(1 + int(m.group(2) or 0)
               for m in re.finditer(r"^Warning: ([a-z0-9][a-z0-9.-]*)(?: \(and (\d+) other pods?\))?: ", stderr, re.M))


def devinfo_problems(text: str, device: str) -> list[str]:
    problems = []
    if not re.search(rf"^hca_id:\s+{re.escape(device)}$", text, re.M):
        problems.append(f"no hca_id {device}")
    if "PORT_ACTIVE" not in text:
        problems.append("port not PORT_ACTIVE")
    if not re.search(r"link_layer:\s+Ethernet", text):
        problems.append("link layer is not Ethernet")
    return problems


def memlock_limits(text: str) -> tuple[str, str] | None:
    """(soft, hard) of 'Max locked memory' in /proc/self/limits, in bytes or 'unlimited'."""
    m = re.search(r"^Max locked memory\s+(\S+)\s+(\S+)\s+bytes", text, re.M)
    return (m.group(1), m.group(2)) if m else None


def dpkg_versions(text: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


# ---------------------------------------------------------------- run


def load_config(path: Path = CONFIG) -> dict:
    """vm-rxe.yaml, so the pods repeat the VM baseline, with this scenario's keys on top."""
    return {**yaml.safe_load(rxe.CONFIG.read_text()), **yaml.safe_load(path.read_text())}


class Run(rxe.Run):
    def __init__(self, inventories: list[Path], config_path: Path, run_id: str | None):
        config = load_config(config_path)
        listing = json.loads(subprocess.run(["ansible-inventory", "--list", *[a for i in inventories for a in ("-i", str(i))]],
                                            capture_output=True, text=True, check=True).stdout)
        hostvars = listing["_meta"]["hostvars"][config["server"]]
        if "lab_k8s_namespace" not in hostvars:
            raise ValueError("the inventory sets no lab_k8s_namespace, the namespace of the lab test pods")
        super().__init__(SCENARIO, inventories, config, run_id)
        self.config_path = config_path
        nodes, first_cp, _ = cluster_from_listing(listing)
        self.kubectl = Kubectl(Remote(nodes[first_cp], self.run_id, self.log))
        self.ns = str(hostvars["lab_k8s_namespace"])
        lock = yaml.safe_load(LOCK.read_text())["components"]
        self.lock = lock
        self.digest = lock[config["image"]]["value"]
        self.image = f"{lock[config['image']]['ref']}@{self.digest}"
        self.nodes = {"server": config["server"], "client": config["client"]}
        self.pods: dict[str, PodExec] = {}

    def pod_name(self, role: str) -> str:
        return f"rdma-{role}"

    def profile(self, name: str) -> dict:
        return self.config["profiles"][name]


def write_resolved_config(run: Run) -> None:
    rxe.write_resolved_config(run)
    resolved = yaml.safe_load((run.dir / "resolved-config.yaml").read_text())
    resolved |= {"scenario": SCENARIO, "namespace": run.ns, "image": run.image,
                 "pods": {role: {"name": run.pod_name(role), "node": node} for role, node in run.nodes.items()}}
    (run.dir / "resolved-config.yaml").write_text(yaml.safe_dump(resolved, sort_keys=False))
    rfl.add_inputs(run.dir, [run.config_path, LOCK])
    rfl.set_section(run.dir, "inputs", {"parameters": {"namespace": run.ns, "image": run.image, "profile": run.config["profile"]}})


def check_pods(run: Run) -> Outcome:
    existing, _ = run.kubectl.json(f"get namespace {run.ns} --ignore-not-found")
    if existing:
        owner = existing.get("metadata", {}).get("labels", {}).get(RUN_LABEL, "no run")
        evidence = [run.raw("rdma-pods-ready/existing-namespace.json", json.dumps(existing, indent=2) + "\n")]
        return failed("environment-blocked", evidence, f"namespace {run.ns} already exists, from {owner}. "
                      f"make cleanup RUN={owner} removes it")
    objects = [namespace_object(run.ns, run.run_id, "privileged")]
    objects += [rdma_pod(run.ns, run.run_id, run.pod_name(role), node, run.image, run.profile(run.config["profile"]), run.config)
                for role, node in run.nodes.items()]
    applied = run.kubectl("apply", objects)
    evidence = [save(run.raw, "rdma-pods-ready/apply.txt", applied, "kubectl apply -f <namespace, pods>"),
                run.raw("rdma-pods-ready/objects.json", json.dumps(objects, indent=2) + "\n")]
    if applied.rc != 0:
        return failed("command-failed", evidence, f"kubectl apply exited {applied.rc}", applied.rc)
    t = run.config["ready_timeout_s"]
    waited = run.kubectl(f"-n {run.ns} wait --for=condition=Ready pod --all --timeout={t}s", timeout_s=t + 30)
    evidence.append(save(run.raw, "rdma-pods-ready/wait.txt", waited, f"kubectl wait --for=condition=Ready pod --all --timeout={t}s"))
    pods, done = run.kubectl.json(f"-n {run.ns} get pods")
    evidence.append(save(run.raw, "rdma-pods-ready/pods.json", done, "kubectl get pods -o json"))
    nodes, _ = run.kubectl.json("get nodes")
    node_ips = {n["metadata"]["name"]: [a["address"] for a in n["status"]["addresses"] if a["type"] == "InternalIP"]
                for n in nodes.get("items", [])}
    by_name = {p["metadata"]["name"]: p for p in pods.get("items", [])}
    problems, seen = [], []
    for role, node in run.nodes.items():
        p = by_name.get(run.pod_name(role))
        if p is None:
            problems.append(f"pod {run.pod_name(role)} does not exist")
            continue
        status = p["status"]
        if not any(c.get("type") == "Ready" and c.get("status") == "True" for c in status.get("conditions", [])):
            problems.append(f"pod {run.pod_name(role)} is {status.get('phase')} and not Ready")
        if p["spec"].get("nodeName") != node:
            problems.append(f"pod {run.pod_name(role)} runs on {p['spec'].get('nodeName')}, want {node}")
        if status.get("podIP") not in node_ips.get(node, []):
            problems.append(f"pod {run.pod_name(role)} has {status.get('podIP')}, not the node address {node_ips.get(node)}")
        problems += image_digest_problems(p, run.digest)
        seen.append(f"{run.pod_name(role)} on {node} with the node address {status.get('podIP')}")
    if problems:
        return failed("timeout" if waited.rc != 0 else "assertion-failed", evidence, "; ".join(problems), waited.rc)
    for role in ROLES:
        guest = run.guests[run.nodes[role]]
        run.pods[role] = PodExec(run.kubectl, run.ns, run.pod_name(role), run.config["container"], guest, run.run_id, run.log)
    return passed(evidence, "; ".join(seen) + f"; both run {run.image}")


def check_pod_tools(run: Run) -> Outcome:
    evidence, problems, seen, tools = [], [], [], {}
    query = "dpkg-query -W -f '${Package}=${Version}\\n' " + " ".join(POD_TOOLS)
    for role, pod in run.pods.items():
        inside = pod.sh(query)
        on_node = run.remote[run.nodes[role]].sh(query)
        evidence += [save(run.raw, f"pod-tools-match-lock/{pod.pod}.txt", inside, f"kubectl exec {pod.pod} -- {query}"),
                     save(run.raw, f"pod-tools-match-lock/{pod.guest.name}.txt", on_node, query)]
        got, node = dpkg_versions(inside.stdout), dpkg_versions(on_node.stdout)
        tools |= {f"{pod.pod} {k}": v for k, v in got.items()}
        for package in POD_TOOLS:
            want = run.lock[TOOL_LOCK[package]]["value"]
            if got.get(package) != want or node.get(package) != want:
                problems.append(f"{package}: pod {pod.pod} {got.get(package)}, node {pod.guest.name} {node.get(package)}, lock {want}")
        seen.append(f"{pod.pod} " + ", ".join(f"{k} {got.get(k)}" for k in POD_TOOLS))
    rfl.set_section(run.dir, "environment", {"tools": tools})
    if problems:
        return failed("assertion-failed", evidence, "; ".join(problems))
    return passed(evidence, "; ".join(seen) + "; the same versions on the nodes and in the lock")


def check_security(run: Run) -> Outcome:
    profile = run.profile(run.config["profile"])
    pods, done = run.kubectl.json(f"-n {run.ns} get pods -l {RUN_LABEL}={run.run_id}")
    evidence = [save(run.raw, "pod-security-as-declared/pods.json", done, "kubectl get pods -o json")]
    problems = [p for pod in pods.get("items", []) for p in security_problems(pod, profile)]
    running = len(pods.get("items", []))
    seen = []
    for level in run.config["podsecurity_levels_refused"]:
        command = f"label --dry-run=server --overwrite namespace {run.ns} {PSA}/enforce={level}"
        relabel = run.kubectl(command)
        evidence.append(save(run.raw, f"pod-security-as-declared/dry-run-{level}.txt", relabel, f"kubectl {command}"))
        refused = podsecurity_refused(relabel.stderr)
        seen.append(f"PodSecurity {level} would refuse {refused} of {running} pods")
        if refused != running or running == 0:
            problems.append(f"PodSecurity {level} would refuse {refused} of the {running} pods")
    if problems:
        return failed("assertion-failed", evidence, "; ".join(problems))
    return passed(evidence, f"profile {run.config['profile']}: host network, no host PID or IPC, no volume; " + "; ".join(seen))


def check_narrower(run: Run) -> Outcome:
    """Each narrower profile of the config, in a pod of its own on the server node, fails its probe as recorded."""
    evidence, problems, seen = [], [], []
    node = run.nodes["server"]
    for item in run.config["narrower_profiles"]:
        name = f"narrow-{item['profile']}"
        pod = rdma_pod(run.ns, run.run_id, name, node, run.image, run.profile(item["profile"]), run.config)
        applied = run.kubectl("apply", pod)
        waited = run.kubectl(f"-n {run.ns} wait --for=condition=Ready pod/{name} --timeout={run.config['ready_timeout_s']}s",
                             timeout_s=run.config["ready_timeout_s"] + 30)
        probe = run.kubectl(run.kubectl.exec_argv(run.ns, name, run.config["container"], item["probe"]))
        evidence += [run.raw(f"narrower-profiles-fail/{name}.json", json.dumps(pod, indent=2) + "\n"),
                     save(run.raw, f"narrower-profiles-fail/{name}-probe.txt", probe, f"kubectl exec {name} -- {item['probe']}")]
        run.kubectl(f"-n {run.ns} delete pod {name} --wait --timeout=60s", timeout_s=90)
        output = probe.stdout + probe.stderr
        if applied.rc != 0 or waited.rc != 0:
            problems.append(f"{item['profile']}: the pod did not start (apply {applied.rc}, wait {waited.rc})")
        elif probe.rc == 0 or item["expect"] not in output:
            problems.append(f"{item['profile']}: probe exit {probe.rc} without {item['expect']!r}, so this narrower profile may work")
        else:
            seen.append(f"{item['profile']}: exit {probe.rc}, {item['expect']!r}")
    if problems:
        return failed("assertion-failed", evidence, "; ".join(problems))
    return passed(evidence, "; ".join(seen))


def check_devinfo(run: Run) -> Outcome:
    evidence, problems, seen = [], [], []
    for pod in run.pods.values():
        command = f"ibv_devinfo -d {pod.guest.rxe_device}"
        done = pod.sh(command)
        evidence.append(save(run.raw, f"ibv-devinfo-in-pods/{pod.guest.name}-{pod.pod}.txt", done,
                             f"node {pod.guest.name} pod {pod.namespace}/{pod.pod}: {command}"))
        found = devinfo_problems(done.stdout, pod.guest.rxe_device) if done.rc == 0 else [f"exit {done.rc}"]
        problems += [f"{pod.pod}: {p}" for p in found]
        seen.append(f"{pod.pod} on {pod.guest.name} {pod.guest.rxe_device} PORT_ACTIVE")
    if problems:
        return failed("assertion-failed", evidence, "; ".join(problems))
    return passed(evidence, "; ".join(seen))


def check_memlock(run: Run) -> Outcome:
    """The memlock limits inside each pod, read beside those of the node's SSH user that the VM baseline runs as.
    Both ends of the pair must run under the same limits, as both ends of the VM baseline do."""
    evidence, problems, seen, found = [], [], [], {}
    command = "grep -E '^Max locked memory' /proc/self/limits"
    for role, pod in run.pods.items():
        inside = pod.sh(command)
        node = run.remote[run.nodes[role]].sh(command)
        evidence += [save(run.raw, f"memlock-in-pods/{pod.guest.name}-{pod.pod}.txt", inside,
                          f"node {pod.guest.name} pod {pod.namespace}/{pod.pod}: {command}"),
                     save(run.raw, f"memlock-in-pods/{pod.guest.name}-ssh-user.txt", node, f"node {pod.guest.name}: {command}")]
        limits, baseline = memlock_limits(inside.stdout), memlock_limits(node.stdout)
        if limits is None:
            problems.append(f"{pod.pod}: no memlock limit read (exit {inside.rc})")
            continue
        found[pod.pod] = limits
        seen.append(f"{pod.pod} soft {limits[0]} hard {limits[1]} bytes "
                    f"(VM baseline user {baseline[0] + ' ' + baseline[1] if baseline else 'unread'})")
        for side, value in zip(("soft", "hard"), limits):
            run.metrics.append({"name": f"memlock.{side}_bytes", "value": None if value == "unlimited" else int(value),
                                "unit": "bytes", "absent_reason": "the limit is unlimited" if value == "unlimited" else None,
                                "source": evidence[-2], "labels": {"pod": pod.pod, "node": pod.guest.name}})
    if len(set(found.values())) > 1:
        problems.append(f"the two ends run under different limits: {found}")
    if problems:
        return failed("assertion-failed", evidence, "; ".join(problems))
    return passed(evidence, "; ".join(seen))


def check_removed(run: Run) -> Outcome:
    t = run.config["delete_timeout_s"]
    deleted = run.kubectl(f"delete namespace {run.ns} --ignore-not-found --wait --timeout={t}s", timeout_s=t + 30)
    left = run.kubectl(f"get pods -A -l {RUN_LABEL}={run.run_id} -o name")
    ns_left = run.kubectl(f"get namespace -l {RUN_LABEL}={run.run_id} -o name")
    evidence = [save(run.raw, "rdma-pods-removed/delete.txt", deleted, f"kubectl delete namespace {run.ns} --wait"),
                save(run.raw, "rdma-pods-removed/pods-left.txt", left, f"kubectl get pods -A -l {RUN_LABEL}={run.run_id}"),
                save(run.raw, "rdma-pods-removed/namespaces-left.txt", ns_left, f"kubectl get namespace -l {RUN_LABEL}={run.run_id}")]
    if left.rc != 0 or ns_left.rc != 0:
        return failed("command-failed", evidence, f"kubectl get exited {left.rc}, {ns_left.rc}")
    if left.stdout.strip() or ns_left.stdout.strip():
        return failed("assertion-failed", evidence, f"still present: {(left.stdout + ns_left.stdout).split()}")
    return passed(evidence, f"namespace {run.ns} and every pod labeled {RUN_LABEL}={run.run_id} are gone")


AFTER_PODS = ("pod-tools-match-lock", "pod-security-as-declared", "narrower-profiles-fail", "ibv-devinfo-in-pods",
              "memlock-in-pods", "rxe-bound-to-lab-nic", "route-via-lab-nic", "rping-verified", "rdma-read", "rdma-write",
              "rdma-send", "roce-on-lab-nic", "no-roce-on-mgmt-nic")


def suite(run: Run) -> None:
    pods = check_pods(run)
    run.record("rdma-pods-ready", pods)
    if pods.status != "passed":
        for check_id in AFTER_PODS:
            run.record(check_id, precondition("rdma-pods-ready"))
        return
    run.record("pod-tools-match-lock", check_pod_tools(run))
    run.record("pod-security-as-declared", check_security(run))
    run.record("narrower-profiles-fail", check_narrower(run))
    run.record("ibv-devinfo-in-pods", check_devinfo(run))
    run.record("memlock-in-pods", check_memlock(run))
    run.server, run.client = run.pods["server"], run.pods["client"]
    rxe.traffic_suite(run)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run.hostnet", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-i", "--inventory", type=Path, action="append", required=True)
    parser.add_argument("--run-id", help="share one run id across scenarios")
    parser.add_argument("--config", type=Path, default=CONFIG, help="another parameter file, as a deliberate break uses")
    args = parser.parse_args(argv)

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    run = Run(args.inventory, args.config, args.run_id)
    run.say(f"run {run.run_id} scenario {SCENARIO} in {run.dir}, namespace {run.ns}")
    try:
        write_resolved_config(run)
        rxe.describe_environment(run)
        suite(run)
    except KeyboardInterrupt:
        run.warn("interrupted, removing this run's pods and guest processes")
    except Exception as exc:
        run.warn(f"runner error, removing this run's pods and guest processes: {exc!r}")
        raise
    finally:
        run.record("rdma-pods-removed", check_removed(run))
        run.record("no-process-left", check_leftovers(run))
        for remote in run.remote.values():
            remote.remove_dir()
        if run.metrics:
            rfl.write_metrics(run.dir, run.metrics)
        rfl.set_section(run.dir, "timing", {"wall_clock_seconds": round(time.monotonic() - run.started, 1)})
        code, message = rfl.finalize(run.dir)
        write_summary(run)
        run.say(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
