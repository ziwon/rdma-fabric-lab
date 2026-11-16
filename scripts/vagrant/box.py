"""The VMware box of the Vagrant lab, built from lab.guest_image without any registry.

The box is a gzip tar of metadata.json, box.vmx and disk.vmdk. qemu-img converts the pinned cloud
image into a monolithic sparse VMDK on an LSI Logic adapter, whose mptspi driver is in the image's
initrd. Nothing here needs VMware. Vagrant clones the box per guest and grows the primary disk to
the profile size, and cloud-init grows the root partition at first boot.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

PROVIDER = "vmware_desktop"
IMAGE_COMPONENT = "lab.guest_image"
DISK = "disk.vmdk"
VMX = "box.vmx"
CONVERT_OPTIONS = "adapter_type=lsilogic,subformat=monolithicSparse"
# Hardware version 19 is Workstation 16.2. Newer releases open it unchanged.
VIRTUAL_HW = 19
# The cloud image boots both ways. BIOS is the path the QEMU harness proved with SeaBIOS.
VMX_SETTINGS = (
    (".encoding", "UTF-8"),
    ("config.version", "8"),
    ("virtualHW.version", str(VIRTUAL_HW)),
    ("guestOS", "ubuntu-64"),
    ("firmware", "bios"),
    ("numvcpus", "2"),
    ("memsize", "2048"),
    ("pciBridge0.present", "TRUE"),
    *((f"pciBridge{i}.{k}", v) for i in range(4, 8)
      for k, v in (("present", "TRUE"), ("virtualDev", "pcieRootPort"), ("functions", "8"))),
    ("vmci0.present", "TRUE"),
    ("hpet0.present", "TRUE"),
    ("floppy0.present", "FALSE"),
    ("scsi0.present", "TRUE"),
    ("scsi0.virtualDev", "lsilogic"),
    ("scsi0:0.present", "TRUE"),
    ("scsi0:0.fileName", DISK),
    ("ethernet0.present", "TRUE"),
    ("ethernet0.connectionType", "nat"),
    ("ethernet0.virtualDev", "e1000e"),
    ("ethernet0.addressType", "generated"),
    # The image's kernel command line has console=ttyS0, so this file keeps every boot log.
    ("serial0.present", "TRUE"),
    ("serial0.fileType", "file"),
    ("serial0.fileName", "serial.log"),
    ("tools.syncTime", "FALSE"),
)


class BoxError(RuntimeError):
    pass


def image_pin(lock: dict) -> tuple[str, str, str]:
    """(source URL, sha256 hex, ref) of the cloud image the QEMU harness also boots."""
    component = lock["components"][IMAGE_COMPONENT]
    value = component["value"] or ""
    if not value.startswith("sha256:"):
        raise BoxError(f"{IMAGE_COMPONENT} is {value!r}, not a sha256 pin")
    return component["source"], value.removeprefix("sha256:"), component["ref"]


def box_name(lock: dict) -> str:
    """rfl-<series>-<serial> from the image ref, such as rfl-noble-20261017."""
    _, _, ref = image_pin(lock)
    return "rfl-" + ref.replace("/", "-")


def vmx_text(name: str) -> str:
    lines = [f'{key} = "{value}"' for key, value in (*VMX_SETTINGS, ("displayName", name))]
    return "\n".join(lines) + "\n"


def metadata() -> str:
    return json.dumps({"provider": PROVIDER}) + "\n"


def inputs_digest(image_sha256: str, name: str, qemu_img_version: str) -> str:
    """Changes whenever anything that shapes the box changes, so a stale box is rebuilt."""
    text = "\n".join((image_sha256, CONVERT_OPTIONS, qemu_img_version, vmx_text(name), metadata()))
    return hashlib.sha256(text.encode()).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def paths(data_dir: Path, name: str) -> dict[str, Path]:
    return {
        "box": data_dir / "boxes" / f"{name}.box",
        "sha256": data_dir / "boxes" / f"{name}.box.sha256",
        "inputs": data_dir / "boxes" / f"{name}.box.inputs",
        "work": data_dir / "box-build" / name,
    }


def ensure_image(data_dir: Path, url: str, sha256: str, say) -> Path:
    image = data_dir / "downloads" / f"sha256-{sha256}.img"
    if not image.is_file():
        image.parent.mkdir(parents=True, exist_ok=True)
        part = image.with_suffix(".part")
        say(f"downloading {url}")
        with urllib.request.urlopen(url, timeout=60) as response, part.open("wb") as out:
            shutil.copyfileobj(response, out, 1 << 20)
        part.rename(image)
    found = sha256_file(image)
    if found != sha256:
        raise BoxError(f"{image} hashes to {found}, not {IMAGE_COMPONENT} {sha256}. Refusing to build from it.")
    image.chmod(0o444)
    return image


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mtime = 0
    return info


def build(lock: dict, data_dir: Path, say) -> Path:
    """Write <data>/boxes/<name>.box and its .sha256. A box whose inputs did not change is kept."""
    url, sha256, _ = image_pin(lock)
    name = box_name(lock)
    where = paths(data_dir, name)
    qemu_img = subprocess.run(["qemu-img", "--version"], capture_output=True, text=True, check=True).stdout.splitlines()[0]
    digest = inputs_digest(sha256, name, qemu_img)
    if where["box"].is_file() and where["inputs"].is_file() and where["inputs"].read_text().strip() == digest:
        say(f"{where['box']} is up to date")
        return where["box"]
    image = ensure_image(data_dir, url, sha256, say)
    work = where["work"]
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    say(f"converting {image.name} to {DISK}")
    subprocess.run(["qemu-img", "convert", "-q", "-f", "qcow2", "-O", "vmdk", "-o", CONVERT_OPTIONS, str(image),
                    str(work / DISK)], check=True)
    (work / VMX).write_text(vmx_text(name))
    (work / "metadata.json").write_text(metadata())
    where["box"].parent.mkdir(parents=True, exist_ok=True)
    part = where["box"].with_suffix(".box.part")
    say(f"packing {where['box']}")
    with tarfile.open(part, "w:gz", compresslevel=1) as tar:
        for member in ("metadata.json", VMX, DISK):
            tar.add(work / member, arcname=member, filter=_tar_filter)
    part.rename(where["box"])
    where["sha256"].write_text(f"{sha256_file(where['box'])}  {where['box'].name}\n")
    where["inputs"].write_text(digest + "\n")
    shutil.rmtree(work)
    say(f"wrote {where['box']}, sha256 {where['sha256'].read_text().split()[0]}")
    return where["box"]
