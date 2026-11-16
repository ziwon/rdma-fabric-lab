"""python -m vagrant: the Vagrant lab on VMware Workstation. mk/vagrant.mk calls every subcommand.

    plan       the JSON the Vagrantfile reads: box, SSH identity, sizes, MACs and addresses
    seeds      SSH keys, known_hosts and one NoCloud seed ISO per guest under --state
    box        build the VMware box from lab.guest_image under --data. Needs no VMware
    box-add    add that box to Vagrant unless the same bytes are already there
    check      print what a target of kind --need lacks. Exits 2 when it must refuse
    inventory  write the override inventory from `vagrant ssh-config` and the NIC names found by MAC
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

from run.inventory import ssh_argv

from . import box, plan, preflight

LOCK = plan.REPO_ROOT / "versions.lock.yaml"
LAB_INVENTORY = plan.REPO_ROOT / "ansible" / "inventories" / "lab"
READY_TIMEOUT_S = 600


def say(message: str) -> None:
    print(f"vagrant: {message}", file=sys.stderr)


def load_lock() -> dict:
    return yaml.safe_load(LOCK.read_text())


def listing() -> dict:
    env = dict(os.environ, ANSIBLE_CONFIG=str(plan.REPO_ROOT / "ansible" / "ansible.cfg"))
    out = subprocess.run([str(Path(sys.executable).parent / "ansible-inventory"), "-i", str(LAB_INVENTORY), "--list"],
                         capture_output=True, text=True, check=True, env=env)
    return json.loads(out.stdout)


def lab_model() -> tuple[object, dict, list[plan.Machine]]:
    profile = plan.vmware_profile(plan.harness().parse(plan.PROFILE.read_text()))
    hostvars = listing()["_meta"]["hostvars"]
    lab_cidr = hostvars[profile.guests[0].name]["lab_address_plan"]["rdma"]
    return profile, hostvars, plan.machines(profile, hostvars, lab_cidr)


def ensure_keypair(path: Path, comment: str) -> None:
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(path)], check=True)


def write_if_changed(path: Path, text: str) -> bool:
    if path.is_file() and path.read_text() == text:
        return False
    path.write_text(text)
    return True


def cmd_plan(args) -> int:
    _, _, machine_list = lab_model()
    print(json.dumps(plan.vagrantfile_plan(machine_list, box.box_name(load_lock()), str(Path(args.state).resolve())), indent=2))
    return 0


def cmd_seeds(args) -> int:
    state = Path(args.state).resolve()
    profile, hostvars, _ = lab_model()
    harness = plan.harness()
    ensure_keypair(state / "ssh" / "id_ed25519", "rfl-vagrant")
    authorized = (state / "ssh" / "id_ed25519.pub").read_text()
    host_keys = {}
    for guest in profile.guests:
        d = state / "guests" / guest.name
        ensure_keypair(d / "ssh_host_ed25519_key", f"root@{guest.name}")
        host_keys[guest.name] = (d / "ssh_host_ed25519_key.pub").read_text()
        files = {
            "user-data": plan.user_data(guest.name, authorized, (d / "ssh_host_ed25519_key").read_text(), host_keys[guest.name]),
            "meta-data": plan.meta_data(guest.name),
            "network-config": yaml.safe_dump(harness.network_config(profile, guest, hostvars[guest.name]), sort_keys=False),
        }
        changed = [name for name, text in files.items() if write_if_changed(d / name, text)]
        iso = Path(plan.seed_path(str(state), guest.name))
        if changed or not iso.is_file():
            subprocess.run(["cloud-localds", "-N", str(d / "network-config"), str(iso), str(d / "user-data"), str(d / "meta-data")],
                           check=True)
            say(f"wrote {iso}")
    write_if_changed(state / "ssh" / "known_hosts", plan.known_hosts(host_keys))
    return 0


def cmd_box(args) -> int:
    try:
        box.build(load_lock(), Path(args.data), say)
    except box.BoxError as exc:
        say(str(exc))
        return 1
    return 0


def cmd_box_add(args) -> int:
    name = box.box_name(load_lock())
    where = box.paths(Path(args.data), name)
    if not where["box"].is_file():
        say(f"{where['box']} does not exist. Run make vagrant-box first.")
        return 2
    digest = where["sha256"].read_text().split()[0]
    record = Path(os.environ["VAGRANT_HOME"]) / f"rfl-{name}.sha256"
    listed = subprocess.run(["vagrant", "box", "list", "--machine-readable"], capture_output=True, text=True, check=True).stdout
    if (name, box.PROVIDER) in preflight.boxes(listed) and record.is_file() and record.read_text().strip() == digest:
        say(f"box {name} is added with sha256 {digest}")
        return 0
    subprocess.run(["vagrant", "box", "add", "--force", "--name", name, "--provider", box.PROVIDER,
                    "--checksum-type", "sha256", "--checksum", digest, str(where["box"])], check=True)
    record.write_text(digest + "\n")
    return 0


def cmd_check(args) -> int:
    lock = load_lock()["components"]
    pins = {k: lock[k]["value"] for k in ("vagrant.vagrant", "vagrant.vmware_desktop_plugin")}
    facts = preflight.collect()
    found = preflight.report(facts, args.need, pins)
    for name, problem in found:
        print(f"[refuse] {problem}" if problem else f"[ok] {name}", file=sys.stderr)
    for warning in preflight.warnings(facts, args.need):
        print(f"[warn] {warning}", file=sys.stderr)
    if any(problem for _, problem in found):
        print(f"make {args.target} refuses. Nothing was changed.", file=sys.stderr)
        return 2
    return 0


def _ssh(entry: dict, command: str, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run([*ssh_argv(entry), command], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)


def wait_ready(name: str, entry: dict) -> None:
    deadline = time.monotonic() + READY_TIMEOUT_S
    while _ssh(entry, "true", 30).returncode != 0:
        if time.monotonic() > deadline:
            raise plan.PlanError(f"{name}: no SSH after {READY_TIMEOUT_S} s. See serial.log in its machine folder.")
        time.sleep(3)
    if _ssh(entry, f"timeout {READY_TIMEOUT_S} cloud-init status --wait", READY_TIMEOUT_S + 30).returncode != 0:
        raise plan.PlanError(f"{name}: cloud-init did not finish cleanly")


def cmd_inventory(args) -> int:
    state = Path(args.state).resolve()
    profile, _, _ = lab_model()
    text = subprocess.run(["vagrant", "ssh-config"], capture_output=True, text=True, check=True).stdout
    hosts = plan.ssh_config_hosts(text)
    known = str(state / "ssh" / "known_hosts")
    connections = {g.name: plan.connection(g.name, hosts.get(g.name, {}), known) for g in profile.guests}
    discovered = {}
    for guest in profile.guests:
        wait_ready(guest.name, connections[guest.name])
        links = json.loads(_ssh(connections[guest.name], "ip -j link show", 30).stdout)
        discovered[guest.name] = {link["address"]: link["ifname"] for link in links if "address" in link}
        say(f"{guest.name} ready")
    inventory = plan.override_inventory(profile, connections, discovered)
    out = state / "inventory.yml"
    header = (f"# Written by make vagrant-up. Pass it as LAB_OVERRIDE={os.path.relpath(out, plan.REPO_ROOT)}, "
              f"the second -i after {profile.inventory}.\n")
    tmp = out.with_suffix(".tmp")
    tmp.write_text(header + yaml.safe_dump(inventory, sort_keys=False))
    tmp.rename(out)
    say(f"wrote {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m vagrant", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "seeds", "inventory"):
        sub.add_parser(name).add_argument("--state", required=True, help="small state: keys, seeds, the override inventory")
    for name in ("box", "box-add"):
        sub.add_parser(name).add_argument("--data", required=True, help="large files: the image download and the box")
    check = sub.add_parser("check")
    check.add_argument("--need", required=True, choices=sorted(preflight.NEEDS))
    check.add_argument("--target", required=True, help="the make target to name in the refusal")
    args = parser.parse_args(argv)
    handlers = {"plan": cmd_plan, "seeds": cmd_seeds, "box": cmd_box, "box-add": cmd_box_add, "check": cmd_check,
                "inventory": cmd_inventory}
    try:
        return handlers[args.command](args)
    except plan.PlanError as exc:
        say(str(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
