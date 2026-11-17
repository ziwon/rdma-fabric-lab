"""The guest copy of the image context carries the modes Git keeps, whatever umask the checkout had.

A checkout made with umask 002 gave rfl-ns3 mode 0775 instead of 0755, and the Track C image digest changed (docs/adr/0002-execution-environments.md section 5).
"""

import io
import tarfile

import pytest

from ibsim import guest as track_b
from ns3 import guest as track_c


def tree(root, synced, umask_modes: bool):
    for top in synced:
        script, data = root / top / "bin" / "entry", root / top / "file.conf"
        script.parent.mkdir(parents=True)
        script.write_text("#!/bin/sh\n")
        data.write_text("x\n")
        script.chmod(0o775 if umask_modes else 0o755)
        data.chmod(0o664 if umask_modes else 0o644)
        (root / top).chmod(0o775 if umask_modes else 0o755)
        script.parent.chmod(0o775 if umask_modes else 0o755)


@pytest.mark.parametrize("module", [track_b, track_c])
def test_a_group_writable_checkout_gives_the_same_bytes_as_a_plain_one(tmp_path, module):
    loose, plain = tmp_path / "loose", tmp_path / "plain"
    tree(loose, module.SYNCED, True)
    tree(plain, module.SYNCED, False)
    assert module.synced_tar(loose) == module.synced_tar(plain)
    with tarfile.open(fileobj=io.BytesIO(module.synced_tar(loose))) as tar:
        modes = {m.name: m.mode for m in tar.getmembers()}
    top = module.SYNCED[0]
    assert modes[f"{top}/bin/entry"] == 0o755 and modes[f"{top}/file.conf"] == 0o644 and modes[f"{top}/bin"] == 0o755
