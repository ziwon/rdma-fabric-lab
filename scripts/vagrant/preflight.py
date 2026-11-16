"""What a Vagrant target needs from the host, and the refusal it prints when something is missing.

Facts are read once from the host. Each check turns the facts into one refusal or none, and NEEDS
says which checks each kind of target runs, so every target refuses the same way.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

PLUGIN = "vagrant-vmware-desktop"
UTILITY_BINARY = "/opt/vagrant-vmware-desktop/bin/vagrant-vmware-utility"
UTILITY_ADDRESS = ("127.0.0.1", 9922)
DOC = "docs/setup-vagrant.md"
VERSION_LINE = re.compile(r"^Vagrant (\d+\.\d+\.\d+)\s*$", re.MULTILINE)
VMWARE_MODULES = ("vmmon", "vmnet")
KVM_MODULES = ("kvm_intel", "kvm_amd")
KVM_VIRT_AT_LOAD = Path("/sys/module/kvm/parameters/enable_virt_at_load")


@dataclass(frozen=True)
class Facts:
    qemu: tuple[tuple[int, str], ...]
    vagrant: str | None
    plugin: str | None
    utility_installed: bool
    utility_listening: bool
    vmrun: bool
    modules: frozenset[str]
    kvm_virt_at_load: bool | None
    vagrant_home: str


def qemu_processes(proc: Path = Path("/proc")) -> list[tuple[int, str]]:
    """(pid, argv0) of every qemu-system process. argv0, not comm, because the harness renames comm to rfl-lab-<guest>."""
    found = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv0 = (entry / "cmdline").read_bytes().split(b"\0", 1)[0].decode(errors="replace")
        except OSError:
            continue
        if Path(argv0).name.startswith("qemu-system"):
            found.append((int(entry.name), argv0))
    return sorted(found)


def vagrant_version(text: str) -> str | None:
    match = VERSION_LINE.search(text)
    return match.group(1) if match else None


def machine_readable(text: str) -> list[tuple[str, str, tuple[str, ...]]]:
    """(target, type, data) rows of `vagrant --machine-readable`, with Vagrant's comma escape undone."""
    rows = []
    for line in text.splitlines():
        parts = line.split(",")
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        data = tuple(p.replace("%!(VAGRANT_COMMA)", ",").replace("\\n", "\n") for p in parts[3:])
        rows.append((parts[1], parts[2], data))
    return rows


def plugin_version(text: str, plugin: str = PLUGIN) -> str | None:
    """Version of plugin from `vagrant plugin list --machine-readable`, which prints "3.0.5, global"."""
    for target, kind, data in machine_readable(text):
        if kind == "plugin-version" and target == plugin and data:
            return data[0].split(",")[0].strip()
    return None


def boxes(text: str) -> set[tuple[str, str]]:
    """(name, provider) pairs from `vagrant box list --machine-readable`."""
    found, name = set(), None
    for _, kind, data in machine_readable(text):
        if kind == "box-name" and data:
            name = data[0]
        elif kind == "box-provider" and data and name is not None:
            found.add((name, data[0]))
    return found


def loaded_modules(text: str) -> frozenset[str]:
    return frozenset(line.split(" ", 1)[0] for line in text.splitlines() if line)


def check_vagrant(facts: Facts, pins: dict[str, str]) -> str | None:
    want = pins["vagrant.vagrant"]
    if facts.vagrant is None:
        return f"Vagrant is not installed, or `vagrant --version` printed no version. Install Vagrant {want} as {DOC} section 3.1 says."
    if facts.vagrant != want:
        return f"Vagrant {facts.vagrant} runs, but versions.lock.yaml pins vagrant.vagrant {want}. Install {want} or pin the new release."
    return None


def check_plugin(facts: Facts, pins: dict[str, str]) -> str | None:
    want = pins["vagrant.vmware_desktop_plugin"]
    if facts.vagrant is None:
        return f"The {PLUGIN} plugin is not checked, because Vagrant does not run. Install it after Vagrant, {DOC} section 3.3."
    if facts.plugin is None:
        return (f"The {PLUGIN} plugin is missing from VAGRANT_HOME={facts.vagrant_home}. "
                f"Install it there with `vagrant plugin install {PLUGIN} --plugin-version {want}`, {DOC} section 3.3.")
    if facts.plugin != want:
        return f"{PLUGIN} {facts.plugin} is installed, but versions.lock.yaml pins {want}."
    return None


def check_utility(facts: Facts, pins: dict[str, str]) -> str | None:
    if not facts.utility_installed:
        return f"The Vagrant VMware Utility is not installed ({UTILITY_BINARY} is missing). {DOC} section 3.2 installs it."
    if not facts.utility_listening:
        return (f"The Vagrant VMware Utility does not answer on {UTILITY_ADDRESS[0]}:{UTILITY_ADDRESS[1]}. "
                "Start it with `sudo systemctl start vagrant-vmware-utility`.")
    return None


def check_vmware(facts: Facts, pins: dict[str, str]) -> str | None:
    if not facts.vmrun:
        return f"VMware Workstation is not installed: vmrun is not on PATH. {DOC} section 2.2 installs it."
    missing = [m for m in VMWARE_MODULES if m not in facts.modules]
    if missing:
        return (f"The kernel modules {' and '.join(missing)} are not loaded, so VMware cannot power on a guest. "
                f"Build and sign them as {DOC} sections 2.3 and 2.4 say.")
    return None


def check_no_qemu(facts: Facts, pins: dict[str, str]) -> str | None:
    if not facts.qemu:
        return None
    listed = ", ".join(f"{pid} ({argv0})" for pid, argv0 in facts.qemu)
    return (f"qemu-system processes run: {listed}. A KVM guest holds VT-x, and a VMware guest cannot power on beside it. "
            "Stop the QEMU guests first, for example with make lab-down.")


CHECKS = {
    "vagrant": check_vagrant,
    "plugin": check_plugin,
    "utility": check_utility,
    "vmware": check_vmware,
    "no-qemu": check_no_qemu,
}

# provider: every target that drives existing guests. boot: the target that powers guests on.
NEEDS = {
    "provider": ("vagrant", "plugin", "utility", "vmware"),
    "boot": ("no-qemu", "vagrant", "plugin", "utility", "vmware"),
}


def report(facts: Facts, need: str, pins: dict[str, str]) -> list[tuple[str, str | None]]:
    """(check, refusal or None) for every check a target of kind need runs."""
    return [(name, CHECKS[name](facts, pins)) for name in NEEDS[need]]


def warnings(facts: Facts, need: str) -> list[str]:
    kvm = [m for m in KVM_MODULES if m in facts.modules]
    if need == "boot" and kvm and facts.kvm_virt_at_load:
        return [f"{kvm[0]} is loaded with kvm.enable_virt_at_load=Y, so KVM holds VT-x even with no QEMU guest. "
                f"If VMware fails to power on a guest, set enable_virt_at_load=0 as {DOC} section 2.5 says. "
                "Nobody has yet confirmed that this blocks VMware Workstation."]
    return []


def _run(argv: list[str], timeout: int = 120) -> str | None:
    if shutil.which(argv[0]) is None:
        return None
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def _listening(address: tuple[str, int]) -> bool:
    try:
        with socket.create_connection(address, timeout=2):
            return True
    except OSError:
        return False


def collect(proc: Path = Path("/proc")) -> Facts:
    vagrant = vagrant_version(_run(["vagrant", "--version"]) or "")
    plugin = plugin_version(_run(["vagrant", "plugin", "list", "--machine-readable"]) or "") if vagrant else None
    try:
        at_load = KVM_VIRT_AT_LOAD.read_text().strip() == "Y"
    except OSError:
        at_load = None
    return Facts(
        qemu=tuple(qemu_processes(proc)),
        vagrant=vagrant,
        plugin=plugin,
        utility_installed=Path(UTILITY_BINARY).is_file(),
        utility_listening=_listening(UTILITY_ADDRESS),
        vmrun=shutil.which("vmrun") is not None,
        modules=loaded_modules((proc / "modules").read_text()),
        kvm_virt_at_load=at_load,
        vagrant_home=os.environ.get("VAGRANT_HOME", "~/.vagrant.d"),
    )
