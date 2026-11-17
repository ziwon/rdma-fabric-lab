"""make track-c: one Track C scenario inside the Track C guest, recorded through the rfl CLI.

    python -m ns3 run --scenario c-02-single-flow -i <inventory> -i <override> [--run-id <id>] [--break <name>]

The runner stays on the host. It renders the inputs, sends them to guest.sh run, which starts one
locked-down container of the pinned image per run of the scenario, and reads every output back over
SSH. Each scenario runs twice with the same inputs, run1 and run2, and the two must agree.
--break <name> applies one entry of BREAKS while every expectation keeps the declared scenario, so
that run must end failed with the entry's checks failed.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from run import rfl
from run.base import Outcome, RunBase, failed, passed, write_summary

from . import compare, guest, incast, network, results, verdict

TRACK = guest.REPO_ROOT / "tracks" / "c-ns3"
CONFIG = TRACK / "config" / "scenarios.yaml"
LOCK = guest.REPO_ROOT / "versions.lock.yaml"
IMAGE_TAG = "rfl-ns3:m6"
IMAGE_INPUTS = [guest.REPO_ROOT / "images" / "ns3" / f for f in ("Dockerfile", "rfl-ns3", "apt-snapshot-install", "fetch-sources")]
DRIVER = TRACK / "driver" / "rfl-star.cc"
SLOTS = ("run1", "run2")
RESULT_KIND = "packet-model-simulation"

ENVIRONMENT_CHECKS = ("image-pinned", "container-locked-down")
SCENARIO_CHECKS = {
    "simai-example": ("example-completes", "result-files-written", "rerun-identical"),
    "star": ("config-applied", "flow-completes", "fct-lower-bound", "zero-drops", "no-pfc-pause", "no-ecn-mark",
             "rerun-identical"),
    "incast": ("config-applied", "same-traffic", "flows-accounted", "fct-bounds", "pfc-as-configured", "ecn-as-configured",
               "profiles-differ", "rerun-identical"),
}
CLOSING_CHECKS = ("no-container-left", "host-untouched")


def check_ids(kind: str) -> tuple[str, ...]:
    """Every check a scenario of this kind records, in order. Its spec declares exactly these."""
    return ENVIRONMENT_CHECKS + SCENARIO_CHECKS[kind] + CLOSING_CHECKS


@dataclass(frozen=True)
class Break:
    fails: dict[str, tuple[str, ...]]
    what: str

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(self.fails)


def every_kind(*checks: str) -> dict[str, tuple[str, ...]]:
    return {kind: checks for kind in SCENARIO_CHECKS}


BREAKS = {
    "image-unpinned": Break(every_kind("image-pinned"), "the runner expects ns3.base_image instead of ns3.image"),
    "container-unlocked": Break(every_kind("container-locked-down"), "guest.sh run leaves out --read-only"),
    "container-left": Break(every_kind("no-container-left"), "the runner checks for containers without calling guest.sh stop first"),
    "host-process": Break(every_kind("host-untouched"), "a copy of sleep named SimAI_simulator runs on the host during the run"),
    "rerun-input-differs": Break(every_kind("rerun-identical"), "run2 gets other inputs than run1, given per scenario as rerun_break"),
    "workload-missing": Break({"simai-example": ("example-completes", "result-files-written")},
                              "the simulate step names a workload file that does not exist"),
    "rate-not-applied": Break({"star": ("config-applied", "fct-lower-bound"), "incast": ("config-applied", "fct-bounds")},
                              "the simulator gets twice the declared rate on every link"),
    "flow-cut-short": Break({"star": ("flow-completes",)}, "the simulator stops at 10 us, before the flow can finish"),
    "lossy-links": Break({"star": ("zero-drops",)}, "every link device drops 1% of the packets it receives (RateErrorModel)"),
    "ecn-bottleneck": Break({"star": ("no-ecn-mark",)}, "the receiver link runs at a quarter of the declared rate"),
    "pfc-bottleneck": Break({"star": ("no-pfc-pause",)},
                            "the receiver link runs at a quarter of the declared rate and the switch buffer is 1 MB with ECN off"),
    "profile-changes-traffic": Break({"incast": ("same-traffic",)}, "every c-ecn case sends 1000 B more per flow"),
    "flow-dropped": Break({"incast": ("flows-accounted",)}, "every case gets its last flow left out"),
    "pfc-not-honored": Break({"incast": ("pfc-as-configured",)}, "the PFC-on profiles run with PFC off"),
    "ecn-left-on": Break({"incast": ("ecn-as-configured",)}, "the ECN-off profiles run with ECN on"),
    "profiles-identical": Break({"incast": ("profiles-differ",)}, "every profile runs with the settings of c-none"),
}


def simulated_star(net: network.Star, name: str | None, slot: str, rerun_break: dict) -> network.Star:
    """The network the simulator gets. Only a deliberate break makes it differ from the declared one."""
    if name == "rate-not-applied":
        for host in net.hosts:
            net = network.with_link_rate(net, host, 2)
    elif name == "flow-cut-short":
        net = replace(net, stop_ns=10_000)
    elif name == "lossy-links":
        net = replace(net, error_rate=0.01)
    elif name in ("ecn-bottleneck", "pfc-bottleneck"):
        receiver = net.flows[0].dst
        net = network.with_link_rate(net, receiver, 0.25)
        if name == "pfc-bottleneck":
            net = replace(net, buffer_bytes=1_000_000, ecn=False)
    elif name == "rerun-input-differs" and slot == "run2":
        shift = network.time_ns(rerun_break["flow_start_shift"])
        net = replace(net, flows=tuple(replace(f, start_ns=f.start_ns + shift) for f in net.flows))
    return net


def simulated_case(case: incast.Case, name: str | None, slot: str, rerun_break: dict, profiles: dict) -> network.Star:
    """The network one incast case's simulator gets. Only a deliberate break makes it differ."""
    net = case.net
    if name in ("rate-not-applied", "rerun-input-differs"):
        return simulated_star(net, name, slot, rerun_break)
    if name == "profile-changes-traffic" and case.profile == "c-ecn":
        return replace(net, flows=tuple(replace(f, payload_bytes=f.payload_bytes + 1000) for f in net.flows))
    if name == "flow-dropped":
        return replace(net, flows=net.flows[:-1])
    if name == "pfc-not-honored" and net.pfc:
        return replace(net, pfc=False)
    if name == "ecn-left-on" and not net.ecn:
        return replace(net, ecn=True)
    if name == "profiles-identical":
        none = profiles["c-none"]
        return replace(net, pfc=none["pfc"], ecn=none["ecn"], cc_mode=network.CC_MODES[none["cc"]])
    return net


def simulated_steps(steps: list[list[str]], name: str | None, slot: str, rerun_break: dict) -> list[list[str]]:
    steps = [list(step) for step in steps]
    if name == "workload-missing":
        for step in steps:
            if step[1] == "simai":
                step[step.index("-w") + 1] = "/opt/simai/example/rfl-no-such-workload.txt"
    elif name == "rerun-input-differs" and slot == "run2":
        step_name, flag, value = rerun_break["step"], rerun_break["flag"], rerun_break["value"]
        for step in steps:
            if step[0] == step_name:
                step[step.index(flag) + 1] = value
    return steps


def steps_tsv(steps: list[list[str]]) -> bytes:
    for step in steps:
        if any("\t" in cell or "\n" in cell or not cell for cell in step):
            raise ValueError(f"step {step} holds an empty cell or a tab or newline")
    return "".join("\t".join(step) + "\n" for step in steps).encode()


class Run(RunBase):
    def __init__(self, scenario: str, inventories: list[Path], run_id: str | None, broken: str | None):
        super().__init__(TRACK / "scenarios" / f"{scenario}.yaml", run_id,
                         {"name": broken, "description": BREAKS[broken].what} if broken else None)
        self.scenario = scenario
        config = yaml.safe_load(CONFIG.read_text())
        self.params = config["scenarios"][scenario]
        self.kind = self.params["kind"]
        if broken is not None and self.kind not in BREAKS[broken].kinds:
            raise ValueError(f"break {broken} does not apply to {scenario}")
        self.broken = broken
        self.must_fail = BREAKS[broken].fails[self.kind] if broken else ()
        self.shared = incast.merged(config["incast"], self.params.get("overrides", {})) if self.kind == "incast" else {}
        self.cases = incast.cases(self.shared, self.params["workload"]) if self.kind == "incast" else []
        self.key = f"{self.run_id}-{scenario}"
        self.lock = yaml.safe_load(LOCK.read_text())
        components = self.lock["components"]
        self.image = components["ns3.image"]["value"]
        self.expected_image = components["ns3.base_image"]["value"] if broken == "image-unpinned" else self.image
        self.guest = guest.load(inventories, self.log)
        self.metrics_note = ("Packet-model results of ns-3-alibabacloud inside the Track C guest, not hardware. Metrics labeled "
                             "domain=simulator-cost measure the simulator process and say nothing about the modeled network.")
        self.rerun_break = self.params.get("rerun_break", {})

    def gsh(self, *args: str, stdin: bytes | None = None, timeout_s: float = 120, binary: bool = False):
        return self.guest.guest_sh(*args, stdin=stdin, timeout_s=timeout_s, binary=binary)

    def metric(self, name: str, value: float | None, unit: str, source: str, domain: str, reason: str | None = None,
               **labels: str) -> None:
        self.metrics.append({"name": name, "value": value, "unit": unit, "absent_reason": None if value is not None else reason,
                             "source": source, "labels": {"domain": domain, **labels}})


def outcome(result: verdict.Verdict, evidence: list[str]) -> Outcome:
    return passed(evidence, result.detail) if result.ok else failed("assertion-failed", evidence, result.detail, exit_code=None)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- environment


def describe_environment(run: Run) -> dict:
    command = (
        '. /etc/os-release; echo "os=$PRETTY_NAME"; echo "kernel=$(uname -r)"; echo "virt=$(systemd-detect-virt --vm || true)"; '
        'echo "marker=$(cat /etc/rfl/track-c-guest 2>/dev/null)"; echo "nproc=$(nproc)"; '
        "echo \"mem_kib=$(awk '/MemTotal/ {print $2}' /proc/meminfo)\"; "
        "echo \"docker=$(sudo docker version --format '{{.Server.Version}}')\"; "
        f"echo \"image=$(sudo docker image inspect --format '{{{{.Id}}}}' {IMAGE_TAG} 2>/dev/null)\"; "
        "dpkg-query -W -f='package ${Package}=${Version}\\n' docker.io docker-buildx containerd runc"
    )
    done = run.guest.sh(command)
    run.raw("guest/environment.txt", f"$ {command}\n{done.stdout}{done.stderr}")
    pairs = [line.partition("=") for line in done.stdout.splitlines() if "=" in line]
    env = {key: value for key, _, value in pairs if not key.startswith("package ")}
    env["packages"] = {key.split(" ", 1)[1]: value for key, _, value in pairs if key.startswith("package ")}
    for key in ("os", "kernel", "virt", "marker", "docker", "image", "nproc", "mem_kib"):
        env.setdefault(key, "")
    components = run.lock["components"]
    tools = {"docker": env["docker"], "guest vcpus": env["nproc"], "guest memory KiB": env["mem_kib"],
             **{f"guest {k}": v for k, v in env["packages"].items()},
             **{name: components[name]["value"] for name in ("ns3.gcc", "ns3.cmake", "ns3.python", "ns3.apt_snapshot")}}
    sources = [{"name": name, "sha": components[f"ns3.{key}"]["value"]}
               for name, key in (("SimAI", "simai"), ("ns-3-alibabacloud", "ns3_alibabacloud"), ("SimCCL", "simccl"))]
    section: dict = {"guests": [{"name": run.guest.name, "os": env["os"], "kernel": env["kernel"]}], "tools": tools,
                     "sources": sources}
    if run.image:
        section["images"] = [{"name": IMAGE_TAG, "digest": run.image},
                             {"name": components["ns3.base_image"]["ref"], "digest": components["ns3.base_image"]["value"]}]
    rfl.set_section(run.dir, "environment", section)
    return env


def host_scan(run: Run, when: str) -> tuple[list[str], str]:
    ps = subprocess.run(["ps", "-eo", "pid=,comm=,args="], capture_output=True, text=True).stdout
    found = verdict.host_simulator_processes(ps)
    try:
        docker = subprocess.run(["docker", "ps", "--all", "--filter", "label=rfl.track=c-ns3", "--format", "{{.Names}}"],
                                capture_output=True, text=True, timeout=30)
        containers = f"docker ps --all --filter label=rfl.track=c-ns3: rc={docker.returncode} {docker.stdout.strip() or 'none'} {docker.stderr.strip()}"
        found += [f"host container {n}" for n in docker.stdout.split()] if docker.returncode == 0 else []
    except (OSError, subprocess.TimeoutExpired) as exc:
        containers = f"docker ps on the host could not run: {exc}"
    names = ", ".join(sorted({p[:15] for p in verdict.SIMULATOR_PROGRAMS}))
    text = (f"host scan {when}: ps -eo pid=,comm=,args= lines whose command name is one of {names}\n"
            + ("\n".join(found) if found else "none") + f"\n{containers}\n")
    return found, run.raw(f"host/scan-{when}.txt", text)


class Decoy:
    """For the host-process break: a copy of sleep named like the simulator, on the host, killed afterwards.

    It carries RFL_RUN=<run-id>, and its directory the run id, so make cleanup finds both when the runner died first."""

    def __init__(self, run_id: str):
        self.dir = Path(tempfile.mkdtemp(prefix=f"rfl-ns3-decoy-{run_id}-"))
        binary = self.dir / "SimAI_simulator"
        shutil.copy2("/bin/sleep", binary)
        self.process = subprocess.Popen([str(binary), "600"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, env={**os.environ, "RFL_RUN": run_id})

    def close(self) -> None:
        self.process.kill()
        self.process.wait(timeout=10)
        shutil.rmtree(self.dir)


# ---------------------------------------------------------------- inputs and container runs


@dataclass
class SlotRun:
    slot: str
    rc: int
    out: Path
    evidence: str
    files: dict[str, bytes]


def header(run: Run, slot: str, case: str = "") -> str:
    return (f"Rendered by python -m ns3 from {CONFIG.relative_to(guest.REPO_ROOT)} scenario {run.scenario}{case}, {slot}."
            + (f"\nDeliberate break {run.broken}: {BREAKS[run.broken].what}." if run.broken else ""))


def slot_inputs(run: Run, slot: str) -> dict[str, bytes]:
    if run.kind == "incast":
        files = {f"{c.step}.txt": network.render(simulated_case(c, run.broken, slot, run.rerun_break, run.shared["profiles"]),
                                                 header(run, slot, f" case {c.step}")).encode() for c in run.cases}
        steps = [[c.step, "rfl-star", f"/run/rfl/in/{c.step}.txt", f"/run/rfl/out/work/{c.step}"] for c in run.cases]
        return {"steps.tsv": steps_tsv(steps), **files}
    steps = run.params["steps"]
    if run.kind == "simai-example":
        steps = simulated_steps(steps, run.broken, slot, run.rerun_break)
    files = {"steps.tsv": steps_tsv(steps)}
    if run.kind == "star":
        declared = network.star(run.params["network"])
        simulated = simulated_star(declared, run.broken, slot, run.rerun_break)
        files["network.txt"] = network.render(simulated, header(run, slot)).encode()
    return files


def run_slot(run: Run, slot: str) -> SlotRun:
    files = slot_inputs(run, slot)
    for name, data in files.items():
        run.raw(f"{slot}/in/{name}", data)
    unlocked = ("unlocked",) if run.broken == "container-unlocked" else ()
    done = run.gsh("run", run.key, slot, run.image, *unlocked, stdin=guest.tar_of(files), timeout_s=3700)
    run.raw(f"{slot}/guest-sh-run.txt", f"rc={done.rc}\n{done.stdout}{done.stderr}")
    fetched = run.gsh("fetch", run.key, slot, timeout_s=600, binary=True)
    target = run.dir / "raw" / slot
    if fetched.rc == 0:
        with tarfile.open(fileobj=io.BytesIO(fetched.stdout)) as tar:
            tar.extractall(target, filter="data")
    else:
        run.raw(f"{slot}/fetch-error.txt", f"rc={fetched.rc}\n{fetched.stderr}")
    run.say(f"{slot}: container exit {done.rc}")
    return SlotRun(slot, done.rc, target / "out", f"raw/{slot}/guest-sh-run.txt", files)


def locked_down(run: Run, env: dict, slots: list[SlotRun]) -> Outcome:
    problems, evidence = [], ["raw/guest/environment.txt"]
    for slot in slots:
        inspect = run.gsh("inspect", run.key, slot.slot)
        evidence += [f"raw/{slot.slot}/docker-run.txt", run.raw(f"{slot.slot}/inspect.json", inspect.stdout + inspect.stderr),
                     f"raw/{slot.slot}/out/identity.txt"]
        identity_file = slot.out / "identity.txt"
        identity = dict(line.partition("=")[::2] for line in identity_file.read_text().splitlines() if "=" in line) \
            if identity_file.is_file() else {}
        found = (verdict.container_problems(json.loads(inspect.stdout)[0], run.image, identity) if inspect.rc == 0
                 else ["docker inspect failed"])
        problems += [f"{slot.slot}: {p}" for p in found]
    guest_kernel = run.lock["components"]["ns3.guest_kernel"]["value"]
    if env["kernel"] != guest_kernel:
        problems.append(f"guest kernel {env['kernel']}, versions.lock.yaml pins {guest_kernel}")
    if env["virt"] in ("", "none") or env["marker"] != run.guest.name:
        problems.append(f"guest virtualization {env['virt']!r} and marker {env['marker']!r}, want a VM marked {run.guest.name}")
    return outcome(verdict.verdict(problems, (
        f"{run.guest.name} is a {env['virt']} guest on kernel {env['kernel']}; both containers ran the locked image as "
        f"{verdict.CONTAINER_USER} with no network, no device, no capabilities, no-new-privileges, a read-only root, "
        f"AppArmor docker-default and one writable mount {verdict.WRITABLE_MOUNT}; the entry point reported uid and gid 10001")),
        evidence)


# ---------------------------------------------------------------- scenario kinds


def step_cost(run: Run, slot: SlotRun) -> None:
    for step in run.params["steps"]:
        path = slot.out / f"{step[0]}.time"
        source = f"raw/{slot.slot}/out/{step[0]}.time"
        try:
            figures = results.gnu_time(path.read_text())
        except (OSError, results.ResultError) as exc:
            for name, unit in (("wall_s", "s"), ("max_rss_kib", "KiB")):
                run.metric(f"simulator.{step[0]}.{name}", None, unit, slot.evidence, "simulator-cost",
                           f"GNU time output missing or unreadable: {exc}", slot=slot.slot)
            continue
        run.metric(f"simulator.{step[0]}.wall_s", figures["wall_s"], "s", source, "simulator-cost", slot=slot.slot)
        run.metric(f"simulator.{step[0]}.max_rss_kib", figures["max_rss_kib"], "KiB", source, "simulator-cost", slot=slot.slot)


def present(out: Path, names: list[str]) -> dict[str, bytes]:
    return {name: (out / name).read_bytes() for name in names if (out / name).is_file()}


def unlisted(out: Path, listed: set[str]) -> list[str]:
    """Files under work/ and astra-sim/ that the scenario does not name."""
    found = [str(p.relative_to(out)) for top in ("work", "astra-sim") for p in (out / top).rglob("*") if p.is_file()]
    return sorted(set(found) - listed)


def simai_example(run: Run, slots: list[SlotRun]) -> float | None:
    p = run.params
    problems = []
    for slot in slots:
        for step in p["steps"]:
            rc_file = slot.out / f"{step[0]}.rc"
            rc = rc_file.read_text().strip() if rc_file.is_file() else "missing"
            if rc != "0":
                problems.append(f"{slot.slot} step {step[0]} exit {rc}")
        log = slot.out / p["completion"]["file"]
        if p["completion"]["line"] not in (log.read_text(errors="replace") if log.is_file() else ""):
            problems.append(f"{slot.slot}: {p['completion']['file']} lacks {p['completion']['line']!r}")
    evidence = [s.evidence for s in slots] + [f"raw/{s.slot}/out/{step[0]}.stdout" for s in slots for step in p["steps"]]
    run.record("example-completes", outcome(verdict.verdict(problems, (
        f"both runs exited 0 in every step ({', '.join(step[0] for step in p['steps'])}) and "
        f"{p['completion']['file']} holds {p['completion']['line']!r}")), evidence))

    compared = p["result_files"] + p["empty_result_files"]
    found = [present(slot.out, compared) for slot in slots]
    problems = [f"{slot.slot}: {name} is missing" for slot, files in zip(slots, found) for name in compared if name not in files]
    problems += [f"{slot.slot}: {name} is empty" for slot, files in zip(slots, found) for name in p["result_files"]
                 if name in files and not files[name]]
    listing = run.raw("example/result-files.txt", "".join(
        f"{slot.slot}\t{name}\t{sha256(files[name]) if name in files else 'missing'}\t{len(files.get(name, b''))}\n"
        for slot, files in zip(slots, found) for name in compared + p["log_files"]))
    run.record("result-files-written", outcome(verdict.verdict(problems, (
        f"both runs wrote the {len(p['result_files'])} result files, each non-empty, and the "
        f"{len(p['empty_result_files'])} files this example leaves empty")), [listing]))

    texts = [{name: data.decode(errors="replace") for name, data in files.items()} for files in found]
    extra = [f"{slot.slot}: {name} is not named by the scenario" for slot in slots
             for name in unlisted(slot.out, set(compared + p["log_files"]))]
    same = verdict.same_contents(*texts, "result files")
    run.record("rerun-identical", outcome(verdict.verdict(([same.detail] if not same.ok else []) + extra, same.detail), [listing]))

    for slot in slots:
        step_cost(run, slot)
    marker = p["simulated_time"]
    source = f"raw/run1/out/{marker['file']}"
    text = (slots[0].out / marker["file"]).read_text(errors="replace") if (slots[0].out / marker["file"]).is_file() else ""
    value = results.last_number_after(text, marker["after"])
    simulated = None if value is None else value * marker["seconds_per_unit"]
    run.metric("model.simulated_time_s", simulated, "s", source if value is not None else slots[0].evidence, "modeled-network",
               f"{marker['file']} holds no {marker['after']!r}", slot="run1")
    return simulated


def star_scenario(run: Run, slots: list[SlotRun]) -> float | None:
    declared = network.star(run.params["network"])
    work = [slot.out / "work" for slot in slots]
    parsed = []
    for slot, path in zip(slots, work):
        try:
            parsed.append(results.parse_star(path))
        except (OSError, results.ResultError) as exc:
            run.raw(f"{slot.slot}/parse-error.txt", f"{exc}\n")
            parsed.append(None)
    first = parsed[0]
    evidence = {name: f"raw/run1/out/work/{name}" for name in ("resolved.tsv", "links.tsv", "flows.tsv", "counters.tsv", "events.tsv",
                                                                "run.tsv")}
    if first is None:
        for check_id in SCENARIO_CHECKS["star"]:
            run.record(check_id, failed("command-failed", [slots[0].evidence], "rfl-star wrote no readable output in run1"))
        return None
    stdout = (slots[0].out / "simulate.stdout").read_text(errors="replace") if (slots[0].out / "simulate.stdout").is_file() else ""
    run.record("config-applied", outcome(verdict.config_applied(declared, first),
                                         [evidence["resolved.tsv"], evidence["links.tsv"], evidence["counters.tsv"]]))
    run.record("flow-completes", outcome(verdict.flow_completes(declared, first), [evidence["flows.tsv"], evidence["run.tsv"]]))
    bound, figures = verdict.fct_lower_bound(declared, first)
    run.record("fct-lower-bound", outcome(bound, [evidence["flows.tsv"], "raw/run1/in/network.txt"]))
    mmu_drops = results.mmu_drop_lines(stdout)
    run.record("zero-drops", outcome(verdict.zero_drops(declared, first, mmu_drops),
                                     [evidence["counters.tsv"], evidence["events.tsv"], "raw/run1/out/simulate.stdout"]))
    run.record("no-pfc-pause", outcome(verdict.no_pfc_pause(declared, first), [evidence["counters.tsv"], evidence["events.tsv"]]))
    run.record("no-ecn-mark", outcome(verdict.no_ecn_mark(declared, first),
                                      [evidence["counters.tsv"], evidence["events.tsv"], evidence["flows.tsv"]]))
    if parsed[1] is None:
        same = verdict.Verdict(False, "run2 wrote no readable output")
    else:
        same = verdict.same_contents(*(results.modeled_figures(path) for path in work), "modeled figure files")
    run.record("rerun-identical", outcome(same, [f"raw/{s.slot}/out/work/flows.tsv" for s in slots]))

    for name, value, label in figures:
        run.metric(f"model.{name}", value, "ns", evidence["flows.tsv"], "modeled-network",
                   "the flow did not complete", flow=label, slot="run1")
    for name, value in verdict.drop_figures(declared, first, mmu_drops).items():
        run.metric("model.drops." + name.replace(" ", "_").replace("(", "").replace(")", "").lower(), value, "packets",
                   evidence["counters.tsv"], "modeled-network", slot="run1")
    run.metric("model.pfc_frames_sent", first.total("tx", "pfc"), "packets", evidence["counters.tsv"], "modeled-network", slot="run1")
    run.metric("model.ce_marked_data_packets", first.event("ce_data_tx", declared.switch), "packets", evidence["events.tsv"],
               "modeled-network", slot="run1")
    for slot, out in zip(slots, parsed):
        if out is None:
            continue
        source = f"raw/{slot.slot}/out/work/run.tsv"
        run.metric("simulator.run_wall_s", int(out.run["simulator_wall_ns"]) / 1e9, "s", source, "simulator-cost", slot=slot.slot)
        run.metric("simulator.max_rss_kib", float(out.run["simulator_max_rss_kib"]), "KiB", source, "simulator-cost", slot=slot.slot)
        step_cost(run, slot)
    return first.simulated_ns / 1e9


def parse_cases(run: Run, slot: SlotRun) -> tuple[dict[str, compare.CaseOut], list[str]]:
    parsed, errors = {}, []
    for case in run.cases:
        work = slot.out / "work" / case.step
        try:
            parsed[case.step] = compare.CaseOut(case, results.parse_star(work),
                                                results.parse_series(work, slot.out / f"{case.step}.stdout-summary"))
        except (OSError, results.ResultError) as exc:
            errors.append(f"{case.step}: {exc}")
    return parsed, errors


FIGURE_COLUMNS = ("completed", "flows", "completion_ratio", "goodput_bps", "makespan_ns", "peak_queue_bytes", "pause_frames_sent",
                  "pauses_received", "pauses_handled", "resumes_handled", "pause_ns", "marks", "mid_band_mark_fraction", "cnps",
                  "rate_reductions",
                  "min_rate_bps", "mmu_drops_trace", "mmu_drops_stdout", "retransmitted_packets", "nacks", "simulated_ns")


def figures_tsv(cases: list[compare.CaseOut]) -> str:
    rows = ["step\t" + "\t".join(FIGURE_COLUMNS)]
    for c in cases:
        f = compare.figures(c)
        rows.append(c.case.step + "\t" + "\t".join("" if f[k] is None else f"{f[k]:g}" if isinstance(f[k], float) else str(f[k])
                                                   for k in FIGURE_COLUMNS))
    return "\n".join(rows) + "\n"


def incast_metrics(run: Run, cases: list[compare.CaseOut], source: str) -> None:
    groups: dict[tuple[str, str], list[compare.CaseOut]] = {}
    for c in cases:
        groups.setdefault((c.case.group, c.case.profile), []).append(c)
    for (group, profile), members in groups.items():
        f = [compare.figures(c) for c in members]
        labels = {"group": group, "profile": profile, "slot": "run1"}
        series = {
            "large_fct_ns": [v for x in f for v in x["large_fct"]], "small_fct_ns": [v for x in f for v in x["small_fct"]],
            "goodput_bps": [x["goodput_bps"] for x in f if x["goodput_bps"] is not None],
            "completion_ratio": [x["completion_ratio"] for x in f], "peak_queue_bytes": [x["peak_queue_bytes"] for x in f],
            "pauses_received": [x["pauses_received"] for x in f], "pauses_handled": [x["pauses_handled"] for x in f],
            "pause_ns": [x["pause_ns"] for x in f],
            "marks": [x["marks"] for x in f], "cnps": [x["cnps"] for x in f], "rate_reductions": [x["rate_reductions"] for x in f],
            "mmu_drops": [x["mmu_drops_trace"] for x in f], "retransmitted_packets": [x["retransmitted_packets"] for x in f],
        }
        units = {"large_fct_ns": "ns", "small_fct_ns": "ns", "goodput_bps": "bit/s", "completion_ratio": "ratio",
                 "peak_queue_bytes": "bytes", "pause_ns": "ns"}
        for name, values in series.items():
            for stat, pick in (("min", min), ("median", compare.statistics.median), ("max", max)):
                value = float(pick(values)) if values else None
                entry = {"name": f"model.{name}.{stat}", "value": value, "unit": units.get(name, "count"),
                         "absent_reason": None if values else "no sample: no flow of this class completed",
                         "source": source, "labels": {"domain": "modeled-network", **labels}}
                if values:
                    entry["sample_count"] = len(values)
                run.metrics.append(entry)


def incast_scenario(run: Run, slots: list[SlotRun]) -> float | None:
    parsed = {}
    for slot in slots:
        parsed[slot.slot], errors = parse_cases(run, slot)
        if errors:
            run.raw(f"{slot.slot}/parse-errors.txt", "\n".join(errors) + "\n")
    first = parsed["run1"]
    if len(first) != len(run.cases):
        for check_id in SCENARIO_CHECKS["incast"]:
            run.record(check_id, failed("command-failed", ["raw/run1/parse-errors.txt", slots[0].evidence],
                                        f"{len(run.cases) - len(first)} of {len(run.cases)} cases wrote no readable output in run1"))
        return None
    cases = [first[c.step] for c in run.cases]
    table = run.raw("incast/figures.tsv", figures_tsv(cases))
    summary = run.raw("comparison.tsv", compare.summary_tsv(compare.summary(cases)))
    run.raw("incast/pause-intervals.tsv", compare.pause_intervals_tsv(cases))
    steps = "raw/run1/in/steps.tsv"
    run.record("config-applied", outcome(verdict.incast_config_applied(cases), [steps, f"raw/run1/out/work/{run.cases[0].step}/resolved.tsv"]))
    files = {c.step: (slots[0].out.parent / "in" / f"{c.step}.txt").read_text() for c in run.cases}
    run.record("same-traffic", outcome(verdict.same_traffic(run.cases, files), [f"raw/run1/in/{run.cases[0].step}.txt", steps]))
    run.record("flows-accounted", outcome(verdict.flows_accounted(cases), [table]))
    run.record("fct-bounds", outcome(verdict.fct_bounds(cases), [table]))
    run.record("pfc-as-configured", outcome(verdict.pfc_as_configured(cases), [table]))
    run.record("ecn-as-configured", outcome(verdict.ecn_as_configured(cases), [table]))
    run.record("profiles-differ", outcome(verdict.profiles_differ(cases), [table, summary]))
    second = parsed.get("run2", {})
    modeled = {s.slot: {step: results.modeled_figures(s.out / "work" / step) for step in parsed[s.slot]} for s in slots}
    run.record("rerun-identical", outcome(verdict.incast_rerun_identical(modeled["run1"], modeled.get("run2", {}))
                                          if second else verdict.Verdict(False, "run2 wrote no readable output"), [summary]))
    incast_metrics(run, cases, summary)
    for slot in slots:
        outs = parsed[slot.slot].values()
        source = f"raw/{slot.slot}/out/work/{run.cases[0].step}/run.tsv"
        run.metric("simulator.run_wall_s.total", sum(int(c.out.run["simulator_wall_ns"]) for c in outs) / 1e9, "s", source,
                   "simulator-cost", slot=slot.slot)
        run.metric("simulator.max_rss_kib.max", max(float(c.out.run["simulator_max_rss_kib"]) for c in outs), "KiB", source,
                   "simulator-cost", slot=slot.slot)
    return sum(c.out.simulated_ns for c in cases) / 1e9


KINDS = {"simai-example": simai_example, "star": star_scenario, "incast": incast_scenario}


def record_inputs(run: Run) -> None:
    rfl.add_inputs(run.dir, [CONFIG, *IMAGE_INPUTS, DRIVER, TRACK / "scripts" / "guest.sh"])
    parameters = {"kind": run.kind, "deliberate_break": run.broken, "steps": run.params.get("steps")}
    seed = None
    if run.kind == "incast":
        parameters.update(workload=run.params["workload"], incast=run.shared, cases=[c.step for c in run.cases])
    if run.kind == "star":
        declared = network.star(run.params["network"])
        seed = declared.seed
        parameters["network"] = run.params["network"]
    rfl.set_section(run.dir, "inputs", {"seed": seed, "parameters": parameters})
    resolved = {"scenario": run.scenario, "parameters": run.params,
                "deliberate_break": {"name": run.broken, "what": BREAKS[run.broken].what,
                                     "must_fail": list(run.must_fail)} if run.broken else None,
                "image": {"tag": IMAGE_TAG, "digest": run.image, "expected": run.expected_image},
                "lock": {k: v["value"] for k, v in run.lock["components"].items() if k.startswith("ns3.")},
                "slots": list(SLOTS)}
    (run.dir / "resolved-config.yaml").write_text(yaml.safe_dump(resolved, sort_keys=False))


def scenario(run: Run) -> float | None:
    record_inputs(run)
    env = describe_environment(run)
    image_ok = run.image is not None and env["image"] == run.expected_image
    run.record("image-pinned", passed(["raw/guest/environment.txt"], f"{IMAGE_TAG} in {run.guest.name} is {run.image}, the ns3.image pin")
               if image_ok else failed("assertion-failed", ["raw/guest/environment.txt"],
                                       f"{IMAGE_TAG} in {run.guest.name} is {env['image']!r}, the runner expects {run.expected_image}"))
    if not image_ok:
        return None
    slots = []
    for slot in SLOTS:
        slots.append(run_slot(run, slot))
        rfl.add_inputs(run.dir, [run.dir / "raw" / slot / "in" / name for name in slots[-1].files])
    run.record("container-locked-down", locked_down(run, env, slots))
    for slot in slots:
        if slot.rc != 0:
            run.warn(f"{slot.slot}: the container exited {slot.rc}")
    return KINDS[run.kind](run, slots)


def close(run: Run) -> None:
    stopped = None if run.broken == "container-left" else run.gsh("stop", run.key)
    listing = run.gsh("ls")
    left = [line for line in listing.stdout.splitlines() if line.split() and line.split()[0].startswith(f"rfl-ns3-{run.key}-")]
    stop_text = "not called: deliberate break container-left" if stopped is None else f"rc={stopped.rc} {stopped.stderr}"
    ev = run.raw("container/after-stop.txt", f"$ guest.sh stop {run.key}\n{stop_text}\n"
                                             f"$ guest.sh ls\nrc={listing.rc}\n{listing.stdout or '(no Track C container)'}\n")
    ok = stopped is not None and stopped.rc == 0 and listing.rc == 0 and not left
    run.record("no-container-left", passed([ev], f"guest.sh stop removed the containers of {run.key}; no Track C container remains")
               if ok else failed("assertion-failed", [ev], f"still listed: {left}" if left else stop_text))
    if stopped is None:
        run.gsh("stop", run.key)


def main(scenario_name: str, inventories: list[Path], run_id: str | None, broken: str | None = None) -> int:
    run = Run(scenario_name, inventories, run_id, broken)
    run.say(f"run {run.run_id} scenario {scenario_name} in {run.dir}, guest {run.guest.name}"
            + (f", deliberate break {broken}: {BREAKS[broken].what}" if broken else ""))
    decoy = Decoy(run.run_id) if broken == "host-process" else None
    simulated = None
    try:
        before, scan_start = host_scan(run, "start")
        simulated = scenario(run)
    except KeyboardInterrupt:
        run.warn("interrupted, removing this run's containers")
    except Exception as exc:
        run.warn(f"runner error, removing this run's containers: {exc!r}")
        raise
    finally:
        close(run)
        after, scan_end = host_scan(run, "end")
        if decoy is not None:
            decoy.close()
        scans = [scan_start, scan_end]
        run.record("host-untouched", passed(scans, "no simulator process and no Track C container on the host at the start "
                                                   "and the end of the run")
                   if not before and not after else failed("assertion-failed", scans, f"found on the host: {before + after}"))
        if run.metrics:
            rfl.write_metrics(run.dir, run.metrics)
        rfl.set_section(run.dir, "timing", {"wall_clock_seconds": round(time.monotonic() - run.started, 1),
                                            "simulated_seconds": simulated})
        code, message = rfl.finalize(run.dir)
        write_summary(run)
        run.say(message)
    return code


def run_breaks(inventories: list[Path], scenarios: list[str]) -> int:
    """Run every break that applies to each scenario. Exit 0 only when each run ended failed on its checks."""
    config = yaml.safe_load(CONFIG.read_text())["scenarios"]
    rows, bad, last = [], [], ""
    for scenario_name in scenarios:
        kind = config[scenario_name]["kind"]
        if not config[scenario_name].get("run_breaks", True):
            continue
        for name, entry in BREAKS.items():
            if kind not in entry.kinds:
                continue
            while (run_id := time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())) == last:
                time.sleep(0.2)
            last = run_id
            code = main(scenario_name, inventories, run_id, name)
            manifest = json.loads((guest.REPO_ROOT / "results" / run_id / "c-ns3" / scenario_name / "manifest.json").read_text())
            status = {c["id"]: c["status"] for c in manifest["checks"]}
            must = entry.fails[kind]
            unbroken = [c for c in must if status.get(c) != "failed"]
            also = sorted(c for c, s in status.items() if s == "failed" and c not in must)
            rows.append(f"{run_id}\t{scenario_name}\t{name}\texit {code}\tmust fail: {','.join(must)}\t"
                        f"did not fail: {','.join(unbroken) or 'none'}\talso failed: {','.join(also) or 'none'}")
            if code != 1 or unbroken:
                bad.append(f"{scenario_name}/{name}")
    print("\n".join(["run_id\tscenario\tbreak\texit\tmust fail\tdid not fail\talso failed", *rows]))
    print(f"breaks that did not fail as declared: {', '.join(bad) or 'none'}")
    return 1 if bad else 0
