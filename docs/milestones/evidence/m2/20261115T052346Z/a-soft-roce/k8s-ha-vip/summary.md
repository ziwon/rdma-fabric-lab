# a-soft-roce / k8s-ha-vip / 20261115T052346Z

Status: **failed**. Fleet-shaped control plane on harness guests: three control planes joined with kubeadm join --control-plane behind a kube-vip VIP on the management network. The guest holding the VIP loses power, and the API must answer on the VIP again from the surviving control planes. The guest then boots and rejoins.

| check | status | failure class | detail |
|---|---|---|---|
| control-planes-joined | passed |  | control planes ha-cp1, ha-cp2, ha-cp3 Ready with kube-apiserver and etcd, members ha-cp1, ha-cp2, ha-cp3, endpoint 192.168.140.20:6443 |
| api-on-vip | passed |  | ha-w1 /livez on 192.168.140.20 ok; /readyz ok from ha-cp1, ha-cp2, ha-cp3 through admin.conf on the VIP |
| vip-holder-identified | passed |  | lease plndr-cp-lock held by ha-cp3, VIP 192.168.140.20 on ha-cp3 only |
| api-survives-holder-loss | failed | timeout | /livez on 192.168.140.20 did not answer steadily within 120 s (82 failed probes); after the loss the lease names None and the VIP is on no host |
| cluster-works-after-loss | failed | assertion-failed | kubectl get nodes from ha-cp1 exited 1; ConfigMap write, read, delete exited 1, 1, 1; etcd healthy endpoints [], below quorum of 3 |
| holder-rejoins | passed |  | ha-cp3 Ready again, etcd healthy 3 of 3 |
| no-process-left | passed |  | ha-cp1: 0 stopped by the runner, 0 remaining; ha-cp2: 0 stopped by the runner, 0 remaining; ha-cp3: 0 stopped by the runner, 0 remaining; ha-w1: 0 stopped by the runner, 0 remaining |

Measured on these harness guests. No pass threshold applies to the outage length.

| metric | value | unit | absent reason |
|---|---|---|---|
| ha_vip.api_unavailable_seconds |  | s | the probes never succeeded after the power-off |

Host: Ubuntu 24.04.5 LTS 7.0.0-42-generic.
Guest ha-cp1: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest ha-cp2: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest ha-cp3: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest ha-w1: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
