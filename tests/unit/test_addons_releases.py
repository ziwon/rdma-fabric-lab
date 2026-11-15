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
def test_a_release_is_unchanged_only_when_deployed_with_the_locked_chart_and_the_same_values(tmp_path, metadata, deployed, expected):
    cilium = releases.load(table(tmp_path, TABLE))[0]
    assert releases.unchanged(cilium, "1.20.4", {"ipam": {"mode": "kubernetes"}}, metadata, deployed) is expected


def test_get_reads_the_release_as_json_from_its_namespace(tmp_path):
    cilium = releases.load(table(tmp_path, TABLE))[0]
    argv = releases.get_argv("helm", "values", cilium, Path("kc"))
    assert argv[:4] == ["helm", "get", "values", "cilium"]
    assert argv[argv.index("--namespace") + 1] == "kube-system" and argv[argv.index("--output") + 1] == "json"
