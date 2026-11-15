"""make doctor: read-only checks of the machine that hosts the lab.

Exits 1 when any finding fails. An address plan range that overlaps a network the
host already uses always fails. Resource shortfalls against the lab budget warn,
because that budget is a starting allocation.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import host, plan

REPO_ROOT = Path(__file__).resolve().parents[2]
LAB_INVENTORY = REPO_ROOT / "ansible" / "inventories" / "lab"
LOCK = REPO_ROOT / "versions.lock.yaml"
GIB = 1024**3

LEVELS = ("ok", "info", "warn", "fail")

# Lab budget. vCPU, memory GiB and disk GiB per component, B and C at their upper bounds.
BUDGET = (
    ("cp1", 2, 4, 30),
    ("worker1", 4, 6, 40),
    ("worker2", 4, 6, 40),
    ("Track B container", 2, 2, 0),
    ("Track C container", 4, 8, 0),
)
VM_NAMES = ("cp1", "worker1", "worker2")

# name, argv printing a version, what needs it, level when missing.
TOOLS = (
    ("ssh", ["ssh", "-V"], "Ansible and the runner reach the guests", "fail"),
    ("ip", ["ip", "-V"], "doctor reads addresses and routes", "fail"),
    ("ansible-playbook", ["ansible-playbook", "--version"], "make bootstrap and make rxe-up", "fail"),
    ("qemu-system-x86_64", ["qemu-system-x86_64", "--version"], "the QEMU harness", "warn"),
    ("qemu-img", ["qemu-img", "--version"], "the QEMU harness", "warn"),
    ("cloud-localds", None, "the QEMU harness seeds", "warn"),
    ("ssh-keygen", None, "the QEMU harness keys", "warn"),
    ("curl", ["curl", "--version"], "the QEMU harness image download", "warn"),
    ("vmrun", ["vmrun"], "VMware Workstation, the hypervisor of the manual setup", "info"),
)


@dataclass(frozen=True)
class Finding:
    section: str
    level: str
    message: str


def _first_line(argv: list[str]) -> str:
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)
    return (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr).strip() else ""


def virtualization_findings(cpuinfo: str, kvm_rw: bool, kvm_exists: bool, hypervisors: list[str]) -> list[Finding]:
    flags = {f for line in cpuinfo.splitlines() if line.startswith("flags") for f in line.split(":", 1)[1].split()}
    found = []
    ext = sorted(flags & {"vmx", "svm"})
    found.append(
        Finding("virtualization", "ok", f"CPU exposes {', '.join(ext)}")
        if ext
        else Finding("virtualization", "fail", "CPU flags show neither vmx nor svm, so no guest gets hardware virtualization")
    )
    if kvm_rw:
        found.append(Finding("virtualization", "ok", "/dev/kvm is readable and writable for this user"))
    elif kvm_exists:
        found.append(Finding("virtualization", "warn", "/dev/kvm exists but this user cannot open it. Join the kvm group for the QEMU harness."))
    else:
        found.append(Finding("virtualization", "warn", "/dev/kvm is absent. The QEMU harness needs KVM; VMware brings its own driver."))
    found.append(
        Finding("virtualization", "ok", f"hypervisors found: {', '.join(hypervisors)}")
        if hypervisors
        else Finding("virtualization", "fail", "neither VMware Workstation (vmrun) nor qemu-system-x86_64 is installed")
    )
    return found


def tool_findings(which, version_of) -> list[Finding]:
    found = []
    for name, argv, purpose, missing_level in TOOLS:
        path = which(name)
        if path is None:
            found.append(Finding("tools", missing_level, f"{name} not found. Needed by {purpose}."))
        else:
            version = version_of(argv) if argv else ""
            found.append(Finding("tools", "ok", f"{name} {version or path}".strip()))
    return found


def resource_findings(cpus: int, mem_available_gib: float, disk_free_gib: float, disk_path: str) -> list[Finding]:
    vm = [row for row in BUDGET if row[0] in VM_NAMES]
    vm_cpu, vm_mem, vm_disk = (sum(r[i] for r in vm) for i in (1, 2, 3))
    all_cpu, all_mem = (sum(r[i] for r in BUDGET) for i in (1, 2))
    found = []
    if cpus >= all_cpu:
        found.append(Finding("resources", "ok", f"{cpus} CPUs cover the lab vCPU budget of {vm_cpu} for the VMs and {all_cpu} with B and C"))
    else:
        found.append(Finding("resources", "warn", f"{cpus} CPUs are below the {all_cpu} vCPU budget with B and C. Run B and C one after the other."))
    if mem_available_gib >= all_mem:
        found.append(Finding("resources", "ok", f"{mem_available_gib:.1f} GiB available covers {vm_mem} GiB for the VMs and {all_mem} GiB with B and C"))
    else:
        found.append(
            Finding(
                "resources",
                "warn",
                f"{mem_available_gib:.1f} GiB available is below the {all_mem} GiB budget with B and C"
                + ("" if mem_available_gib >= vm_mem else f" and below the {vm_mem} GiB for the VMs. Shrink the VMs.")
                + ".",
            )
        )
    if disk_free_gib >= vm_disk:
        found.append(Finding("resources", "ok", f"{disk_free_gib:.1f} GiB free on {disk_path} covers the {vm_disk} GiB VM disk budget"))
    else:
        found.append(
            Finding(
                "resources",
                "warn",
                f"{disk_free_gib:.1f} GiB free on {disk_path} is below the {vm_disk} GiB VM disk budget. QEMU qcow2 overlays "
                "grow on demand, preallocated VMware disks do not.",
            )
        )
    return found


def lock_findings(lock: dict, observed: dict[str, str]) -> list[Finding]:
    found = []
    for name, value in observed.items():
        pinned = lock["components"].get(name, {}).get("value")
        if pinned == value:
            found.append(Finding("versions", "ok", f"{name} {value} matches versions.lock.yaml"))
        else:
            found.append(Finding("versions", "info", f"{name} is {value} here, the lock records {pinned} from the M1 verification host"))
    return found


def address_findings(address_plan: dict, nodes: dict[str, dict], sources: list[host.Source]) -> list[Finding]:
    found = [Finding("address plan", "fail", p) for p in plan.plan_problems(address_plan, nodes)]
    spans = [s for source in sources for s in source.spans]
    for source in sources:
        if source.error:
            found.append(Finding("address plan", "warn", f"{source.name} not checked: {source.error}"))
        else:
            found.append(Finding("address plan", "ok", f"read {len(source.spans)} ranges from {source.name}"))
    in_use = sorted({str(n) for s in spans if s.version == 4 for n in plan.networks(s)}, key=_sort_key)
    found.append(Finding("address plan", "info", f"host IPv4 ranges in use: {', '.join(in_use)}"))
    clashes = plan.overlaps(address_plan, spans)
    found += [Finding("address plan", "fail", f"{mine.label} overlaps {theirs.label}") for mine, theirs in clashes]
    if not clashes:
        ranges = ", ".join(f"{k} {address_plan[k]}" for k in plan.PLAN_KEYS)
        found.append(Finding("address plan", "ok", f"no host range overlaps {ranges}"))
    return found


def _sort_key(cidr: str) -> tuple[int, ...]:
    address, _, prefix = cidr.partition("/")
    return (*(int(o) for o in address.split(".")), int(prefix or 32))


def report(findings: list[Finding]) -> str:
    lines, section = [], None
    for f in findings:
        if f.section != section:
            section = f.section
            lines.append(section)
        lines.append(f"  [{f.level}]{' ' * (5 - len(f.level))}{f.message}")
    failed = sum(f.level == "fail" for f in findings)
    warned = sum(f.level == "warn" for f in findings)
    lines.append(f"doctor: {failed} failed, {warned} warnings")
    return "\n".join(lines)


def exit_code(findings: list[Finding]) -> int:
    return 1 if any(f.level == "fail" for f in findings) else 0


def _inventory() -> tuple[dict, dict[str, dict]]:
    out = subprocess.run(
        ["ansible-inventory", "-i", str(LAB_INVENTORY), "--list"], capture_output=True, text=True, check=True
    )
    hostvars = json.loads(out.stdout)["_meta"]["hostvars"]
    return plan.from_hostvars(hostvars)


def _observed_host() -> dict[str, str]:
    os_name = platform.system()
    for line in Path("/etc/os-release").read_text().splitlines():
        if line.startswith("PRETTY_NAME="):
            os_name = line.split("=", 1)[1].strip('"')
    observed = {"host.os": os_name, "host.kernel": platform.release()}
    qemu = subprocess.run(["dpkg-query", "-W", "-f", "${Version}", "qemu-system-x86"], capture_output=True, text=True)
    if qemu.returncode == 0 and qemu.stdout:
        observed["host.hypervisor"] = f"qemu-system-x86 {qemu.stdout} with KVM"
    return observed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="doctor", description=__doc__)
    parser.add_argument("--plan", type=Path, help="YAML file whose lab_address_plan replaces the inventory one")
    args = parser.parse_args(argv)

    address_plan, nodes = _inventory()
    if args.plan:
        address_plan |= yaml.safe_load(args.plan.read_text())["lab_address_plan"]

    hypervisors = [n for n in ("vmrun", "vmware", "qemu-system-x86_64") if shutil.which(n)]
    stat = os.statvfs(REPO_ROOT)
    mem_available_kib = next(
        int(line.split()[1]) for line in Path("/proc/meminfo").read_text().splitlines() if line.startswith("MemAvailable:")
    )
    findings = (
        virtualization_findings(
            Path("/proc/cpuinfo").read_text(),
            os.access("/dev/kvm", os.R_OK | os.W_OK),
            os.path.exists("/dev/kvm"),
            hypervisors,
        )
        + tool_findings(shutil.which, _first_line)
        + resource_findings(
            os.cpu_count() or 0, mem_available_kib * 1024 / GIB, stat.f_bavail * stat.f_frsize / GIB, str(REPO_ROOT)
        )
        + lock_findings(yaml.safe_load(LOCK.read_text()), _observed_host())
        + address_findings(address_plan, nodes, host.collect())
    )
    print(report(findings))
    return exit_code(findings)


if __name__ == "__main__":
    sys.exit(main())
