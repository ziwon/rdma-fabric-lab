"""make k8s-multus-test: a Multus secondary network net1 beside Cilium, recorded through rfl.

    python -m run.multus test -i ansible/inventories/lab -i .lab/inventory.yml
    python -m run.multus render -i ansible/inventories/lab -i .lab/inventory.yml

The NetworkAttachmentDefinition comes from the inventory alone: the namespace lab_k8s_namespace, the
master lab_rdma_nic, which every worker must name alike, and the range lab_address_plan.multus_pool
inside lab_address_plan.rdma. Two ordinary pods on different workers get eth0 from Cilium and net1
from Whereabouts. The run checks both networks, the default route, address bookkeeping across pod
recreation, and the CNI configuration after one worker reboots.
"""

from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import json
import re
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from . import pcap, rfl
from .base import Outcome, RunBase, check_leftovers, failed, passed, write_summary
from .k8s import cluster_from_listing, group_hosts, precondition
from .kube import RUN_LABEL, Kubectl, save
from .remote import Remote

REPO_ROOT = Path(__file__).resolve().parents[2]
TRACK = REPO_ROOT / "tracks" / "a-soft-roce"
SCENARIO = "k8s-multus-net1"
SPEC = TRACK / "scenarios" / f"{SCENARIO}.yaml"
CONFIG = TRACK / "config" / f"{SCENARIO}.yaml"
LOCK = REPO_ROOT / "versions.lock.yaml"
NETWORKS = "k8s.v1.cni.cncf.io/networks"
NETWORK_STATUS = "k8s.v1.cni.cncf.io/network-status"
CNI_DIR = "/etc/cni/net.d"
# containerd loads the first file of the CNI directory with one of these extensions, in name order.
CNI_EXTENSIONS = (".conf", ".conflist", ".json")
MULTUS_FILE = "00-multus.conf"
PSA = "pod-security.kubernetes.io"
SERVER, CLIENT = "net1-server", "net1-client"


class AttachmentError(ValueError):
    pass


# ---------------------------------------------------------------- inventory and objects


@dataclass(frozen=True)
class Attachment:
    """The inventory facts the NetworkAttachmentDefinition is rendered from."""

    namespace: str
    master: str
    cidr: str
    range_start: str
    range_end: str

    def holds(self, ip: str) -> bool:
        address = ipaddress.ip_address(ip)
        return ipaddress.ip_address(self.range_start) <= address <= ipaddress.ip_address(self.range_end)


def attachment_from_listing(listing: dict) -> Attachment:
    hostvars = listing["_meta"]["hostvars"]
    workers = group_hosts(listing, "workers")
    if not workers:
        raise AttachmentError("inventory group workers has no hosts")
    nics = {w: hostvars[w].get("lab_rdma_nic") for w in workers}
    if None in nics.values():
        raise AttachmentError(f"workers without lab_rdma_nic: {', '.join(w for w, n in nics.items() if n is None)}")
    if len(set(nics.values())) != 1:
        raise AttachmentError(
            "lab_rdma_nic differs across the workers (" + ", ".join(f"{w} {n}" for w, n in nics.items()) + "). One "
            "NetworkAttachmentDefinition names one macvlan master for every node, so the lab NIC needs one name on every "
            "worker. The QEMU harness renames it lab0 at first boot. Rename the NICs, then set lab_rdma_nic to that name.")
    first = hostvars[workers[0]]
    missing = [k for k in ("lab_k8s_namespace", "lab_address_plan") if k not in first]
    if missing:
        raise AttachmentError(f"the inventory lacks {', '.join(missing)}")
    plan = first["lab_address_plan"]
    cidr = ipaddress.ip_network(plan["rdma"])
    start, _, end = str(plan["multus_pool"]).partition("-")
    first_ip, last_ip = ipaddress.ip_address(start.strip()), ipaddress.ip_address(end.strip())
    if first_ip not in cidr or last_ip not in cidr or first_ip > last_ip:
        raise AttachmentError(f"multus_pool {plan['multus_pool']} is not a range inside rdma {cidr}")
    taken = [w for w in workers if first_ip <= ipaddress.ip_address(hostvars[w].get("lab_rdma_ip", "0.0.0.0")) <= last_ip]
    if taken:
        raise AttachmentError(f"multus_pool {plan['multus_pool']} holds the lab_rdma_ip of {', '.join(taken)}")
    return Attachment(str(first["lab_k8s_namespace"]), next(iter(nics.values())), str(cidr), str(first_ip), str(last_ip))


def network_attachment(att: Attachment, name: str, kind: str, mode: str, cni_version: str, ipam_extra: dict | None = None) -> dict:
    """The NetworkAttachmentDefinition: a child of the lab NIC with a Whereabouts address and no gateway."""
    config = {
        "cniVersion": cni_version, "name": name, "type": kind, "master": att.master, "mode": mode,
        "ipam": {"type": "whereabouts", "range": att.cidr, "range_start": att.range_start, "range_end": att.range_end,
                 **(ipam_extra or {})},
    }
    return {"apiVersion": "k8s.cni.cncf.io/v1", "kind": "NetworkAttachmentDefinition",
            "metadata": {"name": name, "namespace": att.namespace}, "spec": {"config": json.dumps(config)}}


def test_pod(ns: str, run_id: str, name: str, node: str, image: str, network: str, command: str, port: int | None) -> dict:
    """An ordinary pod at the PodSecurity restricted level with net1 from the network attachment."""
    container = {
        "name": name, "image": image, "command": ["sh", "-c", command],
        "securityContext": {"allowPrivilegeEscalation": False, "runAsNonRoot": True, "runAsUser": 65534, "runAsGroup": 65534,
                            "capabilities": {"drop": ["ALL"]}, "seccompProfile": {"type": "RuntimeDefault"}},
    }
    if port:
        container["ports"] = [{"containerPort": port, "name": "http"}]
    return {"apiVersion": "v1", "kind": "Pod",
            "metadata": {"name": name, "namespace": ns, "labels": {RUN_LABEL: run_id, "role": name},
                         "annotations": {NETWORKS: network}},
            "spec": {"nodeSelector": {"kubernetes.io/hostname": node}, "terminationGracePeriodSeconds": 1,
                     "automountServiceAccountToken": False, "enableServiceLinks": False, "containers": [container]}}


# ---------------------------------------------------------------- parsing


def cni_problems(listing: str, multus: str, delegate: str) -> list[str]:
    """Why containerd would not run Multus with Cilium as its default delegate, from ls -1 of the CNI dir and two files."""
    files = [f for f in listing.split() if f.endswith(CNI_EXTENSIONS)]
    problems = []
    if not files or files[0] != MULTUS_FILE:
        problems.append(f"the first CNI file is {files[0] if files else 'none'}, not {MULTUS_FILE} (files {files})")
    try:
        conf = json.loads(multus)
    except json.JSONDecodeError:
        return problems + [f"{MULTUS_FILE} is not JSON"]
    cluster = str(conf.get("clusterNetwork", ""))
    if conf.get("type") != "multus-shim":
        problems.append(f"{MULTUS_FILE} type is {conf.get('type')}, not multus-shim")
    try:
        target = json.loads(delegate)
    except json.JSONDecodeError:
        return problems + [f"the default delegate {cluster} is not JSON"]
    if [p.get("type") for p in target.get("plugins", [])][:1] != ["cilium-cni"]:
        problems.append(f"the default delegate {cluster} runs {[p.get('type') for p in target.get('plugins', [])]}, not cilium-cni")
    return problems


def pod_networks(pod: dict) -> dict[str, dict]:
    """interface -> {network, ips, default} from the network-status annotation Multus writes."""
    raw = pod.get("metadata", {}).get("annotations", {}).get(NETWORK_STATUS)
    if not raw:
        return {}
    return {n.get("interface"): {"network": n.get("name"), "ips": n.get("ips", []), "default": bool(n.get("default"))}
            for n in json.loads(raw)}


def interface_address(text: str) -> str | None:
    """IPv4 address of `ip -4 addr show dev <name>` output."""
    m = re.search(r"\binet (\d+\.\d+\.\d+\.\d+)/\d+", text)
    return m.group(1) if m else None


def route_problems(routes: str, route_get: str) -> list[str]:
    """Why `ip -4 route` and `ip -4 route get <an address off both networks>` do not keep default traffic on eth0."""
    defaults = [line for line in routes.splitlines() if line.startswith("default")]
    problems = []
    if len(defaults) != 1 or " dev eth0" not in defaults[0]:
        problems.append(f"default routes {defaults}, want one through eth0")
    if " dev eth0 " not in f"{route_get.strip()} ":
        problems.append(f"traffic off both networks leaves as {route_get.strip()!r}, not through eth0")
    return problems


def allocations(pools: dict) -> list[dict]:
    """Every Whereabouts allocation as {ip, podref, ifname, pool}. Offsets count from the network address of the range."""
    out = []
    for pool in pools.get("items", []):
        base = ipaddress.ip_network(pool["spec"]["range"], strict=False).network_address
        for offset, entry in (pool["spec"].get("allocations") or {}).items():
            out.append({"ip": str(base + int(offset)), "podref": entry.get("podref"), "ifname": entry.get("ifname"),
                        "pool": pool["metadata"]["name"]})
    return out


def bookkeeping_problems(pod_ips: dict[str, str], found: list[dict], ns: str, att: Attachment) -> list[str]:
    """Duplicates and leaks between the net1 address of each pod and the Whereabouts allocations of the namespace."""
    problems = []
    ips = [ip for ip in pod_ips.values() if ip is not None]
    twice = sorted({ip for ip in ips if ips.count(ip) > 1})
    if twice:
        problems.append(f"net1 address held by two pods: {twice}")
    every = [a["ip"] for a in found]
    double = sorted({ip for ip in every if every.count(ip) > 1})
    if double:
        problems.append(f"address allocated twice: {double}")
    ours = [a for a in found if str(a["podref"]).startswith(f"{ns}/")]
    for pod, ip in pod_ips.items():
        if ip is None:
            problems.append(f"{pod} has no net1 address")
            continue
        held = [a for a in ours if a["podref"] == f"{ns}/{pod}"]
        if [a["ip"] for a in held] != [ip]:
            problems.append(f"{pod} has net1 {ip}, Whereabouts holds {[a['ip'] for a in held]} for it")
        if not att.holds(ip):
            problems.append(f"{pod} net1 {ip} lies outside {att.range_start}-{att.range_end}")
    stale = [a for a in ours if a["podref"].split("/", 1)[1] not in pod_ips]
    if stale:
        problems.append(f"allocations for pods that do not exist: {[(a['ip'], a['podref']) for a in stale]}")
    return problems


def rfc3339(text: str) -> float:
    return dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp()


# ---------------------------------------------------------------- run


class Run(RunBase):
    def __init__(self, inventories: list[Path], config_path: Path, run_id: str | None):
        listing = load_listing(inventories)
        attachment = attachment_from_listing(listing)
        super().__init__(SPEC, run_id)
        self.inventories = inventories
        self.config_path = config_path
        self.config = config = yaml.safe_load(config_path.read_text())
        self.attachment = attachment
        self.nodes, self.first_cp, self.contract = cluster_from_listing(listing)
        self.remote = {name: Remote(n, self.run_id, self.log) for name, n in self.nodes.items()}
        self.kubectl = Kubectl(self.remote[self.first_cp])
        self.workers = [self.remote[config["server_node"]], self.remote[config["client_node"]]]
        self.ns = self.attachment.namespace
        lock = yaml.safe_load(LOCK.read_text())["components"][config["image"]]
        self.image = f"{lock['ref']}@{lock['value']}"
        self.nad = network_attachment(self.attachment, config["network"], config["secondary"],
                                      config["modes"][config["secondary"]], config["cni_version"], config.get("ipam_extra"))
        self.pod_ips: dict[str, dict[str, str]] = {}

    def pods(self) -> dict[str, tuple[str, str, int | None]]:
        """pod name -> (node, command, port)."""
        port = self.config["http_port"]
        return {SERVER: (self.config["server_node"], f"mkdir -p /tmp/www && hostname > /tmp/www/index.html && "
                                                     f"exec httpd -f -p {port} -h /tmp/www", port),
                CLIENT: (self.config["client_node"], "exec sleep 86400", None)}

    def pod_object(self, name: str) -> dict:
        node, command, port = self.pods()[name]
        return test_pod(self.ns, self.run_id, name, node, self.image, self.config["network"], command, port)

    def exec(self, pod: str, command: str, timeout_s: float = 60):
        return self.kubectl(self.kubectl.exec_argv(self.ns, pod, pod, command), timeout_s=timeout_s)


def held_objects(att: Attachment, nad: dict, config: dict, run_id: str) -> dict:
    """The namespace, the attachment and both test pods as one List, outside any run of this scenario."""
    lock = yaml.safe_load(LOCK.read_text())["components"][config["image"]]
    image = f"{lock['ref']}@{lock['value']}"
    namespace = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": att.namespace, "labels": {
        RUN_LABEL: run_id, f"{PSA}/enforce": "restricted", f"{PSA}/enforce-version": "latest"}}}
    pods = [test_pod(att.namespace, run_id, name, config[node], image, config["network"], command, None)
            for name, node, command in ((SERVER, "server_node", "exec sleep 86400"), (CLIENT, "client_node", "exec sleep 86400"))]
    return {"apiVersion": "v1", "kind": "List", "items": [namespace, nad, *pods]}


def load_listing(inventories: list[Path]) -> dict:
    return json.loads(subprocess.run(["ansible-inventory", "--list", *[a for i in inventories for a in ("-i", str(i))]],
                                     capture_output=True, text=True, check=True).stdout)


def write_resolved_config(run: Run) -> None:
    resolved = {
        "run_id": run.run_id, "scenario": SCENARIO, "namespace": run.ns, "image": run.image,
        "attachment": asdict(run.attachment), "network_attachment": run.nad,
        "inventories": [str(i) for i in run.inventories], "first_control_plane": run.first_cp,
        "nodes": {n: {"node_ip": g.node_ip, "mgmt_nic": g.mgmt_nic, "rdma_nic": g.rdma_nic, "rdma_ip": g.rdma_ip}
                  for n, g in run.nodes.items()},
        "config": run.config,
    }
    (run.dir / "resolved-config.yaml").write_text(yaml.safe_dump(resolved, sort_keys=False))
    inputs = [run.config_path, LOCK] + [p for i in run.inventories for p in sorted(Path(i).rglob("*.yml") if Path(i).is_dir() else [Path(i)])]
    rfl.add_inputs(run.dir, inputs)
    rfl.set_section(run.dir, "inputs", {"parameters": {"namespace": run.ns, "image": run.image, "secondary": run.config["secondary"],
                                                        "master": run.attachment.master,
                                                        "range": f"{run.attachment.range_start}-{run.attachment.range_end}"}})


def describe_environment(run: Run) -> None:
    guests, tools = [], {}
    for name, remote in run.remote.items():
        done = remote.sh(". /etc/os-release; echo \"$PRETTY_NAME\"; uname -r")
        lines = done.stdout.splitlines() + ["", ""]
        guests.append({"name": name, "os": lines[0], "kernel": lines[1]})
    found, _ = run.kubectl.json("-n kube-system get daemonsets")
    for item in found.get("items", []):
        tools[f"{item['metadata']['name']} image"] = ", ".join(c["image"] for c in item["spec"]["template"]["spec"]["containers"])
    tools["test image"] = run.image
    rfl.set_section(run.dir, "environment", {"guests": guests, "tools": tools})


# ---------------------------------------------------------------- checks


def cni_state(run: Run, check_id: str, names: list[str]) -> tuple[list[str], list[str], list[str]]:
    evidence, problems, seen = [], [], []
    for name in names:
        remote = run.remote[name]
        listing = remote.sh(f"sudo ls -1 {CNI_DIR}")
        multus = remote.sh(f"sudo cat {CNI_DIR}/{MULTUS_FILE}")
        try:
            delegate_path = str(json.loads(multus.stdout).get("clusterNetwork", "")).removeprefix("/host")
        except json.JSONDecodeError:
            delegate_path = ""
        if not delegate_path.startswith(f"{CNI_DIR}/"):
            delegate_path = f"{CNI_DIR}/<clusterNetwork {delegate_path!r} outside the CNI directory>"
        delegate = remote.sh(f"sudo cat {shlex.quote(delegate_path)}")
        evidence += [save(run.raw, f"{check_id}/{name}-ls.txt", listing, f"sudo ls -1 {CNI_DIR}"),
                     save(run.raw, f"{check_id}/{name}-{MULTUS_FILE}", multus, f"sudo cat {CNI_DIR}/{MULTUS_FILE}"),
                     save(run.raw, f"{check_id}/{name}-delegate.txt", delegate, f"sudo cat {delegate_path}")]
        found = cni_problems(listing.stdout, multus.stdout, delegate.stdout)
        problems += [f"{name}: {p}" for p in found]
        seen.append(f"{name} {MULTUS_FILE} first, delegate {delegate_path}")
    return evidence, problems, seen


def check_cni(run: Run) -> Outcome:
    evidence, problems, seen = cni_state(run, "multus-config-first", list(run.nodes))
    return failed("assertion-failed", evidence, "; ".join(problems)) if problems else passed(evidence, "; ".join(seen))


def check_attachment(run: Run) -> Outcome:
    existing, _ = run.kubectl.json(f"get namespace {run.ns} --ignore-not-found")
    if existing:
        owner = existing.get("metadata", {}).get("labels", {}).get(RUN_LABEL, "no run")
        evidence = [run.raw("network-attachment-from-inventory/existing-namespace.json", json.dumps(existing, indent=2) + "\n")]
        return failed("environment-blocked", evidence, f"namespace {run.ns} already exists, from {owner}. "
                      f"make cleanup RUN={owner} removes it")
    namespace = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": run.ns, "labels": {
        RUN_LABEL: run.run_id, f"{PSA}/enforce": "restricted", f"{PSA}/enforce-version": "latest"}}}
    applied = run.kubectl("apply", [namespace, run.nad])
    back, done = run.kubectl.json(f"-n {run.ns} get network-attachment-definitions {run.config['network']}")
    evidence = [save(run.raw, "network-attachment-from-inventory/apply.txt", applied, "kubectl apply -f <namespace, nad>"),
                save(run.raw, "network-attachment-from-inventory/nad.json", done, "kubectl get network-attachment-definitions -o json")]
    if applied.rc != 0 or done.rc != 0:
        return failed("command-failed", evidence, f"kubectl apply exited {applied.rc}, get {done.rc}", applied.rc or done.rc)
    config = json.loads(back.get("spec", {}).get("config", "{}"))
    att = run.attachment
    want = {"type": run.config["secondary"], "master": att.master}
    want_ipam = {"type": "whereabouts", "range": att.cidr, "range_start": att.range_start, "range_end": att.range_end}
    problems = [f"{k} is {config.get(k)}, want {v}" for k, v in want.items() if config.get(k) != v]
    problems += [f"ipam.{k} is {config.get('ipam', {}).get(k)}, want {v}" for k, v in want_ipam.items()
                 if config.get("ipam", {}).get(k) != v]
    if problems:
        return failed("assertion-failed", evidence, "; ".join(problems))
    extra = sorted(set(config.get("ipam", {})) - set(want_ipam))
    return passed(evidence, f"{run.config['network']} in {run.ns}: {config['type']} {config.get('mode')} on master {att.master}, "
                            f"Whereabouts {att.range_start}-{att.range_end} in {att.cidr}"
                            + (f", extra IPAM keys {extra} from the config" if extra else ", no gateway and no route"))


def pools(run: Run, rel: str) -> tuple[list[dict], str]:
    found, done = run.kubectl.json("-n kube-system get ippools.whereabouts.cni.cncf.io")
    return allocations(found), save(run.raw, rel, done, "kubectl -n kube-system get ippools.whereabouts.cni.cncf.io -o json")


def start_pods(run: Run, check_id: str, names: list[str]) -> tuple[Outcome | None, list[str]]:
    objects = [run.pod_object(n) for n in names]
    applied = run.kubectl("apply", objects)
    evidence = [save(run.raw, f"{check_id}/apply.txt", applied, "kubectl apply -f <pods>"),
                run.raw(f"{check_id}/objects.json", json.dumps(objects, indent=2) + "\n")]
    if applied.rc != 0:
        return failed("command-failed", evidence, f"kubectl apply exited {applied.rc}", applied.rc), evidence
    t = run.config["ready_timeout_s"]
    waited = run.kubectl(f"-n {run.ns} wait --for=condition=Ready {' '.join(f'pod/{n}' for n in names)} --timeout={t}s",
                         timeout_s=t + 30)
    evidence.append(save(run.raw, f"{check_id}/wait.txt", waited, f"kubectl wait --for=condition=Ready --timeout={t}s"))
    if waited.rc != 0:
        events = run.kubectl(f"-n {run.ns} get events --sort-by=.lastTimestamp")
        evidence.append(save(run.raw, f"{check_id}/events.txt", events, "kubectl get events"))
        return failed("timeout", evidence, f"pods {names} not Ready within {t} s", waited.rc), evidence
    return None, evidence


def read_networks(run: Run, check_id: str, names: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Fills run.pod_ips[pod] = {eth0, net1} from the annotation and checks it against the addresses inside each pod."""
    pods, done = run.kubectl.json(f"-n {run.ns} get pods")
    nodes, _ = run.kubectl.json("get nodes")
    cidrs = {n["metadata"]["name"]: n["spec"].get("podCIDR") for n in nodes.get("items", [])}
    evidence = [save(run.raw, f"{check_id}/pods.json", done, "kubectl get pods -o json")]
    by_name = {p["metadata"]["name"]: p for p in pods.get("items", [])}
    problems, seen = [], []
    for name in names:
        pod = by_name.get(name, {})
        status = pod_networks(pod)
        inside = run.exec(name, "ip -4 addr show dev eth0; ip -4 addr show dev net1")
        evidence.append(save(run.raw, f"{check_id}/{name}-ip-addr.txt", inside, "ip -4 addr show dev eth0; ip -4 addr show dev net1"))
        eth0, _, net1 = inside.stdout.partition(": net1")
        got = {"eth0": interface_address(eth0), "net1": interface_address(net1)}
        eth0_status, net1_status = status.get("eth0", {}), status.get("net1", {})
        node = pod.get("spec", {}).get("nodeName")
        if pod.get("spec", {}).get("hostNetwork"):
            problems.append(f"{name} uses the host network")
        if not eth0_status.get("default") or eth0_status.get("ips") != [got["eth0"]]:
            problems.append(f"{name} eth0: annotation {eth0_status}, inside {got['eth0']}")
        elif node not in cidrs or ipaddress.ip_address(got["eth0"]) not in ipaddress.ip_network(cidrs[node]):
            problems.append(f"{name} eth0 {got['eth0']} lies outside the pod CIDR {cidrs.get(node)} of {node}")
        if net1_status.get("network") != f"{run.ns}/{run.config['network']}" or net1_status.get("ips") != [got["net1"]]:
            problems.append(f"{name} net1: annotation {net1_status}, inside {got['net1']}")
        elif not run.attachment.holds(got["net1"]):
            problems.append(f"{name} net1 {got['net1']} lies outside {run.attachment.range_start}-{run.attachment.range_end}")
        run.pod_ips[name] = got
        seen.append(f"{name} on {node}: eth0 {got['eth0']} from {eth0_status.get('network')}, net1 {got['net1']}")
    return evidence, problems, seen


def check_pods(run: Run) -> Outcome:
    bad, evidence = start_pods(run, "test-pods-ready", list(run.pods()))
    if bad:
        return bad
    pods, done = run.kubectl.json(f"-n {run.ns} get pods")
    evidence.append(save(run.raw, "test-pods-ready/pods.json", done, "kubectl get pods -o json"))
    where = {p["metadata"]["name"]: p["spec"].get("nodeName") for p in pods.get("items", [])}
    wrong = [f"{n} runs on {where.get(n)}, want {node}" for n, (node, _, _) in run.pods().items() if where.get(n) != node]
    if wrong:
        return failed("assertion-failed", evidence, "; ".join(wrong))
    return passed(evidence, ", ".join(f"{n} on {where[n]}" for n in run.pods()) +
                  f", admitted by PodSecurity restricted in {run.ns}")


def check_interfaces(run: Run) -> Outcome:
    evidence, problems, seen = read_networks(run, "pods-have-eth0-and-net1", list(run.pods()))
    return failed("assertion-failed", evidence, "; ".join(problems)) if problems else passed(evidence, "; ".join(seen))


def check_routes(run: Run) -> Outcome:
    """The default route, and where the kernel sends the node address of the first control plane, off both pod networks."""
    probe = run.nodes[run.first_cp].node_ip
    evidence, problems, seen = [], [], []
    for name in run.pods():
        routes = run.exec(name, "ip -4 route")
        get = run.exec(name, f"ip -4 route get {probe}")
        evidence += [save(run.raw, f"default-route-on-eth0/{name}.txt", routes, "ip -4 route"),
                     save(run.raw, f"default-route-on-eth0/{name}-get.txt", get, f"ip -4 route get {probe}")]
        found = route_problems(routes.stdout, get.stdout) if routes.rc == 0 and get.rc == 0 else \
            [f"ip route exited {routes.rc}, ip route get exited {get.rc}"]
        problems += [f"{name}: {p}" for p in found]
        seen.append(f"{name} " + "; ".join(line for line in routes.stdout.splitlines() if line.startswith("default")) +
                    f", {probe} via {get.stdout.split(' dev ')[-1].split()[0] if ' dev ' in get.stdout else '?'}")
    return failed("assertion-failed", evidence, "; ".join(problems)) if problems else passed(evidence, "; ".join(seen))


def fetch(run: Run, check_id: str, ip: str, iface: str) -> Outcome:
    url = f"http://{ip}:{run.config['http_port']}/"
    t = run.config["request_timeout_s"]
    route = run.exec(CLIENT, f"ip -4 route get {ip}")
    evidence = [save(run.raw, f"{check_id}/route.txt", route, f"ip -4 route get {ip}")]
    if f" dev {iface} " not in f"{route.stdout} ":
        return failed("assertion-failed", evidence, f"{CLIENT} routes {ip} as {route.stdout.strip()!r}, not through {iface}")
    for attempt in range(1, run.config["request_attempts"] + 1):
        done = run.exec(CLIENT, f"wget -q -T {t} -O - {url}", timeout_s=t + 30)
        evidence.append(save(run.raw, f"{check_id}/attempt-{attempt}.txt", done, f"wget -q -T {t} -O - {url}"))
        if done.rc == 0 and done.stdout.strip() == SERVER:
            return passed(evidence, f"{CLIENT} fetched {SERVER!r} from {url} through {iface} on attempt {attempt}")
        time.sleep(1)
    return failed("timeout", evidence, f"{url} did not answer {SERVER!r} in {run.config['request_attempts']} attempts")


def capture(run: Run, start: bool) -> dict[str, dict]:
    c, summaries = run.config["capture"], {}
    for remote in run.workers:
        g = remote.guest
        if start:
            # Without immediate mode libpcap hands packets over in batches, and a stop right after the traffic loses the last one.
            remote.start("capture-lab", f"tcpdump --immediate-mode -n -U -i {g.rdma_nic} -s {c['snaplen']} -w capture-lab.pcap "
                                        f"{shlex.quote('net ' + run.attachment.cidr)}", c["max_seconds"], sudo=True)
            continue
        remote.signal("capture-lab", "INT", "tcpdump")
        rc = remote.wait("capture-lab", 15)
        data = remote.read("capture-lab.pcap", binary=True)
        run.raw(f"traffic/{g.name}-lab.pcap", data)
        run.raw(f"traffic/{g.name}-lab.tcpdump.stderr", remote.read("capture-lab.err"))
        try:
            summary = pcap.summarize(data, None, run.attachment.cidr).as_dict()
        except pcap.PcapError as exc:
            summary = {"error": str(exc)}
        summaries[g.name] = {"nic": g.rdma_nic, "tcpdump_rc": rc, **summary}
    if start:
        deadline = time.monotonic() + 15
        for remote in run.workers:
            while "listening on" not in remote.read("capture-lab.err") and time.monotonic() < deadline:
                time.sleep(0.5)
    else:
        run.raw("traffic/summary.json", json.dumps(summaries, indent=2) + "\n")
    return summaries


def check_lab_nic(run: Run, summaries: dict[str, dict]) -> Outcome:
    a, b = run.pod_ips[SERVER]["net1"], run.pod_ips[CLIENT]["net1"]
    want = {f"{a}>{b}", f"{b}>{a}"}
    evidence = ["raw/traffic/summary.json"] + [f"raw/traffic/{n}-lab.pcap" for n in summaries]
    problems, seen = [], []
    for name, s in summaries.items():
        flows = s.get("watch_net_flows", {})
        seen.append(f"{name} {s.get('nic')} {flows.get(f'{a}>{b}', 0)} frames {a}>{b}, {flows.get(f'{b}>{a}', 0)} frames {b}>{a}")
        if "error" in s or not want <= set(flows):
            problems.append(f"{name}: {s.get('error') or f'flows {flows}, want both directions between {a} and {b}'}")
    return failed("assertion-failed", evidence, "; ".join(problems)) if problems else passed(evidence, "; ".join(seen))


def check_bookkeeping(run: Run, check_id: str) -> Outcome:
    found, evidence = pools(run, f"{check_id}/ippools.json")
    ips = {pod: addrs["net1"] for pod, addrs in run.pod_ips.items()}
    problems = bookkeeping_problems(ips, found, run.ns, run.attachment)
    if problems:
        return failed("assertion-failed", [evidence], "; ".join(problems))
    return passed([evidence], f"net1 {ips} each held once by Whereabouts, {len(found)} allocations in all, none twice")


def wait_released(run: Run, check_id: str, names: list[str]) -> tuple[list[str], list[dict]]:
    """Deletes the pods and waits until Whereabouts holds no address for any of them."""
    evidence = []
    deleted = run.kubectl(f"-n {run.ns} delete pod {' '.join(names)} --wait --timeout={run.config['delete_timeout_s']}s",
                          timeout_s=run.config["delete_timeout_s"] + 30)
    evidence.append(save(run.raw, f"{check_id}/delete.txt", deleted, f"kubectl delete pod {' '.join(names)} --wait"))
    refs = {f"{run.ns}/{n}" for n in names}
    deadline = time.monotonic() + run.config["delete_timeout_s"]
    while True:
        found, path = pools(run, f"{check_id}/ippools-after-delete.json")
        held = [a for a in found if a["podref"] in refs]
        if not held or time.monotonic() >= deadline:
            return evidence + [path], held
        time.sleep(2)


def check_recreate(run: Run) -> Outcome:
    names = list(run.pods())
    before = {n: run.pod_ips[n]["net1"] for n in names}
    evidence, held = wait_released(run, "recreate-releases-addresses", names)
    if held:
        return failed("assertion-failed", evidence, f"after deletion Whereabouts still holds {[(a['ip'], a['podref']) for a in held]}")
    bad, started = start_pods(run, "recreate-releases-addresses", names)
    evidence += started
    if bad:
        return Outcome(bad.status, bad.failure_class, bad.exit_code, evidence, bad.detail)
    more, problems, _ = read_networks(run, "recreate-releases-addresses", names)
    found, path = pools(run, "recreate-releases-addresses/ippools-after-recreate.json")
    evidence += more + [path]
    after = {n: run.pod_ips[n]["net1"] for n in names}
    problems += bookkeeping_problems(after, found, run.ns, run.attachment)
    if problems:
        return failed("assertion-failed", evidence, "; ".join(problems))
    return passed(evidence, f"deletion released {before}; the recreated pods got {after}, each held once")


def boot_id(remote: Remote) -> str | None:
    done = remote.sh("cat /proc/sys/kernel/random/boot_id", timeout_s=15)
    return done.stdout.strip() if done.rc == 0 and done.stdout.strip() else None


def reboot(run: Run, name: str) -> tuple[bool, list[str], str, float | None]:
    """Reboots one node and waits until its kubelet reports the new boot and its Multus pod started after it."""
    remote = run.remote[name]
    before = boot_id(remote)
    remote.sh("sudo systemctl reboot", timeout_s=20)
    deadline = time.monotonic() + run.config["reboot_timeout_s"]
    after = None
    while time.monotonic() < deadline and not after:
        current = boot_id(remote)
        after = current if current and current != before else None
        time.sleep(3)
    booted = remote.sh("awk '/^btime/ {print $2}' /proc/stat", timeout_s=15).stdout.strip() if after else ""
    evidence = [run.raw("reboot-keeps-cni-order/boot-ids.json", json.dumps({"node": name, "before": before, "after": after,
                                                                             "booted_epoch": booted}, indent=2) + "\n")]
    if not after:
        return False, evidence, f"no new boot id from {name} within {run.config['reboot_timeout_s']} s", None
    boot = float(booted)
    while time.monotonic() < deadline:
        node, _ = run.kubectl.json(f"get node {name}")
        multus, _ = run.kubectl.json(f"-n kube-system get pods -l name=multus --field-selector spec.nodeName={name}")
        node_ready = node.get("status", {}).get("nodeInfo", {}).get("bootID") == after and any(
            c["type"] == "Ready" and c["status"] == "True" for c in node.get("status", {}).get("conditions", []))
        started = [s.get("state", {}).get("running", {}).get("startedAt") for p in multus.get("items", [])
                   for s in p.get("status", {}).get("containerStatuses", []) if s.get("ready")]
        if node_ready and started and all(t and rfc3339(t) >= boot for t in started):
            evidence.append(run.raw("reboot-keeps-cni-order/multus-pod.json", json.dumps(multus, indent=2) + "\n"))
            return True, evidence, f"{name} booted {before[:8]} -> {after[:8]}, kubelet reports the new boot, Multus restarted", boot
        time.sleep(3)
    return False, evidence, f"{name} rebooted, but its Node or Multus pod did not come back within {run.config['reboot_timeout_s']} s", boot


def check_reboot(run: Run) -> tuple[Outcome, bool]:
    """Multus removes its file when its daemon stops and writes it when the daemon starts, after the pod turns Ready.
    So the first look after the reboot is recorded as it is, and the check waits a bounded time for the settled order."""
    name = run.config["reboot_node"]
    pods_there = [n for n, (node, _, _) in run.pods().items() if node == name]
    evidence, held = wait_released(run, "reboot-keeps-cni-order", pods_there)
    if held:
        return failed("assertion-failed", evidence, f"Whereabouts still holds {held} before the reboot"), False
    ok, more, detail, boot = reboot(run, name)
    evidence += more
    if not ok:
        return failed("timeout", evidence, detail), False
    first, first_problems, _ = cni_state(run, "reboot-keeps-cni-order/at-multus-ready", [name])
    evidence += first
    deadline = time.monotonic() + run.config["cni_settle_timeout_s"]
    while True:
        settled, problems, seen = cni_state(run, "reboot-keeps-cni-order/settled", [name])
        if not problems or time.monotonic() >= deadline:
            break
        time.sleep(1)
    evidence += settled
    if problems:
        return failed("assertion-failed", evidence, f"not within {run.config['cni_settle_timeout_s']} s: " + "; ".join(problems)), True
    stat = run.remote[name].sh(f"sudo stat -c %Y {CNI_DIR}/{MULTUS_FILE}")
    evidence.append(save(run.raw, "reboot-keeps-cni-order/multus-conf-mtime.txt", stat, f"sudo stat -c %Y {CNI_DIR}/{MULTUS_FILE}"))
    gap = float(stat.stdout.strip()) - boot if stat.rc == 0 and stat.stdout.strip().isdigit() else None
    run.metrics.append({"name": "reboot.multus_conf_absent_after_boot_s", "value": gap, "unit": "s",
                        "absent_reason": None if gap is not None else "stat of the Multus file failed",
                        "source": evidence[-1], "labels": {"node": name}})
    window = f"{MULTUS_FILE} written {gap:.0f} s after boot" if gap is not None else "write time unknown"
    at_ready = "absent" if first_problems else "present"
    return passed(evidence, f"{detail}; at Multus Ready the file was {at_ready}; {window}; {'; '.join(seen)}"), True


def check_after_reboot(run: Run) -> Outcome:
    name = run.config["reboot_node"]
    pods_there = [n for n, (node, _, _) in run.pods().items() if node == name]
    bad, evidence = start_pods(run, "new-pod-after-reboot-gets-net1", pods_there)
    if bad:
        return bad
    more, problems, seen = read_networks(run, "new-pod-after-reboot-gets-net1", pods_there)
    evidence += more
    found, path = pools(run, "new-pod-after-reboot-gets-net1/ippools.json")
    evidence.append(path)
    problems += bookkeeping_problems({n: run.pod_ips[n]["net1"] for n in run.pods()}, found, run.ns, run.attachment)
    if not problems:
        reach = fetch(run, "new-pod-after-reboot-gets-net1/net1", run.pod_ips[SERVER]["net1"], "net1")
        evidence += reach.evidence
        if reach.status != "passed":
            problems.append(reach.detail or "net1 request failed")
    if problems:
        return failed("assertion-failed", evidence, "; ".join(problems))
    return passed(evidence, "; ".join(seen) + f"; {CLIENT} reached {SERVER} over net1 again")


def check_namespace_removed(run: Run) -> Outcome:
    t = run.config["delete_timeout_s"]
    deleted = run.kubectl(f"delete namespace {run.ns} --ignore-not-found --wait --timeout={t}s", timeout_s=t + 30)
    left = run.kubectl(f"get namespace -l {RUN_LABEL}={run.run_id} -o name")
    objects = run.kubectl(f"get pods -A -l {RUN_LABEL}={run.run_id} -o name")
    found, path = pools(run, "test-namespace-removed/ippools.json")
    held = [a for a in found if str(a["podref"]).startswith(f"{run.ns}/")]
    evidence = [save(run.raw, "test-namespace-removed/delete.txt", deleted, f"kubectl delete namespace {run.ns} --wait"),
                save(run.raw, "test-namespace-removed/left.txt", left, f"kubectl get namespace -l {RUN_LABEL}={run.run_id}"),
                save(run.raw, "test-namespace-removed/pods.txt", objects, f"kubectl get pods -A -l {RUN_LABEL}={run.run_id}"), path]
    if left.rc != 0 or objects.rc != 0:
        return failed("command-failed", evidence, f"kubectl get exited {left.rc}, {objects.rc}")
    if left.stdout.strip() or objects.stdout.strip() or held:
        return failed("assertion-failed", evidence, f"still present: {(left.stdout + objects.stdout).split()}, allocations {held}")
    return passed(evidence, f"namespace {run.ns}, its pods and every Whereabouts allocation of it are gone")


STEPS = ("network-attachment-from-inventory", "test-pods-ready", "pods-have-eth0-and-net1", "default-route-on-eth0",
         "eth0-cross-node", "net1-cross-node", "net1-on-lab-nic", "no-duplicate-addresses", "recreate-releases-addresses",
         "reboot-keeps-cni-order", "new-pod-after-reboot-gets-net1")


GATES = (("network-attachment-from-inventory", check_attachment), ("test-pods-ready", check_pods),
         ("pods-have-eth0-and-net1", check_interfaces))


def suite(run: Run) -> None:
    run.record("multus-config-first", check_cni(run))
    for check_id, step in GATES:
        outcome = step(run)
        run.record(check_id, outcome)
        if outcome.status != "passed":
            for later in STEPS[STEPS.index(check_id) + 1:]:
                run.record(later, precondition(check_id))
            return
    run.record("default-route-on-eth0", check_routes(run))
    capture(run, start=True)
    run.record("eth0-cross-node", fetch(run, "eth0-cross-node", run.pod_ips[SERVER]["eth0"], "eth0"))
    run.record("net1-cross-node", fetch(run, "net1-cross-node", run.pod_ips[SERVER]["net1"], "net1"))
    run.record("net1-on-lab-nic", check_lab_nic(run, capture(run, start=False)))
    run.record("no-duplicate-addresses", check_bookkeeping(run, "no-duplicate-addresses"))
    run.record("recreate-releases-addresses", check_recreate(run))
    outcome, rebooted = check_reboot(run)
    run.record("reboot-keeps-cni-order", outcome)
    run.record("new-pod-after-reboot-gets-net1", check_after_reboot(run) if rebooted else precondition("reboot-keeps-cni-order"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run.multus", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["test", "render"])
    parser.add_argument("-i", "--inventory", type=Path, action="append", required=True)
    parser.add_argument("--run-id", help="share one run id across scenarios")
    parser.add_argument("--config", type=Path, default=CONFIG, help="another parameter file, as a deliberate break uses")
    parser.add_argument("--with-pods", metavar="RUN_ID", help="render: a List of the namespace, the attachment and both test "
                        "pods labeled with this run id, so other scenarios can run while net1 sits on the lab NIC")
    args = parser.parse_args(argv)
    config = yaml.safe_load(args.config.read_text())
    if args.command == "render":
        try:
            att = attachment_from_listing(load_listing(args.inventory))
        except AttachmentError as exc:
            print(f"run.multus: {exc}", file=sys.stderr)
            return 2
        nad = network_attachment(att, config["network"], config["secondary"], config["modes"][config["secondary"]],
                                 config["cni_version"], config.get("ipam_extra"))
        if args.with_pods:
            print(json.dumps(held_objects(att, nad, config, args.with_pods), indent=2))
        else:
            print(yaml.safe_dump(nad, sort_keys=False), end="")
        return 0

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    try:
        run = Run(args.inventory, args.config, args.run_id)
    except AttachmentError as exc:
        print(f"run.multus: {exc}", file=sys.stderr)
        return 2
    run.say(f"run {run.run_id} scenario {SCENARIO} in {run.dir}, namespace {run.ns}")
    try:
        write_resolved_config(run)
        describe_environment(run)
        suite(run)
    except KeyboardInterrupt:
        run.warn("interrupted, removing this run's namespace and guest processes")
    except Exception as exc:
        run.warn(f"runner error, removing this run's namespace and guest processes: {exc!r}")
        raise
    finally:
        run.record("test-namespace-removed", check_namespace_removed(run))
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
