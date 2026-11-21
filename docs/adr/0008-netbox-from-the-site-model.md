# ADR 0008: NetBox from the site model

> Status: accepted, 2026-11-22, and implemented. Section 4 gives the runs.

`sites/cel1.yaml` is the single source of the cel1 region (`docs/fleet.md` section 2). It feeds the inventory, the harness profile, the Track B fabric and the rail network values, and its validator checks the address plan and the cabling. A site that runs NetBox wants the same facts there: the devices, their interfaces, the prefixes and addresses, and every cable. This ADR decides how the lab puts them there.

## 1. Decision

1. **The site model stays the source, and NetBox follows it.** `make netbox-sync` makes NetBox hold exactly the objects the model asks for. The model is never read back from NetBox, so the validator keeps every guarantee it gives today.
2. **The sync owns only objects with the tag `rfl-site-model`.** It reads, changes and deletes tagged objects alone. An object a person adds without the tag survives every sync, and an object with the tag that the model lacks is deleted.
3. **The objects follow from the model.** `scripts/netbox/model.py` turns it into 1142 objects for cel1.

   | Kind | From the model | cel1 |
   |---|---|---|
   | Site | `site` | 1 |
   | Manufacturer | one, `Site model`, for the invented device types | 1 |
   | Device type and role | one per class, and one per switch model and switch role | 4 and 5 |
   | Device | every host and switch | 25 |
   | Interface | every class interface of every host, labeled with its HCA, and every port of every switch | 861 |
   | Prefix | every network, every rail pod range, the pod and the Service range, scoped to the site | 21 |
   | IP address | every host address on its interface, the API VIP with role `vip`, the syslog address | 95 |
   | Cable | every fabric link, labeled with its rail or `uplink` | 128 |

   An InfiniBand interface gets the type of the fabric's link speed, `infiniband-ndr` for cel1. An Ethernet interface of the model has no speed, so it gets type `other`.
4. **The sync compares by natural keys.** A device is its name, an interface its device and name, a prefix or an address itself, and a cable the sorted pair of its interface keys. Deletes run first in reverse kind order, then creates and updates in kind order, all through the bulk API. `make netbox-diff` runs the same comparison without writing and exits 1 on a difference.
5. **NetBox runs only inside its own guest.** The harness profile `netbox` boots one guest, `netbox1`, with 4 vCPUs and 4 GiB. `ansible/playbooks/netbox-guest.yml` installs Docker and its compose plugin from the Ubuntu archive at the locked versions and starts netbox-docker 5.1.2, NetBox v4.7.5, with PostgreSQL and Valkey, every image pinned by digest. The host runs no container. NetBox's port 8080 reaches the host only as `127.0.0.1:22352`.
6. **The secrets stay in the guest.** The first run of the playbook makes the database and Valkey passwords, the secret key, the token pepper, the superuser's password and a v2 API token in `/etc/rfl/netbox/secrets.json`, mode 0600, root's. The playbook logs none of them. The sync, the scenario and the screenshots read the token or the password over SSH for one command and keep it in memory.

## 2. Reasons

- **Why the model, not NetBox, is the source.** The validator rejects an overlapping range, a rail port cabled to the wrong leaf and a pod range that does not fit its nodes (`docs/fleet.md` section 2.4). NetBox would accept each of them. Making NetBox the source would move those checks into NetBox scripts or into a pass over its export. An export keeps them where they are and still gives a site its NetBox.
- **Why a tag.** A NetBox is shared. People add circuits, contacts and devices the model never names. With the tag, the sync can delete what it made without touching what it did not.
- **Why a guest.** The tracks run their simulators in guests and never on the host (ADR 0002). NetBox is a service of several containers. On this host no new container runs until the kernel with the AppArmor fix is installed.
- **Why the standard library for the client.** The sync needs a few REST calls. pynetbox would add a dependency to the lock for them.

## 3. Alternatives

| Option | Why not now |
|---|---|
| NetBox as the source, with `sites/*.yaml` rendered from its export | The validator's checks would have to run on every export, and two writers of the plan would need a rule for conflicts. It is the next step if a site's people edit NetBox first |
| The NetBox Ansible collection | Each module call is one object. 1142 objects take far longer than the bulk API, and a delete of what the model dropped needs the tag logic anyway |
| NetBox on the host or in the cel1 simulation | The host runs no container now. The cel1 guests stand in for hosts of the region, and NetBox is not one of them |

## 4. Verification

- **Unit tests.** `tests/unit/test_netbox_sync.py` runs the sync against a fake NetBox that answers in the shapes of the 4.7 API: 1142 objects from nothing and no difference after, the exact drift and its undo, a stray tagged object kept without prune, an untagged object never touched, and a moved cable as a delete and a create. Turning off prune, the description comparison or the tag filter fails a test each.
- **The scenario `cel1-netbox`.** Its 8 checks: the locked NetBox version, the sync, no difference, a second sync that changes nothing, the drift that the run makes by hand found exactly and undone, an untagged address that survives, and the run's own address removed. `tests/integration/qemu-lab/acceptance-netbox.sh` runs it from a wiped guest, where the first sync creates all 1142 objects, and repeats the comparison after the guest powers off and boots again. The run of record is in `docs/fleet.md` section 15.
- **Breaks.** `tests/integration/qemu-lab/breaks-cel1-netbox.sh` fails every check once and repairs it: the lock names another NetBox, the sync skips the cables, a second writer in the guest edits an address every half second, prune is off, the hand-made address carries the tag, and the run keeps its hand-made address.

## 5. What it does not claim

- No site's real NetBox was touched. The data is the invented cel1.
- The sync writes the objects of section 1 only: no racks, platforms, VLANs, VRFs, tenants or custom fields.
- NetBox's interface type `infiniband-ndr` shows as `NDR (100 Gbps)`, a per-lane rate. The model's links are 4x NDR.

## 6. Sources

- NetBox v4.7.5, https://github.com/netbox-community/netbox/releases/tag/v4.7.5, and its REST API documentation, https://netboxlabs.com/docs/netbox/en/stable/integrations/rest-api/. `netbox/users/constants.py` at v4.7.5 for the v2 token, `nbt_` with a 12-character key and a 40-character secret.
- netbox-docker 5.1.2, https://github.com/netbox-community/netbox-docker/releases/tag/5.1.2: `docker-compose.yml`, `env/netbox.env` and `docker/super_user.py`, which creates the superuser and its token at the first start.
