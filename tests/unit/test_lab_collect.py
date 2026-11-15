import copy
from pathlib import Path

import yaml

from collect import __main__ as collect

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT = (REPO_ROOT / "tests/fixtures/a-soft-roce/collect/guest-report.txt").read_text()
LOCK = yaml.safe_load((REPO_ROOT / "versions.lock.yaml").read_text())
PACKAGES = yaml.safe_load((REPO_ROOT / "ansible/inventories/lab/group_vars/all/lab.yml").read_text())["lab_packages"]


def test_guest_report_maps_to_lock_components():
    observed = collect.observed_versions(REPORT, PACKAGES)
    assert observed["lab.guest_kernel"] == ["6.8.0-147-generic"]
    assert observed["lab.guest_image"] == ["20261017"]
    assert len(observed["lab.rdma_core"]) == 4
    assert observed["lab.perftest"] == ["24.01.0+0.38-1build2"]


def test_lock_agrees_with_the_captured_guest():
    rows = collect.lock_rows(LOCK, collect.observed_versions(REPORT, PACKAGES))
    assert {r["component"] for r in rows} == {c for c in LOCK["scopes"]["m1"] if c.startswith("lab.")}
    assert "lab.k8s_test_image" not in {r["component"] for r in rows}
    assert all(r["agrees"] for r in rows), rows


def test_a_moved_lock_value_disagrees():
    moved = copy.deepcopy(LOCK)
    moved["components"]["lab.rdma_core"]["value"] = "50.0-2ubuntu0.3"
    rows = {r["component"]: r for r in collect.lock_rows(moved, collect.observed_versions(REPORT, PACKAGES))}
    assert not rows["lab.rdma_core"]["agrees"]
    assert rows["lab.perftest"]["agrees"]


def test_missing_package_disagrees():
    report = "\n".join(line for line in REPORT.splitlines() if not line.startswith("perftest="))
    rows = {r["component"]: r for r in collect.lock_rows(LOCK, collect.observed_versions(report, PACKAGES))}
    assert not rows["lab.perftest"]["agrees"]
