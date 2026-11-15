# b-ibsim / b-04 / 20261115T151655Z

Status: **passed**. A leaf to spine link failure on the cel1 compute fabric rendered from sites/cel1.yaml: 8 DGX B200 hosts with one HCA per rail, a leaf per rail and 4 spines. The other spine links carry every route around the failure. A management simulation of ibsim and OpenSM, not of NDR hardware or firmware.

| check | status | failure class | detail |
|---|---|---|---|
| image-pinned | passed |  | rfl-ibsim:m4 in ibsim1 is sha256:cb3e3550f4f60de5414abe55dbe81e45fd68106ef5ecc4cfa4d701b6083ee279, the ibsim.image pin |
| container-locked-down | passed |  | ibsim1 is a kvm guest on kernel 6.8.0-147-generic; the container runs the locked image as 10001:10001 with no network, no device, no capabilities, no-new-privileges, a read-only root and AppArmor docker-default, and every process in it runs as UID 10001 |
| subnet-up | passed |  | master at LID 1, 1 SUBNET UP, every sweep routed by updn |
| topology-discovered | passed |  | 12 switches, 64 HCAs and 128 links, as expected |
| lids-unique | passed |  | 64 endpoint LIDs, non-zero, unique, LMC 0 |
| port-states | passed |  | 832 ports as expected, 256 of them Active/LinkUp |
| routes-shortest | passed |  | 768 switch routes to endpoint LIDs, each one hop closer to its endpoint |
| lid-reachability | passed |  | 76 LIDs answer with their node name |
| fault-resweep | passed |  | ibsim accepted Unlink "cel1-ib-leaf-r3"[35]; OpenSM logged SUBNET UP 1 more time(s), the first 0.062 s after the command |
| fault-topology | passed |  | 12 switches, 64 HCAs and 127 links, as expected; 832 ports as expected, 254 of them Active/LinkUp; every endpoint kept its LID |
| fault-routes | passed |  | 768 switch routes to endpoint LIDs, each one hop closer to its endpoint; 13 routes used the failed link: 13 moved to another port, 0 removed with their LID |
| fault-reachability | passed |  | 76 LIDs answer with their node name |
| recovery-resweep | passed |  | ibsim accepted ReLink "cel1-ib-leaf-r3"[35]; OpenSM logged SUBNET UP 1 more time(s), the first 0.07 s after the command |
| recovered | passed |  | 12 switches, 64 HCAs and 128 links, as expected; 832 ports as expected, 256 of them Active/LinkUp; every endpoint kept its LID; 768 switch routes to endpoint LIDs, each one hop closer to its endpoint; 76 LIDs answer with their node name |
| no-container-left | passed |  | guest.sh stop removed rfl-ibsim-20261115T151655Z-b-04; no Track B container remains |
| host-untouched | passed |  | no simulator process and no Track B container on the host at the start and the end of the run |

Observations of this simulator run in the Track B guest. No pass threshold applies.

| metric | value | unit | absent reason |
|---|---|---|---|
| opensm.first_subnet_up_s | 0.029 | s |  |
| opensm.resweep_after_fault_s | 0.062 | s |  |
| opensm.resweep_after_recovered_s | 0.07 | s |  |

Host: Ubuntu 24.04.5 LTS 7.0.0-43-generic.
Guest ibsim1: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
