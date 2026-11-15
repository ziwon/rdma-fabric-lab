"""Find keys of a Helm values file that the chart's own values.yaml does not have.

Helm ignores a key no template reads, so a misspelled key renders without error and changes
nothing. A key is known when its whole path exists in the chart defaults. Below a default that
is an empty map or null, the chart takes free-form keys, so anything goes.
"""

from __future__ import annotations


def unknown_keys(values: dict, defaults: dict, prefix: str = "") -> list[str]:
    found = []
    for key, value in values.items():
        path = f"{prefix}{key}"
        if key not in defaults:
            found.append(f"{path} is not a key of the chart")
            continue
        default = defaults[key]
        if not isinstance(value, dict) or default is None or default == {}:
            continue
        if not isinstance(default, dict):
            found.append(f"{path} is a map here but a {type(default).__name__} in the chart")
            continue
        found += unknown_keys(value, default, f"{path}.")
    return found
