"""JSON schemas for kubeconform from the CustomResourceDefinitions that an environment's releases render.

kubeconform's default schemas cover only the kinds of Kubernetes itself, so it skips a custom resource unchecked. Each
served version of each CRD here becomes <dir>/<group>/<kind in lower case>_<version>.json, which LOCATION finds. The
schemas are strict: an object that lists its properties allows no other key, unless the CRD preserves unknown fields
there, so a misspelled key of a custom resource fails as it does for a built-in kind under -strict.
"""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import yaml

# kubeconform -schema-location template, relative to the schema directory.
LOCATION = "{{ .Group }}/{{ .ResourceKind }}_{{ .ResourceAPIVersion }}.json"
PRESERVE = "x-kubernetes-preserve-unknown-fields"
INT_OR_STRING = "x-kubernetes-int-or-string"
# Every object has these at its root, whether or not the CRD's schema names them.
ROOT_FIELDS = {"apiVersion": {"type": "string"}, "kind": {"type": "string"}, "metadata": {"type": "object"}}


def crds(manifest: str) -> list[dict]:
    """The CustomResourceDefinitions of a rendered manifest."""
    return [d for d in yaml.safe_load_all(manifest) if d and d.get("kind") == "CustomResourceDefinition"
            and str(d.get("apiVersion", "")).startswith("apiextensions.k8s.io/")]


def kinds(definitions: list[dict]) -> set[tuple[str, str]]:
    """(group, kind) of every definition, as kubeconform reports a resource's version and kind."""
    return {(d["spec"]["group"], d["spec"]["names"]["kind"]) for d in definitions}


def strict(schema: dict) -> dict:
    """The structural OpenAPI v3 schema of a CRD version as a strict JSON schema."""
    out = copy.deepcopy(schema)
    _convert(out)
    return out


def _convert(node) -> None:
    if isinstance(node, list):
        for item in node:
            _convert(item)
        return
    if not isinstance(node, dict):
        return
    if node.pop("nullable", False) and isinstance(node.get("type"), str):
        node["type"] = [node["type"], "null"]
    if node.get(INT_OR_STRING) and "type" not in node:
        node["type"] = ["integer", "string"]
    for key in ("properties", "patternProperties"):
        for child in (node.get(key) or {}).values():
            _convert(child)
    for key in ("items", "additionalProperties", "not"):
        if isinstance(node.get(key), (dict, list)):
            _convert(node[key])
    for key in ("allOf", "anyOf", "oneOf"):
        _convert(node.get(key) or [])
    if node.get("properties") and "additionalProperties" not in node and not node.get(PRESERVE):
        node["additionalProperties"] = False


def write(definitions: list[dict], out: Path) -> list[Path]:
    """Write one schema per served version under out, which this replaces. Returns the files written."""
    shutil.rmtree(out, ignore_errors=True)
    written = []
    for crd in definitions:
        spec = crd["spec"]
        for version in spec["versions"]:
            if not version.get("served", True) or "schema" not in version:
                continue
            schema = strict(version["schema"]["openAPIV3Schema"])
            schema.setdefault("properties", {})
            for field, rule in ROOT_FIELDS.items():
                schema["properties"].setdefault(field, rule)
            path = out / spec["group"] / f"{spec['names']['kind'].lower()}_{version['name']}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(schema, indent=1, sort_keys=True) + "\n")
            written.append(path)
    return written


def summary(results: list[dict], custom: set[tuple[str, str]]) -> tuple[dict[str, int], list[str]]:
    """Counts per kubeconform status of one file, and the problems: every invalid or erroring resource, and every
    resource skipped although one of the CRDs defines its kind, which means a schema went missing."""
    counts = {"valid": 0, "invalid": 0, "error": 0, "skipped": 0}
    problems = []
    for r in results:
        status = r["status"].removeprefix("status").lower()
        counts[status] = counts.get(status, 0) + 1
        group = r.get("version", "").rpartition("/")[0]
        name = f"{r.get('kind')} {r.get('name')}"
        if status in ("invalid", "error"):
            problems.append(f"{name}: {r.get('msg', '').strip()}")
        elif status == "skipped" and (group, r.get("kind")) in custom:
            problems.append(f"{name}: skipped, though a CRD of this environment defines {r.get('kind')}")
    return counts, problems
