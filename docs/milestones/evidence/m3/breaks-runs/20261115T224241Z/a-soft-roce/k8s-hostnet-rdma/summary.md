# a-soft-roce / k8s-hostnet-rdma / 20261115T224241Z

Status: **failed**. The vm-rxe-baseline tests repeated between a hostNetwork pod on each worker. The pods run the pinned RDMA test image and use the node's RXE device against the lab NIC addresses, while every NIC of both nodes is captured. This shows a container can use the node's RDMA device. It does not show a pod-private interface, device isolation or Cilium's pod data path. Bandwidth is a software measurement of this host and guest pair.

| check | status | failure class | detail |
|---|---|---|---|
| rdma-pods-ready | failed | timeout | pod rdma-client is Pending and not Ready; pod rdma-client runs , want sha256:756e51cfbd6399905cef22945b3be3daa143f258b688f081f9e1c8b5fe5e280d |
| pod-tools-match-lock | not_run | not-attempted | not attempted: rdma-pods-ready did not pass, so the result would not mean what the check says |
| pod-security-as-declared | not_run | not-attempted | not attempted: rdma-pods-ready did not pass, so the result would not mean what the check says |
| narrower-profiles-fail | not_run | not-attempted | not attempted: rdma-pods-ready did not pass, so the result would not mean what the check says |
| ibv-devinfo-in-pods | not_run | not-attempted | not attempted: rdma-pods-ready did not pass, so the result would not mean what the check says |
| memlock-in-pods | not_run | not-attempted | not attempted: rdma-pods-ready did not pass, so the result would not mean what the check says |
| rxe-bound-to-lab-nic | not_run | not-attempted | not attempted: rdma-pods-ready did not pass, so the result would not mean what the check says |
| route-via-lab-nic | not_run | not-attempted | not attempted: rdma-pods-ready did not pass, so the result would not mean what the check says |
| rping-verified | not_run | not-attempted | not attempted: rdma-pods-ready did not pass, so the result would not mean what the check says |
| rdma-read | not_run | not-attempted | not attempted: rdma-pods-ready did not pass, so the result would not mean what the check says |
| rdma-write | not_run | not-attempted | not attempted: rdma-pods-ready did not pass, so the result would not mean what the check says |
| rdma-send | not_run | not-attempted | not attempted: rdma-pods-ready did not pass, so the result would not mean what the check says |
| roce-on-lab-nic | not_run | not-attempted | not attempted: rdma-pods-ready did not pass, so the result would not mean what the check says |
| no-roce-on-mgmt-nic | not_run | not-attempted | not attempted: rdma-pods-ready did not pass, so the result would not mean what the check says |
| rdma-pods-removed | passed |  | namespace rdma-lab and every pod labeled rdma-fabric-lab/run=20261115T224241Z are gone |
| no-process-left | passed |  | worker1: 0 stopped by the runner, 0 remaining; worker2: 0 stopped by the runner, 0 remaining |

Host: Ubuntu 24.04.5 LTS 7.0.0-43-generic.
Guest worker1: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest worker2: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
