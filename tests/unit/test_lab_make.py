import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def listing(api_port: int) -> dict:
    return {"kube_control_plane": {"hosts": ["cp1"]}, "_meta": {"hostvars": {"cp1": {"k8s_api_port": api_port}}}}


def dry_run_k8s(lab_dir: Path) -> str:
    """make -n k8s with LAB_DIR on the command line, the form make 4.3 does not hand to $(shell)."""
    out = subprocess.run(["make", "-n", "--no-print-directory", "k8s", f"LAB_DIR={lab_dir}"], cwd=ROOT, capture_output=True,
                         text=True, check=True, env={"PATH": f"{ROOT / '.venv' / 'bin'}:/usr/bin:/bin", "HOME": str(lab_dir)})
    return next(line for line in out.stdout.splitlines() if "playbooks/kubernetes.yml" in line)


@pytest.mark.parametrize(("api_port", "server"), [(6443, "-e rfl_kubeconfig_server=https://127.0.0.1:22643"), (7443, None)])
def test_a_command_line_lab_dir_gives_the_harness_calls_its_own_listing_and_override_inventory(tmp_path, api_port, server):
    (tmp_path / ".listing.json").write_text(json.dumps(listing(api_port)))
    (tmp_path / "inventory.yml").write_text("all: {}\n")
    line = dry_run_k8s(tmp_path)
    assert f"-i {tmp_path}/inventory.yml" in line and "-i .lab/inventory.yml" not in line
    assert (server in line) if server else ("rfl_kubeconfig_server" not in line)
