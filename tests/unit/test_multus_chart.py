import hashlib
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
needs_helm = pytest.mark.skipif(not HELM.is_file(), reason="make tools installs the pinned helm into .tools/bin")


def release() -> releases.Release:
    return next(r for r in releases.load(ROOT / "helm" / "releases.yaml") if r.name == "multus")


def objects(text: str) -> list[dict]:
    return sorted((d for d in yaml.safe_load_all(text) if d), key=lambda d: (d["kind"], d["metadata"]["name"]))


def pinned_image() -> str:
    image = releases.image_values(release(), LOCK)["image"]
    return f"{image['repository']}:{image['tag']}"


def test_the_chart_carries_the_pinned_upstream_manifest_byte_for_byte():
    assert "sha256:" + hashlib.sha256(UPSTREAM.read_bytes()).hexdigest() == LOCK["components"]["cni.multus_manifest"]["value"]
    assert UPSTREAM.read_text().count(SNAPSHOT) == 2


def test_the_chart_version_is_the_locked_multus_version():
    assert releases.chart_argv(release(), ROOT, LOCK) == [str(CHART)]
    assert pinned_image().endswith("@" + LOCK["components"]["cni.multus_image"]["value"])


@needs_helm
def test_the_chart_renders_the_upstream_objects_with_only_the_image_pinned():
    argv = releases.template_argv(str(HELM), release(), "lab", LOCK, ROOT / "helm", LOCK["components"]["k8s.kubernetes"]["value"])
    rendered = subprocess.run(argv, capture_output=True, text=True, check=True).stdout
    assert objects(rendered) == objects(UPSTREAM.read_text().replace(SNAPSHOT, pinned_image()))


@needs_helm
def test_the_chart_refuses_an_upstream_manifest_that_moved_its_image(tmp_path):
    chart = tmp_path / "chart"
    shutil.copytree(CHART, chart)
    moved = chart / "files" / UPSTREAM.name
    moved.write_text(moved.read_text().replace(SNAPSHOT, "ghcr.io/k8snetworkplumbingwg/multus-cni:v9-thick", 1))
    out = subprocess.run([str(HELM), "template", "multus", str(chart), "--set-string", "image.repository=r",
                          "--set-string", "image.tag=t"], capture_output=True, text=True)
    assert out.returncode != 0 and "must name" in out.stderr
