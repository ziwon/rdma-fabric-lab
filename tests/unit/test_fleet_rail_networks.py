"""The rail networks of a site model (ADR 0007): their checks, and the chart values they render."""

import copy
from pathlib import Path

import pytest
import yaml

from fleet import __main__ as fleet_cli
from fleet import render, site
from test_fleet_site import LAB_PLAN, MODEL

REPO = Path(__file__).resolve().parents[2]
SELECTOR = {"t1.example/node-class": "gpu"}


def rail_model() -> dict:
    """The small test model with a pod range on each of its two rails. Documentation addresses only."""
    raw = copy.deepcopy(MODEL)
    raw["kubernetes"]["rail_networks"] = {"namespace": "rdma-jobs", "node_selector": dict(SELECTOR), "per_node_block": 8}
    raw["networks"]["rail0"]["pod_cidr"] = "198.51.100.128/27"
    raw["networks"]["rail1"]["pod_cidr"] = "198.51.100.160/27"
    raw["classes"]["gpu"]["labels"].update(SELECTOR)
    return raw


def problems_after(change):
    raw = rail_model()
    change(raw)
    return site.problems(site.parse(raw), LAB_PLAN)


def test_the_small_rail_model_is_valid():
    assert site.problems(site.parse(rail_model()), LAB_PLAN) == []


def test_the_cel1_rails_carry_one_pod_range_each_on_the_dgx_class():
    cel1 = site.load(REPO / "sites/cel1.yaml")
    assert [n.name for n in cel1.pod_rails()] == [f"rail{r}" for r in range(8)]
    assert [str(n.pod_cidr) for n in cel1.pod_rails()] == [f"10.143.{r}.0/24" for r in range(8)]
    assert [c.name for c in cel1.rail_classes()] == ["dgx_b200"]
    assert cel1.rail_networks.namespace == "rdma-jobs"


def add_class(name, interfaces, labels):
    def change(raw):
        raw["classes"][name] = {"kube_role": "node", "os": "DGX OS 7", "interfaces": interfaces, "labels": labels}

    return change


def drop_selector_label(raw):
    raw["classes"]["gpu"]["labels"].pop("t1.example/node-class")


@pytest.mark.parametrize(
    "change, expected",
    [
        pytest.param(lambda raw: raw["networks"]["management"].update({"pod_cidr": "198.51.100.192/27"}),
                     "networks.management.pod_cidr: only an InfiniBand rail network carries pod addresses", id="pod-cidr-on-ethernet"),
        pytest.param(lambda raw: raw["networks"]["rail1"].pop("pod_cidr"),
                     "rail networks rail1 have no pod_cidr. A pod gets every rail or none", id="pod-cidr-on-some-rails"),
        pytest.param(lambda raw: raw["networks"]["rail1"].update({"pod_cidr": "198.51.100.64/27"}),
                     "networks.rail1 198.51.100.64/26 overlaps networks.rail1.pod_cidr 198.51.100.64/27", id="pod-cidr-overlaps-network"),
        pytest.param(lambda raw: raw["networks"]["rail1"].update({"pod_cidr": "198.51.100.128/27"}),
                     "networks.rail0.pod_cidr 198.51.100.128/27 overlaps networks.rail1.pod_cidr", id="pod-cidrs-overlap"),
        pytest.param(lambda raw: raw["kubernetes"]["rail_networks"].update({"per_node_block": 16}),
                     "networks.rail0.pod_cidr: 198.51.100.128/27 holds 30 pod addresses, and 2 nodes in blocks of 16 need 32",
                     id="pool-too-small"),
        pytest.param(lambda raw: raw["kubernetes"]["rail_networks"].update({"per_node_block": 0}),
                     "per_node_block: 0 is not a positive number", id="block-not-positive"),
        pytest.param(lambda raw: raw["kubernetes"]["rail_networks"].update({"namespace": "RDMA_jobs"}),
                     "kubernetes.rail_networks.namespace: 'RDMA_jobs' is not a namespace name", id="namespace-name"),
        pytest.param(lambda raw: raw["kubernetes"]["rail_networks"].update({"node_selector": {"t1.example/node-class": "cpu"}}),
                     "kubernetes.rail_networks.node_selector: selects no class", id="selector-selects-nothing"),
        pytest.param(lambda raw: raw["kubernetes"]["rail_networks"].update({"node_selector": {}}),
                     "node_selector: selects every node", id="selector-empty"),
        pytest.param(lambda raw: raw["classes"]["control_plane"].update({"labels": dict(SELECTOR)}),
                     "classes.control_plane: selected by kubernetes.rail_networks.node_selector, but has no interface on rail0, rail1",
                     id="selector-selects-a-class-without-rails"),
        pytest.param(add_class("gpu_b", {"mgmt0": "management", "rail0": "rail0", "rail1": "rail1"}, {}),
                     "classes.gpu_b: has an interface on a pod rail, but kubernetes.rail_networks.node_selector does not select it",
                     id="rail-class-not-selected"),
        pytest.param(add_class("gpu_b", {"mgmt0": "management", "rl0": "rail0", "rail1": "rail1"}, dict(SELECTOR)),
                     "networks.rail0: the selected classes name its interface rail0, rl0", id="master-names-differ"),
        pytest.param(lambda raw: raw["kubernetes"].pop("rail_networks"),
                     "networks rail0, rail1 have a pod_cidr, which needs kubernetes.rail_networks", id="pod-cidr-without-section"),
        pytest.param(lambda raw: [raw["networks"][r].pop("pod_cidr") for r in ("rail0", "rail1")],
                     "kubernetes.rail_networks: no network has a pod_cidr", id="section-without-pod-cidr"),
    ],
)
def test_each_rail_network_rule_names_what_breaks_it(change, expected):
    found = problems_after(change)
    assert any(expected in p for p in found), found


def test_a_pod_range_must_stay_off_the_lab_plan():
    found = problems_after(lambda raw: raw["networks"]["rail1"].update({"pod_cidr": "192.0.2.192/27"}))
    assert "networks.rail1.pod_cidr 192.0.2.192/27 overlaps the lab address plan entry management" in found


def test_an_unknown_key_of_the_rail_networks_is_refused():
    raw = rail_model()
    raw["kubernetes"]["rail_networks"]["pkey"] = "0x8001"
    with pytest.raises(site.SiteError, match="kubernetes.rail_networks: unknown pkey"):
        site.parse(raw)


def test_the_chart_values_name_one_network_and_pool_per_rail_with_its_host_interface():
    values = yaml.safe_load(render.rail_values(site.parse(rail_model())))
    assert values == {
        "namespace": "rdma-jobs",
        "resourceName": "t1_rails",
        "nodeSelector": SELECTOR,
        "perNodeBlockSize": 8,
        "rails": [
            {"name": "t1-rail0", "master": "rail0", "subnet": "198.51.100.128/27"},
            {"name": "t1-rail1", "master": "rail1", "subnet": "198.51.100.160/27"},
        ],
    }


def test_a_site_without_rail_networks_renders_no_chart_values():
    assert render.rail_values(site.parse(copy.deepcopy(MODEL))) is None


def test_the_committed_cel1_values_are_what_the_model_renders():
    rendered = render.rail_values(site.load(REPO / "sites/cel1.yaml"))
    assert rendered == (REPO / "helm/rail-networks/values-cel1.yaml").read_text()
    assert rendered.startswith(render.MARKER)


def write_model(tmp_path, raw) -> Path:
    path = tmp_path / "site.yaml"
    path.write_text(yaml.safe_dump(raw))
    return path


def test_render_refuses_a_rail_site_without_a_values_path(tmp_path, capsys):
    model = write_model(tmp_path, rail_model())
    assert fleet_cli.main(["render", str(model), "--out", str(tmp_path / "inv")]) == 1
    assert "needs --rail-values" in capsys.readouterr().err
    assert not (tmp_path / "inv").exists()


def test_render_check_finds_drifted_chart_values(tmp_path, capsys):
    model, values = write_model(tmp_path, rail_model()), tmp_path / "values.yaml"
    argv = ["render", str(model), "--out", str(tmp_path / "inv"), "--rail-values", str(values)]
    assert fleet_cli.main(argv) == 0
    assert fleet_cli.main([*argv, "--check"]) == 0
    values.write_text(values.read_text().replace("perNodeBlockSize: 8", "perNodeBlockSize: 4"))
    assert fleet_cli.main([*argv, "--check"]) == 1
    assert f"{values}: differs from the site model" in capsys.readouterr().err
