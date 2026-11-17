"""guest.sh and the image entry point refuse to start anything on a machine that is not the Track C guest.

docker, systemd-detect-virt and every simulator program are replaced by stubs first on PATH that
record any call, so the tests prove the refusal happens before any of them is reached.
"""

import os
import subprocess
from pathlib import Path

import pytest

from ns3.verdict import SIMULATOR_PROGRAMS

REPO = Path(__file__).resolve().parents[2]
GUEST_SH = REPO / "tracks/c-ns3/scripts/guest.sh"
ENTRY = REPO / "images/ns3/rfl-ns3"
KEY = "20261116T000000Z-c-02-single-flow"
IMAGE = "sha256:" + "a" * 64


@pytest.fixture
def stubs(tmp_path):
    if Path("/etc/rfl/track-c-guest").exists():
        pytest.skip("this machine carries the Track C guest marker")
    bin_dir, calls = tmp_path / "bin", tmp_path / "calls"
    bin_dir.mkdir()
    for name in ("docker", "systemd-detect-virt", "python3", *SIMULATOR_PROGRAMS):
        stub = bin_dir / name
        stub.write_text(f'#!/bin/sh\necho "{name} $*" >> {calls}\n')
        stub.chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}, calls


def run(argv: list[str], env: dict, stdin: bytes = b"") -> subprocess.CompletedProcess:
    return subprocess.run(argv, input=stdin, capture_output=True, env=env, timeout=30)


@pytest.mark.parametrize(
    "args",
    [
        ["build", "images/ns3", "tracks/c-ns3/driver", "rfl-ns3:m5", "SOURCE_DATE_EPOCH=1"],
        ["clean", "rfl-ns3:m5", "ubuntu:24.04"],
        ["save", "rfl-ns3:m5", IMAGE],
        ["run", KEY, "run1", IMAGE],
        ["run", KEY, "run1", IMAGE, "unlocked"],
        ["fetch", KEY, "run1"],
        ["inspect", KEY, "run1"],
        ["stop", KEY],
        ["stop-run", KEY.split("-")[0]],
        ["ls"],
    ],
)
def test_guest_sh_refuses_every_command_outside_the_guest(stubs, args):
    env, calls = stubs
    done = run([str(GUEST_SH), *args], env)
    assert done.returncode == 2
    assert b"refusing to run: /etc/rfl/track-c-guest is missing" in done.stderr and b"make track-c" in done.stderr
    assert not calls.exists()


def test_the_image_entry_point_refuses_without_the_guest_marker_mount(stubs):
    env, calls = stubs
    done = run([str(ENTRY), "steps"], env)
    assert done.returncode == 2 and b"runs only inside the Track C guest" in done.stderr
    assert not calls.exists()


def test_the_stubs_record_a_call_that_does_reach_them(stubs):
    env, calls = stubs
    assert run(["docker", "ps"], env).returncode == 0
    assert calls.read_text() == "docker ps\n"
