import datetime as dt
from pathlib import Path

import pytest

from ibsim import observe
from ibsim.observe import ParseError, PortState, SmInfo
from ibsim.topology import Port

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "b-ibsim"


def fixture(rel: str) -> str:
    return (FIXTURES / rel).read_text()


def ibroute_pages(rel: str) -> list[str]:
    """The runner saves one ibroute page per switch in one file, each after a '$ ibroute <lid>' line."""
    return [page for page in fixture(rel).split("$ ibroute ")[1:]]


def utc(text: str) -> float:
    return dt.datetime.strptime(text, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=dt.timezone.utc).timestamp()


def test_iblinkinfo_lists_every_port_with_state_and_peer():
    states = observe.link_states(fixture("b-03/baseline/iblinkinfo.txt"))
    assert len(states) == 20
    assert states[Port("hca3", 1)] == PortState("Active", "LinkUp", Port("sw2", 1))
    assert states[Port("sw1", 7)] == PortState("Active", "LinkUp", Port("sw2", 7))
    assert states[Port("sw1", 3)] == PortState("Down", "Polling", None)


def test_iblinkinfo_after_the_fault_shows_the_switch_port_down_and_no_hca3():
    states = observe.link_states(fixture("b-03/fault/iblinkinfo.txt"))
    assert states[Port("sw2", 1)] == PortState("Down", "Polling", None)
    assert Port("hca3", 1) not in states


def test_iblinkinfo_without_a_port_line_is_refused():
    with pytest.raises(ParseError):
        observe.link_states("ibwarn: [1] mad_rpc: _do_madrpc failed\n")


def test_ibswitches_gives_lid_and_lmc_per_switch():
    assert observe.switch_lids(fixture("b-03/baseline/ibswitches.txt")) == {"sw1": (2, 0), "sw2": (3, 0)}
    with pytest.raises(ParseError):
        observe.switch_lids("")


def test_ibhosts_names_every_channel_adapter():
    assert sorted(observe.host_names(fixture("b-01/baseline/ibhosts.txt"))) == ["hca1", "hca2"]


def test_ibroute_gives_the_switch_and_its_unicast_table():
    first, second = ibroute_pages("b-03/baseline/ibroute.txt")
    assert observe.routes(first) == ("sw1", {1: 1, 2: 0, 3: 7, 4: 2, 5: 7, 6: 8})
    assert observe.routes(second) == ("sw2", {1: 7, 2: 7, 3: 0, 4: 8, 5: 1, 6: 2})


def test_ibroute_after_the_fault_has_no_route_to_the_removed_lid():
    name, table = observe.routes(ibroute_pages("b-03/fault/ibroute.txt")[0])
    assert name == "sw1" and 5 not in table and len(table) == 5


def test_ibroute_without_its_header_is_refused():
    with pytest.raises(ParseError):
        observe.routes("ibwarn: [1] mad_rpc: _do_madrpc failed; dport (Lid 9)\n")


def test_sminfo_gives_the_master_lid():
    assert observe.sminfo(fixture("b-01/baseline/sminfo.txt")) == SmInfo(1, 0, "SMINFO_MASTER")
    with pytest.raises(ParseError):
        observe.sminfo("sminfo: iberror: failed: query\n")


def test_opensm_log_times_are_read_as_utc_in_the_given_year():
    log = fixture("b-03/container/opensm.log")
    assert observe.first_log_time(log, 2026) == pytest.approx(utc("2026-11-15 14:21:45.231902"))
    assert observe.subnet_up_times(log, 2026) == pytest.approx(
        [utc("2026-11-15 14:21:45.235573"), utc("2026-11-15 14:21:51.375897"), utc("2026-11-15 14:21:56.928377")])
    assert observe.log_time("no stamp here", 2026) is None


@pytest.mark.parametrize(
    ("rel", "engines"),
    [
        ("b-03/container/opensm.log", ["minhop"] * 3),
        ("b-04/container/opensm.log", ["updn"] * 3),
        ("b-02-updn-fallback/container/opensm.log", ["minhop"]),
    ],
)
def test_the_log_names_the_engine_that_configured_each_sweep(rel, engines):
    assert observe.configured_engine(fixture(rel)) == engines


def test_the_fallback_log_shows_updn_gave_up():
    assert "disabling UPDN algorithm, no root nodes were found" in fixture("b-02-updn-fallback/container/opensm.log")


def test_nodedesc_probe_maps_each_lid_to_its_answer_or_none():
    assert observe.nodedesc_answers(fixture("b-03/baseline/smpquery-nodedesc.txt")) == {
        1: "hca1", 2: "sw1", 3: "sw2", 4: "hca2", 5: "hca3", 6: "hca4"}
    assert observe.nodedesc_answers(fixture("b-03/fault/smpquery-nodedesc.txt")) == {
        1: "hca1", 2: "sw1", 3: "sw2", 4: "hca2", 5: None, 6: "hca4"}


def test_cel1_node_descriptions_with_spaces_survive_the_probe_parser():
    answers = observe.nodedesc_answers(fixture("b-04/baseline/smpquery-nodedesc.txt"))
    assert len(answers) == 76 and None not in answers.values()
    assert "cel1-dgx-01 mlx5_0" in answers.values() and "cel1-ib-spine-04" in answers.values()
