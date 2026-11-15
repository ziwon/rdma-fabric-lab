"""make rxe-test: Track A scenarios between the lab workers, recorded through the rfl CLI.

    python -m run.rxe --scenario vm-rxe-baseline -i ansible/inventories/lab -i .lab/inventory.yml

Every guest process runs under timeout with a run marker, so a failed or interrupted
run leaves nothing that `make cleanup RUN=<run-id>` cannot find.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import inventory, parse, pcap, rfl
from .base import Outcome, RunBase, check_leftovers, failed, passed, write_summary
from .remote import KILL_AFTER_S, Remote

REPO_ROOT = Path(__file__).resolve().parents[2]
TRACK = REPO_ROOT / "tracks" / "a-soft-roce"
CONFIG = TRACK / "config" / "vm-rxe.yaml"
PLAYBOOKS = REPO_ROOT / "ansible" / "playbooks"


@dataclass(frozen=True)
class Proc:
    """One guest process of a check: its exit code (None if it never finished) and output."""

    guest: str
    role: str
    rc: int | None
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        return self.stdout + self.stderr


class Run(RunBase):
    def __init__(self, scenario: str, inventories: list[Path], config: dict, run_id: str | None):
        super().__init__(TRACK / "scenarios" / f"{scenario}.yaml", run_id)
        self.scenario = scenario
        self.inventories = inventories
        self.config = config
        self.guests = inventory.load(inventories)
        self.remote = {name: Remote(g, self.run_id, self.log) for name, g in self.guests.items()}
        self.server = self.remote[config["server"]]
        self.client = self.remote[config["client"]]

    def save_proc(self, check_id: str, proc: Proc) -> list[str]:
        base = f"{check_id}/{proc.guest}-{proc.role}"
        return [
            self.raw(f"{base}.rc", f"{'still running at the deadline' if proc.rc is None else proc.rc}\n"),
            self.raw(f"{base}.stdout", proc.stdout),
            self.raw(f"{base}.stderr", proc.stderr),
        ]


def peer_of(run: Run, remote: Remote) -> Remote:
    return run.client if remote is run.server else run.server


# ---------------------------------------------------------------- environment


def describe_environment(run: Run) -> None:
    guests, tools = [], {}
    for name, remote in run.remote.items():
        done = remote.sh(
            ". /etc/os-release; echo \"$PRETTY_NAME\"; uname -r; "
            "dpkg-query -W -f '${Package}=${Version}\\n' rdma-core ibverbs-providers rdmacm-utils perftest 2>/dev/null"
        )
        run.raw(f"environment/{name}.txt", done.stdout + done.stderr)
        lines = done.stdout.splitlines()
        if len(lines) >= 2:
            guests.append({"name": name, "os": lines[0], "kernel": lines[1]})
        for line in lines[2:]:
            package, _, version = line.partition("=")
            tools[f"{name} {package}"] = version
    run.say("guests: " + ", ".join(f"{g['name']} {g['kernel']}" for g in guests))
    rfl.set_section(run.dir, "environment", {"guests": guests, "tools": tools})


def write_resolved_config(run: Run) -> None:
    resolved = {
        "run_id": run.run_id,
        "scenario": run.scenario,
        "inventories": [str(i) for i in run.inventories],
        "guests": {
            name: {
                "ssh_destination": g.ssh[-1],
                "mgmt_nic": g.mgmt_nic,
                "mgmt_ip": g.mgmt_ip,
                "uplink_nic": g.uplink_nic,
                "rdma_nic": g.rdma_nic,
                "rdma_ip": g.rdma_ip,
                "rxe_device": g.rxe_device,
            }
            for name, g in run.guests.items()
        },
        "config": run.config,
    }
    (run.dir / "resolved-config.yaml").write_text(yaml.safe_dump(resolved, sort_keys=False))
    inputs = [CONFIG] + [p for i in run.inventories for p in sorted(Path(i).rglob("*.yml") if Path(i).is_dir() else [Path(i)])]
    rfl.add_inputs(run.dir, inputs)
    rfl.set_section(run.dir, "inputs", {"parameters": {"server": run.config["server"], "client": run.config["client"],
                                                        "rping": run.config["rping"], "perftest": run.config["perftest"]}})


# ---------------------------------------------------------------- device and route


def check_device(run: Run, check_id: str = "rxe-bound-to-lab-nic") -> Outcome:
    evidence, problems = [], []
    for name, remote in run.remote.items():
        g = remote.guest
        done = remote.sh("rdma -j link show")
        evidence.append(run.raw(f"{check_id}/{name}-rdma-link.json", done.stdout or done.stderr))
        try:
            links = parse.rdma_links(done.stdout)
        except (json.JSONDecodeError, parse.ParseError) as exc:
            problems.append(f"{name}: rdma -j link show unreadable ({exc})")
            continue
        on_nic = [link for link in links if link.get("netdev") == g.rdma_nic]
        named = [link for link in links if link.get("ifname") == g.rxe_device]
        if len(on_nic) != 1 or on_nic[0].get("ifname") != g.rxe_device:
            problems.append(f"{name}: links on {g.rdma_nic} are {[link.get('ifname') for link in on_nic]}, want [{g.rxe_device}]")
        elif on_nic[0].get("state") != "ACTIVE":
            problems.append(f"{name}: {g.rxe_device} on {g.rdma_nic} is {on_nic[0].get('state')}, not ACTIVE")
        if any(link.get("netdev") != g.rdma_nic for link in named):
            problems.append(f"{name}: {g.rxe_device} is bound to {[link.get('netdev') for link in named]}")
    if problems:
        return failed("assertion-failed", evidence, "; ".join(problems))
    return passed(evidence, ", ".join(f"{n} {r.guest.rxe_device} on {r.guest.rdma_nic} ACTIVE" for n, r in run.remote.items()))


def check_route(run: Run) -> Outcome:
    evidence, problems, seen = [], [], []
    for name, remote in run.remote.items():
        peer = peer_of(run, remote).guest
        done = remote.sh(f"ip -j route get {peer.rdma_ip}")
        evidence.append(run.raw(f"route-via-lab-nic/{name}.json", done.stdout or done.stderr))
        try:
            dev = json.loads(done.stdout or "[{}]")[0].get("dev")
        except json.JSONDecodeError:
            dev = None
        seen.append(f"{name} to {peer.rdma_ip} via {dev}")
        if dev != remote.guest.rdma_nic:
            problems.append(f"{name} routes {peer.rdma_ip} via {dev}, not {remote.guest.rdma_nic}")
    if problems:
        return failed("assertion-failed", evidence, "; ".join(problems))
    return passed(evidence, ", ".join(seen))


# ---------------------------------------------------------------- client and server pairs


def collect_proc(remote: Remote, name: str, role: str, rc: int | None) -> Proc:
    return Proc(remote.guest.name, role, rc, remote.read(f"{name}.out"), remote.read(f"{name}.err"))


def wait_listening(run: Run, check_id: str, name: str, comm: str, port: int) -> tuple[str, int | None]:
    """("ready" | "exited" | "timeout", server rc when it exited). Saves the last cm_id listing it read."""
    deadline = time.monotonic() + run.config["readiness_timeout_s"]
    while True:
        done = run.server.sh(f"cat {run.server.dir}/{name}.rc 2>/dev/null; echo ===; rdma -j resource show cm_id")
        head, _, listing = done.stdout.partition("===")
        state = (
            ("exited", int(head.strip())) if head.strip().lstrip("-").isdigit()
            else ("ready", None) if parse.cm_listening(listing.strip(), comm, port)
            else ("timeout", None) if time.monotonic() >= deadline
            else None
        )
        if state:
            run.raw(f"{check_id}/{run.server.guest.name}-server.cm_id.json", listing.strip() + "\n")
            return state
        time.sleep(0.5)


def stop_session(remote: Remote, name: str) -> None:
    remote.sh(
        f"sid=$(cat {remote.dir}/{name}.pid 2>/dev/null) && [ -n \"$sid\" ] && "
        "{ sudo pkill -TERM -s \"$sid\"; sleep 1; sudo pkill -KILL -s \"$sid\"; }; true"
    )


def run_pair(run: Run, check_id: str, comm: str, port: int, server_cmd: str, client_cmd: str,
             timeout_s: int) -> tuple[Proc, Proc | None, str]:
    """Server first, client once the server listens. Returns both processes and why the pair ended."""
    s_name, c_name = f"{check_id}-server", f"{check_id}-client"
    server_bound = timeout_s + run.config["readiness_timeout_s"] + run.config["server_drain_s"]
    run.server.start(s_name, server_cmd, server_bound)
    state, s_rc = wait_listening(run, check_id, s_name, comm, port)
    client = None
    if state == "ready":
        run.client.start(c_name, client_cmd, timeout_s)
        c_rc = run.client.wait(c_name, timeout_s + KILL_AFTER_S + 10)
        if c_rc is None:
            stop_session(run.client, c_name)
        client = collect_proc(run.client, c_name, "client", c_rc)
        s_rc = run.server.wait(s_name, run.config["server_drain_s"])
    if s_rc is None:
        stop_session(run.server, s_name)
    server = collect_proc(run.server, s_name, "server", s_rc)
    return server, client, state


def pair_outcome(run: Run, check_id: str, server: Proc, client: Proc | None, state: str) -> Outcome | None:
    """The failed outcome of a pair, or None when both ends exited 0."""
    evidence = [f"raw/{check_id}/{server.guest}-server.cm_id.json"] + run.save_proc(check_id, server)
    evidence += run.save_proc(check_id, client) if client else []
    if client is None:
        why = {"exited": f"the server exited {server.rc} before it listened",
               "timeout": f"the server did not listen within {run.config['readiness_timeout_s']} s"}[state]
        cls = "timeout" if state == "timeout" else parse.failure_class(server.rc, server.output)
        return failed(cls, evidence, f"{why}, client not started", server.rc)
    if client.rc == 0 and server.rc == 0:
        return None
    worst = client if client.rc != 0 else server
    return failed(parse.failure_class(worst.rc, worst.output), evidence,
                  f"server exit {server.rc}, client exit {client.rc}", worst.rc)


def check_rping(run: Run) -> Outcome:
    c = run.config["rping"]
    addr = run.server.guest.rdma_ip
    args = f"-a {addr} -p {c['port']} -C {c['count']} -S {c['size']} -V -v"
    server, client, state = run_pair(run, "rping-verified", "rping", c["port"], f"rping -s {args}", f"rping -c {args}",
                                     c["timeout_s"])
    bad = pair_outcome(run, "rping-verified", server, client, state)
    if bad:
        return bad
    evidence = [f"raw/rping-verified/{server.guest}-server.cm_id.json"]
    evidence += [f"raw/rping-verified/{p.guest}-{p.role}.{s}" for p in (server, client) for s in ("stdout", "rc")]
    want = list(range(c["count"]))
    got = {p.role: parse.rping_iterations(p.stdout) for p in (server, client)}
    if got["client"] != want or got["server"] != want:
        return failed("assertion-failed", evidence, f"iterations printed {got}, want 0..{c['count'] - 1} on both ends")
    return passed(evidence, f"{c['count']} iterations of {c['size']} bytes validated with -V, both ends exit 0")


def check_perftest(run: Run, check_id: str, tool: str) -> Outcome:
    c = run.config["perftest"]
    common = f"-d {{dev}} -R -p {c['port']} -s {c['size']} -n {c['iterations']} -F --report_gbits"
    server_cmd = f"{tool} {common.format(dev=run.server.guest.rxe_device)}"
    client_cmd = f"{tool} {common.format(dev=run.client.guest.rxe_device)} {run.server.guest.rdma_ip}"
    server, client, state = run_pair(run, check_id, tool, c["port"], server_cmd, client_cmd, c["timeout_s"])
    bad = pair_outcome(run, check_id, server, client, state)
    source = f"raw/{check_id}/{run.client.guest.name}-client.stdout"
    labels = {"tool": tool, "side": "client", "guest": run.client.guest.name, "server": run.server.guest.name}
    if bad:
        add_absent_metrics(run, check_id, source, f"{tool} did not complete: {bad.detail}", labels)
        return bad
    evidence = [f"raw/{check_id}/{server.guest}-server.cm_id.json"]
    evidence += [f"raw/{check_id}/{p.guest}-{p.role}.{s}" for p in (server, client) for s in ("stdout", "rc")]
    try:
        result = parse.perftest_bw(client.stdout)
    except parse.ParseError as exc:
        add_absent_metrics(run, check_id, source, f"client output unparsable: {exc}", labels)
        return failed("assertion-failed", evidence, str(exc))
    if result.iterations != c["iterations"] or result.bytes != c["size"]:
        add_absent_metrics(run, check_id, source, "result row does not match the requested run", labels)
        return failed("assertion-failed", evidence,
                      f"client reports {result.iterations} x {result.bytes} B, requested {c['iterations']} x {c['size']} B")
    labels |= {"bytes": str(result.bytes), "iterations": str(result.iterations), "device": result.device or "",
               "gid_index_chosen_by_rdma_cm": str(result.gid_index)}
    for metric, value, unit in (("bw_average", result.bw_average, result.bw_unit),
                                ("bw_peak", result.bw_peak, result.bw_unit),
                                ("msg_rate", result.msg_rate_mpps, "Mpps")):
        run.metrics.append({"name": f"{check_id.replace('-', '_')}.{metric}", "value": value, "unit": unit,
                            "absent_reason": None, "source": source, "sample_count": result.iterations,
                            "labels": labels})
    return passed(evidence, f"{result.iterations} x {result.bytes} B, {result.bw_average} {result.bw_unit} average, "
                            f"GID index {result.gid_index} chosen by rdma_cm")


def add_absent_metrics(run: Run, check_id: str, source: str, reason: str, labels: dict) -> None:
    if not (run.dir / source).is_file() or (run.dir / source).stat().st_size == 0:
        source = f"raw/{check_id}/{run.server.guest.name}-server.rc"
    for metric, unit in (("bw_average", "Gb/sec"), ("bw_peak", "Gb/sec"), ("msg_rate", "Mpps")):
        run.metrics.append({"name": f"{check_id.replace('-', '_')}.{metric}", "value": None, "unit": unit,
                            "absent_reason": reason, "source": source, "labels": labels})


# ---------------------------------------------------------------- captures

CAPTURES = (
    # role, NIC attribute, BPF filter. The other NICs record only RoCEv2 and what proves the capture was
    # live, so they collect no management traffic: SSH connection setups where SSH arrives,
    # and the marker pings the runner sends over the management network.
    ("lab", "rdma_nic", ""),
    ("mgmt", "mgmt_nic", "udp port 4791 or icmp or (tcp port 22 and tcp[tcpflags] & tcp-syn != 0)"),
    ("uplink", "uplink_nic", "udp port 4791 or (tcp port 22 and tcp[tcpflags] & tcp-syn != 0)"),
)


def captures_of(guest) -> list[tuple[str, str, str]]:
    """The capture rows of a guest. The uplink row exists only where the uplink is not the management NIC."""
    return [c for c in CAPTURES if c[0] != "uplink" or guest.uplink_nic != guest.mgmt_nic]


def mark_mgmt_network(run: Run) -> None:
    """Pings each peer's management address over the management NIC, so its capture has frames to show."""
    for remote in run.remote.values():
        peer = peer_of(run, remote).guest
        if peer.mgmt_ip:
            remote.sh(f"ping -c 2 -W 2 -I {remote.guest.mgmt_nic} {peer.mgmt_ip}", timeout_s=15)


def start_captures(run: Run) -> dict[str, float]:
    c = run.config["capture"]
    for remote in run.remote.values():
        for role, nic_attr, bpf in captures_of(remote.guest):
            cmd = f"tcpdump -n -U -i {getattr(remote.guest, nic_attr)} -s {c['snaplen']} -w capture-{role}.pcap"
            remote.start(f"capture-{role}", cmd + (f" {shlex.quote(bpf)}" if bpf else ""), c["max_seconds"], sudo=True)
    deadline = time.monotonic() + run.config["readiness_timeout_s"]
    for remote in run.remote.values():
        for role, _, _ in captures_of(remote.guest):
            while "listening on" not in remote.read(f"capture-{role}.err") and time.monotonic() < deadline:
                time.sleep(0.5)
    mark_mgmt_network(run)
    return {"started": time.time()}


def stop_captures(run: Run, window: dict[str, float]) -> dict[str, dict]:
    mark_mgmt_network(run)
    window["stopped"] = time.time()
    summaries = {}
    for name, remote in run.remote.items():
        for role, nic_attr, _ in captures_of(remote.guest):
            remote.signal(f"capture-{role}", "INT", "tcpdump")
            rc = remote.wait(f"capture-{role}", 15)
            data = remote.read(f"capture-{role}.pcap", binary=True)
            run.raw(f"traffic/{name}-{role}.pcap", data)
            run.raw(f"traffic/{name}-{role}.tcpdump.stderr", remote.read(f"capture-{role}.err"))
            try:
                summary = pcap.summarize(data).as_dict()
            except pcap.PcapError as exc:
                summary = {"error": str(exc)}
            summaries[f"{name}-{role}"] = {"nic": getattr(remote.guest, nic_attr), "tcpdump_rc": rc, **summary}
    run.raw("traffic/summary.json", json.dumps({"window": window, "captures": summaries}, indent=2) + "\n")
    return summaries


def check_lab_traffic(run: Run, summaries: dict[str, dict]) -> Outcome:
    pair = {run.server.guest.rdma_ip, run.client.guest.rdma_ip}
    wanted = {f"{run.server.guest.rdma_ip}>{run.client.guest.rdma_ip}", f"{run.client.guest.rdma_ip}>{run.server.guest.rdma_ip}"}
    evidence = ["raw/traffic/summary.json"] + [f"raw/traffic/{n}-lab.pcap" for n in run.remote]
    problems, seen = [], []
    for name in run.remote:
        s = summaries[f"{name}-lab"]
        flows = s.get("rocev2_flows", {})
        stray = [f for f in flows if set(f.split(">")) - pair]
        seen.append(f"{name} {s.get('nic')} {s.get('rocev2_frames', 0)} RoCEv2 frames")
        if "error" in s or not wanted <= set(flows) or stray:
            problems.append(f"{name}: {s.get('error') or f'flows {flows}, want both directions of {sorted(pair)} only'}")
    if problems:
        return failed("assertion-failed", evidence, "; ".join(problems))
    return passed(evidence, ", ".join(seen))


def liveness_problem(role: str, summary: dict, ssh_here: bool) -> str | None:
    """Why a non-lab capture cannot show it was live, or None. ssh_here is true on the NIC SSH arrives at."""
    if ssh_here and summary.get("ssh_syn_frames", 0) > 0:
        return None
    if role == "mgmt" and summary.get("icmp_echo_frames", 0) > 0:
        return None
    return f"the {role} capture saw no {'SSH setup' if ssh_here else 'marker ping'}, so it may not have been live"


def check_mgmt_traffic(run: Run, summaries: dict[str, dict]) -> Outcome:
    evidence = ["raw/traffic/summary.json"]
    problems, seen = [], []
    for name, remote in run.remote.items():
        for role, _, _ in captures_of(remote.guest)[1:]:
            s = summaries[f"{name}-{role}"]
            evidence.append(f"raw/traffic/{name}-{role}.pcap")
            seen.append(f"{name} {role} {s.get('nic')} {s.get('rocev2_frames')} RoCEv2, {s.get('ssh_syn_frames')} SSH SYN, "
                        f"{s.get('icmp_echo_frames')} ICMP echo")
            ssh_here = s.get("nic") == remote.guest.uplink_nic
            if "error" in s:
                problems.append(f"{name} {role}: {s['error']}")
            elif s["rocev2_frames"] != 0:
                problems.append(f"{name}: {s['rocev2_frames']} RoCEv2 frames on the {role} NIC {s.get('nic')}")
            elif problem := liveness_problem(role, s, ssh_here):
                problems.append(f"{name}: {problem}")
    if problems:
        return failed("assertion-failed", evidence, "; ".join(problems))
    return passed(evidence, ", ".join(seen))


# ---------------------------------------------------------------- scenarios


def traffic_suite(run: Run) -> None:
    run.record("rxe-bound-to-lab-nic", check_device(run))
    run.record("route-via-lab-nic", check_route(run))
    window = start_captures(run)
    run.record("rping-verified", check_rping(run))
    for test in run.config["perftest"]["tests"]:
        run.record(test["check"], check_perftest(run, test["check"], test["tool"]))
    summaries = stop_captures(run, window)
    run.record("roce-on-lab-nic", check_lab_traffic(run, summaries))
    run.record("no-roce-on-mgmt-nic", check_mgmt_traffic(run, summaries))


def boot_id(remote: Remote) -> str | None:
    done = remote.sh("cat /proc/sys/kernel/random/boot_id", timeout_s=15)
    return done.stdout.strip() if done.rc == 0 and done.stdout.strip() else None


def scenario_reboot(run: Run) -> None:
    before = {name: boot_id(r) for name, r in run.remote.items()}
    for remote in run.remote.values():
        remote.sh("sudo systemctl reboot", timeout_s=20)
    deadline = time.monotonic() + run.config["reboot_timeout_s"]
    after: dict[str, str | None] = {}
    for name, remote in run.remote.items():
        while time.monotonic() < deadline:
            current = boot_id(remote)
            if current and current != before[name]:
                after[name] = current
                break
            time.sleep(3)
        after.setdefault(name, None)
    evidence = [run.raw("guests-rebooted/boot-ids.json", json.dumps({"before": before, "after": after}, indent=2) + "\n")]
    stuck = [n for n in run.remote if not after[n]]
    run.record("guests-rebooted", failed("timeout", evidence, f"no new boot id from {', '.join(stuck)} within "
                                         f"{run.config['reboot_timeout_s']} s") if stuck
               else passed(evidence, ", ".join(f"{n} {before[n][:8]} -> {after[n][:8]}" for n in run.remote)))
    if stuck:
        return
    evidence, problems = [], []
    for name, remote in run.remote.items():
        g = remote.guest
        unit = remote.sh("systemctl show rfl-rxe.service -p ActiveState -p Result -p ExecMainStatus -p ActiveEnterTimestamp")
        journal = remote.sh("sudo journalctl -b -u rfl-rxe.service --no-pager -o short-iso")
        evidence += [run.raw(f"rxe-restored-without-ansible/{name}-unit.txt", unit.stdout),
                     run.raw(f"rxe-restored-without-ansible/{name}-journal.txt", journal.stdout or journal.stderr)]
        props = dict(line.split("=", 1) for line in unit.stdout.splitlines() if "=" in line)
        if props.get("ActiveState") != "active" or props.get("Result") != "success":
            problems.append(f"{name}: rfl-rxe.service is {props.get('ActiveState')} with result {props.get('Result')}")
        if f"added {g.rxe_device} on {g.rdma_nic}" not in journal.stdout:
            problems.append(f"{name}: the journal of this boot does not show rfl-rxe adding {g.rxe_device} on {g.rdma_nic}")
    device = check_device(run, "rxe-restored-without-ansible")
    evidence += device.evidence
    if device.status != "passed":
        problems.append(device.detail or "device check failed")
    run.record("rxe-restored-without-ansible", failed("assertion-failed", evidence, "; ".join(problems)) if problems
               else passed(evidence, "rfl-rxe.service active and journal shows the device added at boot on both workers; "
                                     "this run invoked no Ansible"))
    traffic_suite(run)


def ansible_playbook(run: Run, playbook: str) -> tuple[int, str, str]:
    argv = [str(Path(sys.executable).with_name("ansible-playbook"))]
    for inv in run.inventories:
        argv += ["-i", str(inv)]
    argv.append(str(PLAYBOOKS / playbook))
    start = time.monotonic()
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=run.config["ansible_timeout_s"],
                             env={**os.environ, "ANSIBLE_NOCOLOR": "1", "ANSIBLE_FORCE_COLOR": "0"})
        rc, stdout, stderr = out.returncode, out.stdout, out.stderr
    except subprocess.TimeoutExpired as exc:
        rc, stdout, stderr = 124, (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""), "timeout"
    run.log.write("control", shlex.join(argv), rc, time.monotonic() - start)
    return rc, stdout, stderr


def check_rerun(run: Run, check_id: str, playbook: str) -> Outcome:
    rc, stdout, stderr = ansible_playbook(run, playbook)
    evidence = [run.raw(f"{check_id}/{playbook}.log", stdout), run.raw(f"{check_id}/{playbook}.stderr", stderr),
                run.raw(f"{check_id}/{playbook}.rc", f"{rc}\n")]
    try:
        recap = parse.ansible_recap(stdout)
    except parse.ParseError as exc:
        return failed(parse.failure_class(rc, stderr) or "command-failed", evidence, str(exc), rc)
    summary = ", ".join(f"{h} changed={c.get('changed')} failed={c.get('failed')} unreachable={c.get('unreachable')}"
                        for h, c in sorted(recap.items()))
    missing = sorted(set(run.guests) - set(recap))
    if rc != 0 or missing or any(c.get("failed") or c.get("unreachable") for c in recap.values()):
        return failed("command-failed", evidence, f"exit {rc}; {summary}; missing {missing}", rc)
    if any(c.get("changed") for c in recap.values()):
        return failed("assertion-failed", evidence, f"rerun changed something: {summary}", rc)
    return passed(evidence, summary, rc)


def check_single_device(run: Run) -> Outcome:
    evidence, problems = [], []
    for name, remote in run.remote.items():
        g = remote.guest
        done = remote.sh(
            f"rdma -j link show; echo ===; ip -j -4 addr show dev {g.rdma_nic}; echo ===; "
            "ls /etc/netplan/ | grep -c rfl; echo ===; systemctl list-unit-files 'rfl-rxe*' --no-legend | wc -l"
        )
        evidence.append(run.raw(f"single-rxe-device/{name}.txt", done.stdout + done.stderr))
        parts = [p.strip() for p in done.stdout.split("===")]
        if len(parts) != 4:
            problems.append(f"{name}: could not read the device state")
            continue
        links = [link.get("ifname") for link in parse.rdma_links(parts[0]) if link.get("netdev") == g.rdma_nic]
        addrs = [a["local"] for link in json.loads(parts[1] or "[]") for a in link.get("addr_info", [])]
        if links != [g.rxe_device] or addrs != [g.rdma_ip] or parts[2] != "1" or parts[3] != "1":
            problems.append(f"{name}: links {links}, IPv4 {addrs}, lab netplan files {parts[2]}, rfl-rxe units {parts[3]}")
    if problems:
        return failed("assertion-failed", evidence, "; ".join(problems))
    return passed(evidence, "one device, one address, one netplan file and one unit on each worker")


def scenario_baseline(run: Run) -> None:
    traffic_suite(run)


def scenario_rerun(run: Run) -> None:
    run.record("bootstrap-rerun-unchanged", check_rerun(run, "bootstrap-rerun-unchanged", "bootstrap.yml"))
    run.record("rxe-up-rerun-unchanged", check_rerun(run, "rxe-up-rerun-unchanged", "rxe.yml"))
    run.record("single-rxe-device", check_single_device(run))


SCENARIOS = {
    "vm-rxe-baseline": (scenario_baseline, True),
    "vm-rxe-reboot": (scenario_reboot, True),
    "vm-rxe-rerun": (scenario_rerun, False),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run.rxe", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="vm-rxe-baseline")
    parser.add_argument("-i", "--inventory", type=Path, action="append", required=True)
    parser.add_argument("--run-id", help="share one run id across scenarios")
    args = parser.parse_args(argv)

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    run = Run(args.scenario, args.inventory, yaml.safe_load(CONFIG.read_text()), args.run_id)
    scenario, has_traffic = SCENARIOS[args.scenario]
    run.say(f"run {run.run_id} scenario {args.scenario} in {run.dir}")
    try:
        write_resolved_config(run)
        describe_environment(run)
        scenario(run)
    except KeyboardInterrupt:
        run.warn("interrupted, stopping this run's guest processes")
    except Exception as exc:
        run.warn(f"runner error, stopping this run's guest processes: {exc!r}")
        raise
    finally:
        if has_traffic:
            run.record("no-process-left", check_leftovers(run))
        for remote in run.remote.values():
            remote.remove_dir()
        if run.metrics:
            (run.dir / "metrics.json").write_text(json.dumps(
                {"schema_version": 1, "result_kind": "software-rdma-measurement", "metrics": run.metrics}, indent=2) + "\n")
        rfl.set_section(run.dir, "timing", {"wall_clock_seconds": round(time.monotonic() - run.started, 1)})
        code, message = rfl.finalize(run.dir)
        write_summary(run)
        run.say(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
