import json
from pathlib import Path

from fleet import site
from run import cel1

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/cel1"
REPO = Path(__file__).resolve().parents[2]


def fixture(name):
    return (FIXTURES / name).read_text()


def test_the_dgx_guest_carries_every_interface_of_its_class_with_the_model_address():
    have = cel1.interface_addresses(json.loads(fixture("ip-addr-dgx-01.json")))
    model = site.load(REPO / "sites/cel1.yaml").hosts["cel1-dgx-01"]
    want = {name: str(address) for name, address in model.addresses.items()}
    assert len(want) == 11
    assert cel1.interface_problems(want, have) == []


def test_a_missing_interface_and_a_wrong_address_are_named():
    have = cel1.interface_addresses(json.loads(fixture("ip-addr-dgx-01.json")))
    have.pop("rail5")
    have["stor1"] = ["10.141.1.99"]
    want = {"rail5": "10.142.5.21", "stor1": "10.141.1.21", "mgmt0": "10.140.0.21"}
    assert cel1.interface_problems(want, have) == ["no interface rail5",
                                                   "stor1 has ['10.141.1.99'], want 10.141.1.21"]


def test_kube_proxy_replacement_names_its_devices():
    assert cel1.kube_proxy_replacement(fixture("cilium-status-dgx-01.txt")) == (True, ["mgmt0"])
    assert cel1.kube_proxy_replacement("KubeProxyReplacement:    False\n") == (False, [])
    two = "KubeProxyReplacement:    True   [mgmt0   10.140.0.21 (Direct Routing), rail0   10.142.0.21]\n"
    assert cel1.kube_proxy_replacement(two) == (True, ["mgmt0", "rail0"])
    assert cel1.kube_proxy_replacement("Cilium: Ok\n") == (None, [])


def test_the_dgx_containerd_defaults_to_the_nvidia_runtime_and_the_control_plane_to_runc():
    assert cel1.containerd_runtime(fixture("containerd-config-dump-dgx-01.toml")) == ("nvidia", "/usr/bin/nvidia-container-runtime")
    assert cel1.containerd_runtime(fixture("containerd-config-dump-cp-01.toml")) == ("runc", "/usr/local/sbin/runc")
    assert cel1.containerd_runtime("not = [toml") == (None, None)


def test_the_syslog_connection_leaves_from_the_node_ip():
    ss = fixture("ss-established-dgx-01.txt")
    assert cel1.established_to(ss, "10.140.0.5", 514) == ["10.140.0.21"]
    assert cel1.established_to(ss, "10.140.0.6", 514) == []


def test_labels_of_another_class_are_named():
    dgx = {"rfl.example/node-class": "dgx-b200", "nvidia.com/gpu.deploy.driver": "false"}
    pcie = {"rfl.example/node-class": "pcie-gpu"}
    assert cel1.label_problems(pcie, pcie, {"nvidia.com/gpu.deploy.driver": "false"}) == []
    assert cel1.label_problems(dgx, pcie, {"nvidia.com/gpu.deploy.driver": "false"}) == [
        "label rfl.example/node-class='dgx-b200', want 'pcie-gpu'", "carries nvidia.com/gpu.deploy.driver=false of another class"]


def test_a_vxlan_listing_of_links_of_other_types_names_no_tunnel():
    assert cel1.tunnel_links(fixture("ip-link-type-vxlan-dgx-02.json")) == []
    assert cel1.tunnel_links('[{},{"ifname":"cilium_vxlan","linkinfo":{"info_kind":"vxlan"}}]') == ["cilium_vxlan"]


def test_the_kubelet_registers_the_class_labels_of_the_inventory():
    assert cel1.registered_labels(fixture("kubeadm-flags-dgx-02.env")) == {
        "rfl.example/node-class": "dgx-b200", "nvidia.com/gpu.deploy.driver": "false", "nvidia.com/gpu.deploy.container-toolkit": "false"}
    assert cel1.registered_labels('KUBELET_KUBEADM_ARGS="--node-ip=10.140.0.11"') == {}
