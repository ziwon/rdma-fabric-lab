"""make track-b: one Track B scenario inside the Track B guest, recorded through the rfl CLI.

    python -m ibsim run --scenario b-01 -i <inventory> -i <override> [--run-id <id>] [--break]

The runner stays on the host. It starts the fabric container of the run in the guest through
guest.sh, reads every observation back over SSH, and checks it against the topology file.
--break hands the simulator the topology file without the scenario's break link, while every
expectation still comes from the full topology file. Such a run must end failed.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from run import rfl
from run.base import Outcome, RunBase, failed, passed, write_summary

from . import guest, observe, verdict
from .observe import PortState, SmInfo
from .topology import Discovered, Port, Topology, parse_ibnetdiscover, parse_netfile, render_netfile

TRACK = guest.REPO_ROOT / "tracks" / "b-ibsim"
CONFIG = TRACK / "config" / "scenarios.yaml"
LOCK = guest.REPO_ROOT / "versions.lock.yaml"
IMAGE_TAG = "rfl-ibsim:m4"
IMAGE_INPUTS = [guest.REPO_ROOT / "images" / "ibsim" / f for f in ("Dockerfile", "rfl-ibsim", "opensm.conf", "dpkg-keep-net-examples")]
CONTAINER_USER = "10001:10001"
# Commands that must never run on the host: the simulator, the subnet manager and every diagnostic tool.
SIMULATOR_COMMANDS = frozenset({"ibsim", "opensm", "ibnetdiscover", "iblinkinfo", "ibswitches", "ibhosts", "ibroute",
                                "ibtracert", "sminfo", "smpquery", "saquery", "ibstat"})

ENVIRONMENT_CHECKS = ("image-pinned", "container-locked-down")
BASELINE_CHECKS = ("subnet-up", "topology-discovered", "lids-unique", "port-states", "routes-shortest", "lid-reachability")
FAULT_CHECKS = ("fault-resweep", "fault-topology", "fault-routes", "fault-reachability", "recovery-resweep", "recovered")
CLOSING_CHECKS = ("no-container-left", "host-untouched")


def check_ids(params: dict) -> tuple[str, ...]:
    """Every check a scenario with these parameters records, in order. Its spec declares exactly these."""
    return ENVIRONMENT_CHECKS + BASELINE_CHECKS + (FAULT_CHECKS if "fault" in params else ()) + CLOSING_CHECKS


class ToolFailed(RuntimeError):
    def __init__(self, what: str, evidence: str, rc: int):
        super().__init__(f"{what} exited {rc}")
        self.evidence = evidence
        self.rc = rc


@dataclass(frozen=True)
class Snapshot:
    """Everything the tools report about the fabric at one moment, parsed, with the raw files it came from."""

    discovered: Discovered
    states: dict[Port, PortState]
    tables: dict[str, dict[int, int]]
    sm: SmInfo
    answers: dict[int, str | None]
    log: str
    evidence: dict[str, str]


def lid_names(found: Discovered) -> dict[int, str]:
    return {lid: port.node for port, (lid, _) in found.lids.items()}


def combine(*verdicts: verdict.Verdict) -> verdict.Verdict:
    return verdict.Verdict(all(v.ok for v in verdicts), "; ".join(v.detail for v in verdicts))


def as_outcome(result: verdict.Verdict, evidence: list[str]) -> Outcome:
    return passed(evidence, result.detail) if result.ok else failed("assertion-failed", evidence, result.detail, exit_code=None)


# Field of docker inspect -> (what it shows, the value a locked-down Track B container has).
LOCKED_DOWN = {
    ("HostConfig", "Privileged"): ("not privileged", False),
    ("HostConfig", "NetworkMode"): ("no network", "none"),
    ("HostConfig", "Devices"): ("no device node", []),
    ("HostConfig", "CapDrop"): ("every capability dropped", ["ALL"]),
    ("HostConfig", "ReadonlyRootfs"): ("read-only root file system", True),
    ("HostConfig", "PidMode"): ("own PID namespace", ""),
    ("HostConfig", "IpcMode"): ("own IPC namespace", "private"),
    ("Config", "User"): ("non-root user", CONTAINER_USER),
    ("AppArmorProfile",): ("AppArmor docker-default", "docker-default"),
}


def container_problems(inspect: dict, image: str, uids: list[str]) -> list[str]:
    problems = []
    for path, (meaning, want) in LOCKED_DOWN.items():
        value = inspect
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        if value != want:
            problems.append(f"{'.'.join(path)} is {value!r}, want {want!r} ({meaning})")
    host = inspect.get("HostConfig", {})
    if host.get("CapAdd"):
        problems.append(f"HostConfig.CapAdd is {host['CapAdd']!r}")
    if "no-new-privileges" not in (host.get("SecurityOpt") or []):
        problems.append("no-new-privileges is not set")
    if inspect.get("Image") != image:
        problems.append(f"the container runs image {inspect.get('Image')}, not the locked {image}")
    if any(e.startswith("LD_PRELOAD=") for e in inspect.get("Config", {}).get("Env") or []):
        problems.append("LD_PRELOAD is set for the whole container")
    problems += [f"mount {m.get('Destination')} is writable" for m in inspect.get("Mounts", []) if m.get("RW")]
    if not uids or set(uids) != {CONTAINER_USER.split(":")[0]}:
        problems.append(f"container processes run as UIDs {sorted(set(uids))}, want only {CONTAINER_USER.split(':')[0]}")
    return problems


def host_simulator_processes(ps_text: str) -> list[str]:
    """Lines of ps -eo pid=,comm=,args= whose command is one of the simulator commands."""
    found = []
    for line in ps_text.splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[1] in SIMULATOR_COMMANDS:
            found.append(line.strip())
    return found


class Run(RunBase):
    def __init__(self, scenario: str, inventories: list[Path], run_id: str | None, broken: bool):
        super().__init__(TRACK / "scenarios" / f"{scenario}.yaml", run_id)
        config = yaml.safe_load(CONFIG.read_text())
        self.scenario = scenario
        self.params = {**config["defaults"], **config["scenarios"][scenario]}
        self.metrics_note = "Observations of this simulator run in the Track B guest. No pass threshold applies."
        self.key = f"{self.run_id}-{scenario}"
        self.lock = yaml.safe_load(LOCK.read_text())
        self.image = self.lock["components"]["ibsim.image"]["value"]
        self.topology_file = guest.REPO_ROOT / self.params["topology"]
        self.full = parse_netfile(self.topology_file.read_text())
        self.cut = Port(self.params["break"]["node"], self.params["break"]["port"]) if broken else None
        self.simulated = broken_netfile(self.params, self.full) if broken else self.topology_file.read_text()
        self.sm_port = Port(self.params["sm_host"], 1)
        self.guest = guest.load(inventories, self.log)
        self.year = time.gmtime().tm_year

    def gsh(self, *args: str, stdin: bytes | None = None, timeout_s: float = 120):
        return self.guest.guest_sh(*args, stdin=stdin, timeout_s=timeout_s)

    def tool(self, phase: str, name: str, *args: str, file: str | None = None) -> tuple[str, str]:
        done = self.gsh("exec", self.key, "tool", name, *args)
        path = self.raw(f"{phase}/{file or name}.txt", done.stdout + (f"\n# stderr\n{done.stderr}" if done.stderr else ""))
        if done.rc != 0:
            raise ToolFailed(f"{name} {' '.join(args)}".strip(), path, done.rc)
        return done.stdout, path

    def opensm_log(self) -> str:
        done = self.gsh("read", self.key, "opensm.log")
        return done.stdout if done.rc == 0 else ""

    def metric(self, name: str, value: float | None, unit: str, source: str, reason: str | None = None, **labels: str) -> None:
        entry = {"name": name, "value": value, "unit": unit, "absent_reason": None if value is not None else reason,
                 "source": source}
        if labels:
            entry["labels"] = labels
        self.metrics.append(entry)


# ---------------------------------------------------------------- environment


def describe_environment(run: Run) -> dict:
    command = (
        '. /etc/os-release; echo "os=$PRETTY_NAME"; echo "kernel=$(uname -r)"; echo "virt=$(systemd-detect-virt --vm || true)"; '
        'echo "marker=$(cat /etc/rfl/track-b-guest 2>/dev/null)"; '
        "echo \"docker=$(sudo docker version --format '{{.Server.Version}}')\"; "
        f"echo \"image=$(sudo docker image inspect --format '{{{{.Id}}}}' {IMAGE_TAG} 2>/dev/null)\"; "
        "dpkg-query -W -f='package ${Package}=${Version}\\n' docker.io docker-buildx containerd runc"
    )
    done = run.guest.sh(command)
    run.raw("guest/environment.txt", f"$ {command}\n{done.stdout}{done.stderr}")
    pairs = [line.partition("=") for line in done.stdout.splitlines() if "=" in line]
    env = {key: value for key, _, value in pairs if not key.startswith("package ")}
    env["packages"] = {key.split(" ", 1)[1]: value for key, _, value in pairs if key.startswith("package ")}
    for key in ("os", "kernel", "virt", "marker", "docker", "image"):
        env.setdefault(key, "")
    components = run.lock["components"]
    tools = {
        "ibsim": components["ibsim.ibsim"]["value"], "opensm": components["ibsim.opensm"]["value"],
        "infiniband-diags": components["ibsim.infiniband_diags"]["value"], "routing_engine": run.params["routing_engine"],
        "docker": env["docker"], **{f"guest {k}": v for k, v in env["packages"].items()},
    }
    section: dict = {"guests": [{"name": run.guest.name, "os": env["os"], "kernel": env["kernel"]}], "tools": tools}
    if run.image:
        section["images"] = [{"name": IMAGE_TAG, "digest": run.image}]
    rfl.set_section(run.dir, "environment", section)
    return env


def broken_netfile(params: dict, full: Topology) -> str:
    """The net file of a deliberate break: the scenario's topology without its break link."""
    cut = Port(params["break"]["node"], params["break"]["port"])
    header = (f"Deliberate break: {params['topology']} without the link {cut}-{full.peer(cut)}.\n"
              "Every expectation of the run still comes from the full topology file.")
    return render_netfile(full.without_link_at(cut), header)


def record_inputs(run: Run) -> None:
    files = [CONFIG, run.topology_file, *IMAGE_INPUTS, TRACK / "scripts" / "guest.sh"]
    parameters = {"topology": run.params["topology"], "sm_host": run.params["sm_host"],
                  "routing_engine": run.params["routing_engine"], "simulated_netfile": run.params["topology"]}
    if run.cut is not None:
        simulated = run.raw("break/simulated.net", run.simulated)
        files.append(run.dir / simulated)
        parameters["simulated_netfile"] = simulated
        parameters["break_link"] = f"{run.cut}-{run.full.peer(run.cut)}"
    rfl.add_inputs(run.dir, files)
    if "fault" in run.params:
        parameters["fault_port"] = str(Port(run.params["fault"]["node"], run.params["fault"]["port"]))
    rfl.set_section(run.dir, "inputs", {"parameters": parameters})
    resolved = {"scenario": run.scenario, "parameters": run.params, "deliberate_break": run.cut is not None,
                "image": {"tag": IMAGE_TAG, "digest": run.image},
                "lock": {k: v["value"] for k, v in run.lock["components"].items() if k.startswith("ibsim.")},
                "opensm_conf": (guest.REPO_ROOT / "images/ibsim/opensm.conf").read_text()}
    (run.dir / "resolved-config.yaml").write_text(yaml.safe_dump(resolved, sort_keys=False))


def host_scan(run: Run, when: str) -> tuple[list[str], str]:
    ps = subprocess.run(["ps", "-eo", "pid=,comm=,args="], capture_output=True, text=True).stdout
    found = host_simulator_processes(ps)
    try:
        docker = subprocess.run(["docker", "ps", "--all", "--filter", "label=rfl.track=b-ibsim", "--format", "{{.Names}}"],
                                capture_output=True, text=True, timeout=30)
        containers = f"docker ps --all --filter label=rfl.track=b-ibsim: rc={docker.returncode} {docker.stdout.strip() or 'none'} {docker.stderr.strip()}"
        found += [f"host container {n}" for n in docker.stdout.split()]
    except (OSError, subprocess.TimeoutExpired) as exc:
        containers = f"docker ps on the host could not run: {exc}"
    text = (f"host scan {when}: ps -eo pid=,comm=,args= lines whose command is one of {', '.join(sorted(SIMULATOR_COMMANDS))}\n"
            + ("\n".join(found) if found else "none") + f"\n{containers}\n")
    return found, run.raw(f"host/scan-{when}.txt", text)


# ---------------------------------------------------------------- fabric


def wait_subnet_up(run: Run, after: int, timeout_s: float) -> tuple[str, int] | None:
    """The OpenSM log once it holds more than `after` SUBNET UP lines and stays unchanged for settle_s."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        log = run.opensm_log()
        ups = log.count(observe.SUBNET_UP)
        if ups > after:
            time.sleep(run.params["settle_s"])
            settled = run.opensm_log()
            if settled.count(observe.SUBNET_UP) == ups:
                return settled, ups
            continue
        time.sleep(0.5)
    return None


def probe_lids(run: Run, phase: str, lids: list[int]) -> tuple[dict[int, str | None], str]:
    loop = " ".join(
        f"echo '== {lid}'; {guest.GUEST_SH} exec {run.key} tool smpquery nodedesc {lid} 2>&1; echo rc=$?;" for lid in lids
    )
    done = run.guest.sh(f"sudo sh -c {shlex.quote(loop)}", timeout_s=60 + 10 * len(lids))
    path = run.raw(f"{phase}/smpquery-nodedesc.txt", done.stdout + done.stderr)
    return observe.nodedesc_answers(done.stdout), path


def snapshot(run: Run, phase: str, probe: list[int] | None = None) -> Snapshot:
    evidence = {}
    text, evidence["ibnetdiscover"] = run.tool(phase, "ibnetdiscover")
    discovered = parse_ibnetdiscover(text)
    text, evidence["iblinkinfo"] = run.tool(phase, "iblinkinfo", "-l")
    states = observe.link_states(text)
    text, evidence["ibswitches"] = run.tool(phase, "ibswitches")
    switches = observe.switch_lids(text)
    _, evidence["ibhosts"] = run.tool(phase, "ibhosts")
    text, evidence["sminfo"] = run.tool(phase, "sminfo")
    sm = observe.sminfo(text)
    tables, pages = {}, []
    for name, (lid, _) in sorted(switches.items()):
        done = run.gsh("exec", run.key, "tool", "ibroute", str(lid))
        pages.append(f"$ ibroute {lid}\n{done.stdout}{done.stderr}")
        if done.rc != 0:
            raise ToolFailed(f"ibroute {lid}", run.raw(f"{phase}/ibroute.txt", "\n".join(pages)), done.rc)
        switch, table = observe.routes(done.stdout)
        tables[switch] = table
    evidence["ibroute"] = run.raw(f"{phase}/ibroute.txt", "\n".join(pages))
    answers, evidence["smpquery"] = probe_lids(run, phase, probe if probe is not None else sorted(lid_names(discovered)))
    log = run.opensm_log()
    evidence["opensm.log"] = run.raw(f"{phase}/opensm.log", log)
    return Snapshot(discovered, states, tables, sm, answers, log, evidence)


def baseline_checks(run: Run, shot: Snapshot, expected: Topology) -> None:
    ev = shot.evidence
    engines = observe.configured_engine(shot.log)
    run.record("subnet-up", as_outcome(verdict.subnet_manager(
        shot.sm, run.sm_port, shot.discovered, engines, run.params["routing_engine"], shot.log.count(observe.SUBNET_UP)),
        [ev["sminfo"], ev["opensm.log"], ev["ibnetdiscover"]]))
    run.record("topology-discovered", as_outcome(verdict.topology_matches(expected, shot.discovered.topology),
                                                 [ev["ibnetdiscover"], ev["ibhosts"], ev["ibswitches"]]))
    run.record("lids-unique", as_outcome(verdict.lids_unique(expected, shot.discovered), [ev["ibnetdiscover"]]))
    run.record("port-states", as_outcome(verdict.port_states(expected, shot.states), [ev["iblinkinfo"]]))
    run.record("routes-shortest", as_outcome(verdict.routes_shortest(expected, shot.tables, shot.discovered),
                                             [ev["ibroute"], ev["ibnetdiscover"]]))
    run.record("lid-reachability", as_outcome(verdict.reachability(shot.answers, lid_names(shot.discovered), set()),
                                              [ev["smpquery"]]))


def lids_kept(baseline: Discovered, now: Discovered, expected: Topology) -> verdict.Verdict:
    changed = [f"{p} {baseline.lids.get(p)} -> {now.lids.get(p)}" for p in verdict.endpoint_ports(expected)
               if baseline.lids.get(p) != now.lids.get(p)]
    return verdict.Verdict(not changed, f"LIDs changed: {', '.join(changed)}" if changed else "every endpoint kept its LID")


def console(run: Run, phase: str, command: str) -> tuple[float | None, str, bool]:
    """Guest clock just before the command, the raw evidence, and whether ibsim accepted it."""
    done = run.guest.sh(f"date +%s.%N; sudo {guest.GUEST_SH} exec {run.key} console {shlex.quote(command)}")
    path = run.raw(f"{phase}/console.txt", f"$ {command}\n{done.stdout}{done.stderr}")
    first, _, reply = done.stdout.partition("\n")
    try:
        at = float(first)
    except ValueError:
        at = None
    accepted = done.rc == 0 and not any(line.startswith("#") for line in reply.splitlines())
    return at, path, accepted


def resweep(run: Run, phase: str, command: str, check_id: str, ups_before: int) -> float | None:
    at, path, accepted = console(run, phase, command)
    if not accepted:
        run.record(check_id, failed("command-failed", [path], f"ibsim did not accept: {command}"))
        return None
    found = wait_subnet_up(run, ups_before, run.params["resweep_timeout_s"])
    if found is None:
        run.record(check_id, failed("timeout", [path], f"no new SUBNET UP within {run.params['resweep_timeout_s']} s after {command}"))
        return None
    log, ups = found
    log_path = run.raw(f"{phase}/opensm-resweep.log", log)
    after = [t for t in observe.subnet_up_times(log, run.year) if at is not None and t >= at]
    seconds = round(after[0] - at, 3) if after else None
    run.metric(f"opensm.resweep_after_{phase}_s", seconds, "s", log_path,
               "the guest clock or the log gave no SUBNET UP time after the command", observation="this simulator run")
    run.record(check_id, passed([path, log_path], f"ibsim accepted {command}; OpenSM logged SUBNET UP {ups - ups_before} more "
                                                  f"time(s), the first {seconds} s after the command"))
    return seconds


def fault_and_recovery(run: Run, base: Snapshot, full: Topology) -> None:
    node, number = run.params["fault"]["node"], run.params["fault"]["port"]
    port = Port(node, number)
    peer = full.peer(port)
    failed_ports = [port, peer]
    degraded = full.without_link_at(port).reachable_from(run.sm_port.node)
    lids = sorted(lid_names(base.discovered))
    names = lid_names(base.discovered)
    cut_off = {lid for lid, n in names.items() if n not in {x.name for x in degraded.nodes}}
    ups = base.log.count(observe.SUBNET_UP)

    if resweep(run, "fault", f'Unlink "{node}"[{number}]', "fault-resweep", ups) is None:
        return
    shot = snapshot(run, "fault", probe=lids)
    ev = shot.evidence
    run.record("fault-topology", as_outcome(combine(
        verdict.topology_matches(degraded, shot.discovered.topology), verdict.port_states(degraded, shot.states),
        lids_kept(base.discovered, shot.discovered, degraded)), [ev["ibnetdiscover"], ev["iblinkinfo"]]))
    run.record("fault-routes", as_outcome(combine(
        verdict.routes_shortest(degraded, shot.tables, shot.discovered),
        verdict.rerouted(base.tables, shot.tables, failed_ports, shot.discovered)),
        [ev["ibroute"], base.evidence["ibroute"]]))
    want = {lid: n for lid, n in names.items() if lid not in cut_off}
    run.record("fault-reachability", as_outcome(verdict.reachability(shot.answers, want, cut_off), [ev["smpquery"]]))

    if resweep(run, "recovered", f'ReLink "{node}"[{number}]', "recovery-resweep", shot.log.count(observe.SUBNET_UP)) is None:
        return
    back = snapshot(run, "recovered", probe=lids)
    ev = back.evidence
    expected = full.reachable_from(run.sm_port.node)
    run.record("recovered", as_outcome(combine(
        verdict.topology_matches(expected, back.discovered.topology), verdict.port_states(expected, back.states),
        lids_kept(base.discovered, back.discovered, expected),
        verdict.routes_shortest(expected, back.tables, back.discovered),
        verdict.reachability(back.answers, names, set())),
        [ev["ibnetdiscover"], ev["iblinkinfo"], ev["ibroute"], ev["smpquery"]]))


def scenario(run: Run) -> None:
    record_inputs(run)
    env = describe_environment(run)
    guest_ev = "raw/guest/environment.txt"
    image_ok = run.image is not None and env["image"] == run.image
    run.record("image-pinned", passed([guest_ev], f"{IMAGE_TAG} in {run.guest.name} is {run.image}, the ibsim.image pin")
               if image_ok else failed("assertion-failed", [guest_ev],
                                       f"{IMAGE_TAG} in {run.guest.name} is {env['image']!r}, versions.lock.yaml pins {run.image}"))
    if run.image is None:
        return
    started = run.gsh("start", run.key, run.image, run.params["sm_host"], "-R", run.params["routing_engine"],
                      stdin=run.simulated.encode())
    run_args = run.raw("container/docker-run.txt", started.stdout + started.stderr)
    if started.rc != 0:
        run.record("container-locked-down", failed("command-failed", [run_args], f"guest.sh start exited {started.rc}"))
        return
    inspect = run.gsh("inspect", run.key)
    top = run.gsh("top", run.key)
    inspect_ev = run.raw("container/inspect.json", inspect.stdout + inspect.stderr)
    top_ev = run.raw("container/top.txt", top.stdout + top.stderr)
    uids = [line.split()[0] for line in top.stdout.splitlines()[1:] if line.split()]
    problems = container_problems(json.loads(inspect.stdout)[0], run.image, uids) if inspect.rc == 0 else ["docker inspect failed"]
    guest_kernel = run.lock["components"]["ibsim.guest_kernel"]["value"]
    if env["kernel"] != guest_kernel:
        problems.append(f"guest kernel {env['kernel']}, versions.lock.yaml pins {guest_kernel}")
    if env["virt"] in ("", "none") or env["marker"] != run.guest.name:
        problems.append(f"guest virtualization {env['virt']!r} and marker {env['marker']!r}, want a VM marked {run.guest.name}")
    run.record("container-locked-down", as_outcome(
        verdict.Verdict(not problems, "; ".join(problems) if problems else
                        f"{run.guest.name} is a {env['virt']} guest on kernel {env['kernel']}; the container runs the locked image "
                        f"as {CONTAINER_USER} with no network, no device, no capabilities, no-new-privileges, a read-only root and "
                        "AppArmor docker-default, and every process in it runs as UID 10001"),
        [run_args, inspect_ev, top_ev, guest_ev]))

    found = wait_subnet_up(run, 0, run.params["subnet_up_timeout_s"])
    if found is None:
        log = run.raw("baseline/opensm.log", run.opensm_log())
        run.record("subnet-up", failed("timeout", [log], f"no SUBNET UP within {run.params['subnet_up_timeout_s']} s"))
        return
    log, _ = found
    start, first_up = observe.first_log_time(log, run.year), (observe.subnet_up_times(log, run.year) or [None])[0]
    expected = run.full.reachable_from(run.sm_port.node)
    try:
        base = snapshot(run, "baseline")
    except ToolFailed as exc:
        for check_id in BASELINE_CHECKS:
            run.record(check_id, failed("command-failed", [exc.evidence], str(exc), exit_code=exc.rc))
        return
    run.metric("opensm.first_subnet_up_s", round(first_up - start, 3) if start and first_up else None, "s",
               base.evidence["opensm.log"], "the log gave no start or SUBNET UP time", observation="this simulator run")
    baseline_checks(run, base, expected)
    if "fault" in run.params:
        try:
            fault_and_recovery(run, base, run.full)
        except ToolFailed as exc:
            for check_id in FAULT_CHECKS:
                if check_id not in run.recorded:
                    run.record(check_id, failed("command-failed", [exc.evidence], str(exc), exit_code=exc.rc))


def save_logs(run: Run) -> None:
    for name in ("ibsim.out", "ibsim.err", "opensm.out", "opensm.log"):
        done = run.gsh("read", run.key, name)
        if done.rc == 0 and done.stdout:
            run.raw(f"container/{name}", done.stdout)


def close(run: Run) -> None:
    recorded = set(run.recorded)
    if "container-locked-down" in recorded:
        save_logs(run)
    stopped = run.gsh("stop", run.key)
    listing = run.gsh("ls")
    left = [line for line in listing.stdout.splitlines() if line.split() and line.split()[0] == f"rfl-ibsim-{run.key}"]
    ev = run.raw("container/after-stop.txt", f"$ guest.sh stop {run.key}\nrc={stopped.rc} {stopped.stderr}\n"
                                             f"$ guest.sh ls\nrc={listing.rc}\n{listing.stdout or '(no Track B container)'}\n")
    run.record("no-container-left", passed([ev], f"guest.sh stop removed rfl-ibsim-{run.key}; no Track B container remains")
               if stopped.rc == 0 and listing.rc == 0 and not left else
               failed("assertion-failed", [ev], f"still listed: {left}" if left else f"stop rc={stopped.rc}, ls rc={listing.rc}"))


def main(scenario_name: str, inventories: list[Path], run_id: str | None, broken: bool = False) -> int:
    run = Run(scenario_name, inventories, run_id, broken)
    run.say(f"run {run.run_id} scenario {scenario_name} in {run.dir}, guest {run.guest.name}"
            + (f", deliberate break: the simulated fabric lacks the link at {run.cut}" if run.cut else ""))
    before, scan_start = host_scan(run, "start")
    try:
        scenario(run)
    except KeyboardInterrupt:
        run.warn("interrupted, removing this run's container")
    except Exception as exc:
        run.warn(f"runner error, removing this run's container: {exc!r}")
        raise
    finally:
        close(run)
        after, scan_end = host_scan(run, "end")
        scans = [scan_start, scan_end]
        run.record("host-untouched", passed(scans, "no simulator process and no Track B container on the host at the start "
                                                   "and the end of the run")
                   if not before and not after else failed("assertion-failed", scans, f"found on the host: {before + after}"))
        if run.metrics:
            (run.dir / "metrics.json").write_text(json.dumps(
                {"schema_version": 1, "result_kind": "ib-management-simulation", "metrics": run.metrics}, indent=2) + "\n")
        rfl.set_section(run.dir, "timing", {"wall_clock_seconds": round(time.monotonic() - run.started, 1)})
        code, message = rfl.finalize(run.dir)
        write_summary(run)
        run.say(message)
    return code
