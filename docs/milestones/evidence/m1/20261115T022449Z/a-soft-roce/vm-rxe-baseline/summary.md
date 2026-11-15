# a-soft-roce / vm-rxe-baseline / 20261115T022449Z

Status: **failed**. PLAN A1 baseline between two worker VMs over Soft-RoCE. rping and perftest Read, Write and Send run over RDMA CM while both NICs of both workers are captured. Bandwidth is a software measurement of this host and guest pair.

| check | status | failure class | detail |
|---|---|---|---|
| rxe-bound-to-lab-nic | failed | assertion-failed | worker1: rxe0 on enp0s4 is DOWN, not ACTIVE |
| route-via-lab-nic | failed | assertion-failed | worker1 routes 10.77.0.13 via enp0s3, not enp0s4 |
| rping-verified | failed | command-failed | the server exited 255 before it listened, client not started |
| rdma-read | failed | command-failed | the server exited 1 before it listened, client not started |
| rdma-write | failed | command-failed | the server exited 1 before it listened, client not started |
| rdma-send | failed | command-failed | the server exited 1 before it listened, client not started |
| roce-on-lab-nic | failed | assertion-failed | worker1: not a classic pcap file; worker2: flows {}, want both directions of ['10.77.0.12', '10.77.0.13'] only |
| no-roce-on-mgmt-nic | passed |  | worker1 enp0s3 0 RoCEv2, 42 SSH SYN, worker2 enp0s3 0 RoCEv2, 3 SSH SYN |
| no-process-left | passed |  | worker1: 0 stopped by the runner, 0 remaining; worker2: 0 stopped by the runner, 0 remaining |

Software RDMA measurement of this host and guest pair. No pass threshold applies.

| metric | value | unit | absent reason |
|---|---|---|---|
| rdma_read.bw_average |  | Gb/sec | ib_read_bw did not complete: the server exited 1 before it listened, client not started |
| rdma_read.bw_peak |  | Gb/sec | ib_read_bw did not complete: the server exited 1 before it listened, client not started |
| rdma_read.msg_rate |  | Mpps | ib_read_bw did not complete: the server exited 1 before it listened, client not started |
| rdma_write.bw_average |  | Gb/sec | ib_write_bw did not complete: the server exited 1 before it listened, client not started |
| rdma_write.bw_peak |  | Gb/sec | ib_write_bw did not complete: the server exited 1 before it listened, client not started |
| rdma_write.msg_rate |  | Mpps | ib_write_bw did not complete: the server exited 1 before it listened, client not started |
| rdma_send.bw_average |  | Gb/sec | ib_send_bw did not complete: the server exited 1 before it listened, client not started |
| rdma_send.bw_peak |  | Gb/sec | ib_send_bw did not complete: the server exited 1 before it listened, client not started |
| rdma_send.msg_rate |  | Mpps | ib_send_bw did not complete: the server exited 1 before it listened, client not started |

Host: Ubuntu 24.04.5 LTS 7.0.0-42-generic.
Guest worker1: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest worker2: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
