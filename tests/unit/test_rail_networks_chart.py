"""helm/rail-networks/chart and the cel1 releases around it (ADR 0007)."""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from addons import releases
from fleet import site

ROOT = Path(__file__).resolve().parents[2]
HELM_DIR = ROOT / "helm"
CHART = HELM_DIR / "rail-networks" / "chart"
CRD = CHART / "crds" / "nv-ipam.nvidia.com_ippools.yaml"
HELM = ROOT / ".tools" / "bin" / "helm"
LOCK = yaml.safe_load((ROOT / "versions.lock.yaml").read_text())
KUBE = LOCK["components"]["k8s.kubernetes"]["value"]
needs_helm = pytest.mark.skipif(not HELM.is_file(), reason="make tools installs the pinned helm into .tools/bin")


def release(name: str) -> releases.Release:
    return next(r for r in releases.load(HELM_DIR / "releases.yaml") if r.name == name)


def render(*extra: str) -> subprocess.CompletedProcess:
    argv = releases.template_argv(str(HELM), release("rail-networks"), "cel1", LOCK, HELM_DIR, KUBE)
    return subprocess.run([*argv, *extra], capture_output=True, text=True)


def objects() -> dict[str, list[dict]]:
    out = render()
    assert out.returncode == 0, out.stderr
    found: dict[str, list[dict]] = {}
    for d in yaml.safe_load_all(out.stdout):
        if d:
            found.setdefault(d["kind"], []).append(d)
    return found


def test_the_chart_carries_the_operator_ippool_crd_byte_for_byte():
    assert "sha256:" + hashlib.sha256(CRD.read_bytes()).hexdigest() == LOCK["components"]["addons.network_operator_ippool_crd"]["value"]


def test_the_chart_version_is_the_locked_network_operator_version():
    assert release("rail-networks").version == release("network-operator").version == "addons.network_operator_chart"
    assert releases.chart_argv(release("rail-networks"), ROOT, LOCK) == [str(CHART)]


def test_multus_follows_cilium_and_the_operator_crds_precede_the_rail_networks():
    cel1 = [r.name for r in releases.select(releases.load(HELM_DIR / "releases.yaml"), "cel1")]
    lab = [r.name for r in releases.select(releases.load(HELM_DIR / "releases.yaml"), "lab")]
    assert cel1 == ["cilium", "multus", "gpu-operator", "network-operator", "rail-networks"]
    assert lab == ["cilium", "multus", "whereabouts"]


def test_cel1_cilium_writes_where_cel1_multus_takes_its_delegate():
    cilium = yaml.safe_load((HELM_DIR / "cilium" / "values-cel1.yaml").read_text())
    multus = yaml.safe_load((HELM_DIR / "multus" / "values-cel1.yaml").read_text())
    assert cilium["cni"] == {"confPath": multus["daemonConfig"]["multusAutoconfigDir"], "exclusive": False}
    assert cilium["devices"] == "mgmt0"


def test_the_operator_chart_runs_no_nfd_and_no_crd_hook():
    values = yaml.safe_load((HELM_DIR / "network-operator" / "values-cel1.yaml").read_text())
    assert values == {"nfd": {"enabled": False, "deployNodeFeatureRules": False}, "upgradeCRDs": False, "keepNCP": False}


@needs_helm
def test_the_cel1_render_holds_one_policy_and_a_network_and_a_pool_per_rail():
    found = objects()
    assert {kind: len(items) for kind, items in found.items()} == {
        "CustomResourceDefinition": 1, "Namespace": 1, "NicClusterPolicy": 1, "IPoIBNetwork": 8, "IPPool": 8}
    namespace = found["Namespace"][0]
    assert namespace["metadata"]["name"] == "rdma-jobs"
    assert namespace["metadata"]["labels"] == {"pod-security.kubernetes.io/enforce": "privileged"}
    assert namespace["metadata"]["annotations"] == {"helm.sh/resource-policy": "keep"}


@needs_helm
def test_the_policy_runs_only_the_device_plugin_nv_ipam_and_ipoib_cni_on_the_dgx_class():
    spec = objects()["NicClusterPolicy"][0]["spec"]
    assert set(spec) == {"nodeAffinity", "rdmaSharedDevicePlugin", "nvIpam", "secondaryNetwork"}
    assert set(spec["secondaryNetwork"]) == {"ipoib"}
    terms = spec["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"]
    assert terms == [{"matchExpressions": [{"key": "rfl.example/node-class", "operator": "In", "values": ["dgx-b200"]}]}]
    assert spec["nvIpam"]["enableWebhook"] is False


@needs_helm
def test_every_policy_image_is_the_locked_digest():
    spec = objects()["NicClusterPolicy"][0]["spec"]
    components = {
        "rdma_shared_dev_plugin": spec["rdmaSharedDevicePlugin"],
        "nv_ipam": spec["nvIpam"],
        "ipoib_cni": spec["secondaryNetwork"]["ipoib"],
    }
    for key, component in components.items():
        locked = LOCK["components"][f"addons.network_operator_{key}_image"]
        assert f"{component['repository']}/{component['image']}" == locked["ref"].rsplit(":", 1)[0]
        assert component["version"] == locked["value"]


@needs_helm
def test_one_device_plugin_resource_selects_every_rail_and_admits_a_block_of_pods():
    cel1 = site.load(ROOT / "sites/cel1.yaml")
    spec = objects()["NicClusterPolicy"][0]["spec"]
    config = json.loads(spec["rdmaSharedDevicePlugin"]["config"])
    assert config == {"configList": [{
        "resourceName": "cel1_rails",
        "rdmaHcaMax": cel1.rail_networks.per_node_block,
        "selectors": {"ifNames": [f"rail{r}" for r in range(8)]},
    }]}


@needs_helm
def test_each_rail_network_uses_its_own_pool_in_the_operator_namespace():
    found = objects()
    pools = {p["metadata"]["name"]: p for p in found["IPPool"]}
    for network in found["IPoIBNetwork"]:
        name, spec = network["metadata"]["name"], network["spec"]
        rail = name.removeprefix("cel1-")
        assert spec["master"] == rail
        assert spec["networkNamespace"] == "rdma-jobs"
        assert json.loads(spec["ipam"]) == {"type": "nv-ipam", "poolName": name}
        pool = pools[name]
        assert pool["metadata"]["namespace"] == release("rail-networks").namespace == "nvidia-network-operator"
        assert pool["spec"]["subnet"] == f"10.143.{rail.removeprefix('rail')}.0/24"
        assert pool["spec"]["perNodeBlockSize"] == 16
        assert "gateway" not in pool["spec"]


@needs_helm
def test_an_image_tag_without_a_digest_is_refused():
    out = render("--set-string", "images.ipoibCni.tag=network-operator-v26.7.1")
    assert out.returncode != 0
    assert "images.ipoibCni.tag network-operator-v26.7.1 must be <tag>@sha256:<digest>" in out.stderr


@needs_helm
@pytest.mark.parametrize("override, message", [
    (["--set", "rails=null"], "rails is empty"),
    (["--set", "nodeSelector=null"], "nodeSelector is empty"),
    (["--set", "perNodeBlockSize=0"], "perNodeBlockSize must be at least 1"),
])
def test_incomplete_site_values_are_refused(override, message):
    out = render(*override)
    assert out.returncode != 0
    assert message in out.stderr
