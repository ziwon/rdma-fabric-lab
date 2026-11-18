"""make fleet-rails-test: the rail networks of ADR 0007 on the cel1 simulation, recorded through the rfl CLI.

    python -m run.cel1_rails -i ansible/inventories/cel1 -i <simulation inventory.yml> \\
        --power-stop '<command> {guest}' --power-on '<command> {guest}'

The rendered values of helm/rail-networks give the rail namespace, the selector, the block size, the resource name and
each rail's network, master and subnet, the same file the chart reads, and the site model gives the hosts that must be
rail hosts. The rails of the simulation are Ethernet NICs without RDMA devices, so two results are failures by design:
the device plugin exposes no device, because it looks for RDMA devices on PCI functions, and ipoib-cni refuses every
rail master, because none is an IPoIB interface. Every guest command runs over the SSH forward the simulation inventory
names, and kubectl runs on a control plane against its admin.conf.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

import yaml

from fleet import site as site_model

from . import multus, rfl
from .base import Outcome, RunBase, check_leftovers, failed, passed, write_summary
from .k8s import cluster_from_listing, group_hosts, ready
from .kube import RUN_LABEL, Kubectl, save
from .remote import Remote

REPO_ROOT = Path(__file__).resolve().parents[2]
TRACK = REPO_ROOT / "tracks" / "cel1"
SCENARIO = "cel1-rail-networks"
SPEC = TRACK / "scenarios" / f"{SCENARIO}.yaml"
CONFIG = TRACK / "config" / f"{SCENARIO}.yaml"
LOCK = REPO_ROOT / "versions.lock.yaml"
RAIL_TEST = "rdma-fabric-lab/rail-test"
RDMA_POD, ATTACH_POD, BOOT_POD = "rail-rdma", "rail-attach", "rail-boot"
POLICY_KIND = "NicClusterPolicy"
# The images of the three components the policy names, by their image name in the lock refs.
COMPONENTS = {
    "rdma-shared-device-plugin": "addons.network_operator_rdma_shared_dev_plugin_image",
    "nv-ipam": "addons.network_operator_nv_ipam_image",
    "ipoib-cni": "addons.network_operator_ipoib_cni_image",
}
PLUGIN_POD_LABEL = "app=rdma-shared-dp"
# The device plugin's log line of the config it loaded, and of the devices it exposes to the kubelet.
LOADED = re.compile(r"loaded config: \[\{ResourceName:(?P<name>\S+) .*?RdmaHcaMax:(?P<max>\d+) .*?IfNames:\[(?P<ifnames>[^\]]*)\]")
EXPOSING = re.compile(r'exposing "(\d+)" devices')


def check_ids() -> tuple[str, ...]:
    return tuple(c["id"] for c in yaml.safe_load(SPEC.read_text())["checks"])


# ---------------------------------------------------------------- parsing


def selected(nodes: dict, selector: dict[str, str]) -> list[str]:
    """Names of the Nodes whose labels hold every label of the selector."""
    return sorted(n["metadata"]["name"] for n in nodes.get("items", [])
                  if selector.items() <= (n["metadata"].get("labels") or {}).items())


def locked_image(lock: dict, key: str) -> str:
    """repository@digest of an image-digest lock component, as the policy names it."""
    component = lock[key]
    return f"{component['ref'].rsplit(':', 1)[0]}@{component['value']}"


def component_problems(daemonsets: dict, pods: dict, images: dict[str, str], hosts: list[str]) -> tuple[list[str], list[str]]:
    """The DaemonSets that the policy owns must be exactly one per component image, each with one ready pod on every rail
    host and on no other node. images maps component -> repository@digest."""
    owned = [d for d in daemonsets.get("items", [])
             if any(o.get("kind") == POLICY_KIND for o in d["metadata"].get("ownerReferences") or [])]
    by_image: dict[str, list[dict]] = {}
    for ds in owned:
        for c in ds["spec"]["template"]["spec"].get("containers", []):
            by_image.setdefault(c["image"], []).append(ds)
    problems, seen = [], []
    wanted = set(images.values())
    for image in sorted(set(by_image) - wanted):
        problems.append(f"the policy runs DaemonSets {[d['metadata']['name'] for d in by_image[image]]} with image {image}, "
                        "which is none of its three components")
    for component, image in sorted(images.items()):
        sets = by_image.get(image, [])
        if len(sets) != 1:
            problems.append(f"{component}: {len(sets)} DaemonSets of the policy run {image}, want 1")
            continue
        ds = sets[0]
        name = ds["metadata"]["name"]
        status = ds.get("status", {})
        on = sorted(p["spec"].get("nodeName", "?") for p in pods.get("items", [])
                    if any(o.get("kind") == "DaemonSet" and o.get("name") == name for o in p["metadata"].get("ownerReferences") or [])
                    and not p["metadata"].get("deletionTimestamp"))
        ready_on = sorted(p["spec"].get("nodeName", "?") for p in pods.get("items", [])
                          if p["spec"].get("nodeName") in on and ready(p.get("status", {}).get("conditions"))
                          and any(o.get("name") == name for o in p["metadata"].get("ownerReferences") or []))
        if on != hosts:
            problems.append(f"{component} DaemonSet {name} runs on {on}, want exactly the rail hosts {hosts}")
        elif ready_on != hosts or status.get("numberReady") != len(hosts):
            problems.append(f"{component} DaemonSet {name} has {status.get('numberReady')} of {len(hosts)} pods ready, "
                            f"not ready on {sorted(set(hosts) - set(ready_on))}")
        seen.append(f"{component} {name} {status.get('numberReady')}/{status.get('desiredNumberScheduled')}")
    return problems, seen


def block_problems(pool: dict, hosts: list[str], block: int, subnet: str) -> tuple[list[str], list[str]]:
    """Each rail host holds one block of the pool's subnet, of block addresses, and the blocks are disjoint."""
    name, net = pool["metadata"]["name"], ipaddress.ip_network(subnet)
    allocations = pool.get("status", {}).get("allocations") or []
    problems, seen = [], []
    if pool.get("spec", {}).get("subnet") != subnet:
        problems.append(f"{name}: subnet {pool.get('spec', {}).get('subnet')}, want {subnet}")
    nodes = sorted(a.get("nodeName", "?") for a in allocations)
    if nodes != hosts:
        problems.append(f"{name}: blocks for {nodes}, want one for each rail host {hosts}")
    spans = []
    for a in allocations:
        try:
            first, last = ipaddress.ip_address(a["startIP"]), ipaddress.ip_address(a["endIP"])
        except (KeyError, ValueError):
            problems.append(f"{name}: allocation {a} has no valid startIP and endIP")
            continue
        size = int(last) - int(first) + 1
        if first not in net or last not in net:
            problems.append(f"{name}: block {first}-{last} of {a.get('nodeName')} leaves {subnet}")
        if size != block:
            problems.append(f"{name}: block {first}-{last} of {a.get('nodeName')} holds {size} addresses, want {block}")
        spans.append((int(first), int(last), a.get("nodeName")))
    spans.sort()
    for (_, end_a, node_a), (start_b, _, node_b) in zip(spans, spans[1:]):
        if start_b <= end_a:
            problems.append(f"{name}: the blocks of {node_a} and {node_b} overlap")
    if spans:
        seen.append(f"{name} {len(spans)} blocks {ipaddress.ip_address(spans[0][0])}-{ipaddress.ip_address(spans[-1][1])}")
    return problems, seen


def attachment_problems(nads: dict, rails: list[dict], namespace: str) -> list[str]:
    """One NetworkAttachmentDefinition per rail in namespace, of type ipoib on the rail's master, with the rail's pool."""
    found = {n["metadata"]["name"]: n for n in nads.get("items", []) if n["metadata"].get("namespace") == namespace}
    problems = []
    for rail in rails:
        nad = found.get(rail["name"])
        if nad is None:
            problems.append(f"no NetworkAttachmentDefinition {namespace}/{rail['name']}")
            continue
        try:
            config = json.loads(nad["spec"]["config"])
        except (KeyError, json.JSONDecodeError):
            problems.append(f"{rail['name']}: config is not JSON")
            continue
        want = {"type": "ipoib", "master": rail["master"], "ipam": {"type": "nv-ipam", "poolName": rail["name"]}}
        got = {"type": config.get("type"), "master": config.get("master"),
               "ipam": {k: (config.get("ipam") or {}).get(k) for k in ("type", "poolName")}}
        if got != want:
            problems.append(f"{rail['name']}: {got}, want {want}")
    return problems


def resource_problems(nodes: dict, resource: str, hosts: list[str], units: int) -> tuple[list[str], dict[str, str]]:
    """Every rail host offers units of resource, every other node none."""
    offered = {n["metadata"]["name"]: (n.get("status", {}).get("allocatable") or {}).get(resource) for n in nodes.get("items", [])}
    problems = [f"{name} offers {have or 'no'} {resource}, want {units}" for name, have in sorted(offered.items())
                if name in hosts and have != str(units)]
    problems += [f"{name} offers {have} {resource}, which only the rail hosts may" for name, have in sorted(offered.items())
                 if name not in hosts and have not in (None, "0")]
    return problems, {n: v for n, v in offered.items() if v}


def plugin_log_problems(log: str, resource: str, block: int, netdevs: list[str]) -> tuple[list[str], str]:
    """The plugin loaded the rendered config and exposes no device. Returns the problems and the last exposing line."""
    loaded = LOADED.search(log)
    exposing = EXPOSING.findall(log)
    problems = []
    if loaded is None:
        problems.append("no loaded config line in the plugin log")
    else:
        got = (loaded["name"], int(loaded["max"]), loaded["ifnames"].split())
        if got != (resource, block, netdevs):
            problems.append(f"the plugin loaded resource {got[0]}, rdmaHcaMax {got[1]}, ifNames {got[2]}; the values give "
                            f"{resource}, {block}, {netdevs}")
    if not exposing:
        problems.append("the plugin log names no exposed devices")
    elif exposing[-1] != "0":
        problems.append(f"the plugin exposes {exposing[-1]} devices, but no rail of the simulation has an RDMA device")
    return problems, f'exposing "{exposing[-1]}" devices' if exposing else ""


def waiting_problems(pod: dict, events: dict, resource: str) -> tuple[list[str], str]:
    """The pod that asks for the rail resource stays unscheduled for want of it."""
    problems = []
    if pod.get("spec", {}).get("nodeName") or pod.get("status", {}).get("phase") != "Pending":
        problems.append(f"the pod is {pod.get('status', {}).get('phase')} on {pod.get('spec', {}).get('nodeName') or 'no node'}, "
                        "want Pending and unscheduled")
    messages = [e.get("message", "") for e in events.get("items", []) if e.get("reason") == "FailedScheduling"]
    insufficient = [m for m in messages if f"Insufficient {resource}" in m]
    if not insufficient:
        problems.append(f"no FailedScheduling event names Insufficient {resource}; events {messages[:2]}")
    return problems, insufficient[0] if insufficient else ""


def refusal_problems(pod: dict, events: dict, rails: list[dict]) -> tuple[list[str], list[str]]:
    """The attach pod must not run, and its sandbox errors must be ipoib-cni's own refusal of a rail master."""
    phase = pod.get("status", {}).get("phase")
    statuses = pod.get("status", {}).get("containerStatuses") or []
    problems = []
    if phase == "Running" or any(s.get("ready") or "running" in (s.get("state") or {}) for s in statuses):
        problems.append(f"the pod runs (phase {phase}) although every rail is Ethernet")
    messages = [e.get("message", "") for e in events.get("items", []) if e.get("reason") == "FailedCreatePodSandBox"]
    refusals = [m for m in messages if ipoib_refusal(m, rails)]
    if not messages:
        problems.append("no FailedCreatePodSandBox event for the pod")
    elif not refusals:
        problems.append(f"no sandbox error is ipoib-cni refusing a rail master; the first is {messages[0][:400]!r}")
    return problems, refusals


def ipoib_refusal(message: str, rails: list[dict]) -> bool:
    """A sandbox error in which ipoib-cni refused a rail's master because it is no IPoIB interface. A plugin that did not
    run, such as a missing binary, gives another error."""
    return "not of type ipoib" in message and any(f'error adding container to network "{r["name"]}"' in message for r in rails)


# ---------------------------------------------------------------- run


class Run(RunBase):
    def __init__(self, inventories: list[Path], config_path: Path, power_stop: str, power_on: str, run_id: str | None):
        listing = multus.load_listing(inventories)
        super().__init__(SPEC, run_id)
        self.inventories, self.config_path = inventories, config_path
        self.config = config = yaml.safe_load(config_path.read_text())
        self.power_stop, self.power_on = power_stop, power_on
        self.nodes, self.first_cp, _ = cluster_from_listing(listing)
        self.cps = group_hosts(listing, "kube_control_plane")
        self.remote = {name: Remote(n, self.run_id, self.log) for name, n in self.nodes.items()}
        self.kubectl = Kubectl(self.remote[self.first_cp])
        self.env = str(listing["_meta"]["hostvars"][self.first_cp]["rfl_inventory"])
        self.layout = multus.layout_from_values(self.env)
        self.values = yaml.safe_load((REPO_ROOT / config["rail_values"]).read_text())
        model = site_model.load(REPO_ROOT / config["site"])
        rail_classes = {c.name for c in model.rail_classes()}
        # The hosts that must be rail hosts, from the site model, so a Node that lost its label shows as a failure.
        self.hosts = sorted(h.name for h in model.hosts.values() if h.node_class in rail_classes)
        self.ns = self.values["namespace"]
        self.resource = f"rdma/{self.values['resourceName']}"
        self.block = int(self.values["perNodeBlockSize"])
        self.rails = self.values["rails"]
        lock = yaml.safe_load(LOCK.read_text())["components"]
        self.image = locked_image(lock, config["image"])
        self.images = {component: locked_image(lock, key) for component, key in COMPONENTS.items()}

    def pod(self, name: str, node: str, command: str, networks: str | None = None, units: int = 0,
            tolerations: list[dict] | None = None) -> dict:
        """A test pod in the rail namespace on node, as user 65534 without privileges. With units it asks for the rail
        resource and holds IPC_LOCK alone, which the device plugin and RDMA memory registration need."""
        caps = {"drop": ["ALL"], "add": ["IPC_LOCK"]} if units else {"drop": ["ALL"]}
        container = {"name": name, "image": self.image, "command": ["sh", "-c", command],
                     "securityContext": {"privileged": False, "allowPrivilegeEscalation": False, "runAsNonRoot": True,
                                         "runAsUser": 65534, "runAsGroup": 65534, "capabilities": caps,
                                         "seccompProfile": {"type": "RuntimeDefault"}}}
        if units:
            container["resources"] = {"requests": {self.resource: str(units)}, "limits": {self.resource: str(units)}}
        meta = {"name": name, "namespace": self.ns, "labels": {RUN_LABEL: self.run_id, RAIL_TEST: name}}
        if networks:
            meta["annotations"] = {multus.NETWORKS: networks}
        spec = {"nodeSelector": {"kubernetes.io/hostname": node}, "terminationGracePeriodSeconds": 1,
                "automountServiceAccountToken": False, "enableServiceLinks": False, "containers": [container]}
        if tolerations:
            spec["tolerations"] = tolerations
        return {"apiVersion": "v1", "kind": "Pod", "metadata": meta, "spec": spec}

    def rail_networks(self) -> str:
        return json.dumps([{"name": r["name"], "interface": r["master"]} for r in self.rails])

    def exec(self, pod: str, command: str, timeout_s: float = 60):
        return self.kubectl(self.kubectl.exec_argv(self.ns, pod, pod, command), timeout_s=timeout_s)

    def power(self, template: str, guest: str, check_id: str, what: str) -> tuple[int, str]:
        argv = shlex.split(template.format(guest=guest))
        start = time.monotonic()
        out = subprocess.run(argv, capture_output=True, text=True)
        self.log.write("control", shlex.join(argv), out.returncode, time.monotonic() - start)
        return out.returncode, self.raw(f"{check_id}/{what}-{guest}.txt", f"$ {shlex.join(argv)}\nexit {out.returncode}\n"
                                                                            f"{out.stdout}{out.stderr}")


def describe(run: Run) -> None:
    guests, tools = [], {}
    for name, remote in run.remote.items():
        done = remote.sh(". /etc/os-release; echo \"$PRETTY_NAME\"; uname -r")
        lines = done.stdout.splitlines() + ["", ""]
        guests.append({"name": name, "os": lines[0], "kernel": lines[1]})
    found, _ = run.kubectl.json(f"-n {run.config['operator_namespace']} get deployments,daemonsets")
    for item in found.get("items", []):
        tools[f"{item['kind']} {item['metadata']['name']} image"] = ", ".join(
            c["image"] for c in item["spec"]["template"]["spec"]["containers"])
    tools["test image"] = run.image
    rfl.set_section(run.dir, "environment", {"guests": guests, "tools": tools})
    resolved = {"run_id": run.run_id, "scenario": SCENARIO, "namespace": run.ns, "resource": run.resource, "block": run.block,
                "rails": run.rails, "selector": run.values["nodeSelector"], "image": run.image, "components": run.images,
                "cni_layout": {**asdict(run.layout), "delegate": run.layout.delegate},
                "inventories": [str(i) for i in run.inventories], "first_control_plane": run.first_cp,
                "power_stop": run.power_stop, "power_on": run.power_on, "config": run.config}
    (run.dir / "resolved-config.yaml").write_text(yaml.safe_dump(resolved, sort_keys=False))
    inputs = [run.config_path, LOCK, REPO_ROOT / run.config["rail_values"], REPO_ROOT / "helm" / "cilium" / f"values-{run.env}.yaml"]
    inputs += [p for i in run.inventories for p in sorted(Path(i).rglob("*.yml") if Path(i).is_dir() else [Path(i)])]
    rfl.add_inputs(run.dir, inputs)
    rfl.set_section(run.dir, "inputs", {"parameters": {"namespace": run.ns, "resource": run.resource, "block": run.block,
                                                        "rdma_pod_node": run.config["rdma_pod_node"],
                                                        "attach_pod_node": run.config["attach_pod_node"],
                                                        "boot_node": run.config["boot_node"]}})


def verdict(problems: list[str], evidence: list[str], detail: str, failure_class: str = "assertion-failed") -> Outcome:
    return failed(failure_class, evidence, "; ".join(problems)) if problems else passed(evidence, detail)


# ---------------------------------------------------------------- checks


def check_cni(run: Run) -> Outcome:
    evidence, problems, _ = multus.cni_state(run, "cni-layout-on-every-node", list(run.nodes))
    return verdict(problems, evidence, f"{len(run.nodes)} nodes: {run.layout.conf_dir} holds {multus.MULTUS_FILE} alone, "
                                       f"delegate {run.layout.delegate}")


def check_operator(run: Run) -> Outcome:
    ns, name = run.config["operator_namespace"], run.config["policy"]
    nodes, nd = run.kubectl.json("get nodes")
    policy, pd = run.kubectl.json(f"get nicclusterpolicies.mellanox.com {name}")
    daemonsets, dd = run.kubectl.json(f"-n {ns} get daemonsets")
    deployments, de = run.kubectl.json(f"-n {ns} get deployments")
    pods, po = run.kubectl.json(f"-n {ns} get pods")
    evidence = [save(run.raw, "operator-runs-three-components-on-rail-hosts/nodes.json", nd, "kubectl get nodes -o json"),
                save(run.raw, "operator-runs-three-components-on-rail-hosts/policy.json", pd, f"kubectl get nicclusterpolicy {name} -o json"),
                save(run.raw, "operator-runs-three-components-on-rail-hosts/daemonsets.json", dd, f"kubectl -n {ns} get daemonsets -o json"),
                save(run.raw, "operator-runs-three-components-on-rail-hosts/deployments.json", de, f"kubectl -n {ns} get deployments -o json"),
                save(run.raw, "operator-runs-three-components-on-rail-hosts/pods.json", po, f"kubectl -n {ns} get pods -o json")]
    carrying = selected(nodes, run.values["nodeSelector"])
    problems = [] if carrying == run.hosts else [f"the Nodes that carry {run.values['nodeSelector']} are {carrying}, the site "
                                                 f"model's rail hosts are {run.hosts}"]
    state = policy.get("status", {}).get("state")
    if state != "ready":
        problems.append(f"NicClusterPolicy {name} state is {state}, want ready")
    found, seen = component_problems(daemonsets, pods, run.images, run.hosts)
    problems += found
    controllers = [d for d in deployments.get("items", []) if any(c["image"] == run.images["nv-ipam"]
                                                                  for c in d["spec"]["template"]["spec"].get("containers", []))]
    if len(controllers) != 1 or not ready(controllers[0].get("status", {}).get("conditions"), "Available"):
        problems.append(f"{len(controllers)} nv-ipam controller Deployments, want 1 Available")
    return verdict(problems, evidence, f"policy {state}; {'; '.join(seen)}; nv-ipam controller Available; rail hosts {run.hosts}")


def check_pools(run: Run) -> Outcome:
    ns = run.config["operator_namespace"]
    pools, done = run.kubectl.json(f"-n {ns} get ippools.nv-ipam.nvidia.com")
    evidence = [save(run.raw, "pools-give-each-rail-host-a-block/ippools.json", done, f"kubectl -n {ns} get ippools -o json")]
    by_name = {p["metadata"]["name"]: p for p in pools.get("items", [])}
    problems, seen = [], []
    for rail in run.rails:
        if rail["name"] not in by_name:
            problems.append(f"no IPPool {ns}/{rail['name']}")
            continue
        found, more = block_problems(by_name[rail["name"]], run.hosts, run.block, rail["subnet"])
        problems += found
        seen += more
    return verdict(problems, evidence, f"blocks of {run.block} for {len(run.hosts)} rail hosts: {'; '.join(seen)}")


def check_attachments(run: Run) -> Outcome:
    nads, done = run.kubectl.json(f"-n {run.ns} get network-attachment-definitions.k8s.cni.cncf.io")
    evidence = [save(run.raw, "rail-attachments-name-ipoib-on-each-rail/nads.json", done,
                     f"kubectl -n {run.ns} get network-attachment-definitions -o json")]
    problems = attachment_problems(nads, run.rails, run.ns)
    return verdict(problems, evidence, f"{len(run.rails)} attachments in {run.ns}, each ipoib on its rail with nv-ipam from its pool")


def check_resource(run: Run) -> Outcome:
    check_id, ns = "rdma-resource-registered-without-a-device", run.config["operator_namespace"]
    nodes, done = run.kubectl.json("get nodes")
    pods, pd = run.kubectl.json(f"-n {ns} get pods -l {PLUGIN_POD_LABEL}")
    evidence = [save(run.raw, f"{check_id}/nodes.json", done, "kubectl get nodes -o json"),
                save(run.raw, f"{check_id}/plugin-pods.json", pd, f"kubectl -n {ns} get pods -l {PLUGIN_POD_LABEL} -o json")]
    problems, offered = resource_problems(nodes, run.resource, run.hosts, 0)
    problems += [f"{name} does not register {run.resource}" for name in run.hosts if name not in offered]
    by_node = {p["spec"].get("nodeName"): p["metadata"]["name"] for p in pods.get("items", []) if not p["metadata"].get("deletionTimestamp")}
    netdevs, exposing = [r["master"] for r in run.rails], set()
    for host in run.hosts:
        if host not in by_node:
            problems.append(f"no device plugin pod on {host}")
            continue
        log = run.kubectl(f"-n {ns} logs {by_node[host]}")
        evidence.append(save(run.raw, f"{check_id}/{host}-plugin.log", log, f"kubectl -n {ns} logs {by_node[host]}"))
        found, line = plugin_log_problems(log.stdout, run.values["resourceName"], run.block, netdevs)
        problems += [f"{host}: {p}" for p in found]
        exposing.add(line)
    return verdict(problems, evidence, f"{len(run.hosts)} rail hosts register {run.resource} at 0, no other node does; each plugin "
                                       f"loaded {run.values['resourceName']} with rdmaHcaMax {run.block} and ifNames {netdevs}, "
                                       f"{', '.join(sorted(exposing))}")


def pod_events(run: Run, name: str) -> tuple[dict, dict, object]:
    """The pod and the events of this pod object only. Events outlive a deleted pod, and an earlier run's pod had the same
    name, so they are selected by the pod's uid."""
    pod, _ = run.kubectl.json(f"-n {run.ns} get pod {name}")
    uid = pod.get("metadata", {}).get("uid") or "none"
    events, done = run.kubectl.json(f"-n {run.ns} get events --field-selector involvedObject.uid={uid}")
    return pod, events, done


def wait_pod(run: Run, check_id: str, name: str, timeout_s: int) -> tuple[dict, list[str], int]:
    t = timeout_s
    waited = run.kubectl(f"-n {run.ns} wait --for=condition=Ready pod/{name} --timeout={t}s", timeout_s=t + 30)
    pod, got = run.kubectl.json(f"-n {run.ns} get pod {name}")
    _, _, events = pod_events(run, name)
    return pod, [save(run.raw, f"{check_id}/wait.txt", waited, f"kubectl wait --for=condition=Ready pod/{name} --timeout={t}s"),
                 save(run.raw, f"{check_id}/pod.json", got, f"kubectl get pod {name} -o json"),
                 save(run.raw, f"{check_id}/events.json", events, f"kubectl get events of pod {name} by its uid -o json")], waited.rc


def check_rdma_pod(run: Run) -> Outcome:
    check_id, node = "rdma-pod-waits-without-a-device", run.config["rdma_pod_node"]
    obj = run.pod(RDMA_POD, node, "exec sleep 86400", units=run.config["rdma_pod_units"])
    applied = run.kubectl("apply", obj)
    evidence = [save(run.raw, f"{check_id}/apply.txt", applied, "kubectl apply -f <pod asking for the rail resource>"),
                run.raw(f"{check_id}/pod-object.json", json.dumps(obj, indent=2) + "\n")]
    if applied.rc != 0:
        return failed("command-failed", evidence, f"kubectl apply exited {applied.rc}", applied.rc)
    deadline = time.monotonic() + run.config["attach_wait_s"]
    while time.monotonic() < deadline:
        pod, events, _ = pod_events(run, RDMA_POD)
        if pod.get("spec", {}).get("nodeName") or any(e.get("reason") == "FailedScheduling" for e in events.get("items", [])):
            break
        time.sleep(3)
    pod, events, ev = pod_events(run, RDMA_POD)
    evidence += [run.raw(f"{check_id}/pod.json", json.dumps(pod, indent=2) + "\n"),
                 save(run.raw, f"{check_id}/events.json", ev, f"kubectl get events of pod {RDMA_POD} by its uid -o json")]
    problems, message = waiting_problems(pod, events, run.resource)
    container = (pod.get("spec", {}).get("containers") or [{}])[0]
    security = container.get("securityContext") or {}
    if security.get("privileged") is not False or (security.get("capabilities") or {}).get("add") != ["IPC_LOCK"]:
        problems.append(f"the pod's security context is {security}, want privileged false and IPC_LOCK as the only added capability")
    return verdict(problems, evidence, f"{RDMA_POD} for {node} asks for 1 {run.resource} with privileged false and IPC_LOCK; "
                                       f"it stays Pending: {message[:200]}")


def check_refusal(run: Run) -> Outcome:
    check_id, node = "ipoib-refuses-the-ethernet-rails", run.config["attach_pod_node"]
    obj = run.pod(ATTACH_POD, node, "exec sleep 86400", networks=run.rail_networks())
    applied = run.kubectl("apply", obj)
    evidence = [save(run.raw, f"{check_id}/apply.txt", applied, "kubectl apply -f <pod with the eight rail networks>"),
                run.raw(f"{check_id}/pod-object.json", json.dumps(obj, indent=2) + "\n")]
    if applied.rc != 0:
        return failed("command-failed", evidence, f"kubectl apply exited {applied.rc}", applied.rc)
    deadline = time.monotonic() + run.config["attach_wait_s"]
    while time.monotonic() < deadline:
        pod, events, _ = pod_events(run, ATTACH_POD)
        if any(e.get("reason") == "FailedCreatePodSandBox" for e in events.get("items", [])):
            break
        time.sleep(3)
    pod, events, ev = pod_events(run, ATTACH_POD)
    rail = run.remote[node].sh(f"ip -d link show {run.rails[0]['master']}")
    evidence += [run.raw(f"{check_id}/pod.json", json.dumps(pod, indent=2) + "\n"),
                 save(run.raw, f"{check_id}/events.json", ev, f"kubectl get events of pod {ATTACH_POD} by its uid -o json"),
                 save(run.raw, f"{check_id}/{node}-{run.rails[0]['master']}.txt", rail, f"ip -d link show {run.rails[0]['master']}")]
    problems, refusals = refusal_problems(pod, events, run.rails)
    return verdict(problems, evidence, f"{ATTACH_POD} on {node} stays {pod.get('status', {}).get('phase')}; "
                                       f"ipoib-cni refused: {refusals[0][:300] if refusals else ''}")


def boot_id(remote: Remote) -> str | None:
    done = remote.sh("cat /proc/sys/kernel/random/boot_id", timeout_s=15)
    return done.stdout.strip() if done.rc == 0 and done.stdout.strip() else None


def check_boot_window(run: Run) -> Outcome:
    """Powers a rail host down cleanly, so Multus removes its file, boots it, and creates a pod there as soon as the new
    boot answers. The verdict is the one of k8s-multus-net1's pod-at-boot-waits-for-multus."""
    check_id, name = "pod-at-boot-waits-for-multus", run.config["boot_node"]
    remote = run.remote[name]
    before = boot_id(remote)
    rc, path = run.power(run.power_stop, name, check_id, "stop")
    evidence = [path]
    if rc != 0:
        return failed("command-failed", evidence, f"powering {name} down exited {rc}", rc)
    starting = subprocess.Popen(shlex.split(run.power_on.format(guest=name)), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True)
    after, boot = None, None
    deadline = time.monotonic() + run.config["reboot_timeout_s"]
    while time.monotonic() < deadline and after is None:
        done = remote.sh("cat /proc/sys/kernel/random/boot_id; awk '/^btime/ {print $2}' /proc/stat", timeout_s=3)
        lines = done.stdout.split()
        if done.rc == 0 and len(lines) == 2 and lines[0] != before:
            after, boot = lines[0], float(lines[1])
        else:
            time.sleep(0.5)
    obj = run.pod(BOOT_POD, name, "exec sleep 86400", tolerations=multus.BOOT_TOLERATIONS)
    applied = run.kubectl("apply", obj) if after else None
    t = run.config["multus_conf_timeout_s"]
    window = remote.sh(multus.window_command(run.layout, t), timeout_s=t + 30) if after else None
    output, _ = starting.communicate(timeout=run.config["reboot_timeout_s"])
    evidence.append(run.raw(f"{check_id}/start-{name}.txt", f"$ {run.power_on.format(guest=name)}\nexit {starting.returncode}\n{output}"))
    evidence.append(run.raw(f"{check_id}/boot-ids.json", json.dumps({"node": name, "before": before, "after": after,
                                                                    "booted_epoch": boot}, indent=2) + "\n"))
    if after is None or boot is None:
        return failed("timeout", evidence, f"no new boot id from {name} within {run.config['reboot_timeout_s']} s")
    evidence += [save(run.raw, f"{check_id}/apply.txt", applied, "kubectl apply -f <pod tolerating NoSchedule>"),
                 save(run.raw, f"{check_id}/{name}-window.txt", window, f"sample NetworkReady and the CNI directory while "
                                                                        f"{multus.MULTUS_FILE} is absent")]
    if applied.rc != 0:
        return failed("command-failed", evidence, f"kubectl apply exited {applied.rc}", applied.rc)
    pod, more, rc = wait_pod(run, check_id, BOOT_POD, run.config["ready_timeout_s"])
    evidence += more
    samples, born = multus.parse_window(window.stdout)
    uid = pod.get("metadata", {}).get("uid") or "no-uid"
    journal = remote.sh(f"sudo journalctl -b -u containerd -o short-unix --no-pager | grep -F {shlex.quote(uid)} | grep -F RunPodSandbox")
    evidence.append(save(run.raw, f"{check_id}/{name}-containerd.txt", journal,
                         f"journalctl -b -u containerd -o short-unix | grep {uid} | grep RunPodSandbox"))
    w = multus.BootWindow(boot, samples, born, multus.sandbox_start(journal.stdout))
    evidence.append(run.raw(f"{check_id}/window.json", json.dumps(asdict(w), indent=2) + "\n"))
    problems = multus.window_problems(w)
    if rc != 0:
        problems.append(f"{BOOT_POD} not Ready within {run.config['ready_timeout_s']} s")
    networks = multus.pod_networks(pod)
    eth0 = networks.get("eth0", {})
    if not (eth0.get("default") and eth0.get("ips")):
        problems.append(f"Multus reports {networks or 'no networks'} for {BOOT_POD}, want eth0 as its default network")
    more, found, _ = multus.cni_state(run, f"{check_id}/after", [name])
    evidence += more
    problems += found
    t = run.config["node_ready_timeout_s"]
    node_ready = run.kubectl(f"wait --for=condition=Ready node/{name} --timeout={t}s", timeout_s=t + 30)
    evidence.append(save(run.raw, f"{check_id}/node-ready.txt", node_ready, f"kubectl wait --for=condition=Ready node/{name}"))
    if node_ready.rc != 0:
        problems.append(f"Node {name} not Ready within {t} s of its boot")
    inside = multus.inside_window(w)
    seen = (f"{name} booted {before[:8] if before else '?'} -> {after[:8]}; pod created by boot+"
            f"{(inside[0].at - boot) if inside else float('nan'):.1f} s; no network in all {len(inside)} samples until "
            f"{multus.MULTUS_FILE} at boot+{(born or boot) - boot:.1f} s; sandbox at boot+{(w.sandbox_started or boot) - boot:.1f} s; "
            f"eth0 {eth0.get('ips')} from {eth0.get('network')}")
    return verdict(problems, evidence, seen)


def check_removed(run: Run) -> Outcome:
    """Deletes the pods of this run, then finds no rail test pod of any run in the rail namespace, so one that an earlier
    run or anything else left there fails the check instead of being swept away."""
    t, mine = run.config["delete_timeout_s"], f"{RUN_LABEL}={run.run_id}"
    deleted = run.kubectl(f"-n {run.ns} delete pod -l {mine} --ignore-not-found --wait --timeout={t}s", timeout_s=t + 30)
    left = run.kubectl(f"-n {run.ns} get pods -l {RAIL_TEST} -o name")
    evidence = [save(run.raw, "rail-test-pods-removed/delete.txt", deleted, f"kubectl -n {run.ns} delete pod -l {mine} --wait"),
                save(run.raw, "rail-test-pods-removed/left.txt", left, f"kubectl -n {run.ns} get pods -l {RAIL_TEST}")]
    if left.rc != 0:
        return failed("command-failed", evidence, f"kubectl get pods exited {left.rc}", left.rc)
    if left.stdout.strip():
        return failed("assertion-failed", evidence, f"still present: {left.stdout.split()}")
    return passed(evidence, f"no pod labeled {RAIL_TEST} in {run.ns}")


def suite(run: Run) -> None:
    run.record("cni-layout-on-every-node", check_cni(run))
    run.record("operator-runs-three-components-on-rail-hosts", check_operator(run))
    run.record("pools-give-each-rail-host-a-block", check_pools(run))
    run.record("rail-attachments-name-ipoib-on-each-rail", check_attachments(run))
    run.record("rdma-resource-registered-without-a-device", check_resource(run))
    try:
        run.record("rdma-pod-waits-without-a-device", check_rdma_pod(run))
        run.record("ipoib-refuses-the-ethernet-rails", check_refusal(run))
        run.record("pod-at-boot-waits-for-multus", check_boot_window(run))
    finally:
        run.record("rail-test-pods-removed", check_removed(run))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run.cel1_rails", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-i", "--inventory", type=Path, action="append", required=True)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--power-stop", required=True, help="command template with {guest} that powers a guest down cleanly")
    parser.add_argument("--power-on", required=True, help="command template with {guest} that boots it again")
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    run = Run(args.inventory, args.config, args.power_stop, args.power_on, args.run_id)
    run.say(f"run {run.run_id} scenario {SCENARIO} in {run.dir}, {len(run.rails)} rails, {len(run.nodes)} nodes")
    try:
        describe(run)
        suite(run)
    except KeyboardInterrupt:
        run.warn("interrupted")
    except Exception as exc:
        run.warn(f"runner error: {exc!r}")
        raise
    finally:
        run.record("no-process-left", check_leftovers(run))
        for remote in run.remote.values():
            remote.remove_dir()
        rfl.set_section(run.dir, "timing", {"wall_clock_seconds": round(time.monotonic() - run.started, 1)})
        code, message = rfl.finalize(run.dir)
        write_summary(run)
        run.say(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
