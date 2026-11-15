import copy
from pathlib import Path

import pytest
import yaml

from fleet import site

REPO = Path(__file__).resolve().parents[2]

# Two rails, two leaves, one spine. Documentation addresses only.
MODEL = {
    "site": "t1",
    "kubernetes": {
        "node_network": "management",
        "api": {"vip": "192.0.2.10", "port": 6443},
        "pod_cidr": "203.0.113.0/25",
        "service_cidr": "203.0.113.128/26",
        "kube_proxy": False,
        "control_plane_schedulable": False,
    },
    "services": {"syslog": {"address": "192.0.2.5", "port": 514}},
    "networks": {
        "management": {"cidr": "192.0.2.0/26", "kind": "ethernet"},
        "rail0": {"cidr": "198.51.100.0/26", "kind": "infiniband", "rail": 0},
        "rail1": {"cidr": "198.51.100.64/26", "kind": "infiniband", "rail": 1},
    },
    "classes": {
        "control_plane": {"kube_role": "control_plane", "os": "Ubuntu 24.04", "interfaces": {"mgmt0": "management"}},
        "gpu": {
            "kube_role": "node",
            "os": "DGX OS 7",
            "interfaces": {
                "mgmt0": "management",
                "rail0": {"network": "rail0", "hca": "mlx5_0"},
                "rail1": {"network": "rail1", "hca": "mlx5_1"},
            },
            "labels": {"nvidia.com/gpu.deploy.driver": "false"},
        },
    },
    "hosts": {
        "t1-cp-01": {"class": "control_plane", "addresses": {"mgmt0": "192.0.2.11"}},
        "t1-gpu-01": {"class": "gpu", "addresses": {"mgmt0": "192.0.2.21", "rail0": "198.51.100.21", "rail1": "198.51.100.85"}},
        "t1-gpu-02": {"class": "gpu", "addresses": {"mgmt0": "192.0.2.22", "rail0": "198.51.100.22", "rail1": "198.51.100.86"}},
    },
    "fabrics": {
        "compute": {
            "kind": "infiniband",
            "topology": "rail-optimized",
            "link": {"width": "4x", "speed": "NDR"},
            "switch_models": {"small": {"ports": 4}},
            "switches": {
                "t1-leaf-r0": {"model": "small", "role": "leaf", "rail": 0},
                "t1-leaf-r1": {"model": "small", "role": "leaf", "rail": 1},
                "t1-spine-01": {"model": "small", "role": "spine"},
            },
            "links": [
                ["t1-gpu-01/rail0", "t1-leaf-r0/1"],
                ["t1-gpu-02/rail0", "t1-leaf-r0/2"],
                ["t1-gpu-01/rail1", "t1-leaf-r1/1"],
                ["t1-gpu-02/rail1", "t1-leaf-r1/2"],
                ["t1-leaf-r0/3", "t1-spine-01/1"],
                ["t1-leaf-r1/3", "t1-spine-01/2"],
            ],
        }
    },
}
LAB_PLAN = {"management": "192.0.2.192/27", "multus_pool": "192.0.2.230-192.0.2.239"}


def problems_after(change, lab_plan=None):
    raw = copy.deepcopy(MODEL)
    change(raw)
    return site.problems(site.parse(raw), lab_plan)


def links(raw):
    return raw["fabrics"]["compute"]["links"]


def test_the_small_model_is_valid():
    assert site.problems(site.parse(copy.deepcopy(MODEL)), LAB_PLAN) == []


def lab_group_vars():
    merged = {}
    for path in sorted((REPO / "ansible/inventories/lab/group_vars/all").glob("*.yml")):
        merged.update(yaml.safe_load(path.read_text()) or {})
    return merged


def test_the_cel1_site_model_is_valid_against_the_lab_plan():
    plan = site.lab_plan(lab_group_vars())
    assert {"management", "rdma", "pod", "service"} <= set(plan)
    assert site.problems(site.load(REPO / "sites/cel1.yaml"), plan) == []


def test_the_lab_pod_and_service_ranges_count_as_lab_plan_entries():
    plan = site.lab_plan({"lab_address_plan": {"management": "192.0.2.192/27"}, "k8s_pod_cidr": "198.51.100.0/24"})
    assert plan == {"management": "192.0.2.192/27", "pod": "198.51.100.0/24"}
    found = problems_after(lambda raw: None, {"pod": "198.51.100.0/24"})
    assert any(p.endswith("overlaps the lab address plan entry pod") for p in found)


def test_the_cel1_site_cables_eight_rails_of_eight_dgx_nodes():
    cel1 = site.load(REPO / "sites/cel1.yaml")
    fabric = cel1.fabrics["compute"]
    dgx = [h for h in cel1.hosts.values() if h.node_class == "dgx_b200"]
    rail_links = [(a, b) for a, b in fabric.links if a.node in cel1.hosts]
    assert len(dgx) == 8
    assert len(rail_links) == 64
    assert {(a.node, a.port) for a, _ in rail_links} == {(h.name, f"rail{r}") for h in dgx for r in range(8)}


def set_in(path, value):
    def change(raw):
        node = raw
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value

    return change


def remove(path):
    def change(raw):
        node = raw
        for key in path[:-1]:
            node = node[key]
        del node[path[-1]]

    return change


@pytest.mark.parametrize(
    "change, expected",
    [
        pytest.param(
            set_in(["classes", "control_plane", "interfaces"], {"Management0": "management"}),
            "not a Linux interface name",
            id="interface-name-charset",
        ),
        pytest.param(
            set_in(["classes", "control_plane", "interfaces"], {"mgmtinterface0001": "management"}),
            "not a Linux interface name",
            id="interface-name-too-long",
        ),
        pytest.param(
            set_in(["classes", "control_plane", "interfaces"], {"eth0": "management"}),
            "races with the kernel",
            id="interface-name-kernel-prefix",
        ),
        pytest.param(
            set_in(["classes", "control_plane", "interfaces", "mgmt0"], "oob"),
            "unknown network oob",
            id="unknown-network",
        ),
        pytest.param(
            set_in(["kubernetes", "node_network"], "rail0"),
            "needs exactly one interface on the node network",
            id="class-without-node-network-interface",
        ),
        pytest.param(set_in(["hosts", "t1-cp-01", "class"], "storage"), "unknown class storage", id="unknown-class"),
        pytest.param(
            set_in(["hosts", "t1-cp-01", "addresses"], {}),
            "t1-cp-01: no address for interface mgmt0",
            id="host-missing-address",
        ),
        pytest.param(
            set_in(["hosts", "t1-cp-01", "addresses", "stor0"], "192.0.2.40"),
            "address for stor0, which class control_plane does not have",
            id="host-address-for-unknown-interface",
        ),
        pytest.param(
            lambda raw: raw["fabrics"]["compute"]["switches"].update({"t1-cp-01": {"model": "small", "role": "spine"}}),
            "name t1-cp-01 is used by more than one host or switch",
            id="host-and-switch-share-a-name",
        ),
        pytest.param(
            lambda raw: raw["classes"].update({"kube_node": raw["classes"].pop("gpu")})
            or [h.update({"class": "kube_node"}) for h in raw["hosts"].values() if h["class"] == "gpu"],
            "a class name must be a new Ansible group name",
            id="class-name-is-a-contract-group",
        ),
        pytest.param(
            lambda raw: raw["hosts"].update({"T1_cp": raw["hosts"].pop("t1-cp-01")}),
            "not a Kubernetes node name",
            id="host-name-not-rfc1123",
        ),
        pytest.param(
            set_in(["networks", "rail1", "cidr"], "198.51.100.0/25"),
            "networks.rail0 198.51.100.0/26 overlaps networks.rail1",
            id="networks-overlap",
        ),
        pytest.param(
            set_in(["kubernetes", "pod_cidr"], "192.0.2.0/25"),
            "overlaps kubernetes.pod_cidr",
            id="network-overlaps-pod-cidr",
        ),
        pytest.param(
            set_in(["kubernetes", "service_cidr"], "203.0.113.0/26"),
            "kubernetes.pod_cidr 203.0.113.0/25 overlaps kubernetes.service_cidr",
            id="pod-overlaps-service",
        ),
        pytest.param(
            set_in(["hosts", "t1-cp-01", "addresses", "mgmt0"], "198.51.100.30"),
            "is not a host address of management",
            id="address-outside-its-network",
        ),
        pytest.param(
            set_in(["hosts", "t1-cp-01", "addresses", "mgmt0"], "192.0.2.0"),
            "is not a host address of management",
            id="address-is-the-network-address",
        ),
        pytest.param(
            set_in(["hosts", "t1-cp-01", "addresses", "mgmt0"], "192.0.2.10"),
            "address 192.0.2.10 is held by kubernetes.api.vip, hosts.t1-cp-01.addresses.mgmt0",
            id="host-takes-the-vip",
        ),
        pytest.param(
            set_in(["hosts", "t1-gpu-02", "addresses", "rail0"], "198.51.100.21"),
            "address 198.51.100.21 is held by",
            id="two-hosts-one-address",
        ),
        pytest.param(set_in(["kubernetes", "api", "vip"], "198.51.100.10"), "outside the node network", id="vip-outside-node-network"),
        pytest.param(
            lambda raw: [h.update({"class": "gpu", "addresses": {"mgmt0": "192.0.2.11", "rail0": "198.51.100.11", "rail1": "198.51.100.75"}})
                         for n, h in raw["hosts"].items() if n == "t1-cp-01"],
            "no host has a control_plane class",
            id="no-control-plane",
        ),
        pytest.param(
            lambda raw: raw["classes"].update(
                {"control_plane_b": {"kube_role": "control_plane", "os": "x", "interfaces": {"mgmt1": "management"}}}
            ) or raw["hosts"].update({"t1-cp-02": {"class": "control_plane_b", "addresses": {"mgmt1": "192.0.2.12"}}}),
            "kube-vip takes one",
            id="control-planes-disagree-on-the-vip-interface",
        ),
        pytest.param(
            set_in(["fabrics", "compute", "switches", "t1-spine-01", "model"], "huge"),
            "unknown switch model huge",
            id="unknown-switch-model",
        ),
        pytest.param(
            remove(["fabrics", "compute", "switches", "t1-leaf-r0", "rail"]),
            "a leaf names its rail and a spine does not",
            id="leaf-without-rail",
        ),
        pytest.param(
            set_in(["fabrics", "compute", "switches", "t1-spine-01", "rail"], 0),
            "a leaf names its rail and a spine does not",
            id="spine-with-rail",
        ),
        pytest.param(
            lambda raw: links(raw).__setitem__(4, ["t1-leaf-r0/5", "t1-spine-01/1"]),
            "t1-leaf-r0 has ports 1 to 4, not 5",
            id="port-beyond-radix",
        ),
        pytest.param(
            lambda raw: links(raw).__setitem__(4, ["t1-leaf-r0/0", "t1-spine-01/1"]),
            "t1-leaf-r0 has ports 1 to 4, not 0",
            id="port-zero",
        ),
        pytest.param(
            lambda raw: links(raw).__setitem__(4, ["t1-leaf-r0/up1", "t1-spine-01/1"]),
            "switch port up1 is not a number",
            id="port-not-a-number",
        ),
        pytest.param(
            lambda raw: links(raw).__setitem__(0, ["t1-gpu-01/rail0", "t1-leaf-r1/4"]),
            "rail 0 port cabled to t1-leaf-r1 of rail 1",
            id="rail-port-on-the-wrong-leaf",
        ),
        pytest.param(
            lambda raw: links(raw).append(["t1-gpu-01/mgmt0", "t1-leaf-r0/4"]),
            "t1-gpu-01/mgmt0 is not a compute rail interface",
            id="management-port-on-the-fabric",
        ),
        pytest.param(
            lambda raw: links(raw).append(["t1-gpu-09/rail0", "t1-leaf-r0/4"]),
            "unknown node t1-gpu-09",
            id="unknown-node-in-a-link",
        ),
        pytest.param(
            lambda raw: links(raw).__setitem__(0, ["t1-gpu-01/rail0", "t1-spine-01/3"]),
            "not rail to spine",
            id="rail-port-on-a-spine",
        ),
        pytest.param(
            lambda raw: links(raw).append(["t1-leaf-r0/4", "t1-leaf-r1/4"]),
            "not leaf to leaf",
            id="leaf-to-leaf",
        ),
        pytest.param(
            lambda raw: links(raw).__setitem__(1, ["t1-gpu-02/rail0", "t1-leaf-r0/1"]),
            "port t1-leaf-r0/1 is used by 2 links",
            id="switch-port-used-twice",
        ),
        pytest.param(
            lambda raw: links(raw).append(["t1-gpu-01/rail0", "t1-leaf-r0/4"]),
            "port t1-gpu-01/rail0 is used by 2 links",
            id="rail-port-used-twice",
        ),
        pytest.param(lambda raw: links(raw).pop(3), "rail port t1-gpu-02/rail1 is not cabled", id="rail-not-cabled"),
        pytest.param(lambda raw: links(raw).pop(5), "leaf t1-leaf-r1 has no uplink to t1-spine-01", id="leaf-without-uplink"),
    ],
)
def test_each_violation_is_named(change, expected):
    found = problems_after(change)
    assert any(expected in p for p in found), found


def test_a_range_overlapping_the_lab_plan_is_named_by_its_lab_entry():
    found = problems_after(lambda raw: None, {"pool": "192.0.2.20-192.0.2.29"})
    assert found == ["networks.management 192.0.2.0/26 overlaps the lab address plan entry pool"]


@pytest.mark.parametrize(
    "change, expected",
    [
        pytest.param(remove(["kubernetes", "pod_cidr"]), "kubernetes: missing pod_cidr", id="missing-key"),
        pytest.param(set_in(["hosts", "t1-cp-01", "rack"], 4), "hosts.t1-cp-01: unknown rack", id="unknown-key"),
        pytest.param(set_in(["networks", "rail0", "cidr"], "198.51.100.1/26"), "is not an IPv4 network", id="host-bits-in-a-cidr"),
        pytest.param(set_in(["hosts", "t1-cp-01", "addresses", "mgmt0"], "192.0.2.300"), "is not an IPv4 address", id="bad-address"),
        pytest.param(
            set_in(["classes", "gpu", "labels", "nvidia.com/gpu.deploy.driver"], False),
            "must be quoted strings",
            id="unquoted-label-value",
        ),
        pytest.param(set_in(["fabrics", "compute", "links"], [["t1-gpu-01/rail0"]]), "must be a pair of endpoints", id="half-a-link"),
        pytest.param(set_in(["fabrics", "compute", "link", "speed"], "XDR9"), "is not one of SDR", id="unknown-link-speed"),
    ],
)
def test_a_wrong_shape_is_refused_while_parsing(change, expected):
    raw = copy.deepcopy(MODEL)
    change(raw)
    with pytest.raises(site.SiteError, match=expected):
        site.parse(raw)


def test_a_name_defined_twice_in_the_file_is_refused(tmp_path):
    text = yaml.safe_dump(MODEL, sort_keys=False)
    doubled = text.replace("  t1-gpu-02:\n", "  t1-gpu-01:\n", 1)
    assert doubled != text
    (tmp_path / "s.yaml").write_text(doubled)
    with pytest.raises(site.SiteError, match="t1-gpu-01 defined twice"):
        site.load(tmp_path / "s.yaml")
