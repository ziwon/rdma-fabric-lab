"""make fleet-sim-test: the cel1 simulation against its site model, recorded through the rfl CLI.

    python -m run.cel1 -i ansible/inventories/cel1 -i <simulation inventory.yml> \\
        --power-off '<command> {guest}' --power-on '<command> {guest}'

Every guest command runs over the SSH forward the simulation inventory names, and kubectl runs on a
control plane against its admin.conf. The site model gives the interfaces and addresses each guest
must carry. The checks of the VIP fault are the ones of k8s-ha-vip.
"""

from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import re
import shlex
import signal
import sys
import time
import tomllib
from pathlib import Path

import yaml

from fleet import site as site_model

from . import ha, pcap, rfl
from .base import Outcome, check_leftovers, failed, passed, write_summary
from .inventory import ssh_argv
from .k8s import (CLIENT, KUBECTL, RUN_LABEL, SERVER, SERVICE, Node, check_fetch, group_hosts, namespace_of,
                  node_problems, nslookup_addresses, pod_problems, ready, save, test_objects)
from .remote import Done, Remote

REPO_ROOT = Path(__file__).resolve().parents[2]
TRACK = REPO_ROOT / "tracks" / "cel1"
SPEC = TRACK / "scenarios" / "cel1-sim-baseline.yaml"
CONFIG = TRACK / "config" / "cel1-sim-baseline.yaml"
LOCK = REPO_ROOT / "versions.lock.yaml"
TUNNELS = {"vxlan": 8472, "geneve": 6081}
CRICTL = "sudo crictl --runtime-endpoint unix:///run/containerd/containerd.sock"
CRI_PLUGIN = "io.containerd.cri.v1.runtime"
SYSLOG_GROUP = "simulation_syslog"
OPT_OUT = ("nvidia.com/gpu.deploy.driver", "nvidia.com/gpu.deploy.container-toolkit")
# The GPU Operator owns these labels on a running Node. On a node without nvidia.com/gpu.present it
# removes them all (updateGPUStateLabels in controllers/nodelabeling_controller.go at v26.7.2), so the
# guests without GPUs lose the opt-outs the kubelet registered. The checks read those from the kubelet.
OPERATOR_PREFIX = "nvidia.com/gpu.deploy."
GPU_PRESENT = "nvidia.com/gpu.present"


# ---------------------------------------------------------------- parsing


def interface_addresses(ip_addr: list[dict]) -> dict[str, list[str]]:
    """Interface name -> IPv4 addresses of `ip -j -4 addr show`."""
    return {link["ifname"]: [a["local"] for a in link.get("addr_info", []) if a.get("family") == "inet"] for link in ip_addr}


def interface_problems(want: dict[str, str], have: dict[str, list[str]]) -> list[str]:
    """want maps interface -> address from the site model."""
    found = []
    for iface, address in sorted(want.items()):
        if iface not in have:
            found.append(f"no interface {iface}")
        elif address not in have[iface]:
            found.append(f"{iface} has {have[iface] or 'no address'}, want {address}")
    return found


def label_problems(node_labels: dict[str, str], want: dict[str, str], others: dict[str, str]) -> list[str]:
    """want are the k8s_node_labels of the host. others are labels of other classes it must not carry."""
    found = [f"label {k}={node_labels.get(k)!r}, want {v!r}" for k, v in sorted(want.items()) if node_labels.get(k) != v]
    found += [f"carries {k}={v} of another class" for k, v in sorted(others.items()) if node_labels.get(k) == v]
    return found


def registered_labels(flags_env: str) -> dict[str, str]:
    """The --node-labels of /var/lib/kubelet/kubeadm-flags.env, the labels the kubelet registers with."""
    match = re.search(r"--node-labels=([^\s\"]*)", flags_env)
    pairs = [item.partition("=") for item in (match.group(1).split(",") if match and match.group(1) else [])]
    return {key: value for key, _, value in pairs}


def tunnel_links(ip_json: str) -> list[str]:
    """Names of the links in `ip -d -j link show type <kind>`. It prints {} for every link of another type."""
    try:
        return [link["ifname"] for link in json.loads(ip_json or "[]") if link.get("ifname")]
    except json.JSONDecodeError:
        return [f"unparsed output {ip_json.strip()[:40]!r}"]


def kube_proxy_replacement(status: str) -> tuple[bool | None, list[str]]:
    """(enabled, devices) of the KubeProxyReplacement line of `cilium-dbg status`."""
    match = re.search(r"^KubeProxyReplacement:\s+(\w+)(?:\s+\[(.*)\])?\s*$", status, re.MULTILINE)
    if not match:
        return None, []
    devices = [part.split()[0] for part in (match.group(2) or "").split(", ") if part.split()]
    return match.group(1) == "True", devices


def containerd_runtime(dump: str) -> tuple[str | None, str | None]:
    """(default runtime name, BinaryName of that runtime) of `containerd config dump`."""
    try:
        config = tomllib.loads(dump)
    except tomllib.TOMLDecodeError:
        return None, None
    cri = config.get("plugins", {}).get(CRI_PLUGIN, {}).get("containerd", {})
    default = cri.get("default_runtime_name")
    binary = cri.get("runtimes", {}).get(default, {}).get("options", {}).get("BinaryName")
    return default, binary


def established_to(ss_output: str, address: str, port: int) -> list[str]:
    """Local addresses of established TCP connections to address:port in `ss -Htn state established` output."""
    found = []
    for line in ss_output.splitlines():
        cols = line.split()
        if len(cols) >= 4 and cols[-1] == f"{address}:{port}":
            found.append(cols[-2].rsplit(":", 1)[0])
    return found


# ---------------------------------------------------------------- run


class Run(ha.Run):
    def __init__(self, inventories: list[Path], config: dict, power_off: str, power_on: str, run_id: str | None):
        super().__init__(inventories, config, power_off, power_on, run_id, spec=SPEC)
        self.config = config
        self.site = site_model.load(REPO_ROOT / config["site"])
        hostvars = self.listing["_meta"]["hostvars"]
        self.labels = {h: dict(hostvars[h].get("k8s_node_labels") or {}) for h in self.nodes}
        self.runtimes = {h: hostvars[h].get("k8s_container_runtime_default", "runc") for h in self.nodes}
        stand_ins = group_hosts(self.listing, SYSLOG_GROUP)
        if len(stand_ins) != 1:
            raise ValueError(f"the inventory has {len(stand_ins)} hosts in {SYSLOG_GROUP}, want 1")
        self.syslog_host = stand_ins[0]
        self.remote[self.syslog_host] = Remote(
            Node(self.syslog_host, ssh_argv(hostvars[self.syslog_host]), str(self.site.syslog_address), None, None, None),
            self.run_id, self.log)
        self.ns = namespace_of(self.run_id)
        lock = yaml.safe_load(LOCK.read_text())["components"][config["image"]]
        self.image = f"{lock['ref']}@{lock['value']}"
        self.pod_ips: dict[str, str] = {}
        self.pod_cidr = str(self.site.pod_cidr)
        self.service_cidr = str(self.site.service_cidr)
        self.metrics_note = "Measured on these simulation guests. No pass threshold applies to the outage length."

    def apply(self, manifest: list[dict] | dict) -> Done:
        docs = manifest if isinstance(manifest, list) else [manifest]
        payload = base64.b64encode(json.dumps({"apiVersion": "v1", "kind": "List", "items": docs}).encode()).decode()
        return self.remote[self.first_cp].sh(f"echo {payload} | base64 -d | {KUBECTL} apply -f -")

    def client_get(self, url: str) -> Done:
        t = self.config["request_timeout_s"]
        return self.kubectl(self.first_cp, f"-n {self.ns} exec {CLIENT} -- wget -q -T {t} -O - {shlex.quote(url)}", timeout_s=t + 30)

    def server_url(self) -> str:
        return f"http://{self.pod_ips[SERVER]}:{self.config['http_port']}/"

    def service_url(self) -> str:
        return f"http://{SERVICE}.{self.ns}.svc.cluster.local:{self.config['service_port']}/"

    def model_interfaces(self, host: str) -> dict[str, str]:
        h = self.site.hosts[host]
        return {name: str(address) for name, address in h.addresses.items()}


def describe(run: Run) -> None:
    guests, tools = [], {}
    for name, remote in run.remote.items():
        done = remote.sh(". /etc/os-release; echo \"$PRETTY_NAME\"; uname -r; kubelet --version 2>/dev/null; "
                         "/usr/local/bin/containerd --version 2>/dev/null | cut -d' ' -f3")
        run.raw(f"environment/{name}.txt", done.stdout + done.stderr)
        lines = done.stdout.splitlines() + ["", "", "", ""]
        guests.append({"name": name, "os": lines[0], "kernel": lines[1]})
        if name in run.nodes:
            tools[f"{name} kubelet"] = lines[2].replace("Kubernetes ", "")
            tools[f"{name} containerd"] = lines[3]
    ds, _ = run.kubectl_json(run.first_cp, "-n kube-system get daemonset cilium")
    tools["cilium-agent image"] = ", ".join(c["image"] for c in ds.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])) or "not found"
    tools["test image"] = run.image
    rfl.set_section(run.dir, "environment", {"guests": guests, "tools": tools})
    resolved = {"run_id": run.run_id, "scenario": "cel1-sim-baseline", "namespace": run.ns, "image": run.image,
                "vip": run.vip, "port": run.port, "control_planes": run.cps, "nodes": list(run.nodes),
                "syslog_stand_in": run.syslog_host, "runtimes": run.runtimes, "inventories": [str(i) for i in run.inventories],
                "power_off": run.power_off, "power_on": run.power_on, "config": run.config}
    (run.dir / "resolved-config.yaml").write_text(yaml.safe_dump(resolved, sort_keys=False))
    inputs = [CONFIG, LOCK, REPO_ROOT / run.config["site"]]
    inputs += [p for i in run.inventories for p in sorted(Path(i).rglob("*.yml") if Path(i).is_dir() else [Path(i)])]
    rfl.add_inputs(run.dir, inputs)
    rfl.set_section(run.dir, "inputs", {"parameters": {"namespace": run.ns, "image": run.image, "vip": run.vip,
                                                        "server_node": run.config["server_node"],
                                                        "client_node": run.config["client_node"],
                                                        "probe_from": run.config["probe_from"]}})


def verdict(problems: list[str], evidence: list[str], detail: str, failure_class: str = "assertion-failed") -> Outcome:
    return failed(failure_class, evidence, "; ".join(problems)) if problems else passed(evidence, detail)


# ---------------------------------------------------------------- checks


def check_interfaces(run: Run) -> Outcome:
    evidence, problems, counted = [], [], 0
    for host in run.site.hosts:
        done = run.remote[host].sh("ip -j -4 addr show")
        evidence.append(save(run, f"interfaces-match-site-model/{host}.json", done, "ip -j -4 addr show"))
        try:
            have = interface_addresses(json.loads(done.stdout or "[]"))
        except json.JSONDecodeError:
            have = {}
        want = run.model_interfaces(host)
        counted += len(want)
        problems += [f"{host}: {p}" for p in interface_problems(want, have)]
    return verdict(problems, evidence, f"{counted} interfaces of {len(run.site.hosts)} hosts carry their site model names and addresses")


def check_nodes(run: Run) -> Outcome:
    nodes, done = run.kubectl_json(run.first_cp, "get nodes")
    evidence = [save(run, "nodes-ready-with-class-labels/nodes.json", done, "kubectl get nodes -o json")]
    if done.rc != 0:
        return failed("command-failed", evidence, f"kubectl get nodes exited {done.rc}", done.rc)
    problems, _ = node_problems(nodes, {n: g.node_ip for n, g in run.nodes.items()})
    live = {n["metadata"]["name"]: n["metadata"].get("labels", {}) for n in nodes.get("items", [])}
    every = {(k, v) for labels in run.labels.values() for k, v in labels.items()}
    classes: dict[str, int] = {}
    for host, want in run.labels.items():
        flags = run.remote[host].sh("sudo cat /var/lib/kubelet/kubeadm-flags.env")
        evidence.append(save(run, f"nodes-ready-with-class-labels/{host}-kubeadm-flags.env", flags, "cat /var/lib/kubelet/kubeadm-flags.env"))
        registered = registered_labels(flags.stdout)
        if registered != want:
            problems.append(f"{host}: the kubelet registers {registered}, the inventory wants {want}")
        others = dict(kv for kv in every if kv[0] not in want)
        mine = {k: v for k, v in want.items() if not k.startswith(OPERATOR_PREFIX)}
        problems += [f"{host}: {p}" for p in label_problems(live.get(host, {}), mine, others)]
        classes[want.get("rfl.example/node-class", "none")] = classes.get(want.get("rfl.example/node-class", "none"), 0) + 1
    ready_count = sum(1 for n in nodes.get("items", []) if ready(n["status"].get("conditions")))
    counts = ", ".join(f"{n} {c}" for c, n in sorted(classes.items()))
    owned = sorted({f"{h} {k}={v}" for h, labels in live.items() for k, v in labels.items() if k.startswith(OPERATOR_PREFIX)})
    return verdict(problems, evidence, f"{ready_count} of {len(run.nodes)} Nodes Ready on their node IPs; every kubelet registers the "
                                       f"k8s_node_labels of its class ({counts}) and the Nodes carry them; live {OPERATOR_PREFIX}* "
                                       f"labels: {', '.join(owned) or 'none'}")


def check_no_kube_proxy(run: Run) -> Outcome:
    objects = run.kubectl(run.first_cp, "-n kube-system get daemonset,pods -l k8s-app=kube-proxy -o name; "
                          f"{KUBECTL} -n kube-system get daemonset kube-proxy --ignore-not-found -o name")
    evidence = [save(run, "no-kube-proxy/kube-proxy-objects.txt", objects, "kubectl get daemonset,pods -l k8s-app=kube-proxy")]
    problems = [] if objects.rc == 0 and not objects.stdout.strip() else [f"kube-proxy objects: {objects.stdout.split() or objects.rc}"]
    chains = {}
    for host in run.nodes:
        done = run.remote[host].sh("sudo iptables-save | grep -cE '^:KUBE-(SERVICES|SVC-|NODEPORTS)' || true")
        evidence.append(save(run, f"no-kube-proxy/{host}-kube-chains.txt", done, "iptables-save | grep -c '^:KUBE-(SERVICES|SVC-|NODEPORTS)'"))
        chains[host] = done.stdout.strip()
        if done.rc != 0 or chains[host] != "0":
            problems.append(f"{host} has {chains[host] or 'unknown'} kube-proxy chains")
    pods, pd = run.kubectl_json(run.first_cp, "-n kube-system get pods -l k8s-app=cilium")
    evidence.append(save(run, "no-kube-proxy/cilium-pods.json", pd, "kubectl get pods -l k8s-app=cilium -o json"))
    agents = {p["spec"].get("nodeName"): p["metadata"]["name"] for p in pods.get("items", []) if not p["metadata"].get("deletionTimestamp")}
    seen = set()
    for host in run.nodes:
        if host not in agents:
            problems.append(f"{host} has no Cilium agent")
            continue
        status = run.kubectl(run.first_cp, f"-n kube-system exec {agents[host]} -c cilium-agent -- cilium-dbg status")
        evidence.append(save(run, f"no-kube-proxy/{host}-cilium-status.txt", status, "cilium-dbg status"))
        enabled, devices = kube_proxy_replacement(status.stdout)
        seen.add(f"{enabled} {','.join(devices)}")
        if enabled is not True or devices != ["mgmt0"]:
            problems.append(f"{host} agent reports KubeProxyReplacement {enabled} on {devices or 'no device'}")
    return verdict(problems, evidence, f"no kube-proxy object, 0 KUBE chains on {len(run.nodes)} nodes, "
                                       f"every agent KubeProxyReplacement {' / '.join(sorted(seen))}")


def _agent_on(run: Run, host: str) -> tuple[dict | None, Done]:
    pods, done = run.kubectl_json(run.first_cp, f"-n kube-system get pods -l k8s-app=cilium --field-selector spec.nodeName={host}")
    live = [p for p in pods.get("items", []) if not p["metadata"].get("deletionTimestamp")]
    return (live[0] if len(live) == 1 else None), done


def check_cilium_api(run: Run) -> Outcome:
    ds, done = run.kubectl_json(run.first_cp, "-n kube-system get daemonset cilium")
    evidence = [save(run, "cilium-api-direct/daemonset.json", done, "kubectl get daemonset cilium -o json")]
    spec = ds.get("spec", {}).get("template", {}).get("spec", {})
    envs = {f"{c['name']} {e['name']}": e.get("value") for c in spec.get("initContainers", []) + spec.get("containers", [])
            for e in c.get("env", []) if e["name"] in ("KUBERNETES_SERVICE_HOST", "KUBERNETES_SERVICE_PORT")}
    want = {"KUBERNETES_SERVICE_HOST": run.vip, "KUBERNETES_SERVICE_PORT": str(run.port)}
    problems = [f"{k} is {v}, want {want[k.split()[1]]}" for k, v in sorted(envs.items()) if v != want[k.split()[1]]]
    if not any(k.startswith("cilium-agent ") for k in envs):
        problems.append("the cilium-agent container sets no KUBERNETES_SERVICE_HOST")
    host = run.config["restart_agent_on"]
    old, before = _agent_on(run, host)
    evidence.append(save(run, "cilium-api-direct/agent-before.json", before, f"kubectl get pods -l k8s-app=cilium --field-selector spec.nodeName={host}"))
    if old is None:
        return failed("assertion-failed", evidence, f"no single Cilium agent on {host}")
    deleted = run.kubectl(run.first_cp, f"-n kube-system delete pod {old['metadata']['name']} --wait=false")
    evidence.append(save(run, "cilium-api-direct/delete.txt", deleted, f"kubectl delete pod {old['metadata']['name']}"))
    deadline = time.monotonic() + run.config["agent_ready_timeout_s"]
    new, after = None, before
    while time.monotonic() < deadline:
        new, after = _agent_on(run, host)
        if new and new["metadata"]["uid"] != old["metadata"]["uid"] and ready(new["status"].get("conditions")):
            break
        new = None
        time.sleep(3)
    evidence.append(save(run, "cilium-api-direct/agent-after.json", after, f"kubectl get pods -l k8s-app=cilium --field-selector spec.nodeName={host}"))
    if new is None:
        problems.append(f"no new Ready agent on {host} within {run.config['agent_ready_timeout_s']} s")
        return failed("timeout", evidence, "; ".join(problems))
    name = new["metadata"]["name"]
    logs = run.kubectl(run.first_cp, f"-n kube-system logs {name} -c cilium-agent")
    evidence.append(save(run, "cilium-api-direct/agent-after.log", logs, f"kubectl logs {name} -c cilium-agent"))
    vip_url, cluster_ip = f"https://{run.vip}:{run.port}", str(ipaddress.ip_network(run.service_cidr)[1])
    if vip_url not in logs.stdout:
        problems.append(f"the restarted agent log never names {vip_url}")
    if f"{cluster_ip}:443" in logs.stdout:
        problems.append(f"the restarted agent log names the ClusterIP {cluster_ip}:443")
    restarts = sum(s.get("restartCount", 0) for s in new["status"].get("initContainerStatuses", []) + new["status"].get("containerStatuses", []))
    return verdict(problems, evidence, f"agent env {want}; {old['metadata']['name']} deleted on {host}, {name} Ready with {restarts} "
                                       f"container restarts, its log connects to {vip_url}")


def check_test_pods(run: Run) -> Outcome:
    objects = test_objects(run.ns, run.run_id, run.image, run.config)
    applied = run.apply(objects)
    evidence = [save(run, "test-pods-ready/apply.txt", applied, "kubectl apply -f <namespace, pods, service>"),
                run.raw("test-pods-ready/objects.json", json.dumps(objects, indent=2) + "\n")]
    if applied.rc != 0:
        return failed("command-failed", evidence, f"kubectl apply exited {applied.rc}", applied.rc)
    t = run.config["ready_timeout_s"]
    waited = run.kubectl(run.first_cp, f"-n {run.ns} wait --for=condition=Ready pod --all --timeout={t}s", timeout_s=t + 30)
    evidence.append(save(run, "test-pods-ready/wait.txt", waited, f"kubectl wait --for=condition=Ready pod --all --timeout={t}s"))
    pods, done = run.kubectl_json(run.first_cp, f"-n {run.ns} get pods")
    nodes, _ = run.kubectl_json(run.first_cp, "get nodes")
    evidence.append(save(run, "test-pods-ready/pods.json", done, "kubectl get pods -o json"))
    cidrs = {n["metadata"]["name"]: n["spec"].get("podCIDR", "") for n in nodes.get("items", []) if n["spec"].get("podCIDR")}
    problems, run.pod_ips = pod_problems(pods, {SERVER: run.config["server_node"], CLIENT: run.config["client_node"]}, cidrs)
    if problems:
        return failed("timeout" if waited.rc != 0 else "assertion-failed", evidence, "; ".join(problems), waited.rc)
    return passed(evidence, f"server {run.pod_ips[SERVER]} on {run.config['server_node']}, client {run.pod_ips[CLIENT]} on {run.config['client_node']}")


def check_dns(run: Run) -> Outcome:
    svc, _ = run.kubectl_json(run.first_cp, f"-n {run.ns} get service {SERVICE}")
    want = {f"{SERVICE}.{run.ns}.svc.cluster.local": svc.get("spec", {}).get("clusterIP"),
            "kubernetes.default.svc.cluster.local": str(ipaddress.ip_network(run.service_cidr)[1])}
    evidence, problems, seen = [], [], []
    for name, address in want.items():
        done = run.kubectl(run.first_cp, f"-n {run.ns} exec {CLIENT} -- nslookup {name}", timeout_s=40)
        evidence.append(save(run, f"cluster-dns/{name}.txt", done, f"nslookup {name}"))
        got = nslookup_addresses(done.stdout, name)
        seen.append(f"{name} -> {', '.join(got) or 'nothing'}")
        if address not in got:
            problems.append(f"{name} resolved to {got or 'nothing'} (exit {done.rc}), want {address}")
    return verdict(problems, evidence, "; ".join(seen))


# ---------------------------------------------------------------- captures


class Captures:
    """tcpdump on every NIC of the two test nodes. mgmt0 keeps only the test pod flow and tunnels, the
    storage and rail NICs keep anything pod, Service, tunnel or ICMP."""

    def __init__(self, run: Run):
        self.run = run
        self.hosts = [run.config["server_node"], run.config["client_node"]]
        self.nics = {h: sorted(run.model_interfaces(h)) for h in self.hosts}

    def bpf(self, nic: str) -> str:
        tunnels = " or ".join(f"udp port {p}" for p in TUNNELS.values())
        if nic == "mgmt0":
            a, b = self.run.pod_ips[CLIENT], self.run.pod_ips[SERVER]
            return f"(host {a} and host {b}) or {tunnels}"
        return f"net {self.run.pod_cidr} or net {self.run.service_cidr} or {tunnels} or icmp"

    def start(self) -> None:
        c = self.run.config["capture"]
        for host in self.hosts:
            for nic in self.nics[host]:
                self.run.remote[host].start(f"capture-{nic}", f"tcpdump -n -U -i {nic} -s {c['snaplen']} -w capture-{nic}.pcap "
                                                              f"{shlex.quote(self.bpf(nic))}", c["max_seconds"], sudo=True)
        deadline = time.monotonic() + 20
        for host in self.hosts:
            for nic in self.nics[host]:
                while "listening on" not in self.run.remote[host].read(f"capture-{nic}.err") and time.monotonic() < deadline:
                    time.sleep(0.5)
        self.mark()

    def mark(self) -> None:
        a, b = self.hosts
        for local, peer in ((a, b), (b, a)):
            pings = [f"ping -c 2 -W 2 -I {nic} {addr}" for nic, addr in sorted(self.run.model_interfaces(peer).items()) if nic != "mgmt0"]
            if pings:
                self.run.remote[local].sh(" ; ".join(pings), timeout_s=60)

    def stop(self) -> dict[str, dict]:
        self.mark()
        out = {}
        for host in self.hosts:
            remote = self.run.remote[host]
            for nic in self.nics[host]:
                remote.signal(f"capture-{nic}", "INT", "tcpdump")
                rc = remote.wait(f"capture-{nic}", 15)
                data = remote.read(f"capture-{nic}.pcap", binary=True)
                self.run.raw(f"traffic/{host}-{nic}.pcap", data)
                self.run.raw(f"traffic/{host}-{nic}.tcpdump.stderr", remote.read(f"capture-{nic}.err"))
                entry = {"nic": nic, "bpf": self.bpf(nic), "tcpdump_rc": rc}
                try:
                    pod = pcap.summarize(data, TUNNELS["vxlan"], self.run.pod_cidr if nic != "mgmt0" else f"{self.run.pod_ips[CLIENT]}/32")
                    svc = pcap.summarize(data, TUNNELS["geneve"], self.run.service_cidr)
                    entry |= {"frames": pod.frames, "icmp_echo_frames": pod.icmp_echo_frames, "vxlan_frames": pod.tunnel_frames,
                              "geneve_frames": svc.tunnel_frames, "pod_frames": pod.watch_net_frames, "service_frames": svc.watch_net_frames}
                except pcap.PcapError as exc:
                    entry["error"] = str(exc)
                out[f"{host}-{nic}"] = entry
        self.run.raw("traffic/summary.json", json.dumps(out, indent=2) + "\n")
        return out


def check_native_routing(run: Run, fetch: Outcome, summaries: dict[str, dict]) -> Outcome:
    server, client = run.config["server_node"], run.config["client_node"]
    route = run.remote[client].sh(f"ip route get {run.pod_ips[SERVER]}")
    evidence = fetch.evidence + [save(run, "pod-to-pod-native-routing/route.txt", route, f"ip route get {run.pod_ips[SERVER]}")]
    problems = [] if fetch.status == "passed" else [f"the fetch by pod IP failed: {fetch.detail}"]
    want = f"via {run.nodes[server].node_ip} dev mgmt0"
    if want not in route.stdout:
        problems.append(f"route to {run.pod_ips[SERVER]} is {route.stdout.strip()!r}, want {want}")
    for host in (server, client):
        for kind in TUNNELS:
            links = run.remote[host].sh(f"ip -d -j link show type {kind}")
            evidence.append(save(run, f"pod-to-pod-native-routing/{host}-{kind}-links.json", links, f"ip -d -j link show type {kind}"))
            if links.rc != 0 or tunnel_links(links.stdout):
                problems.append(f"{host} has {kind} links {tunnel_links(links.stdout)} (exit {links.rc})")
    evidence += ["raw/traffic/summary.json"] + [f"raw/traffic/{h}-mgmt0.pcap" for h in (server, client)]
    seen = []
    for host in (server, client):
        s = summaries[f"{host}-mgmt0"]
        seen.append(f"{host} mgmt0 {s.get('pod_frames')} pod-to-pod, {s.get('vxlan_frames')} VXLAN, {s.get('geneve_frames')} Geneve")
        if "error" in s or not s.get("pod_frames") or s.get("vxlan_frames") or s.get("geneve_frames"):
            problems.append(f"{host} mgmt0 capture: {s}")
    return verdict(problems, evidence, f"{fetch.detail}; {route.stdout.strip()}; {'; '.join(seen)}")


def check_quiet_nics(run: Run, summaries: dict[str, dict]) -> Outcome:
    evidence, problems, frames, live = ["raw/traffic/summary.json"], [], 0, 0
    for key, s in sorted(summaries.items()):
        if s["nic"] == "mgmt0":
            continue
        evidence.append(f"raw/traffic/{key}.pcap")
        live += bool(s.get("icmp_echo_frames"))
        frames += s.get("pod_frames", 0) + s.get("service_frames", 0) + s.get("vxlan_frames", 0) + s.get("geneve_frames", 0)
        if "error" in s:
            problems.append(f"{key}: {s['error']}")
        elif not s.get("icmp_echo_frames"):
            problems.append(f"{key}: no marker ping, so the capture may not have been live")
        bad = {k: s.get(k) for k in ("pod_frames", "service_frames", "vxlan_frames", "geneve_frames") if s.get(k)}
        if bad:
            problems.append(f"{key}: {bad}")
    count = sum(1 for s in summaries.values() if s["nic"] != "mgmt0")
    return verdict(problems, evidence, f"{count} storage and rail captures, {live} with marker pings, {frames} pod, Service or tunnel frames")


def check_nvidia_runtime(run: Run) -> Outcome:
    evidence, problems, tally = [], [], {}
    for host in run.nodes:
        done = run.remote[host].sh("sudo /usr/local/bin/containerd config dump")
        evidence.append(save(run, f"dgx-containerd-defaults-to-nvidia/{host}-config-dump.toml", done, "containerd config dump"))
        default, binary = containerd_runtime(done.stdout)
        want = run.runtimes[host]
        tally[f"{default} {binary}"] = tally.get(f"{default} {binary}", 0) + 1
        if default != want:
            problems.append(f"{host} defaults to {default}, the inventory wants {want}")
        if want == "nvidia" and binary != run.config["nvidia_runtime"]:
            problems.append(f"{host} nvidia runtime runs {binary}, want {run.config['nvidia_runtime']}")
    server = run.config["server_node"]
    if run.runtimes.get(server) != "nvidia":
        problems.append(f"the server pod node {server} does not default to nvidia, so no pod ran through it")
    remote = run.remote[server]
    listed = remote.sh(f"{CRICTL} ps --name {SERVER} --label io.kubernetes.pod.namespace={run.ns} -o json")
    evidence.append(save(run, "dgx-containerd-defaults-to-nvidia/server-container.json", listed, "crictl ps --name server -o json"))
    try:
        ids = [c["id"] for c in json.loads(listed.stdout or "{}").get("containers", [])]
    except json.JSONDecodeError:
        ids = []
    if len(ids) != 1:
        problems.append(f"crictl lists {len(ids)} server containers on {server}")
        return verdict(problems, evidence, "")
    inspect = remote.sh(f"{CRICTL} inspect {ids[0]}")
    calls = remote.sh(f"sudo grep -F {ids[0]} {run.config['nvidia_runtime_log']}")
    evidence += [save(run, "dgx-containerd-defaults-to-nvidia/server-inspect.json", inspect, f"crictl inspect {ids[0]}"),
                 save(run, "dgx-containerd-defaults-to-nvidia/stand-in-calls.txt", calls, f"grep {ids[0]} {run.config['nvidia_runtime_log']}")]
    try:
        info = json.loads(inspect.stdout or "{}").get("info", {})
    except json.JSONDecodeError:
        info = {}
    binary = (info.get("runtimeOptions") or {}).get("binary_name")
    verbs = sorted({w for line in calls.stdout.splitlines() for w in line.split() if w in ("create", "start", "state", "kill", "delete")})
    if binary != run.config["nvidia_runtime"]:
        problems.append(f"crictl inspect names runtime binary {binary}, want {run.config['nvidia_runtime']}")
    if "create" not in verbs:
        problems.append(f"the stand-in log holds no create of {ids[0][:12]}")
    summary = ", ".join(f"{n} nodes {k}" for k, n in sorted(tally.items()))
    return verdict(problems, evidence, f"{summary}; server container {ids[0][:12]} on {server} runs through {binary}, "
                                       f"stand-in calls {', '.join(verbs)}")


def check_gpu_operator(run: Run) -> Outcome:
    ns = run.config["gpu_operator_namespace"]
    deploys, dd = run.kubectl_json(run.first_cp, f"-n {ns} get deployments")
    daemonsets, ds = run.kubectl_json(run.first_cp, f"-n {ns} get daemonsets")
    policies, pd = run.kubectl_json(run.first_cp, "get clusterpolicies.nvidia.com")
    pods = run.kubectl(run.first_cp, f"-n {ns} get pods -o wide")
    nodes, nd = run.kubectl_json(run.first_cp, "get nodes")
    evidence = [save(run, "gpu-operator-installed/deployments.json", dd, f"kubectl -n {ns} get deployments -o json"),
                save(run, "gpu-operator-installed/daemonsets.json", ds, f"kubectl -n {ns} get daemonsets -o json"),
                save(run, "gpu-operator-installed/clusterpolicies.json", pd, "kubectl get clusterpolicies.nvidia.com -o json"),
                save(run, "gpu-operator-installed/pods.txt", pods, f"kubectl -n {ns} get pods -o wide"),
                save(run, "gpu-operator-installed/nodes.json", nd, "kubectl get nodes -o json")]
    problems = []
    available = {d["metadata"]["name"]: ready(d.get("status", {}).get("conditions"), "Available") for d in deploys.get("items", [])}
    if "gpu-operator" not in available:
        problems.append(f"no gpu-operator Deployment among {sorted(available)}")
    problems += [f"Deployment {n} is not Available" for n, ok in sorted(available.items()) if not ok]
    items = policies.get("items", [])
    if not items:
        problems.append("no ClusterPolicy")
    opted_out = []
    for host, want in run.labels.items():
        if not all(want.get(k) == "false" for k in OPT_OUT):
            continue
        flags = run.remote[host].sh("sudo cat /var/lib/kubelet/kubeadm-flags.env")
        evidence.append(save(run, f"gpu-operator-installed/{host}-kubeadm-flags.env", flags, "cat /var/lib/kubelet/kubeadm-flags.env"))
        registered = registered_labels(flags.stdout)
        if any(registered.get(k) != "false" for k in OPT_OUT):
            problems.append(f"{host} registers {[f'{k}={registered.get(k)}' for k in OPT_OUT]}, want both false")
        opted_out.append(host)
    if len(opted_out) != sum(1 for h in run.site.hosts.values() if run.site.classes[h.node_class].container_runtime == "nvidia"):
        problems.append(f"{len(opted_out)} nodes opt out of the driver and toolkit, want every DGX node")
    by_node = {n["metadata"]["name"]: n["metadata"].get("labels", {}) for n in nodes.get("items", [])}
    gpu_nodes = sorted(h for h, labels in by_node.items() if labels.get(GPU_PRESENT) == "true")
    pci = sorted(h for h, labels in by_node.items() if labels.get("feature.node.kubernetes.io/pci-10de.present") == "true")
    owned = sorted(f"{h} {k}={v}" for h, labels in by_node.items() for k, v in labels.items() if k.startswith(OPERATOR_PREFIX))
    operands = {d["metadata"]["name"]: f"{d['status'].get('numberReady', 0)}/{d['status'].get('desiredNumberScheduled', 0)}"
                for d in daemonsets.get("items", [])}
    ready_condition = next((c for c in (items[0].get("status", {}).get("conditions") or []) if c.get("type") == "Ready"), {}) if items else {}
    state = items[0].get("status", {}).get("state") if items else None
    found = (f"ClusterPolicy state {state}, Ready reason {ready_condition.get('reason')}: {ready_condition.get('message')}; "
             f"nodes with {GPU_PRESENT}=true: {gpu_nodes or 'none'}; nodes with an NVIDIA PCI device label: {pci or 'none'}; "
             f"live {OPERATOR_PREFIX}* labels: {owned or 'none'}; DaemonSets ready/desired {operands}")
    run.raw("gpu-operator-installed/operand-state.txt", found + "\n")
    evidence.append("raw/gpu-operator-installed/operand-state.txt")
    return verdict(problems, evidence, f"Deployments {available}; {len(opted_out)} DGX kubelets register both opt-outs; found: {found}")


def check_syslog(run: Run) -> Outcome:
    address, port = str(run.site.syslog_address), run.site.syslog_port
    marker = f"rfl-syslog-{run.run_id}"
    for host in run.nodes:
        run.remote[host].sh(f"logger -t {marker} {marker}-from-{host}")
    stand_in = run.remote[run.syslog_host]
    deadline = time.monotonic() + run.config["syslog_timeout_s"]
    while True:
        got = stand_in.sh(f"sudo grep -lF {marker} /var/log/remote/*.log 2>/dev/null; true")
        files = {Path(p).stem for p in got.stdout.split()}
        if set(run.nodes) <= files or time.monotonic() >= deadline:
            break
        time.sleep(3)
    lines = stand_in.sh(f"sudo grep -hF {marker} /var/log/remote/*.log; ls -l /var/log/remote")
    evidence = [save(run, "syslog-to-central-address/received.txt", lines, f"grep {marker} /var/log/remote/*.log on {run.syslog_host}")]
    problems = [f"no marker from {h} at {address}" for h in run.nodes if h not in files or f"{marker}-from-{h}" not in lines.stdout]
    for host, node in run.nodes.items():
        ss = run.remote[host].sh("ss -Htn state established")
        evidence.append(save(run, f"syslog-to-central-address/{host}-ss.txt", ss, "ss -Htn state established"))
        local = established_to(ss.stdout, address, port)
        if local != [node.node_ip]:
            problems.append(f"{host} connections to {address}:{port} come from {local or 'nowhere'}, want {node.node_ip}")
    return verdict(problems, evidence, f"markers of {len(run.nodes)} nodes arrived at {run.syslog_host} on {address}:{port}, "
                                       f"each over TCP from its node IP")


def check_namespace_removed(run: Run) -> Outcome:
    t = run.config["delete_timeout_s"]
    cp = run.up_cps()[0]
    deleted = run.kubectl(cp, f"delete namespace {run.ns} --ignore-not-found --wait --timeout={t}s", timeout_s=t + 30)
    left = run.kubectl(cp, f"get namespace {run.ns} --ignore-not-found -o name")
    objects = run.kubectl(cp, f"get all,networkpolicies -A -l {RUN_LABEL}={run.run_id} -o name")
    evidence = [save(run, "test-namespace-removed/delete.txt", deleted, f"kubectl delete namespace {run.ns} --wait"),
                save(run, "test-namespace-removed/left.txt", left, f"kubectl get namespace {run.ns}"),
                save(run, "test-namespace-removed/labeled.txt", objects, f"kubectl get all,networkpolicies -A -l {RUN_LABEL}={run.run_id}")]
    if left.rc != 0 or objects.rc != 0:
        return failed("command-failed", evidence, f"kubectl get exited {left.rc}, {objects.rc}")
    if left.stdout.strip() or objects.stdout.strip():
        return failed("assertion-failed", evidence, f"still present: {(left.stdout + objects.stdout).split()}")
    return passed(evidence, f"namespace {run.ns} and every object labeled {RUN_LABEL}={run.run_id} are gone")


def precondition(needs: str) -> Outcome:
    return Outcome("not_run", "not-attempted", None, [], f"not attempted: {needs} did not pass, so the result would not mean what the check says")


def traffic(run: Run) -> None:
    pods = check_test_pods(run)
    run.record("test-pods-ready", pods)
    later = ("pod-to-pod-native-routing", "cluster-dns", "clusterip-service", "no-pod-traffic-on-storage-or-rail-nics",
             "dgx-containerd-defaults-to-nvidia")
    if pods.status != "passed":
        for check_id in later:
            run.record(check_id, precondition("test-pods-ready"))
        return
    captures = Captures(run)
    captures.start()
    fetch = check_fetch(run, "pod-to-pod-native-routing", run.server_url())
    run.record("cluster-dns", check_dns(run))
    run.record("clusterip-service", check_fetch(run, "clusterip-service", run.service_url()))
    summaries = captures.stop()
    run.record("pod-to-pod-native-routing", check_native_routing(run, fetch, summaries))
    run.record("no-pod-traffic-on-storage-or-rail-nics", check_quiet_nics(run, summaries))
    run.record("dgx-containerd-defaults-to-nvidia", check_nvidia_runtime(run))


def suite(run: Run) -> None:
    run.record("interfaces-match-site-model", check_interfaces(run))
    run.record("nodes-ready-with-class-labels", check_nodes(run))
    run.record("control-planes-joined", ha.check_joined(run))
    run.record("api-on-vip", ha.check_api_on_vip(run))
    run.record("no-kube-proxy", check_no_kube_proxy(run))
    run.record("cilium-api-direct", check_cilium_api(run))
    run.record("gpu-operator-installed", check_gpu_operator(run))
    run.record("syslog-to-central-address", check_syslog(run))
    try:
        traffic(run)
    finally:
        run.record("test-namespace-removed", check_namespace_removed(run))
    outcome, holder = ha.check_holder(run)
    run.record("vip-holder-identified", outcome)
    if holder:
        run.record("api-survives-holder-loss", ha.check_survives(run, holder))
        run.record("cluster-works-after-loss", ha.check_cluster_after(run))
        run.record("holder-rejoins", ha.check_rejoins(run, holder))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run.cel1", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-i", "--inventory", type=Path, action="append", required=True)
    parser.add_argument("--power-off", required=True, help="command template with {guest} that cuts a guest's power")
    parser.add_argument("--power-on", required=True, help="command template with {guest} that boots it again")
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    run = Run(args.inventory, yaml.safe_load(CONFIG.read_text()), args.power_off, args.power_on, args.run_id)
    run.say(f"run {run.run_id} scenario cel1-sim-baseline in {run.dir}, VIP {run.vip}, {len(run.nodes)} nodes")
    try:
        describe(run)
        suite(run)
    except KeyboardInterrupt:
        run.warn("interrupted")
    except Exception as exc:
        run.warn(f"runner error: {exc!r}")
        raise
    finally:
        for guest in sorted(run.down):
            run.warn(f"{guest} is still powered off; boot it with: {run.power_on.format(guest=guest)}")
            run.remote.pop(guest)
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
