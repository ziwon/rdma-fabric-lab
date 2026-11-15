"""Counts RoCEv2 and VXLAN frames in a classic pcap file, so the control node needs no tcpdump."""

from __future__ import annotations

import ipaddress
import struct
from collections import Counter
from dataclasses import dataclass, field

ROCEV2_PORT = 4791
SSH_PORT = 22
ICMP_ECHO_REPLY, ICMP_ECHO_REQUEST = 0, 8
LINKTYPE_ETHERNET = 1
UDP_HEADER = 8
VXLAN_HEADER = 8

# magic -> (byte order, timestamp divisor)
_MAGIC = {
    b"\xd4\xc3\xb2\xa1": ("<", 1e6),
    b"\xa1\xb2\xc3\xd4": (">", 1e6),
    b"\x4d\x3c\xb2\xa1": ("<", 1e9),
    b"\xa1\xb2\x3c\x4d": (">", 1e9),
}


class PcapError(ValueError):
    pass


@dataclass
class Summary:
    frames: int = 0
    rocev2_frames: int = 0
    rocev2_flows: Counter = field(default_factory=Counter)
    ssh_syn_frames: int = 0
    icmp_echo_frames: int = 0
    tunnel_frames: int = 0
    tunnel_flows: Counter = field(default_factory=Counter)
    tunnel_inner_flows: Counter = field(default_factory=Counter)
    watch_net_frames: int = 0
    first_ts: float | None = None
    last_ts: float | None = None

    def as_dict(self) -> dict:
        return {
            "frames": self.frames,
            "rocev2_frames": self.rocev2_frames,
            "rocev2_flows": dict(sorted(self.rocev2_flows.items())),
            "ssh_syn_frames": self.ssh_syn_frames,
            "icmp_echo_frames": self.icmp_echo_frames,
            "tunnel_frames": self.tunnel_frames,
            "tunnel_flows": dict(sorted(self.tunnel_flows.items())),
            "tunnel_inner_flows": dict(sorted(self.tunnel_inner_flows.items())),
            "watch_net_frames": self.watch_net_frames,
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
        }


def _l4(frame: bytes) -> tuple[str, str, int, bytes] | None:
    """(src, dst, IP protocol, transport bytes) of an Ethernet frame carrying IPv4 or IPv6."""
    offset, ethertype = 14, frame[12:14]
    while ethertype in (b"\x81\x00", b"\x88\xa8") and len(frame) >= offset + 4:
        ethertype, offset = frame[offset + 2 : offset + 4], offset + 4
    ip = frame[offset:]
    if ethertype == b"\x08\x00" and len(ip) >= 20:
        ihl = (ip[0] & 0x0F) * 4
        return str(ipaddress.IPv4Address(ip[12:16])), str(ipaddress.IPv4Address(ip[16:20])), ip[9], ip[ihl:]
    if ethertype == b"\x86\xdd" and len(ip) >= 40:
        return str(ipaddress.IPv6Address(ip[8:24])), str(ipaddress.IPv6Address(ip[24:40])), ip[6], ip[40:]
    return None


def summarize(data: bytes, tunnel_port: int | None = None, watch_net: str | None = None) -> Summary:
    """Frame counts of a capture. tunnel_port names the UDP port of a VXLAN tunnel, whose inner IP
    flows are counted too. watch_net counts frames with an outer address in that network."""
    if len(data) < 24 or data[:4] not in _MAGIC:
        raise PcapError("not a classic pcap file")
    order, divisor = _MAGIC[data[:4]]
    linktype = struct.unpack(order + "I", data[20:24])[0]
    if linktype != LINKTYPE_ETHERNET:
        raise PcapError(f"link type {linktype} is not Ethernet")
    out = Summary()
    watched = ipaddress.ip_network(watch_net) if watch_net else None
    offset = 24
    while offset + 16 <= len(data):
        sec, frac, incl, _ = struct.unpack(order + "IIII", data[offset : offset + 16])
        if offset + 16 + incl > len(data):
            break
        frame = data[offset + 16 : offset + 16 + incl]
        offset += 16 + incl
        ts = sec + frac / divisor
        out.frames += 1
        out.first_ts = ts if out.first_ts is None else out.first_ts
        out.last_ts = ts
        parsed = _l4(frame)
        if parsed is None:
            continue
        src, dst, proto, l4 = parsed
        if watched and (ipaddress.ip_address(src) in watched or ipaddress.ip_address(dst) in watched):
            out.watch_net_frames += 1
        if proto == 17 and tunnel_port and len(l4) >= 4 and struct.unpack("!H", l4[2:4])[0] == tunnel_port:
            out.tunnel_frames += 1
            out.tunnel_flows[f"{src}>{dst}"] += 1
            inner = _l4(l4[UDP_HEADER + VXLAN_HEADER:]) if len(l4) >= UDP_HEADER + VXLAN_HEADER + 14 else None
            if inner:
                out.tunnel_inner_flows[f"{inner[0]}>{inner[1]}"] += 1
        elif proto == 17 and len(l4) >= 4 and struct.unpack("!H", l4[2:4])[0] == ROCEV2_PORT:
            out.rocev2_frames += 1
            out.rocev2_flows[f"{src}>{dst}"] += 1
        elif proto == 6 and len(l4) >= 14 and struct.unpack("!H", l4[2:4])[0] == SSH_PORT and l4[13] & 0x12 == 0x02:
            out.ssh_syn_frames += 1
        elif proto == 1 and len(l4) >= 1 and l4[0] in (ICMP_ECHO_REPLY, ICMP_ECHO_REQUEST):
            out.icmp_echo_frames += 1
    return out
