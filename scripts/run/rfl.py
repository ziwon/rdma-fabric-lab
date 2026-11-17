"""The rfl CLI, called as a subprocess so every runner goes through the same contract checks."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class RflError(RuntimeError):
    pass


def _rfl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "scripts")}
    out = subprocess.run([sys.executable, "-m", "rfl", *args], capture_output=True, text=True, env=env)
    if check and out.returncode != 0:
        raise RflError(f"rfl {args[0]} exited {out.returncode}: {out.stderr.strip()}")
    return out


def init(spec: Path, run_id: str | None = None, deliberate_break: dict | None = None) -> Path:
    """Without deliberate_break, rfl run-init reads RFL_BREAK and RFL_BREAK_WHAT, which a wrapper that broke the
    environment sets."""
    args = ["run-init", "--spec", str(spec), *(["--run-id", run_id] if run_id else [])]
    if deliberate_break:
        args += ["--break", deliberate_break["name"], "--break-what", deliberate_break["description"]]
    return Path(_rfl(*args).stdout.strip())


def check(
    run_dir: Path,
    check_id: str,
    status: str,
    *,
    exit_code: int | None = None,
    failure_class: str | None = None,
    evidence: list[str] = (),
    detail: str | None = None,
) -> None:
    args = ["run-check", str(run_dir), "--id", check_id, "--status", status]
    if exit_code is not None:
        args += ["--exit-code", str(exit_code)]
    if failure_class:
        args += ["--failure-class", failure_class]
    for item in evidence:
        args += ["--evidence", item]
    if detail:
        args += ["--detail", detail]
    _rfl(*args)


def set_section(run_dir: Path, section: str, data: dict) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(data, handle)
    try:
        _rfl("run-set", str(run_dir), "--section", section, "--file", handle.name)
    finally:
        os.unlink(handle.name)


def add_inputs(run_dir: Path, files: list[Path]) -> None:
    _rfl("run-input", str(run_dir), *(str(f) for f in files))


def finalize(run_dir: Path) -> tuple[int, str]:
    out = _rfl("run-finalize", str(run_dir), check=False)
    return out.returncode, (out.stdout + out.stderr).strip()


def write_metrics(run_dir: Path, metrics: list[dict]) -> None:
    """Write metrics.json with the result kind the run's scenario spec declared."""
    kind = json.loads((run_dir / "manifest.json").read_text())["result_kind"]
    (run_dir / "metrics.json").write_text(
        json.dumps({"schema_version": 1, "result_kind": kind, "metrics": metrics}, indent=2) + "\n")
