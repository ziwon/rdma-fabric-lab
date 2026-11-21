"""The topology drawing of a site model in NetBox (ADR 0008).

The topology views plugin draws the devices and cables of NetBox. Without stored coordinates it lays them
out with a physics simulation, which never draws a fabric the same way twice. This module places every
device of the model on a fixed grid in a coordinate group of its own, tier by tier:

    spines       the switches without a rail
    leaves       the switches of a rail, in rail order
    fabric hosts the hosts cabled to a fabric
    other hosts  every other host, class by class

and gives each role an icon of the plugin. The group and its coordinates carry the managed tag, and
`apply` makes them equal the layout: it creates, moves and deletes coordinates of the group only.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from fleet.site import Site

from .model import TAG, slug

GROUP = TAG
API = "plugins/netbox_topology_views"
X_STEP = 160
Y_STEP = 240
IMG = "netbox_topology_views/img"


def tiers(site: Site) -> list[list[str]]:
    switches = [sw for f in site.fabrics.values() for sw in f.switches.values()]
    cabled = {e.node for f in site.fabrics.values() for link in f.links for e in link if e.node in site.hosts}
    classes = list(site.classes)
    rows = [
        sorted(sw.name for sw in switches if sw.rail is None),
        [sw.name for sw in sorted((sw for sw in switches if sw.rail is not None), key=lambda sw: (sw.rail, sw.name))],
        sorted(cabled),
        [h.name for h in sorted((h for h in site.hosts.values() if h.name not in cabled),
                                key=lambda h: (classes.index(h.node_class), h.name))],
    ]
    return [r for r in rows if r]


def layout(site: Site) -> dict[str, tuple[int, int]]:
    """device name -> (x, y), every row centered on x = 0."""
    out = {}
    for i, row in enumerate(tiers(site)):
        for j, name in enumerate(row):
            out[name] = (round((j - (len(row) - 1) / 2) * X_STEP), i * Y_STEP)
    return out


def role_images(site: Site) -> dict[str, str]:
    """role slug -> image path in NetBox's static directory."""
    out = {slug(c.name): f"{IMG}/server.svg" for c in site.classes.values()}
    for f in site.fabrics.values():
        for sw in f.switches.values():
            out[slug(sw.role)] = f"{IMG}/{'core-switch' if sw.rail is None else 'access-switch'}.svg"
    return out


def cable_pairs(site: Site) -> Counter:
    """How many cables join each unordered pair of devices."""
    return Counter(tuple(sorted((a.node, b.node))) for f in site.fabrics.values() for a, b in f.links)


@dataclass
class Changes:
    created: int = 0
    moved: int = 0
    deleted: int = 0

    def __str__(self) -> str:
        return f"{self.created} created, {self.moved} moved, {self.deleted} deleted"


def group(client) -> dict | None:
    found = client.list(f"{API}/coordinate-groups", {"name": GROUP})
    return found[0] if found else None


def _device_id(ref) -> int:
    """The plugin's serializer gives a coordinate's device as an id, a nested object or a URL."""
    if isinstance(ref, dict):
        return ref["id"]
    if isinstance(ref, str):
        return int(ref.rstrip("/").rsplit("/", 1)[-1])
    return ref


def coordinates(client, group_id: int, names: dict[int, str]) -> dict[str, dict]:
    """device name -> coordinate of the group. names maps a device id to its name."""
    return {names.get(_device_id(c["device"]), f"device {_device_id(c['device'])}"): c
            for c in client.list(f"{API}/coordinate", {"group": group_id})}


def apply(client, site: Site) -> Changes:
    """Make the coordinate group equal the layout and set the icon of every role of the model."""
    tag = {"name": TAG}
    g = group(client) or client.create(f"{API}/coordinate-groups", [{
        "name": GROUP, "description": f"Layout of sites/{site.name}.yaml by make netbox-topology", "tags": [tag]}])[0]
    want = layout(site)
    devices = {d["name"]: d["id"] for d in client.list("dcim/devices", {"site": site.name})}
    missing = sorted(set(want) - set(devices))
    if missing:
        raise ValueError(f"devices of the model missing in NetBox, run make netbox-sync first: {missing}")
    have = coordinates(client, g["id"], {i: n for n, i in devices.items()})
    out = Changes()
    stale = [{"id": c["id"]} for name, c in have.items() if name not in want]
    if stale:
        client.delete(f"{API}/coordinate", stale)
        out.deleted = len(stale)
    new = [{"device": devices[n], "group": g["id"], "x": x, "y": y, "tags": [tag]}
           for n, (x, y) in want.items() if n not in have]
    if new:
        client.create(f"{API}/coordinate", new)
        out.created = len(new)
    moved = [{"id": have[n]["id"], "x": x, "y": y} for n, (x, y) in want.items()
             if n in have and (have[n]["x"], have[n]["y"]) != (x, y)]
    if moved:
        client.update(f"{API}/coordinate", moved)
        out.moved = len(moved)
    roles = {r["slug"]: r["id"] for r in client.list("dcim/device-roles")}
    client.request("POST", f"{API}/images/save", {str(roles[s]): img for s, img in role_images(site).items() if s in roles})
    return out


def query(site_id: int, group_id: int) -> dict:
    """The query of the topology page and its XML export that draws the site with the layout."""
    return {"site_id": site_id, "group": group_id, "show_cables": "True", "show_unconnected": "True",
            "node_label_items": "devicename", "straight_cables": "True"}


def drawn(client, site_id: int, group_id: int) -> tuple[set[str], Counter]:
    """The devices and the cables per device pair that the plugin draws, read from its draw.io export."""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(client.request("GET", f"{API}/xml-export", query=query(site_id, group_id), text=True))
    cells = list(root.iter("mxCell"))
    nodes = {c.get("id"): c.get("value") for c in cells if c.get("vertex") == "1"}
    pairs = Counter(tuple(sorted((nodes[c.get("source")], nodes[c.get("target")]))) for c in cells if c.get("edge") == "1")
    return set(nodes.values()), pairs


def problems(client, site: Site, site_id: int) -> list[str]:
    """How the drawing in NetBox differs from the model: the stored layout, the devices and the cables drawn."""
    g = group(client)
    if g is None:
        return [f"no coordinate group {GROUP}"]
    names = {d["id"]: d["name"] for d in client.list("dcim/devices", {"site_id": site_id})}
    have = {n: (c["x"], c["y"]) for n, c in coordinates(client, g["id"], names).items()}
    out = [f"{n}: at {have.get(n)}, the layout puts it at {xy}" for n, xy in sorted(layout(site).items()) if have.get(n) != xy]
    out += [f"{n}: a coordinate the layout lacks" for n in sorted(set(have) - set(layout(site)))]
    nodes, pairs = drawn(client, site_id, g["id"])
    want_nodes = set(site.hosts) | {sw for f in site.fabrics.values() for sw in f.switches}
    out += [f"{n}: not drawn" for n in sorted(want_nodes - nodes)]
    out += [f"{n}: drawn, not in the model" for n in sorted(nodes - want_nodes)]
    want_pairs = cable_pairs(site)
    out += [f"{a} - {b}: {pairs[(a, b)]} cables drawn, the model has {want_pairs[(a, b)]}"
            for a, b in sorted(set(pairs) | set(want_pairs)) if pairs[(a, b)] != want_pairs[(a, b)]]
    return out
