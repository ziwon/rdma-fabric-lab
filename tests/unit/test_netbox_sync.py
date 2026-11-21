"""The NetBox export of a site model (ADR 0008), against a fake NetBox that answers in the shapes of the NetBox 4.7 API."""

import copy
from pathlib import Path

import pytest
import yaml

from fleet import site
from netbox import model, reconcile
from run import netbox as runner

REPO = Path(__file__).resolve().parents[2]
CEL1 = site.load(REPO / "sites/cel1.yaml")
REFS = {"dcim/device-types": ["manufacturer"], "dcim/devices": ["device_type", "role", "site"], "dcim/interfaces": ["device"]}
VALUES = {"dcim/sites": ["status"], "dcim/devices": ["status"], "dcim/interfaces": ["type"], "ipam/prefixes": ["status"],
          "ipam/ip-addresses": ["status", "role"], "dcim/cables": ["status"]}


class FakeNetBox:
    def __init__(self):
        self.store: dict[str, dict[int, dict]] = {}
        self.next_id = 1

    def _shape(self, endpoint, row, obj):
        for k, v in row.items():
            if k == "tags":
                obj[k] = [{"id": i, "slug": self.store["extras/tags"][i]["slug"]} for i in v]
            elif k in REFS.get(endpoint, []):
                obj[k] = {"id": v, "name": self.store.get("dcim/devices", {}).get(v, {}).get("name")} if k == "device" else {"id": v}
            elif k in VALUES.get(endpoint, []):
                obj[k] = {"value": v} if v else None
            else:
                obj[k] = v
        return obj

    def list(self, endpoint, query=None):
        out = list(self.store.get(endpoint, {}).values())
        for key, value in (query or {}).items():
            if key == "tag":
                out = [o for o in out if value in [t["slug"] for t in o.get("tags", [])]]
            else:
                out = [o for o in out if o.get(key) == value]
        return copy.deepcopy(out)

    def create(self, endpoint, rows):
        made = []
        for row in rows:
            obj = self._shape(endpoint, row, {"id": self.next_id})
            self.store.setdefault(endpoint, {})[self.next_id] = obj
            self.next_id += 1
            made.append(copy.deepcopy(obj))
        return made

    def update(self, endpoint, rows):
        for row in rows:
            self._shape(endpoint, {k: v for k, v in row.items() if k != "id"}, self.store[endpoint][row["id"]])
        return rows

    def delete(self, endpoint, rows):
        for row in rows:
            del self.store[endpoint][row["id"]]
        if endpoint == "dcim/interfaces":  # NetBox deletes the cables of a deleted interface
            gone = {r["id"] for r in rows}
            for cid, cable in list(self.store.get("dcim/cables", {}).items()):
                if {t["object_id"] for t in cable["a_terminations"] + cable["b_terminations"]} & gone:
                    del self.store["dcim/cables"][cid]


def synced():
    nb, wanted = FakeNetBox(), model.desired(CEL1)
    reconcile.apply(nb, wanted, reconcile.plan(nb, wanted))
    return nb, wanted


def test_the_cel1_model_asks_for_1142_objects():
    assert model.counts(model.desired(CEL1)) == {"tag": 1, "manufacturer": 1, "device-type": 4, "role": 5, "site": 1, "device": 25,
                                                "interface": 861, "prefix": 21, "ip": 95, "cable": 128}


def test_every_address_lies_in_a_prefix_and_the_same_model_gives_the_same_objects():
    assert model.check_addresses(model.desired(CEL1)) == []
    assert model.desired(CEL1) == model.desired(CEL1)


def test_a_cable_key_names_both_ends_in_either_order_and_rails_carry_their_label():
    assert model.cable_key("b/1", "a/2") == model.cable_key("a/2", "b/1") == "a/2 = b/1"
    cables = model.desired(CEL1)["cable"]
    assert cables["cel1-dgx-01/rail3 = cel1-ib-leaf-r3/1"]["label"] == "rail3"
    assert sum(c["label"] == "uplink" for c in cables.values()) == 64


def test_a_sync_from_nothing_creates_everything_and_leaves_no_difference():
    nb, wanted = FakeNetBox(), model.desired(CEL1)
    done = reconcile.apply(nb, wanted, reconcile.plan(nb, wanted))
    assert done == {"create": 1142, "update": 0, "delete": 0}
    assert reconcile.plan(nb, wanted).changes == []


def test_the_comparison_names_exactly_the_drift_and_the_next_sync_undoes_it():
    nb, wanted = synced()
    existing, _, _ = reconcile.read(nb)
    cable = existing["cable"]["cel1-dgx-01/rail3 = cel1-ib-leaf-r3/1"]
    nb.delete("dcim/cables", [{"id": cable["id"]}])
    ip = existing["ip"]["10.142.5.24/24"]
    nb.update("ipam/ip-addresses", [{"id": ip["id"], "description": "by hand"}])
    tag = existing["tag"][model.TAG]["id"]
    nb.create("ipam/ip-addresses", [{"address": "10.142.0.99/24", "status": "active", "tags": [tag]}])
    changes = reconcile.plan(nb, wanted).changes
    config = yaml.safe_load((REPO / "tracks/cel1/config/cel1-netbox.yaml").read_text())
    assert runner.drift_problems(changes, config) == []
    assert reconcile.apply(nb, wanted, reconcile.plan(nb, wanted)) == {"create": 1, "update": 1, "delete": 1}
    assert reconcile.plan(nb, wanted).changes == []


def test_without_prune_a_stray_managed_object_stays():
    nb, wanted = synced()
    tag = reconcile.read(nb)[0]["tag"][model.TAG]["id"]
    nb.create("ipam/ip-addresses", [{"address": "10.142.0.99/24", "status": "active", "tags": [tag]}])
    assert reconcile.apply(nb, wanted, reconcile.plan(nb, wanted), prune=False) == {"create": 0, "update": 0, "delete": 0}
    assert [str(c) for c in reconcile.plan(nb, wanted).changes] == ["delete ip 10.142.0.99/24"]


def test_an_object_without_the_managed_tag_is_never_touched():
    nb, wanted = synced()
    nb.create("ipam/ip-addresses", [{"address": "10.142.0.98/24", "status": "reserved"}])
    reconcile.apply(nb, wanted, reconcile.plan(nb, wanted))
    assert nb.list("ipam/ip-addresses", {"address": "10.142.0.98/24"})


def test_a_cable_moved_to_another_port_is_deleted_and_created():
    nb, wanted = synced()
    moved = copy.deepcopy(wanted)
    key = "cel1-dgx-01/rail3 = cel1-ib-leaf-r3/1"
    fields = moved["cable"].pop(key)
    fields["b"] = model.Ref("interface", "cel1-ib-leaf-r3/9")
    moved["cable"]["cel1-dgx-01/rail3 = cel1-ib-leaf-r3/9"] = fields
    assert sorted(str(c) for c in reconcile.plan(nb, moved).changes) == [
        "create cable cel1-dgx-01/rail3 = cel1-ib-leaf-r3/9", f"delete cable {key}"]


@pytest.mark.parametrize("ref, version", [("docker.io/netboxcommunity/netbox:v4.7.5-5.1.2", "4.7.5"),
                                          ("docker.io/netboxcommunity/netbox:v4.8.0-6.0.0", "4.8.0")])
def test_the_locked_version_comes_from_the_image_tag(ref, version):
    assert runner.locked_version({"netbox.image": {"ref": ref}}) == version


def test_the_spec_lists_the_checks_the_runner_records():
    source = (REPO / "scripts/run/netbox.py").read_text()
    assert all(f'"{c}"' in source for c in runner.check_ids())
    assert len(runner.check_ids()) == 9
