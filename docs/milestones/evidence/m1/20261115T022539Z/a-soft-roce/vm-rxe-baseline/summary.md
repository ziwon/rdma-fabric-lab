# a-soft-roce / vm-rxe-baseline / 20261115T022539Z

Status: **failed**. PLAN A1 baseline between two worker VMs over Soft-RoCE. rping and perftest Read, Write and Send run over RDMA CM while both NICs of both workers are captured. Bandwidth is a software measurement of this host and guest pair.

| check | status | failure class | detail |
|---|---|---|---|
| rxe-bound-to-lab-nic | passed |  | worker1 rxe0 on enp0s4 ACTIVE, worker2 rxe0 on enp0s4 ACTIVE |
| route-via-lab-nic | passed |  | worker1 to 10.77.0.13 via enp0s4, worker2 to 10.77.0.12 via enp0s4 |
| rping-verified | failed | timeout | server exit None, client exit 124 |
| rdma-read | failed | command-failed | server exit None, client exit 1 |
| rdma-write | failed | command-failed | server exit None, client exit 1 |
| rdma-send | failed | command-failed | server exit None, client exit 1 |
| roce-on-lab-nic | failed | assertion-failed | worker1: flows {'10.77.0.13>10.77.0.12': 5}, want both directions of ['10.77.0.12', '10.77.0.13'] only; worker2: flows {'10.77.0.13>10.77.0.12': 5}, want both directions of ['10.77.0.12', '10.77.0.13'] only |
| no-roce-on-mgmt-nic | passed |  | worker1 enp0s3 0 RoCEv2, 120 SSH SYN, worker2 enp0s3 0 RoCEv2, 157 SSH SYN |
| no-process-left | passed |  | worker1: 0 stopped by the runner, 0 remaining; worker2: 0 stopped by the runner, 0 remaining |

Software RDMA measurement of this host and guest pair. No pass threshold applies.

| metric | value | unit | absent reason |
|---|---|---|---|
| rdma_read.bw_average |  | Gb/sec | ib_read_bw did not complete: server exit None, client exit 1 |
| rdma_read.bw_peak |  | Gb/sec | ib_read_bw did not complete: server exit None, client exit 1 |
| rdma_read.msg_rate |  | Mpps | ib_read_bw did not complete: server exit None, client exit 1 |
| rdma_write.bw_average |  | Gb/sec | ib_write_bw did not complete: server exit None, client exit 1 |
| rdma_write.bw_peak |  | Gb/sec | ib_write_bw did not complete: server exit None, client exit 1 |
| rdma_write.msg_rate |  | Mpps | ib_write_bw did not complete: server exit None, client exit 1 |
| rdma_send.bw_average |  | Gb/sec | ib_send_bw did not complete: server exit None, client exit 1 |
| rdma_send.bw_peak |  | Gb/sec | ib_send_bw did not complete: server exit None, client exit 1 |
| rdma_send.msg_rate |  | Mpps | ib_send_bw did not complete: server exit None, client exit 1 |

Host: Ubuntu 24.04.5 LTS 7.0.0-42-generic.
Guest worker1: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest worker2: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
