import json
from pathlib import Path

import pytest

from rfl.__main__ import main

SPEC = Path(__file__).resolve().parents[1] / "fixtures" / "rfl" / "scenario.yaml"
RUN_ID = "20260101T000000Z"


@pytest.fixture
def run_dir(tmp_path, capsys):
    assert main(["run-init", "--spec", str(SPEC), "--results-root", str(tmp_path), "--run-id", RUN_ID]) == 0
    path = Path(capsys.readouterr().out.strip())
    assert path == tmp_path / RUN_ID / "a-soft-roce" / "unit-fixture"
    return path


def evidence(run_dir: Path, name: str, content: str = "output\n") -> str:
    (run_dir / "raw" / name).write_text(content)
    return f"raw/{name}"


def pass_check(run_dir: Path, check_id: str) -> int:
    return main(
        ["run-check", str(run_dir), "--id", check_id, "--status", "passed", "--exit-code", "0",
         "--evidence", evidence(run_dir, f"{check_id}.log")]
    )


def fail_check(run_dir: Path, check_id: str, status: str, failure_class: str) -> int:
    return main(
        ["run-check", str(run_dir), "--id", check_id, "--status", status, "--failure-class", failure_class]
    )


def complete_passed_run(run_dir: Path) -> None:
    assert pass_check(run_dir, "first") == 0
    assert pass_check(run_dir, "second") == 0
    (run_dir / "commands.log").write_text("ib_write_bw -d rxe0\n")
    (run_dir / "resolved-config.yaml").write_text("device: rxe0\n")


def status_of(run_dir: Path) -> str:
    return json.loads((run_dir / "manifest.json").read_text())["status"]


def test_fresh_run_is_not_run(run_dir):
    assert main(["run-finalize", str(run_dir)]) == 3
    assert status_of(run_dir) == "not_run"
    assert main(["validate", str(run_dir)]) == 0


def test_run_passes_only_when_every_declared_check_passed(run_dir):
    assert pass_check(run_dir, "first") == 0
    assert status_of(run_dir) == "not_run"
    complete_passed_run(run_dir)
    assert main(["run-finalize", str(run_dir)]) == 0
    assert status_of(run_dir) == "passed"
    assert main(["validate", str(run_dir)]) == 0


@pytest.mark.parametrize(
    ("second_status", "failure_class", "expected", "exit_code"),
    [("failed", "timeout", "failed", 1), ("blocked", "environment-blocked", "blocked", 2)],
)
def test_one_bad_check_decides_the_run(run_dir, second_status, failure_class, expected, exit_code):
    assert pass_check(run_dir, "first") == 0
    assert fail_check(run_dir, "second", second_status, failure_class) == 0
    assert main(["run-finalize", str(run_dir)]) == exit_code
    assert status_of(run_dir) == expected


def test_failed_outranks_blocked(run_dir):
    assert fail_check(run_dir, "first", "blocked", "environment-blocked") == 0
    assert fail_check(run_dir, "second", "failed", "command-failed") == 0
    assert status_of(run_dir) == "failed"


def test_passed_check_is_refused_without_usable_evidence(run_dir, tmp_path):
    outside = tmp_path / "outside.log"
    outside.write_text("output\n")
    attempts = [
        [],
        ["--evidence", "raw/missing.log"],
        ["--evidence", evidence(run_dir, "empty.log", "")],
        ["--evidence", "../../../outside.log"],
    ]
    for extra in attempts:
        assert main(["run-check", str(run_dir), "--id", "first", "--status", "passed", *extra]) == 4
    assert status_of(run_dir) == "not_run"


def test_failed_check_needs_a_failure_class(run_dir):
    assert main(["run-check", str(run_dir), "--id", "first", "--status", "failed"]) == 4


def test_undeclared_check_is_refused(run_dir):
    assert main(["run-check", str(run_dir), "--id", "invented", "--status", "failed",
                 "--failure-class", "timeout"]) == 4


def test_finalized_run_takes_no_more_checks(run_dir):
    assert main(["run-finalize", str(run_dir)]) == 3
    assert pass_check(run_dir, "first") == 4


def test_hand_edited_status_is_invalid(run_dir):
    manifest_file = run_dir / "manifest.json"
    manifest = json.loads(manifest_file.read_text())
    manifest["status"] = "passed"
    manifest_file.write_text(json.dumps(manifest))
    assert main(["validate", str(run_dir)]) == 4


def test_passed_run_is_invalid_once_its_evidence_is_gone(run_dir):
    complete_passed_run(run_dir)
    assert main(["run-finalize", str(run_dir)]) == 0
    (run_dir / "raw" / "first.log").unlink()
    assert main(["validate", str(run_dir)]) == 4


@pytest.mark.parametrize("missing", ["commands.log", "resolved-config.yaml"])
def test_passed_run_needs_commands_and_resolved_config(run_dir, missing):
    complete_passed_run(run_dir)
    (run_dir / missing).unlink()
    assert main(["run-finalize", str(run_dir)]) == 4


def metric(run_dir: Path, **overrides) -> dict:
    base = {"name": "ib_write_bw.bw_average", "value": 12.5, "unit": "MiB/s",
            "absent_reason": None, "source": evidence(run_dir, "bw.log")}
    return base | overrides


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, 0),
        ({"value": None, "absent_reason": "client timed out before reporting"}, 0),
        ({"value": None}, 4),
        ({"value": 0, "absent_reason": "not measured"}, 4),
        ({"source": "raw/never-written.log"}, 4),
    ],
)
def test_metrics_never_stand_in_for_missing_measurements(run_dir, overrides, expected):
    metrics = {"schema_version": 1, "result_kind": "software-rdma-measurement",
               "metrics": [metric(run_dir, **overrides)]}
    (run_dir / "metrics.json").write_text(json.dumps(metrics))
    assert main(["validate", str(run_dir)]) == expected


def test_input_files_are_recorded_with_checksums(run_dir, tmp_path):
    topology = tmp_path / "topology.txt"
    topology.write_text("abc")
    assert main(["run-input", str(run_dir), str(topology)]) == 0
    files = json.loads((run_dir / "manifest.json").read_text())["inputs"]["files"]
    recorded = {f["path"]: f["sha256"] for f in files}
    assert recorded[str(topology)] == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert "tests/fixtures/rfl/scenario.yaml" in recorded


def test_packet_model_run_cannot_pass_without_simulated_time(tmp_path, capsys):
    spec = tmp_path / "c.yaml"
    spec.write_text(SPEC.read_text().replace("software-rdma-measurement", "packet-model-simulation")
                    .replace("a-soft-roce", "c-ns3"))
    assert main(["run-init", "--spec", str(spec), "--results-root", str(tmp_path), "--run-id", RUN_ID]) == 0
    run_dir = Path(capsys.readouterr().out.strip())
    complete_passed_run(run_dir)
    assert main(["run-finalize", str(run_dir)]) == 4
    timing = tmp_path / "timing.json"
    timing.write_text(json.dumps({"wall_clock_seconds": 3.2, "simulated_seconds": 0.05}))
    assert main(["run-set", str(run_dir), "--section", "timing", "--file", str(timing)]) == 0
    assert main(["validate", str(run_dir)]) == 0


def test_report_tallies_optional_runs_apart_from_required(tmp_path, capsys):
    optional_spec = tmp_path / "optional.yaml"
    optional_spec.write_text(SPEC.read_text().replace("required: true", "required: false")
                             .replace("unit-fixture", "optional-fixture"))
    for spec in (SPEC, optional_spec):
        assert main(["run-init", "--spec", str(spec), "--results-root", str(tmp_path), "--run-id", RUN_ID]) == 0
    required_dir, optional_dir = (Path(line) for line in capsys.readouterr().out.split())
    complete_passed_run(required_dir)
    assert main(["run-finalize", str(required_dir)]) == 0
    assert fail_check(optional_dir, "first", "blocked", "unsupported-on-tested-stack") == 0
    capsys.readouterr()
    assert main(["report", "--results-root", str(tmp_path)]) == 0
    report = capsys.readouterr().out
    required_line = next(line for line in report.splitlines() if line.startswith("required"))
    optional_line = next(line for line in report.splitlines() if line.startswith("optional"))
    assert "passed=1" in required_line and "blocked=0" in required_line
    assert "blocked=1" in optional_line and "passed=0" in optional_line


def test_runner_metrics_take_the_result_kind_of_the_scenario(tmp_path, capsys):
    from run import rfl as runner_rfl
    spec = tmp_path / "cluster.yaml"
    spec.write_text(SPEC.read_text().replace("software-rdma-measurement", "software-cluster-test"))
    assert main(["run-init", "--spec", str(spec), "--results-root", str(tmp_path), "--run-id", RUN_ID]) == 0
    run_dir = Path(capsys.readouterr().out.strip())
    source = evidence(run_dir, "gap.log")
    runner_rfl.write_metrics(run_dir, [{"name": "api_unavailable_seconds", "value": 6.0, "unit": "s",
                                        "absent_reason": None, "source": source}])
    assert json.loads((run_dir / "metrics.json").read_text())["result_kind"] == "software-cluster-test"
    assert main(["validate", str(run_dir)]) == 0
