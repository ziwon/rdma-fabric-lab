import subprocess
import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[1] / "integration" / "qemu-lab"
sys.path.insert(0, str(HARNESS))

import guests  # noqa: E402

PROFILES = HARNESS / "profiles"
ROW = "a\t1\t1\t1\t1\tmac=52:54:00:00:00:01,net=user\n"


def qemu_flags(profile: Path) -> list[str]:
    done = subprocess.run([sys.executable, "-I", str(HARNESS / "guests.py"), "qemu-flags", str(profile)],
                          capture_output=True, text=True, check=True)
    return done.stdout.splitlines()


@pytest.mark.parametrize("name", ["lab", "ha"])
def test_profiles_without_a_qemu_header_add_no_qemu_flag(name):
    assert qemu_flags(PROFILES / f"{name}.tsv") == []


def test_the_ibsim_profile_adds_no_reboot():
    assert qemu_flags(PROFILES / "ibsim.tsv") == ["-no-reboot"]


@pytest.mark.parametrize("flags", ["-S", "-no-reboot -device virtio-rng-pci", "-snapshot"])
def test_any_other_qemu_flag_is_refused(flags):
    with pytest.raises(guests.ProfileError, match="are not allowed"):
        guests.parse(f"#inventory x\n#qemu {flags}\n{ROW}")


def test_an_empty_flag_list_expands_to_no_argument_in_the_lab_sh_shell():
    """lab.sh reads the flags with mapfile and passes "${profile_flags[@]}" under set -u."""
    script = ('set -euo pipefail; mapfile -t profile_flags < <("$1" -I "$2" qemu-flags "$3"); '
              'count() { echo $#; }; count -smp 4 "${profile_flags[@]}" -m 1')
    for profile, want in (("lab.tsv", "4"), ("ha.tsv", "4"), ("ibsim.tsv", "5")):
        done = subprocess.run(["bash", "-c", script, "bash", sys.executable, str(HARNESS / "guests.py"), str(PROFILES / profile)],
                              capture_output=True, text=True, check=True)
        assert done.stdout.strip() == want, profile
