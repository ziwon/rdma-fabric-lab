"""make netbox-test: the cel1 site model in the NetBox guest (ADR 0008), recorded through the rfl CLI.

    python -m run.netbox -i tests/integration/qemu-lab/profiles/netbox/inventory -i <netbox inventory.yml>

The run reads the superuser's API token from the guest over SSH and keeps it in memory. It syncs, compares, syncs again,
makes three changes by hand and checks that the comparison names exactly them and the next sync undoes them, and it
keeps one address of its own without the managed tag, which every sync must leave alone. Then it lays out the topology
drawing of the model and reads back what the topology views plugin draws.
"""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
from pathlib import Path

import yaml

from fleet import site as site_model
from netbox import client as nb
from netbox import model, reconcile, topology

from . import rfl
from .base import Outcome, RunBase, failed, passed, write_summary
from .inventory import ssh_argv

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIO = "cel1-netbox"
SPEC = REPO_ROOT / "tracks" / "cel1" / "scenarios" / f"{SCENARIO}.yaml"
CONFIG = REPO_ROOT / "tracks" / "cel1" / "config" / f"{SCENARIO}.yaml"
LOCK = REPO_ROOT / "versions.lock.yaml"


def check_ids() -> tuple[str, ...]:
    return tuple(c["id"] for c in yaml.safe_load(SPEC.read_text())["checks"])


def locked_version(lock: dict) -> str:
    """The NetBox version in the tag of netbox.image, v4.7.5-5.1.2 -> 4.7.5."""
    return lock["netbox.image"]["ref"].rsplit(":", 1)[1].split("-")[0].removeprefix("v")


def locked_plugin(lock: dict) -> str:
    return str(lock["netbox.topology_views"]["ref"])


def expected_drift(config: dict) -> list[tuple[str, str, str]]:
    return sorted([("create", "cable", config["drift_cable"]), ("update", "ip", config["drift_address"]),
                   ("delete", "ip", config["stray_address"])])


def drift_problems(changes: list[reconcile.Change], config: dict) -> list[str]:
    got = sorted((c.action, c.kind, c.key) for c in changes)
    want = expected_drift(config)
    problems = [f"the comparison does not name {' '.join(w)}" for w in want if w not in got]
    problems += [f"the comparison also names {' '.join(g)}" for g in got if g not in want]
    return problems


class Run(RunBase):
    def __init__(self, inventories: list[Path], config_path: Path, run_id: str | None):
        super().__init__(SPEC, run_id)
        self.inventories, self.config_path = inventories, config_path
        self.config = yaml.safe_load(config_path.read_text())
        self.site = site_model.load(REPO_ROOT / self.config["site"])
        self.wanted = model.desired(self.site)
        self.lock = yaml.safe_load(LOCK.read_text())["components"]
        start = time.monotonic()
        token = nb.token(nb.secrets(inventories))
        self.log.write("netbox1", f"sudo cat {nb.SECRETS}  (the token, kept in memory)", 0, time.monotonic() - start)
        self.client = nb.Client(self.config["url"], token)
        self.client.on_request = lambda method, path, status, seconds: self.log.write(
            "netbox-api", f"{method} /api/{path}", 0 if 200 <= status < 300 else (status or 1), seconds)
        self.hand_made_id: int | None = None

    def save(self, rel: str, data) -> str:
        return self.raw(rel, json.dumps(data, indent=1, sort_keys=True, default=str) + "\n")

    def synced_model(self) -> dict:
        """The model a sync of this run applies: the whole one, or without the kinds a break skips."""
        skips = set(self.config.get("sync_skips") or [])
        return {k: ({} if k in skips else v) for k, v in self.wanted.items()}

    def sync(self, rel: str) -> tuple[dict, list[reconcile.Change], str]:
        plan = reconcile.plan(self.client, self.synced_model())
        done = reconcile.apply(self.client, self.synced_model(), plan, prune=self.config.get("prune", True))
        path = self.save(rel, {"applied": done, "plan": [str(c) for c in plan.changes]})
        return done, plan.changes, path

    def diff(self, rel: str) -> tuple[list[reconcile.Change], str]:
        changes = reconcile.plan(self.client, self.wanted).changes
        return changes, self.save(rel, [str(c) for c in changes])


def describe(run: Run) -> None:
    status = run.client.status()
    hostvars = nb.guest_hostvars(run.inventories)
    done = subprocess.run([*ssh_argv(hostvars), '. /etc/os-release; echo "$PRETTY_NAME"; uname -r'], capture_output=True, text=True,
                          timeout=60)
    lines = done.stdout.splitlines() + ["", ""]
    rfl.set_section(run.dir, "environment", {"guests": [{"name": hostvars.get("inventory_hostname", "netbox1"), "os": lines[0],
                                                         "kernel": lines[1]}],
                                             "tools": {"netbox": status.get("netbox-version"),
                                                       "python": status.get("python-version"),
                                                       "netbox image": f"{run.lock['netbox.image']['ref']}@{run.lock['netbox.image']['value']}",
                                                       "netbox-topology-views": (status.get("plugins") or {}).get(
                                                           "netbox_topology_views"),
                                                       "postgres image": run.lock["netbox.postgres_image"]["ref"],
                                                       "valkey image": run.lock["netbox.valkey_image"]["ref"]}})
    (run.dir / "resolved-config.yaml").write_text(yaml.safe_dump(
        {"run_id": run.run_id, "scenario": SCENARIO, "config": run.config, "inventories": [str(i) for i in run.inventories],
         "model_counts": model.counts(run.wanted)}, sort_keys=False))
    rfl.add_inputs(run.dir, [run.config_path, LOCK, REPO_ROOT / run.config["site"]])
    rfl.set_section(run.dir, "inputs", {"parameters": {"url": run.config["url"], "objects": sum(model.counts(run.wanted).values()),
                                                        "prune": run.config.get("prune", True),
                                                        "sync_skips": run.config.get("sync_skips") or [],
                                                        "skip_topology": run.config.get("skip_topology", False)}})


def check_version(run: Run) -> Outcome:
    status = run.client.status()
    evidence = [run.save("netbox-is-the-locked-version/status.json", status)]
    want, got = locked_version(run.lock), status.get("netbox-version")
    if got != want:
        return failed("assertion-failed", evidence, f"NetBox reports {got}, the lock pins {want}")
    return passed(evidence, f"NetBox {got} answers with the superuser's token, as netbox.image pins")


def add_hand_made(run: Run) -> list[str]:
    body = {"address": run.config["hand_made_address"], "status": "reserved", "description": f"added by hand, run {run.run_id}"}
    if run.config.get("hand_made_tagged"):
        body["tags"] = [run.client.list("extras/tags", {"slug": model.TAG})[0]["id"]]
    made = run.client.create("ipam/ip-addresses", [body])[0]
    run.hand_made_id = made["id"]
    return [run.save("hand-made-objects-stay/created.json", made)]


def count_problems(run: Run) -> tuple[list[str], dict]:
    existing, _, orphans = reconcile.read(run.client)
    have = {k: len([key for key in v if not key.startswith("#")]) for k, v in existing.items()}
    want = model.counts(run.wanted)
    problems = [f"NetBox holds {have[k]} managed {k} objects, the model {want[k]}" for k in model.KINDS if have[k] != want[k]]
    problems += [str(o) for o in orphans]
    return problems, have


def check_sync(run: Run) -> Outcome:
    done, _, path = run.sync("sync-makes-netbox-hold-the-model/sync-1.json")
    problems, have = count_problems(run)
    evidence = [path, run.save("sync-makes-netbox-hold-the-model/counts.json", have)]
    return failed("assertion-failed", evidence, "; ".join(problems)) if problems else passed(
        evidence, f"applied {done}; NetBox holds {sum(have.values())} managed objects {have}")


def check_equal(run: Run, check_id: str) -> Outcome:
    changes, path = run.diff(f"{check_id}/diff.json")
    if changes:
        return failed("assertion-failed", [path], f"{len(changes)} differences, first: {'; '.join(str(c) for c in changes[:3])}")
    return passed([path], "0 differences between NetBox and the site model")


def check_second(run: Run) -> Outcome:
    done, changes, path = run.sync("second-sync-changes-nothing/sync-2.json")
    if any(done.values()):
        return failed("assertion-failed", [path], f"the second sync applied {done}: {'; '.join(str(c) for c in changes[:3])}")
    return passed([path], f"the second sync applied {done}")


def make_drift(run: Run) -> str:
    c, cfg = run.client, run.config
    a, b = cfg["drift_cable"].split(" = ")
    existing, _, _ = reconcile.read(c, ("device", "interface", "cable"))
    cable = existing["cable"].get(model.cable_key(a, b))
    ip = c.list("ipam/ip-addresses", {"address": cfg["drift_address"]})[0]
    tag = c.list("extras/tags", {"slug": model.TAG})[0]
    if cable:
        c.delete("dcim/cables", [{"id": cable["id"]}])
    c.update("ipam/ip-addresses", [{"id": ip["id"], "description": f"edited by hand, run {run.run_id}"}])
    c.create("ipam/ip-addresses", [{"address": cfg["stray_address"], "status": "active", "tags": [tag["id"]]}])
    return run.save("drift-is-found-exactly/made.json", {"deleted_cable": cable and cable["id"], "edited_address": ip["id"],
                                                         "stray_address": cfg["stray_address"]})


def check_drift(run: Run) -> Outcome:
    made = make_drift(run)
    changes, path = run.diff("drift-is-found-exactly/diff.json")
    problems = drift_problems(changes, run.config)
    if problems:
        return failed("assertion-failed", [made, path], "; ".join(problems))
    return passed([made, path], "; ".join(str(c) for c in sorted(changes, key=lambda c: (c.action, c.kind))))


def check_undo(run: Run) -> Outcome:
    done, _, path = run.sync("sync-undoes-the-drift/sync-3.json")
    changes, dpath = run.diff("sync-undoes-the-drift/diff.json")
    problems = [] if done == {"create": 1, "update": 1, "delete": 1} else [f"the sync applied {done}, want one of each"]
    problems += [f"{len(changes)} differences left: {'; '.join(str(c) for c in changes[:3])}"] if changes else []
    return failed("assertion-failed", [path, dpath], "; ".join(problems)) if problems else passed(
        [path, dpath], f"the sync applied {done}; 0 differences left")


def check_topology(run: Run) -> Outcome:
    status = run.client.status()
    got, want = (status.get("plugins") or {}).get("netbox_topology_views"), locked_plugin(run.lock)
    evidence = [run.save("topology-draws-the-model/plugins.json", status.get("plugins"))]
    if got != want:
        return failed("assertion-failed", evidence, f"netbox_topology_views is {got}, the lock pins {want}")
    if not run.config.get("skip_topology"):
        done = topology.apply(run.client, run.site)
        evidence.append(run.save("topology-draws-the-model/layout.json", {"applied": str(done)}))
    site_id = run.client.list("dcim/sites", {"slug": run.site.name})[0]["id"]
    problems = topology.problems(run.client, run.site, site_id)
    g = topology.group(run.client)
    if g is not None:
        nodes, pairs = topology.drawn(run.client, site_id, g["id"])
        evidence.append(run.save("topology-draws-the-model/drawn.json", {
            "devices": sorted(nodes), "cables": {f"{a} - {b}": n for (a, b), n in sorted(pairs.items())}}))
    evidence.append(run.save("topology-draws-the-model/problems.json", problems))
    if problems:
        return failed("assertion-failed", evidence, f"{len(problems)} problems, first: {'; '.join(problems[:3])}")
    return passed(evidence, f"plugin {got}; {len(nodes)} devices at their layout coordinates and {sum(pairs.values())} "
                            f"cables in {len(pairs)} device pairs drawn, as the model has them")


def check_hand_made(run: Run, created: list[str]) -> Outcome:
    found = run.client.list("ipam/ip-addresses", {"address": run.config["hand_made_address"]})
    evidence = created + [run.save("hand-made-objects-stay/after.json", found)]
    mine = [f for f in found if f["id"] == run.hand_made_id]
    if not mine:
        return failed("assertion-failed", evidence, f"{run.config['hand_made_address']}, added by hand as #{run.hand_made_id}, "
                                                    "is gone after the syncs")
    return passed(evidence, f"{run.config['hand_made_address']} #{run.hand_made_id} without the managed tag survived 3 syncs")


def check_removed(run: Run) -> Outcome:
    if run.hand_made_id is not None and not run.config.get("keep_hand_made"):
        try:
            run.client.delete("ipam/ip-addresses", [{"id": run.hand_made_id}])
        except nb.NetBoxError:
            pass
    left = run.client.list("ipam/ip-addresses", {"address": run.config["hand_made_address"]})
    changes, path = run.diff("run-objects-removed/diff.json")
    evidence = [run.save("run-objects-removed/left.json", left), path]
    problems = [f"{run.config['hand_made_address']} is still there"] if left else []
    problems += [f"{len(changes)} differences from the model"] if changes else []
    return failed("assertion-failed", evidence, "; ".join(problems)) if problems else passed(
        evidence, f"{run.config['hand_made_address']} removed, 0 differences from the model")


def suite(run: Run) -> None:
    version = check_version(run)
    run.record("netbox-is-the-locked-version", version)
    created = add_hand_made(run)
    try:
        run.record("sync-makes-netbox-hold-the-model", check_sync(run))
        run.record("netbox-equals-the-model", check_equal(run, "netbox-equals-the-model"))
        run.record("second-sync-changes-nothing", check_second(run))
        run.record("drift-is-found-exactly", check_drift(run))
        run.record("sync-undoes-the-drift", check_undo(run))
        run.record("topology-draws-the-model", check_topology(run))
        run.record("hand-made-objects-stay", check_hand_made(run, created))
    finally:
        run.record("run-objects-removed", check_removed(run))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run.netbox", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-i", "--inventory", type=Path, action="append", required=True)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    run = Run(args.inventory, args.config, args.run_id)
    run.say(f"run {run.run_id} scenario {SCENARIO} in {run.dir}, {sum(model.counts(run.wanted).values())} objects")
    try:
        describe(run)
        suite(run)
    except KeyboardInterrupt:
        run.warn("interrupted")
    except Exception as exc:
        run.warn(f"runner error: {exc!r}")
        raise
    finally:
        rfl.set_section(run.dir, "timing", {"wall_clock_seconds": round(time.monotonic() - run.started, 1)})
        code, message = rfl.finalize(run.dir)
        write_summary(run)
        run.say(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
