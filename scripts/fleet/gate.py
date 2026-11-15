"""The gate in front of every target that changes an invented site such as cel1.

An invented site has no real hosts, so its targets may only reach the guests of its simulation.
The simulation's override inventory points every host at 127.0.0.1, where the harness forwards
each guest's SSH port, and names the forwarded API address for the admin kubeconfig. The gate reads
only local files: the merged ansible-inventory listing and the kubeconfig. It contacts nothing.
"""

from __future__ import annotations

from urllib.parse import urlsplit

LOOPBACK = "127.0.0.1"


def _hosts(listing: dict, group: str = "all") -> list[str]:
    seen, stack, visited = [], [group], set()
    while stack:
        name = stack.pop(0)
        if name in visited:
            continue
        visited.add(name)
        entry = listing.get(name) or {}
        seen += [h for h in entry.get("hosts", []) if h not in seen]
        stack += entry.get("children", [])
    return seen


def _loopback_url(url) -> bool:
    try:
        parts = urlsplit(str(url))
        return parts.scheme == "https" and parts.hostname == LOOPBACK and parts.port is not None
    except ValueError:
        return False


def inventory_refusals(listing: dict, invented: set[str]) -> list[str]:
    """Why the merged inventory could reach something other than a simulation guest of an invented site.

    Only hosts whose rfl_inventory names an invented site are held to the rule, so an inventory of
    another site passes through unchanged.
    """
    hostvars = (listing.get("_meta") or {}).get("hostvars") or {}
    hosts = _hosts(listing)
    sites = {hostvars.get(h, {}).get("rfl_inventory") for h in hosts} & invented
    found = []
    for host in hosts:
        own = hostvars.get(host) or {}
        if sites and own.get("ansible_host") != LOOPBACK:
            found.append(f"{host}: ansible_host is {own.get('ansible_host', 'unset')}, not {LOOPBACK}")
        elif sites and not isinstance(own.get("ansible_port"), int):
            found.append(f"{host}: no ansible_port, so SSH would not use the forward of its guest")
    first = _hosts(listing, "kube_control_plane")[:1]
    for host in first if sites else []:
        server = (hostvars.get(host) or {}).get("rfl_kubeconfig_server")
        if not _loopback_url(server):
            found.append(f"{host}: rfl_kubeconfig_server is {server or 'unset'}, so the admin kubeconfig would name the "
                         f"endpoint inside the site instead of https://{LOOPBACK}:<forward>")
    return found


def kubeconfig_refusals(kubeconfig: dict) -> list[str]:
    """Why a kubeconfig could make helm or kubectl reach something other than a forwarded guest API."""
    clusters = kubeconfig.get("clusters") or []
    if not clusters:
        return ["the kubeconfig names no cluster"]
    found = []
    for entry in clusters:
        server = (entry.get("cluster") or {}).get("server")
        if not _loopback_url(server):
            found.append(f"cluster {entry.get('name')}: server {server} is not https://{LOOPBACK}:<forward>")
    return found
