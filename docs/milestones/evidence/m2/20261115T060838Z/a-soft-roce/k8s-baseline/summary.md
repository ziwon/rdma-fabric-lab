# a-soft-roce / k8s-baseline / 20261115T060838Z

Status: **failed**. PLAN A2 baseline of the kubeadm cluster with Cilium in VXLAN tunnel mode and kube-proxy kept. Two test pods on different workers exercise pod to pod traffic, cluster DNS, a ClusterIP Service and a NetworkPolicy, while the management and lab NICs of both workers are captured.

| check | status | failure class | detail |
|---|---|---|---|
| nodes-ready | passed |  | cp1 Ready 192.168.140.11; worker1 Ready 192.168.140.12; worker2 Ready 192.168.140.13 |
| cilium-agents-ready | passed |  | cp1 cilium-gthw2; worker1 cilium-55wwn; worker2 cilium-8knw9 |
| test-pods-ready | passed |  | server 10.244.1.73 on worker1, client 10.244.2.172 on worker2 |
| pod-to-pod-cross-node | failed | timeout | http://10.244.1.73:8080/ not after 3 attempts, last exit 1 'wget: download timed out\ncommand terminated with exit code 1' |
| cluster-dns | failed | assertion-failed | server.rfl-20261115t060838z.svc.cluster.local resolved to nothing (exit 1), want 10.106.120.155; kubernetes.default.svc.cluster.local resolved to nothing (exit 1), want 10.96.0.1 |
| clusterip-service | failed | timeout | http://server.rfl-20261115t060838z.svc.cluster.local:80/ not after 3 attempts, last exit 1 "wget: bad address 'server.rfl-20261115t060838z.svc.cluster.local:80'\ncommand ter" |
| network-policy-blocks | not_run | not-attempted | not attempted: clusterip-service did not pass, so the result would not mean what the check says |
| network-policy-allows | not_run | not-attempted | not attempted: clusterip-service did not pass, so the result would not mean what the check says |
| vxlan-on-mgmt-nic | failed | assertion-failed | worker1: pod flows inside VXLAN ['10.244.2.172>10.244.1.73'], want both of ['10.244.1.73>10.244.2.172', '10.244.2.172>10.244.1.73']; worker2: pod flows inside VXLAN ['10.244.2.172>10.244.1.73'], want both of ['10.244.1.73>10.244.2.172', '10.244.2.172>10.244.1.73'] |
| no-pod-traffic-on-lab-nic | passed |  | worker1 enp0s4 0 VXLAN, 0 pod CIDR, 16 ICMP echo; worker2 enp0s4 0 VXLAN, 0 pod CIDR, 16 ICMP echo |
| test-namespace-removed | passed |  | namespace rfl-20261115t060838z and every object labeled rdma-fabric-lab/run=20261115T060838Z are gone |
| no-process-left | passed |  | cp1: 0 stopped by the runner, 0 remaining; worker1: 0 stopped by the runner, 0 remaining; worker2: 0 stopped by the runner, 0 remaining |

Host: Ubuntu 24.04.5 LTS 7.0.0-42-generic.
Guest cp1: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest worker1: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest worker2: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
