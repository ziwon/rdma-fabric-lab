from pathlib import Path

import pytest
import yaml

from doctor import __main__ as doctor
from doctor import host, plan

REPO_ROOT = Path(__file__).resolve().parents[2]
HOST = REPO_ROOT / "tests" / "fixtures" / "a-soft-roce" / "host"
LAB = REPO_ROOT / "ansible/inventories/lab"
LAB_VARS = {k: v for f in ("lab.yml", "k8s.yml") for k, v in yaml.safe_load((LAB / "group_vars/all" / f).read_text()).items()}


def _hosts(group: dict) -> dict:
    found = dict(group.get("hosts") or {})
    for child in (group.get("children") or {}).values():
        found |= _hosts(child or {})
    return found


HOSTVARS = {name: LAB_VARS | (values or {}) for name, values in _hosts(yaml.safe_load((LAB / "hosts.yml").read_text())["all"]).items()}
PLAN, NODES = plan.from_hostvars(HOSTVARS)


def host_sources() -> list[host.Source]:
    return [
        host.Source("addresses", host.spans_from_ip_addr((HOST / "ip-addr-docker0.json").read_text())),
        host.Source(
            "routes",
            host.spans_from_ip_route((HOST / "ip-route-docker0.json").read_text())
            + host.spans_from_ip_route((HOST / "ip-route-tailscale-magicdns.json").read_text()),
        ),
        host.Source("docker", host.spans_from_docker((HOST / "docker-network-bridge.json").read_text())),
        host.Source("libvirt", host.spans_from_libvirt("default", (HOST / "libvirt-default.xml").read_text())),
    ]


def failures(findings):
    return [f.message for f in findings if f.level == "fail"]


def test_plan_of_the_inventory_clears_the_captured_host_networks():
    findings = doctor.address_findings(PLAN, NODES, host_sources())
    assert failures(findings) == []
    assert doctor.exit_code(findings) == 0


def test_rdma_network_inside_docker_bridge_conflicts():
    clashing = dict(PLAN, rdma="172.17.0.0/24", multus_pool="172.17.0.100-172.17.0.199")
    nodes = {"worker1": {"lab_rdma_ip": "172.17.0.12"}, "worker2": {"lab_rdma_ip": "172.17.0.13"}}
    findings = doctor.address_findings(clashing, nodes, host_sources())
    messages = failures(findings)
    assert doctor.exit_code(findings) == 1
    assert any("plan rdma 172.17.0.0/24" in m and "docker network bridge 172.17.0.0/16" in m for m in messages)
    assert any("plan rdma 172.17.0.0/24 overlaps route 172.17.0.0/16" in m for m in messages)
    assert any("multus_pool" in m and "172.17.0.0/16" in m for m in messages)


def test_service_range_over_cgnat_hits_the_overlay_route():
    findings = doctor.address_findings(dict(PLAN, service="100.64.0.0/10"), NODES, host_sources())
    assert any("plan service 100.64.0.0/10 overlaps route 100.100.100.100" in m for m in failures(findings))


def test_management_network_on_the_libvirt_bridge_conflicts():
    clashing = dict(PLAN, management="192.168.122.0/24")
    nodes = {"cp1": {"k8s_node_ip": "192.168.122.11"}}
    assert any("libvirt network default" in m for m in failures(doctor.address_findings(clashing, nodes, host_sources())))


def test_unreadable_source_warns_without_hiding_the_rest():
    sources = host_sources() + [host.Source("docker networks", [], "permission denied")]
    findings = doctor.address_findings(PLAN, NODES, sources)
    assert any(f.level == "warn" and "permission denied" in f.message for f in findings)
    assert doctor.exit_code(findings) == 0


@pytest.mark.parametrize(
    ("change", "nodes", "expected"),
    [
        ({"multus_pool": "10.78.0.100-10.78.0.199"}, {}, "lies outside plan rdma"),
        ({"pod": "10.77.0.0/16"}, {}, "plan rdma 10.77.0.0/24 overlaps plan pod 10.77.0.0/16"),
        ({"service": "192.168.0.0/16"}, {}, "plan management 192.168.140.0/24 overlaps plan service"),
        ({}, {"worker1": {"lab_rdma_ip": "10.77.0.150"}}, "falls inside plan multus_pool"),
        ({}, {"worker1": {"lab_rdma_ip": "10.78.0.12"}}, "lies outside plan rdma"),
        ({}, {"a": {"lab_rdma_ip": "10.77.0.12"}, "b": {"lab_rdma_ip": "10.77.0.12"}}, "is also assigned to"),
    ],
)
def test_contradictions_inside_the_plan_fail(change, nodes, expected):
    problems = plan.plan_problems(dict(PLAN, **change), nodes)
    assert any(expected in p for p in problems), problems


def test_plan_of_the_inventory_is_consistent():
    assert plan.plan_problems(PLAN, NODES) == []


def test_ipv6_host_range_never_overlaps_an_ipv4_plan():
    v6 = plan.span("kind bridge", "fc00:f853:ccd:e793::/64")
    assert plan.overlaps(PLAN, [v6]) == []


def test_span_reads_cidr_address_and_pool():
    assert plan.span("a", "10.77.0.0/24").last - plan.span("a", "10.77.0.0/24").first == 255
    assert plan.span("b", "10.77.0.12").first == plan.span("b", "10.77.0.12").last
    pool = plan.span("c", "10.77.0.100-10.77.0.199")
    assert pool.last - pool.first == 99
    with pytest.raises(ValueError):
        plan.span("d", "10.77.0.199-10.77.0.100")


def test_resource_shortfall_warns_and_never_fails():
    findings = doctor.resource_findings(cpus=8, mem_available_gib=12.0, disk_free_gib=20.0, disk_path="/x")
    assert [f.level for f in findings] == ["warn", "warn", "warn"]
    assert doctor.exit_code(findings) == 0


def test_resources_at_the_budget_pass():
    findings = doctor.resource_findings(cpus=16, mem_available_gib=26.0, disk_free_gib=110.0, disk_path="/x")
    assert [f.level for f in findings] == ["ok", "ok", "ok"]


def test_cpu_without_virtualization_extensions_fails():
    findings = doctor.virtualization_findings("flags\t: fpu sse2\n", kvm_rw=False, kvm_exists=False, hypervisors=[])
    assert doctor.exit_code(findings) == 1
    assert len(failures(findings)) == 2


def test_missing_required_tool_fails_and_missing_harness_tool_warns():
    present = {"ip", "ansible-playbook", "qemu-img", "cloud-localds", "ssh-keygen", "curl"}
    findings = doctor.tool_findings(lambda n: f"/usr/bin/{n}" if n in present else None, lambda argv: "v1")
    by_name = {f.message.split()[0]: f.level for f in findings}
    assert by_name["ssh"] == "fail"
    assert by_name["qemu-system-x86_64"] == "warn"
    assert by_name["vmrun"] == "info"


def test_report_ends_with_the_tally():
    text = doctor.report([doctor.Finding("x", "fail", "broken"), doctor.Finding("x", "warn", "meh")])
    assert text.splitlines()[-1] == "doctor: 1 failed, 1 warnings"


def test_plan_takes_pod_and_service_from_the_inventory_contract():
    hostvars = {
        "cp1": {"lab_address_plan": {"management": "192.168.140.0/24", "rdma": "10.77.0.0/24",
                                     "multus_pool": "10.77.0.100-10.77.0.199"},
                "k8s_pod_cidr": "10.244.0.0/16", "k8s_service_cidr": "10.96.0.0/12", "k8s_node_ip": "192.168.140.11"},
        "worker1": {"k8s_node_ip": "192.168.140.12", "lab_rdma_ip": "10.77.0.12", "ansible_port": 22212},
    }
    address_plan, nodes = plan.from_hostvars(hostvars)
    assert address_plan["pod"] == "10.244.0.0/16" and address_plan["service"] == "10.96.0.0/12"
    assert nodes == {"cp1": {"k8s_node_ip": "192.168.140.11"},
                     "worker1": {"k8s_node_ip": "192.168.140.12", "lab_rdma_ip": "10.77.0.12"}}
    assert plan.plan_problems(address_plan, nodes) == []


def test_node_ip_outside_the_management_network_is_a_plan_problem():
    nodes = {"worker1": {"k8s_node_ip": "192.168.141.12"}}
    assert any("lies outside plan management" in p for p in plan.plan_problems(PLAN, nodes))
