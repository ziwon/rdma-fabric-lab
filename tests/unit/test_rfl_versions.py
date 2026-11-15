from pathlib import Path

import pytest
import yaml

from rfl import versions
from rfl.__main__ import main

REPO_ROOT = Path(__file__).resolve().parents[2]
SHA = "9ec9b3a202ab15f7577cfd755df9b11881edde83"


def write_lock(tmp_path: Path, components: dict, scopes: dict) -> str:
    lock = tmp_path / "versions.lock.yaml"
    lock.write_text(yaml.safe_dump({"schema_version": 1, "components": components, "scopes": scopes}))
    return str(lock)


def component(value, kind="version"):
    return {"kind": kind, "value": value, "reason": "test", "source": "test"}


def test_scope_with_an_undecided_component_is_refused(tmp_path, capsys):
    lock = write_lock(
        tmp_path,
        {"lab.perftest": component(None), "tooling.ansible": component("9.13.0")},
        {"m1": ["lab.perftest"], "tooling": ["tooling.ansible"]},
    )
    assert main(["versions", "--lock", lock]) == 0
    assert main(["versions", "--lock", lock, "--scope", "tooling"]) == 0
    capsys.readouterr()
    assert main(["versions", "--lock", lock, "--scope", "m1"]) == 1
    assert "lab.perftest" in capsys.readouterr().err


@pytest.mark.parametrize(
    "bad",
    [
        component("latest"),
        component("master", kind="git-sha"),
        component(SHA[:12], kind="git-sha"),
        component("ubuntu:24.04", kind="image-digest"),
        component("deadbeef", kind="checksum"),
    ],
)
def test_moving_or_malformed_pins_are_refused(tmp_path, bad):
    lock = write_lock(tmp_path, {"x.y": bad}, {"s": ["x.y"]})
    assert main(["versions", "--lock", lock]) == 4


def test_well_formed_pins_are_accepted(tmp_path):
    lock = write_lock(
        tmp_path,
        {"a.sha": component(SHA, kind="git-sha"), "a.digest": component("sha256:" + "0" * 64, kind="image-digest")},
        {"s": ["a.sha", "a.digest"]},
    )
    assert main(["versions", "--lock", lock, "--scope", "s"]) == 0


def test_scope_cannot_name_a_missing_component(tmp_path):
    lock = write_lock(tmp_path, {"x.y": component("1.0")}, {"s": ["x.y", "x.z"]})
    assert main(["versions", "--lock", lock]) == 4


def test_unknown_scope_is_an_error(tmp_path):
    lock = write_lock(tmp_path, {"x.y": component("1.0")}, {"s": ["x.y"]})
    assert main(["versions", "--lock", lock, "--scope", "nope"]) == 4


def test_repository_lock_is_valid_and_tooling_is_pinned():
    lock = str(REPO_ROOT / "versions.lock.yaml")
    assert main(["versions", "--lock", lock]) == 0
    assert main(["versions", "--lock", lock, "--scope", "tooling"]) == 0


TOOLING_LOCK = {
    "components": {
        "tooling.python": component("3.12"),
        "tooling.ansible_core": component("2.16.20"),
        "lab.perftest": component("24.01"),
    }
}


def test_matching_tooling_reports_no_drift():
    observed = {"tooling.python": "3.12", "tooling.ansible_core": "2.16.20"}
    assert versions.tooling_drift(TOOLING_LOCK, observed) == []


@pytest.mark.parametrize(
    ("observed", "named"),
    [
        ({"tooling.python": "3.13", "tooling.ansible_core": "2.16.20"}, "tooling.python"),
        ({"tooling.python": "3.12", "tooling.ansible_core": "2.17.1"}, "tooling.ansible_core"),
        ({"tooling.python": "3.12", "tooling.ansible_core": None}, "tooling.ansible_core"),
    ],
)
def test_a_different_or_missing_tool_is_reported(observed, named):
    drift = versions.tooling_drift(TOOLING_LOCK, observed)
    assert len(drift) == 1 and drift[0].startswith(named)


def test_components_the_lock_does_not_pin_are_ignored():
    assert versions.tooling_drift(TOOLING_LOCK, {"tooling.python": "3.12", "tooling.yamllint": "9.9"}) == []
