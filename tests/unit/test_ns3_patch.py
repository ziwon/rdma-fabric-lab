"""The one ns-3 patch of the image: pinned, licensed like ns-3, and limited to QbbNetDevice::Resume."""

import hashlib
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
PATCH = REPO / "images/ns3/qbb-resume-qp-lookup.patch"
LOCK = yaml.safe_load((REPO / "versions.lock.yaml").read_text())["components"]


def test_the_patch_hashes_to_its_lock_pin():
    assert "sha256:" + hashlib.sha256(PATCH.read_bytes()).hexdigest() == LOCK["ns3.resume_patch"]["value"]


def test_the_patch_and_the_driver_start_with_the_ns3_license():
    assert PATCH.read_text().splitlines()[0] == "SPDX-License-Identifier: GPL-2.0-only"
    driver = (REPO / "tracks/c-ns3/driver/rfl-star.cc").read_text()
    assert driver.splitlines()[0] == "// SPDX-License-Identifier: GPL-2.0-only"


def test_the_header_names_the_upstream_file_and_commit():
    head = PATCH.read_text().split("--- a/", 1)[0]
    assert "simulation/src/point-to-point/model/qbb-net-device.cc" in head
    assert LOCK["ns3.ns3_alibabacloud"]["value"] in head


def test_the_patch_touches_one_hunk_of_resume_only():
    body = PATCH.read_text().split("--- a/", 1)[1]
    assert body.count("@@ -") == 1 and body.startswith("simulation/src/point-to-point/model/qbb-net-device.cc")
    removed = [line for line in body.splitlines() if line.startswith("-") and not line.startswith("---")]
    added = [line for line in body.splitlines() if line.startswith("+") and not line.startswith("+++")]
    assert [line.strip("-\t ") for line in removed] == [
        "Ptr<RdmaQueuePair> lastQp = m_rdmaEQ->GetQp(qIndex);",
        "if(lastQp->nvls_enable == 1 && m_node->GetNodeType() == 2) SwitchAsHostSend();"]
    assert [line.strip("+\t ") for line in added] == [
        "if (m_node->GetNodeType() == 2 && m_rdmaEQ->GetQp(qIndex)->nvls_enable == 1) SwitchAsHostSend();"]


def test_the_image_build_applies_the_patch_and_fails_when_it_does_not_apply():
    dockerfile = (REPO / "images/ns3/Dockerfile").read_text()
    apply = dockerfile.index("apply --check /opt/rfl-patches/qbb-resume-qp-lookup.patch")
    assert dockerfile.index("RUN fetch-sources") < apply < dockerfile.index("RUN ./scripts/build.sh -c ns3")
