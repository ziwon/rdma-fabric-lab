import copy
import json
import re
from pathlib import Path

import pytest
import yaml

from collect import __main__ as collect

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT = (REPO_ROOT / "tests/fixtures/a-soft-roce/collect/guest-report.txt").read_text()
LOCK = yaml.safe_load((REPO_ROOT / "versions.lock.yaml").read_text())
PACKAGES = yaml.safe_load((REPO_ROOT / "ansible/inventories/lab/group_vars/all/lab.yml").read_text())["lab_packages"]


def test_guest_report_maps_to_lock_components():
    observed = collect.observed_versions(REPORT, PACKAGES)
    assert observed["lab.guest_kernel"] == ["6.8.0-147-generic"]
    assert observed["lab.guest_image"] == ["20261017"]
    assert len(observed["lab.rdma_core"]) == 4
    assert observed["lab.perftest"] == ["24.01.0+0.38-1build2"]


def test_lock_agrees_with_the_captured_guest():
    rows = collect.lock_rows(LOCK, collect.observed_versions(REPORT, PACKAGES))
    assert {r["component"] for r in rows} == {c for c in LOCK["scopes"]["lab"] if c.startswith("lab.")}
    assert "lab.k8s_test_image" not in {r["component"] for r in rows}
    assert all(r["agrees"] for r in rows), rows


def test_a_moved_lock_value_disagrees():
    moved = copy.deepcopy(LOCK)
    moved["components"]["lab.rdma_core"]["value"] = "50.0-2ubuntu0.3"
    rows = {r["component"]: r for r in collect.lock_rows(moved, collect.observed_versions(REPORT, PACKAGES))}
    assert not rows["lab.rdma_core"]["agrees"]
    assert rows["lab.perftest"]["agrees"]


def test_missing_package_disagrees():
    report = "\n".join(line for line in REPORT.splitlines() if not line.startswith("perftest="))
    rows = {r["component"]: r for r in collect.lock_rows(LOCK, collect.observed_versions(report, PACKAGES))}
    assert not rows["lab.perftest"]["agrees"]


SECRETS = ("hunter2-not-a-real-password", "TOKEN-0123456789abcdef", "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC")
PEM = f"-----BEGIN OPENSSH PRIVATE KEY-----\n{SECRETS[2]}\n-----END OPENSSH PRIVATE KEY-----\n"


def worker_hostvars() -> dict:
    """The variables ansible-inventory resolves for a lab worker, with a credential behind every name that hints at one."""
    return {
        "ansible_host": "127.0.0.1", "ansible_port": 22212, "ansible_user": "ubuntu",
        "ansible_ssh_private_key_file": "/home/u/.lab/ssh/id_ed25519",
        "ansible_ssh_common_args": "-o UserKnownHostsFile=/home/u/.lab/ssh/known_hosts",
        "ansible_become_password": SECRETS[0],
        "k8s_admin_kubeconfig": ".lab/kubeconfig", "k8s_join_token": SECRETS[1], "k8s_node_ip": "192.168.140.12",
        "lab_mgmt_nic": "mgmt0", "lab_rdma_nic": "lab0", "lab_rdma_ip": "10.77.0.12", "lab_rxe_device": "rxe0",
        "lab_packages": PACKAGES, "rfl_registry_secret": SECRETS[0],
    }


class FakeRemote:
    """A worker that answers every snapshot command, and prints a private key for the netplan file when told to."""

    leak_key = False

    def __init__(self, guest, run_id, log):
        self.guest = guest

    def sh(self, command, timeout_s=60, binary=False):
        from run.remote import Done
        if command == collect.GUEST_REPORT:
            return Done(0, REPORT, "")
        if "netplan" in command and self.leak_key:
            return Done(0, PEM, "")
        return Done(0, f"output of {command}\n", "")


def run_collect(tmp_path, monkeypatch, leak_key: bool) -> Path:
    import json as json_module
    import subprocess

    from rfl import manifest
    from run import rfl as runner_rfl

    listing = {"workers": {"hosts": ["worker1"]}, "_meta": {"hostvars": {"worker1": worker_hostvars()}}}
    real_run = subprocess.run

    def fake_run(argv, *args, **kwargs):
        if argv[0] == "ansible-inventory":
            return subprocess.CompletedProcess(argv, 0, json_module.dumps(listing), "")
        return real_run(argv, *args, **kwargs)

    results = tmp_path / "results"
    monkeypatch.setattr(collect.subprocess, "run", fake_run)
    monkeypatch.setattr(runner_rfl, "init", lambda spec, run_id=None, deliberate_break=None: manifest.init(spec, results, run_id))
    monkeypatch.setattr(FakeRemote, "leak_key", leak_key)
    monkeypatch.setattr(collect, "Remote", FakeRemote)
    collect.main(["-i", "unused", "--run-id", "20261117T000000Z"])
    return results / "20261117T000000Z" / "a-soft-roce" / "vm-environment"


def written_text(run_dir: Path) -> str:
    return "".join(p.read_text(errors="replace") for p in sorted(run_dir.rglob("*")) if p.is_file())


@pytest.mark.parametrize("leak_key", [False, True])
def test_collect_never_writes_a_key_a_token_a_password_or_a_kubeconfig(tmp_path, monkeypatch, leak_key):
    run_dir = run_collect(tmp_path, monkeypatch, leak_key)
    text = written_text(run_dir)
    assert not [s for s in SECRETS if s in text]
    assert "PRIVATE KEY-----" not in text
    variables = yaml.safe_load((run_dir / "resolved-config.yaml").read_text())["variables"]["worker1"]
    assert variables["lab_rdma_nic"] == "lab0" and variables["ansible_port"] == 22212
    assert not {"ansible_ssh_private_key_file", "k8s_admin_kubeconfig", "k8s_join_token", "ansible_become_password",
                "rfl_registry_secret"} & set(variables)
    checks = {c["id"]: c for c in json.loads((run_dir / "manifest.json").read_text())["checks"]}
    assert checks["no-credential-collected"]["status"] == ("failed" if leak_key else "passed")
    if leak_key:
        assert "withheld" in (run_dir / "raw/state/worker1/netplan-rdma.txt").read_text()


def test_no_snapshot_command_reads_a_credential_path():
    commands = list(collect.GUEST_STATE.values()) + [" ".join(c) for c in collect.HOST_STATE.values()] + [collect.GUEST_REPORT]
    pattern = re.compile(r"kubeconfig|admin\.conf|/etc/kubernetes|\.ssh|id_ed25519|id_rsa|shadow|token|secret|/etc/rfl/.*key", re.I)
    assert not [c for c in commands if pattern.search(c)]


@pytest.mark.parametrize(("text", "found"), [
    ("client-key-data: LS0tLS1CRUdJTg==\n", ["kubeconfig client credential"]),
    ("  token: abc.def\n", ["token value"]),
    ("Password = x\n", ["password value"]),
    ("-----BEGIN RSA PRIVATE KEY-----\n", ["PEM private key"]),
    ("ssh_pwauth: false\nKey size 2048\n", []),
])
def test_credential_markers_name_the_shape_and_never_the_value(text, found):
    assert collect.credential_markers(text) == found
