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


def init_run(results: Path, spec: Path, run_id: str, capsys, *extra: str) -> Path:
    capsys.readouterr()
    assert main(["run-init", "--spec", str(spec), "--results-root", str(results), "--run-id", run_id, *extra]) == 0
    return Path(capsys.readouterr().out.strip())


def report(results: Path, specs: list[Path], capsys, *extra: str) -> tuple[int, str]:
    capsys.readouterr()
    code = main(["report", "--results-root", str(results), "--specs", *map(str, specs), *extra])
    return code, capsys.readouterr().out


def scenario_line(text: str, scenario: str) -> str:
    return next(line for line in text.splitlines() if f"/{scenario} " in line)


def test_report_counts_the_latest_finished_run_that_is_not_a_break(tmp_path, capsys):
    results = tmp_path / "results"
    older = init_run(results, SPEC, "20260101T000000Z", capsys)
    assert fail_check(older, "first", "failed", "assertion-failed") == 0
    assert main(["run-finalize", str(older)]) == 1
    latest = init_run(results, SPEC, "20260102T000000Z", capsys)
    complete_passed_run(latest)
    assert main(["run-finalize", str(latest)]) == 0
    broken = init_run(results, SPEC, "20260103T000000Z", capsys, "--break", "no-link", "--break-what", "the link is cut")
    assert fail_check(broken, "first", "failed", "assertion-failed") == 0
    assert main(["run-finalize", str(broken)]) == 1
    init_run(results, SPEC, "20260104T000000Z", capsys)

    code, text = report(results, [SPEC], capsys, "--strict")
    assert code == 0
    line = scenario_line(text, "unit-fixture")
    assert line.split()[:3] == ["passed", "a-soft-roce/unit-fixture", "20260102T000000Z"]
    assert "older 1, breaks 1, unfinished 1" in line
    assert "required (MVP): 1 of 1 scenarios passed" in text


def test_report_groups_by_result_kind_with_optional_apart(tmp_path, capsys):
    results = tmp_path / "results"
    optional = tmp_path / "optional.yaml"
    optional.write_text(SPEC.read_text().replace("required: true", "required: false")
                        .replace("unit-fixture", "optional-fixture").replace("software-rdma-measurement", "packet-model-simulation"))
    never = tmp_path / "never.yaml"
    never.write_text(SPEC.read_text().replace("unit-fixture", "never-fixture").replace("software-rdma-measurement", "ib-management-simulation"))
    required_dir = init_run(results, SPEC, RUN_ID, capsys)
    complete_passed_run(required_dir)
    assert main(["run-finalize", str(required_dir)]) == 0
    optional_dir = init_run(results, optional, RUN_ID, capsys)
    assert fail_check(optional_dir, "first", "blocked", "unsupported-on-tested-stack") == 0
    assert main(["run-finalize", str(optional_dir)]) == 2

    code, text = report(results, [SPEC, optional, never], capsys)
    assert code == 0
    sections = text.split("\n\n")
    rdma = next(s for s in sections if s.startswith("software-rdma-measurement"))
    model = next(s for s in sections if s.startswith("packet-model-simulation"))
    ib = next(s for s in sections if s.startswith("ib-management-simulation"))
    assert "required: passed 1, failed 0" in rdma
    assert "optional: passed 0, failed 0, blocked 1" in model and "optional-fixture" in model
    assert "required: passed 0, failed 0, blocked 0, not_run 0, never ran 1" in ib
    assert "required (MVP): 1 of 2 scenarios passed" in text
    assert "optional: 0 of 1 scenarios passed" in text
    assert report(results, [SPEC, optional, never], capsys, "--strict")[0] == 1


def test_report_exits_4_on_an_invalid_run_directory(tmp_path, capsys):
    results = tmp_path / "results"
    run_dir = init_run(results, SPEC, RUN_ID, capsys)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    manifest["status"] = "passed"
    (run_dir / "manifest.json").write_text(json.dumps(manifest))
    code, text = report(results, [SPEC], capsys)
    assert code == 4
    assert "invalid: 1" in text


def test_the_break_comes_from_the_environment_of_a_wrapper(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("RFL_BREAK", "rsyslog-stopped")
    monkeypatch.setenv("RFL_BREAK_WHAT", "rsyslog stopped on one node")
    run_dir = init_run(tmp_path, SPEC, RUN_ID, capsys)
    assert json.loads((run_dir / "manifest.json").read_text())["deliberate_break"] == {
        "name": "rsyslog-stopped", "description": "rsyslog stopped on one node"}
    assert main(["validate", str(run_dir)]) == 0


@pytest.mark.parametrize("extra", [["--break", "no-link"], ["--break-what", "cut"], ["--break", "No Link", "--break-what", "cut"]])
def test_a_break_needs_a_slug_name_and_a_description(tmp_path, capsys, extra):
    assert main(["run-init", "--spec", str(SPEC), "--results-root", str(tmp_path), "--run-id", RUN_ID, *extra]) == 4
    assert not (tmp_path / RUN_ID).exists()


def test_an_ordinary_run_records_a_null_break(run_dir):
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["schema_version"] == 2
    assert manifest["deliberate_break"] is None


def test_version_1_manifests_stay_valid_without_the_break_field(run_dir):
    manifest = json.loads((run_dir / "manifest.json").read_text())
    del manifest["deliberate_break"]
    manifest["schema_version"] = 1
    (run_dir / "manifest.json").write_text(json.dumps(manifest))
    assert main(["validate", str(run_dir)]) == 0
    manifest["deliberate_break"] = None
    (run_dir / "manifest.json").write_text(json.dumps(manifest))
    assert main(["validate", str(run_dir)]) == 4


def test_version_2_manifest_without_the_break_field_is_invalid(run_dir):
    manifest = json.loads((run_dir / "manifest.json").read_text())
    del manifest["deliberate_break"]
    (run_dir / "manifest.json").write_text(json.dumps(manifest))
    assert main(["validate", str(run_dir)]) == 4


@pytest.mark.parametrize(("track", "parameters", "expected"), [
    ("b-ibsim", {"break_link": "S1/P3-H1/P1"}, "break-link"),
    ("b-ibsim", {"topology": "b-01.net"}, None),
    ("c-ns3", {"deliberate_break": "pfc-off"}, "deliberate-break"),
    ("c-ns3", {"deliberate_break": None}, None),
    ("a-soft-roce", {"deliberate_break": "anything"}, None),
])
def test_version_1_breaks_come_from_the_input_parameter_of_their_track(track, parameters, expected):
    from rfl import manifest
    found = manifest.deliberate_break_of({"schema_version": 1, "track": track, "inputs": {"parameters": parameters}})
    assert (found or {}).get("name") == expected


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
