"""versions.lock.yaml: schema, pin shape, and per-scope completeness."""

from __future__ import annotations

import re
import sys
from importlib import metadata
from pathlib import Path

import yaml

from .manifest import schema_errors

MOVING_REFS = {"latest", "stable", "current", "master", "main", "head", "edge", "nightly"}

# Lock component -> installed distribution it pins. tooling.python is the interpreter itself.
TOOLING_DISTRIBUTIONS = {
    "tooling.ansible": "ansible",
    "tooling.ansible_core": "ansible-core",
    "tooling.ansible_lint": "ansible-lint",
    "tooling.yamllint": "yamllint",
}

VALUE_PATTERN = {
    "git-sha": re.compile(r"^[0-9a-f]{40}$"),
    "image-digest": re.compile(r"^sha256:[0-9a-f]{64}$"),
    "checksum": re.compile(r"^sha256:[0-9a-f]{64}$"),
}


def load(lock_path: Path) -> dict:
    return yaml.safe_load(lock_path.read_text())


def problems(lock: dict) -> list[str]:
    found = schema_errors(lock, "versions-lock.schema.json")
    if found:
        return found
    components = lock["components"]
    for name, component in components.items():
        value, kind = component["value"], component["kind"]
        if value is None:
            continue
        if value.strip().lower() in MOVING_REFS:
            found.append(f"{name}: {value} is a moving reference, pin an immutable value")
        pattern = VALUE_PATTERN.get(kind)
        if pattern and not pattern.match(value):
            found.append(f"{name}: {value} is not a valid {kind}")
    for scope, members in lock["scopes"].items():
        found += [
            f"scope {scope}: {member} is not a component"
            for member in members
            if member not in components
        ]
    return found


def undecided(lock: dict, scope: str) -> list[str]:
    """Components of the scope that still have value null."""
    if scope not in lock["scopes"]:
        raise KeyError(f"unknown scope {scope}. Known: {', '.join(sorted(lock['scopes']))}")
    return [m for m in lock["scopes"][scope] if lock["components"][m]["value"] is None]


def observed_tooling() -> dict[str, str | None]:
    """Versions of the interpreter and distributions this process runs on."""
    observed: dict[str, str | None] = {
        "tooling.python": f"{sys.version_info.major}.{sys.version_info.minor}"
    }
    for component, distribution in TOOLING_DISTRIBUTIONS.items():
        try:
            observed[component] = metadata.version(distribution)
        except metadata.PackageNotFoundError:
            observed[component] = None
    return observed


def tooling_drift(lock: dict, observed: dict[str, str | None]) -> list[str]:
    """Every tooling component whose installed version differs from the lock."""
    return [
        f"{component}: the lock pins {lock['components'][component]['value']}, this environment has {found or 'nothing'}"
        for component, found in sorted(observed.items())
        if component in lock["components"] and lock["components"][component]["value"] != found
    ]
