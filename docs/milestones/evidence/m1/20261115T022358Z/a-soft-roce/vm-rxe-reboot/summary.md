# a-soft-roce / vm-rxe-reboot / 20261115T022358Z

Status: **failed**. PLAN A1 persistence. Both workers reboot, rfl-rxe.service recreates the device with no Ansible involved, and the baseline tests pass again.

| check | status | failure class | detail |
|---|---|---|---|
| guests-rebooted | passed |  | worker1 40f436e5 -> 7cc9f047, worker2 d78697ce -> bd05e9e4 |
| rxe-restored-without-ansible | failed | assertion-failed | worker1: rfl-rxe.service is inactive with result success; worker1: the journal of this boot does not show rfl-rxe adding rxe0 on enp0s4; worker2: rfl-rxe.service is inactive with result success; worker2: the journal of this boot does not show rfl-rxe adding rxe0 on enp0s4; worker1: links on enp0s4 are [], want [rxe0]; worker2: links on enp0s4 are [], want [rxe0] |
| rxe-bound-to-lab-nic | failed | assertion-failed | worker1: links on enp0s4 are [], want [rxe0]; worker2: links on enp0s4 are [], want [rxe0] |
| route-via-lab-nic | passed |  | worker1 to 10.77.0.13 via enp0s4, worker2 to 10.77.0.12 via enp0s4 |
| rping-verified | failed | command-failed | the server exited 19 before it listened, client not started |
| rdma-read | failed | command-failed | the server exited 7 before it listened, client not started |
| rdma-write | failed | command-failed | the server exited 1 before it listened, client not started |
| rdma-send | failed | command-failed | the server exited 1 before it listened, client not started |
| roce-on-lab-nic | failed | assertion-failed | worker1: flows {}, want both directions of ['10.77.0.12', '10.77.0.13'] only; worker2: flows {}, want both directions of ['10.77.0.12', '10.77.0.13'] only |
| no-roce-on-mgmt-nic | passed |  | worker1 enp0s3 0 RoCEv2, 19 SSH SYN, worker2 enp0s3 0 RoCEv2, 3 SSH SYN |
| no-process-left | passed |  | worker1: 0 stopped by the runner, 0 remaining; worker2: 0 stopped by the runner, 0 remaining |

Software RDMA measurement of this host and guest pair. No pass threshold applies.

| metric | value | unit | absent reason |
|---|---|---|---|
| rdma_read.bw_average |  | Gb/sec | ib_read_bw did not complete: the server exited 7 before it listened, client not started |
| rdma_read.bw_peak |  | Gb/sec | ib_read_bw did not complete: the server exited 7 before it listened, client not started |
| rdma_read.msg_rate |  | Mpps | ib_read_bw did not complete: the server exited 7 before it listened, client not started |
| rdma_write.bw_average |  | Gb/sec | ib_write_bw did not complete: the server exited 1 before it listened, client not started |
| rdma_write.bw_peak |  | Gb/sec | ib_write_bw did not complete: the server exited 1 before it listened, client not started |
| rdma_write.msg_rate |  | Mpps | ib_write_bw did not complete: the server exited 1 before it listened, client not started |
| rdma_send.bw_average |  | Gb/sec | ib_send_bw did not complete: the server exited 1 before it listened, client not started |
| rdma_send.bw_peak |  | Gb/sec | ib_send_bw did not complete: the server exited 1 before it listened, client not started |
| rdma_send.msg_rate |  | Mpps | ib_send_bw did not complete: the server exited 1 before it listened, client not started |

Host: Ubuntu 24.04.5 LTS 7.0.0-42-generic.
Guest worker1: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest worker2: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
