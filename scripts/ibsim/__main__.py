"""Track B, the InfiniBand management simulation.

    python -m ibsim render --site sites/cel1.yaml --fabric compute --out tracks/b-ibsim/topologies/cel1-compute.net [--check]
    python -m ibsim image -i <inventory> -i <override> [--log <file>]
    python -m ibsim run --scenario b-01 -i <inventory> -i <override> [--run-id <id>] [--break]

Everything that starts ibsim, OpenSM, a diagnostic tool or a container runs inside the Track B guest.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from fleet import site
from run.remote import CommandLog

from . import guest, run, topology

LOCK = run.LOCK
IMAGE_TAG = run.IMAGE_TAG
# Build argument of images/ibsim/Dockerfile -> lock component that supplies it.
BUILD_ARGS = {
    "APT_SNAPSHOT": "ibsim.apt_snapshot",
    "IBSIM_VERSION": "ibsim.ibsim",
    "OPENSM_VERSION": "ibsim.opensm",
    "INFINIBAND_DIAGS_VERSION": "ibsim.infiniband_diags",
}

RENDER_HEADER = (
    "Rendered by `make track-b-render` from {site} fabrics.{fabric}. Do not edit.\n"
    "One single-port HCA per cabled host interface, named '<host> <hca>'. Counts: {counts}."
)


def rendered(site_path: Path, fabric: str) -> str:
    topo = topology.from_fabric(site.load(site_path), fabric)
    summary = ", ".join(f"{k} {v}" for k, v in topology.counts(topo).items())
    return topology.render_netfile(topo, RENDER_HEADER.format(site=site_path, fabric=fabric, counts=summary))


def _cmd_render(args: argparse.Namespace) -> int:
    text = rendered(args.site, args.fabric)
    if args.check:
        if not args.out.is_file() or args.out.read_text() != text:
            print(f"{args.out} differs from what {args.site} fabrics.{args.fabric} renders. Run make track-b-render.", file=sys.stderr)
            return 1
        print(f"{args.out} matches {args.site} fabrics.{args.fabric}")
        return 0
    args.out.write_text(text)
    print(f"wrote {args.out}")
    return 0


def build_args(lock: dict) -> list[str]:
    components = lock["components"]
    base = components["ibsim.base_image"]
    args = [f"BASE_IMAGE={base['ref']}@{base['value']}"]
    return args + [f"{arg}={components[name]['value']}" for arg, name in BUILD_ARGS.items()]


def _cmd_image(args: argparse.Namespace) -> int:
    lock = yaml.safe_load(LOCK.read_text())
    target = guest.load(args.inventory, CommandLog(None))
    for step in (target.sync(), target.guest_sh("build", f"{guest.GUEST_SRC}/images/ibsim", IMAGE_TAG, *build_args(lock),
                                                  timeout_s=1800)):
        if args.log:
            with args.log.open("a") as handle:
                handle.write(step.stderr)
        if step.rc != 0:
            print(step.stderr[-4000:], file=sys.stderr)
            print(f"image build in {target.name} exited {step.rc}", file=sys.stderr)
            return 1
    built = step.stdout.strip()
    pinned = lock["components"]["ibsim.image"]["value"]
    print(f"{IMAGE_TAG} in {target.name}: {built}")
    if built != pinned:
        print(f"versions.lock.yaml pins ibsim.image {pinned}, the build in {target.name} gave {built}. "
              "Set ibsim.image to the new digest, then rerun every scenario on it.", file=sys.stderr)
        return 1
    print("matches ibsim.image in versions.lock.yaml")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ibsim", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("render", help="write the ibsim topology of one site fabric")
    p.add_argument("--site", type=Path, required=True)
    p.add_argument("--fabric", required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--check", action="store_true", help="fail when --out differs instead of writing it")
    p.set_defaults(func=_cmd_render)
    p = sub.add_parser("image", help="build the Track B image inside the guest and compare its ID with the lock")
    p.add_argument("-i", "--inventory", type=Path, action="append", required=True)
    p.add_argument("--log", type=Path, help="append the build output to this file")
    p.set_defaults(func=_cmd_image)
    p = sub.add_parser("run", help="run one scenario inside the Track B guest through the rfl CLI")
    p.add_argument("--scenario", required=True, choices=sorted(yaml.safe_load(run.CONFIG.read_text())["scenarios"]))
    p.add_argument("-i", "--inventory", type=Path, action="append", required=True)
    p.add_argument("--run-id", help="share one run id across scenarios")
    p.add_argument("--break", dest="broken", action="store_true",
                   help="simulate the topology without the scenario's break link, keep every expectation; the run must fail")
    p.set_defaults(func=lambda a: run.main(a.scenario, a.inventory, a.run_id, a.broken))
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
