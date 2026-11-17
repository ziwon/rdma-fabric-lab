"""make collect: record the state of the host and the lab workers as a validated run.

It compares every lab.* value of versions.lock.yaml with what the workers report, and keeps the
inventory variables of each worker as Ansible resolves them. It reads no key, no credential and no
kubeconfig. Every file it writes passes a scan for credential text first, and a file that fails the
scan is replaced by a note that it was withheld.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

import yaml

from rfl.versions import observed_tooling
from run import rfl
from run.inventory import guests_from_listing
from run.remote import CommandLog, Remote

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC = REPO_ROOT / "tracks" / "a-soft-roce" / "scenarios" / "vm-environment.yaml"
LOCK = REPO_ROOT / "versions.lock.yaml"

# name -> guest command. Their output is the snapshot; nothing here writes to the guest.
GUEST_STATE = {
    "os-release": "cat /etc/os-release",
    "uname": "uname -a",
    "ip-addr": "ip -j addr show",
    "ip-route": "ip -j route show table all",
    "rdma-link": "rdma -j link show",
    "rdma-system": "rdma system show",
    "ibv-devinfo": "ibv_devinfo -v",
    "netplan-rdma": "sudo cat /etc/netplan/60-rfl-rdma.yaml",
    "rxe-env": "cat /etc/rfl/rxe.env",
    "rxe-unit": "systemctl status rfl-rxe.service --no-pager",
    "rxe-journal": "sudo journalctl -b -u rfl-rxe.service --no-pager -o short-iso",
    "modules-load": "cat /etc/modules-load.d/rfl-rdma_rxe.conf",
    "held-packages": "apt-mark showhold",
    "lscpu": "lscpu",
    "memory": "free -m",
    "time-sync": "timedatectl show",
}
HOST_STATE = {
    "uname": ["uname", "-a"],
    "os-release": ["cat", "/etc/os-release"],
    "nproc": ["nproc"],
    "memory": ["free", "-m"],
    "qemu": ["qemu-system-x86_64", "--version"],
}


# Inventory variables collect keeps: the lab contract by prefix, and how the host reaches each worker.
RESOLVED_PREFIXES = ("lab_", "k8s_", "rfl_")
CONNECTION_VARIABLES = ("ansible_host", "ansible_port", "ansible_user")
# A variable whose name says it holds or points at a credential is never written, whatever its prefix.
CREDENTIAL_NAME = re.compile(r"pass|secret|token|private_key|key_file|kubeconfig|credential|cert", re.IGNORECASE)
# Text that only a credential carries. Labels, so a finding never repeats any part of the value.
CREDENTIAL_TEXT = {
    "PEM private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "kubeconfig client credential": re.compile(r"client-(key|certificate)-data:"),
    "token value": re.compile(r"^\s*token:\s*\S", re.MULTILINE),
    "password value": re.compile(r"password\s*[:=]\s*\S", re.IGNORECASE),
}


def resolved_variables(hostvars: dict) -> dict:
    return {k: v for k, v in sorted(hostvars.items())
            if (k.startswith(RESOLVED_PREFIXES) or k in CONNECTION_VARIABLES) and not CREDENTIAL_NAME.search(k)}


def credential_markers(text: str) -> list[str]:
    return [label for label, pattern in CREDENTIAL_TEXT.items() if pattern.search(text)]


def observed_versions(report: str, packages: list[dict]) -> dict[str, list[str]]:
    """Lock component -> values a guest reported, from the output of GUEST_REPORT."""
    os_name, kernel, build, *rows = report.splitlines() + ["", "", ""]
    observed: dict[str, list[str]] = {"lab.guest_os": [os_name], "lab.guest_kernel": [kernel]}
    if build.startswith("serial: "):
        observed["lab.guest_image"] = [build.split(": ", 1)[1].strip()]
    installed = dict(r.split("=", 1) for r in rows if "=" in r)
    for p in packages:
        if "lock" in p:
            observed.setdefault(p["lock"], []).append(installed.get(p["name"], f"{p['name']} not installed"))
    return observed


GUEST_REPORT = (
    ". /etc/os-release; echo \"$PRETTY_NAME\"; uname -r; grep -h '^serial:' /etc/cloud/build.info 2>/dev/null || echo; "
    "dpkg-query -W -f '${Package}=${Version}\\n' rdma-core ibverbs-providers ibverbs-utils rdmacm-utils perftest 2>/dev/null"
)


def lock_rows(lock: dict, observed: dict[str, list[str]]) -> list[dict]:
    """One row per guest component of scope m1: the locked value, what each guest reported, and whether all agree."""
    rows = []
    for name in sorted(c for c in lock["scopes"]["m1"] if c.startswith("lab.")):
        component = lock["components"][name]
        want = component["value"]
        if name == "lab.guest_image":
            want = component.get("ref", "").rpartition("/")[2] or None
        seen = observed.get(name, [])
        rows.append({"component": name, "locked": want, "observed": seen, "agrees": bool(seen) and all(v == want for v in seen)})
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="collect", description=__doc__)
    parser.add_argument("-i", "--inventory", type=Path, action="append", required=True)
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)

    started = time.monotonic()
    run_dir = rfl.init(SPEC, args.run_id)
    run_id = run_dir.parts[-3]
    log = CommandLog(run_dir / "commands.log")
    listing_argv = ["ansible-inventory", "--list"] + [a for i in args.inventory for a in ("-i", str(i))]
    listing = json.loads(subprocess.run(listing_argv, capture_output=True, text=True, check=True).stdout)
    guests = guests_from_listing(listing)
    packages = listing["_meta"]["hostvars"][next(iter(guests))]["lab_packages"]
    lock = yaml.safe_load(LOCK.read_text())
    remotes = {name: Remote(g, run_id, log) for name, g in guests.items()}

    scanned: list[str] = []
    withheld: list[str] = []

    def keep(path: Path, text: str) -> None:
        rel = str(path.relative_to(run_dir))
        found = credential_markers(text)
        scanned.append(rel)
        if found:
            withheld.append(f"{rel}: {', '.join(found)}")
            text = f"withheld: this snapshot carried text shaped like a credential ({', '.join(found)}), so collect did not keep it\n"
        path.write_text(text)

    def raw(rel: str, text: str) -> str:
        path = run_dir / "raw" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        keep(path, text)
        return f"raw/{rel}"

    hostvars = listing["_meta"]["hostvars"]
    keep(run_dir / "resolved-config.yaml", yaml.safe_dump(
        {"run_id": run_id, "inventories": [str(i) for i in args.inventory], "guest_state": GUEST_STATE,
         "host_state": {k: " ".join(v) for k, v in HOST_STATE.items()},
         "variables": {name: resolved_variables(hostvars[name]) for name in guests}}, sort_keys=False))
    rfl.add_inputs(run_dir, [LOCK])

    observed: dict[str, list[str]] = {}
    env_guests = []
    for name, remote in remotes.items():
        done = remote.sh(GUEST_REPORT)
        raw(f"versions/{name}.txt", done.stdout)
        for component, values in observed_versions(done.stdout, packages).items():
            observed.setdefault(component, []).extend(values)
        lines = done.stdout.splitlines()
        if len(lines) >= 2:
            env_guests.append({"name": name, "os": lines[0], "kernel": lines[1]})
    rfl.set_section(run_dir, "environment", {"guests": env_guests})

    rows = lock_rows(lock, observed)
    evidence = [raw("versions/lock-comparison.json", json.dumps(rows, indent=2) + "\n")] + [f"raw/versions/{n}.txt" for n in remotes]
    image = next(r for r in rows if r["component"] == "lab.guest_image")
    others = [r for r in rows if r["component"] != "lab.guest_image"]
    bad = [f"{r['component']} locked {r['locked']}, guests report {r['observed']}" for r in others if not r["agrees"]]
    rfl.check(run_dir, "guest-versions-match-lock", "failed" if bad else "passed",
              failure_class="assertion-failed" if bad else None, evidence=evidence,
              detail="; ".join(bad) if bad else ", ".join(f"{r['component']}={r['locked']}" for r in others))
    if not image["observed"]:
        rfl.check(run_dir, "guest-image-build", "blocked", failure_class="environment-blocked", evidence=evidence,
                  detail="the guests carry no /etc/cloud/build.info, so they cannot report their image build")
    else:
        rfl.check(run_dir, "guest-image-build", "passed" if image["agrees"] else "failed",
                  failure_class=None if image["agrees"] else "assertion-failed", evidence=evidence,
                  detail=f"build.info serial {image['observed']} against lab.guest_image ref {image['locked']}")

    failures, state_evidence = [], []
    for name, remote in remotes.items():
        for key, command in GUEST_STATE.items():
            done = remote.sh(command)
            state_evidence.append(raw(f"state/{name}/{key}.txt", f"$ {command}\nrc={done.rc}\n{done.stdout}{done.stderr}"))
            if done.rc != 0:
                failures.append(f"{name} {key} exit {done.rc}")
    rfl.check(run_dir, "guest-state-collected", "failed" if failures else "passed",
              failure_class="command-failed" if failures else None, evidence=state_evidence,
              detail="; ".join(failures) if failures else f"{len(GUEST_STATE)} snapshots from each of {', '.join(remotes)}")

    host_evidence, host_failures = [], []
    for key, command in HOST_STATE.items():
        try:
            out = subprocess.run(command, capture_output=True, text=True, timeout=30)
            rc, text = out.returncode, out.stdout + out.stderr
        except OSError as exc:
            rc, text = 127, str(exc)
        log.write("control", " ".join(command), rc, 0.0)
        host_evidence.append(raw(f"state/host/{key}.txt", f"$ {' '.join(command)}\nrc={rc}\n{text}"))
        if rc != 0:
            host_failures.append(f"{key} exit {rc}")
    host_evidence.append(raw("state/host/tooling.json", json.dumps(observed_tooling(), indent=2) + "\n"))
    rfl.check(run_dir, "host-state-collected", "failed" if host_failures else "passed",
              failure_class="command-failed" if host_failures else None, evidence=host_evidence,
              detail="; ".join(host_failures) if host_failures else f"kernel {platform.release()}, {len(HOST_STATE)} snapshots")

    scan = raw("credential-scan.txt", f"{len(scanned)} files scanned for {', '.join(CREDENTIAL_TEXT)}\n"
               + "".join(f"withheld {w}\n" for w in withheld) + "".join(f"kept {r}\n" for r in scanned))
    rfl.check(run_dir, "no-credential-collected", "failed" if withheld else "passed",
              failure_class="assertion-failed" if withheld else None, evidence=[scan],
              detail=f"withheld {len(withheld)} of {len(scanned)} files: {'; '.join(withheld)}" if withheld
              else f"{len(scanned)} files scanned, none carried credential text")

    rfl.set_section(run_dir, "timing", {"wall_clock_seconds": round(time.monotonic() - started, 1)})
    code, message = rfl.finalize(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    (run_dir / "summary.md").write_text(
        f"# a-soft-roce / vm-environment / {run_id}\n\nStatus: **{manifest['status']}**.\n\n"
        "| component | locked | observed | agrees |\n|---|---|---|---|\n"
        + "".join(f"| {r['component']} | {r['locked']} | {', '.join(sorted(set(r['observed'])))} | {r['agrees']} |\n" for r in rows)
        + "\n| check | status | detail |\n|---|---|---|\n"
        + "".join(f"| {c['id']} | {c['status']} | {(c['detail'] or '').replace('|', '/')} |\n" for c in manifest["checks"])
    )
    print(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
