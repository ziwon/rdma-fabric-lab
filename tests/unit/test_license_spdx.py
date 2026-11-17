"""The repository is MIT, except the files that link to or patch ns-3, which keep GPL-2.0-only."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GPL_FILES = sorted([*(ROOT / "tracks/c-ns3/driver").glob("*"), *(ROOT / "images/ns3").glob("*.patch")])
SPDX_GPL = "SPDX-License-Identifier: GPL-2.0-only"


def test_the_repository_license_is_mit():
    text = (ROOT / "LICENSE").read_text()
    assert text.startswith("MIT License\n")
    assert "Copyright (c) 2026 The rdma-fabric-lab contributors" in text


def test_every_ns3_driver_and_patch_file_carries_the_gpl_spdx_line_first():
    assert GPL_FILES, "the ns-3 driver or patch files moved"
    missing = [str(p.relative_to(ROOT)) for p in GPL_FILES if SPDX_GPL not in p.read_text().splitlines()[0]]
    assert missing == []


def test_readme_names_the_gpl_exception():
    readme = (ROOT / "README.md").read_text()
    assert "## License" in readme
    for path in ("tracks/c-ns3/driver/rfl-star.cc", "images/ns3/qbb-resume-qp-lookup.patch"):
        assert path in readme
