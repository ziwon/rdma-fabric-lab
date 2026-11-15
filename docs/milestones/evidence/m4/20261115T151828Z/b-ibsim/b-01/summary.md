# b-ibsim / b-01 / 20261115T151828Z

Status: **failed**. Two HCAs on one switch from a hand-written net file. OpenSM discovers the fabric, brings every port Active and assigns LIDs. A management simulation, no data path.

| check | status | failure class | detail |
|---|---|---|---|
| image-pinned | passed |  | rfl-ibsim:m4 in ibsim1 is sha256:cb3e3550f4f60de5414abe55dbe81e45fd68106ef5ecc4cfa4d701b6083ee279, the ibsim.image pin |
| container-locked-down | passed |  | ibsim1 is a kvm guest on kernel 6.8.0-147-generic; the container runs the locked image as 10001:10001 with no network, no device, no capabilities, no-new-privileges, a read-only root and AppArmor docker-default, and every process in it runs as UID 10001 |
| subnet-up | passed |  | master at LID 1, 1 SUBNET UP, every sweep routed by minhop |
| topology-discovered | failed | assertion-failed | 2 problems: missing link "hca2"[1]-"sw1"[2], missing node Hca hca2 (1 ports) |
| lids-unique | failed | assertion-failed | 1 problems: "hca2"[1] has no LID |
| port-states | failed | assertion-failed | 2 problems: "hca2"[1] is not listed, "sw1"[2] is Down/Polling to None, want Active/LinkUp to "hca2"[1] |
| routes-shortest | passed |  | 1 switch routes to endpoint LIDs, each one hop closer to its endpoint |
| lid-reachability | passed |  | 2 LIDs answer with their node name |
| no-container-left | passed |  | guest.sh stop removed rfl-ibsim-20261115T151828Z-b-01; no Track B container remains |
| host-untouched | passed |  | no simulator process and no Track B container on the host at the start and the end of the run |

Observations of this simulator run in the Track B guest. No pass threshold applies.

| metric | value | unit | absent reason |
|---|---|---|---|
| opensm.first_subnet_up_s | 0.002 | s |  |

Host: Ubuntu 24.04.5 LTS 7.0.0-43-generic.
Guest ibsim1: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
