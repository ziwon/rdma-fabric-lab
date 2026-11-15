"""Networks this host already uses: interface addresses, routes of every table,
and the subnets of container and VM networks, whether their bridge is up or not."""

from __future__ import annotations

import ipaddress
import json
import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .plan import Span, span

# Route types that name no network in use.
SKIPPED_ROUTE_TYPES = {"multicast", "broadcast"}


@dataclass(frozen=True)
class Source:
    """One place host networks come from. spans is empty when the source could not be read."""

    name: str
    spans: list[Span]
    error: str | None = None


def spans_from_ip_addr(text: str) -> list[Span]:
    found = []
    for link in json.loads(text):
        for info in link.get("addr_info", []):
            iface = ipaddress.ip_interface(f"{info['local']}/{info['prefixlen']}")
            found.append(span(f"address {iface} on {link['ifname']}", str(iface.network)))
    return found


def spans_from_ip_route(text: str) -> list[Span]:
    found = []
    for route in json.loads(text):
        dst = route.get("dst", "")
        if dst in ("", "default") or route.get("type") in SKIPPED_ROUTE_TYPES:
            continue
        where = " ".join(
            f"{key} {route[key]}" for key in ("type", "dev", "table") if route.get(key) not in (None, "unicast")
        )
        found.append(span(f"route {dst} {where}".rstrip(), dst))
    return found


def spans_from_docker(text: str) -> list[Span]:
    found = []
    for network in json.loads(text):
        for config in (network.get("IPAM") or {}).get("Config") or []:
            if config.get("Subnet"):
                found.append(span(f"docker network {network['Name']} {config['Subnet']}", config["Subnet"]))
    return found


def spans_from_libvirt(name: str, xml_text: str) -> list[Span]:
    found = []
    for ip in ET.fromstring(xml_text).iter("ip"):
        address = ip.get("address")
        if not address:
            continue
        mask = ip.get("prefix") or ip.get("netmask") or ("64" if ":" in address else "24")
        net = ipaddress.ip_interface(f"{address}/{mask}").network
        found.append(span(f"libvirt network {name} {net}", str(net)))
    return found


def _run(argv: list[str]) -> str:
    return subprocess.run(argv, capture_output=True, text=True, check=True, timeout=30).stdout


def _read(name: str, read) -> Source:
    try:
        return Source(name, read())
    except FileNotFoundError as exc:
        return Source(name, [], f"{exc.filename} is not installed")
    except subprocess.CalledProcessError as exc:
        lines = (exc.stderr or exc.stdout or "").strip().splitlines()
        return Source(name, [], lines[-1] if lines else f"exit {exc.returncode}")
    except (subprocess.TimeoutExpired, ValueError, ET.ParseError) as exc:
        return Source(name, [], str(exc))


def _docker() -> list[Span]:
    if not shutil.which("docker"):
        raise FileNotFoundError(2, "not found", "docker")
    ids = _run(["docker", "network", "ls", "-q"]).split()
    return spans_from_docker(_run(["docker", "network", "inspect", *ids])) if ids else []


def _libvirt() -> list[Span]:
    if not shutil.which("virsh"):
        raise FileNotFoundError(2, "not found", "virsh")
    virsh = ["virsh", "-q", "-c", "qemu:///system"]
    names = _run([*virsh, "net-list", "--all", "--name"]).split()
    return [s for n in names for s in spans_from_libvirt(n, _run([*virsh, "net-dumpxml", n]))]


def collect() -> list[Source]:
    """Read every source. Nothing here changes the host."""
    return [
        _read("interface addresses", lambda: spans_from_ip_addr(_run(["ip", "-j", "addr", "show"]))),
        _read("routes of all tables, IPv4 and IPv6", lambda: spans_from_ip_route(_run(["ip", "-j", "route", "show", "table", "all"]))),
        _read("docker networks", _docker),
        _read("libvirt networks", _libvirt),
    ]
