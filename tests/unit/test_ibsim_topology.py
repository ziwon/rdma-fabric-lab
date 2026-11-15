from pathlib import Path

import pytest
import yaml

from fleet import site
from ibsim import __main__ as cli
from ibsim.topology import HCA, SWITCH, Link, Port, TopologyError, counts, from_fabric, parse_ibnetdiscover, parse_netfile, render_netfile

REPO = Path(__file__).resolve().parents[2]
TOPOLOGIES = REPO / "tracks" / "b-ibsim" / "topologies"
FIXTURES = REPO / "tests" / "fixtures" / "b-ibsim"
CEL1 = REPO / "sites" / "cel1.yaml"
CEL1_NET = TOPOLOGIES / "cel1-compute.net"


def netfile(name: str):
    return parse_netfile((TOPOLOGIES / name).read_text())


def fixture(rel: str) -> str:
    return (FIXTURES / rel).read_text()


def link(a: tuple[str, int], b: tuple[str, int]) -> frozenset:
    return Link({Port(*a), Port(*b)})


def test_b01_is_two_hcas_on_one_switch():
    topo = netfile("b-01.net")
    assert [n.name for n in topo.of_kind(SWITCH)] == ["sw1"]
    assert sorted(n.name for n in topo.of_kind(HCA)) == ["hca1", "hca2"]
    assert topo.links == {link(("sw1", 1), ("hca1", 1)), link(("sw1", 2), ("hca2", 1))}


def test_b02_is_four_hcas_on_two_switches_joined_by_two_links():
    topo = netfile("b-02.net")
    assert counts(topo) == {"switches": 2, "hcas": 4, "switch_ports": 16, "hca_ports": 4, "links": 6}
    assert topo.peer(Port("sw1", 7)) == Port("sw2", 7) and topo.peer(Port("sw1", 8)) == Port("sw2", 8)
    assert topo.peer(Port("sw1", 3)) is None


@pytest.mark.parametrize("name", ["b-01.net", "b-02.net", "cel1-compute.net"])
def test_rendering_a_topology_and_parsing_it_again_gives_the_same_topology(name):
    topo = netfile(name)
    again = parse_netfile(render_netfile(topo, "header line"))
    assert set(again.nodes) == set(topo.nodes) and again.links == topo.links


def test_cel1_topology_has_the_counts_of_the_site_model_read_from_the_raw_yaml():
    raw = yaml.safe_load(CEL1.read_text())
    fabric = raw["fabrics"]["compute"]
    switch_ports = {name: fabric["switch_models"][s["model"]]["ports"] for name, s in fabric["switches"].items()}
    host_ends = {end for pair in fabric["links"] for end in pair if end.split("/")[0] not in switch_ports}
    want = {"switches": len(fabric["switches"]), "hcas": len(host_ends), "switch_ports": sum(switch_ports.values()),
            "hca_ports": len(host_ends), "links": len(fabric["links"])}
    assert want == {"switches": 12, "hcas": 64, "switch_ports": 768, "hca_ports": 64, "links": 128}
    assert counts(from_fabric(site.load(CEL1), "compute")) == want
    assert counts(parse_netfile(CEL1_NET.read_text())) == want


def test_every_cel1_cable_becomes_a_link_between_the_named_switch_port_and_the_hosts_hca():
    raw = yaml.safe_load(CEL1.read_text())
    fabric = raw["fabrics"]["compute"]
    hca_of = {}
    for host, spec in raw["hosts"].items():
        for interface, attrs in raw["classes"][spec["class"]]["interfaces"].items():
            if isinstance(attrs, dict) and attrs.get("hca"):
                hca_of[f"{host}/{interface}"] = f"{host} {attrs['hca']}"

    def end(text: str) -> Port:
        if text in hca_of:
            return Port(hca_of[text], 1)
        node, port = text.split("/")
        return Port(node, int(port))

    want = {Link({end(a), end(b)}) for a, b in fabric["links"]}
    assert parse_netfile(CEL1_NET.read_text()).links == want


def test_render_reproduces_the_committed_cel1_netfile():
    assert cli.rendered(Path("sites/cel1.yaml"), "compute") == CEL1_NET.read_text()


def test_render_check_passes_on_the_committed_file_and_fails_on_a_changed_one(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(REPO)
    base = ["render", "--site", "sites/cel1.yaml", "--fabric", "compute"]
    assert cli.main([*base, "--out", str(CEL1_NET), "--check"]) == 0
    changed = tmp_path / "cel1.net"
    changed.write_text(CEL1_NET.read_text().replace('"cel1-ib-spine-02"[7]', '"cel1-ib-spine-02"[9]', 1))
    assert cli.main([*base, "--out", str(changed), "--check"]) == 1
    assert "differs" in capsys.readouterr().err
    assert cli.main([*base, "--out", str(changed)]) == 0
    assert cli.main([*base, "--out", str(changed), "--check"]) == 0


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('Switch\t2 "s"\n[1]\t"h"[1]\n\nHca\t1 "h"\n', "does not list"),
        ('Switch\t2 "s"\n[1]\t"x"[1]\n', "unknown node"),
        ('Switch\t2 "s"\n[3]\t"h"[1]\n\nHca\t1 "h"\n[1]\t"s"[3]\n', "has ports 1 to 2"),
        ('Switch\t2 "s"\n[1]\t"h"[1]\n\nSwitch\t2 "s"\n\nHca\t1 "h"\n[1]\t"s"[1]\n', "appears twice"),
        ('Switch\t2 "s"\nnot a port line\n', "neither a node header nor a port line"),
    ],
)
def test_broken_netfiles_are_refused_with_the_reason(text, message):
    with pytest.raises(TopologyError, match=message):
        parse_netfile(text)


def test_a_failed_endpoint_link_cuts_off_only_that_hca():
    degraded = netfile("b-02.net").without_link_at(Port("hca3", 1))
    reached = degraded.reachable_from("hca1")
    assert sorted(n.name for n in reached.nodes) == ["hca1", "hca2", "hca4", "sw1", "sw2"]
    assert len(reached.links) == 5


def test_a_failed_parallel_link_keeps_every_node():
    degraded = netfile("b-02.net").without_link_at(Port("sw1", 8))
    assert len(degraded.reachable_from("hca1").nodes) == 6 and len(degraded.links) == 5


def test_an_unlinked_port_has_no_link_to_remove():
    with pytest.raises(TopologyError, match="no link to remove"):
        netfile("b-02.net").without_link_at(Port("sw1", 3))


def test_hops_cross_links_between_nodes():
    hops = netfile("b-02.net").hops_from("hca1")
    assert hops == {"hca1": 0, "sw1": 1, "hca2": 2, "sw2": 2, "hca3": 3, "hca4": 3}


def test_ibnetdiscover_of_b03_names_nodes_by_description_and_reads_every_lid():
    found = parse_ibnetdiscover(fixture("b-03/baseline/ibnetdiscover.txt"))
    expected = netfile("b-02.net")
    assert set(found.topology.nodes) == set(expected.nodes) and found.topology.links == expected.links
    assert found.lids == {Port("hca1", 1): (1, 0), Port("hca2", 1): (4, 0), Port("hca3", 1): (5, 0),
                          Port("hca4", 1): (6, 0), Port("sw1", 0): (2, 0), Port("sw2", 0): (3, 0)}


def test_ibnetdiscover_after_the_b03_fault_lacks_hca3():
    found = parse_ibnetdiscover(fixture("b-03/fault/ibnetdiscover.txt"))
    assert "hca3" not in {n.name for n in found.topology.nodes}
    assert Port("hca3", 1) not in found.lids


def test_ibnetdiscover_of_cel1_equals_the_rendered_netfile_and_loses_the_failed_link():
    expected = parse_netfile(CEL1_NET.read_text())
    base = parse_ibnetdiscover(fixture("b-04/baseline/ibnetdiscover.txt")).topology
    fault = parse_ibnetdiscover(fixture("b-04/fault/ibnetdiscover.txt")).topology
    assert set(base.nodes) == set(expected.nodes) and base.links == expected.links
    assert fault.links == expected.without_link_at(Port("cel1-ib-leaf-r3", 35)).links


def test_two_nodes_with_one_description_are_refused():
    text = fixture("b-03/baseline/ibnetdiscover.txt").replace('# "hca4"', '# "hca3"')
    with pytest.raises(TopologyError, match="same NodeDescription"):
        parse_ibnetdiscover(text)
