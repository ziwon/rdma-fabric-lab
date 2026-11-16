"""Track C checks on rfl-star output captured from real runs (tests/fixtures/c-ns3/README.md).

The declared network always comes from tracks/c-ns3/config/scenarios.yaml, as in the runner, so a
fixture from a deliberate break shows the check failing for the reason that break introduces.
"""

import json
import shutil
from pathlib import Path

import pytest
import yaml

from ns3 import network, results, verdict

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures" / "c-ns3"
DECLARED = network.star(yaml.safe_load((REPO / "tracks/c-ns3/config/scenarios.yaml").read_text())
                        ["scenarios"]["c-02-single-flow"]["network"])


def out(name: str) -> results.StarOut:
    return results.parse_star(FIXTURES / name)


def test_every_check_passes_on_the_baseline_run():
    base = out("baseline")
    for check in (verdict.config_applied, verdict.flow_completes, verdict.no_pfc_pause, verdict.no_ecn_mark):
        assert check(DECLARED, base).ok, check.__name__
    assert verdict.zero_drops(DECLARED, base, 0).ok


def test_the_baseline_fct_is_the_model_mechanics_to_the_nanosecond():
    result, figures = verdict.fct_lower_bound(DECLARED, out("baseline"))
    assert result.ok
    assert "FCT 88092 ns is 6092.0 ns (7.43%) above the bound 82000.0 ns" in result.detail
    assert "Unexplained: 0 ns" in result.detail
    assert ("fct_ns", 88092, "flow 0") in figures and ("expected_ns", 88092, "flow 0") in figures


def test_a_rate_the_driver_did_not_apply_fails_the_mapping_and_the_bound():
    broken = out("rate-not-applied")
    applied = verdict.config_applied(DECLARED, broken)
    assert not applied.ok and "link rate at the host end, bit/s is 200000000000, want 100000000000" in applied.detail
    bound, _ = verdict.fct_lower_bound(DECLARED, broken)
    assert not bound.ok and "FCT 46046 ns is below the bound 82000.0 ns" in bound.detail


def test_a_flow_stopped_early_does_not_complete():
    result = verdict.flow_completes(DECLARED, out("flow-cut-short"))
    assert not result.ok and "did not complete; the simulator stopped at 10000 ns" in result.detail
    bound, figures = verdict.fct_lower_bound(DECLARED, out("flow-cut-short"))
    assert not bound.ok and ("fct_ns", None, "flow 0") in figures


def test_lossy_links_show_drops_nacks_and_resent_segments():
    result = verdict.zero_drops(DECLARED, out("lossy-links"), 0)
    assert not result.ok
    for part in ("data packets lost on links: 36", "PhyRxDrop events: 50", "NACKs sent: 40",
                 "data packets sent beyond one per segment: 977"):
        assert part in result.detail


def test_mmu_drops_are_counted_from_the_simulator_stdout():
    stdout = (FIXTURES / "mmu-drops" / "simulate-head.stdout").read_text()
    assert results.mmu_drop_lines(stdout) == 3
    assert "MMU drop lines on stdout: 3" in verdict.zero_drops(DECLARED, out("mmu-drops"), 3).detail
    assert results.mmu_drop_lines("") == 0


def test_a_bottleneck_shows_ecn_marks_and_cnp_feedback():
    result = verdict.no_ecn_mark(DECLARED, out("ecn-bottleneck"))
    assert not result.ok
    for part in ("CE-marked data packets the switch sent: 13", "CE-marked data packets hosts received: 13",
                 "ACKs with the CNP flag hosts received: 13", "CNPs RdmaHw counted per QP: 13"):
        assert part in result.detail
    assert verdict.no_pfc_pause(DECLARED, out("ecn-bottleneck")).ok


def test_a_small_buffer_behind_a_bottleneck_shows_pause_and_resume():
    result = verdict.no_pfc_pause(DECLARED, out("pfc-bottleneck"))
    assert not result.ok and "PAUSE received (QbbPfc 1): 25" in result.detail and "RESUME received (QbbPfc 0): 25" in result.detail
    assert "EcnEnabled reads false" in verdict.no_ecn_mark(DECLARED, out("pfc-bottleneck")).detail


def test_with_pfc_off_the_switch_still_sends_pause_but_the_host_ignores_it():
    mmu = out("mmu-drops")
    assert mmu.total("tx", "pfc") == 1 and mmu.event("pause_rx") == 0
    assert verdict.no_pfc_pause(DECLARED, mmu).detail == "PFC frames sent: 1"
    assert {v for (where, a), v in mmu.resolved.items() if a == "QbbEnabled"} == {"false"}


def copy_work(tmp_path: Path, name: str) -> Path:
    target = tmp_path / name
    shutil.copytree(FIXTURES / name, target)
    return target


def test_reruns_compare_modeled_figures_and_ignore_simulator_cost(tmp_path):
    first, second = copy_work(tmp_path, "baseline"), tmp_path / "second"
    shutil.copytree(first, second)
    shutil.copy(FIXTURES / "baseline" / "run2-run.tsv", second / "run.tsv")
    assert (first / "run.tsv").read_text() != (second / "run.tsv").read_text()
    assert verdict.same_contents(results.modeled_figures(first), results.modeled_figures(second), "files").ok
    flows = (second / "flows.tsv").read_text().replace("\t88092\t", "\t88093\t")
    (second / "flows.tsv").write_text(flows)
    differs = verdict.same_contents(results.modeled_figures(first), results.modeled_figures(second), "files")
    assert not differs.ok and differs.detail.startswith("flows.tsv differs at line 2")


def test_a_flows_file_with_other_columns_is_refused(tmp_path):
    work = copy_work(tmp_path, "baseline")
    (work / "flows.tsv").write_text((work / "flows.tsv").read_text().replace("finish_ns", "fct_ns"))
    with pytest.raises(results.ResultError, match="header"):
        results.parse_star(work)


def test_a_flow_that_finishes_before_it_starts_is_refused(tmp_path):
    work = copy_work(tmp_path, "baseline")
    (work / "flows.tsv").write_text((work / "flows.tsv").read_text().replace("\t0\t1\t88092\t", "\t90000\t1\t88092\t"))
    with pytest.raises(results.ResultError, match="before its start"):
        results.parse_star(work)


def inspect(name: str) -> dict:
    return json.loads((FIXTURES / "container" / name).read_text())[0]


IDENTITY = {"uid": "10001", "gid": "10001"}


def test_the_locked_container_has_no_problem():
    found = inspect("inspect-locked.json")
    assert verdict.container_problems(found, found["Image"], IDENTITY) == []


def test_a_writable_root_another_image_or_a_root_user_is_a_problem():
    unlocked = inspect("inspect-unlocked.json")
    assert verdict.container_problems(unlocked, unlocked["Image"], IDENTITY) == [
        "HostConfig.ReadonlyRootfs is False, want True (read-only root file system)"]
    locked = inspect("inspect-locked.json")
    assert any("not the locked" in p for p in verdict.container_problems(locked, "sha256:" + "0" * 64, IDENTITY))
    assert any("uid 0" in p for p in verdict.container_problems(locked, locked["Image"], {"uid": "0", "gid": "0"}))


def test_the_identity_file_names_the_container_user_and_the_sources():
    text = (FIXTURES / "container" / "identity.txt").read_text()
    assert text.startswith("uid=10001\ngid=10001\n")
    assert "a57d765c5478f319c5f5ab6fdc53f9cc7f461743" in text and "3e0c7c1bfbbe9f77890ddcf5e5b9c79fc6dd7437" in text


def test_host_scan_matches_the_truncated_command_name_only():
    ps = ("  101 SimAI_simulator /tmp/x/SimAI_simulator 600\n"
          "  102 ns3.36.1-AstraS /opt/simai/ns3.36.1-AstraSimNetwork-debug -t 8\n"
          "  103 zsh zsh -c grep SimAI_simulator\n")
    assert [line.split()[0] for line in verdict.host_simulator_processes(ps)] == ["101", "102"]
