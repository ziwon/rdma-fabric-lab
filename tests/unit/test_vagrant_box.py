import hashlib
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

from vagrant import box

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCK = yaml.safe_load((REPO_ROOT / "versions.lock.yaml").read_text())
# The slots vagrant-vmware-desktop 3.0.5 searches for the primary disk, cap/disk.rb PRIMARY_DISK_SLOTS.
PRIMARY_DISK_SLOTS = ("nvme0:0", "scsi0:0", "sata0:0", "ide0:0")
needs_qemu_img = pytest.mark.skipif(shutil.which("qemu-img") is None, reason="qemu-img is not installed")


def vmx(text: str) -> dict[str, str]:
    pairs = [line.split(" = ", 1) for line in text.splitlines()]
    keys = [k for k, _ in pairs]
    assert len(keys) == len(set(keys)), "every VMX key once"
    return {k: v.strip('"') for k, v in pairs}


def lock_for(image: Path, ref: str = "noble/20990101") -> dict:
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    return {"components": {"lab.guest_image": {"value": f"sha256:{digest}", "source": "https://unused.invalid/x.img", "ref": ref}}}


def test_the_box_is_named_after_the_pinned_image_build():
    assert box.box_name(LOCK) == "rfl-" + LOCK["components"]["lab.guest_image"]["ref"].replace("/", "-")


def test_an_image_pin_that_is_not_a_sha256_is_refused():
    with pytest.raises(box.BoxError, match="not a sha256 pin"):
        box.image_pin({"components": {"lab.guest_image": {"value": None, "source": "s", "ref": "r"}}})


def test_vmx_puts_the_disk_on_a_primary_slot_the_provider_finds():
    settings = vmx(box.vmx_text("rfl-noble-20261017"))
    primary = [s for s in PRIMARY_DISK_SLOTS if settings.get(f"{s}.present") == "TRUE"]
    assert primary == ["scsi0:0"]
    assert settings["scsi0:0.fileName"] == box.DISK and settings["scsi0.virtualDev"] == "lsilogic"
    assert settings["ethernet0.connectionType"] == "nat" and settings["displayName"] == "rfl-noble-20261017"
    assert settings["serial0.fileName"] == "serial.log"


def test_metadata_names_the_vmware_desktop_provider():
    assert json.loads(box.metadata()) == {"provider": "vmware_desktop"}


def test_inputs_digest_changes_with_the_image_the_name_and_qemu_img():
    base = box.inputs_digest("a" * 64, "n", "qemu-img version 8.2.2")
    assert base == box.inputs_digest("a" * 64, "n", "qemu-img version 8.2.2")
    assert len({base, box.inputs_digest("b" * 64, "n", "qemu-img version 8.2.2"), box.inputs_digest("a" * 64, "m", "qemu-img version 8.2.2"),
                box.inputs_digest("a" * 64, "n", "qemu-img version 9.0.0")}) == 4


@needs_qemu_img
def test_build_packs_a_box_vagrant_can_add_and_keeps_it_while_inputs_hold(tmp_path):
    source = tmp_path / "source.qcow2"
    subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", str(source), "64M"], check=True)
    lock = lock_for(source)
    _, digest, _ = box.image_pin(lock)
    data = tmp_path / "data"
    (data / "downloads").mkdir(parents=True)
    shutil.copy(source, data / "downloads" / f"sha256-{digest}.img")
    messages = []
    built = box.build(lock, data, messages.append)
    assert built == data / "boxes" / "rfl-noble-20990101.box"
    with tarfile.open(built) as tar:
        assert sorted(tar.getnames()) == ["box.vmx", "disk.vmdk", "metadata.json"]
        tar.extractall(tmp_path / "unpacked", filter="data")
    info = json.loads(subprocess.run(["qemu-img", "info", "--output=json", str(tmp_path / "unpacked" / "disk.vmdk")],
                                     capture_output=True, text=True, check=True).stdout)
    assert info["format"] == "vmdk" and info["virtual-size"] == 64 * 1024 * 1024
    assert (data / "boxes" / "rfl-noble-20990101.box.sha256").read_text().split()[0] == hashlib.sha256(built.read_bytes()).hexdigest()
    assert not (data / "box-build").joinpath("rfl-noble-20990101").exists()
    stamp = built.stat().st_mtime_ns
    box.build(lock, data, messages.append)
    assert built.stat().st_mtime_ns == stamp and messages[-1].endswith("is up to date")


@needs_qemu_img
def test_build_refuses_an_image_that_does_not_hash_to_the_pin(tmp_path):
    source = tmp_path / "source.qcow2"
    subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", str(source), "64M"], check=True)
    lock = lock_for(source)
    _, digest, _ = box.image_pin(lock)
    (tmp_path / "data" / "downloads").mkdir(parents=True)
    (tmp_path / "data" / "downloads" / f"sha256-{digest}.img").write_bytes(b"not the image")
    with pytest.raises(box.BoxError, match="Refusing to build from it"):
        box.build(lock, tmp_path / "data", lambda _: None)
    assert not (tmp_path / "data" / "boxes").exists()
