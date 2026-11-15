"""Run manifest for results/<run-id>/<track>/<scenario>/.

A run starts from a scenario spec that declares every check. Each check is
recorded with its evidence, and the overall status is derived from the checks.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import platform
import subprocess
from pathlib import Path

import jsonschema
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO_ROOT / "schemas"

# Worst first. The overall status is the first of these that any check holds.
STATUS_SEVERITY = ("failed", "blocked", "not_run", "passed")
EXIT_CODE = {"passed": 0, "failed": 1, "blocked": 2, "not_run": 3}
EXIT_INVALID = 4

FILES_REQUIRED_WHEN_PASSED = ("commands.log", "resolved-config.yaml")


class ManifestError(Exception):
    pass


def load_schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text())


def schema_errors(instance: object, schema_name: str) -> list[str]:
    validator = jsonschema.Draft202012Validator(
        load_schema(schema_name), format_checker=jsonschema.FormatChecker()
    )
    return [
        f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    ]


def derive_status(check_statuses: list[str]) -> str:
    for status in STATUS_SEVERITY:
        if status in check_statuses:
            return status
    return "not_run"


def new_run_id(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout


def _repo_state() -> dict:
    commit = _git("rev-parse", "HEAD")
    porcelain = _git("status", "--porcelain")
    return {
        "commit": commit.strip() if commit else None,
        "dirty": bool(porcelain.strip()) if porcelain is not None else None,
    }


def _host() -> dict:
    os_name = platform.system()
    os_release = Path("/etc/os-release")
    if os_release.is_file():
        for line in os_release.read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                os_name = line.split("=", 1)[1].strip().strip('"')
    return {"os": os_name, "kernel": platform.release()}


def load_spec(spec_path: Path) -> dict:
    spec = yaml.safe_load(spec_path.read_text())
    errors = schema_errors(spec, "scenario.schema.json")
    ids = [c["id"] for c in spec.get("checks", [])] if isinstance(spec, dict) else []
    errors += [f"checks: duplicate id {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
    if errors:
        raise ManifestError(f"{spec_path}: " + "; ".join(errors))
    return spec


def manifest_path(run_dir: Path) -> Path:
    return run_dir / "manifest.json"


def read(run_dir: Path) -> dict:
    path = manifest_path(run_dir)
    if not path.is_file():
        raise ManifestError(f"{path}: no manifest. Start the run with run-init.")
    return json.loads(path.read_text())


def write(run_dir: Path, manifest: dict) -> None:
    manifest["status"] = derive_status([c["status"] for c in manifest["checks"]])
    path = manifest_path(run_dir)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def init(spec_path: Path, results_root: Path, run_id: str | None = None) -> Path:
    spec = load_spec(spec_path)
    run_id = run_id or new_run_id()
    run_dir = results_root / run_id / spec["track"] / spec["scenario"]
    if manifest_path(run_dir).exists():
        raise ManifestError(f"{run_dir}: this run already has a manifest")
    (run_dir / "raw").mkdir(parents=True)
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "track": spec["track"],
        "scenario": spec["scenario"],
        "description": spec["description"],
        "required": spec["required"],
        "result_kind": spec["result_kind"],
        "status": "not_run",
        "created_at": _utc_now(),
        "finished_at": None,
        "repo": _repo_state(),
        "environment": {"host": _host()},
        "inputs": {"files": [], "seed": None, "parameters": {}},
        "commands_log": "commands.log",
        "checks": [
            {
                "id": c["id"],
                "description": c["description"],
                "status": "not_run",
                "exit_code": None,
                "failure_class": "not-attempted",
                "evidence": [],
                "detail": None,
            }
            for c in spec["checks"]
        ],
        "timing": {"wall_clock_seconds": None, "simulated_seconds": None},
        "security": spec["security"],
    }
    write(run_dir, manifest)
    add_input_files(run_dir, [spec_path])
    return run_dir


def _evidence_problem(run_dir: Path, rel: str) -> str | None:
    root = run_dir.resolve()
    target = (run_dir / rel).resolve()
    if root != target and root not in target.parents:
        return f"evidence {rel} is outside the run directory"
    if not target.is_file():
        return f"evidence {rel} does not exist"
    if target.stat().st_size == 0:
        return f"evidence {rel} is empty"
    return None


def _check_problems(run_dir: Path, check: dict) -> list[str]:
    problems = []
    status, failure_class = check["status"], check["failure_class"]
    if status == "passed":
        if failure_class is not None:
            problems.append("a passed check carries a failure_class")
        if not check["evidence"]:
            problems.append("a passed check needs at least one evidence file")
    elif failure_class is None:
        problems.append(f"a {status} check needs a failure_class")
    for rel in check["evidence"]:
        problem = _evidence_problem(run_dir, rel)
        if problem:
            problems.append(problem)
    return [f"check {check['id']}: {p}" for p in problems]


def record_check(
    run_dir: Path,
    check_id: str,
    status: str,
    *,
    exit_code: int | None = None,
    failure_class: str | None = None,
    evidence: list[str] | None = None,
    detail: str | None = None,
) -> dict:
    manifest = read(run_dir)
    if manifest["finished_at"] is not None:
        raise ManifestError(f"{run_dir}: the run is finalized")
    declared = {c["id"]: c for c in manifest["checks"]}
    if check_id not in declared:
        raise ManifestError(
            f"check {check_id} is not declared in the scenario spec. Declared: {', '.join(declared)}"
        )
    check = declared[check_id]
    check.update(
        status=status,
        exit_code=exit_code,
        failure_class=failure_class,
        evidence=list(evidence or []),
        detail=detail,
    )
    problems = _check_problems(run_dir, check)
    if problems:
        raise ManifestError("; ".join(problems))
    write(run_dir, manifest)
    return manifest


def _deep_merge(base: dict, extra: dict) -> dict:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


MERGEABLE_SECTIONS = ("environment", "inputs", "timing")


def merge_section(run_dir: Path, section: str, data: dict) -> dict:
    if section not in MERGEABLE_SECTIONS:
        raise ManifestError(f"section {section} cannot be set. Choose from {MERGEABLE_SECTIONS}")
    manifest = read(run_dir)
    _deep_merge(manifest[section], data)
    write(run_dir, manifest)
    return manifest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def add_input_files(run_dir: Path, paths: list[Path]) -> dict:
    manifest = read(run_dir)
    files = {f["path"]: f for f in manifest["inputs"]["files"]}
    for path in paths:
        resolved = path.resolve()
        name = (
            str(resolved.relative_to(REPO_ROOT)) if REPO_ROOT in resolved.parents else str(resolved)
        )
        files[name] = {"path": name, "sha256": _sha256(resolved)}
    manifest["inputs"]["files"] = sorted(files.values(), key=lambda f: f["path"])
    write(run_dir, manifest)
    return manifest


def finalize(run_dir: Path) -> dict:
    manifest = read(run_dir)
    if manifest["finished_at"] is None:
        manifest["finished_at"] = _utc_now()
    write(run_dir, manifest)
    return manifest


def problems(run_dir: Path) -> list[str]:
    """Every reason this run directory breaks the result contract."""
    try:
        manifest = read(run_dir)
    except (ManifestError, json.JSONDecodeError) as exc:
        return [str(exc)]
    found = schema_errors(manifest, "run-manifest.schema.json")
    if found:
        return found
    derived = derive_status([c["status"] for c in manifest["checks"]])
    if manifest["status"] != derived:
        found.append(f"status is {manifest['status']} but the checks derive {derived}")
    expected_dir = Path(manifest["run_id"]) / manifest["track"] / manifest["scenario"]
    if run_dir.resolve().parts[-3:] != expected_dir.parts:
        found.append(f"directory does not end in {expected_dir}")
    for check in manifest["checks"]:
        found += _check_problems(run_dir, check)
    if manifest["status"] == "passed":
        if manifest["finished_at"] is None:
            found.append("a passed run must be finalized")
        for name in FILES_REQUIRED_WHEN_PASSED:
            if _evidence_problem(run_dir, name):
                found.append(f"a passed run needs a non-empty {name}")
        if (
            manifest["result_kind"] == "packet-model-simulation"
            and manifest["timing"]["simulated_seconds"] is None
        ):
            found.append("a passed packet-model run must record timing.simulated_seconds")
    metrics_file = run_dir / "metrics.json"
    if metrics_file.is_file():
        try:
            metrics = json.loads(metrics_file.read_text())
        except json.JSONDecodeError as exc:
            return found + [f"metrics.json: {exc}"]
        found += [f"metrics.json: {e}" for e in schema_errors(metrics, "result.schema.json")]
        if not found:
            if metrics["result_kind"] != manifest["result_kind"]:
                found.append("metrics.json: result_kind differs from the manifest")
            for metric in metrics["metrics"]:
                problem = _evidence_problem(run_dir, metric["source"])
                if problem:
                    found.append(f"metrics.json: {metric['name']}: {problem}")
    return found


def find_run_dirs(results_root: Path, run_id: str | None = None) -> list[Path]:
    base = results_root / run_id if run_id else results_root
    return sorted(p.parent for p in base.glob("**/manifest.json"))


def tally(results_root: Path, run_id: str | None = None) -> list[dict]:
    rows = []
    for run_dir in find_run_dirs(results_root, run_id):
        broken = problems(run_dir)
        manifest = {} if broken else read(run_dir)
        rows.append(
            {
                "dir": str(run_dir),
                "valid": not broken,
                "problems": broken,
                "required": manifest.get("required"),
                "result_kind": manifest.get("result_kind"),
                "status": manifest.get("status"),
            }
        )
    return rows
