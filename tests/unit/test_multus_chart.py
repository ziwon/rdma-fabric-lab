import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from addons import releases

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "helm" / "multus" / "chart"
UPSTREAM = CHART / "files" / "multus-daemonset-thick.yml.upstream"
HELM = ROOT / ".tools" / "bin" / "helm"
LOCK = yaml.safe_load((ROOT / "versions.lock.yaml").read_text())
SNAPSHOT = "ghcr.io/k8snetworkplumbingwg/multus-cni:snapshot-thick"
UPSTREAM_AUTOCONFIG = '"multusAutoconfigDir": "/host/etc/cni/net.d",'
CILIUM_CONF_DIR = yaml.safe_load((ROOT / "helm" / "cilium" / "values-lab.yaml").read_text())["cni"]["confPath"]
needs_helm = pytest.mark.skipif(not HELM.is_file(), reason="make tools installs the pinned helm into .tools/bin")


def release() -> releases.Release:
    return next(r for r in releases.load(ROOT / "helm" / "releases.yaml") if r.name == "multus")


def objects(text: str) -> list[dict]:
    return sorted((d for d in yaml.safe_load_all(text) if d), key=lambda d: (d["kind"], d["metadata"]["name"]))


def pinned_image() -> str:
    image = releases.image_values(release(), LOCK)["image"]
    return f"{image['repository']}:{image['tag']}"


def render_lab() -> list[dict]:
    argv = releases.template_argv(str(HELM), release(), "lab", LOCK, ROOT / "helm", LOCK["components"]["k8s.kubernetes"]["value"])
    return objects(subprocess.run(argv, capture_output=True, text=True, check=True).stdout)


def daemon_config(rendered: list[dict]) -> dict:
    config_map = next(d for d in rendered if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "multus-daemon-config")
    return json.loads(config_map["data"]["daemon-config.json"])


def test_the_chart_carries_the_pinned_upstream_manifest_byte_for_byte():
    assert "sha256:" + hashlib.sha256(UPSTREAM.read_bytes()).hexdigest() == LOCK["components"]["cni.multus_manifest"]["value"]
    assert UPSTREAM.read_text().count(SNAPSHOT) == 2
    assert UPSTREAM.read_text().count(UPSTREAM_AUTOCONFIG) == 1


def test_the_chart_version_is_the_locked_multus_version():
    assert releases.chart_argv(release(), ROOT, LOCK) == [str(CHART)]
    assert pinned_image().endswith("@" + LOCK["components"]["cni.multus_image"]["value"])


@needs_helm
def test_the_lab_render_is_upstream_with_only_the_image_and_the_delegate_directory_changed():
    lab_dir = f'"multusAutoconfigDir": "{CILIUM_CONF_DIR}",'
    assert render_lab() == objects(UPSTREAM.read_text().replace(SNAPSHOT, pinned_image()).replace(UPSTREAM_AUTOCONFIG, lab_dir))


@needs_helm
def test_the_chart_alone_keeps_the_upstream_delegate_directory():
    out = subprocess.run([str(HELM), "template", "multus", str(CHART), "--namespace", "kube-system",
                          "--set-string", f"image.repository={pinned_image().split(':')[0]}", "--set-string", "image.tag=t"],
                         capture_output=True, text=True, check=True).stdout
    assert objects(out) == objects(UPSTREAM.read_text().replace(SNAPSHOT, f"{pinned_image().split(':')[0]}:t"))


@needs_helm
def test_multus_reads_its_delegate_from_the_host_directory_cilium_writes_to_at_the_same_path():
    rendered = render_lab()
    daemon = next(d for d in rendered if d["kind"] == "DaemonSet")["spec"]["template"]["spec"]
    container = next(c for c in daemon["containers"] if c["name"] == "kube-multus")
    delegate_dir = daemon_config(rendered)["multusAutoconfigDir"]
    mounts = {m["mountPath"]: m["name"] for m in container["volumeMounts"]}
    volumes = {v["name"]: v.get("hostPath", {}).get("path") for v in daemon["volumes"]}
    assert volumes[mounts[delegate_dir]] == CILIUM_CONF_DIR == delegate_dir
    assert daemon_config(rendered)["cniConfigDir"] == "/host/etc/cni/net.d" and volumes[mounts["/host/etc/cni/net.d"]] == "/etc/cni/net.d"


def copied_chart(tmp_path: Path, old: str, new: str) -> Path:
    chart = tmp_path / "chart"
    shutil.copytree(CHART, chart)
    moved = chart / "files" / UPSTREAM.name
    moved.write_text(moved.read_text().replace(old, new, 1))
    return chart


def render_copy(chart: Path) -> subprocess.CompletedProcess:
    return subprocess.run([str(HELM), "template", "multus", str(chart), "--set-string", "image.repository=r",
                           "--set-string", "image.tag=t"], capture_output=True, text=True)


@needs_helm
def test_the_chart_refuses_an_upstream_manifest_that_moved_its_image(tmp_path):
    out = render_copy(copied_chart(tmp_path, SNAPSHOT, "ghcr.io/k8snetworkplumbingwg/multus-cni:v9-thick"))
    assert out.returncode != 0 and "must name" in out.stderr


@needs_helm
@pytest.mark.parametrize("changed", ['"multusAutoconfigDir": "/host/etc/cni/other.d",', '"multusAutoconfigDir":"/host/etc/cni/net.d",'])
def test_the_chart_refuses_an_upstream_daemon_config_without_the_exact_delegate_directory(tmp_path, changed):
    out = render_copy(copied_chart(tmp_path, UPSTREAM_AUTOCONFIG, changed))
    assert out.returncode != 0 and "must hold" in out.stderr
