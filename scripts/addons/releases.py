"""helm/releases.yaml: the release table and the helm command line for each release."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

TIMEOUT = "10m"


class ReleaseError(Exception):
    pass


@dataclass(frozen=True)
class Release:
    name: str
    namespace: str
    chart: str
    repo: str
    version: str
    environments: tuple[str, ...]
    values: tuple[str, ...]


def load(path: Path) -> list[Release]:
    raw = yaml.safe_load(path.read_text())["releases"]
    releases = [
        Release(
            name=r["name"],
            namespace=r["namespace"],
            chart=r["chart"],
            repo=r["repo"],
            version=r["version"],
            environments=tuple(r["environments"]),
            values=tuple(r["values"]),
        )
        for r in raw
    ]
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


def unchanged(release: Release, chart_version: str, desired: dict, metadata: dict | None, deployed: dict | None) -> bool:
    """True when the deployed release already runs this chart version with exactly these user values."""
    return (
        metadata is not None
        and metadata.get("status") == "deployed"
        and metadata.get("chart") == release.chart
        and metadata.get("version") == chart_version
        and (deployed or {}) == desired
    )


def get_argv(helm: str, what: str, release: Release, kubeconfig: Path) -> list[str]:
    """helm get <metadata|values> of the release as JSON."""
    return [helm, "get", what, release.name, "--namespace", release.namespace, "--output", "json", "--kubeconfig", str(kubeconfig)]


def _common(release: Release, env: str, lock: dict, helm_dir: Path) -> list[str]:
    argv = [release.name, release.chart, "--repo", release.repo, "--version", chart_version(release, lock)]
    argv += ["--namespace", release.namespace]
    for f in values_files(release, env, helm_dir):
        argv += ["--values", str(f)]
    return argv


def apply_argv(helm: str, release: Release, env: str, lock: dict, helm_dir: Path, kubeconfig: Path) -> list[str]:
    return [
        helm, "upgrade", "--install", *_common(release, env, lock, helm_dir),
        "--create-namespace", "--wait", "--timeout", TIMEOUT, "--kubeconfig", str(kubeconfig),
    ]


def template_argv(helm: str, release: Release, env: str, lock: dict, helm_dir: Path, kube_version: str) -> list[str]:
    return [helm, "template", *_common(release, env, lock, helm_dir), "--kube-version", kube_version, "--include-crds"]
