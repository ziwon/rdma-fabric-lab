from pathlib import Path

import pytest

from ibsim import observe, verdict
from ibsim.run import lid_names
from ibsim.topology import Port, parse_ibnetdiscover, parse_netfile

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures" / "b-ibsim"
B02 = parse_netfile((REPO / "tracks/b-ibsim/topologies/b-02.net").read_text())
CEL1 = parse_netfile((REPO / "tracks/b-ibsim/topologies/cel1-compute.net").read_text())
HCA3 = Port("hca3", 1)
LEAF_R3_35 = Port("cel1-ib-leaf-r3", 35)


def fixture(rel: str) -> str:
    return (FIXTURES / rel).read_text()


def discovered(rel: str, edit=lambda text: text):
    return parse_ibnetdiscover(edit(fixture(rel)))


def tables(rel: str) -> dict[str, dict[int, int]]:
    return dict(observe.routes(page) for page in fixture(rel).split("$ ibroute ")[1:])


def states(rel: str, edit=lambda text: text):
    return observe.link_states(edit(fixture(rel)))


def b03_degraded():
    return B02.without_link_at(HCA3).reachable_from("hca1")


def cel1_degraded():
    return CEL1.without_link_at(LEAF_R3_35).reachable_from("cel1-dgx-01 mlx5_0")


def test_discovery_equal_to_the_topology_passes():
    result = verdict.topology_matches(B02, discovered("b-03/baseline/ibnetdiscover.txt").topology)
    assert result.ok, result.detail
    assert verdict.topology_matches(CEL1, discovered("b-04/baseline/ibnetdiscover.txt").topology).ok


def test_discovery_missing_a_node_and_its_link_fails():
    result = verdict.topology_matches(B02, discovered("b-03/fault/ibnetdiscover.txt").topology)
    assert not result.ok
    assert "missing node Hca hca3" in result.detail and 'missing link "hca3"[1]-"sw2"[1]' in result.detail


def test_discovery_with_a_node_the_topology_lacks_fails():
    result = verdict.topology_matches(b03_degraded(), discovered("b-03/baseline/ibnetdiscover.txt").topology)
    assert not result.ok and "extra node Hca hca3" in result.detail and "extra link" in result.detail


def test_discovery_after_a_spine_link_failure_matches_the_degraded_cel1_fabric_only():
    found = discovered("b-04/fault/ibnetdiscover.txt").topology
    assert verdict.topology_matches(cel1_degraded(), found).ok
    assert not verdict.topology_matches(CEL1, found).ok


def test_unique_nonzero_lids_with_lmc_0_pass():
    result = verdict.lids_unique(B02, discovered("b-03/baseline/ibnetdiscover.txt"))
    assert result.ok and result.detail.startswith("4 endpoint LIDs")


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("# lid 4 lmc 0", "# lid 1 lmc 0", "LID 1 is held by"),
        ("# lid 4 lmc 0", "# lid 0 lmc 0", '"hca2"[1] has LID 0 LMC 0'),
        ("# lid 4 lmc 0", "# lid 4 lmc 1", '"hca2"[1] has LID 4 LMC 1'),
    ],
)
def test_a_shared_zero_or_lmc_lid_fails(old, new, message):
    result = verdict.lids_unique(B02, discovered("b-03/baseline/ibnetdiscover.txt", lambda t: t.replace(old, new)))
    assert not result.ok and message in result.detail


def test_an_endpoint_without_a_lid_fails():
    result = verdict.lids_unique(B02, discovered("b-03/fault/ibnetdiscover.txt"))
    assert not result.ok and '"hca3"[1] has no LID' in result.detail


def test_port_states_of_the_fabric_pass():
    result = verdict.port_states(B02, states("b-03/baseline/iblinkinfo.txt"))
    assert result.ok and result.detail == "20 ports as expected, 12 of them Active/LinkUp"
    assert verdict.port_states(b03_degraded(), states("b-03/fault/iblinkinfo.txt")).ok


def test_a_port_down_where_the_topology_has_a_link_fails():
    result = verdict.port_states(B02, states("b-03/fault/iblinkinfo.txt"))
    assert not result.ok
    assert '"sw2"[1] is Down/Polling to None, want Active/LinkUp to "hca3"[1]' in result.detail
    assert '"hca3"[1] is not listed' in result.detail


def test_a_port_active_where_the_topology_has_no_link_fails():
    result = verdict.port_states(b03_degraded(), states("b-03/baseline/iblinkinfo.txt"))
    assert not result.ok and '"sw2"[1] has no link but is Active/LinkUp' in result.detail


def test_a_port_facing_the_wrong_peer_fails():
    def swap(text: str) -> str:
        lines = text.splitlines()
        lines = [line.replace('"hca1" (Could', '"hca2" (Could') if '"                           sw1"      2    1[' in line
                 else line for line in lines]
        return "\n".join(lines)

    result = verdict.port_states(B02, states("b-03/baseline/iblinkinfo.txt", swap))
    assert not result.ok and '"sw1"[1] is Active/LinkUp to "hca2"[1], want Active/LinkUp to "hca1"[1]' in result.detail


def test_shortest_routes_pass_on_both_fabrics_before_and_after_the_fault():
    assert verdict.routes_shortest(B02, tables("b-03/baseline/ibroute.txt"), discovered("b-03/baseline/ibnetdiscover.txt")).ok
    assert verdict.routes_shortest(b03_degraded(), tables("b-03/fault/ibroute.txt"), discovered("b-03/fault/ibnetdiscover.txt")).ok
    result = verdict.routes_shortest(CEL1, tables("b-04/baseline/ibroute.txt"), discovered("b-04/baseline/ibnetdiscover.txt"))
    assert result.ok and result.detail.startswith("768 switch routes")
    assert verdict.routes_shortest(cel1_degraded(), tables("b-04/fault/ibroute.txt"),
                                   discovered("b-04/fault/ibnetdiscover.txt")).ok


def test_a_route_away_from_its_endpoint_fails():
    bad = tables("b-03/baseline/ibroute.txt")
    bad["sw1"][5] = 1
    result = verdict.routes_shortest(B02, bad, discovered("b-03/baseline/ibnetdiscover.txt"))
    assert not result.ok and 'sw1 sends LID 5 "hca3"[1] out of port 1, not toward it' in result.detail


def test_a_missing_route_a_stale_lid_and_a_missing_table_fail():
    bad = tables("b-03/baseline/ibroute.txt")
    del bad["sw1"][6]
    bad["sw2"][9] = 1
    found = discovered("b-03/baseline/ibnetdiscover.txt")
    result = verdict.routes_shortest(B02, bad, found)
    assert not result.ok
    assert 'sw1 has no route to LID 6 "hca4"[1]' in result.detail and "sw2 routes LID 9, which no port holds" in result.detail
    del bad["sw2"]
    assert "no forwarding table of sw2" in verdict.routes_shortest(B02, bad, found).detail


def test_stale_routes_to_a_removed_lid_fail_after_the_fault():
    result = verdict.routes_shortest(b03_degraded(), tables("b-03/baseline/ibroute.txt"), discovered("b-03/fault/ibnetdiscover.txt"))
    assert not result.ok and "routes LID 5, which no port holds" in result.detail


def test_routes_over_the_failed_cel1_spine_link_moved_to_other_ports():
    failed = [LEAF_R3_35, CEL1.peer(LEAF_R3_35)]
    result = verdict.rerouted(tables("b-04/baseline/ibroute.txt"), tables("b-04/fault/ibroute.txt"), failed,
                              discovered("b-04/fault/ibnetdiscover.txt"))
    assert result.ok, result.detail
    assert result.detail.startswith("13 routes used the failed link: 13 moved to another port, 0 removed")


def test_routes_left_on_the_failed_link_fail():
    failed = [LEAF_R3_35, CEL1.peer(LEAF_R3_35)]
    before = tables("b-04/baseline/ibroute.txt")
    result = verdict.rerouted(before, before, failed, discovered("b-04/fault/ibnetdiscover.txt"))
    assert not result.ok and "still uses the failed port" in result.detail


def test_routes_to_a_cut_off_endpoint_leave_with_its_lid():
    result = verdict.rerouted(tables("b-03/baseline/ibroute.txt"), tables("b-03/fault/ibroute.txt"), [HCA3, B02.peer(HCA3)],
                              discovered("b-03/fault/ibnetdiscover.txt"))
    assert result.ok and "0 moved to another port, 1 removed with their LID" in result.detail


def test_a_fault_on_a_link_no_route_used_tests_nothing():
    unused = [Port("sw1", 3), Port("sw1", 4)]
    before = tables("b-03/baseline/ibroute.txt")
    result = verdict.rerouted(before, before, unused, discovered("b-03/baseline/ibnetdiscover.txt"))
    assert not result.ok and "tested nothing" in result.detail


def sm_verdict(engine: str, log_rel: str = "b-01/baseline/opensm.log", sminfo_edit=lambda text: text):
    found = parse_ibnetdiscover(fixture("b-01/baseline/ibnetdiscover.txt"))
    log = fixture(log_rel)
    return verdict.subnet_manager(observe.sminfo(sminfo_edit(fixture("b-01/baseline/sminfo.txt"))), Port("hca1", 1), found,
                                  observe.configured_engine(log), engine, log.count(observe.SUBNET_UP))


def test_master_sm_with_the_configured_engine_passes():
    result = sm_verdict("minhop")
    assert result.ok, result.detail


def test_an_engine_that_fell_back_fails():
    result = sm_verdict("updn", "b-02-updn-fallback/container/opensm.log")
    assert not result.ok and "names routing engines ['minhop'], want only updn" in result.detail


def test_an_sm_that_is_not_master_fails():
    result = sm_verdict("minhop", sminfo_edit=lambda t: t.replace("state 3 SMINFO_MASTER", "state 2 SMINFO_STANDBY"))
    assert not result.ok and "sminfo reports SMINFO_STANDBY" in result.detail


def test_no_subnet_up_fails():
    found = parse_ibnetdiscover(fixture("b-01/baseline/ibnetdiscover.txt"))
    result = verdict.subnet_manager(observe.sminfo(fixture("b-01/baseline/sminfo.txt")), Port("hca1", 1), found, ["minhop"],
                                    "minhop", 0)
    assert not result.ok and "no SUBNET UP" in result.detail


def test_every_lid_answering_with_its_name_passes():
    found = discovered("b-03/baseline/ibnetdiscover.txt")
    answers = observe.nodedesc_answers(fixture("b-03/baseline/smpquery-nodedesc.txt"))
    assert verdict.reachability(answers, lid_names(found), set()).ok


def test_a_cut_off_lid_must_stay_silent_and_the_others_must_answer():
    names = lid_names(discovered("b-03/baseline/ibnetdiscover.txt"))
    fault = observe.nodedesc_answers(fixture("b-03/fault/smpquery-nodedesc.txt"))
    assert verdict.reachability(fault, {lid: n for lid, n in names.items() if lid != 5}, {5}).ok
    result = verdict.reachability(fault, names, set())
    assert not result.ok and "LID 5 answered None, want 'hca3'" in result.detail
    baseline = observe.nodedesc_answers(fixture("b-03/baseline/smpquery-nodedesc.txt"))
    result = verdict.reachability(baseline, {lid: n for lid, n in names.items() if lid != 5}, {5})
    assert not result.ok and "LID 5 still answers as 'hca3'" in result.detail
