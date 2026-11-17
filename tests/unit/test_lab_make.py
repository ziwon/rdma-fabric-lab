import json
import re
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


def dry_run(target: str) -> str:
    out = subprocess.run(["make", "-n", "--no-print-directory", target], cwd=ROOT, capture_output=True, text=True, check=True,
                         env={"PATH": f"{ROOT / '.venv' / 'bin'}:/usr/bin:/bin", "HOME": str(ROOT)})
    return out.stdout


def scenarios_of(target: str) -> list[str]:
    line = next(line for line in dry_run(target).splitlines() if "for scenario in " in line)
    return line.split("for scenario in ", 1)[1].split(";", 1)[0].split()


def test_track_c_is_its_smoke_scenarios_then_its_incast_matrix():
    import yaml
    kinds = {name: s["kind"] for name, s in yaml.safe_load((ROOT / "tracks/c-ns3/config/scenarios.yaml").read_text())["scenarios"].items()}
    assert scenarios_of("track-c-smoke") == ["c-01-example", "c-02-single-flow"]
    assert scenarios_of("track-c-matrix") == sorted(n for n, k in kinds.items() if k == "incast")
    assert scenarios_of("track-c") == scenarios_of("track-c-smoke") + scenarios_of("track-c-matrix")


def test_track_a_runs_every_required_track_a_scenario_with_the_reboot_last():
    import yaml
    required = {p.stem for p in (ROOT / "tracks/a-soft-roce/scenarios").glob("*.yaml") if yaml.safe_load(p.read_text())["required"]}
    runs = [line for line in dry_run("track-a").splitlines() if "-m run." in line]
    ran = [line.split("--scenario ")[1].split()[0] if "--scenario " in line else {"run.k8s": "k8s-baseline", "run.hostnet":
           "k8s-hostnet-rdma", "run.multus": "k8s-multus-net1"}[line.split("-m ")[1].split()[0]] for line in runs]
    assert sorted(ran) == sorted(required) and ran[-1] == "vm-rxe-reboot"


def test_help_lists_only_targets_make_knows():
    listed = [line.split()[0] for line in subprocess.run(["make", "--no-print-directory", "help"], cwd=ROOT, capture_output=True,
                                                         text=True, check=True).stdout.splitlines() if line.strip()]
    database = subprocess.run(["make", "-pRrq", "--no-print-directory"], cwd=ROOT, capture_output=True, text=True).stdout
    known = {line.split(":", 1)[0] for line in database.splitlines() if re.match(r"^[a-zA-Z0-9_-]+:", line)}
    assert set(listed) <= known and {"track-a", "track-c-smoke", "track-c-matrix", "collect", "report", "cleanup"} <= set(listed)


@pytest.mark.parametrize(("target", "runner"), [("track-b", "-m ibsim image"), ("track-c", "-m ns3 image")])
def test_a_command_line_lab_dir_reaches_the_track_b_and_track_c_inventories(tmp_path, target, runner):
    out = subprocess.run(["make", "-n", "--no-print-directory", target, f"LAB_DIR={tmp_path}"], cwd=ROOT, capture_output=True,
                         text=True, check=True, env={"PATH": f"{ROOT / '.venv' / 'bin'}:/usr/bin:/bin", "HOME": str(tmp_path)})
    line = next(line for line in out.stdout.splitlines() if runner in line)
    assert f"-i {tmp_path}/inventory.yml" in line
