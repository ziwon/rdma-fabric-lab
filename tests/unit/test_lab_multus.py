import copy
import json
from pathlib import Path

import pytest

from run import multus

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "a-soft-roce" / "m3"
ADDRESS_PLAN = {"rdma": "192.0.2.0/24", "multus_pool": "192.0.2.100-192.0.2.199"}
LISTING = {
    "workers": {"hosts": ["w1", "w2"]},
    "_meta": {"hostvars": {
        "w1": {"lab_rdma_nic": "rd0", "lab_rdma_ip": "192.0.2.12", "lab_k8s_namespace": "lab-ns", "lab_address_plan": ADDRESS_PLAN},
        "w2": {"lab_rdma_nic": "rd0", "lab_rdma_ip": "192.0.2.13", "lab_k8s_namespace": "lab-ns", "lab_address_plan": ADDRESS_PLAN},
    }},
}


def listing(**w2) -> dict:
    out = copy.deepcopy(LISTING)
    out["_meta"]["hostvars"]["w2"] |= w2
    return out


def test_the_attachment_comes_from_the_inventory():
    att = multus.attachment_from_listing(LISTING)
    assert att == multus.Attachment("lab-ns", "rd0", "192.0.2.0/24", "192.0.2.100", "192.0.2.199")
    assert att.holds("192.0.2.150") and not att.holds("192.0.2.12")


def test_workers_whose_lab_nics_differ_are_refused_by_name():
    with pytest.raises(multus.AttachmentError, match="lab_rdma_nic differs across the workers \\(w1 rd0, w2 rd1\\)"):
        multus.attachment_from_listing(listing(lab_rdma_nic="rd1"))


@pytest.mark.parametrize(("plan", "message"), [
    ({"rdma": "192.0.2.0/24", "multus_pool": "198.51.100.1-198.51.100.9"}, "is not a range inside"),
    ({"rdma": "192.0.2.0/24", "multus_pool": "192.0.2.10-192.0.2.20"}, "holds the lab_rdma_ip of w1, w2"),
])
def test_a_pool_outside_the_lab_network_or_over_a_node_address_is_refused(plan, message):
    broken = copy.deepcopy(LISTING)
    for host in broken["_meta"]["hostvars"].values():
        host["lab_address_plan"] = plan
    with pytest.raises(multus.AttachmentError, match=message):
        multus.attachment_from_listing(broken)


def test_the_network_attachment_is_a_child_of_the_master_with_no_gateway():
    nad = multus.network_attachment(multus.attachment_from_listing(LISTING), "net", "macvlan", "bridge", "0.3.1")
    config = json.loads(nad["spec"]["config"])
    assert nad["metadata"] == {"name": "net", "namespace": "lab-ns"}
    assert (config["type"], config["master"], config["mode"]) == ("macvlan", "rd0", "bridge")
    assert config["ipam"] == {"type": "whereabouts", "range": "192.0.2.0/24", "range_start": "192.0.2.100",
                              "range_end": "192.0.2.199"}


def test_test_pods_are_ordinary_restricted_pods_that_ask_for_the_attachment():
    pod = multus.test_pod("ns", "20261116T000000Z", "p", "node", "img", "net", "sleep 1", None)
    assert pod["metadata"]["annotations"] == {multus.NETWORKS: "net"}
    assert "hostNetwork" not in pod["spec"]
    context = pod["spec"]["containers"][0]["securityContext"]
    assert context["runAsNonRoot"] and context["capabilities"] == {"drop": ["ALL"]}
    assert context["seccompProfile"] == {"type": "RuntimeDefault"} and context["allowPrivilegeEscalation"] is False


def cni(listing_text=None, multus_text=None, delegate=None) -> list[str]:
    return multus.cni_problems(listing_text or (FIX / "cni-ls.stdout").read_text(),
                               multus_text or (FIX / "00-multus.conf").read_text(),
                               delegate or (FIX / "05-cilium.conflist").read_text())


def test_multus_first_with_cilium_as_the_default_delegate_passes():
    assert cni() == []


def test_a_file_before_multus_or_another_delegate_fails():
    assert cni(listing_text="00-0-other.conflist\n00-multus.conf\n05-cilium.conflist\n")
    assert cni(listing_text="05-cilium.conflist\nwhereabouts.d\n")
    assert cni(delegate='{"name": "x", "plugins": [{"type": "flannel"}]}')


def test_network_status_names_eth0_from_cilium_and_net1_from_the_attachment():
    pods = json.loads((FIX / "net1-pods.json").read_text())["items"]
    nets = multus.pod_networks(pods[0])
    assert nets["eth0"]["network"] == "cilium" and nets["eth0"]["default"] is True
    assert nets["net1"]["network"] == "rdma-lab/rdma-lab-net" and len(nets["net1"]["ips"]) == 1
    assert multus.pod_networks({"metadata": {}}) == {}


def test_interface_address_and_routes_inside_a_pod():
    addr = (FIX / "net1-server-ip-addr.stdout").read_text()
    eth0, _, net1 = addr.partition(": net1")
    assert multus.interface_address(eth0) and multus.interface_address(net1)
    routes = (FIX / "net1-server-ip-route.stdout").read_text()
    get = (FIX / "net1-server-ip-route-get.stdout").read_text()
    assert multus.route_problems(routes, get) == []
    assert multus.route_problems(routes + "default via 192.0.2.1 dev net1\n", get)
    assert multus.route_problems(routes, "198.51.100.7 via 192.0.2.12 dev net1 src 192.0.2.100 uid 65534\n    cache\n")


def test_allocations_count_from_the_network_address_of_the_range():
    found = multus.allocations(json.loads((FIX / "ippools.json").read_text()))
    assert sorted((a["ip"], a["podref"]) for a in found) == [("10.77.0.100", "rdma-lab/net1-server"),
                                                            ("10.77.0.101", "rdma-lab/net1-client")]


def test_bookkeeping_flags_duplicates_strays_and_leaks():
    att = multus.Attachment("ns", "rd0", "192.0.2.0/24", "192.0.2.100", "192.0.2.199")
    held = [{"ip": "192.0.2.100", "podref": "ns/a"}, {"ip": "192.0.2.101", "podref": "ns/b"}]
    assert multus.bookkeeping_problems({"a": "192.0.2.100", "b": "192.0.2.101"}, held, "ns", att) == []
    assert any("held by two pods" in p for p in multus.bookkeeping_problems({"a": "192.0.2.100", "b": "192.0.2.100"},
                                                                           held, "ns", att))
    leak = held + [{"ip": "192.0.2.150", "podref": "ns/ghost"}]
    assert any("do not exist" in p for p in multus.bookkeeping_problems({"a": "192.0.2.100", "b": "192.0.2.101"}, leak, "ns", att))
    twice = held + [{"ip": "192.0.2.100", "podref": "other/x"}]
    assert any("allocated twice" in p for p in multus.bookkeeping_problems({"a": "192.0.2.100", "b": "192.0.2.101"},
                                                                           twice, "ns", att))


def test_a_pod_without_net1_is_a_problem_and_not_a_crash():
    att = multus.Attachment("ns", "rd0", "192.0.2.0/24", "192.0.2.100", "192.0.2.199")
    held = [{"ip": "192.0.2.100", "podref": "ns/a"}]
    assert multus.bookkeeping_problems({"a": "192.0.2.100", "b": None}, held, "ns", att) == ["b has no net1 address"]
