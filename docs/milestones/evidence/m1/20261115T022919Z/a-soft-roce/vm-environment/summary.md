# a-soft-roce / vm-environment / 20261115T022919Z

Status: **passed**.

| component | locked | observed | agrees |
|---|---|---|---|
| lab.guest_image | 20261017 | 20261017 | True |
| lab.guest_kernel | 6.8.0-147-generic | 6.8.0-147-generic | True |
| lab.guest_os | Ubuntu 24.04.5 LTS | Ubuntu 24.04.5 LTS | True |
| lab.perftest | 24.01.0+0.38-1build2 | 24.01.0+0.38-1build2 | True |
| lab.rdma_core | 50.0-2ubuntu0.2 | 50.0-2ubuntu0.2 | True |

| check | status | detail |
|---|---|---|
| guest-versions-match-lock | passed | lab.guest_kernel=6.8.0-147-generic, lab.guest_os=Ubuntu 24.04.5 LTS, lab.perftest=24.01.0+0.38-1build2, lab.rdma_core=50.0-2ubuntu0.2 |
| guest-image-build | passed | build.info serial ['20261017', '20261017'] against lab.guest_image ref 20261017 |
| guest-state-collected | passed | 16 snapshots from each of worker1, worker2 |
| host-state-collected | passed | kernel 7.0.0-42-generic, 5 snapshots |
