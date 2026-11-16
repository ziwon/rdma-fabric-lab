"""Install or render the Helm releases of one environment.

apply runs helm upgrade --install for each release in table order, unless the deployed
release already has the locked chart version and the same user values. template renders
each release for the locked Kubernetes version and validates the result with kubeconform,
without a cluster.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

from . import releases

REPO_ROOT = Path(__file__).resolve().parents[2]
HELM_DIR = REPO_ROOT / "helm"
TOOLS = REPO_ROOT / ".tools" / "bin"


def _lock() -> dict:
    return yaml.safe_load((REPO_ROOT / "versions.lock.yaml").read_text())


def _helm_json(argv: list[str]) -> dict | None:
    """Parsed JSON of a helm get command, or None when it fails, as it does for a release that does not exist."""
    out = subprocess.run(argv, capture_output=True, text=True)
    return json.loads(out.stdout or "null") if out.returncode == 0 else None


def _cmd_apply(args: argparse.Namespace) -> int:
    lock = _lock()
    helm = str(TOOLS / "helm")
    for release in releases.select(releases.load(HELM_DIR / "releases.yaml"), args.env, args.release):
        version = releases.chart_version(release, lock)
        desired = releases.desired_values(release, args.env, HELM_DIR, lock)
        metadata = _helm_json(releases.get_argv(helm, "metadata", release, args.kubeconfig))
        deployed = _helm_json(releases.get_argv(helm, "values", release, args.kubeconfig)) if metadata else None
        chart = releases.chart_name(release, REPO_ROOT)
        if not args.force and releases.unchanged(chart, version, desired, metadata, deployed):
            print(f"{release.name}: unchanged, chart {version} at revision {metadata['revision']}", flush=True)
            continue
        argv = releases.apply_argv(helm, release, args.env, lock, HELM_DIR, args.kubeconfig)
        print("+", " ".join(argv), flush=True)
        subprocess.run(argv, check=True)
        after = _helm_json(releases.get_argv(helm, "metadata", release, args.kubeconfig)) or {}
        print(f"{release.name}: upgraded, chart {version} at revision {after.get('revision')}"
              f"{' (forced)' if args.force else ''}", flush=True)
    return 0


def _cmd_template(args: argparse.Namespace) -> int:
    lock = _lock()
    kube_version = lock["components"]["k8s.kubernetes"]["value"]
    args.out.mkdir(parents=True, exist_ok=True)
    for release in releases.select(releases.load(HELM_DIR / "releases.yaml"), args.env, args.release):
        rendered = args.out / f"{args.env}-{release.name}.yaml"
        argv = releases.template_argv(str(TOOLS / "helm"), release, args.env, lock, HELM_DIR, kube_version)
        print("+", " ".join(argv), flush=True)
        out = subprocess.run(argv, capture_output=True, text=True)
        if out.returncode != 0:
            print(out.stderr, end="", file=sys.stderr)
            print(f"{release.name}: helm template exited {out.returncode}", file=sys.stderr)
            return out.returncode
        rendered.write_text(out.stdout)
        check = [
            str(TOOLS / "kubeconform"), "-strict", "-summary", "-ignore-missing-schemas",
            "-kubernetes-version", kube_version.lstrip("v"), str(rendered),
        ]
        print("+", " ".join(check), flush=True)
        subprocess.run(check, check=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="addons", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("apply", help="helm upgrade --install every release of the environment")
    p.add_argument("--env", required=True)
    p.add_argument("--kubeconfig", type=Path, required=True)
    p.add_argument("--release")
    p.add_argument("--force", action="store_true",
                   help="upgrade even when chart version and values are unchanged, so Helm repairs drifted objects")
    p.set_defaults(func=_cmd_apply)
    p = sub.add_parser("template", help="render and validate every release of the environment offline")
    p.add_argument("--env", required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--release")
    p.set_defaults(func=_cmd_template)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except releases.ReleaseError as exc:
        print(exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
