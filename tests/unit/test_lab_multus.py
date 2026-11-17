import copy
import json
from pathlib import Path

import pytest

from run import multus

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "a-soft-roce" / "m3"
NEW = FIX.parent / "m3-cni-dir"
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


LAYOUT = multus.CniLayout("/etc/cni/net.d", "/etc/cni/multus/net.d")


def layout(fix: Path = NEW, listing_name: str = "cni-ls.stdout", lay: multus.CniLayout = LAYOUT, **override) -> list[str]:
    texts = {"listing": (fix / listing_name).read_text(), "multus": (fix / "00-multus.conf").read_text(),
             "delegate": (fix / "05-cilium.conflist").read_text()} | override
    return multus.layout_problems(texts["listing"], texts["multus"], texts["delegate"], lay)


def test_multus_alone_in_the_cni_dir_naming_cilium_in_its_own_directory_passes():
    assert layout() == []


def test_the_m3_layout_with_cilium_beside_multus_fails_on_the_file_and_the_delegate_path():
    problems = layout(FIX)
    assert any("holds the CNI files ['00-multus.conf', '05-cilium.conflist']" in p for p in problems)
    assert any("clusterNetwork is '/host/etc/cni/net.d/05-cilium.conflist'" in p for p in problems)


def test_a_copy_of_ciliums_file_back_in_the_cni_dir_fails():
    assert layout(listing_name="cni-ls-cilium-copy.stdout") == [
        "/etc/cni/net.d holds the CNI files ['00-multus.conf', '05-cilium.conflist'], want 00-multus.conf alone"]


def test_a_missing_multus_file_or_another_delegate_fails():
    assert layout(listing=".kubernetes-cni-keep\nwhereabouts.d\n", multus="")
    assert layout(delegate='{"name": "x", "plugins": [{"type": "flannel"}]}')


def test_the_layout_comes_from_the_cilium_values_of_the_environment(tmp_path):
    (tmp_path / "cilium").mkdir()
    (tmp_path / "cilium" / "values-lab.yaml").write_text("cni:\n  confPath: /etc/cni/multus/net.d/\n  exclusive: false\n")
    (tmp_path / "cilium" / "values-cel1.yaml").write_text("ipam: {mode: kubernetes}\n")
    assert multus.layout_from_values("lab", tmp_path) == LAYOUT
    assert multus.layout_from_values("lab", tmp_path).delegate == "/etc/cni/multus/net.d/05-cilium.conflist"
    assert multus.layout_from_values("cel1", tmp_path) == multus.CniLayout("/etc/cni/net.d", "/etc/cni/net.d")


def boot_window(name: str, boot: float) -> multus.BootWindow:
    samples, born = multus.parse_window((NEW / f"window-{name}.stdout").read_text())
    return multus.BootWindow(boot, samples, born, multus.sandbox_start((NEW / f"containerd-{name}.stdout").read_text()))


def test_a_pod_held_until_multus_wrote_its_file_passes():
    w = boot_window("waited", 1794807261.0)
    assert len(w.samples) == 25 and {s.network for s in w.samples} == {"false"}
    assert w.multus_born == 1794807279.474 and w.sandbox_started == 1794807281.09202
    assert multus.window_problems(w) == []


def test_a_pod_started_while_ciliums_copy_gave_containerd_a_network_fails_twice():
    problems = multus.window_problems(boot_window("cilium-copy", 1794807432.0))
    assert problems[0].startswith("containerd reported a network in 24 of 24 samples")
    assert "05-cilium.conflist" in problems[0]
    assert problems[1].startswith("containerd began the pod's sandbox at boot+16.5 s, before Multus wrote its file at boot+18.")


def test_a_sample_whose_listing_already_shows_multus_straddled_its_write_and_does_not_count():
    w = boot_window("straddled", 1794809558.0)
    last = w.samples[-1]
    assert last.network == "true" and multus.MULTUS_FILE in last.files and last.at > w.multus_born
    assert len(multus.inside_window(w)) == len(w.samples) - 1
    assert multus.window_problems(w) == []


def test_no_look_inside_the_window_or_no_sandbox_is_a_failure_not_a_pass():
    w = boot_window("waited", 1794807261.0)
    assert "missed the window" in multus.window_problems(multus.BootWindow(w.boot, (), w.multus_born, w.sandbox_started))[0]
    assert multus.window_problems(multus.BootWindow(w.boot, w.samples, w.multus_born, None)) == [
        "containerd's journal of this boot holds no RunPodSandbox for the pod"]
    assert multus.window_problems(multus.BootWindow(w.boot, w.samples, None, w.sandbox_started)) == [f"{multus.MULTUS_FILE} did not appear"]


def test_a_sample_while_containerd_was_down_counts_as_no_network():
    samples, born = multus.parse_window("sample 10.5 down .kubernetes-cni-keep\nsample 11.0 false \nborn missing\n")
    assert [(s.at, s.network, s.files) for s in samples] == [(10.5, "down", (".kubernetes-cni-keep",)), (11.0, "false", ())]
    assert born is None


def test_the_boot_pod_tolerates_noschedule_taints_and_the_others_do_not():
    plain = multus.test_pod("ns", "r", "p", "node", "img", "net", "sleep 1", None)
    boot = multus.test_pod("ns", "r", "p", "node", "img", "net", "sleep 1", None, multus.BOOT_TOLERATIONS)
    assert "tolerations" not in plain["spec"]
    assert boot["spec"]["tolerations"] == [{"operator": "Exists", "effect": "NoSchedule"}]
    assert {k: v for k, v in boot["spec"].items() if k != "tolerations"} == plain["spec"]


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


def test_a_file_that_survived_the_reboot_names_the_shutdown_not_the_first_look():
    w = boot_window("survived", 1794848339.0)
    assert w.samples == () and w.multus_born == 1794847758.848
    problems = multus.window_problems(w)
    assert len(problems) == 1 and problems[0].startswith(f"{multus.MULTUS_FILE} survived the reboot, born at boot-580.2 s")
    assert "Conflicts=shutdown.target" in problems[0]
