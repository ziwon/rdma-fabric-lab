# a-soft-roce / k8s-baseline / 20261115T053843Z

Status: **failed**. PLAN A2 baseline of the kubeadm cluster with Cilium in VXLAN tunnel mode and kube-proxy kept. Two test pods on different workers exercise pod to pod traffic, cluster DNS, a ClusterIP Service and a NetworkPolicy, while the management and lab NICs of both workers are captured.

| check | status | failure class | detail |
|---|---|---|---|
| nodes-ready | passed |  | cp1 Ready 192.168.140.11; worker1 Ready 192.168.140.12; worker2 Ready 192.168.140.13 |
| cilium-agents-ready | failed | assertion-failed | worker1 has 0 ready Cilium agents (cilium-j5rb7 Pending) |
| test-pods-ready | failed | timeout | pod server is Pending and not Ready (no reason); pod server runs on None, want worker1 |
| pod-to-pod-cross-node | not_run | not-attempted |  |
| cluster-dns | not_run | not-attempted |  |
| clusterip-service | not_run | not-attempted |  |
| network-policy-blocks | not_run | not-attempted |  |
| network-policy-allows | not_run | not-attempted |  |
| vxlan-on-mgmt-nic | not_run | not-attempted |  |
| no-pod-traffic-on-lab-nic | not_run | not-attempted |  |
| test-namespace-removed | passed |  | namespace rfl-20261115t053843z and every object labeled rdma-fabric-lab/run=20261115T053843Z are gone |
| no-process-left | passed |  | cp1: 0 stopped by the runner, 0 remaining; worker1: 0 stopped by the runner, 0 remaining; worker2: 0 stopped by the runner, 0 remaining |

Host: Ubuntu 24.04.5 LTS 7.0.0-42-generic.
Guest cp1: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest worker1: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest worker2: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
