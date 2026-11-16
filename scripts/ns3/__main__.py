"""Track C, the ns-3-alibabacloud packet model.

    python -m ns3 image -i <inventory> -i <override> [--clean] [--log <file>] [--archive <file>]
    python -m ns3 run --scenario c-02-single-flow -i <inventory> -i <override> [--run-id <id>] [--break <name>]
    python -m ns3 breaks -i <inventory> -i <override> [--scenario <name>]...

Everything that builds or runs the simulator, and every container, runs inside the Track C guest.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import yaml

from run.remote import CommandLog

from . import guest, run

# Build argument of images/ns3/Dockerfile -> lock component that supplies it.
BUILD_ARGS = {
    "APT_SNAPSHOT": "ns3.apt_snapshot",
    "SIMAI_COMMIT": "ns3.simai",
    "NS3_COMMIT": "ns3.ns3_alibabacloud",
    "SIMCCL_COMMIT": "ns3.simccl",
    "SOURCE_DATE_EPOCH": "ns3.source_date_epoch",
}


def base_image(lock: dict) -> str:
    base = lock["components"]["ns3.base_image"]
    return f"{base['ref']}@{base['value']}"


def build_args(lock: dict) -> list[str]:
    components = lock["components"]
    return [f"BASE_IMAGE={base_image(lock)}"] + [f"{arg}={components[name]['value']}" for arg, name in BUILD_ARGS.items()]


def _cmd_image(args: argparse.Namespace) -> int:
    lock = yaml.safe_load(run.LOCK.read_text())
    target = guest.load(args.inventory, CommandLog(None))
    steps = [target.sync()]
    if args.clean:
        steps.append(target.guest_sh("clean", run.IMAGE_TAG, base_image(lock), timeout_s=600))
    src = guest.GUEST_SRC
    steps.append(target.guest_sh("build", f"{src}/images/ns3", f"{src}/tracks/c-ns3/driver", run.IMAGE_TAG, *build_args(lock),
                                 timeout_s=7200))
    for step in steps:
        if args.log:
            with args.log.open("a") as handle:
                handle.write(step.stdout if step is not steps[-1] else "")
                handle.write(step.stderr)
        if step.rc != 0:
            print(step.stderr[-4000:], file=sys.stderr)
            print(f"image step in {target.name} exited {step.rc}", file=sys.stderr)
            return 1
    built = steps[-1].stdout.strip()
    pinned = lock["components"]["ns3.image"]["value"]
    print(f"{run.IMAGE_TAG} in {target.name}: {built}" + (" (built after guest.sh clean)" if args.clean else ""))
    if built != pinned:
        print(f"versions.lock.yaml pins ns3.image {pinned}, the build in {target.name} gave {built}. "
              "Set ns3.image to the new digest only after a build from clean gives it again.", file=sys.stderr)
        return 1
    print("matches ns3.image in versions.lock.yaml")
    if args.archive:
        args.archive.parent.mkdir(parents=True, exist_ok=True)
        with args.archive.open("wb") as handle:
            saved = subprocess.run([*target.ssh, f"sudo {guest.GUEST_SH} save {run.IMAGE_TAG} {built}"], stdout=handle,
                                   stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, timeout=3600)
        if saved.returncode != 0:
            print(saved.stderr.decode(errors="replace")[-2000:], file=sys.stderr)
            return 1
        print(f"wrote {args.archive}, {args.archive.stat().st_size} bytes, docker save of {run.IMAGE_TAG} {built}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ns3", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    scenarios = sorted(yaml.safe_load(run.CONFIG.read_text())["scenarios"])
    p = sub.add_parser("image", help="build the Track C image inside the guest and compare its ID with the lock")
    p.add_argument("-i", "--inventory", type=Path, action="append", required=True)
    p.add_argument("--clean", action="store_true", help="delete the tag, the base image and all build cache first")
    p.add_argument("--log", type=Path, help="append the build output to this file")
    p.add_argument("--archive", type=Path, help="when the ID matches the lock, write docker save of the image here")
    p.set_defaults(func=_cmd_image)
    p = sub.add_parser("run", help="run one scenario inside the Track C guest through the rfl CLI")
    p.add_argument("--scenario", required=True, choices=scenarios)
    p.add_argument("-i", "--inventory", type=Path, action="append", required=True)
    p.add_argument("--run-id", help="share one run id across scenarios")
    p.add_argument("--break", dest="broken", choices=sorted(run.BREAKS),
                   help="apply one deliberate break and keep every expectation; the run must fail")
    p.set_defaults(func=lambda a: run.main(a.scenario, a.inventory, a.run_id, a.broken))
    p = sub.add_parser("breaks", help="run every deliberate break of the scenarios, each under its own run id")
    p.add_argument("-i", "--inventory", type=Path, action="append", required=True)
    p.add_argument("--scenario", action="append", choices=scenarios, help="default: every scenario")
    p.set_defaults(func=lambda a: run.run_breaks(a.inventory, a.scenario or scenarios))
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
