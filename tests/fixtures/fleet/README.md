# Fleet fixtures

`cel1-inventory-list.json` is the output of

```text
ANSIBLE_CONFIG=ansible/ansible.cfg .venv/bin/ansible-inventory -i ansible/inventories/cel1 --list
```

with the `_meta.profile` key removed and keys sorted, as

```text
.venv/bin/python -c "import json,sys; d=json.load(sys.stdin); d.get('_meta',{}).pop('profile',None); print(json.dumps(d, indent=4, sort_keys=True))"
```

run with ansible-core 2.21.6 on 2026-11-16 against the inventory that `make fleet-render`
wrote from `sites/cel1.yaml`. The contract checker tests change a copy of it in memory.
Every address in it is an invented cel1 address.
