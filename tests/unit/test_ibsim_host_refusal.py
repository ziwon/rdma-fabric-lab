"""guest.sh and the image entry point refuse to start anything on a machine that is not the Track B guest.

docker and every simulator command are replaced by stubs first on PATH that record any call, so
the tests prove the refusal happens before either is reached.
"""

import os
import subprocess
from pathlib import Path

import pytest

from ibsim.run import SIMULATOR_COMMANDS

REPO = Path(__file__).resolve().parents[2]
GUEST_SH = REPO / "tracks/b-ibsim/scripts/guest.sh"
ENTRY = REPO / "images/ibsim/rfl-ibsim"
KEY = "20261116T000000Z-b-01"
IMAGE = "sha256:" + "a" * 64


@pytest.fixture
def stubs(tmp_path):
    if Path("/etc/rfl/track-b-guest").exists():
        pytest.skip("this machine carries the Track B guest marker")
    bin_dir, calls = tmp_path / "bin", tmp_path / "calls"
    bin_dir.mkdir()
    for name in ("docker", "systemd-detect-virt", *SIMULATOR_COMMANDS):
        stub = bin_dir / name
        stub.write_text(f'#!/bin/sh\necho "{name} $*" >> {calls}\n')
        stub.chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}, calls


def run(argv: list[str], env: dict, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(argv, input=stdin, capture_output=True, text=True, env=env, timeout=30)


@pytest.mark.parametrize(
    "args",
    [
        ["start", KEY, IMAGE, "hca1", "-R", "minhop"],
        ["build", "images/ibsim", "rfl-ibsim:m4", "APT_SNAPSHOT=x"],
        ["exec", KEY, "tool", "ibnetdiscover"],
        ["read", KEY, "opensm.log"],
        ["inspect", KEY],
        ["top", KEY],
        ["stop", KEY],
        ["stop-run", KEY.split("-")[0]],
        ["ls"],
    ],
)
def test_guest_sh_refuses_every_command_outside_the_guest(stubs, args):
    env, calls = stubs
    done = run([str(GUEST_SH), *args], env, (REPO / "tracks/b-ibsim/topologies/b-01.net").read_text())
    assert done.returncode == 2
    assert "refusing to run: /etc/rfl/track-b-guest is missing" in done.stderr and "make track-b" in done.stderr
    assert not calls.exists()


@pytest.mark.parametrize("args", [["fabric", "/dev/null"], ["tool", "ibnetdiscover"], ["console", "Unlink x"]])
def test_the_image_entry_point_refuses_without_the_guest_marker_mount(stubs, args):
    env, calls = stubs
    done = run([str(ENTRY), *args], {**env, "SIM_HOST": "hca1"})
    assert done.returncode == 2 and "runs only inside the Track B guest" in done.stderr
    assert not calls.exists()


def test_the_stubs_record_a_call_that_does_reach_them(stubs):
    env, calls = stubs
    assert run(["docker", "ps"], env).returncode == 0
    assert calls.read_text() == "docker ps\n"
