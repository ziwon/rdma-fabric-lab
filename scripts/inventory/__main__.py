"""Check one inventory against the contract of ADR 0001 section 4. Nothing connects to a host."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from addons import releases

from . import contract

REPO_ROOT = Path(__file__).resolve().parents[2]


def _ansible_inventory() -> str:
    beside = Path(sys.executable).parent / "ansible-inventory"
    return str(beside) if beside.exists() else (shutil.which("ansible-inventory") or "ansible-inventory")


def _merge(base: dict, over: dict) -> dict:
    merged = dict(base)
    for key, value in over.items():
        merged[key] = _merge(merged[key], value) if isinstance(value, dict) and isinstance(merged.get(key), dict) else value
    return merged


def cilium_values(table: list[releases.Release], helm_dir: Path, env: str) -> dict | None:
    """Merged values of the cilium chart in env, in the order helm applies them."""
    chosen = [r for r in releases.select(table, env) if r.chart == "cilium"]
    if not chosen:
        return None
    merged: dict = {}
    for path in releases.values_files(chosen[0], env, helm_dir):
        merged = _merge(merged, yaml.safe_load(path.read_text()) or {})
    return merged


def _cmd_check(args: argparse.Namespace) -> int:
    if args.json:
        listed = json.loads(args.json.read_text())
        source = str(args.json)
    else:
        argv = [_ansible_inventory(), *(x for i in args.inventory for x in ("-i", str(i))), "--list"]
        listed = json.loads(subprocess.run(argv, check=True, capture_output=True, text=True).stdout)
        source = " + ".join(map(str, args.inventory))
    table = releases.load(args.releases)
    environments = {e for r in table for e in r.environments}
    missing_values: list[contract.Violation] = []

    def values(env: str) -> dict | None:
        try:
            return cilium_values(table, args.helm_dir, env)
        except releases.ReleaseError as exc:
            missing_values.append(contract.Violation("cilium-values", str(exc)))
            return None

    found = contract.problems(listed, environments, values, explicit=args.explicit) + missing_values
    for violation in found:
        print(f"{source}: {violation}", file=sys.stderr)
    hosts = contract.members(listed, "k8s_cluster")
    print(f"{source}: contract {'broken' if found else 'holds'}, {len(found)} violations over {len(hosts)} hosts"
          + (", every variable explicit" if args.explicit else ""))
    return 1 if found else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="inventory", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check", help="Check ansible-inventory --list of one inventory against the contract")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("-i", "--inventory", type=Path, action="append", help="Inventory path, repeat to layer a second one")
    source.add_argument("--json", type=Path, help="Saved ansible-inventory --list output instead of running it")
    p.add_argument("--explicit", action="store_true", help="Every contract variable must be set on every host")
    p.add_argument("--releases", type=Path, default=REPO_ROOT / "helm" / "releases.yaml")
    p.add_argument("--helm-dir", type=Path, default=REPO_ROOT / "helm")
    p.set_defaults(func=_cmd_check)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
