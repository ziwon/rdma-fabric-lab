"""Offline checks for the GPU fleet: site model, rendered inventory and values keys.
Nothing here connects to a host."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import yaml

from addons import releases

from . import render, site, values_keys



def _load_site(path: Path) -> site.Site | None:
    try:
        return site.load(path)
    except site.SiteError as exc:
        print(f"{path}: {exc}", file=sys.stderr)
        return None


def _cmd_site_check(args: argparse.Namespace) -> int:
    model = _load_site(args.site)
    if model is None:
        return 1
    lab_plan = None
    if args.lab_plan:
        merged: dict = {}
        for path in sorted(args.lab_plan.glob("*.yml")):
            merged.update(yaml.safe_load(path.read_text()) or {})
        lab_plan = site.lab_plan(merged)
    found = site.problems(model, lab_plan)
    for problem in found:
        print(f"{args.site}: {problem}", file=sys.stderr)
    links = sum(len(f.links) for f in model.fabrics.values())
    print(
        f"{args.site}: {'invalid' if found else 'valid'}, {len(model.hosts)} hosts, {len(model.networks)} networks, {links} fabric links, "
        f"checked against {'the lab plan' if lab_plan else 'no lab plan'}"
    )
    return 1 if found else 0


def _cmd_render(args: argparse.Namespace) -> int:
    model = _load_site(args.site)
    if model is None:
        return 1
    found = site.problems(model)
    if found:
        for problem in found:
            print(f"{args.site}: {problem}", file=sys.stderr)
        print(f"{args.site}: invalid, nothing rendered", file=sys.stderr)
        return 1
    files = render.render(model)
    if args.check:
        differences = render.drift(files, args.out)
        for difference in differences:
            print(difference, file=sys.stderr)
        print(f"{args.out}: {len(differences)} differences from {args.site} over {len(files)} rendered files")
        return 1 if differences else 0
    foreign = render.write(files, args.out)
    for path in foreign:
        print(f"{path}: not rendered from the site model, left in place", file=sys.stderr)
    print(f"{args.out}: wrote {len(files)} files from {args.site}")
    return 1 if foreign else 0


def _chart_defaults(helm: str, release: releases.Release, lock: dict) -> dict:
    argv = [helm, "show", "values", release.chart, "--repo", release.repo, "--version", releases.chart_version(release, lock)]
    return yaml.safe_load(subprocess.run(argv, check=True, capture_output=True, text=True).stdout) or {}


def _cmd_values_keys(args: argparse.Namespace) -> int:
    lock = yaml.safe_load(args.lock.read_text())
    found = []
    checked = 0
    try:
        chosen = releases.select(releases.load(args.helm_dir / "releases.yaml"), args.env)
        for release in chosen:
            defaults = _chart_defaults(str(args.helm), release, lock)
            for path in releases.values_files(release, args.env, args.helm_dir):
                checked += 1
                found += [f"{path}: {p}" for p in values_keys.unknown_keys(yaml.safe_load(path.read_text()) or {}, defaults)]
                print(f"{path}: checked against {release.chart} {releases.chart_version(release, lock)} values.yaml")
    except releases.ReleaseError as exc:
        print(exc, file=sys.stderr)
        return 2
    for problem in found:
        print(problem, file=sys.stderr)
    print(f"{args.env}: {checked} values files, {len(found)} unknown keys")
    return 1 if found else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fleet")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("site-check", help="Validate a site model: names, address plan and compute fabric cabling")
    p.add_argument("site", type=Path)
    p.add_argument("--lab-plan", type=Path, help="Lab group_vars/all directory, whose address ranges no site range may overlap")
    p.set_defaults(func=_cmd_site_check)

    p = sub.add_parser("render", help="Write the Ansible inventory of a site model, or with --check compare it")
    p.add_argument("site", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--check", action="store_true", help="Change nothing, exit 1 when the directory differs")
    p.set_defaults(func=_cmd_render)

    p = sub.add_parser("values-keys", help="Every key of an environment's values files must exist in the chart's values.yaml")
    p.add_argument("--env", required=True)
    p.add_argument("--helm", type=Path, required=True, help="helm binary that fetches the chart at its locked version")
    p.add_argument("--helm-dir", type=Path, default=Path("helm"))
    p.add_argument("--lock", type=Path, default=Path("versions.lock.yaml"))
    p.set_defaults(func=_cmd_values_keys)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
