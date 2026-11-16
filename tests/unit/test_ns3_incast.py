"""Incast cases, their figures and checks, on rfl-star output captured from real runs (tests/fixtures/c-ns3/README.md)."""

from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from ns3 import compare, incast, network, results, verdict

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests/fixtures/c-ns3/incast"
CONFIG = yaml.safe_load((REPO / "tracks/c-ns3/config/scenarios.yaml").read_text())
SHARED, SCENARIOS = CONFIG["incast"], CONFIG["scenarios"]
PROFILES = ("c-none", "c-pfc", "c-ecn", "c-both")


def declared(scenario: str) -> dict[str, incast.Case]:
    shared = incast.merged(SHARED, SCENARIOS[scenario].get("overrides", {}))
    return {c.step: c for c in incast.cases(shared, SCENARIOS[scenario]["workload"])}


def load(scenario: str, step: str) -> compare.CaseOut:
    work = FIXTURES / scenario / step
    return compare.CaseOut(declared(scenario)[step], results.parse_star(work), results.parse_series(work, work / "stdout-summary"))


def group(fan_in: int) -> list[compare.CaseOut]:
    return [load("c-03-incast-simultaneous", f"n{fan_in}-{p}-s1") for p in PROFILES]


def test_every_scenario_expands_to_fan_in_by_profile_by_seed():
    cases = list(declared("c-03-incast-simultaneous").values())
    assert len(cases) == 3 * 4 * 5 and cases[0].step == "n2-c-none-s1" and cases[-1].step == "n8-c-both-s5"
    assert len(declared("c-06-sensitivity-kmin")) == 4 * 5
    assert {c.net.kmin_kb for c in declared("c-06-sensitivity-kmin").values()} == {100}


def test_the_profiles_set_only_pfc_ecn_and_cc():
    table = {c.profile: (c.net.pfc, c.net.ecn, c.net.cc_mode) for c in declared("c-03-incast-simultaneous").values()}
    assert table == {"c-none": (False, False, 0), "c-pfc": (True, False, 0), "c-ecn": (False, True, 1), "c-both": (True, True, 1)}


def test_cases_of_one_fan_in_render_the_same_traffic():
    cases = [c for c in declared("c-03-incast-simultaneous").values() if c.fan_in == 8]
    texts = [network.render(c.net, f"case {c.step}") for c in cases]
    assert all(incast.traffic_differences(texts[0], t) == [] for t in texts)
    changed = network.render(replace(cases[0].net, buffer_bytes=1), "x")
    assert incast.traffic_differences(texts[0], changed) == ["buffer_bytes"]


def test_the_workloads_place_flows_as_written_down():
    staggered = declared("c-04-incast-staggered")["n4-c-none-s1"].net.flows
    assert [f.start_ns for f in staggered] == [0, 20_000, 40_000, 60_000]
    mixed = declared("c-05-incast-mixed")["n8-c-none-s1"]
    assert mixed.classes.count("large") == 4 and mixed.classes.count("small") == 16
    assert sorted({f.start_ns for f, cls in zip(mixed.net.flows, mixed.classes) if cls == "small"}) == [10_000, 30_000, 50_000, 70_000]
    assert {f.priority for f in mixed.net.flows} == {3}


def test_every_check_passes_on_a_real_fan_in_group():
    cases = group(8) + group(2)
    for check in (verdict.incast_config_applied, verdict.flows_accounted, verdict.fct_bounds, verdict.pfc_as_configured,
                  verdict.ecn_as_configured, verdict.profiles_differ):
        assert check(cases).ok, (check.__name__, check(cases).detail)
    files = {c.case.step: (FIXTURES / "c-03-incast-simultaneous" / c.case.step / "network.txt").read_text() for c in cases}
    assert verdict.same_traffic([c.case for c in cases], files).ok


def test_the_profiles_leave_their_own_marks_in_the_counters():
    none, pfc, ecn, both = (compare.figures(c) for c in group(8))
    assert (none["mmu_drops_trace"], none["mmu_drops_stdout"], none["pauses_handled"], none["marks"]) == (9962, 9962, 0, 0)
    assert none["pauses_received"] == 31 and none["pause_ns"] == 0
    assert (pfc["mmu_drops_trace"], pfc["pauses_handled"], pfc["pauses_received"], pfc["marks"]) == (0, 190, 190, 0)
    assert ecn["marks"] == 2679 and ecn["cnps"] == 2655 and ecn["rate_reductions"] == 414 and ecn["min_rate_bps"] == 100_000_000
    assert both["pauses_handled"] == 48 and both["mmu_drops_trace"] == 0 and both["marks"] == 2294


def test_marks_follow_kmin_kmax_and_pmax():
    ecn = load("c-03-incast-simultaneous", "n8-c-ecn-s1")
    bands = {band: v for (_, _, _, band), v in ecn.series.bands.items()}
    assert bands["at_most_kmin"][1] == 0 and bands["above_kmax"][0] == bands["above_kmax"][1] == 2389
    assert 0 < compare.figures(ecn)["mid_band_mark_fraction"] <= 0.2
    assert all(egress > 400_000 for _, _, _, _, egress in ecn.series.marks)


def test_without_a_pause_the_two_ecn_profiles_take_the_same_path():
    ecn, both = load("c-03-incast-simultaneous", "n2-c-ecn-s1"), load("c-03-incast-simultaneous", "n2-c-both-s1")
    assert verdict.outputs(ecn) == verdict.outputs(both) and both.out.total("tx", "pfc") == 0
    assert "pairs saw no trigger" in verdict.profiles_differ(group(2)).detail


def test_pause_intervals_close_on_resume_and_count_only_where_hosts_act():
    pfc = load("c-03-incast-simultaneous", "n8-c-pfc-s1")
    intervals = compare.pause_intervals(pfc)
    assert len(intervals) == 190 and all(end is not None and end > start for *_, start, end in intervals)
    assert compare.figures(pfc)["pause_ns"] == sum(end - start for *_, start, end in intervals) == 3_449_474


def test_a_stalled_flow_is_counted_unfinished_with_where_it_stopped():
    stalled = load("c-04-incast-staggered", "n8-c-none-s1")
    f = compare.figures(stalled)
    assert (f["completed"], f["flows"]) == (7, 8)
    flow = next(o for o in stalled.out.flows if not o.completed)
    assert flow.rx_in_order_bytes == stalled.series.packets[flow.flow][3] < flow.payload_bytes
    assert stalled.series.packets[flow.flow][2] == 0
    assert verdict.flows_accounted([stalled]).ok and "1 did not finish" in verdict.flows_accounted([stalled]).detail


def test_small_flows_are_reported_apart_from_large_ones():
    mixed = compare.figures(load("c-05-incast-mixed", "n8-c-ecn-s1"))
    assert len(mixed["small_fct"]) == 16 and len(mixed["large_fct"]) == 4


def test_the_summary_gives_sample_counts_and_distinct_outcomes():
    rows = compare.summary(group(8))
    assert [r["profile"] for r in rows] == list(PROFILES)
    assert all("(n=8)" in r["large_fct_ns"] and r["completion_ratio_min"] == "1.000" for r in rows)
    assert all(r["distinct_outcomes"] == "1" and r["seeds"] == "1" for r in rows)
    assert compare.summary_tsv(rows).splitlines()[0].split("\t") == list(compare.SUMMARY_COLUMNS)


@pytest.mark.parametrize("break_it, check, fragment", [
    (lambda c: replace(c, case=replace(c.case, net=replace(c.case.net, pfc=not c.case.net.pfc))), verdict.pfc_as_configured,
     "handler"),
    (lambda c: replace(c, case=replace(c.case, net=replace(c.case.net, ecn=not c.case.net.ecn))), verdict.ecn_as_configured,
     "ECN is off but"),
    (lambda c: replace(c, case=replace(c.case, net=network.with_link_rate(c.case.net, "r", 0.1))), verdict.fct_bounds,
     "the receiver link needs"),
    (lambda c: replace(c, out=replace(c.out, flows=c.out.flows[:-1])), verdict.flows_accounted, "flows.tsv lists 7 flows"),
])
def test_each_incast_check_fails_on_its_broken_input(break_it, check, fragment):
    result = check([break_it(c) for c in group(8)])
    assert not result.ok and fragment in result.detail


def test_identical_profiles_fail_the_path_check():
    none = load("c-03-incast-simultaneous", "n8-c-none-s1")
    clones = [replace(none, case=replace(none.case, profile=p)) for p in PROFILES]
    result = verdict.profiles_differ(clones)
    assert not result.ok and "gave the same flows and counters" in result.detail


def test_a_profile_that_changes_traffic_fails_same_traffic():
    cases = group(8)
    files = {c.case.step: (FIXTURES / "c-03-incast-simultaneous" / c.case.step / "network.txt").read_text() for c in cases}
    files["n8-c-ecn-s1"] = files["n8-c-ecn-s1"].replace("1000000 0 3", "1001000 0 3")
    result = verdict.same_traffic([c.case for c in cases], files)
    assert not result.ok and "flow:" in result.detail
