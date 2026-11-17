"""Command line for the result contract and the version lock.

Exit codes of run-finalize follow the derived status: passed 0, failed 1,
blocked 2, not_run 3. Exit code 4 means the input broke a contract.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import manifest, versions

DEFAULT_RESULTS = manifest.REPO_ROOT / "results"
DEFAULT_LOCK = manifest.REPO_ROOT / "versions.lock.yaml"


def _cmd_run_init(args: argparse.Namespace) -> int:
    if bool(args.break_name) != bool(args.break_what):
        raise manifest.ManifestError("a deliberate break needs both --break and --break-what")
    broken = {"name": args.break_name, "description": args.break_what} if args.break_name else None
    print(manifest.init(args.spec, args.results_root, args.run_id, broken))
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


REPORT_STATUSES = ("passed", "failed", "blocked", "not_run", "never ran")


def _report_status(row: dict) -> str:
    return row["latest"]["status"] if row["latest"] else "never ran"


def _cmd_report(args: argparse.Namespace) -> int:
    rows, invalid = manifest.latest_runs(args.results_root, args.run_id, args.specs or None)
    print("Latest finished run of each scenario. Deliberate breaks, unfinished runs and older runs are not counted.")
    for kind in manifest.RESULT_KINDS:
        of_kind = [r for r in rows if r["result_kind"] == kind]
        print(f"\n{kind}")
        for label, wanted in (("required", True), ("optional", False)):
            group = [r for r in of_kind if r["required"] is wanted]
            counts = ", ".join(f"{s} {sum(_report_status(r) == s for r in group)}" for s in REPORT_STATUSES)
            print(f"  {label}: {counts}")
            for r in group:
                run = r["latest"]["run_id"] if r["latest"] else "-"
                print(f"    {_report_status(r):9} {r['track'] + '/' + r['scenario']:40} {run:16}"
                      f"  older {r['older']}, breaks {r['breaks']}, unfinished {r['unfinished']}")
    print()
    for label, wanted in (("required (MVP)", True), ("optional", False)):
        group = [r for r in rows if r["required"] is wanted]
        print(f"{label}: {sum(_report_status(r) == 'passed' for r in group)} of {len(group)} scenarios passed")
    print(f"not counted: {sum(r['breaks'] for r in rows)} deliberate break runs, "
          f"{sum(r['unfinished'] for r in rows)} unfinished runs, {sum(r['older'] for r in rows)} older runs")
    print(f"invalid: {len(invalid)}")
    for row in invalid:
        print(f"  {row['dir']}: {'; '.join(row['problems'])}")
    if invalid:
        return manifest.EXIT_INVALID
    unmet = [r for r in rows if r["required"] and _report_status(r) != "passed"]
    return 1 if args.strict and unmet else 0


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
    # A wrapper that breaks the environment before it calls a runner names the break in these variables.
    p.add_argument("--break", dest="break_name", default=os.environ.get("RFL_BREAK"),
                   help="name of the deliberate break this run carries, default $RFL_BREAK")
    p.add_argument("--break-what", default=os.environ.get("RFL_BREAK_WHAT"),
                   help="what the break does, default $RFL_BREAK_WHAT")
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

    p = sub.add_parser("report", help="latest finished run of each scenario that is not a deliberate break")
    results_options(p)
    p.add_argument("--strict", action="store_true", help="exit 1 unless every required scenario passed")
    p.add_argument("--specs", type=Path, nargs="*", help="scenario specs to report on, default tracks/*/scenarios/*.yaml")
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
