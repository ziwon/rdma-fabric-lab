"""A small NetBox REST client on the standard library, and the token it needs from the NetBox guest.

The token lives only in the guest, in /etc/rfl/netbox/secrets.json, readable by root. It is read over
SSH for each command and kept in memory, never written on the host.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from inventory.__main__ import _ansible_inventory
from run.inventory import ssh_argv

DEFAULT_URL = "http://127.0.0.1:22352"
SECRETS = "/etc/rfl/netbox/secrets.json"
GUEST_GROUP = "netbox_guest"
PAGE = 1000


class NetBoxError(RuntimeError):
    pass


class Client:
    """on_request, when set, is called with (method, path and query, HTTP status or 0, seconds) after every request, so a
    run can record each call. Neither the token nor a request body is passed to it."""

    def __init__(self, url: str, token: str, timeout_s: float = 120):
        self.base = url.rstrip("/") + "/api/"
        self.on_request = None
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", "Content-Type": "application/json"}
        self.timeout_s = timeout_s

    def request(self, method: str, path: str, body=None, query: dict | None = None, text: bool = False):
        url = path if path.startswith("http") else self.base + path.strip("/") + "/"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=self.headers)
        start, status = time.monotonic(), 0
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                status = resp.status
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            raise NetBoxError(f"{method} {url}: HTTP {exc.code}: {exc.read().decode(errors='replace')[:600]}") from None
        except urllib.error.URLError as exc:
            raise NetBoxError(f"{method} {url}: {exc.reason}") from None
        finally:
            if self.on_request:
                rows = f" ({len(body)} rows)" if isinstance(body, list) else ""
                self.on_request(method, url.split("/api/", 1)[-1] + rows, status, time.monotonic() - start)
        if text:
            return raw.decode()
        return json.loads(raw) if raw else None

    def status(self) -> dict:
        return self.request("GET", "status")

    def list(self, endpoint: str, query: dict | None = None) -> list[dict]:
        out, page = [], self.request("GET", endpoint, query={**(query or {}), "limit": PAGE})
        while True:
            out += page["results"]
            if not page.get("next"):
                return out
            page = self.request("GET", page["next"])

    def create(self, endpoint: str, rows: list[dict]) -> list[dict]:
        return self.request("POST", endpoint, rows)

    def update(self, endpoint: str, rows: list[dict]) -> list[dict]:
        return self.request("PATCH", endpoint, rows)

    def delete(self, endpoint: str, rows: list[dict]) -> None:
        self.request("DELETE", endpoint, rows)


def guest_hostvars(inventories: list[Path]) -> dict:
    """The connection variables of the one host of the NetBox guest group."""
    argv = [_ansible_inventory(), "--list", *[a for i in inventories for a in ("-i", str(i))]]
    listing = json.loads(subprocess.run(argv, capture_output=True, text=True, check=True).stdout)
    hosts = listing.get(GUEST_GROUP, {}).get("hosts", [])
    if len(hosts) != 1:
        raise NetBoxError(f"the inventories name {len(hosts)} hosts in {GUEST_GROUP}, want the one NetBox guest")
    return listing["_meta"]["hostvars"][hosts[0]]


def secrets(inventories: list[Path]) -> dict:
    done = subprocess.run([*ssh_argv(guest_hostvars(inventories)), f"sudo cat {SECRETS}"], capture_output=True, text=True,
                          timeout=60)
    if done.returncode != 0:
        raise NetBoxError(f"reading {SECRETS} in the NetBox guest exited {done.returncode}: {done.stderr.strip()[:300]}")
    return json.loads(done.stdout)


def token(s: dict) -> str:
    return f"nbt_{s['api_key']}.{s['api_token']}"
