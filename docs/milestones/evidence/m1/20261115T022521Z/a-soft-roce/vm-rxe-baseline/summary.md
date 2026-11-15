# a-soft-roce / vm-rxe-baseline / 20261115T022521Z

Status: **passed**. PLAN A1 baseline between two worker VMs over Soft-RoCE. rping and perftest Read, Write and Send run over RDMA CM while both NICs of both workers are captured. Bandwidth is a software measurement of this host and guest pair.

| check | status | failure class | detail |
|---|---|---|---|
| rxe-bound-to-lab-nic | passed |  | worker1 rxe0 on enp0s4 ACTIVE, worker2 rxe0 on enp0s4 ACTIVE |
| route-via-lab-nic | passed |  | worker1 to 10.77.0.13 via enp0s4, worker2 to 10.77.0.12 via enp0s4 |
| rping-verified | passed |  | 10 iterations of 64 bytes validated with -V, both ends exit 0 |
| rdma-read | passed |  | 1000 x 4096 B, 2.62 Gb/sec average, GID index 1 chosen by rdma_cm |
| rdma-write | passed |  | 1000 x 4096 B, 1.8 Gb/sec average, GID index 1 chosen by rdma_cm |
| rdma-send | passed |  | 1000 x 4096 B, 2.55 Gb/sec average, GID index 1 chosen by rdma_cm |
| roce-on-lab-nic | passed |  | worker1 enp0s4 15332 RoCEv2 frames, worker2 enp0s4 15332 RoCEv2 frames |
| no-roce-on-mgmt-nic | passed |  | worker1 enp0s3 0 RoCEv2, 24 SSH SYN, worker2 enp0s3 0 RoCEv2, 24 SSH SYN |
| no-process-left | passed |  | worker1: 0 stopped by the runner, 0 remaining; worker2: 0 stopped by the runner, 0 remaining |

Software RDMA measurement of this host and guest pair. No pass threshold applies.

| metric | value | unit | absent reason |
|---|---|---|---|
| rdma_read.bw_average | 2.62 | Gb/sec |  |
| rdma_read.bw_peak | 2.62 | Gb/sec |  |
| rdma_read.msg_rate | 0.079948 | Mpps |  |
| rdma_write.bw_average | 1.8 | Gb/sec |  |
| rdma_write.bw_peak | 1.89 | Gb/sec |  |
| rdma_write.msg_rate | 0.054975 | Mpps |  |
| rdma_send.bw_average | 2.55 | Gb/sec |  |
| rdma_send.bw_peak | 2.66 | Gb/sec |  |
| rdma_send.msg_rate | 0.077718 | Mpps |  |

Host: Ubuntu 24.04.5 LTS 7.0.0-42-generic.
Guest worker1: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest worker2: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
