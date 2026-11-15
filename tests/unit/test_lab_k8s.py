import json
from pathlib import Path

import pytest

from run import k8s, pcap

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "a-soft-roce"
NODES = json.loads((FIX / "k8s/get-nodes.json").read_text())
CILIUM = json.loads((FIX / "k8s/cilium-pods.json").read_text())
PODS = json.loads((FIX / "k8s/test-pods.json").read_text())
EXPECTED = {"cp1": "192.168.140.11", "worker1": "192.168.140.12", "worker2": "192.168.140.13"}
CIDRS = {n["metadata"]["name"]: n["spec"]["podCIDR"] for n in NODES["items"]}
SERVER_IP, CLIENT_IP = "10.244.2.102", "10.244.1.180"


def test_ready_nodes_on_their_contract_addresses_pass():
    problems, seen = k8s.node_problems(NODES, EXPECTED)
    assert problems == [] and len(seen) == 3


def test_a_node_on_another_address_or_missing_is_named():
    problems, _ = k8s.node_problems(NODES, EXPECTED | {"worker2": "192.168.140.99", "worker3": "192.168.140.14"})
    assert any("worker2 has InternalIP ['192.168.140.13'], want 192.168.140.99" in p for p in problems)
    assert "worker3 is not a Node" in problems


def test_one_ready_cilium_agent_per_node_passes_and_a_deleted_one_fails():
    assert k8s.cilium_problems(CILIUM, list(EXPECTED))[0] == []
    broken = json.loads(json.dumps(CILIUM))
    agent = next(p for p in broken["items"] if p["spec"]["nodeName"] == "worker1")
    agent["metadata"]["deletionTimestamp"] = "2026-11-15T05:00:00Z"
    problems, _ = k8s.cilium_problems(broken, list(EXPECTED))
    assert problems == [f"worker1 has 0 ready Cilium agents ({agent['metadata']['name']} Running)"]


def test_test_pods_run_on_their_workers_inside_the_node_pod_cidrs():
    problems, ips = k8s.pod_problems(PODS, {"server": "worker1", "client": "worker2"}, CIDRS)
    assert problems == [] and ips == {"server": SERVER_IP, "client": CLIENT_IP}


def test_a_pod_on_the_wrong_node_is_named():
    problems, _ = k8s.pod_problems(PODS, {"server": "worker2", "client": "worker2"}, CIDRS)
    assert problems[0] == "pod server runs on worker1, want worker2"


def test_nslookup_answer_is_read_without_the_server_address():
    text = (FIX / "k8s/nslookup-kubernetes-default.stdout").read_text()
    assert k8s.nslookup_addresses(text, "kubernetes.default.svc.cluster.local") == ["10.96.0.1"]
    assert k8s.nslookup_addresses(text, "other.svc.cluster.local") == []


def summary(name: str) -> dict:
    return pcap.summarize((FIX / "k8s" / name).read_bytes(), 8472, "10.244.0.0/16").as_dict()


def test_mgmt_capture_holds_the_test_pods_inside_vxlan_in_both_directions():
    s = summary("worker1-mgmt-vxlan.pcap")
    assert s["tunnel_frames"] > 0 and s["rocev2_frames"] == 0
    assert k8s.tunnel_problems(s, "192.168.140.12", "192.168.140.13", CLIENT_IP, SERVER_IP) == []


def test_mgmt_capture_with_vxlan_dropped_on_worker1_shows_one_direction_only():
    s = summary("worker1-mgmt-vxlan-dropped.pcap")
    problems = k8s.tunnel_problems(s, "192.168.140.12", "192.168.140.13", "10.244.1.72", "10.244.2.163")
    assert len(problems) == 1 and "pod flows inside VXLAN ['10.244.1.72>10.244.2.163']" in problems[0]


def test_lab_capture_has_marker_pings_and_no_pod_traffic():
    s = summary("worker1-lab.pcap")
    assert s["icmp_echo_frames"] > 0
    assert k8s.lab_nic_problems(s) == []
    assert k8s.lab_nic_problems(s | {"watch_net_frames": 3}) == ["3 frames to or from the pod CIDR"]
    assert k8s.lab_nic_problems(s | {"icmp_echo_frames": 0}) == ["no marker ping, so the capture may not have been live"]


def test_test_objects_are_labeled_unprivileged_and_pinned_to_their_workers():
    config = {"http_port": 8080, "service_port": 80, "server_node": "worker1", "client_node": "worker2"}
    objects = k8s.test_objects("rfl-x", "20261115T000000Z", "img@sha256:" + "0" * 64, config)
    assert [o["kind"] for o in objects] == ["Namespace", "Pod", "Pod", "Service"]
    assert all(o["metadata"]["labels"][k8s.RUN_LABEL] == "20261115T000000Z" for o in objects)
    server, client = objects[1], objects[2]
    assert server["spec"]["nodeSelector"] == {"kubernetes.io/hostname": "worker1"}
    assert client["spec"]["nodeSelector"] == {"kubernetes.io/hostname": "worker2"}
    for pod in (server, client):
        ctx = pod["spec"]["containers"][0]["securityContext"]
        assert ctx["runAsNonRoot"] and ctx["capabilities"] == {"drop": ["ALL"]} and not ctx["allowPrivilegeEscalation"]
        assert "hostNetwork" not in pod["spec"] and "volumes" not in pod["spec"]
    assert objects[3]["spec"]["ports"] == [{"port": 80, "targetPort": 8080, "protocol": "TCP"}]


def test_allow_policy_admits_only_the_client_label_on_the_server_port():
    policy = k8s.allow_policy("rfl-x", "r", 8080)
    assert policy["spec"]["ingress"] == [{"from": [{"podSelector": {"matchLabels": {"role": "client"}}}],
                                          "ports": [{"port": 8080, "protocol": "TCP"}]}]
    assert k8s.deny_policy("rfl-x", "r")["spec"] == {"podSelector": {}, "policyTypes": ["Ingress"]}


LISTING = {
    "_meta": {"hostvars": {
        "cp1": {"ansible_host": "127.0.0.1", "ansible_port": 22211, "k8s_node_ip": "192.168.140.11",
                "k8s_pod_cidr": "10.244.0.0/16", "k8s_service_cidr": "10.96.0.0/12"},
        "worker1": {"k8s_node_ip": "192.168.140.12", "lab_rdma_nic": "enp0s4", "lab_rdma_ip": "10.77.0.12"},
    }},
    "k8s_cluster": {"children": ["kube_control_plane", "kube_node"]},
    "kube_control_plane": {"hosts": ["cp1"]},
    "kube_node": {"children": ["workers"]},
    "workers": {"hosts": ["worker1"]},
}


def test_cluster_comes_from_nested_groups_with_the_first_control_plane():
    nodes, first, contract = k8s.cluster_from_listing(LISTING)
    assert list(nodes) == ["cp1", "worker1"] and first == "cp1"
    assert nodes["worker1"].rdma_nic == "enp0s4" and nodes["cp1"].rdma_nic is None
    assert contract == {"k8s_pod_cidr": "10.244.0.0/16", "k8s_service_cidr": "10.96.0.0/12"}


def test_a_cluster_host_without_a_node_ip_is_refused():
    broken = json.loads(json.dumps(LISTING))
    del broken["_meta"]["hostvars"]["worker1"]["k8s_node_ip"]
    with pytest.raises(ValueError, match="worker1"):
        k8s.cluster_from_listing(broken)
