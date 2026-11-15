import pytest

from run import inventory

HARNESS_HOSTVARS = {
    "ansible_host": "127.0.0.1",
    "ansible_port": 22212,
    "ansible_user": "ubuntu",
    "ansible_ssh_private_key_file": "/lab/ssh/id_ed25519",
    "ansible_ssh_common_args": "-o IdentitiesOnly=yes -o UserKnownHostsFile=/lab/ssh/known_hosts",
    "lab_mgmt_nic": "enp0s3",
    "lab_rdma_nic": "enp0s4",
    "lab_rdma_ip": "10.77.0.12",
    "lab_rxe_device": "rxe0",
}


def test_ssh_command_follows_the_ansible_connection_variables():
    argv = inventory.ssh_argv(HARNESS_HOSTVARS)
    assert argv[0] == "ssh"
    assert argv[-1] == "ubuntu@127.0.0.1"
    assert ("-p", "22212") in zip(argv, argv[1:])
    assert ("-i", "/lab/ssh/id_ed25519") in zip(argv, argv[1:])
    assert "UserKnownHostsFile=/lab/ssh/known_hosts" in argv
    assert "StrictHostKeyChecking=yes" in argv


def test_ssh_command_for_a_vmware_style_host_without_overrides():
    assert inventory.ssh_argv({"ansible_host": "192.168.140.12"})[-1] == "192.168.140.12"


def test_guest_without_nic_names_is_refused_with_the_missing_variable():
    hostvars = {k: v for k, v in HARNESS_HOSTVARS.items() if k != "lab_rdma_nic"}
    with pytest.raises(ValueError, match="lab_rdma_nic"):
        inventory.guest_from_hostvars("worker1", hostvars)


def test_workers_come_from_the_workers_group_only():
    listing = {
        "_meta": {"hostvars": {"worker1": HARNESS_HOSTVARS, "cp1": {"ansible_host": "192.168.140.11"}}},
        "workers": {"hosts": ["worker1"]},
        "control_plane": {"hosts": ["cp1"]},
    }
    guests = inventory.guests_from_listing(listing)
    assert list(guests) == ["worker1"]
    assert guests["worker1"].rdma_nic == "enp0s4"


def test_empty_workers_group_is_refused():
    with pytest.raises(ValueError, match="no hosts"):
        inventory.guests_from_listing({"_meta": {"hostvars": {}}, "workers": {"hosts": []}})
