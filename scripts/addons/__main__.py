"""Install or render the Helm releases of one environment.

apply runs helm upgrade --install for each release in table order, unless the deployed
release already has the locked chart version and the same user values, and for a repo-local
chart also the objects its chart renders now. template renders
each release for the locked Kubernetes version and validates the result with kubeconform,
without a cluster. Custom resources are checked against strict schemas converted from the CRDs
that the environment's releases render, and one that finds no schema fails.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

from . import releases, schemas

REPO_ROOT = Path(__file__).resolve().parents[2]
HELM_DIR = REPO_ROOT / "helm"
TOOLS = REPO_ROOT / ".tools" / "bin"


def _lock() -> dict:
    return yaml.safe_load((REPO_ROOT / "versions.lock.yaml").read_text())


def _helm_json(argv: list[str]) -> dict | None:
    """Parsed JSON of a helm get command, or None when it fails, as it does for a release that does not exist."""
    out = subprocess.run(argv, capture_output=True, text=True)
    return json.loads(out.stdout or "null") if out.returncode == 0 else None


def _manifests(helm: str, release: releases.Release, env: str, lock: dict, kubeconfig: Path) -> tuple[str | None, str] | None:
    """The deployed and the freshly rendered manifest of a repo-local release, None for any other release."""
    render = releases.manifest_argv(helm, release, env, lock, HELM_DIR, lock["components"]["k8s.kubernetes"]["value"])
    if render is None:
        return None
    deployed = subprocess.run(releases.get_manifest_argv(helm, release, kubeconfig), capture_output=True, text=True)
    return (deployed.stdout if deployed.returncode == 0 else None,
            subprocess.run(render, capture_output=True, text=True, check=True).stdout)


def _cmd_apply(args: argparse.Namespace) -> int:
    lock = _lock()
    helm = str(TOOLS / "helm")
    for release in releases.select(releases.load(HELM_DIR / "releases.yaml"), args.env, args.release):
        version = releases.chart_version(release, lock)
        desired = releases.desired_values(release, args.env, HELM_DIR, lock)
        metadata = _helm_json(releases.get_argv(helm, "metadata", release, args.kubeconfig))
        deployed = _helm_json(releases.get_argv(helm, "values", release, args.kubeconfig)) if metadata else None
        manifests = _manifests(helm, release, args.env, lock, args.kubeconfig) if metadata else None
        chart = releases.chart_name(release, REPO_ROOT)
        if not args.force and releases.unchanged(chart, version, desired, metadata, deployed, manifests):
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
    rendered: dict[str, Path] = {}
    for release in releases.select(releases.load(HELM_DIR / "releases.yaml"), args.env):
        argv = releases.template_argv(str(TOOLS / "helm"), release, args.env, lock, HELM_DIR, kube_version)
        print("+", " ".join(argv), flush=True)
        out = subprocess.run(argv, capture_output=True, text=True)
        if out.returncode != 0:
            print(out.stderr, end="", file=sys.stderr)
            print(f"{release.name}: helm template exited {out.returncode}", file=sys.stderr)
            return out.returncode
        rendered[release.name] = args.out / f"{args.env}-{release.name}.yaml"
        rendered[release.name].write_text(out.stdout)

    # Every release of the environment contributes its CRDs, so a release is checked against the CRDs that another installs.
    definitions = [crd for path in rendered.values() for crd in schemas.crds(path.read_text())]
    schema_dir = args.out / "schemas" / args.env
    written = schemas.write(definitions, schema_dir)
    print(f"{args.env}: {len(written)} schemas from {len(definitions)} CRDs in {schema_dir}", flush=True)
    custom = schemas.kinds(definitions)
    failed = False
    for name, path in rendered.items():
        if args.release and name != args.release:
            continue
        check = [
            str(TOOLS / "kubeconform"), "-strict", "-output", "json", "-verbose", "-ignore-missing-schemas",
            "-kubernetes-version", kube_version.lstrip("v"),
            "-schema-location", "default", "-schema-location", str(schema_dir / schemas.LOCATION), str(path),
        ]
        print("+", " ".join(check), flush=True)
        out = subprocess.run(check, capture_output=True, text=True)
        try:
            results = [r for r in json.loads(out.stdout or "{}").get("resources", []) if r.get("kind")]
        except json.JSONDecodeError:
            print(out.stdout + out.stderr, file=sys.stderr)
            print(f"{name}: kubeconform exited {out.returncode} without a JSON report", file=sys.stderr)
            return 2
        # A document holding only comments or nothing comes back with no kind and no status. It is no resource.
        counts, problems = schemas.summary(results, custom)
        skipped = sorted({r["kind"] for r in results if r["status"] == "statusSkipped"})
        print(f"{name}: {len(results)} resources, {counts['valid']} valid, {counts['invalid']} invalid, {counts['error']} errors, "
              f"{counts['skipped']} skipped{' (' + ', '.join(skipped) + ')' if skipped else ''}", flush=True)
        for problem in problems:
            print(f"{name}: {problem}", file=sys.stderr)
        failed = failed or bool(problems) or out.returncode not in (0, 1)
    return 1 if failed else 0


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
