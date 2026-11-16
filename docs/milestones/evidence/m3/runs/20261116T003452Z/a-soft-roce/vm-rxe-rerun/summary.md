# a-soft-roce / vm-rxe-rerun / 20261116T003452Z

Status: **passed**. RXE idempotence. A second bootstrap and rxe-up against configured workers change nothing and leave one device and one address on the lab NIC.

| check | status | failure class | detail |
|---|---|---|---|
| bootstrap-rerun-unchanged | passed |  | worker1 changed=0 failed=0 unreachable=0, worker2 changed=0 failed=0 unreachable=0 |
| rxe-up-rerun-unchanged | passed |  | worker1 changed=0 failed=0 unreachable=0, worker2 changed=0 failed=0 unreachable=0 |
| single-rxe-device | passed |  | one device, one address, one netplan file and one unit on each worker |

Host: Ubuntu 24.04.5 LTS 7.0.0-43-generic.
Guest worker1: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest worker2: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
