# a-soft-roce / k8s-multus-net1 / 20261115T235500Z

Status: **failed**. A Multus secondary IP network beside Cilium. Two ordinary pods on different workers get eth0 from Cilium and net1, a macvlan child of the lab NIC with a cluster-wide Whereabouts address from the inventory's Multus pool. The run checks both networks across nodes, the default route, address bookkeeping when the pods are recreated, and the CNI configuration of a worker after a reboot. net1 is an IP network only. It shows no RDMA device, provider or permission inside a pod.

| check | status | failure class | detail |
|---|---|---|---|
| multus-config-first | failed | assertion-failed | worker2: the default delegate /host/etc/cni/net.d/00-0-rfl-break.conflist is not JSON |
| network-attachment-from-inventory | passed |  | rdma-lab-net in rdma-lab: macvlan bridge on master lab0, Whereabouts 10.77.0.100-10.77.0.199 in 10.77.0.0/24, no gateway and no route |
| test-pods-ready | failed | timeout | pods ['net1-server', 'net1-client'] not Ready within 180 s |
| pods-have-eth0-and-net1 | not_run | not-attempted | not attempted: test-pods-ready did not pass, so the result would not mean what the check says |
| default-route-on-eth0 | not_run | not-attempted | not attempted: test-pods-ready did not pass, so the result would not mean what the check says |
| eth0-cross-node | not_run | not-attempted | not attempted: test-pods-ready did not pass, so the result would not mean what the check says |
| net1-cross-node | not_run | not-attempted | not attempted: test-pods-ready did not pass, so the result would not mean what the check says |
| net1-on-lab-nic | not_run | not-attempted | not attempted: test-pods-ready did not pass, so the result would not mean what the check says |
| no-duplicate-addresses | not_run | not-attempted | not attempted: test-pods-ready did not pass, so the result would not mean what the check says |
| recreate-releases-addresses | not_run | not-attempted | not attempted: test-pods-ready did not pass, so the result would not mean what the check says |
| reboot-keeps-cni-order | not_run | not-attempted | not attempted: test-pods-ready did not pass, so the result would not mean what the check says |
| new-pod-after-reboot-gets-net1 | not_run | not-attempted | not attempted: test-pods-ready did not pass, so the result would not mean what the check says |
| test-namespace-removed | passed |  | namespace rdma-lab, its pods and every Whereabouts allocation of it are gone |
| no-process-left | failed | assertion-failed | still running: worker1 197329 |

Host: Ubuntu 24.04.5 LTS 7.0.0-43-generic.
Guest cp1: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest worker1: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest worker2: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
