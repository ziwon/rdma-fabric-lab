# a-soft-roce / k8s-baseline / 20261115T060758Z

Status: **passed**. PLAN A2 baseline of the kubeadm cluster with Cilium in VXLAN tunnel mode and kube-proxy kept. Two test pods on different workers exercise pod to pod traffic, cluster DNS, a ClusterIP Service and a NetworkPolicy, while the management and lab NICs of both workers are captured.

| check | status | failure class | detail |
|---|---|---|---|
| nodes-ready | passed |  | cp1 Ready 192.168.140.11; worker1 Ready 192.168.140.12; worker2 Ready 192.168.140.13 |
| cilium-agents-ready | passed |  | cp1 cilium-gthw2; worker1 cilium-55wwn; worker2 cilium-8knw9 |
| test-pods-ready | passed |  | server 10.244.1.155 on worker1, client 10.244.2.44 on worker2 |
| pod-to-pod-cross-node | passed |  | http://10.244.1.155:8080/ returned 'server' after 1 attempts, last exit 0 'server' |
| cluster-dns | passed |  | server.rfl-20261115t060758z.svc.cluster.local -> 10.110.221.216; kubernetes.default.svc.cluster.local -> 10.96.0.1 |
| clusterip-service | passed |  | http://server.rfl-20261115t060758z.svc.cluster.local:80/ returned 'server' after 1 attempts, last exit 0 'server' |
| network-policy-blocks | passed |  | the request was blocked after 2 attempts, last exit 1 'wget: download timed out\ncommand terminated with exit code 1' |
| network-policy-allows | passed |  | the request succeeded after 1 attempts, last exit 0 'server' |
| vxlan-on-mgmt-nic | passed |  | worker1 enp0s5 140 VXLAN frames on UDP 8472; worker2 enp0s5 127 VXLAN frames on UDP 8472 |
| no-pod-traffic-on-lab-nic | passed |  | worker1 enp0s4 0 VXLAN, 0 pod CIDR, 16 ICMP echo; worker2 enp0s4 0 VXLAN, 0 pod CIDR, 16 ICMP echo |
| test-namespace-removed | passed |  | namespace rfl-20261115t060758z and every object labeled rdma-fabric-lab/run=20261115T060758Z are gone |
| no-process-left | passed |  | cp1: 0 stopped by the runner, 0 remaining; worker1: 0 stopped by the runner, 0 remaining; worker2: 0 stopped by the runner, 0 remaining |

Host: Ubuntu 24.04.5 LTS 7.0.0-42-generic.
Guest cp1: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest worker1: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest worker2: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
