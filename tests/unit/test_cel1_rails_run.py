"""The parsing of cel1-rail-networks (ADR 0007 phase 2): what each check reads from the cluster and what it refuses."""

import json
from pathlib import Path

import yaml

from run import cel1_rails as rails

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/cel1/rails"
REPO = Path(__file__).resolve().parents[2]
VALUES = yaml.safe_load((REPO / "helm/rail-networks/values-cel1.yaml").read_text())
HOSTS = [f"cel1-dgx-0{n}" for n in range(1, 9)]
NETDEVS = [r["master"] for r in VALUES["rails"]]


def test_the_spec_lists_the_checks_the_runner_records():
    source = (REPO / "scripts/run/cel1_rails.py").read_text()
    recorded = [c for c in rails.check_ids() if f'"{c}"' in source]
    assert rails.check_ids() == tuple(recorded)
    assert len(rails.check_ids()) == 10


def node(name, labels=None, allocatable=None):
    return {"metadata": {"name": name, "labels": labels or {}}, "status": {"allocatable": allocatable or {}}}


def test_the_selector_picks_the_nodes_that_carry_all_its_labels():
    nodes = {"items": [node("a", {"rfl.example/node-class": "dgx-b200", "x": "1"}), node("b", {"rfl.example/node-class": "pcie-gpu"}),
                       node("c")]}
    assert rails.selected(nodes, {"rfl.example/node-class": "dgx-b200"}) == ["a"]


def daemonset(name, image, owner="NicClusterPolicy", ready=2):
    return {"metadata": {"name": name, "ownerReferences": [{"kind": owner, "name": "nic-cluster-policy"}]},
            "spec": {"template": {"spec": {"containers": [{"image": image}]}}},
            "status": {"numberReady": ready, "desiredNumberScheduled": 2}}


def pod(ds, node_name, is_ready=True):
    return {"metadata": {"name": f"{ds}-{node_name}", "ownerReferences": [{"kind": "DaemonSet", "name": ds}]},
            "spec": {"nodeName": node_name},
            "status": {"conditions": [{"type": "Ready", "status": "True" if is_ready else "False"}]}}


IMAGES = {"rdma-shared-device-plugin": "r@sha256:1", "nv-ipam": "n@sha256:2", "ipoib-cni": "i@sha256:3"}


def components(extra=(), hosts=("h1", "h2"), on=("h1", "h2")):
    sets = [daemonset("rdma", "r@sha256:1"), daemonset("ipam", "n@sha256:2"), daemonset("ipoib", "i@sha256:3"), *extra]
    pods = [pod(ds["metadata"]["name"], n) for ds in sets for n in on]
    return rails.component_problems({"items": sets}, {"items": pods}, IMAGES, list(hosts))


def test_three_components_on_exactly_the_rail_hosts_pass():
    problems, seen = components()
    assert problems == []
    assert len(seen) == 3


def test_a_fourth_component_of_the_policy_is_named():
    problems, _ = components(extra=[daemonset("multus", "m@sha256:9")])
    assert problems == ["the policy runs DaemonSets ['multus'] with image m@sha256:9, which is none of its three components"]


def test_a_component_missing_from_a_rail_host_is_named():
    problems, _ = components(hosts=("h1", "h2", "h3"))
    assert len(problems) == 3 and all("want exactly the rail hosts ['h1', 'h2', 'h3']" in p for p in problems)


def test_a_daemonset_another_owner_runs_does_not_count():
    sets = [daemonset("rdma", "r@sha256:1", owner="Deployment")]
    problems, _ = rails.component_problems({"items": sets}, {"items": []}, {"rdma-shared-device-plugin": "r@sha256:1"}, ["h1"])
    assert problems == ["rdma-shared-device-plugin: 0 DaemonSets of the policy run r@sha256:1, want 1"]


def ippool(allocations, subnet="10.143.0.0/24"):
    return {"metadata": {"name": "cel1-rail0"}, "spec": {"subnet": subnet},
            "status": {"allocations": [{"nodeName": n, "startIP": s, "endIP": e} for n, s, e in allocations]}}


def test_each_rail_host_holds_one_block_of_the_pool():
    pool = ippool([(h, f"10.143.0.{16 * i + 1}", f"10.143.0.{16 * i + 16}") for i, h in enumerate(HOSTS)])
    problems, seen = rails.block_problems(pool, HOSTS, 16, "10.143.0.0/24")
    assert problems == []
    assert seen == ["cel1-rail0 8 blocks 10.143.0.1-10.143.0.128"]


def test_a_missing_host_a_short_block_and_an_overlap_are_named():
    pool = ippool([("cel1-dgx-01", "10.143.0.1", "10.143.0.16"), ("cel1-dgx-02", "10.143.0.10", "10.143.0.20")])
    problems, _ = rails.block_problems(pool, ["cel1-dgx-01", "cel1-dgx-02", "cel1-dgx-03"], 16, "10.143.0.0/24")
    assert "cel1-rail0: blocks for ['cel1-dgx-01', 'cel1-dgx-02'], want one for each rail host ['cel1-dgx-01', 'cel1-dgx-02', " \
           "'cel1-dgx-03']" in problems
    assert "cel1-rail0: block 10.143.0.10-10.143.0.20 of cel1-dgx-02 holds 11 addresses, want 16" in problems
    assert "cel1-rail0: the blocks of cel1-dgx-01 and cel1-dgx-02 overlap" in problems


def test_a_block_outside_the_rail_range_is_named():
    problems, _ = rails.block_problems(ippool([("cel1-dgx-01", "10.142.0.1", "10.142.0.16")]), ["cel1-dgx-01"], 16, "10.143.0.0/24")
    assert "cel1-rail0: block 10.142.0.1-10.142.0.16 of cel1-dgx-01 leaves 10.143.0.0/24" in problems


def nad(name, master, kind="ipoib", pool=None, namespace="rdma-jobs"):
    config = {"cniVersion": "0.3.1", "name": name, "type": kind, "master": master, "ipam": {"type": "nv-ipam", "poolName": pool or name}}
    return {"metadata": {"name": name, "namespace": namespace}, "spec": {"config": json.dumps(config)}}


def test_the_operator_written_attachments_match_the_rails():
    nads = {"items": [nad(r["name"], r["master"]) for r in VALUES["rails"]]}
    assert rails.attachment_problems(nads, VALUES["rails"], "rdma-jobs") == []


def test_a_changed_master_a_missing_rail_and_another_type_are_named():
    nads = {"items": [nad(r["name"], r["master"]) for r in VALUES["rails"][2:]]}
    nads["items"][1] = nad("cel1-rail3", "stor0")
    nads["items"][2] = nad("cel1-rail4", "rail4", kind="macvlan")
    problems = rails.attachment_problems(nads, VALUES["rails"], "rdma-jobs")
    assert problems[:2] == ["no NetworkAttachmentDefinition rdma-jobs/cel1-rail0", "no NetworkAttachmentDefinition rdma-jobs/cel1-rail1"]
    assert any(p.startswith("cel1-rail3: {'type': 'ipoib', 'master': 'stor0'") for p in problems)
    assert any(p.startswith("cel1-rail4: {'type': 'macvlan'") for p in problems)


def test_the_resource_lives_on_the_rail_hosts_only():
    nodes = {"items": [node(h, allocatable={"rdma/cel1_rails": "0"}) for h in HOSTS] + [node("cel1-cp-01")]}
    problems, offered = rails.resource_problems(nodes, "rdma/cel1_rails", HOSTS, 0)
    assert problems == [] and sorted(offered) == HOSTS
    nodes["items"].append(node("cel1-pcie-01", allocatable={"rdma/cel1_rails": "16"}))
    problems, _ = rails.resource_problems(nodes, "rdma/cel1_rails", HOSTS, 0)
    assert problems == ["cel1-pcie-01 offers 16 rdma/cel1_rails, which only the rail hosts may"]


def test_the_plugin_loaded_the_rendered_config_and_exposes_no_device():
    log = (FIXTURES / "plugin-dgx-01.log").read_text()
    problems, line = rails.plugin_log_problems(log, VALUES["resourceName"], VALUES["perNodeBlockSize"], NETDEVS)
    assert problems == [] and line == 'exposing "0" devices'


def test_a_plugin_with_other_settings_or_devices_is_named():
    log = (FIXTURES / "plugin-dgx-01.log").read_text()
    problems, _ = rails.plugin_log_problems(log, "other", 8, NETDEVS[:7])
    assert problems == [f"the plugin loaded resource cel1_rails, rdmaHcaMax 16, ifNames {NETDEVS}; the values give other, 8, "
                        f"{NETDEVS[:7]}"]
    problems, _ = rails.plugin_log_problems(log + 'x exposing "8" devices\n', "cel1_rails", 16, NETDEVS)
    assert problems == ["the plugin exposes 8 devices, but no rail of the simulation has an RDMA device"]


def test_a_pod_held_for_the_resource_passes_and_a_scheduled_one_fails():
    events = {"items": [{"reason": "FailedScheduling", "message": "0/13 nodes are available: 1 Insufficient rdma/cel1_rails."}]}
    pending = {"spec": {}, "status": {"phase": "Pending"}}
    assert rails.waiting_problems(pending, events, "rdma/cel1_rails")[0] == []
    running = {"spec": {"nodeName": "cel1-dgx-04"}, "status": {"phase": "Running"}}
    problems, _ = rails.waiting_problems(running, {"items": []}, "rdma/cel1_rails")
    assert problems[0] == "the pod is Running on cel1-dgx-04, want Pending and unscheduled"
    assert problems[1].startswith("no FailedScheduling event names Insufficient rdma/cel1_rails")


def test_ipoib_cni_refusing_an_ethernet_master_is_its_own_error():
    message = (FIXTURES / "ipoib-refusal.txt").read_text()
    assert rails.ipoib_refusal(message, VALUES["rails"])
    missing = message.replace('master device is (device) not of type ipoib', 'failed to find plugin "ipoib" in path [/opt/cni/bin]')
    assert not rails.ipoib_refusal(missing, VALUES["rails"])


def test_a_running_attach_pod_or_one_without_the_refusal_fails():
    refusal = {"items": [{"reason": "FailedCreatePodSandBox", "message": (FIXTURES / "ipoib-refusal.txt").read_text()}]}
    pending = {"status": {"phase": "Pending"}}
    assert rails.refusal_problems(pending, refusal, VALUES["rails"])[0] == []
    running = {"status": {"phase": "Running", "containerStatuses": [{"ready": True, "state": {"running": {}}}]}}
    assert rails.refusal_problems(running, refusal, VALUES["rails"])[0] == ["the pod runs (phase Running) although every rail is Ethernet"]
    other = {"items": [{"reason": "FailedCreatePodSandBox", "message": "plugin type=\"multus-shim\" failed: no such network"}]}
    problems, _ = rails.refusal_problems(pending, other, VALUES["rails"])
    assert problems[0].startswith("no sandbox error is ipoib-cni refusing a rail master")


def test_the_locked_image_is_named_by_digest():
    lock = yaml.safe_load((REPO / "versions.lock.yaml").read_text())["components"]
    image = rails.locked_image(lock, "addons.network_operator_ipoib_cni_image")
    assert image == "nvcr.io/nvidia/mellanox/ipoib-cni@" + lock["addons.network_operator_ipoib_cni_image"]["value"]
