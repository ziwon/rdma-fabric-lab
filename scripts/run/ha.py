"""make k8s-ha-test: the API VIP of a three-control-plane cluster survives the loss of its holder.

    python -m run.ha -i <inventory> -i <override> --power-off 'lab.sh kill {guest}' --power-on 'lab.sh start {guest}'

The power commands belong to the hypervisor, so the runner takes them as templates. A probe loop on a
worker asks /livez on the VIP every half second, and its log is the measurement of the outage.
"""

from __future__ import annotations

import argparse
import json
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import yaml

from . import rfl
from .base import Outcome, RunBase, check_leftovers, failed, passed, write_summary
from .k8s import KUBECTL, cluster_from_listing, group_hosts, ready, save
from .remote import Done, Remote

REPO_ROOT = Path(__file__).resolve().parents[2]
TRACK = REPO_ROOT / "tracks" / "a-soft-roce"
SPEC = TRACK / "scenarios" / "k8s-ha-vip.yaml"
CONFIG = TRACK / "config" / "k8s-ha-vip.yaml"
ETCDCTL = ("etcdctl --endpoints=https://127.0.0.1:2379 --cacert=/etc/kubernetes/pki/etcd/ca.crt "
           "--cert=/etc/kubernetes/pki/etcd/healthcheck-client.crt --key=/etc/kubernetes/pki/etcd/healthcheck-client.key")
STEADY_OK = 5


def probe_command(vip: str, port: int, interval_s: float, timeout_s: float) -> str:
    """Shell loop printing '<epoch seconds> ok|fail' per /livez probe of the VIP."""
    url = f"https://{vip}:{port}/livez"
    return ("sh -c " + shlex.quote(
        f"while :; do t=$(date +%s.%N); if curl -sk -m {timeout_s} -o /dev/null -w '%{{http_code}}' {url} | grep -qx 200; "
        f"then echo \"$t ok\"; else echo \"$t fail\"; fi; sleep {interval_s}; done"))


def parse_probes(text: str) -> list[tuple[float, bool]]:
    samples = []
    for line in text.splitlines():
        stamp, _, verdict = line.strip().partition(" ")
        try:
            samples.append((float(stamp), verdict == "ok"))
        except ValueError:
            continue
    return samples


def outage(samples: list[tuple[float, bool]], since: float) -> dict:
    """The outage seen by probes after since: first failure, first success after it, and whether probes are steady again.

    unavailable_s runs from the first failed probe to the first good one after it, so it undercounts by at most one
    probe interval. With no failure it is 0.
    """
    after = [(t, ok) for t, ok in samples if t >= since]
    fails = [t for t, ok in after if not ok]
    tail = [ok for _, ok in after[-STEADY_OK:]]
    steady = len(tail) == STEADY_OK and all(tail)
    if not fails:
        return {"probes": len(after), "failures": 0, "first_fail": None, "recovered_at": after[0][0] if after else None,
                "unavailable_s": 0.0 if after else None, "steady": steady}
    first = fails[0]
    recovered = next((t for t, ok in after if ok and t > first), None)
    return {"probes": len(after), "failures": len(fails), "first_fail": first, "recovered_at": recovered,
            "unavailable_s": round(recovered - first, 3) if recovered else None, "steady": steady}


def lease_holder(lease: dict) -> str | None:
    return lease.get("spec", {}).get("holderIdentity") or None


def vip_carriers(addr_listings: dict[str, list[dict]], vip: str) -> list[str]:
    """Hosts whose ip -j addr output carries the VIP."""
    return sorted(host for host, links in addr_listings.items()
                  if any(a.get("local") == vip for link in links for a in link.get("addr_info", [])))


def etcd_members(member_list: dict) -> list[str]:
    return sorted(m.get("name", "") for m in member_list.get("members", []) if m.get("name"))


def healthy_endpoints(health: list[dict]) -> list[str]:
    return sorted(h["endpoint"] for h in health if h.get("health"))


class Run(RunBase):
    def __init__(self, inventories: list[Path], config: dict, power_off: str, power_on: str, run_id: str | None,
                 spec: Path = SPEC):
        super().__init__(spec, run_id)
        self.inventories, self.config = inventories, config
        self.power_off, self.power_on = power_off, power_on
        listing = json.loads(subprocess.run(["ansible-inventory", "--list", *[a for i in inventories for a in ("-i", str(i))]],
                                            capture_output=True, text=True, check=True).stdout)
        self.listing = listing
        self.nodes, self.first_cp, _ = cluster_from_listing(listing)
        self.cps = group_hosts(listing, "kube_control_plane")
        hv = listing["_meta"]["hostvars"][self.first_cp]
        self.vip, self.port = str(hv.get("k8s_api_vip") or ""), int(hv.get("k8s_api_port", 6443))
        self.remote = {name: Remote(n, self.run_id, self.log) for name, n in self.nodes.items()}
        self.probe = self.remote[config["probe_from"]]
        self.down: set[str] = set()
        self.metrics_note = "Measured on these harness guests. No pass threshold applies to the outage length."

    def kubectl(self, host: str, args: str, timeout_s: float = 60) -> Done:
        return self.remote[host].sh(f"{KUBECTL} {args}", timeout_s=timeout_s)

    def kubectl_json(self, host: str, args: str) -> tuple[dict, Done]:
        done = self.kubectl(host, f"{args} -o json")
        try:
            return json.loads(done.stdout or "{}"), done
        except json.JSONDecodeError:
            return {}, done

    def up_cps(self) -> list[str]:
        return [c for c in self.cps if c not in self.down]

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
        done = remote.sh(". /etc/os-release; echo \"$PRETTY_NAME\"; uname -r; kubelet --version")
        lines = done.stdout.splitlines() + ["", "", ""]
        guests.append({"name": name, "os": lines[0], "kernel": lines[1]})
        tools[f"{name} kubelet"] = lines[2].replace("Kubernetes ", "")
    pod, _ = run.kubectl_json(run.first_cp, f"-n kube-system get pod kube-vip-{run.first_cp}")
    tools["kube-vip image"] = ", ".join(c["image"] for c in pod.get("spec", {}).get("containers", [])) or "not found"
    rfl.set_section(run.dir, "environment", {"guests": guests, "tools": tools})
    resolved = {"run_id": run.run_id, "scenario": "k8s-ha-vip", "vip": run.vip, "port": run.port, "control_planes": run.cps,
                "inventories": [str(i) for i in run.inventories], "power_off": run.power_off, "power_on": run.power_on,
                "config": run.config}
    (run.dir / "resolved-config.yaml").write_text(yaml.safe_dump(resolved, sort_keys=False))
    rfl.add_inputs(run.dir, [CONFIG] + [p for i in run.inventories for p in sorted(Path(i).rglob("*.yml") if Path(i).is_dir() else [Path(i)])])
    rfl.set_section(run.dir, "inputs", {"parameters": {"vip": run.vip, "control_planes": ",".join(run.cps),
                                                        "probe_from": run.config["probe_from"]}})


def check_joined(run: Run) -> Outcome:
    nodes, done = run.kubectl_json(run.first_cp, "get nodes")
    evidence = [save(run, "control-planes-joined/nodes.json", done, "kubectl get nodes -o json")]
    pods, pd = run.kubectl_json(run.first_cp, "-n kube-system get pods -l tier=control-plane")
    evidence.append(save(run, "control-planes-joined/control-plane-pods.json", pd, "kubectl get pods -l tier=control-plane -o json"))
    md = run.kubectl(run.first_cp, f"-n kube-system exec etcd-{run.first_cp} -- {ETCDCTL} member list -w json")
    try:
        members = json.loads(md.stdout or "{}")
    except json.JSONDecodeError:
        members = {}
    evidence.append(save(run, "control-planes-joined/etcd-members.json", md, "etcdctl member list -w json"))
    cfg = run.kubectl(run.first_cp, "-n kube-system get configmap kubeadm-config -o jsonpath={.data.ClusterConfiguration}")
    evidence.append(save(run, "control-planes-joined/kubeadm-config.yaml", cfg, "kubectl get configmap kubeadm-config"))
    problems = []
    cp_nodes = {n["metadata"]["name"]: n for n in nodes.get("items", [])
                if "node-role.kubernetes.io/control-plane" in n["metadata"].get("labels", {})}
    if sorted(cp_nodes) != sorted(run.cps):
        problems.append(f"control-plane Nodes {sorted(cp_nodes)}, want {sorted(run.cps)}")
    problems += [f"{n} is not Ready" for n, node in cp_nodes.items() if not ready(node["status"].get("conditions"))]
    running = {(p["metadata"]["labels"].get("component"), p["spec"].get("nodeName")) for p in pods.get("items", [])
               if p["status"].get("phase") == "Running"}
    problems += [f"{c} does not run on {cp}" for cp in run.cps for c in ("kube-apiserver", "etcd") if (c, cp) not in running]
    names = etcd_members(members)
    if names != sorted(run.cps):
        problems.append(f"etcd members {names}, want {sorted(run.cps)}")
    endpoint = (yaml.safe_load(cfg.stdout or "{}") or {}).get("controlPlaneEndpoint")
    if endpoint != f"{run.vip}:{run.port}":
        problems.append(f"controlPlaneEndpoint is {endpoint}, want {run.vip}:{run.port}")
    detail = f"control planes {', '.join(sorted(cp_nodes))} Ready with kube-apiserver and etcd, members {', '.join(names)}, endpoint {endpoint}"
    return failed("assertion-failed", evidence, "; ".join(problems)) if problems else passed(evidence, detail)


def check_api_on_vip(run: Run) -> Outcome:
    url = f"https://{run.vip}:{run.port}/livez"
    probe = run.probe.sh(f"curl -sk -m 5 {url}; echo; curl -sk -m 5 -o /dev/null -w '%{{remote_ip}} %{{http_code}}\\n' {url}")
    evidence = [save(run, "api-on-vip/worker-livez.txt", probe, f"curl -sk {url}")]
    problems = [] if probe.rc == 0 and probe.stdout.startswith("ok") else [f"{run.probe.guest.name} got {probe.stdout.strip()!r}"]
    for cp in run.cps:
        server = run.remote[cp].sh("sudo grep -E '^ +server:' /etc/kubernetes/admin.conf")
        readyz = run.kubectl(cp, "get --raw=/readyz")
        evidence.append(save(run, f"api-on-vip/{cp}-admin-server.txt", server, "grep server: /etc/kubernetes/admin.conf"))
        evidence.append(save(run, f"api-on-vip/{cp}-readyz.txt", readyz, "kubectl get --raw=/readyz"))
        if f"https://{run.vip}:{run.port}" not in server.stdout:
            problems.append(f"{cp} admin.conf points at {server.stdout.strip()}")
        if readyz.rc != 0 or readyz.stdout.strip() != "ok":
            problems.append(f"{cp} /readyz through the VIP exited {readyz.rc}")
    return failed("assertion-failed", evidence, "; ".join(problems)) if problems else \
        passed(evidence, f"{run.probe.guest.name} /livez on {run.vip} ok; /readyz ok from {', '.join(run.cps)} through admin.conf on the VIP")


def find_holder(run: Run, check_id: str) -> tuple[str | None, list[str], list[str]]:
    """(lease holder, hosts carrying the VIP, evidence), read from the control planes that are up."""
    reader = run.up_cps()[0]
    lease, done = run.kubectl_json(reader, f"-n kube-system get lease {run.config['lease']}")
    evidence = [save(run, f"{check_id}/lease.json", done, f"kubectl get lease {run.config['lease']} -o json")]
    listings = {}
    for cp in run.up_cps():
        addr = run.remote[cp].sh("ip -j -4 addr show")
        evidence.append(save(run, f"{check_id}/{cp}-addr.json", addr, "ip -j -4 addr show"))
        try:
            listings[cp] = json.loads(addr.stdout or "[]")
        except json.JSONDecodeError:
            listings[cp] = []
    return lease_holder(lease), vip_carriers(listings, run.vip), evidence


def check_holder(run: Run) -> tuple[Outcome, str | None]:
    holder, carriers, evidence = find_holder(run, "vip-holder-identified")
    if holder is None or carriers != [holder]:
        return failed("assertion-failed", evidence, f"lease holder {holder}, VIP on {carriers or 'no host'}"), None
    return passed(evidence, f"lease {run.config['lease']} held by {holder}, VIP {run.vip} on {holder} only"), holder


def check_survives(run: Run, holder: str) -> Outcome:
    c = run.config
    run.probe.start("probe", probe_command(run.vip, run.port, c["probe_interval_s"], c["probe_timeout_s"]),
                    c["recovery_timeout_s"] + 120)
    time.sleep(3)
    killed_at = time.time()
    rc, kill_evidence = run.power(run.power_off, holder, "api-survives-holder-loss", "power-off")
    if rc != 0:
        return failed("command-failed", [kill_evidence], f"power-off of {holder} exited {rc}", rc)
    run.down.add(holder)
    deadline = time.monotonic() + c["recovery_timeout_s"]
    while True:
        result = outage(parse_probes(run.probe.read("probe.out")), killed_at)
        if (result["failures"] and result["recovered_at"] and result["steady"]) or time.monotonic() >= deadline:
            break
        time.sleep(1)
    run.probe.signal("probe", "TERM", "sh")
    text = run.probe.read("probe.out")
    log = run.raw("api-survives-holder-loss/probes.txt", text)
    result = outage(parse_probes(text), killed_at)
    new_holder, carriers, evidence = find_holder(run, "api-survives-holder-loss")
    evidence = [kill_evidence, log, run.raw("api-survives-holder-loss/outage.json", json.dumps(result | {"killed_at": killed_at}, indent=2) + "\n")] + evidence
    run.metrics.append({"name": "ha_vip.api_unavailable_seconds", "value": result["unavailable_s"], "unit": "s",
                        "absent_reason": None if result["unavailable_s"] is not None else "the probes never succeeded after the power-off",
                        "source": log, "sample_count": max(result["probes"], 1),
                        "labels": {"lost": holder, "new_holder": new_holder or "", "probe_from": run.probe.guest.name,
                                   "probe_interval_s": str(c["probe_interval_s"])}})
    problems = []
    if not (result["recovered_at"] and result["steady"]):
        problems.append(f"/livez on {run.vip} did not answer steadily within {c['recovery_timeout_s']} s ({result['failures']} failed probes)")
    if new_holder in (None, holder) or carriers != [new_holder]:
        problems.append(f"after the loss the lease names {new_holder} and the VIP is on {carriers or 'no host'}")
    if problems:
        return failed("timeout" if not result["recovered_at"] else "assertion-failed", evidence, "; ".join(problems))
    return passed(evidence, f"{holder} powered off; {result['failures']} of {result['probes']} probes failed, API back on {run.vip} "
                            f"after {result['unavailable_s']} s; lease and VIP now on {new_holder}")


def check_cluster_after(run: Run) -> Outcome:
    cp = run.up_cps()[0]
    nodes = run.kubectl(cp, "get nodes -o wide")
    name = f"rfl-{run.run_id.lower()}"
    write = run.kubectl(cp, f"-n default create configmap {name} --from-literal=run={run.run_id}")
    read = run.kubectl(cp, f"-n default get configmap {name} -o jsonpath={{.data.run}}")
    delete = run.kubectl(cp, f"-n default delete configmap {name}")
    health = run.kubectl(cp, f"-n kube-system exec etcd-{cp} -- {ETCDCTL} endpoint health --cluster -w json")
    evidence = [save(run, "cluster-works-after-loss/nodes.txt", nodes, "kubectl get nodes -o wide"),
                save(run, "cluster-works-after-loss/configmap.txt",
                     Done(max(write.rc, read.rc, delete.rc), write.stdout + read.stdout + "\n" + delete.stdout,
                          write.stderr + read.stderr + delete.stderr), f"kubectl create, get, delete configmap {name}"),
                save(run, "cluster-works-after-loss/etcd-health.json", health, "etcdctl endpoint health --cluster -w json")]
    try:
        healthy = healthy_endpoints(json.loads(health.stdout or "[]"))
    except json.JSONDecodeError:
        healthy = []
    problems = []
    if nodes.rc != 0:
        problems.append(f"kubectl get nodes from {cp} exited {nodes.rc}")
    if write.rc != 0 or read.stdout.strip() != run.run_id or delete.rc != 0:
        problems.append(f"ConfigMap write, read, delete exited {write.rc}, {read.rc}, {delete.rc}")
    if not health.stdout.strip():
        problems.append(f"etcdctl endpoint health through kubectl exec exited {health.rc} with no output, so etcd was not read")
    elif len(healthy) < len(run.cps) // 2 + 1:
        problems.append(f"etcd healthy endpoints {healthy}, below quorum of {len(run.cps)}")
    return failed("assertion-failed", evidence, "; ".join(problems)) if problems else \
        passed(evidence, f"from {cp} through the VIP: nodes listed, ConfigMap written, read and deleted; etcd healthy {len(healthy)} of {len(run.cps)}")


def check_rejoins(run: Run, holder: str) -> Outcome:
    rc, evidence_on = run.power(run.power_on, holder, "holder-rejoins", "power-on")
    if rc != 0:
        return failed("command-failed", [evidence_on], f"power-on of {holder} exited {rc}", rc)
    run.down.discard(holder)
    cp = run.up_cps()[0]
    deadline = time.monotonic() + run.config["rejoin_timeout_s"]
    while True:
        node, done = run.kubectl_json(cp, f"get node {holder}")
        health = run.kubectl(cp, f"-n kube-system exec etcd-{cp} -- {ETCDCTL} endpoint health --cluster -w json")
        try:
            healthy = healthy_endpoints(json.loads(health.stdout or "[]"))
        except json.JSONDecodeError:
            healthy = []
        is_ready = ready(node.get("status", {}).get("conditions"))
        if (is_ready and len(healthy) == len(run.cps)) or time.monotonic() >= deadline:
            break
        time.sleep(5)
    evidence = [evidence_on, save(run, "holder-rejoins/node.json", done, f"kubectl get node {holder} -o json"),
                save(run, "holder-rejoins/etcd-health.json", health, "etcdctl endpoint health --cluster -w json")]
    if not (is_ready and len(healthy) == len(run.cps)):
        return failed("timeout", evidence, f"{holder} Ready {is_ready}, etcd healthy {len(healthy)} of {len(run.cps)} "
                                           f"after {run.config['rejoin_timeout_s']} s")
    return passed(evidence, f"{holder} Ready again, etcd healthy {len(healthy)} of {len(run.cps)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run.ha", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-i", "--inventory", type=Path, action="append", required=True)
    parser.add_argument("--power-off", required=True, help="command template with {guest} that cuts a guest's power")
    parser.add_argument("--power-on", required=True, help="command template with {guest} that boots it again")
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    run = Run(args.inventory, yaml.safe_load(CONFIG.read_text()), args.power_off, args.power_on, args.run_id)
    run.say(f"run {run.run_id} scenario k8s-ha-vip in {run.dir}, VIP {run.vip}, control planes {', '.join(run.cps)}")
    holder = None
    try:
        describe(run)
        run.record("control-planes-joined", check_joined(run))
        run.record("api-on-vip", check_api_on_vip(run))
        outcome, holder = check_holder(run)
        run.record("vip-holder-identified", outcome)
        if holder:
            run.record("api-survives-holder-loss", check_survives(run, holder))
            run.record("cluster-works-after-loss", check_cluster_after(run))
            run.record("holder-rejoins", check_rejoins(run, holder))
    except KeyboardInterrupt:
        run.warn("interrupted")
    except Exception as exc:
        run.warn(f"runner error: {exc!r}")
        raise
    finally:
        for guest in sorted(run.down):
            run.warn(f"{guest} is still powered off; boot it with: {run.power_on.format(guest=guest)}")
        for guest in run.down:
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
