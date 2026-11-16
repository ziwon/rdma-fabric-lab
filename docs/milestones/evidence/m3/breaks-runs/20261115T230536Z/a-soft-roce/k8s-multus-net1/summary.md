# a-soft-roce / k8s-multus-net1 / 20261115T230536Z

Status: **failed**. A Multus secondary IP network beside Cilium. Two ordinary pods on different workers get eth0 from Cilium and net1, a macvlan child of the lab NIC with a cluster-wide Whereabouts address from the inventory's Multus pool. The run checks both networks across nodes, the default route, address bookkeeping when the pods are recreated, and the CNI configuration of a worker after a reboot. net1 is an IP network only. It shows no RDMA device, provider or permission inside a pod.

| check | status | failure class | detail |
|---|---|---|---|
| multus-config-first | passed |  | cp1 00-multus.conf first, delegate /etc/cni/net.d/05-cilium.conflist; worker1 00-multus.conf first, delegate /etc/cni/net.d/05-cilium.conflist; worker2 00-multus.conf first, delegate /etc/cni/net.d/05-cilium.conflist |
| network-attachment-from-inventory | failed | command-failed | kubectl apply exited 1, get 1 |
| test-pods-ready | not_run | not-attempted | not attempted: network-attachment-from-inventory did not pass, so the result would not mean what the check says |
| pods-have-eth0-and-net1 | not_run | not-attempted | not attempted: network-attachment-from-inventory did not pass, so the result would not mean what the check says |
| default-route-on-eth0 | not_run | not-attempted | not attempted: network-attachment-from-inventory did not pass, so the result would not mean what the check says |
| eth0-cross-node | not_run | not-attempted | not attempted: network-attachment-from-inventory did not pass, so the result would not mean what the check says |
| net1-cross-node | not_run | not-attempted | not attempted: network-attachment-from-inventory did not pass, so the result would not mean what the check says |
| net1-on-lab-nic | not_run | not-attempted | not attempted: network-attachment-from-inventory did not pass, so the result would not mean what the check says |
| no-duplicate-addresses | not_run | not-attempted | not attempted: network-attachment-from-inventory did not pass, so the result would not mean what the check says |
| recreate-releases-addresses | not_run | not-attempted | not attempted: network-attachment-from-inventory did not pass, so the result would not mean what the check says |
| reboot-keeps-cni-order | not_run | not-attempted | not attempted: network-attachment-from-inventory did not pass, so the result would not mean what the check says |
| new-pod-after-reboot-gets-net1 | not_run | not-attempted | not attempted: network-attachment-from-inventory did not pass, so the result would not mean what the check says |
| test-namespace-removed | passed |  | namespace rdma-lab, its pods and every Whereabouts allocation of it are gone |
| no-process-left | passed |  | cp1: 0 stopped by the runner, 0 remaining; worker1: 0 stopped by the runner, 0 remaining; worker2: 0 stopped by the runner, 0 remaining |

Host: Ubuntu 24.04.5 LTS 7.0.0-43-generic.
Guest cp1: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest worker1: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest worker2: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
