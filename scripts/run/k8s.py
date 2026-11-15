"""make k8s-test: the Kubernetes baseline of the lab cluster, recorded through the rfl CLI.

    python -m run.k8s -i ansible/inventories/lab -i .lab/inventory.yml

kubectl runs on the first control plane against its admin.conf, so the control node needs neither
kubectl nor a kubeconfig. Every object of a run lives in one namespace named after the run and
labeled with its id, and the run deletes that namespace at the end.
"""

from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import pcap, rfl
from .base import Outcome, RunBase, check_leftovers, failed, passed, write_summary
from .inventory import ssh_argv
from .remote import Done, Remote

REPO_ROOT = Path(__file__).resolve().parents[2]
TRACK = REPO_ROOT / "tracks" / "a-soft-roce"
SPEC = TRACK / "scenarios" / "k8s-baseline.yaml"
CONFIG = TRACK / "config" / "k8s-baseline.yaml"
LOCK = REPO_ROOT / "versions.lock.yaml"
KUBECTL = "sudo kubectl --kubeconfig /etc/kubernetes/admin.conf"
RUN_LABEL = "rdma-fabric-lab/run"
SERVER, CLIENT, SERVICE = "server", "client", "server"


@dataclass(frozen=True)
class Node:
    name: str
    ssh: tuple[str, ...]
    node_ip: str
    mgmt_nic: str | None
    rdma_nic: str | None
    rdma_ip: str | None


# ---------------------------------------------------------------- inventory


def group_hosts(listing: dict, group: str) -> list[str]:
    """Hosts of a group of ansible-inventory --list, children included, in listing order."""
    node = listing.get(group, {})
    hosts = list(node.get("hosts", []))
    for child in node.get("children", []):
        hosts += [h for h in group_hosts(listing, child) if h not in hosts]
    return hosts


def cluster_from_listing(listing: dict) -> tuple[dict[str, Node], str, dict]:
    """(nodes of k8s_cluster, the first control plane, the contract variables of that host)."""
    hostvars = listing["_meta"]["hostvars"]
    names = group_hosts(listing, "k8s_cluster")
    if not names:
        raise ValueError("inventory group k8s_cluster has no hosts")
    missing = [n for n in names if "k8s_node_ip" not in hostvars[n]]
    if missing:
        raise ValueError(f"hosts without k8s_node_ip: {', '.join(missing)}")
    nodes = {
        n: Node(n, ssh_argv(hostvars[n]), str(hostvars[n]["k8s_node_ip"]), hostvars[n].get("lab_mgmt_nic"),
                hostvars[n].get("lab_rdma_nic"), hostvars[n].get("lab_rdma_ip"))
        for n in names
    }
    first = group_hosts(listing, "kube_control_plane")[0]
    contract = {k: hostvars[first][k] for k in ("k8s_pod_cidr", "k8s_service_cidr") if k in hostvars[first]}
    return nodes, first, contract


# ---------------------------------------------------------------- objects


def namespace_of(run_id: str) -> str:
    return f"rfl-{run_id.lower()}"


def test_objects(ns: str, run_id: str, image: str, config: dict) -> list[dict]:
    """Namespace, the server and client pods on their workers, and the ClusterIP Service of the server."""
    labels = {RUN_LABEL: run_id}

    def pod(name: str, node: str, command: str, ports: list[dict]) -> dict:
        return {
            "apiVersion": "v1", "kind": "Pod",
            "metadata": {"name": name, "namespace": ns, "labels": labels | {"role": name}},
            "spec": {
                "nodeSelector": {"kubernetes.io/hostname": node},
                "terminationGracePeriodSeconds": 1,
                "containers": [{
                    "name": name, "image": image, "command": ["sh", "-c", command], "ports": ports,
                    "securityContext": {"allowPrivilegeEscalation": False, "runAsNonRoot": True, "runAsUser": 65534,
                                        "capabilities": {"drop": ["ALL"]}},
                }],
            },
        }

    port = config["http_port"]
    return [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": ns, "labels": labels}},
        pod(SERVER, config["server_node"],
            f"mkdir -p /tmp/www && hostname > /tmp/www/index.html && exec httpd -f -p {port} -h /tmp/www",
            [{"containerPort": port, "name": "http"}]),
        pod(CLIENT, config["client_node"], "exec sleep 86400", []),
        {"apiVersion": "v1", "kind": "Service", "metadata": {"name": SERVICE, "namespace": ns, "labels": labels},
         "spec": {"type": "ClusterIP", "selector": {"role": SERVER},
                  "ports": [{"port": config["service_port"], "targetPort": port, "protocol": "TCP"}]}},
    ]


def deny_policy(ns: str, run_id: str) -> dict:
    return {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
            "metadata": {"name": "deny-ingress", "namespace": ns, "labels": {RUN_LABEL: run_id}},
            "spec": {"podSelector": {}, "policyTypes": ["Ingress"]}}


def allow_policy(ns: str, run_id: str, port: int) -> dict:
    return {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
            "metadata": {"name": "allow-client", "namespace": ns, "labels": {RUN_LABEL: run_id}},
            "spec": {"podSelector": {"matchLabels": {"role": SERVER}}, "policyTypes": ["Ingress"],
                     "ingress": [{"from": [{"podSelector": {"matchLabels": {"role": CLIENT}}}],
                                  "ports": [{"port": port, "protocol": "TCP"}]}]}}


# ---------------------------------------------------------------- parsing


def ready(conditions: list[dict], kind: str = "Ready") -> bool:
    return any(c.get("type") == kind and c.get("status") == "True" for c in conditions or [])


def node_problems(nodes: dict, expected: dict[str, str]) -> tuple[list[str], list[str]]:
    """(problems, observations) of kubectl get nodes -o json against inventory name -> k8s_node_ip."""
    by_name = {n["metadata"]["name"]: n for n in nodes.get("items", [])}
    problems, seen = [], []
    for name, ip in expected.items():
        node = by_name.get(name)
        if node is None:
            problems.append(f"{name} is not a Node")
            continue
        internal = [a["address"] for a in node["status"].get("addresses", []) if a["type"] == "InternalIP"]
        is_ready = ready(node["status"].get("conditions"))
        seen.append(f"{name} {'Ready' if is_ready else 'NotReady'} {','.join(internal)}")
        if not is_ready:
            problems.append(f"{name} is not Ready")
        if internal != [ip]:
            problems.append(f"{name} has InternalIP {internal}, want {ip}")
    extra = sorted(set(by_name) - set(expected))
    if extra:
        problems.append(f"Nodes outside the inventory: {', '.join(extra)}")
    return problems, seen


def cilium_problems(pods: dict, node_names: list[str]) -> tuple[list[str], list[str]]:
    """(problems, observations) of the Cilium agent pods: one Running pod with ready containers per node."""
    by_node: dict[str, list[dict]] = {}
    for p in pods.get("items", []):
        by_node.setdefault(p["spec"].get("nodeName", ""), []).append(p)
    problems, seen = [], []
    for name in node_names:
        agents = by_node.get(name, [])
        good = [p for p in agents if p["status"].get("phase") == "Running" and not p["metadata"].get("deletionTimestamp")
                and all(c.get("ready") for c in p["status"].get("containerStatuses", []))
                and p["status"].get("containerStatuses")]
        seen.append(f"{name} {','.join(p['metadata']['name'] for p in good) or 'none ready'}")
        if len(good) != 1:
            states = [f"{p['metadata']['name']} {p['status'].get('phase')}" for p in agents]
            problems.append(f"{name} has {len(good)} ready Cilium agents ({', '.join(states) or 'no agent pod'})")
    return problems, seen


def pod_problems(pods: dict, wanted: dict[str, str], node_cidrs: dict[str, str]) -> tuple[list[str], dict[str, str]]:
    """(problems, pod name -> IP) for test pods that must run Ready on given nodes inside their pod CIDRs."""
    by_name = {p["metadata"]["name"]: p for p in pods.get("items", [])}
    problems, ips = [], {}
    for name, node in wanted.items():
        p = by_name.get(name)
        if p is None:
            problems.append(f"pod {name} does not exist")
            continue
        status = p["status"]
        where, ip = p["spec"].get("nodeName"), status.get("podIP")
        if not ready(status.get("conditions")):
            waiting = [c.get("state", {}).get("waiting", {}).get("reason") for c in status.get("containerStatuses", [])]
            problems.append(f"pod {name} is {status.get('phase')} and not Ready ({', '.join(filter(None, waiting)) or 'no reason'})")
        if where != node:
            problems.append(f"pod {name} runs on {where}, want {node}")
        if ip and where in node_cidrs and ipaddress.ip_address(ip) not in ipaddress.ip_network(node_cidrs[where]):
            problems.append(f"pod {name} has {ip}, outside {where}'s pod CIDR {node_cidrs[where]}")
        if ip:
            ips[name] = ip
    return problems, ips


def nslookup_addresses(text: str, name: str) -> list[str]:
    """Addresses busybox nslookup printed for name. The server's own address comes first and is skipped."""
    found, take = [], False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Name:"):
            take = line.split(None, 1)[1].rstrip(".") == name.rstrip(".") if len(line.split()) > 1 else False
        elif take and line.startswith("Address"):
            found.append(line.split()[-1])
    return found


def tunnel_problems(summary: dict, local_ip: str, peer_ip: str, client_ip: str, server_ip: str) -> list[str]:
    """Why a management capture fails to show pod traffic tunnelled between the two nodes, or nothing."""
    if "error" in summary:
        return [summary["error"]]
    outer, inner = summary.get("tunnel_flows", {}), summary.get("tunnel_inner_flows", {})
    want_outer = {f"{local_ip}>{peer_ip}", f"{peer_ip}>{local_ip}"}
    want_inner = {f"{client_ip}>{server_ip}", f"{server_ip}>{client_ip}"}
    problems = []
    if not want_outer <= set(outer):
        problems.append(f"VXLAN between node IPs {sorted(set(outer) & want_outer)}, want both of {sorted(want_outer)}")
    if not want_inner <= set(inner):
        problems.append(f"pod flows inside VXLAN {sorted(set(inner) & want_inner)}, want both of {sorted(want_inner)}")
    return problems


def lab_nic_problems(summary: dict) -> list[str]:
    if "error" in summary:
        return [summary["error"]]
    problems = []
    if summary.get("tunnel_frames"):
        problems.append(f"{summary['tunnel_frames']} VXLAN frames")
    if summary.get("watch_net_frames"):
        problems.append(f"{summary['watch_net_frames']} frames to or from the pod CIDR")
    if not summary.get("icmp_echo_frames"):
        problems.append("no marker ping, so the capture may not have been live")
    return problems


# ---------------------------------------------------------------- run


class Run(RunBase):
    def __init__(self, inventories: list[Path], config: dict, run_id: str | None):
        super().__init__(SPEC, run_id)
        self.inventories = inventories
        self.config = config
        listing = json.loads(subprocess.run(["ansible-inventory", "--list", *[a for i in inventories for a in ("-i", str(i))]],
                                            capture_output=True, text=True, check=True).stdout)
        self.nodes, self.first_cp, self.contract = cluster_from_listing(listing)
        self.remote = {name: Remote(n, self.run_id, self.log) for name, n in self.nodes.items()}
        self.cp = self.remote[self.first_cp]
        self.workers = [self.remote[config["server_node"]], self.remote[config["client_node"]]]
        self.ns = namespace_of(self.run_id)
        lock = yaml.safe_load(LOCK.read_text())["components"][config["image"]]
        self.image = f"{lock['ref']}@{lock['value']}"
        self.pod_ips: dict[str, str] = {}
        self.tunnel_port: int | None = None

    def kubectl(self, args: str, manifest: list[dict] | dict | None = None, timeout_s: float = 60) -> Done:
        if manifest is None:
            return self.cp.sh(f"{KUBECTL} {args}", timeout_s=timeout_s)
        docs = manifest if isinstance(manifest, list) else [manifest]
        payload = base64.b64encode(json.dumps({"apiVersion": "v1", "kind": "List", "items": docs}).encode()).decode()
        return self.cp.sh(f"echo {payload} | base64 -d | {KUBECTL} {args} -f -", timeout_s=timeout_s)

    def kubectl_json(self, args: str) -> tuple[dict, Done]:
        done = self.kubectl(f"{args} -o json")
        try:
            return json.loads(done.stdout or "{}"), done
        except json.JSONDecodeError:
            return {}, done

    def client_get(self, url: str) -> Done:
        t = self.config["request_timeout_s"]
        return self.kubectl(f"-n {self.ns} exec {CLIENT} -- wget -q -T {t} -O - {shlex.quote(url)}", timeout_s=t + 30)

    def server_url(self) -> str:
        return f"http://{self.pod_ips[SERVER]}:{self.config['http_port']}/"

    def service_url(self) -> str:
        return f"http://{SERVICE}.{self.ns}.svc.cluster.local:{self.config['service_port']}/"


def save(run: Run, rel: str, done: Done, command: str) -> str:
    return run.raw(rel, f"$ {command}\nexit {done.rc}\n--- stdout\n{done.stdout}--- stderr\n{done.stderr}")


def describe_environment(run: Run) -> None:
    guests, tools = [], {}
    for name, remote in run.remote.items():
        done = remote.sh(". /etc/os-release; echo \"$PRETTY_NAME\"; uname -r; kubelet --version; "
                         "/usr/local/bin/containerd --version | cut -d' ' -f3")
        run.raw(f"environment/{name}.txt", done.stdout + done.stderr)
        lines = done.stdout.splitlines() + ["", "", "", ""]
        guests.append({"name": name, "os": lines[0], "kernel": lines[1]})
        tools[f"{name} kubelet"] = lines[2].replace("Kubernetes ", "")
        tools[f"{name} containerd"] = lines[3]
    ds, _ = run.kubectl_json("-n kube-system get daemonset cilium")
    images = [c["image"] for c in ds.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])]
    tools["cilium-agent image"] = ", ".join(images) or "not found"
    tools["test image"] = run.image
    rfl.set_section(run.dir, "environment", {"guests": guests, "tools": tools})


def write_resolved_config(run: Run) -> None:
    resolved = {
        "run_id": run.run_id, "scenario": "k8s-baseline", "namespace": run.ns, "image": run.image,
        "inventories": [str(i) for i in run.inventories], "first_control_plane": run.first_cp, "contract": run.contract,
        "nodes": {n: {"node_ip": g.node_ip, "mgmt_nic": g.mgmt_nic, "rdma_nic": g.rdma_nic, "rdma_ip": g.rdma_ip}
                  for n, g in run.nodes.items()},
        "config": run.config,
    }
    (run.dir / "resolved-config.yaml").write_text(yaml.safe_dump(resolved, sort_keys=False))
    inputs = [CONFIG, LOCK] + [p for i in run.inventories for p in sorted(Path(i).rglob("*.yml") if Path(i).is_dir() else [Path(i)])]
    rfl.add_inputs(run.dir, inputs)
    rfl.set_section(run.dir, "inputs", {"parameters": {"namespace": run.ns, "image": run.image,
                                                        "server_node": run.config["server_node"],
                                                        "client_node": run.config["client_node"]}})


# ---------------------------------------------------------------- checks


def check_nodes(run: Run) -> Outcome:
    nodes, done = run.kubectl_json("get nodes")
    evidence = [save(run, "nodes-ready/get-nodes.json", done, "kubectl get nodes -o json")]
    problems, seen = node_problems(nodes, {n: g.node_ip for n, g in run.nodes.items()})
    if done.rc != 0:
        return failed("command-failed", evidence, f"kubectl get nodes exited {done.rc}", done.rc)
    return failed("assertion-failed", evidence, "; ".join(problems)) if problems else passed(evidence, "; ".join(seen))


def check_cilium(run: Run) -> Outcome:
    pods, done = run.kubectl_json("-n kube-system get pods -l k8s-app=cilium")
    evidence = [save(run, "cilium-agents-ready/pods.json", done, "kubectl -n kube-system get pods -l k8s-app=cilium -o json")]
    status = run.kubectl("-n kube-system exec ds/cilium -c cilium-agent -- cilium-dbg status --brief")
    evidence.append(save(run, "cilium-agents-ready/status-brief.txt", status, "cilium-dbg status --brief"))
    problems, seen = cilium_problems(pods, list(run.nodes))
    if done.rc != 0:
        return failed("command-failed", evidence, f"kubectl get pods exited {done.rc}", done.rc)
    return failed("assertion-failed", evidence, "; ".join(problems)) if problems else passed(evidence, "; ".join(seen))


def check_test_pods(run: Run) -> Outcome:
    objects = test_objects(run.ns, run.run_id, run.image, run.config)
    applied = run.kubectl("apply", objects)
    evidence = [save(run, "test-pods-ready/apply.txt", applied, "kubectl apply -f <namespace, pods, service>"),
                run.raw("test-pods-ready/objects.json", json.dumps(objects, indent=2) + "\n")]
    if applied.rc != 0:
        return failed("command-failed", evidence, f"kubectl apply exited {applied.rc}", applied.rc)
    t = run.config["ready_timeout_s"]
    waited = run.kubectl(f"-n {run.ns} wait --for=condition=Ready pod --all --timeout={t}s", timeout_s=t + 30)
    evidence.append(save(run, "test-pods-ready/wait.txt", waited, f"kubectl wait --for=condition=Ready pod --all --timeout={t}s"))
    pods, done = run.kubectl_json(f"-n {run.ns} get pods")
    nodes, _ = run.kubectl_json("get nodes")
    evidence.append(save(run, "test-pods-ready/pods.json", done, "kubectl get pods -o json"))
    cidrs = {n["metadata"]["name"]: n["spec"].get("podCIDR", "") for n in nodes.get("items", []) if n["spec"].get("podCIDR")}
    problems, run.pod_ips = pod_problems(pods, {SERVER: run.config["server_node"], CLIENT: run.config["client_node"]}, cidrs)
    if problems:
        return failed("timeout" if waited.rc != 0 else "assertion-failed", evidence, "; ".join(problems), waited.rc)
    return passed(evidence, f"server {run.pod_ips[SERVER]} on {run.config['server_node']}, "
                            f"client {run.pod_ips[CLIENT]} on {run.config['client_node']}")


def fetch_until(run: Run, check_id: str, url: str, want_ok: bool, deadline_s: float,
                max_attempts: int | None = None) -> tuple[bool, list[str], str]:
    """Repeat the client request until it succeeds (want_ok) or fails twice in a row, within deadline_s and max_attempts."""
    evidence, attempts, misses = [], 0, 0
    deadline = time.monotonic() + deadline_s
    while True:
        attempts += 1
        done = run.client_get(url)
        evidence.append(save(run, f"{check_id}/attempt-{attempts}.txt", done, f"wget -T {run.config['request_timeout_s']} {url}"))
        ok = done.rc == 0 and done.stdout.strip() == SERVER
        misses = 0 if ok else misses + 1
        if (want_ok and ok) or (not want_ok and misses >= 2):
            return True, evidence, f"after {attempts} attempts, last exit {done.rc} {(done.stdout or done.stderr).strip()[:80]!r}"
        if time.monotonic() >= deadline or attempts == max_attempts:
            return False, evidence, f"not after {attempts} attempts, last exit {done.rc} " \
                                    f"{(done.stdout or done.stderr).strip()[:80]!r}"
        time.sleep(1)


def check_fetch(run: Run, check_id: str, url: str) -> Outcome:
    ok, evidence, detail = fetch_until(run, check_id, url, True, run.config["policy_timeout_s"], run.config["request_attempts"])
    return passed(evidence, f"{url} returned {SERVER!r} {detail}") if ok else failed("timeout", evidence, f"{url} {detail}")


def check_dns(run: Run) -> Outcome:
    svc, _ = run.kubectl_json(f"-n {run.ns} get service {SERVICE}")
    want = {f"{SERVICE}.{run.ns}.svc.cluster.local": svc.get("spec", {}).get("clusterIP"),
            "kubernetes.default.svc.cluster.local": str(ipaddress.ip_network(run.contract["k8s_service_cidr"])[1])}
    evidence, problems, seen = [], [], []
    for name, address in want.items():
        done = run.kubectl(f"-n {run.ns} exec {CLIENT} -- nslookup {name}", timeout_s=40)
        evidence.append(save(run, f"cluster-dns/{name}.txt", done, f"nslookup {name}"))
        got = nslookup_addresses(done.stdout, name)
        seen.append(f"{name} -> {', '.join(got) or 'nothing'}")
        if address not in got:
            problems.append(f"{name} resolved to {got or 'nothing'} (exit {done.rc}), want {address}")
    return failed("assertion-failed", evidence, "; ".join(problems)) if problems else passed(evidence, "; ".join(seen))


def check_policy(run: Run, check_id: str, policy: dict, want_ok: bool) -> Outcome:
    applied = run.kubectl("apply", policy)
    evidence = [save(run, f"{check_id}/apply.txt", applied, f"kubectl apply -f <NetworkPolicy {policy['metadata']['name']}>"),
                run.raw(f"{check_id}/policy.json", json.dumps(policy, indent=2) + "\n")]
    if applied.rc != 0:
        return failed("command-failed", evidence, f"kubectl apply exited {applied.rc}", applied.rc)
    ok, attempts, detail = fetch_until(run, check_id, run.service_url(), want_ok, run.config["policy_timeout_s"])
    verb = "succeeded" if want_ok else "was blocked"
    return passed(evidence + attempts, f"the request {verb} {detail}") if ok else \
        failed("assertion-failed", evidence + attempts, f"the request never {verb.replace('was ', 'got ')} {detail}")


def precondition(needs: str) -> Outcome:
    detail = f"not attempted: {needs} did not pass, so the result would not mean what the check says"
    return Outcome("not_run", "not-attempted", None, [], detail)


# ---------------------------------------------------------------- captures


def read_tunnel_port(run: Run) -> tuple[int | None, str]:
    worker = run.workers[0]
    done = worker.sh("ip -d -j link show cilium_vxlan")
    path = run.raw("vxlan-on-mgmt-nic/cilium_vxlan.json", done.stdout or done.stderr)
    try:
        port = json.loads(done.stdout)[0]["linkinfo"]["info_data"]["port"]
    except (json.JSONDecodeError, IndexError, KeyError):
        port = None
    return port, path


def capture_rows(run: Run, remote: Remote) -> list[tuple[str, str, str]]:
    g = remote.guest
    rows = [("mgmt", g.mgmt_nic, f"udp port {run.tunnel_port}")]
    if g.rdma_nic:
        rows.append(("lab", g.rdma_nic, f"udp port {run.tunnel_port} or net {run.contract['k8s_pod_cidr']} or icmp"))
    return rows


def mark_lab_nic(run: Run) -> None:
    a, b = run.workers
    for remote, peer in ((a, b), (b, a)):
        if remote.guest.rdma_nic and peer.guest.rdma_ip:
            remote.sh(f"ping -c 2 -W 2 -I {remote.guest.rdma_nic} {peer.guest.rdma_ip}", timeout_s=15)


def start_captures(run: Run) -> None:
    c = run.config["capture"]
    for remote in run.workers:
        for role, nic, bpf in capture_rows(run, remote):
            remote.start(f"capture-{role}", f"tcpdump -n -U -i {nic} -s {c['snaplen']} -w capture-{role}.pcap {shlex.quote(bpf)}",
                         c["max_seconds"], sudo=True)
    deadline = time.monotonic() + 15
    for remote in run.workers:
        for role, _, _ in capture_rows(run, remote):
            while "listening on" not in remote.read(f"capture-{role}.err") and time.monotonic() < deadline:
                time.sleep(0.5)
    mark_lab_nic(run)


def stop_captures(run: Run) -> dict[str, dict]:
    mark_lab_nic(run)
    summaries = {}
    for remote in run.workers:
        name = remote.guest.name
        for role, nic, _ in capture_rows(run, remote):
            remote.signal(f"capture-{role}", "INT", "tcpdump")
            rc = remote.wait(f"capture-{role}", 15)
            data = remote.read(f"capture-{role}.pcap", binary=True)
            run.raw(f"traffic/{name}-{role}.pcap", data)
            run.raw(f"traffic/{name}-{role}.tcpdump.stderr", remote.read(f"capture-{role}.err"))
            try:
                summary = pcap.summarize(data, run.tunnel_port, run.contract["k8s_pod_cidr"]).as_dict()
            except pcap.PcapError as exc:
                summary = {"error": str(exc)}
            summaries[f"{name}-{role}"] = {"nic": nic, "tcpdump_rc": rc, **summary}
    run.raw("traffic/summary.json", json.dumps({"tunnel_port": run.tunnel_port, "captures": summaries}, indent=2) + "\n")
    return summaries


def check_vxlan(run: Run, summaries: dict[str, dict], port_evidence: str) -> Outcome:
    a, b = (r.guest for r in run.workers)
    evidence = [port_evidence, "raw/traffic/summary.json"] + [f"raw/traffic/{g.name}-mgmt.pcap" for g in (a, b)]
    problems, seen = [], []
    for local, peer in ((a, b), (b, a)):
        s = summaries[f"{local.name}-mgmt"]
        seen.append(f"{local.name} {s.get('nic')} {s.get('tunnel_frames')} VXLAN frames on UDP {run.tunnel_port}")
        problems += [f"{local.name}: {p}" for p in tunnel_problems(s, local.node_ip, peer.node_ip,
                                                                   run.pod_ips[CLIENT], run.pod_ips[SERVER])]
    return failed("assertion-failed", evidence, "; ".join(problems)) if problems else passed(evidence, "; ".join(seen))


def check_lab_nic(run: Run, summaries: dict[str, dict]) -> Outcome:
    evidence = ["raw/traffic/summary.json"]
    problems, seen = [], []
    for remote in run.workers:
        key = f"{remote.guest.name}-lab"
        if key not in summaries:
            problems.append(f"{remote.guest.name} has no lab NIC in the inventory")
            continue
        s = summaries[key]
        evidence.append(f"raw/traffic/{key}.pcap")
        seen.append(f"{remote.guest.name} {s.get('nic')} {s.get('tunnel_frames')} VXLAN, {s.get('watch_net_frames')} pod CIDR, "
                    f"{s.get('icmp_echo_frames')} ICMP echo")
        problems += [f"{remote.guest.name}: {p}" for p in lab_nic_problems(s)]
    return failed("assertion-failed", evidence, "; ".join(problems)) if problems else passed(evidence, "; ".join(seen))


def check_namespace_removed(run: Run) -> Outcome:
    t = run.config["delete_timeout_s"]
    deleted = run.kubectl(f"delete namespace {run.ns} --ignore-not-found --wait --timeout={t}s", timeout_s=t + 30)
    left = run.kubectl(f"get namespace {run.ns} --ignore-not-found -o name")
    objects = run.kubectl(f"get all,networkpolicies -A -l {RUN_LABEL}={run.run_id} -o name")
    evidence = [save(run, "test-namespace-removed/delete.txt", deleted, f"kubectl delete namespace {run.ns} --wait"),
                save(run, "test-namespace-removed/left.txt", left, f"kubectl get namespace {run.ns}"),
                save(run, "test-namespace-removed/labeled.txt", objects, f"kubectl get all,networkpolicies -A -l {RUN_LABEL}={run.run_id}")]
    if left.rc != 0 or objects.rc != 0:
        return failed("command-failed", evidence, f"kubectl get exited {left.rc}, {objects.rc}")
    if left.stdout.strip() or objects.stdout.strip():
        return failed("assertion-failed", evidence, f"still present: {(left.stdout + objects.stdout).split()}")
    return passed(evidence, f"namespace {run.ns} and every object labeled {RUN_LABEL}={run.run_id} are gone")


def suite(run: Run) -> None:
    run.record("nodes-ready", check_nodes(run))
    run.record("cilium-agents-ready", check_cilium(run))
    pods = check_test_pods(run)
    run.record("test-pods-ready", pods)
    if pods.status != "passed":
        return
    run.tunnel_port, port_evidence = read_tunnel_port(run)
    if run.tunnel_port is None:
        run.warn("no cilium_vxlan device on the server worker, captures use no tunnel port")
    start_captures(run)
    run.record("pod-to-pod-cross-node", check_fetch(run, "pod-to-pod-cross-node", run.server_url()))
    run.record("cluster-dns", check_dns(run))
    service = check_fetch(run, "clusterip-service", run.service_url())
    run.record("clusterip-service", service)
    if service.status == "passed":
        run.record("network-policy-blocks", check_policy(run, "network-policy-blocks", deny_policy(run.ns, run.run_id), False))
        run.record("network-policy-allows", check_policy(
            run, "network-policy-allows", allow_policy(run.ns, run.run_id, run.config["http_port"]), True))
    else:
        for check_id in ("network-policy-blocks", "network-policy-allows"):
            run.record(check_id, precondition("clusterip-service"))
    summaries = stop_captures(run)
    run.record("vxlan-on-mgmt-nic", check_vxlan(run, summaries, port_evidence))
    run.record("no-pod-traffic-on-lab-nic", check_lab_nic(run, summaries))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run.k8s", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-i", "--inventory", type=Path, action="append", required=True)
    parser.add_argument("--run-id", help="share one run id across scenarios")
    args = parser.parse_args(argv)

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    run = Run(args.inventory, yaml.safe_load(CONFIG.read_text()), args.run_id)
    run.say(f"run {run.run_id} scenario k8s-baseline in {run.dir}, namespace {run.ns}")
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
        rfl.set_section(run.dir, "timing", {"wall_clock_seconds": round(time.monotonic() - run.started, 1)})
        code, message = rfl.finalize(run.dir)
        write_summary(run)
        run.say(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
