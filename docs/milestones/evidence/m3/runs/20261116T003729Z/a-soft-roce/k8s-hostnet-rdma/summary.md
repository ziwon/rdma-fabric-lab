# a-soft-roce / k8s-hostnet-rdma / 20261116T003729Z

Status: **passed**. The vm-rxe-baseline tests repeated between a hostNetwork pod on each worker. The pods run the pinned RDMA test image and use the node's RXE device against the lab NIC addresses, while every NIC of both nodes is captured. This shows a container can use the node's RDMA device. It does not show a pod-private interface, device isolation or Cilium's pod data path. Bandwidth is a software measurement of this host and guest pair.

| check | status | failure class | detail |
|---|---|---|---|
| rdma-pods-ready | passed |  | rdma-server on worker1 with the node address 192.168.140.12; rdma-client on worker2 with the node address 192.168.140.13; both run docker.io/library/rfl-rdma-tools:m3@sha256:756e51cfbd6399905cef22945b3be3daa143f258b688f081f9e1c8b5fe5e280d |
| pod-tools-match-lock | passed |  | rdma-server ibverbs-providers 50.0-2ubuntu0.2, ibverbs-utils 50.0-2ubuntu0.2, rdmacm-utils 50.0-2ubuntu0.2, perftest 24.01.0+0.38-1build2; rdma-client ibverbs-providers 50.0-2ubuntu0.2, ibverbs-utils 50.0-2ubuntu0.2, rdmacm-utils 50.0-2ubuntu0.2, perftest 24.01.0+0.38-1build2; the same versions on the nodes and in the lock |
| pod-security-as-declared | passed |  | profile privileged-nonroot: host network, no host PID or IPC, no volume; PodSecurity baseline would refuse 2 of 2 pods; PodSecurity restricted would refuse 2 of 2 pods |
| narrower-profiles-fail | passed |  | unprivileged: exit 255, 'No IB devices found'; unprivileged-device-files: exit 2, 'Operation not permitted' |
| ibv-devinfo-in-pods | passed |  | rdma-server on worker1 rxe0 PORT_ACTIVE; rdma-client on worker2 rxe0 PORT_ACTIVE |
| memlock-in-pods | passed |  | rdma-server soft 8388608 hard 8388608 bytes (VM baseline user 776622080 776622080); rdma-client soft 8388608 hard 8388608 bytes (VM baseline user 776622080 776622080) |
| rxe-bound-to-lab-nic | passed |  | worker1 rxe0 on lab0 ACTIVE, worker2 rxe0 on lab0 ACTIVE |
| route-via-lab-nic | passed |  | worker1 to 10.77.0.13 via lab0, worker2 to 10.77.0.12 via lab0 |
| rping-verified | passed |  | 10 iterations of 64 bytes validated with -V, both ends exit 0 |
| rdma-read | passed |  | 1000 x 4096 B, 1.87 Gb/sec average, GID index 1 chosen by rdma_cm |
| rdma-write | passed |  | 1000 x 4096 B, 2.43 Gb/sec average, GID index 1 chosen by rdma_cm |
| rdma-send | passed |  | 1000 x 4096 B, 2.39 Gb/sec average, GID index 1 chosen by rdma_cm |
| roce-on-lab-nic | passed |  | worker1 lab0 15330 RoCEv2 frames, worker2 lab0 15330 RoCEv2 frames |
| no-roce-on-mgmt-nic | passed |  | worker1 mgmt enp0s5 0 RoCEv2, 0 SSH SYN, 16 ICMP echo, worker1 uplink enp0s3 0 RoCEv2, 11 SSH SYN, 0 ICMP echo, worker2 mgmt enp0s5 0 RoCEv2, 0 SSH SYN, 18 ICMP echo, worker2 uplink enp0s3 0 RoCEv2, 14 SSH SYN, 0 ICMP echo |
| rdma-pods-removed | passed |  | namespace rdma-lab and every pod labeled rdma-fabric-lab/run=20261116T003729Z are gone |
| no-process-left | passed |  | worker1: 0 stopped by the runner, 0 remaining; worker2: 0 stopped by the runner, 0 remaining |

Software RDMA measurement of this host and guest pair. No pass threshold applies.

| metric | value | unit | absent reason |
|---|---|---|---|
| memlock.soft_bytes | 8388608 | bytes |  |
| memlock.hard_bytes | 8388608 | bytes |  |
| memlock.soft_bytes | 8388608 | bytes |  |
| memlock.hard_bytes | 8388608 | bytes |  |
| rdma_read.bw_average | 1.87 | Gb/sec |  |
| rdma_read.bw_peak | 2.09 | Gb/sec |  |
| rdma_read.msg_rate | 0.057178 | Mpps |  |
| rdma_write.bw_average | 2.43 | Gb/sec |  |
| rdma_write.bw_peak | 2.45 | Gb/sec |  |
| rdma_write.msg_rate | 0.074105 | Mpps |  |
| rdma_send.bw_average | 2.39 | Gb/sec |  |
| rdma_send.bw_peak | 2.53 | Gb/sec |  |
| rdma_send.msg_rate | 0.072875 | Mpps |  |

Host: Ubuntu 24.04.5 LTS 7.0.0-43-generic.
Guest worker1: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest worker2: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
