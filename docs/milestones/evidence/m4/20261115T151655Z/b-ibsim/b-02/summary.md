# b-ibsim / b-02 / 20261115T151655Z

Status: **passed**. Four HCAs on two switches joined by two parallel links, from a hand-written net file. OpenSM discovers the fabric and fills both forwarding tables across the inter-switch links. A management simulation, no data path.

| check | status | failure class | detail |
|---|---|---|---|
| image-pinned | passed |  | rfl-ibsim:m4 in ibsim1 is sha256:cb3e3550f4f60de5414abe55dbe81e45fd68106ef5ecc4cfa4d701b6083ee279, the ibsim.image pin |
| container-locked-down | passed |  | ibsim1 is a kvm guest on kernel 6.8.0-147-generic; the container runs the locked image as 10001:10001 with no network, no device, no capabilities, no-new-privileges, a read-only root and AppArmor docker-default, and every process in it runs as UID 10001 |
| subnet-up | passed |  | master at LID 1, 1 SUBNET UP, every sweep routed by minhop |
| topology-discovered | passed |  | 2 switches, 4 HCAs and 6 links, as expected |
| lids-unique | passed |  | 4 endpoint LIDs, non-zero, unique, LMC 0 |
| port-states | passed |  | 20 ports as expected, 12 of them Active/LinkUp |
| routes-shortest | passed |  | 8 switch routes to endpoint LIDs, each one hop closer to its endpoint |
| lid-reachability | passed |  | 6 LIDs answer with their node name |
| no-container-left | passed |  | guest.sh stop removed rfl-ibsim-20261115T151655Z-b-02; no Track B container remains |
| host-untouched | passed |  | no simulator process and no Track B container on the host at the start and the end of the run |

Observations of this simulator run in the Track B guest. No pass threshold applies.

| metric | value | unit | absent reason |
|---|---|---|---|
| opensm.first_subnet_up_s | 0.003 | s |  |

Host: Ubuntu 24.04.5 LTS 7.0.0-43-generic.
Guest ibsim1: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
