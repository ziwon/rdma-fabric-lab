# a-soft-roce / vm-rxe-baseline / 20261116T003428Z

Status: **passed**. RXE baseline between two worker VMs over Soft-RoCE. rping and perftest Read, Write and Send run over RDMA CM while both NICs of both workers are captured. Bandwidth is a software measurement of this host and guest pair.

| check | status | failure class | detail |
|---|---|---|---|
| rxe-bound-to-lab-nic | passed |  | worker1 rxe0 on lab0 ACTIVE, worker2 rxe0 on lab0 ACTIVE |
| route-via-lab-nic | passed |  | worker1 to 10.77.0.13 via lab0, worker2 to 10.77.0.12 via lab0 |
| rping-verified | passed |  | 10 iterations of 64 bytes validated with -V, both ends exit 0 |
| rdma-read | passed |  | 1000 x 4096 B, 2.33 Gb/sec average, GID index 1 chosen by rdma_cm |
| rdma-write | passed |  | 1000 x 4096 B, 2.04 Gb/sec average, GID index 1 chosen by rdma_cm |
| rdma-send | passed |  | 1000 x 4096 B, 2.46 Gb/sec average, GID index 1 chosen by rdma_cm |
| roce-on-lab-nic | passed |  | worker1 lab0 15333 RoCEv2 frames, worker2 lab0 15333 RoCEv2 frames |
| no-roce-on-mgmt-nic | passed |  | worker1 mgmt enp0s5 0 RoCEv2, 0 SSH SYN, 14 ICMP echo, worker1 uplink enp0s3 0 RoCEv2, 32 SSH SYN, 0 ICMP echo, worker2 mgmt enp0s5 0 RoCEv2, 0 SSH SYN, 16 ICMP echo, worker2 uplink enp0s3 0 RoCEv2, 29 SSH SYN, 0 ICMP echo |
| no-process-left | passed |  | worker1: 0 stopped by the runner, 0 remaining; worker2: 0 stopped by the runner, 0 remaining |

Software RDMA measurement of this host and guest pair. No pass threshold applies.

| metric | value | unit | absent reason |
|---|---|---|---|
| rdma_read.bw_average | 2.33 | Gb/sec |  |
| rdma_read.bw_peak | 2.33 | Gb/sec |  |
| rdma_read.msg_rate | 0.071082 | Mpps |  |
| rdma_write.bw_average | 2.04 | Gb/sec |  |
| rdma_write.bw_peak | 2.23 | Gb/sec |  |
| rdma_write.msg_rate | 0.062346 | Mpps |  |
| rdma_send.bw_average | 2.46 | Gb/sec |  |
| rdma_send.bw_peak | 2.47 | Gb/sec |  |
| rdma_send.msg_rate | 0.075048 | Mpps |  |

Host: Ubuntu 24.04.5 LTS 7.0.0-43-generic.
Guest worker1: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest worker2: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
