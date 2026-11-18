from pathlib import Path

import pytest

from addons import releases

LOCK = {"components": {
    "cni.cilium_chart": {"value": "1.20.4"},
    "addons.gpu_operator_chart": {"value": "v26.7.2"},
    "addons.undecided": {"value": None},
}}


def table(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "releases.yaml"
    path.write_text(body)
    return path


TABLE = """
releases:
  - {name: cilium, namespace: kube-system, chart: cilium, repo: https://helm.cilium.io,
     version: cni.cilium_chart, environments: [lab, cel1], values: [cilium/values.yaml, "cilium/values-{env}.yaml"]}
  - {name: gpu-operator, namespace: gpu-operator, chart: gpu-operator, repo: https://helm.ngc.nvidia.com/nvidia,
     version: addons.gpu_operator_chart, environments: [cel1], values: ["gpu-operator/values-{env}.yaml"]}
"""


@pytest.fixture
def helm_dir(tmp_path):
    for name in ("cilium/values.yaml", "cilium/values-lab.yaml", "cilium/values-cel1.yaml", "gpu-operator/values-cel1.yaml"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("{}\n")
    return tmp_path


def test_an_environment_gets_its_releases_in_table_order(tmp_path):
    loaded = releases.load(table(tmp_path, TABLE))
    assert [r.name for r in releases.select(loaded, "cel1")] == ["cilium", "gpu-operator"]
    assert [r.name for r in releases.select(loaded, "lab")] == ["cilium"]


def test_asking_for_a_release_the_environment_does_not_install_fails(tmp_path):
    with pytest.raises(releases.ReleaseError):
        releases.select(releases.load(table(tmp_path, TABLE)), "lab", "gpu-operator")


def test_apply_installs_the_locked_chart_with_the_environment_values(tmp_path, helm_dir):
    cilium = releases.load(table(tmp_path, TABLE))[0]
    argv = releases.apply_argv("helm", cilium, "lab", LOCK, helm_dir, Path("kc"))
    assert argv[:5] == ["helm", "upgrade", "--install", "cilium", "cilium"]
    assert argv[argv.index("--version") + 1] == "1.20.4"
    assert argv[argv.index("--namespace") + 1] == "kube-system"
    values = [argv[i + 1] for i, a in enumerate(argv) if a == "--values"]
    assert values == [str(helm_dir / "cilium/values.yaml"), str(helm_dir / "cilium/values-lab.yaml")]
    assert "--wait" in argv and argv[argv.index("--kubeconfig") + 1] == "kc"
    assert "--force-conflicts" not in argv


def test_a_forced_apply_takes_back_fields_another_manager_changed(tmp_path, helm_dir):
    cilium = releases.load(table(tmp_path, TABLE))[0]
    argv = releases.apply_argv("helm", cilium, "lab", LOCK, helm_dir, Path("kc"), force_conflicts=True)
    assert argv[-1] == "--force-conflicts"


def test_template_renders_for_the_given_kubernetes_version(tmp_path, helm_dir):
    gpu = releases.load(table(tmp_path, TABLE))[1]
    argv = releases.template_argv("helm", gpu, "cel1", LOCK, helm_dir, "v1.36.6")
    assert argv[:2] == ["helm", "template"] and argv[argv.index("--kube-version") + 1] == "v1.36.6"
    assert "--kubeconfig" not in argv


def test_a_missing_values_file_stops_the_release(tmp_path, helm_dir):
    (helm_dir / "cilium/values-lab.yaml").unlink()
    cilium = releases.load(table(tmp_path, TABLE))[0]
    with pytest.raises(releases.ReleaseError, match="values-lab.yaml"):
        releases.apply_argv("helm", cilium, "lab", LOCK, helm_dir, Path("kc"))


@pytest.mark.parametrize("key", ["addons.undecided", "addons.unknown"])
def test_a_chart_version_must_be_pinned_in_the_lock(tmp_path, helm_dir, key):
    body = TABLE.replace("version: cni.cilium_chart", f"version: {key}")
    cilium = releases.load(table(tmp_path, body))[0]
    with pytest.raises(releases.ReleaseError, match=key):
        releases.apply_argv("helm", cilium, "lab", LOCK, helm_dir, Path("kc"))


def test_duplicate_release_names_are_refused(tmp_path):
    body = TABLE.replace("name: gpu-operator", "name: cilium")
    with pytest.raises(releases.ReleaseError, match="duplicate"):
        releases.load(table(tmp_path, body))


def test_the_repository_table_names_pinned_charts():
    root = Path(__file__).resolve().parents[2]
    import yaml
    lock = yaml.safe_load((root / "versions.lock.yaml").read_text())
    for release in releases.load(root / "helm" / "releases.yaml"):
        assert releases.chart_version(release, lock)


def test_later_values_files_merge_maps_key_by_key():
    merged = releases.merge_values([{"ipam": {"mode": "cluster-pool", "operator": {"x": 1}}}, {"ipam": {"mode": "kubernetes"}}])
    assert merged == {"ipam": {"mode": "kubernetes", "operator": {"x": 1}}}


def test_a_later_list_replaces_the_earlier_list():
    assert releases.merge_values([{"devices": ["eth0", "eth1"]}, {"devices": ["ib0"]}]) == {"devices": ["ib0"]}


def test_a_later_null_stays_as_a_null_key_in_the_stored_values():
    merged = releases.merge_values([{"hubble": {"enabled": True}, "mtu": 1500}, {"hubble": None}])
    assert merged == {"hubble": None, "mtu": 1500}


def test_a_later_scalar_replaces_a_map_and_a_map_replaces_a_scalar():
    assert releases.merge_values([{"a": {"b": 1}}, {"a": 2}]) == {"a": 2}
    assert releases.merge_values([{"a": 2}, {"a": {"b": 1}}]) == {"a": {"b": 1}}


def test_empty_files_and_documents_add_nothing(tmp_path):
    first = tmp_path / "a.yaml"
    first.write_text("cni:\n  exclusive: false\n---\ncni:\n  logFile: ''\n")
    empty = tmp_path / "b.yaml"
    empty.write_text("")
    assert releases.load_values([first, empty]) == {"cni": {"exclusive": False, "logFile": ""}}


DEPLOYED = {"name": "cilium", "chart": "cilium", "version": "1.20.4", "revision": 1, "status": "deployed"}


@pytest.mark.parametrize(
    ("metadata", "deployed", "expected"),
    [
        (DEPLOYED, {"ipam": {"mode": "kubernetes"}}, True),
        (None, None, False),
        (DEPLOYED | {"status": "failed"}, {"ipam": {"mode": "kubernetes"}}, False),
        (DEPLOYED | {"version": "1.20.3"}, {"ipam": {"mode": "kubernetes"}}, False),
        (DEPLOYED, {"ipam": {"mode": "cluster-pool"}}, False),
        (DEPLOYED, None, False),
    ],
)
def test_a_release_is_unchanged_only_when_deployed_with_the_locked_chart_and_the_same_values(metadata, deployed, expected):
    assert releases.unchanged("cilium", "1.20.4", {"ipam": {"mode": "kubernetes"}}, metadata, deployed) is expected


MANIFEST = """---
# Source: local-cni/templates/a.yaml
apiVersion: v1
kind: ConfigMap
metadata: {name: config, namespace: kube-system}
data: {daemon-config.json: '{"dir": "/etc/cni/net.d"}'}
---
# Source: local-cni/templates/a.yaml
apiVersion: apps/v1
kind: DaemonSet
metadata: {name: ds, namespace: kube-system}
spec: {template: {spec: {containers: [{name: c, image: "r:t"}]}}}
"""
REORDERED = "---\n" + "\n---\n".join(reversed(MANIFEST.split("---\n")[1:])).replace("# Source: local-cni/templates/a.yaml\n", "")


@pytest.mark.parametrize(
    ("manifests", "expected"),
    [
        (None, True),
        ((MANIFEST, MANIFEST), True),
        ((MANIFEST, REORDERED), True),
        ((MANIFEST, MANIFEST.replace("/etc/cni/net.d", "/etc/cni/multus/net.d")), False),
        ((MANIFEST, MANIFEST + "---\napiVersion: v1\nkind: ServiceAccount\nmetadata: {name: sa}\n"), False),
        ((None, MANIFEST), False),
    ],
)
def test_a_local_chart_is_unchanged_only_when_the_deployed_objects_are_the_ones_it_renders_now(manifests, expected):
    values = {"image": {"tag": "t"}}
    assert releases.unchanged("local-cni", "v4.3.2", values, DEPLOYED | {"chart": "local-cni", "version": "v4.3.2"}, values,
                              manifests) is expected


def test_get_manifest_reads_the_applied_objects_of_the_release(tmp_path):
    cilium = releases.load(table(tmp_path, TABLE))[0]
    assert releases.get_manifest_argv("helm", cilium, Path("kc")) == [
        "helm", "get", "manifest", "cilium", "--namespace", "kube-system", "--kubeconfig", "kc"]


def test_get_reads_the_release_as_json_from_its_namespace(tmp_path):
    cilium = releases.load(table(tmp_path, TABLE))[0]
    argv = releases.get_argv("helm", "values", cilium, Path("kc"))
    assert argv[:4] == ["helm", "get", "values", "cilium"]
    assert argv[argv.index("--namespace") + 1] == "kube-system" and argv[argv.index("--output") + 1] == "json"


DIGEST = "sha256:" + "ab" * 32
SOURCES = """
releases:
  - {name: wa, namespace: kube-system, chart: "oci://registry.example/charts/wa-chart", version: cni.wa_chart,
     environments: [lab], values: [],
     images: [{lock: cni.wa_image, repository: image.repository, tag: image.tag}]}
  - {name: local, namespace: kube-system, chart: ./helm/local/chart, version: cni.local, environments: [lab], values: []}
"""
SOURCE_LOCK = {"components": {
    "cni.wa_chart": {"value": "0.9.4"},
    "cni.local": {"value": "v4.3.2"},
    "cni.wa_image": {"kind": "image-digest", "value": DIGEST, "ref": "registry.example:5000/team/wa:v0.9.4"},
}}


@pytest.fixture
def sources(tmp_path):
    root = tmp_path / "repo"
    chart = root / "helm" / "local" / "chart"
    chart.mkdir(parents=True)
    (chart / "Chart.yaml").write_text("apiVersion: v2\nname: local-cni\nversion: v4.3.2\n")
    return root, releases.load(table(tmp_path, SOURCES))


def test_an_oci_chart_is_pulled_at_the_locked_version_without_a_repository(sources):
    root, (wa, _) = sources
    argv = releases.template_argv("helm", wa, "lab", SOURCE_LOCK, root / "helm", "v1.36.6")
    assert argv[2:5] == ["wa", "oci://registry.example/charts/wa-chart", "--version"] and "--repo" not in argv
    assert releases.chart_name(wa, root) == "wa-chart"


def test_image_pins_come_from_the_lock_as_repository_and_tag_with_digest(sources):
    root, (wa, _) = sources
    argv = releases.apply_argv("helm", wa, "lab", SOURCE_LOCK, root / "helm", Path("kc"))
    pins = [argv[i + 1] for i, a in enumerate(argv) if a == "--set-string"]
    assert pins == ["image.repository=registry.example:5000/team/wa", f"image.tag=v0.9.4@{DIGEST}"]
    assert releases.desired_values(wa, "lab", root / "helm", SOURCE_LOCK) == {
        "image": {"repository": "registry.example:5000/team/wa", "tag": f"v0.9.4@{DIGEST}"}}


@pytest.mark.parametrize("component", [{"kind": "version", "value": "v1"}, {"kind": "image-digest", "value": None},
                                       {"kind": "image-digest", "value": DIGEST, "ref": "wa@sha256:x"}])
def test_an_image_pin_needs_a_pinned_digest_and_a_tagged_ref(sources, component):
    root, (wa, _) = sources
    lock = {"components": {**SOURCE_LOCK["components"], "cni.wa_image": component}}
    with pytest.raises(releases.ReleaseError, match="cni.wa_image"):
        releases.apply_argv("helm", wa, "lab", lock, root / "helm", Path("kc"))


def test_a_local_chart_renders_from_its_directory_under_the_repository(sources):
    root, (_, local) = sources
    argv = releases.template_argv("helm", local, "lab", SOURCE_LOCK, root / "helm", "v1.36.6")
    assert argv[2:4] == ["local", str(root / "helm/local/chart")] and "--repo" not in argv
    assert releases.chart_name(local, root) == "local-cni"


def test_only_a_local_chart_is_rendered_to_compare_with_its_deployed_manifest(tmp_path, sources, helm_dir):
    root, (wa, local) = sources
    cilium = releases.load(table(tmp_path, TABLE))[0]
    assert releases.manifest_argv("helm", cilium, "lab", LOCK, helm_dir, "v1.36.6") is None
    assert releases.manifest_argv("helm", wa, "lab", SOURCE_LOCK, root / "helm", "v1.36.6") is None
    argv = releases.manifest_argv("helm", local, "lab", SOURCE_LOCK, root / "helm", "v1.36.6")
    assert argv == releases.template_argv("helm", local, "lab", SOURCE_LOCK, root / "helm", "v1.36.6")[:-1] + ["--no-hooks"]
    assert "--include-crds" not in argv and "--kubeconfig" not in argv


def test_a_local_chart_whose_version_is_not_the_locked_one_is_refused(sources):
    root, (_, local) = sources
    lock = {"components": {**SOURCE_LOCK["components"], "cni.local": {"value": "v4.3.3"}}}
    with pytest.raises(releases.ReleaseError, match="has version v4.3.2, the lock pins cni.local v4.3.3"):
        releases.apply_argv("helm", local, "lab", lock, root / "helm", Path("kc"))


def test_a_chart_without_repo_oci_or_local_path_is_refused(tmp_path):
    with pytest.raises(releases.ReleaseError, match="needs a repo"):
        releases.load(table(tmp_path, SOURCES.replace("./helm/local/chart", "helm/local/chart")))
