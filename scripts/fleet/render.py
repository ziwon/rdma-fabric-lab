"""Render an Ansible inventory from a site model, to the contract of ADR 0001 section 4.

    hosts.yml                 k8s_cluster of kube_control_plane and kube_node, one group per class
    group_vars/all.yml        the cluster-wide contract variables and the syslog target
    group_vars/<class>.yml    k8s_node_labels of the class
    host_vars/<host>.yml      k8s_node_ip

The inventory sets no ansible_host. A second inventory passed after it says how to reach each
host, so a command without one cannot reach an invented address.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .site import Site

HEADER = "# Rendered by `make fleet-render` from the {name} site model. Edit the model, not this file.\n"
MARKER = "# Rendered by `make fleet-render`"


def _dump(data: dict) -> str:
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False, width=1000)


def _file(site: Site, data: dict) -> str:
    return HEADER.format(name=site.name) + _dump(data)


def _hosts(names: list[str]) -> dict:
    return {"hosts": {n: {} for n in names}}


def render(site: Site) -> dict[str, str]:
    """Relative path to file text. The same model always gives the same bytes."""
    control_planes = [h.name for h in site.members("control_plane")]
    nodes = [h.name for h in site.members("node")]
    children = {"k8s_cluster": {"children": {"kube_control_plane": _hosts(control_planes), "kube_node": _hosts(nodes)}}}
    for node_class in site.classes.values():
        children[node_class.name] = _hosts([h.name for h in site.hosts.values() if h.node_class == node_class.name])

    vip_interface = site.node_interface(site.classes[site.hosts[control_planes[0]].node_class]).name
    files = {
        "hosts.yml": _file(site, {"all": {"children": children}}),
        "group_vars/all.yml": _file(
            site,
            {
                "rfl_inventory": site.name,
                "k8s_api_endpoint": str(site.api_vip),
                "k8s_api_port": site.api_port,
                "k8s_api_vip": str(site.api_vip),
                "k8s_api_vip_interface": vip_interface,
                "k8s_pod_cidr": str(site.pod_cidr),
                "k8s_service_cidr": str(site.service_cidr),
                "k8s_kube_proxy": site.kube_proxy,
                "k8s_control_plane_schedulable": site.control_plane_schedulable,
                "rsyslog_client_tcp_host": str(site.syslog_address),
                "rsyslog_client_tcp_port": site.syslog_port,
            },
        ),
    }
    for node_class in site.classes.values():
        files[f"group_vars/{node_class.name}.yml"] = _file(site, {"k8s_node_labels": dict(node_class.labels)})
    for host in site.hosts.values():
        files[f"host_vars/{host.name}.yml"] = _file(site, {"k8s_node_ip": str(site.node_ip(host))})
    return files


def _on_disk(out: Path) -> dict[str, str]:
    return {str(p.relative_to(out)): p.read_text() for p in sorted(out.rglob("*")) if p.is_file()}


def drift(files: dict[str, str], out: Path) -> list[str]:
    """How the directory differs from the rendered files. Empty means byte for byte equal."""
    present = _on_disk(out) if out.is_dir() else {}
    found = [f"{out / path}: missing" for path in sorted(set(files) - set(present))]
    found += [f"{out / path}: differs from the site model" for path in sorted(files) if path in present and present[path] != files[path]]
    for path in sorted(set(present) - set(files)):
        reason = "rendered earlier, no longer in the site model" if present[path].startswith(MARKER) else "not rendered from the site model"
        found.append(f"{out / path}: {reason}")
    return found


def write(files: dict[str, str], out: Path) -> list[str]:
    """Write every file and remove files an earlier render left for hosts or classes that are gone.
    A file the renderer did not write is left alone and returned."""
    for path, text in files.items():
        target = out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.read_text() != text:
            target.write_text(text)
    foreign = []
    for path, text in _on_disk(out).items():
        if path not in files:
            if text.startswith(MARKER):
                (out / path).unlink()
            else:
                foreign.append(str(out / path))
    return foreign
