import hashlib
import io
import tarfile

import pytest

from run import image

LOCK = {"components": {
    "lab.rdma_tools_base_image": {"value": "sha256:" + "ab" * 32, "ref": "ubuntu:24.04"},
    "lab.rdma_tools_apt_snapshot": {"value": "20261115T000000Z"},
    "lab.rdma_core": {"value": "50.0-2ubuntu0.2"},
    "lab.perftest": {"value": "24.01.0+0.38-1build2"},
}}


def test_every_time_in_the_image_is_the_snapshot_time():
    assert image.source_date_epoch("20261115T000000Z") == 1794700800


def test_build_arguments_come_from_the_lock():
    args = image.build_args(LOCK)
    assert args[0] == f"BASE_IMAGE=ubuntu:24.04@sha256:{'ab' * 32}"
    assert "RDMA_CORE_VERSION=50.0-2ubuntu0.2" in args and "PERFTEST_VERSION=24.01.0+0.38-1build2" in args
    assert args[-1] == "SOURCE_DATE_EPOCH=1794700800"


def test_an_undecided_build_input_stops_the_build():
    lock = {"components": {**LOCK["components"], "lab.perftest": {"value": None}}}
    with pytest.raises(image.ImageError, match="lab.perftest"):
        image.build_args(lock)


def test_the_context_archive_is_the_same_bytes_for_the_same_tree(tmp_path):
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    (ctx / "Dockerfile").write_text("FROM x\n")
    first = image.context_tar(ctx)
    (ctx / "Dockerfile").touch()
    assert image.context_tar(ctx) == first
    with tarfile.open(fileobj=io.BytesIO(first)) as tar:
        assert [(m.name, m.mtime, m.uid) for m in tar.getmembers()] == [("ctx/Dockerfile", 0, 0)]


def test_the_image_digest_is_the_sha256_of_the_archive_index(tmp_path):
    archive = tmp_path / "a.tar"
    body = b'{"schemaVersion":2}'
    with tarfile.open(archive, "w") as tar:
        info = tarfile.TarInfo("index.json")
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))
    assert image.index_digest(archive) == "sha256:" + hashlib.sha256(body).hexdigest()
