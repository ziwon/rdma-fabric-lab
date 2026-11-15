import copy
import json
from pathlib import Path

import pytest
import yaml

from inventory import contract
from inventory.__main__ import cilium_values
from addons import releases

FIXTURE = json.loads((Path(__file__).resolve().parents[1] / "fixtures/fleet/cel1-inventory-list.json").read_text())
ENVIRONMENTS = {"lab", "cel1"}
CILIUM = {
    "kubeProxyReplacement": True,
    "k8sServiceHost": "10.140.0.10",
    "k8sServicePort": 6443,
    "routingMode": "native",
    "ipv4NativeRoutingCIDR": "10.160.0.0/16",
    "ipam": {"mode": "kubernetes"},
}


def codes(inventory, cilium=None, explicit=True):
    found = contract.problems(inventory, ENVIRONMENTS, lambda env: cilium, explicit=explicit)
    return [v.code for v in found], [str(v) for v in found]


def edited(change):
    inventory = copy.deepcopy(FIXTURE)
    change(inventory)
    return inventory


def hostvars(inventory, host):
    return inventory["_meta"]["hostvars"][host]


def every_host(inventory):
    return inventory["_meta"]["hostvars"].values()


def test_the_captured_cel1_inventory_holds_the_contract_with_every_variable_explicit():
    assert codes(FIXTURE, CILIUM) == ([], [])


@pytest.mark.parametrize(
    "change, code, text",
    [
        pytest.param(lambda i: i.pop("kube_node"), "missing-group", "group kube_node is not defined", id="missing-group"),
        pytest.param(lambda i: i["kube_control_plane"].update(hosts=[]), "missing-group", "kube_control_plane has no host", id="no-control-plane"),
        pytest.param(
            lambda i: i["kube_node"]["hosts"].append("cel1-cp-01"),
            "group-membership",
            "cel1-cp-01 is in both kube_control_plane and kube_node",
            id="host-in-both-roles",
        ),
        pytest.param(
            lambda i: i["k8s_cluster"].update(children=["kube_control_plane"]),
            "group-membership",
            "k8s_cluster is not kube_control_plane plus kube_node",
            id="cluster-is-not-the-union",
        ),
        pytest.param(
            lambda i: hostvars(i, "cel1-dgx-03").pop("k8s_node_ip"),
            "missing-variable",
            "cel1-dgx-03: k8s_node_ip is not set",
            id="host-without-node-ip",
        ),
        pytest.param(
            lambda i: hostvars(i, "cel1-dgx-03").update(k8s_node_ip="10.140.0.300"),
            "bad-value",
            "k8s_node_ip '10.140.0.300' is not an IPv4 address",
            id="node-ip-not-an-address",
        ),
        pytest.param(
            lambda i: [h.update(k8s_kube_proxy="false") for h in every_host(i)],
            "bad-value",
            "k8s_kube_proxy 'false' is not true or false",
            id="kube-proxy-as-text",
        ),
        pytest.param(
            lambda i: hostvars(i, "cel1-dgx-03").update(k8s_node_ip="{{ lookup('env', 'NODE_IP') }}"),
            "unresolved-template",
            "k8s_node_ip is a template this offline check cannot evaluate",
            id="unresolvable-template",
        ),
        pytest.param(
            lambda i: hostvars(i, "cel1-dgx-03").update(k8s_api_port=6444),
            "inconsistent-variable",
            "k8s_api_port differs between hosts",
            id="cluster-variable-differs",
        ),
        pytest.param(
            lambda i: hostvars(i, "cel1-dgx-03").update(k8s_node_ip="10.140.0.21"),
            "duplicate-node-ip",
            "10.140.0.21 is k8s_node_ip of cel1-dgx-01, cel1-dgx-03",
            id="duplicate-node-ip",
        ),
        pytest.param(
            lambda i: [h.update(k8s_service_cidr="10.160.128.0/17") for h in every_host(i)],
            "range-overlap",
            "k8s_pod_cidr 10.160.0.0/16 overlaps k8s_service_cidr 10.160.128.0/17",
            id="pod-overlaps-service",
        ),
        pytest.param(
            lambda i: hostvars(i, "cel1-pcie-02").update(k8s_node_ip="10.170.0.32"),
            "range-overlap",
            "k8s_node_ip of cel1-pcie-02 10.170.0.32 lies inside k8s_service_cidr",
            id="node-ip-inside-service-range",
        ),
        pytest.param(
            lambda i: [h.update(k8s_api_vip=None) for h in every_host(i)],
            "missing-vip",
            "3 control planes need k8s_api_vip",
            id="three-control-planes-without-vip",
        ),
        pytest.param(
            lambda i: [h.update(k8s_api_endpoint="api.cel1.example") for h in every_host(i)],
            "vip-endpoint-mismatch",
            "k8s_api_vip 10.140.0.10 is not k8s_api_endpoint api.cel1.example",
            id="vip-differs-from-endpoint",
        ),
        pytest.param(
            lambda i: [h.update(rfl_inventory="cel2") for h in every_host(i)],
            "unknown-environment",
            "rfl_inventory cel2 is not an environment of helm/releases.yaml",
            id="unknown-environment",
        ),
        pytest.param(
            lambda i: hostvars(i, "cel1-dgx-03")["k8s_node_labels"].update({"node-role.kubernetes.io/gpu": ""}),
            "reserved-label-prefix",
            "kubelet may not set labels under node-role.kubernetes.io",
            id="reserved-kubernetes-io-label",
        ),
        pytest.param(
            lambda i: hostvars(i, "cel1-dgx-03")["k8s_node_labels"].update({"k8s.io/rack": "a"}),
            "reserved-label-prefix",
            "kubelet may not set labels under k8s.io",
            id="reserved-k8s-io-label",
        ),
        pytest.param(
            lambda i: hostvars(i, "cel1-dgx-03")["k8s_node_labels"].update({"nvidia.com/gpu.deploy.driver": False}),
            "invalid-label",
            "value False is not a string, kubelet would get 'False'",
            id="label-value-not-a-string",
        ),
        pytest.param(
            lambda i: hostvars(i, "cel1-dgx-03")["k8s_node_labels"].update({"rfl.example/rack row": "a"}),
            "invalid-label",
            "not a valid label key",
            id="label-key-with-space",
        ),
        pytest.param(
            lambda i: hostvars(i, "cel1-dgx-03").update(k8s_node_labels=["gpu"]),
            "bad-value",
            "k8s_node_labels ['gpu'] is not a map",
            id="labels-not-a-map",
        ),
        pytest.param(
            lambda i: hostvars(i, "cel1-dgx-03").update(k8s_container_runtime_default="crun"),
            "bad-value",
            "cel1-dgx-03: k8s_container_runtime_default 'crun' is not one of runc, nvidia",
            id="runtime-not-runc-or-nvidia",
        ),
        pytest.param(
            lambda i: hostvars(i, "cel1-pcie-01").update(k8s_container_runtime_default="Nvidia"),
            "bad-value",
            "cel1-pcie-01: k8s_container_runtime_default 'Nvidia' is not one of runc, nvidia",
            id="runtime-in-another-case",
        ),
        pytest.param(
            lambda i: [h.update(k8s_admin_kubeconfig="") for h in every_host(i)],
            "bad-value",
            "k8s_admin_kubeconfig '' is not a non-empty string",
            id="empty-kubeconfig-path",
        ),
    ],
)
def test_each_violation_is_named(change, code, text):
    found_codes, messages = codes(edited(change), CILIUM)
    assert code in found_codes, messages
    assert any(text in m for m in messages), messages


def test_a_reference_to_another_variable_of_the_host_is_resolved():
    def change(i):
        for name, own in i["_meta"]["hostvars"].items():
            own["mgmt_ip"] = own["k8s_node_ip"]
            own["k8s_node_ip"] = "{{ mgmt_ip }}"

    assert codes(edited(change), CILIUM) == ([], [])


def test_explicit_mode_refuses_a_role_default_the_lab_may_rely_on():
    inventory = edited(lambda i: hostvars(i, "cel1-dgx-03").pop("k8s_node_labels"))
    assert codes(inventory, CILIUM, explicit=False) == ([], [])
    assert codes(inventory, CILIUM, explicit=True)[1] == ["missing-variable: cel1-dgx-03: k8s_node_labels is not set"]


def test_one_control_plane_without_a_vip_holds():
    def change(i):
        i["kube_control_plane"]["hosts"] = ["cel1-cp-01"]
        i["kube_node"]["hosts"] += ["cel1-cp-02", "cel1-cp-03"]
        for own in i["_meta"]["hostvars"].values():
            own["k8s_api_vip"] = None
            own["k8s_api_endpoint"] = "10.140.0.11"
        cilium = dict(CILIUM, k8sServiceHost="10.140.0.11")
        i["_cilium"] = cilium

    inventory = edited(change)
    assert codes(inventory, inventory.pop("_cilium")) == ([], [])


@pytest.mark.parametrize(
    "values, text",
    [
        pytest.param(dict(CILIUM, kubeProxyReplacement="false"), "both off", id="nobody-serves-services"),
        pytest.param(dict(CILIUM, k8sServiceHost="10.140.0.11"), "k8sServiceHost '10.140.0.11' is not k8s_api_endpoint", id="service-host"),
        pytest.param(dict(CILIUM, k8sServicePort=443), "k8sServicePort 443 is not k8s_api_port 6443", id="service-port"),
        pytest.param(
            dict(CILIUM, ipam={"mode": "cluster-pool", "operator": {"clusterPoolIPv4PodCIDRList": ["10.0.0.0/8"]}}),
            "clusterPoolIPv4PodCIDRList ['10.0.0.0/8'] is not [k8s_pod_cidr 10.160.0.0/16]",
            id="cluster-pool-cidr",
        ),
        pytest.param({k: v for k, v in CILIUM.items() if k != "ipam"}, "ipam.mode None takes pod addresses", id="chart-default-ipam"),
        pytest.param(dict(CILIUM, ipv4NativeRoutingCIDR="10.160.0.0/17"), "needs ipv4NativeRoutingCIDR to cover", id="native-cidr"),
    ],
)
def test_cilium_values_that_disagree_with_the_inventory_are_named(values, text):
    found_codes, messages = codes(FIXTURE, values)
    assert found_codes == ["cilium-mismatch"], messages
    assert text in messages[0]


def test_kube_proxy_kept_with_replacement_on_is_named():
    inventory = edited(lambda i: [h.update(k8s_kube_proxy=True) for h in every_host(i)])
    assert codes(inventory, CILIUM)[1] == ["cilium-mismatch: kube-proxy and the Cilium kube-proxy replacement are both on"]


def test_cilium_values_merge_in_release_order(tmp_path):
    (tmp_path / "cilium").mkdir()
    (tmp_path / "cilium/base.yaml").write_text(yaml.safe_dump({"ipam": {"mode": "cluster-pool", "keep": 1}, "devices": "a"}))
    (tmp_path / "cilium/values-cel1.yaml").write_text(yaml.safe_dump({"ipam": {"mode": "kubernetes"}}))
    table = [releases.Release("cilium", "kube-system", "cilium", "r", "cni.cilium_chart", ("cel1",), ("cilium/base.yaml", "cilium/values-{env}.yaml"))]
    assert cilium_values(table, tmp_path, "cel1") == {"ipam": {"mode": "kubernetes", "keep": 1}, "devices": "a"}
    assert cilium_values(table, tmp_path, "lab") is None


def test_the_dgx_nodes_default_to_the_nvidia_runtime_and_every_other_node_to_runc():
    runtimes = {h: v["k8s_container_runtime_default"] for h, v in FIXTURE["_meta"]["hostvars"].items()}
    assert {h for h, r in runtimes.items() if r == "nvidia"} == {f"cel1-dgx-0{n}" for n in range(1, 9)}
    assert set(runtimes.values()) == {"runc", "nvidia"}


def test_the_lab_may_leave_the_runtime_to_the_role_default_of_runc():
    inventory = edited(lambda i: [h.pop("k8s_container_runtime_default") for h in every_host(i)])
    assert codes(inventory, CILIUM, explicit=False) == ([], [])
    assert "missing-variable" in codes(inventory, CILIUM, explicit=True)[0]
