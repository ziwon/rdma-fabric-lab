"""The topology drawing of a site model (ADR 0008), against a fake NetBox with the topology views plugin."""

import copy
from collections import Counter
from pathlib import Path

from fleet import site
from netbox import topology

REPO = Path(__file__).resolve().parents[2]
CEL1 = site.load(REPO / "sites/cel1.yaml")
API = topology.API


class FakeNetBox:
    """Devices and roles of cel1, the plugin's coordinate groups and coordinates, and its draw.io export, which draws
    the devices of the site and the cables in `cables`."""

    def __init__(self):
        names = sorted(CEL1.hosts) + sorted(sw for f in CEL1.fabrics.values() for sw in f.switches)
        self.store = {"dcim/devices": {i: {"id": i, "name": n} for i, n in enumerate(names, 1)},
                      "dcim/device-roles": {i: {"id": i, "slug": s} for i, s in enumerate(topology.role_images(CEL1), 1)},
                      f"{API}/coordinate-groups": {}, f"{API}/coordinate": {}}
        self.next_id = 1000
        self.cables = [(a.node, b.node) for f in CEL1.fabrics.values() for a, b in f.links]
        self.images = None
        self.writes = 0

    def list(self, endpoint, query=None):
        out = list(self.store[endpoint].values())
        for key, value in (query or {}).items():
            if key in ("name", "group"):
                out = [o for o in out if o[key] == value]
        return copy.deepcopy(out)

    def create(self, endpoint, rows):
        self.writes += 1
        made = []
        for row in rows:
            obj = {**row, "id": self.next_id}
            self.store[endpoint][self.next_id] = obj
            self.next_id += 1
            made.append(copy.deepcopy(obj))
        return made

    def update(self, endpoint, rows):
        self.writes += 1
        for row in rows:
            self.store[endpoint][row["id"]].update(row)

    def delete(self, endpoint, rows):
        self.writes += 1
        for row in rows:
            del self.store[endpoint][row["id"]]

    def request(self, method, path, body=None, query=None, text=False):
        if path == f"{API}/images/save":
            self.images = body
            return {"status": "Ok"}
        assert path == f"{API}/xml-export" and query["show_unconnected"] == "True"
        ids = {d["name"]: d["id"] for d in self.store["dcim/devices"].values()}
        cells = [f'<mxCell id="node_{i}" value="{n}" vertex="1"/>' for n, i in ids.items()]
        cells += [f'<mxCell id="edge_{k}" edge="1" source="node_{ids[a]}" target="node_{ids[b]}"/>'
                  for k, (a, b) in enumerate(self.cables)]
        return f"<mxfile><diagram><mxGraphModel><root>{''.join(cells)}</root></mxGraphModel></diagram></mxfile>"


def test_the_layout_puts_spines_over_leaves_over_the_cabled_hosts_over_the_rest():
    rows = topology.tiers(CEL1)
    assert rows[0] == [f"cel1-ib-spine-0{i}" for i in range(1, 5)]
    assert rows[1] == [f"cel1-ib-leaf-r{r}" for r in range(8)]
    assert rows[2] == [f"cel1-dgx-0{i}" for i in range(1, 9)]
    assert rows[3] == ["cel1-cp-01", "cel1-cp-02", "cel1-cp-03", "cel1-pcie-01", "cel1-pcie-02"]
    at = topology.layout(CEL1)
    assert len(at) == 25 and len(set(at.values())) == 25
    for i, row in enumerate(rows):
        xs = [at[n][0] for n in row]
        assert {at[n][1] for n in row} == {i * topology.Y_STEP}
        assert xs == sorted(xs) and sum(xs) == 0  # left to right, centered


def test_rail_leaves_stand_over_the_hosts_of_their_rail():
    at = topology.layout(CEL1)
    for r in range(8):
        assert at[f"cel1-ib-leaf-r{r}"][0] == at[f"cel1-dgx-0{r + 1}"][0]


def test_roles_get_icons_of_the_plugin():
    assert topology.role_images(CEL1) == {
        "control-plane": f"{topology.IMG}/server.svg", "dgx-b200": f"{topology.IMG}/server.svg",
        "pcie-gpu": f"{topology.IMG}/server.svg", "leaf": f"{topology.IMG}/access-switch.svg",
        "spine": f"{topology.IMG}/core-switch.svg"}


def test_apply_lays_out_once_then_changes_nothing():
    nb = FakeNetBox()
    done = topology.apply(nb, CEL1)
    assert (done.created, done.moved, done.deleted) == (25, 0, 0)
    assert topology.problems(nb, CEL1, 1) == []
    assert set(nb.images.values()) == set(topology.role_images(CEL1).values())
    writes = nb.writes
    again = topology.apply(nb, CEL1)
    assert (again.created, again.moved, again.deleted) == (0, 0, 0) and nb.writes == writes


def test_a_moved_device_is_found_and_put_back():
    nb = FakeNetBox()
    topology.apply(nb, CEL1)
    c = next(c for c in nb.store[f"{API}/coordinate"].values() if c["device"] == 1)
    c["x"] += 7
    name = nb.store["dcim/devices"][1]["name"]
    assert [p for p in topology.problems(nb, CEL1, 1) if p.startswith(f"{name}: at")]
    assert topology.apply(nb, CEL1).moved == 1
    assert topology.problems(nb, CEL1, 1) == []


def test_a_coordinate_the_layout_lacks_is_deleted():
    nb = FakeNetBox()
    topology.apply(nb, CEL1)
    nb.store["dcim/devices"][99] = {"id": 99, "name": "made-by-hand"}
    g = topology.group(nb)
    nb.create(f"{API}/coordinate", [{"device": 99, "group": g["id"], "x": 0, "y": 0}])
    assert "made-by-hand: a coordinate the layout lacks" in topology.problems(nb, CEL1, 1)
    assert topology.apply(nb, CEL1).deleted == 1


def test_a_missing_cable_and_a_missing_layout_are_problems():
    nb = FakeNetBox()
    assert topology.problems(nb, CEL1, 1) == [f"no coordinate group {topology.GROUP}"]
    topology.apply(nb, CEL1)
    nb.cables.remove(("cel1-dgx-01", "cel1-ib-leaf-r0"))
    assert topology.problems(nb, CEL1, 1) == [
        "cel1-dgx-01 - cel1-ib-leaf-r0: 0 cables drawn, the model has 1"]


def test_the_cel1_cables_join_device_pairs_as_the_fabric_says():
    pairs = topology.cable_pairs(CEL1)
    assert sum(pairs.values()) == 128
    assert Counter(pairs.values()) == Counter({1: 64, 2: 32})


def test_the_runner_wants_the_plugin_the_lock_pins():
    import yaml
    from run import netbox as runner
    lock = yaml.safe_load((REPO / "versions.lock.yaml").read_text())["components"]
    assert runner.locked_plugin(lock) == "5.0.3"
    assert lock["netbox.topology_views"]["source"].endswith("netbox_topology_views-5.0.3-py3-none-any.whl")
