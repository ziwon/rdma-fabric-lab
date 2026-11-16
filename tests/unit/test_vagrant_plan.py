import dataclasses
from pathlib import Path

import pytest
import yaml

from vagrant import plan

REPO_ROOT = Path(__file__).resolve().parents[2]
LAB = REPO_ROOT / "ansible/inventories/lab"
LAB_VARS = {k: v for f in ("lab.yml", "k8s.yml") for k, v in yaml.safe_load((LAB / "group_vars/all" / f).read_text()).items()}


def _hosts(group: dict) -> dict:
    found = dict(group.get("hosts") or {})
    for child in (group.get("children") or {}).values():
        found |= _hosts(child or {})
    return found


HOSTVARS = {name: LAB_VARS | (values or {}) for name, values in _hosts(yaml.safe_load((LAB / "hosts.yml").read_text())["all"]).items()}
HARNESS = plan.harness()
QEMU = HARNESS.parse(plan.PROFILE.read_text())
VMWARE = plan.vmware_profile(QEMU)
RDMA = LAB_VARS["lab_address_plan"]["rdma"]
MACHINES = {m.name: m for m in plan.machines(VMWARE, HOSTVARS, RDMA)}

# Rendered from templates/commands/ssh_config/config.erb of Vagrant 2.4.9 with ssh_info_public and our
# private key. Replace it with a captured `vagrant ssh-config` once Vagrant runs on the lab host.
SSH_CONFIG = """Host cp1
  HostName 172.16.200.130
  User ubuntu
  Port 22
  UserKnownHostsFile /dev/null
  StrictHostKeyChecking no
  PasswordAuthentication no
  IdentityFile "/lab state/ssh/id_ed25519"
  IdentitiesOnly yes
  LogLevel FATAL
  PubkeyAcceptedKeyTypes +ssh-rsa
  HostKeyAlgorithms +ssh-rsa

Host worker1
  HostName 172.16.200.131
  User ubuntu
  Port 22
  IdentityFile /lab/ssh/id_ed25519
  IdentitiesOnly yes
"""


def test_vmware_macs_keep_the_profile_nic_identity_inside_the_static_range():
    assert plan.vmware_mac("52:54:00:8c:00:11") == "00:50:56:0c:00:11"
    assert plan.vmware_mac("52:54:00:6D:00:13") == "00:50:56:2d:00:13"
    for guest in VMWARE.guests:
        for nic in guest.nics:
            octets = nic.mac.split(":")
            assert octets[:3] == ["00", "50", "56"] and int(octets[3], 16) <= 0x3F


def test_two_profile_macs_that_collapse_onto_one_vmware_mac_are_refused():
    worker = QEMU.guest("worker1")
    twin = dataclasses.replace(worker.nics[1], mac="52:54:00:0c:00:12")
    clashing = dataclasses.replace(QEMU, guests=[dataclasses.replace(worker, nics=(worker.nics[0], twin))])
    with pytest.raises(plan.PlanError, match="both map to the VMware MAC 00:50:56:0c:00:12"):
        plan.vmware_profile(clashing)


def test_machines_take_sizes_from_the_profile_and_addresses_from_the_inventory():
    assert {n: (m.cpus, m.memory_mib, m.disk_gib) for n, m in MACHINES.items()} == {
        g.name: (g.vcpus, g.mem_mib, g.disk_gib) for g in QEMU.guests}
    assert [n.ip for n in MACHINES["cp1"].private_networks] == [HOSTVARS["cp1"]["k8s_node_ip"]]
    assert [n.ip for n in MACHINES["worker2"].private_networks] == [HOSTVARS["worker2"]["lab_rdma_ip"], HOSTVARS["worker2"]["k8s_node_ip"]]
    assert {n.netmask for m in MACHINES.values() for n in m.private_networks} == {"255.255.255.0"}


def test_the_nat_adapter_is_the_user_nic_and_each_private_network_keeps_its_nic_mac():
    worker = VMWARE.guest("worker1")
    assert MACHINES["worker1"].nat_mac == worker.nics[0].mac
    assert [n.mac for n in MACHINES["worker1"].private_networks] == [n.mac for n in worker.nics[1:]]


def test_an_address_outside_its_network_is_refused():
    moved = HOSTVARS | {"worker1": HOSTVARS["worker1"] | {"lab_rdma_ip": "10.78.0.12"}}
    with pytest.raises(plan.PlanError, match="10.78.0.12 lies outside 10.77.0.0/24"):
        plan.machines(VMWARE, moved, RDMA)


def test_a_guest_the_inventory_lacks_is_refused():
    with pytest.raises(plan.PlanError, match="worker2 is not a host of ansible/inventories/lab"):
        plan.machines(VMWARE, {k: v for k, v in HOSTVARS.items() if k != "worker2"}, RDMA)


def test_a_cable_guest_without_an_rdma_address_is_refused():
    bare = HOSTVARS | {"worker1": {k: v for k, v in HOSTVARS["worker1"].items() if k != "lab_rdma_ip"}}
    with pytest.raises(plan.PlanError, match="no lab_rdma_ip"):
        plan.machines(VMWARE, bare, RDMA)


def test_profiles_with_override_groups_or_two_cable_pairs_are_refused():
    with pytest.raises(plan.PlanError, match="#group"):
        plan.machines(dataclasses.replace(VMWARE, groups={"x": ("cp1",)}), HOSTVARS, RDMA)
    cp1 = VMWARE.guest("cp1")
    extra = dataclasses.replace(cp1.nics[1], kind="cable", target="worker1", mac="00:50:56:3e:00:11")
    two = dataclasses.replace(VMWARE, guests=[dataclasses.replace(cp1, nics=(*cp1.nics, extra)), *VMWARE.guests[1:]])
    with pytest.raises(plan.PlanError, match="one cable pair only"):
        plan.machines(two, HOSTVARS, RDMA)


def test_vagrantfile_plan_names_the_box_the_key_and_a_seed_per_machine():
    out = plan.vagrantfile_plan(list(MACHINES.values()), "rfl-noble-20261017", "/state")
    assert out["box"] == "rfl-noble-20261017"
    assert out["ssh"] == {"user": "ubuntu", "private_key": "/state/ssh/id_ed25519"}
    worker = out["machines"][1]
    assert worker["name"] == "worker1" and worker["seed"] == "/state/guests/worker1/seed.iso"
    assert worker["nat_mac"] == "00:50:56:0C:00:12"
    assert worker["private_networks"][0] == {"ip": "10.77.0.12", "netmask": "255.255.255.0", "mac": "0050560d0012"}


def test_seed_network_config_renames_the_lab_nic_lab0_by_its_vmware_mac():
    config = HARNESS.network_config(VMWARE, VMWARE.guest("worker1"), HOSTVARS["worker1"])
    entries = list(config["ethernets"].values())
    assert entries[0] == {"match": {"macaddress": "00:50:56:0c:00:12"}, "dhcp4": True}
    assert entries[1] == {"match": {"macaddress": "00:50:56:0d:00:12"}, "set-name": "lab0"}
    assert entries[2]["addresses"] == ["192.168.140.12/24"] and entries[2]["dhcp4"] is False


def test_user_data_is_cloud_config_with_the_key_and_the_host_key_and_no_upgrade():
    text = plan.user_data("worker1", "ssh-ed25519 AAAAclient rfl-vagrant\n", "-----BEGIN-----\nline\n-----END-----\n",
                          "ssh-ed25519 AAAAhost root@worker1\n")
    assert text.startswith("#cloud-config\n")
    data = yaml.safe_load(text)
    assert data["hostname"] == "worker1"
    assert data["users"][0]["name"] == "ubuntu" and data["users"][0]["ssh_authorized_keys"] == ["ssh-ed25519 AAAAclient rfl-vagrant"]
    assert data["ssh_keys"]["ed25519_private"] == "-----BEGIN-----\nline\n-----END-----\n"
    assert data["ssh_keys"]["ed25519_public"] == "ssh-ed25519 AAAAhost root@worker1"
    assert data["package_update"] is False and data["package_upgrade"] is False and data["ssh_pwauth"] is False


def test_meta_data_gives_each_guest_its_own_instance():
    assert yaml.safe_load(plan.meta_data("cp1")) == {"instance-id": "rfl-vagrant-cp1", "local-hostname": "cp1"}


def test_known_hosts_names_each_guest_so_a_new_dhcp_address_keeps_the_key():
    text = plan.known_hosts({"cp1": "ssh-ed25519 AAAAone root@cp1\n", "worker1": "ssh-ed25519 AAAAtwo root@worker1"})
    assert text == "cp1 ssh-ed25519 AAAAone\nworker1 ssh-ed25519 AAAAtwo\n"


def test_ssh_config_hosts_reads_each_block_and_unquotes_values():
    hosts = plan.ssh_config_hosts(SSH_CONFIG)
    assert set(hosts) == {"cp1", "worker1"}
    assert hosts["cp1"]["HostName"] == "172.16.200.130" and hosts["cp1"]["IdentityFile"] == "/lab state/ssh/id_ed25519"
    assert hosts["worker1"]["Port"] == "22"


def test_connection_pins_the_known_hosts_file_and_the_host_key_alias():
    entry = plan.connection("cp1", plan.ssh_config_hosts(SSH_CONFIG)["cp1"], "/state/ssh/known_hosts")
    assert entry["ansible_host"] == "172.16.200.130" and entry["ansible_port"] == 22 and entry["ansible_user"] == "ubuntu"
    assert entry["ansible_ssh_common_args"] == "-o IdentitiesOnly=yes -o UserKnownHostsFile=/state/ssh/known_hosts -o HostKeyAlias=cp1"
    with pytest.raises(plan.PlanError, match="lacks HostName"):
        plan.connection("cp1", {"Port": "22", "User": "u", "IdentityFile": "/k"}, "/kh")


def _found(rename: str = "lab0") -> dict:
    return {g.name: {n.mac: (rename if n.name else f"ens{160 + 32 * i}") for i, n in enumerate(g.nics)} for g in VMWARE.guests}


CONNECTIONS = {name: {"ansible_host": f"172.16.200.{130 + i}"} for i, name in enumerate(("cp1", "worker1", "worker2"))}


def test_override_inventory_maps_found_nic_names_onto_the_profile_variables():
    hosts = plan.override_inventory(VMWARE, CONNECTIONS, _found())["all"]["hosts"]
    assert hosts["worker1"] == {"ansible_host": "172.16.200.131", "lab_uplink_nic": "ens160", "lab_rdma_nic": "lab0",
                                "lab_mgmt_nic": "ens224"}
    assert hosts["cp1"] == {"ansible_host": "172.16.200.130", "lab_uplink_nic": "ens160", "lab_mgmt_nic": "ens192"}
    assert {hosts[w]["lab_rdma_nic"] for w in ("worker1", "worker2")} == {"lab0"}


def test_override_inventory_refuses_a_lab_nic_that_kept_its_kernel_name():
    with pytest.raises(plan.PlanError, match="not the declared lab0"):
        plan.override_inventory(VMWARE, CONNECTIONS, _found(rename="ens192"))


def test_override_inventory_refuses_a_guest_vagrant_does_not_list():
    with pytest.raises(plan.PlanError, match="names no worker2"):
        plan.override_inventory(VMWARE, {k: v for k, v in CONNECTIONS.items() if k != "worker2"}, _found())


def test_ansible_and_the_runner_read_the_override_on_top_of_the_lab_inventory(tmp_path):
    import json
    import subprocess
    import sys

    from run.inventory import guests_from_listing

    connections = {name: plan.connection(name, {"HostName": f"172.16.200.{130 + i}", "Port": "22", "User": "ubuntu",
                                                 "IdentityFile": "/state/ssh/id_ed25519"}, "/state/ssh/known_hosts")
                   for i, name in enumerate(("cp1", "worker1", "worker2"))}
    override = tmp_path / "inventory.yml"
    override.write_text(yaml.safe_dump(plan.override_inventory(VMWARE, connections, _found()), sort_keys=False))
    out = subprocess.run([str(Path(sys.executable).parent / "ansible-inventory"), "-i", str(LAB), "-i", str(override), "--list"],
                         capture_output=True, text=True, check=True, env={"ANSIBLE_CONFIG": str(REPO_ROOT / "ansible/ansible.cfg"),
                                                                          "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    listing = json.loads(out.stdout)
    workers = guests_from_listing(listing)
    assert workers["worker1"].rdma_nic == "lab0" and workers["worker1"].rdma_ip == "10.77.0.12"
    assert workers["worker1"].uplink_nic == "ens160" and workers["worker1"].mgmt_ip == "192.168.140.12"
    assert workers["worker2"].ssh[-1] == "ubuntu@172.16.200.132" and "HostKeyAlias=worker2" in workers["worker2"].ssh
    assert listing["_meta"]["hostvars"]["cp1"]["k8s_node_ip"] == "192.168.140.11"
