import copy
import io
import json
import re
import tarfile
from pathlib import Path

import pytest
import yaml

from ibsim import __main__ as cli
from ibsim import guest, run
from ibsim.topology import HCA, Port, parse_netfile

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures" / "b-ibsim"
CONFIG = yaml.safe_load((REPO / "tracks/b-ibsim/config/scenarios.yaml").read_text())
SCENARIOS = sorted(CONFIG["scenarios"])
LOCK = yaml.safe_load((REPO / "versions.lock.yaml").read_text())
IMAGE = LOCK["components"]["ibsim.image"]["value"]


def params(scenario: str) -> dict:
    return {**CONFIG["defaults"], **CONFIG["scenarios"][scenario]}


def topology(scenario: str):
    return parse_netfile((REPO / params(scenario)["topology"]).read_text())


def port(spec: dict) -> Port:
    return Port(spec["node"], spec["port"])


def test_every_spec_file_has_a_config_entry():
    specs = sorted(p.stem for p in (REPO / "tracks/b-ibsim/scenarios").glob("*.yaml"))
    assert specs == SCENARIOS == ["b-01", "b-02", "b-03", "b-04"]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_each_spec_declares_exactly_the_checks_its_run_records(scenario):
    spec = yaml.safe_load((REPO / f"tracks/b-ibsim/scenarios/{scenario}.yaml").read_text())
    assert spec["scenario"] == scenario and spec["result_kind"] == "ib-management-simulation"
    assert tuple(c["id"] for c in spec["checks"]) == run.check_ids(params(scenario))


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_the_sm_host_is_a_linked_hca_and_the_break_and_fault_ports_are_linked(scenario):
    p, topo = params(scenario), topology(scenario)
    sm = Port(p["sm_host"], 1)
    assert topo.node(sm.node).kind == HCA and topo.peer(sm) is not None
    assert topo.peer(port(p["break"])) is not None
    assert sm not in (port(p["break"]), topo.peer(port(p["break"])))
    if "fault" in p:
        assert topo.peer(port(p["fault"])) is not None
        assert {port(p["fault"]), topo.peer(port(p["fault"]))} != {port(p["break"]), topo.peer(port(p["break"]))}


def test_the_b03_fault_cuts_one_endpoint_off():
    p, topo = params("b-03"), topology("b-03")
    reached = topo.without_link_at(port(p["fault"])).reachable_from(p["sm_host"])
    assert {n.name for n in topo.nodes} - {n.name for n in reached.nodes} == {"hca3"}


def test_the_b04_fault_leaves_every_cel1_node_reachable_over_the_other_spine_links():
    p, topo = params("b-04"), topology("b-04")
    assert topo.peer(port(p["fault"])).node.startswith("cel1-ib-spine-")
    reached = topo.without_link_at(port(p["fault"])).reachable_from(p["sm_host"])
    assert len(reached.nodes) == len(topo.nodes) == 76


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_a_break_netfile_is_the_topology_without_exactly_the_break_link(scenario):
    p, topo = params(scenario), topology(scenario)
    broken = parse_netfile(run.broken_netfile(p, topo))
    assert set(broken.nodes) == set(topo.nodes)
    assert topo.links - broken.links == {frozenset({port(p["break"]), topo.peer(port(p["break"]))})}
    assert broken.links <= topo.links


def inspect() -> dict:
    return json.loads((FIXTURES / "b-01/container/inspect.json").read_text())[0]


def top_uids(text: str) -> list[str]:
    return [line.split()[0] for line in text.splitlines()[1:] if line.split()]


UIDS = top_uids((FIXTURES / "b-01/container/top.txt").read_text())


def test_the_captured_fabric_container_is_locked_down():
    assert UIDS == ["10001", "10001", "10001"]
    assert run.container_problems(inspect(), IMAGE, UIDS) == []


def broken(path: tuple[str, ...], value):
    data = copy.deepcopy(inspect())
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return data


@pytest.mark.parametrize(
    ("path", "value", "problem"),
    [
        (("HostConfig", "Privileged"), True, "HostConfig.Privileged is True"),
        (("HostConfig", "NetworkMode"), "host", "HostConfig.NetworkMode is 'host'"),
        (("HostConfig", "Devices"), [{"PathOnHost": "/dev/infiniband/umad0"}], "HostConfig.Devices"),
        (("HostConfig", "CapDrop"), None, "HostConfig.CapDrop is None"),
        (("HostConfig", "CapAdd"), ["NET_ADMIN"], "HostConfig.CapAdd is ['NET_ADMIN']"),
        (("HostConfig", "SecurityOpt"), [], "no-new-privileges is not set"),
        (("HostConfig", "ReadonlyRootfs"), False, "HostConfig.ReadonlyRootfs is False"),
        (("HostConfig", "PidMode"), "host", "HostConfig.PidMode is 'host'"),
        (("Config", "User"), "", "Config.User is ''"),
        (("Config", "Env"), ["LD_PRELOAD=/usr/lib/x86_64-linux-gnu/umad2sim/libumad2sim.so"], "LD_PRELOAD is set"),
        (("AppArmorProfile",), "unconfined", "AppArmorProfile is 'unconfined'"),
        (("Image",), "sha256:" + "0" * 64, "not the locked"),
    ],
)
def test_each_loosened_setting_is_reported(path, value, problem):
    problems = run.container_problems(broken(path, value), IMAGE, UIDS)
    assert len(problems) == 1 and problem in problems[0]


def test_a_writable_mount_and_a_root_process_are_reported():
    data = inspect()
    data["Mounts"][0]["RW"] = True
    problems = run.container_problems(data, IMAGE, ["10001", "0"])
    assert any("mount /topology.net is writable" in p for p in problems)
    assert any("UIDs ['0', '10001']" in p for p in problems)


def test_the_process_scan_finds_ibsim_and_opensm_while_a_fabric_runs():
    found = run.host_simulator_processes((FIXTURES / "guest-ps/fabric-running.txt").read_text())
    assert [line.split()[1] for line in found] == ["ibsim", "opensm"]


def test_the_process_scan_finds_nothing_once_the_fabric_stopped():
    assert run.host_simulator_processes((FIXTURES / "guest-ps/fabric-stopped.txt").read_text()) == []


def test_every_dockerfile_argument_comes_from_the_lock():
    names = re.findall(r"^ARG (\w+)$", (REPO / "images/ibsim/Dockerfile").read_text(), re.M)
    args = dict(a.split("=", 1) for a in cli.build_args(LOCK))
    assert sorted(args) == sorted(names)
    assert all(args.values()) and "null" not in args.values()
    base = LOCK["components"]["ibsim.base_image"]
    assert args["BASE_IMAGE"] == f"{base['ref']}@{base['value']}" and base["value"].startswith("sha256:")


def test_the_guest_copy_is_the_same_bytes_for_the_same_tree_and_keeps_scripts_executable():
    first, second = guest.synced_tar(REPO), guest.synced_tar(REPO)
    assert first == second
    with tarfile.open(fileobj=io.BytesIO(first)) as tar:
        members = {m.name: m for m in tar.getmembers()}
    for script in ("tracks/b-ibsim/scripts/guest.sh", "images/ibsim/rfl-ibsim"):
        assert members[script].mode & 0o111 == 0o111
    assert "images/ibsim/Dockerfile" in members and "tracks/b-ibsim/topologies/cel1-compute.net" in members
