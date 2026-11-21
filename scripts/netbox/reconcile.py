"""Make NetBox hold the objects of model.desired(), and say how it differs.

Only objects with the managed tag are read, changed or deleted, so anything a person adds without
the tag stays. A plan compares every managed object with the model by its natural key. An object
the model lacks is deleted, one it adds is created, and one whose fields differ is updated. A key
that changes, such as a cable moved to another port, is a delete and a create. Deletes run first in
reverse kind order, so cables go before the interfaces they join. Creates and updates follow in kind
order, so every reference already has an id.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import KINDS, TAG, Ref, cable_key, interface_key

ENDPOINTS = {
    "tag": "extras/tags", "manufacturer": "dcim/manufacturers", "device-type": "dcim/device-types",
    "role": "dcim/device-roles", "site": "dcim/sites", "device": "dcim/devices", "interface": "dcim/interfaces",
    "prefix": "ipam/prefixes", "ip": "ipam/ip-addresses", "cable": "dcim/cables",
}
SLUGGED = ("tag", "manufacturer", "device-type", "role", "site")
CHUNK = 100


@dataclass(frozen=True)
class Change:
    action: str  # create, update or delete
    kind: str
    key: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.action} {self.kind} {self.key}{': ' + self.detail if self.detail else ''}"


@dataclass
class Plan:
    changes: list[Change] = field(default_factory=list)
    existing: dict[str, dict[str, dict]] = field(default_factory=dict)  # kind -> key -> API object
    ids: dict[str, dict[int, str]] = field(default_factory=dict)  # kind -> id -> key

    def count(self) -> dict[str, int]:
        out = {"create": 0, "update": 0, "delete": 0}
        for c in self.changes:
            out[c.action] += 1
        return out


def _value(v):
    return v.get("value") if isinstance(v, dict) else v


def _ref_id(v):
    return v.get("id") if isinstance(v, dict) else v


def key_of(kind: str, obj: dict, ids: dict[str, dict[int, str]]) -> str | None:
    if kind in SLUGGED:
        return obj["slug"]
    if kind == "device":
        return obj["name"]
    if kind == "interface":
        device = ids["device"].get(_ref_id(obj["device"]))
        return interface_key(device, obj["name"]) if device else None
    if kind == "prefix":
        return obj["prefix"]
    if kind == "ip":
        return obj["address"]
    if kind == "cable":
        ends = []
        for side in ("a_terminations", "b_terminations"):
            terms = obj.get(side) or []
            if len(terms) != 1 or terms[0].get("object_type") != "dcim.interface":
                return None
            ends.append(ids["interface"].get(terms[0].get("object_id")))
        return cable_key(*ends) if all(ends) else None
    raise ValueError(kind)


def observe(kind: str, obj: dict, ids: dict[str, dict[int, str]]) -> dict:
    """The fields of an API object in the shape model.desired() gives them."""
    def ref(ref_kind, value):
        key = ids[ref_kind].get(_ref_id(value)) if value is not None else None
        return Ref(ref_kind, key) if key else None

    if kind == "tag":
        return {"name": obj["name"], "color": obj["color"], "description": obj.get("description", "")}
    if kind == "manufacturer":
        return {"name": obj["name"], "description": obj.get("description", "")}
    if kind == "device-type":
        return {"model": obj["model"], "manufacturer": ref("manufacturer", obj["manufacturer"]),
                "description": obj.get("description", "")}
    if kind == "role":
        return {"name": obj["name"], "color": obj["color"], "description": obj.get("description", "")}
    if kind == "site":
        return {"name": obj["name"], "status": _value(obj["status"]), "description": obj.get("description", "")}
    if kind == "device":
        return {"device_type": ref("device-type", obj["device_type"]), "role": ref("role", obj["role"]),
                "site": ref("site", obj["site"]), "status": _value(obj["status"])}
    if kind == "interface":
        return {"device": ref("device", obj["device"]), "name": obj["name"], "type": _value(obj["type"]),
                "label": obj.get("label", ""), "description": obj.get("description", "")}
    if kind == "prefix":
        scope = ref("site", obj.get("scope_id")) if obj.get("scope_type") == "dcim.site" else None
        return {"scope": scope, "status": _value(obj["status"]), "description": obj.get("description", "")}
    if kind == "ip":
        iface = ref("interface", obj.get("assigned_object_id")) if obj.get("assigned_object_type") == "dcim.interface" else None
        return {"interface": iface, "role": _value(obj.get("role")) or None, "status": _value(obj["status"]),
                "description": obj.get("description", "")}
    if kind == "cable":
        a = ids["interface"].get(obj["a_terminations"][0]["object_id"])
        b = ids["interface"].get(obj["b_terminations"][0]["object_id"])
        return {"ends": frozenset((a, b)), "status": _value(obj["status"]), "label": obj.get("label", "")}
    raise ValueError(kind)


def comparable(kind: str, fields: dict) -> dict:
    """Desired fields in the shape observe() returns."""
    if kind == "cable":
        return {"ends": frozenset((fields["a"].key, fields["b"].key)), "status": fields["status"], "label": fields["label"]}
    return dict(fields)


def payload(kind: str, key: str, fields: dict, key_ids: dict[str, dict[str, int]], tag_id: int | None) -> dict:
    """The body that creates or updates one object."""
    def rid(ref):
        return key_ids[ref.kind][ref.key] if ref is not None else None

    body: dict
    if kind == "tag":
        body = {"name": fields["name"], "slug": key, "color": fields["color"], "description": fields["description"]}
    elif kind == "manufacturer":
        body = {"name": fields["name"], "slug": key, "description": fields["description"]}
    elif kind == "device-type":
        body = {"model": fields["model"], "slug": key, "manufacturer": rid(fields["manufacturer"]),
                "description": fields["description"]}
    elif kind == "role":
        body = {"name": fields["name"], "slug": key, "color": fields["color"], "description": fields["description"]}
    elif kind == "site":
        body = {"name": fields["name"], "slug": key, "status": fields["status"], "description": fields["description"]}
    elif kind == "device":
        body = {"name": key, "device_type": rid(fields["device_type"]), "role": rid(fields["role"]),
                "site": rid(fields["site"]), "status": fields["status"]}
    elif kind == "interface":
        body = {"device": rid(fields["device"]), "name": fields["name"], "type": fields["type"], "label": fields["label"],
                "description": fields["description"]}
    elif kind == "prefix":
        body = {"prefix": key, "scope_type": "dcim.site", "scope_id": rid(fields["scope"]), "status": fields["status"],
                "description": fields["description"]}
    elif kind == "ip":
        iface = rid(fields["interface"])
        body = {"address": key, "assigned_object_type": "dcim.interface" if iface else None, "assigned_object_id": iface,
                "role": fields["role"] or "", "status": fields["status"], "description": fields["description"]}
    elif kind == "cable":
        body = {"a_terminations": [{"object_type": "dcim.interface", "object_id": rid(fields["a"])}],
                "b_terminations": [{"object_type": "dcim.interface", "object_id": rid(fields["b"])}],
                "status": fields["status"], "label": fields["label"]}
    else:
        raise ValueError(kind)
    if kind != "tag" and tag_id is not None:
        body["tags"] = [tag_id]
    return body


def read(client, kinds=KINDS) -> tuple[dict[str, dict[str, dict]], dict[str, dict[int, str]], list[Change]]:
    """Every managed object by kind and key, the id -> key maps, and a delete for every managed object without a key."""
    existing = {k: {} for k in KINDS}
    ids: dict[str, dict[int, str]] = {k: {} for k in KINDS}
    orphans: list[Change] = []
    tags = client.list(ENDPOINTS["tag"], {"slug": TAG})
    if tags:
        existing["tag"][TAG] = tags[0]
        ids["tag"][tags[0]["id"]] = TAG
    for kind in kinds:
        if kind == "tag" or not tags:
            continue
        for obj in client.list(ENDPOINTS[kind], {"tag": TAG}):
            key = key_of(kind, obj, ids)
            if key is None or key in existing[kind]:
                orphans.append(Change("delete", kind, f"#{obj['id']}", "no key the model can name, or a duplicate"))
                existing[kind].setdefault(f"#{obj['id']}", obj)
                continue
            existing[kind][key] = obj
            ids[kind][obj["id"]] = key
    return existing, ids, orphans


def plan(client, wanted: dict[str, dict[str, dict]]) -> Plan:
    existing, ids, orphans = read(client)
    p = Plan(existing=existing, ids=ids)
    for kind in KINDS:
        have, want = existing[kind], wanted[kind]
        for key in sorted(set(have) - set(want)):
            if not key.startswith("#"):
                p.changes.append(Change("delete", kind, key))
        for key in sorted(set(want) - set(have)):
            p.changes.append(Change("create", kind, key))
        for key in sorted(set(want) & set(have)):
            now, then = observe(kind, have[key], ids), comparable(kind, want[key])
            diff = sorted(f for f in then if now.get(f) != then[f])
            if diff:
                p.changes.append(Change("update", kind, key, ", ".join(f"{f} {_show(now.get(f))} -> {_show(then[f])}" for f in diff)))
    p.changes += orphans
    return p


def _show(v) -> str:
    if isinstance(v, Ref):
        return v.key
    if isinstance(v, frozenset):
        return " = ".join(sorted(str(x) for x in v))
    return repr(v)


def apply(client, wanted: dict[str, dict[str, dict]], p: Plan, prune: bool = True) -> dict[str, int]:
    """Carry out a plan. Returns how many objects each action touched. Without prune nothing is deleted, so a managed
    object the model lacks stays, and so does the old object of a changed key."""
    done = {"create": 0, "update": 0, "delete": 0}
    by = {(c.action, c.kind): [] for c in p.changes if prune or c.action != "delete"}
    for c in p.changes:
        if (c.action, c.kind) in by:
            by[(c.action, c.kind)].append(c)
    for kind in reversed(KINDS):
        dels = by.get(("delete", kind), [])
        rows = [{"id": p.existing[kind][c.key]["id"]} for c in dels]
        for i in range(0, len(rows), CHUNK):
            client.delete(ENDPOINTS[kind], rows[i:i + CHUNK])
        done["delete"] += len(rows)
    key_ids = {k: {key: obj["id"] for key, obj in p.existing[k].items() if not key.startswith("#")} for k in KINDS}
    for kind in KINDS:
        tag_id = key_ids["tag"].get(TAG)
        creates = [c.key for c in by.get(("create", kind), [])]
        for i in range(0, len(creates), CHUNK):
            batch = creates[i:i + CHUNK]
            made = client.create(ENDPOINTS[kind], [payload(kind, k, wanted[kind][k], key_ids, tag_id) for k in batch])
            for k, obj in zip(batch, made):
                key_ids[kind][k] = obj["id"]
        done["create"] += len(creates)
        updates = [c.key for c in by.get(("update", kind), [])]
        rows = [{"id": key_ids[kind][k], **payload(kind, k, wanted[kind][k], key_ids, tag_id)} for k in updates]
        if kind == "cable":
            for row in rows:
                row.pop("a_terminations")
                row.pop("b_terminations")
        for i in range(0, len(rows), CHUNK):
            client.update(ENDPOINTS[kind], rows[i:i + CHUNK])
        done["update"] += len(rows)
    return done
