import copy
from pathlib import Path

import yaml

from fleet import render, site
from test_fleet_site import MODEL

REPO = Path(__file__).resolve().parents[2]


def rendered():
    return render.render(site.parse(copy.deepcopy(MODEL)))


def test_hosts_land_in_the_contract_groups_and_one_group_per_class():
    children = yaml.safe_load(rendered()["hosts.yml"])["all"]["children"]
    cluster = children["k8s_cluster"]["children"]
    assert list(cluster["kube_control_plane"]["hosts"]) == ["t1-cp-01"]
    assert list(cluster["kube_node"]["hosts"]) == ["t1-gpu-01", "t1-gpu-02"]
    assert list(children["control_plane"]["hosts"]) == ["t1-cp-01"]
    assert list(children["gpu"]["hosts"]) == ["t1-gpu-01", "t1-gpu-02"]


def test_variables_follow_the_contract_and_labels_stay_strings():
    files = rendered()
    cluster = yaml.safe_load(files["group_vars/all.yml"])
    assert cluster == {
        "rfl_inventory": "t1",
        "k8s_api_endpoint": "192.0.2.10",
        "k8s_api_port": 6443,
        "k8s_api_vip": "192.0.2.10",
        "k8s_api_vip_interface": "mgmt0",
        "k8s_pod_cidr": "203.0.113.0/25",
        "k8s_service_cidr": "203.0.113.128/26",
        "k8s_kube_proxy": False,
        "k8s_control_plane_schedulable": False,
        "rsyslog_client_tcp_host": "192.0.2.5",
        "rsyslog_client_tcp_port": 514,
    }
    assert yaml.safe_load(files["group_vars/gpu.yml"]) == {"k8s_node_labels": {"nvidia.com/gpu.deploy.driver": "false"}}
    assert yaml.safe_load(files["group_vars/control_plane.yml"]) == {"k8s_node_labels": {}}
    assert yaml.safe_load(files["host_vars/t1-gpu-02.yml"]) == {"k8s_node_ip": "192.0.2.22"}
    assert not any("ansible_host" in text for text in files.values())


def test_rendering_twice_gives_the_same_bytes():
    assert rendered() == rendered()


def test_drift_names_an_edit_a_missing_file_and_leftovers(tmp_path):
    files = rendered()
    render.write(files, tmp_path)
    assert render.drift(files, tmp_path) == []
    (tmp_path / "host_vars/t1-gpu-01.yml").write_text("k8s_node_ip: 192.0.2.99\n")
    (tmp_path / "host_vars/t1-gpu-02.yml").unlink()
    (tmp_path / "host_vars/t1-gpu-03.yml").write_text(render.HEADER.format(name="t1") + "k8s_node_ip: 192.0.2.23\n")
    (tmp_path / "group_vars/extra.yml").write_text("x: 1\n")
    assert render.drift(files, tmp_path) == [
        f"{tmp_path}/host_vars/t1-gpu-02.yml: missing",
        f"{tmp_path}/host_vars/t1-gpu-01.yml: differs from the site model",
        f"{tmp_path}/group_vars/extra.yml: not rendered from the site model",
        f"{tmp_path}/host_vars/t1-gpu-03.yml: rendered earlier, no longer in the site model",
    ]


def test_write_removes_its_own_leftovers_and_keeps_hand_written_files(tmp_path):
    files = rendered()
    render.write(files, tmp_path)
    (tmp_path / "host_vars/t1-gpu-03.yml").write_text(render.HEADER.format(name="t1") + "k8s_node_ip: 192.0.2.23\n")
    (tmp_path / "group_vars/extra.yml").write_text("x: 1\n")
    assert render.write(files, tmp_path) == [f"{tmp_path}/group_vars/extra.yml"]
    assert not (tmp_path / "host_vars/t1-gpu-03.yml").exists()


def test_the_committed_cel1_inventory_is_what_its_site_model_renders():
    files = render.render(site.load(REPO / "sites/cel1.yaml"))
    assert render.drift(files, REPO / "ansible/inventories/cel1") == []
