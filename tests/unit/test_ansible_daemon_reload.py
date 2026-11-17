"""systemd reloads only from handlers, so only when a unit file changed.

A systemctl daemon-reload on a node with pods makes systemd 255 drop the Conflicts=shutdown.target that the kubelet set
on its slices. The next shutdown then never stops the pods, and Multus keeps its file across the reboot (M7).
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
MODULES = ("ansible.builtin.systemd_service", "ansible.builtin.systemd", "systemd_service", "systemd")


def tasks(node):
    if isinstance(node, list):
        for item in node:
            yield from tasks(item)
    elif isinstance(node, dict):
        yield node
        for key in ("block", "rescue", "always", "tasks", "pre_tasks", "post_tasks"):
            yield from tasks(node.get(key))


def reloading_tasks(path: Path) -> list[str]:
    return [t.get("name", "?") for t in tasks(yaml.safe_load(path.read_text()))
            for m in MODULES if isinstance(t.get(m), dict) and t[m].get("daemon_reload")]


def test_no_task_outside_a_handler_reloads_systemd():
    files = sorted(ROOT.glob("ansible/roles/*/tasks/*.yml")) + sorted(ROOT.glob("ansible/playbooks/**/*.yml"))
    found = {str(f.relative_to(ROOT)): r for f in files if (r := reloading_tasks(f))}
    assert found == {}


def test_the_guard_sees_a_reloading_task(tmp_path):
    path = tmp_path / "main.yml"
    path.write_text("- name: Start it\n  ansible.builtin.systemd_service:\n    name: x.service\n    daemon_reload: true\n")
    assert reloading_tasks(path) == ["Start it"]
