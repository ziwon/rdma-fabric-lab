"""The Track C scenario specs, their parameters, the break table and the guest profile agree with the runner."""

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from ns3 import run

REPO = Path(__file__).resolve().parents[2]
HARNESS = REPO / "tests/integration/qemu-lab"
CONFIG = yaml.safe_load(run.CONFIG.read_text())["scenarios"]


@pytest.mark.parametrize("scenario", sorted(CONFIG))
def test_each_spec_declares_exactly_the_checks_the_runner_records(scenario):
    spec = yaml.safe_load((run.TRACK / "scenarios" / f"{scenario}.yaml").read_text())
    assert tuple(c["id"] for c in spec["checks"]) == run.check_ids(CONFIG[scenario]["kind"])
    assert spec["result_kind"] == run.RESULT_KIND and spec["scenario"] == scenario and spec["track"] == "c-ns3"


def test_every_break_targets_checks_of_the_kinds_it_applies_to():
    for name, entry in run.BREAKS.items():
        assert entry.kinds, name
        for kind in entry.kinds:
            assert set(entry.must_fail) <= set(run.check_ids(kind)), name


@pytest.mark.parametrize("kind", sorted(run.SCENARIO_CHECKS))
def test_every_check_of_every_kind_has_a_break_that_must_fail_it(kind):
    broken = {c for entry in run.BREAKS.values() if kind in entry.kinds for c in entry.must_fail}
    assert broken == set(run.check_ids(kind))


def test_every_scenario_names_its_rerun_break_input():
    assert set(CONFIG["c-01-example"]["rerun_break"]) == {"step", "flag", "value"}
    assert set(CONFIG["c-02-single-flow"]["rerun_break"]) == {"flow_start_shift"}


def test_the_example_steps_are_the_documented_quick_smoke_test():
    steps = CONFIG["c-01-example"]["steps"]
    assert steps[0][1:] == ["gen-topo", "--ro", "-g", "8", "-gt", "H20", "-bw", "200Gbps", "-nvbw", "2400Gbps"]
    assert steps[1][1:3] == ["simai", "-t"] and "/opt/simai/example/microAllReduce.txt" in steps[1]


def test_a_break_changes_only_run2_for_the_rerun_and_the_workload_for_the_example():
    steps = CONFIG["c-01-example"]["steps"]
    rerun = CONFIG["c-01-example"]["rerun_break"]
    assert run.simulated_steps(steps, "rerun-input-differs", "run1", rerun) == steps
    changed = run.simulated_steps(steps, "rerun-input-differs", "run2", rerun)
    assert changed[0][changed[0].index("-nvbw") + 1] == "1200Gbps" and changed[1] == steps[1]
    missing = run.simulated_steps(steps, "workload-missing", "run1", rerun)
    assert missing[1][missing[1].index("-w") + 1] == "/opt/simai/example/rfl-no-such-workload.txt"
    assert run.simulated_steps(steps, None, "run2", rerun) == steps


def test_steps_with_an_empty_cell_or_a_tab_are_refused():
    with pytest.raises(ValueError):
        run.steps_tsv([["simulate", "rfl-star", "a\tb"]])
    with pytest.raises(ValueError):
        run.steps_tsv([["simulate", ""]])
    assert run.steps_tsv([["simulate", "rfl-star", "/run/rfl/in/network.txt"]]) == b"simulate\trfl-star\t/run/rfl/in/network.txt\n"


def test_the_ns3_profile_boots_one_marked_guest_with_no_reboot():
    flags = subprocess.run([sys.executable, "-I", str(HARNESS / "guests.py"), "qemu-flags", str(HARNESS / "profiles/ns3.tsv")],
                           capture_output=True, text=True, check=True).stdout.split()
    assert flags == ["-no-reboot"]
    hosts = yaml.safe_load((HARNESS / "profiles/ns3/inventory/hosts.yml").read_text())
    assert hosts["all"]["children"]["track_c_guest"]["hosts"] == {"ns3sim1": {"rfl_track_c_guest": True}}


def test_the_driver_settings_are_the_ones_the_renderer_writes():
    from ns3 import network
    text = network.render(network.star(CONFIG["c-02-single-flow"]["network"]), "")
    written = {line.split()[0] for line in text.splitlines() if line and not line.startswith("#")} - {"host", "switch", "link", "flow"}
    source = (run.TRACK / "driver" / "rfl-star.cc").read_text()
    block = source[source.index("kSettings = {"):source.index("};", source.index("kSettings = {"))]
    declared = {part.strip().strip('"') for part in block.split("{", 1)[1].replace("\n", " ").split(",") if part.strip()}
    assert written == declared


def test_star_breaks_change_only_the_simulated_network():
    from ns3 import network
    declared = network.star(CONFIG["c-02-single-flow"]["network"])
    rerun = CONFIG["c-02-single-flow"]["rerun_break"]
    assert run.simulated_star(declared, None, "run1", rerun) == declared
    assert [link.bps for link in run.simulated_star(declared, "rate-not-applied", "run1", rerun).links] == [200 * 10**9] * 2
    assert run.simulated_star(declared, "flow-cut-short", "run1", rerun).stop_ns == 10_000
    assert run.simulated_star(declared, "lossy-links", "run1", rerun).error_rate == 0.01
    ecn = run.simulated_star(declared, "ecn-bottleneck", "run1", rerun)
    assert [link.bps for link in ecn.links] == [100 * 10**9, 25 * 10**9] and ecn.ecn
    pfc = run.simulated_star(declared, "pfc-bottleneck", "run1", rerun)
    assert (pfc.buffer_bytes, pfc.ecn, pfc.links[1].bps) == (1_000_000, False, 25 * 10**9)
    assert [(f.payload_bytes, f.priority) for f in pfc.flows] == [(250_000, 3)] * 4
    assert run.simulated_star(declared, "rerun-input-differs", "run1", rerun) == declared
    assert run.simulated_star(declared, "rerun-input-differs", "run2", rerun).flows[0].start_ns == 1000
