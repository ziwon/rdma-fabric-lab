"""NetBox from a site model (ADR 0008).

    python -m netbox render sites/cel1.yaml                      offline, the objects as JSON
    python -m netbox sync sites/cel1.yaml -i <inventory> ...     make NetBox hold them
    python -m netbox diff sites/cel1.yaml -i <inventory> ...     read only, exit 1 on a difference
    python -m netbox topology sites/cel1.yaml -i <inventory> ... lay out the topology drawing, exit 1 if it differs after
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from fleet import site as site_model

from . import client as nb
from . import model, reconcile, topology


def _load(path: Path):
    s = site_model.load(path)
    problems = site_model.problems(s)
    if problems:
        raise SystemExit(f"{path}: invalid site model: {problems[0]}")
    return s


def _jsonable(objects):
    def conv(v):
        if isinstance(v, model.Ref):
            return {"ref": v.kind, "key": v.key}
        return v
    return {kind: {k: {f: conv(v) for f, v in fields.items()} for k, fields in items.items()} for kind, items in objects.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="netbox", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("render", "sync", "diff", "topology"):
        p = sub.add_parser(name)
        p.add_argument("site", type=Path)
        if name != "render":
            p.add_argument("-i", "--inventory", type=Path, action="append", required=True)
            p.add_argument("--url", default=nb.DEFAULT_URL)
    args = parser.parse_args(argv)
    wanted = model.desired(_load(args.site))
    if args.command == "render":
        print(json.dumps(_jsonable(wanted), indent=1, sort_keys=True))
        print(f"{args.site}: {sum(model.counts(wanted).values())} objects {model.counts(wanted)}", file=sys.stderr)
        return 0
    try:
        client = nb.Client(args.url, nb.token(nb.secrets(args.inventory)))
        if args.command == "topology":
            s = _load(args.site)
            done = topology.apply(client, s)
            site_id = client.list("dcim/sites", {"slug": s.name})[0]["id"]
            left = topology.problems(client, s, site_id)
            for p in left:
                print(p, file=sys.stderr)
            print(f"{args.site}: layout {done}, {len(left)} differences left")
            return 1 if left else 0
        plan = reconcile.plan(client, wanted)
        if args.command == "diff":
            for change in plan.changes:
                print(change)
            print(f"{args.site}: {len(plan.changes)} differences from NetBox {plan.count()}")
            return 1 if plan.changes else 0
        done = reconcile.apply(client, wanted, plan)
        after = reconcile.plan(client, wanted)
    except nb.NetBoxError as exc:
        print(f"netbox {args.command}: {exc}", file=sys.stderr)
        return 2
    print(f"{args.site}: applied {done}, {len(after.changes)} differences left")
    for change in after.changes:
        print(change, file=sys.stderr)
    return 1 if after.changes else 0


if __name__ == "__main__":
    sys.exit(main())
