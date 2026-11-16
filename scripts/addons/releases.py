"""helm/releases.yaml: the release table and the helm command line for each release."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

TIMEOUT = "10m"
OCI = "oci://"
LOCAL = "./"
# repository[:tag] of an image-digest lock ref, as helm --set-string can carry it.
IMAGE_REF = re.compile(r"^(?P<repository>[a-z0-9.-]+(?::[0-9]+)?(?:/[a-z0-9._-]+)+):(?P<tag>[A-Za-z0-9._-]+)$")


class ReleaseError(Exception):
    pass


@dataclass(frozen=True)
class Image:
    """A chart image taken from an image-digest lock component: its ref names repository and tag, its value the digest."""

    lock: str
    repository: str
    tag: str


@dataclass(frozen=True)
class Release:
    name: str
    namespace: str
    chart: str
    repo: str
    version: str
    environments: tuple[str, ...]
    values: tuple[str, ...]
    images: tuple[Image, ...] = ()

    @property
    def source(self) -> str:
        """repo when a chart repository serves the chart, oci for an oci:// reference, local for a ./ path."""
        if self.repo:
            return "repo"
        return "oci" if self.chart.startswith(OCI) else "local"


def load(path: Path) -> list[Release]:
    raw = yaml.safe_load(path.read_text())["releases"]
    releases = [
        Release(
            name=r["name"],
            namespace=r["namespace"],
            chart=r["chart"],
            repo=r.get("repo", ""),
            version=r["version"],
            environments=tuple(r["environments"]),
            values=tuple(r["values"]),
            images=tuple(Image(i["lock"], i["repository"], i["tag"]) for i in r.get("images", [])),
        )
        for r in raw
    ]
    for r in releases:
        if r.source == "local" and not r.chart.startswith(LOCAL):
            raise ReleaseError(f"{path}: {r.name}: chart {r.chart} needs a repo, an {OCI} reference or a {LOCAL} path")
    names = [r.name for r in releases]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise ReleaseError(f"{path}: duplicate release {', '.join(duplicates)}")
    return releases


def select(releases: list[Release], env: str, only: str | None = None) -> list[Release]:
    chosen = [r for r in releases if env in r.environments and (only is None or r.name == only)]
    if only is not None and not chosen:
        raise ReleaseError(f"release {only} is not installed in environment {env}")
    return chosen


def chart_version(release: Release, lock: dict) -> str:
    component = lock["components"].get(release.version)
    if component is None:
        raise ReleaseError(f"{release.name}: {release.version} is not a lock component")
    if component["value"] is None:
        raise ReleaseError(f"{release.name}: {release.version} is undecided in the lock")
    return component["value"]


def local_chart(release: Release, root: Path, lock: dict) -> Path:
    """The chart directory of a repo-local release. Its Chart.yaml version must be the locked one."""
    path = root / release.chart
    meta = path / "Chart.yaml"
    if not meta.is_file():
        raise ReleaseError(f"{release.name}: no chart at {path}")
    declared = str(yaml.safe_load(meta.read_text()).get("version"))
    if declared != chart_version(release, lock):
        raise ReleaseError(f"{release.name}: {meta} has version {declared}, the lock pins {release.version} "
                           f"{chart_version(release, lock)}")
    return path


def chart_name(release: Release, root: Path) -> str:
    """The chart name helm get metadata reports for the release."""
    if release.source == "repo":
        return release.chart
    if release.source == "oci":
        return release.chart.rstrip("/").rsplit("/", 1)[-1]
    return str(yaml.safe_load((root / release.chart / "Chart.yaml").read_text())["name"])


def chart_argv(release: Release, root: Path, lock: dict) -> list[str]:
    """The chart of the release as helm takes it, with the locked version where helm resolves one."""
    if release.source == "repo":
        return [release.chart, "--repo", release.repo, "--version", chart_version(release, lock)]
    if release.source == "oci":
        return [release.chart, "--version", chart_version(release, lock)]
    return [str(local_chart(release, root, lock))]


def image_values(release: Release, lock: dict) -> dict:
    """The user values that pin each image of the release by tag and digest, from the lock."""
    out: dict = {}
    for image in release.images:
        component = lock["components"].get(image.lock)
        if component is None or component.get("kind") != "image-digest" or not component.get("value"):
            raise ReleaseError(f"{release.name}: {image.lock} is not a pinned image-digest lock component")
        ref = IMAGE_REF.match(component.get("ref", ""))
        if ref is None:
            raise ReleaseError(f"{release.name}: {image.lock} ref {component.get('ref')!r} is not repository:tag")
        for path, value in ((image.repository, ref["repository"]), (image.tag, f"{ref['tag']}@{component['value']}")):
            _set_path(out, path, value)
    return out


def _set_path(tree: dict, path: str, value: str) -> None:
    *parents, leaf = path.split(".")
    for key in parents:
        tree = tree.setdefault(key, {})
    tree[leaf] = value


def _flatten(tree: dict, prefix: str = "") -> list[tuple[str, str]]:
    out = []
    for key, value in tree.items():
        out += _flatten(value, f"{prefix}{key}.") if isinstance(value, dict) else [(f"{prefix}{key}", value)]
    return out


def desired_values(release: Release, env: str, helm_dir: Path, lock: dict) -> dict:
    """The user values helm stores for the release: its values files, then the image pins from the lock."""
    return merge_values([load_values(values_files(release, env, helm_dir)), image_values(release, lock)])


def values_files(release: Release, env: str, helm_dir: Path) -> list[Path]:
    files = [helm_dir / v.format(env=env) for v in release.values]
    missing = [str(f) for f in files if not f.is_file()]
    if missing:
        raise ReleaseError(f"{release.name}: missing values file {', '.join(missing)}")
    return files


def merge_values(documents: list[dict | None]) -> dict:
    """The user values Helm stores for several --values files, as MergeMaps in pkg/chart/v2/loader does.

    Maps merge key by key, recursively. Any other later value, a list included, replaces the earlier
    one. A later null stays in the stored values as null, and only removes the chart default when
    Helm coalesces at render time, so `helm get values` shows it.
    """
    out: dict = {}
    for document in documents:
        out = _merge_maps(out, document or {})
    return out


def _merge_maps(a: dict, b: dict) -> dict:
    out = dict(a)
    for key, value in b.items():
        out[key] = _merge_maps(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def load_values(files: list[Path]) -> dict:
    """Merged user values of the files in order. A file may hold several YAML documents, merged in order too."""
    return merge_values([doc for f in files for doc in yaml.safe_load_all(f.read_text())])


def unchanged(chart: str, chart_version: str, desired: dict, metadata: dict | None, deployed: dict | None) -> bool:
    """True when the deployed release already runs this chart at this version with exactly these user values."""
    return (
        metadata is not None
        and metadata.get("status") == "deployed"
        and metadata.get("chart") == chart
        and metadata.get("version") == chart_version
        and (deployed or {}) == desired
    )


def get_argv(helm: str, what: str, release: Release, kubeconfig: Path) -> list[str]:
    """helm get <metadata|values> of the release as JSON."""
    return [helm, "get", what, release.name, "--namespace", release.namespace, "--output", "json", "--kubeconfig", str(kubeconfig)]


def _common(release: Release, env: str, lock: dict, helm_dir: Path) -> list[str]:
    argv = [release.name, *chart_argv(release, helm_dir.parent, lock), "--namespace", release.namespace]
    for f in values_files(release, env, helm_dir):
        argv += ["--values", str(f)]
    for path, value in _flatten(image_values(release, lock)):
        argv += ["--set-string", f"{path}={value}"]
    return argv


def apply_argv(helm: str, release: Release, env: str, lock: dict, helm_dir: Path, kubeconfig: Path) -> list[str]:
    return [
        helm, "upgrade", "--install", *_common(release, env, lock, helm_dir),
        "--create-namespace", "--wait", "--timeout", TIMEOUT, "--kubeconfig", str(kubeconfig),
    ]


def template_argv(helm: str, release: Release, env: str, lock: dict, helm_dir: Path, kube_version: str) -> list[str]:
    return [helm, "template", *_common(release, env, lock, helm_dir), "--kube-version", kube_version, "--include-crds"]
