"""Unprivileged Ethernet learning switch for QEMU dgram netdevs over AF_UNIX sockets.

    switch.py --dir .lab/net/mgmt --pidfile .lab/net/mgmt/switch.pid --port cp1 --port worker1 ...

Port <guest> binds <dir>/<guest>.port and delivers to <dir>/<guest>.nic, the socket the guest's
QEMU binds. AF_UNIX datagrams cannot leave the host, and nothing here needs a capability.
"""

from __future__ import annotations

import argparse
import os
import selectors
import signal
import socket
import sys
from pathlib import Path

BROADCAST_BIT = 0x01
MAX_FRAME = 65536


class Switch:
    """MAC learning: a source MAC belongs to the last port it arrived on."""

    def __init__(self, ports: list[str]):
        self.ports = list(ports)
        self.table: dict[bytes, str] = {}

    def route(self, in_port: str, frame: bytes) -> list[str]:
        if len(frame) < 14:
            return []
        dst, src = frame[0:6], frame[6:12]
        if not src[0] & BROADCAST_BIT:
            self.table[src] = in_port
        if dst[0] & BROADCAST_BIT or dst not in self.table:
            return [p for p in self.ports if p != in_port]
        out = self.table[dst]
        return [] if out == in_port else [out]


def serve(directory: Path, ports: list[str], pidfile: Path) -> None:
    switch = Switch(ports)
    selector = selectors.DefaultSelector()
    sockets, peers = {}, {}
    for port in ports:
        path = directory / f"{port}.port"
        path.unlink(missing_ok=True)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.bind(str(path))
        sock.setblocking(False)
        sockets[port], peers[port] = sock, str(directory / f"{port}.nic")
        selector.register(sock, selectors.EVENT_READ, port)
    pidfile.write_text(f"{os.getpid()}\n")
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    while True:
        for key, _ in selector.select():
            try:
                frame = key.fileobj.recv(MAX_FRAME)
            except BlockingIOError:
                continue
            for out in switch.route(key.data, frame):
                try:
                    sockets[out].sendto(frame, peers[out])
                except (FileNotFoundError, ConnectionRefusedError, BlockingIOError):
                    pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="switch.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument("--pidfile", type=Path, required=True)
    parser.add_argument("--port", action="append", required=True)
    args = parser.parse_args(argv)
    serve(args.dir, args.port, args.pidfile)
    return 0


if __name__ == "__main__":
    sys.exit(main())
