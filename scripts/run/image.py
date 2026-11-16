"""make rdma-tools-image: build images/rdma-tools inside the builder guest and fetch its OCI archive.

    python -m run.image -i <builder inventory> -i <its override> --out <archive>

The host starts no container. It copies the image context into the builder guest, runs
images/rdma-tools/build.sh there through sudo with every build argument from versions.lock.yaml,
and copies the archive back. It fails when the digest differs from lab.rdma_tools_image.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import shlex
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import yaml

from .inventory import ssh_argv
from .remote import CommandLog

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCK = REPO_ROOT / "versions.lock.yaml"
CONTEXT = REPO_ROOT / "images" / "rdma-tools"
GUEST_ROOT = "/var/tmp/rfl-rdma-tools"
GUEST_ARCHIVE = f"{GUEST_ROOT}/rdma-tools.tar"
BUILDER_GROUP = "track_b_guest"
IMAGE = "lab.rdma_tools_image"
# Build argument of images/rdma-tools/Dockerfile -> lock component whose value it carries.
BUILD_ARGS = {
    "APT_SNAPSHOT": "lab.rdma_tools_apt_snapshot",
    "RDMA_CORE_VERSION": "lab.rdma_core",
    "PERFTEST_VERSION": "lab.perftest",
}
BASE = "lab.rdma_tools_base_image"


class ImageError(Exception):
    pass


def source_date_epoch(snapshot: str) -> int:
    """Seconds since the epoch of an archive snapshot id like 20261115T000000Z, the time every file of the image gets."""
    return int(dt.datetime.strptime(snapshot, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc).timestamp())


def build_args(lock: dict) -> list[str]:
    components = lock["components"]
    needed = [BASE, *BUILD_ARGS.values()]
    undecided = [name for name in needed if not components[name]["value"]]
    if undecided:
        raise ImageError(f"undecided in versions.lock.yaml: {', '.join(undecided)}")
    base = components[BASE]
    args = [f"BASE_IMAGE={base['ref']}@{base['value']}"]
    args += [f"{arg}={components[name]['value']}" for arg, name in BUILD_ARGS.items()]
    return args + [f"SOURCE_DATE_EPOCH={source_date_epoch(components[BUILD_ARGS['APT_SNAPSHOT']]['value'])}"]


def context_tar(context: Path) -> bytes:
    """The image context as a tar archive with modes but no owner or time, so equal trees give equal bytes."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for path in sorted(context.rglob("*")):
            info = tar.gettarinfo(str(path), arcname=str(path.relative_to(context.parent)))
            info.uid = info.gid = 0
            info.uname = info.gname = info.linkname = ""
            info.mtime = 0
            if info.isfile():
                with path.open("rb") as handle:
                    tar.addfile(info, handle)
            elif info.isdir():
                tar.addfile(info)
    return buffer.getvalue()


def index_digest(archive: Path) -> str:
    """sha256 of the OCI index of an archive, which ctr import --digests names the image by."""
    with tarfile.open(archive) as tar:
        member = tar.extractfile("index.json")
        if member is None:
            raise ImageError(f"{archive} has no index.json")
        return "sha256:" + hashlib.sha256(member.read()).hexdigest()


def builder(inventories: list[Path]) -> tuple[str, tuple[str, ...]]:
    listing = json.loads(subprocess.run(["ansible-inventory", "--list", *[a for i in inventories for a in ("-i", str(i))]],
                                        capture_output=True, text=True, check=True).stdout)
    hosts = listing.get(BUILDER_GROUP, {}).get("hosts", [])
    if not hosts:
        raise ImageError(f"no host in group {BUILDER_GROUP}. Pass the inventories of the ibsim harness profile.")
    return hosts[0], ssh_argv(listing["_meta"]["hostvars"][hosts[0]])


def run(ssh: tuple[str, ...], log: CommandLog, name: str, command: str, stdin: bytes | None = None,
        timeout_s: float = 120) -> subprocess.CompletedProcess:
    start = time.monotonic()
    out = subprocess.run([*ssh, command], input=stdin, capture_output=True, timeout=timeout_s,
                         stdin=None if stdin is not None else subprocess.DEVNULL)
    log.write(name, command, out.returncode, time.monotonic() - start)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run.image", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-i", "--inventory", type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path, required=True, help="where the OCI archive lands on this host")
    parser.add_argument("--log", type=Path, help="append the build log to this file")
    args = parser.parse_args(argv)
    lock = yaml.safe_load(LOCK.read_text())
    try:
        arguments = build_args(lock)
        name, ssh = builder(args.inventory)
    except ImageError as exc:
        print(exc, file=sys.stderr)
        return 2
    log = CommandLog(None)
    ref = lock["components"][IMAGE]["ref"]
    src = f"{GUEST_ROOT}/src"
    steps = [
        ("sync", f"set -e; rm -rf {src}; mkdir -p {src}; tar -x -C {src}", context_tar(CONTEXT), 120),
        ("build", shlex.join(["sudo", f"{src}/rdma-tools/build.sh", f"{src}/rdma-tools", ref, GUEST_ARCHIVE, *arguments]),
         None, 1800),
    ]
    for step, command, stdin, timeout_s in steps:
        out = run(ssh, log, name, command, stdin, timeout_s)
        if args.log:
            with args.log.open("ab") as handle:
                handle.write(out.stderr)
        if out.returncode != 0:
            print(out.stderr.decode(errors="replace")[-4000:], file=sys.stderr)
            print(f"{step} in {name} exited {out.returncode}", file=sys.stderr)
            return 1
    built = out.stdout.decode().strip()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    part = args.out.with_name(args.out.name + ".part")
    with part.open("wb") as handle:
        fetched = subprocess.run([*ssh, f"sudo cat {GUEST_ARCHIVE}"], stdout=handle, stdin=subprocess.DEVNULL, timeout=600)
    if fetched.returncode != 0 or index_digest(part) != built:
        print(f"copying {GUEST_ARCHIVE} from {name} failed or changed its index", file=sys.stderr)
        return 1
    part.replace(args.out)
    print(f"{ref} built in {name}: {built}, archive {args.out}")
    pinned = lock["components"][IMAGE]["value"]
    if built != pinned:
        print(f"versions.lock.yaml pins {IMAGE} {pinned}, the build in {name} gave {built}. "
              f"Set {IMAGE} to the new digest, then rerun every scenario on it.", file=sys.stderr)
        return 1
    print(f"matches {IMAGE} in versions.lock.yaml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
