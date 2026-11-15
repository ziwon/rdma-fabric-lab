import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[1] / "integration" / "qemu-lab"
sys.path.insert(0, str(HARNESS))

import guests  # noqa: E402
import switch  # noqa: E402

LAB = guests.parse((HARNESS / "profiles/lab.tsv").read_text())
HA = guests.parse((HARNESS / "profiles/ha.tsv").read_text())
HOSTVARS = {"cp1": {"k8s_node_ip": "192.168.140.11"}, "worker1": {"k8s_node_ip": "192.168.140.12"},
            "worker2": {"k8s_node_ip": "192.168.140.13"}}


def test_lab_profile_is_the_plan_guests_on_one_switched_network_and_one_cable():
    assert [g.name for g in LAB.guests] == ["cp1", "worker1", "worker2"]
    assert LAB.inventory == "ansible/inventories/lab"
    assert guests.switches(LAB) == {"mgmt": ["cp1", "worker1", "worker2"]}
    cables = [(g.name, n.target) for g in LAB.guests for n in g.nics if n.kind == "cable"]
    assert cables == [("worker1", "worker2"), ("worker2", "worker1")]


def test_user_nic_forwards_ssh_and_the_api_on_loopback_only():
    args = guests.qemu_nic_args(LAB.guest("cp1"), "/lab")
    user = args[args.index("-netdev") + 1]
    assert "hostfwd=tcp:127.0.0.1:22211-:22" in user and "hostfwd=tcp:127.0.0.1:22643-:6443" in user


def test_switch_and_cable_nics_are_unix_datagram_sockets_under_the_lab_dir():
    args = guests.qemu_nic_args(LAB.guest("worker1"), "/lab")
    netdevs = [args[i + 1] for i, a in enumerate(args) if a == "-netdev"][1:]
    assert all(n.startswith("dgram,") and "local.type=unix" in n and "remote.type=unix" in n for n in netdevs)
    assert "remote.path=/lab/net/cable-worker2-worker1.sock" in netdevs[0]
    assert "local.path=/lab/net/mgmt/worker1.nic" in netdevs[1] and "remote.path=/lab/net/mgmt/worker1.port" in netdevs[1]


def test_network_config_gives_the_node_ip_to_the_switched_nic_and_leaves_the_rdma_nic_to_ansible():
    config = guests.network_config(LAB, LAB.guest("worker1"), HOSTVARS["worker1"])
    entries = list(config["ethernets"].values())
    assert entries[0] == {"match": {"macaddress": "52:54:00:8c:00:12"}, "dhcp4": True}
    assert entries[1]["addresses"] == ["192.168.140.12/24"] and entries[1]["dhcp4"] is False
    assert len(entries) == 2


def test_declared_names_are_renamed_at_first_boot_and_get_no_inventory_variable():
    config = guests.network_config(HA, HA.guest("ha-cp1"), {"k8s_node_ip": "192.168.140.21"})
    assert config["ethernets"]["mgmt0"]["set-name"] == "mgmt0"
    found = {g.name: {n.mac: (n.name or f"enp0s{3 + i}") for i, n in enumerate(g.nics)} for g in HA.guests}
    hosts = guests.override_inventory(HA, found, "/k", "/kh")["all"]["hosts"]
    assert hosts["ha-cp1"]["lab_uplink_nic"] == "enp0s3"
    assert "lab_mgmt_nic" not in hosts["ha-cp1"] and "k8s_api_vip_interface" not in hosts["ha-cp1"]


def test_a_declared_name_the_guest_does_not_carry_is_refused():
    found = {g.name: {n.mac: f"enp0s{3 + i}" for i, n in enumerate(g.nics)} for g in HA.guests}
    with pytest.raises(guests.ProfileError, match="not the declared mgmt0"):
        guests.override_inventory(HA, found, "/k", "/kh")


def test_found_nic_names_land_in_the_variables_the_profile_names():
    found = {"cp1": {"52:54:00:8c:00:11": "enp0s3", "52:54:00:6d:00:11": "enp0s4"},
             "worker1": {"52:54:00:8c:00:12": "enp0s3", "52:54:00:4d:00:12": "enp0s4", "52:54:00:6d:00:12": "enp0s5"},
             "worker2": {"52:54:00:8c:00:13": "enp0s3", "52:54:00:4d:00:13": "enp0s4", "52:54:00:6d:00:13": "enp0s5"}}
    hosts = guests.override_inventory(LAB, found, "/k", "/kh")["all"]["hosts"]
    want = {"lab_uplink_nic": "enp0s3", "lab_rdma_nic": "enp0s4", "lab_mgmt_nic": "enp0s5", "ansible_port": 22212,
            "ansible_host": "127.0.0.1"}
    assert {k: hosts["worker1"][k] for k in want} == want


def test_api_url_is_the_forward_of_the_first_control_plane():
    listing = {"kube_control_plane": {"hosts": ["cp1"]}, "_meta": {"hostvars": {"cp1": {"k8s_api_port": 6443}}}}
    assert guests.api_url(LAB, listing) == "https://127.0.0.1:22643"


HEADER = "#inventory x\n#network mgmt 192.168.140.0/24\n"
ROW = "a\t1\t1\t1\t1\tmac=52:54:00:00:00:01,net=user"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("a\t1\t1\t1\t1\tmac=aa:bb:cc:00:00:01,net=user", "QEMU MAC"),
        (ROW + " mac=52:54:00:00:00:02,net=cable:b", "unknown guest b"),
        (ROW + "\nb\t1\t1\t1\t1\tmac=52:54:00:00:00:03,net=user", "host port used twice"),
        (ROW + ",ip=k8s_node_ip", "ip= and addr= need a switch NIC"),
        ("a\t1\t1\t1\t1\tmac=52:54:00:00:00:01,net=switch:mgmt", "first NIC, and only it, must be net=user"),
        (ROW + " mac=52:54:00:00:00:02,net=switch:other", "has no #network line"),
    ],
)
def test_broken_profiles_are_refused_with_the_reason(body, message):
    with pytest.raises(guests.ProfileError, match=message):
        guests.parse(HEADER + body + "\n")


def test_a_switched_network_inside_the_user_network_is_refused():
    with pytest.raises(guests.ProfileError, match="overlaps the user network"):
        guests.parse("#inventory x\n#network mgmt 10.0.2.0/25\n" + ROW + "\n")


def frame(dst: str, src: str) -> bytes:
    return bytes.fromhex(dst.replace(":", "")) + bytes.fromhex(src.replace(":", "")) + b"\x08\x00" + b"\x00" * 46


A, B, C = "52:54:00:00:00:0a", "52:54:00:00:00:0b", "52:54:00:00:00:0c"


def test_switch_floods_broadcast_and_unknown_destinations_to_the_other_ports():
    sw = switch.Switch(["cp1", "worker1", "worker2"])
    assert sw.route("cp1", frame("ff:ff:ff:ff:ff:ff", A)) == ["worker1", "worker2"]
    assert sw.route("worker1", frame(C, B)) == ["cp1", "worker2"]


def test_switch_delivers_a_learned_destination_to_its_port_only():
    sw = switch.Switch(["cp1", "worker1", "worker2"])
    sw.route("cp1", frame("ff:ff:ff:ff:ff:ff", A))
    sw.route("worker2", frame(A, C))
    assert sw.route("worker1", frame(A, B)) == ["cp1"]
    assert sw.route("worker1", frame(C, B)) == ["worker2"]


def test_switch_relearns_a_moved_mac_and_drops_a_frame_back_to_its_source_port():
    sw = switch.Switch(["cp1", "worker1"])
    sw.route("cp1", frame("ff:ff:ff:ff:ff:ff", A))
    sw.route("worker1", frame("ff:ff:ff:ff:ff:ff", A))
    assert sw.route("cp1", frame(A, B)) == ["worker1"]
    assert sw.route("worker1", frame(A, C)) == []
    assert sw.route("cp1", b"short") == []


STANDIN = guests.parse(
    "#inventory x\n#network mgmt 192.168.140.0/24\n#group stand_ins s\n"
    "a\t1\t1\t1\t1\tmac=52:54:00:00:00:01,net=user,fwd=9:6443 mac=52:54:00:00:00:02,net=switch:mgmt,ip=k8s_node_ip\n"
    "s\t2\t1\t1\t1\tmac=52:54:00:00:00:03,net=user mac=52:54:00:00:00:04,net=switch:mgmt,name=mgmt0,addr=192.168.140.5\n")


def test_a_profile_address_needs_no_inventory_variable():
    config = guests.network_config(STANDIN, STANDIN.guest("s"), {})
    assert config["ethernets"]["mgmt0"]["addresses"] == ["192.168.140.5/24"]


def test_grouped_guests_land_in_their_group_and_the_first_control_plane_gets_the_api_forward():
    found = {"a": {"52:54:00:00:00:01": "enp0s3", "52:54:00:00:00:02": "enp0s4"},
             "s": {"52:54:00:00:00:03": "enp0s3", "52:54:00:00:00:04": "mgmt0"}}
    tree = guests.override_inventory(STANDIN, found, "/k", "/kh", api=("a", "https://127.0.0.1:9"))["all"]
    assert list(tree["hosts"]) == ["a"] and tree["hosts"]["a"]["rfl_kubeconfig_server"] == "https://127.0.0.1:9"
    assert tree["children"] == {"stand_ins": {"hosts": {"s": tree["children"]["stand_ins"]["hosts"]["s"]}}}
    assert tree["children"]["stand_ins"]["hosts"]["s"]["ansible_port"] == 2
    assert "rfl_kubeconfig_server" not in tree["children"]["stand_ins"]["hosts"]["s"]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (ROW + " mac=52:54:00:00:00:02,net=switch:mgmt,addr=192.168.141.5", "lies outside mgmt"),
        (ROW + " mac=52:54:00:00:00:02,net=switch:mgmt,addr=192.168.140.5,ip=k8s_node_ip", "ip= or addr=, not both"),
        (ROW + " mac=52:54:00:00:00:02,net=switch:mgmt,addr=192.168.140.300", "is not an IPv4 address"),
        ("#group g b\n" + ROW, "unknown guest b"),
        ("#group g a\n#group h a\n" + ROW, "a guest belongs to one group only"),
    ],
)
def test_broken_addresses_and_groups_are_refused(body, message):
    with pytest.raises(guests.ProfileError, match=message):
        guests.parse(HEADER + body + "\n")
