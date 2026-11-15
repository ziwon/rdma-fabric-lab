"""Command line for the result contract and the version lock.

Exit codes of run-finalize follow the derived status: passed 0, failed 1,
blocked 2, not_run 3. Exit code 4 means the input broke a contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import manifest, versions

DEFAULT_RESULTS = manifest.REPO_ROOT / "results"
DEFAULT_LOCK = manifest.REPO_ROOT / "versions.lock.yaml"


def _cmd_run_init(args: argparse.Namespace) -> int:
    print(manifest.init(args.spec, args.results_root, args.run_id))
    return 0


def _cmd_run_check(args: argparse.Namespace) -> int:
    manifest.record_check(
        args.run_dir,
        args.id,
        args.status,
        exit_code=args.exit_code,
        failure_class=args.failure_class,
        evidence=args.evidence,
        detail=args.detail,
    )
    return 0


def _cmd_run_set(args: argparse.Namespace) -> int:
    manifest.merge_section(args.run_dir, args.section, json.loads(args.file.read_text()))
    return 0


def _cmd_run_input(args: argparse.Namespace) -> int:
    manifest.add_input_files(args.run_dir, args.files)
    return 0


def _cmd_run_finalize(args: argparse.Namespace) -> int:
    status = manifest.finalize(args.run_dir)["status"]
    found = manifest.problems(args.run_dir)
    for problem in found:
        print(f"{args.run_dir}: {problem}", file=sys.stderr)
    print(f"{args.run_dir}: {status}")
    return manifest.EXIT_INVALID if found else manifest.EXIT_CODE[status]


def _cmd_validate(args: argparse.Namespace) -> int:
    run_dirs = args.run_dirs or manifest.find_run_dirs(args.results_root, args.run_id)
    invalid = 0
    for run_dir in run_dirs:
        found = manifest.problems(run_dir)
        invalid += bool(found)
        for problem in found:
            print(f"{run_dir}: {problem}", file=sys.stderr)
    print(f"validated {len(run_dirs)} run directories, {invalid} invalid")
    return manifest.EXIT_INVALID if invalid else 0


def _cmd_specs(args: argparse.Namespace) -> int:
    invalid = 0
    for spec in args.specs:
        try:
            manifest.load_spec(spec)
        except manifest.ManifestError as exc:
            invalid += 1
            print(exc, file=sys.stderr)
    print(f"validated {len(args.specs)} scenario specs, {invalid} invalid")
    return manifest.EXIT_INVALID if invalid else 0


def _cmd_report(args: argparse.Namespace) -> int:
    rows = manifest.tally(args.results_root, args.run_id)
    invalid = [r for r in rows if not r["valid"]]
    for label, wanted in (("required (MVP)", True), ("optional", False)):
        group = [r for r in rows if r["valid"] and r["required"] is wanted]
        counts = {s: sum(r["status"] == s for r in group) for s in manifest.STATUS_SEVERITY}
        print(f"{label}: " + ", ".join(f"{s}={n}" for s, n in counts.items()))
        for row in group:
            print(f"  {row['status']:8} {row['result_kind']:28} {row['dir']}")
    print(f"invalid: {len(invalid)}")
    for row in invalid:
        print(f"  {row['dir']}: {'; '.join(row['problems'])}")
    return manifest.EXIT_INVALID if invalid else 0


def _cmd_versions(args: argparse.Namespace) -> int:
    lock = versions.load(args.lock)
    found = versions.problems(lock)
    for problem in found:
        print(f"{args.lock}: {problem}", file=sys.stderr)
    if found:
        return manifest.EXIT_INVALID
    pending = sorted(n for n, c in lock["components"].items() if c["value"] is None)
    print(f"{len(lock['components'])} components, {len(pending)} undecided")
    if args.environment:
        drift = versions.tooling_drift(lock, versions.observed_tooling())
        for problem in drift:
            print(f"{problem}. Rebuild with make venv.", file=sys.stderr)
        if drift:
            return 1
    if args.scope is None:
        return 0
    try:
        blocking = versions.undecided(lock, args.scope)
    except KeyError as exc:
        print(exc.args[0], file=sys.stderr)
        return manifest.EXIT_INVALID
    for name in blocking:
        print(f"scope {args.scope}: {name} is undecided. {lock['components'][name]['reason']}", file=sys.stderr)
    if blocking:
        return 1
    print(f"scope {args.scope}: every component is pinned")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rfl", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def results_options(p: argparse.ArgumentParser) -> None:
        p.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS)
        p.add_argument("--run-id")

    p = sub.add_parser("run-init", help="create a run directory from a scenario spec")
    p.add_argument("--spec", type=Path, required=True)
    results_options(p)
    p.set_defaults(func=_cmd_run_init)

    p = sub.add_parser("run-check", help="record the outcome of one declared check")
    p.add_argument("run_dir", type=Path)
    p.add_argument("--id", required=True)
    p.add_argument("--status", required=True, choices=manifest.STATUS_SEVERITY)
    p.add_argument("--exit-code", type=int)
    p.add_argument("--failure-class")
    p.add_argument("--evidence", action="append", default=[], help="file path relative to the run directory")
    p.add_argument("--detail")
    p.set_defaults(func=_cmd_run_check)

    p = sub.add_parser("run-set", help="merge a JSON object into a manifest section")
    p.add_argument("run_dir", type=Path)
    p.add_argument("--section", required=True, choices=manifest.MERGEABLE_SECTIONS)
    p.add_argument("--file", type=Path, required=True)
    p.set_defaults(func=_cmd_run_set)

    p = sub.add_parser("run-input", help="record sha256 checksums of input files")
    p.add_argument("run_dir", type=Path)
    p.add_argument("files", type=Path, nargs="+")
    p.set_defaults(func=_cmd_run_input)

    p = sub.add_parser("run-finalize", help="close the run and exit with its derived status")
    p.add_argument("run_dir", type=Path)
    p.set_defaults(func=_cmd_run_finalize)

    p = sub.add_parser("validate", help="check run directories against the result contract")
    p.add_argument("run_dirs", type=Path, nargs="*")
    results_options(p)
    p.set_defaults(func=_cmd_validate)

    p = sub.add_parser("specs", help="validate scenario spec files")
    p.add_argument("specs", type=Path, nargs="*")
    p.set_defaults(func=_cmd_specs)

    p = sub.add_parser("report", help="tally results, required and optional runs apart")
    results_options(p)
    p.set_defaults(func=_cmd_report)

    p = sub.add_parser("versions", help="validate the version lock")
    p.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    p.add_argument("--scope", help="fail when any component of this scope is undecided")
    p.add_argument("--environment", action="store_true", help="fail when the running tooling differs from the lock")
    p.set_defaults(func=_cmd_versions)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.func(args)
    except manifest.ManifestError as exc:
        print(exc, file=sys.stderr)
        return manifest.EXIT_INVALID


if __name__ == "__main__":
    sys.exit(main())
