import copy
import sys
from pathlib import Path

import pytest

from fleet import sim, site
from test_fleet_site import MODEL

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests/integration/qemu-lab"))

import guests  # noqa: E402

CEL1 = site.load(REPO / "sites/cel1.yaml")
PROFILE = REPO / "tests/integration/qemu-lab/profiles/cel1.tsv"


def parsed(model=CEL1):
    return guests.parse(sim.profile(model, "ansible/inventories/x"))


def test_the_committed_profile_is_what_the_cel1_site_model_renders():
    assert PROFILE.read_text() == sim.profile(CEL1, "ansible/inventories/cel1")


def test_every_interface_of_every_host_is_a_nic_with_its_declared_name_on_its_network():
    profile = parsed()
    for host in CEL1.hosts.values():
        nics = {n.name: n for n in profile.guest(host.name).nics if n.kind == "switch"}
        want = {i.name: i.network for i in CEL1.classes[host.node_class].interfaces}
        assert {name: nic.target for name, nic in nics.items()} == want


def test_each_site_network_is_one_switched_network_with_its_cidr():
    profile = parsed()
    assert {name: str(net) for name, net in profile.networks.items()} == {n.name: str(n.cidr) for n in CEL1.networks.values()}
    members = guests.switches(profile)
    assert sorted(members["management"]) == sorted([*CEL1.hosts, "cel1-sim-syslog"])
    assert members["rail3"] == [f"cel1-dgx-0{n}" for n in range(1, 9)]


def test_guests_boot_with_the_addresses_of_the_model():
    profile = parsed()
    dgx = profile.guest("cel1-dgx-05")
    config = guests.network_config(profile, dgx, {"k8s_node_ip": "10.140.0.25"})["ethernets"]
    assert config["mgmt0"]["addresses"] == ["10.140.0.25/24"]
    assert config["stor1"]["addresses"] == ["10.141.1.25/24"]
    assert config["rail7"]["addresses"] == ["10.142.7.25/24"]
    assert all("routes" not in entry and "gateway4" not in entry for name, entry in config.items() if name != "nic0")


def test_the_syslog_stand_in_holds_the_syslog_address_outside_the_site_inventory():
    profile = parsed()
    assert profile.groups == {"simulation_syslog": ("cel1-sim-syslog",)}
    config = guests.network_config(profile, profile.guest("cel1-sim-syslog"), {})["ethernets"]
    assert config["mgmt0"]["addresses"] == ["10.140.0.5/24"]


def test_only_the_first_control_plane_forwards_the_api_and_panics_end_the_guest():
    profile = parsed()
    forwards = {g.name: n.forwards for g in profile.guests for n in g.nics if n.forwards}
    assert forwards == {"cel1-cp-01": ((sim.API_FORWARD_PORT, 6443),)}
    assert profile.qemu_flags == ("-no-reboot",)


def test_the_sizes_come_from_the_kube_role_and_fit_the_measured_host():
    profile = parsed()
    sizes = {g.name: (g.vcpus, g.mem_mib) for g in profile.guests}
    assert sizes["cel1-cp-02"] == (2, 3072) and sizes["cel1-dgx-08"] == (2, 2560) and sizes["cel1-pcie-01"] == (2, 2560)
    assert sim.footprint(CEL1) == {"guests": 14, "vcpus": 27, "mem_mib": 35584, "disk_gib": 270}


def test_a_syslog_address_outside_every_site_network_is_refused():
    raw = copy.deepcopy(MODEL)
    raw["services"]["syslog"]["address"] = "203.0.113.200"
    with pytest.raises(sim.SimError, match="lies in 0 site networks"):
        sim.profile(site.parse(raw), "x")
