# a-soft-roce / k8s-multus-net1 / 20261115T221719Z

Status: **failed**. A Multus secondary IP network beside Cilium. Two ordinary pods on different workers get eth0 from Cilium and net1, a macvlan child of the lab NIC with a cluster-wide Whereabouts address from the inventory's Multus pool. The run checks both networks across nodes, the default route, address bookkeeping when the pods are recreated, and the CNI configuration of a worker after a reboot. net1 is an IP network only. It shows no RDMA device, provider or permission inside a pod.

| check | status | failure class | detail |
|---|---|---|---|
| multus-config-first | passed |  | cp1 00-multus.conf first, delegate /etc/cni/net.d/05-cilium.conflist; worker1 00-multus.conf first, delegate /etc/cni/net.d/05-cilium.conflist; worker2 00-multus.conf first, delegate /etc/cni/net.d/05-cilium.conflist |
| network-attachment-from-inventory | passed |  | rdma-lab-net in rdma-lab: macvlan bridge on master lab0, Whereabouts 10.77.0.100-10.77.0.199 in 10.77.0.0/24, no gateway |
| test-pods-ready | passed |  | net1-server on worker1, net1-client on worker2, admitted by PodSecurity restricted in rdma-lab |
| pods-have-eth0-and-net1 | passed |  | net1-server on worker1: eth0 10.244.2.93 from cilium, net1 10.77.0.100; net1-client on worker2: eth0 10.244.1.9 from cilium, net1 10.77.0.101 |
| default-route-on-eth0 | passed |  | net1-server default via 10.244.2.64 dev eth0 ; net1-client default via 10.244.1.109 dev eth0  |
| eth0-cross-node | passed |  | net1-client fetched 'net1-server' from http://10.244.2.93:8080/ through eth0 on attempt 1 |
| net1-cross-node | passed |  | net1-client fetched 'net1-server' from http://10.77.0.100:8080/ through net1 on attempt 1 |
| net1-on-lab-nic | failed | assertion-failed | worker1: flows {}, want both directions between 10.77.0.100 and 10.77.0.101 |
| no-duplicate-addresses | passed |  | net1 {'net1-server': '10.77.0.100', 'net1-client': '10.77.0.101'} each held once by Whereabouts, 2 allocations in all, none twice |
| recreate-releases-addresses | passed |  | deletion released {'net1-server': '10.77.0.100', 'net1-client': '10.77.0.101'}; the recreated pods got {'net1-server': '10.77.0.100', 'net1-client': '10.77.0.101'}, each held once |
| reboot-keeps-cni-order | failed | assertion-failed | worker2: the first CNI file is 05-cilium.conflist, not 00-multus.conf (files ['05-cilium.conflist']) |
| new-pod-after-reboot-gets-net1 | passed |  | net1-client on worker2: eth0 10.244.1.152 from cilium, net1 10.77.0.101; net1-client reached net1-server over net1 again |
| test-namespace-removed | passed |  | namespace rdma-lab, its pods and every Whereabouts allocation of it are gone |
| no-process-left | passed |  | cp1: 0 stopped by the runner, 0 remaining; worker1: 0 stopped by the runner, 0 remaining; worker2: 0 stopped by the runner, 0 remaining |

Host: Ubuntu 24.04.5 LTS 7.0.0-43-generic.
Guest cp1: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest worker1: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest worker2: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
