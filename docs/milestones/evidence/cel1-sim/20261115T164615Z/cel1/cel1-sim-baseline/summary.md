# cel1 / cel1-sim-baseline / 20261115T164615Z

Status: **passed**. The cel1 GPU cluster as QEMU guests on this host, built by the fleet targets from wiped guests: 3 control planes behind the kube-vip VIP, 8 DGX B200 nodes and 2 PCIe GPU nodes, Cilium with kube-proxy replacement and native routing, and the GPU Operator. The guests have no GPU and no InfiniBand, and the rails carry Ethernet frames. The scenario checks the cluster against the site model, then cuts the power of the VIP holder and shows the API answers on the VIP again.

| check | status | failure class | detail |
|---|---|---|---|
| interfaces-match-site-model | passed |  | 93 interfaces of 13 hosts carry their site model names and addresses |
| nodes-ready-with-class-labels | passed |  | 13 of 13 Nodes Ready on their node IPs; every kubelet registers the k8s_node_labels of its class (3 control-plane, 8 dgx-b200, 2 pcie-gpu) and the Nodes carry them; live nvidia.com/gpu.deploy.* labels: none |
| control-planes-joined | passed |  | control planes cel1-cp-01, cel1-cp-02, cel1-cp-03 Ready with kube-apiserver and etcd, members cel1-cp-01, cel1-cp-02, cel1-cp-03, endpoint 10.140.0.10:6443 |
| api-on-vip | passed |  | cel1-pcie-01 /livez on 10.140.0.10 ok; /readyz ok from cel1-cp-01, cel1-cp-02, cel1-cp-03 through admin.conf on the VIP |
| no-kube-proxy | passed |  | no kube-proxy object, 0 KUBE chains on 13 nodes, every agent KubeProxyReplacement True mgmt0 |
| cilium-api-direct | passed |  | agent env {'KUBERNETES_SERVICE_HOST': '10.140.0.10', 'KUBERNETES_SERVICE_PORT': '6443'}; cilium-w8v74 deleted on cel1-dgx-03, cilium-ggtl4 Ready with 0 container restarts, its log connects to https://10.140.0.10:6443 |
| test-pods-ready | passed |  | server 10.160.3.204 on cel1-dgx-01, client 10.160.6.151 on cel1-dgx-02 |
| pod-to-pod-native-routing | passed |  | http://10.160.3.204:8080/ returned 'server' after 1 attempts, last exit 0 'server'; 10.160.3.204 via 10.140.0.21 dev mgmt0 src 10.140.0.22 uid 1000 
    cache; cel1-dgx-01 mgmt0 20 pod-to-pod, 0 VXLAN, 0 Geneve; cel1-dgx-02 mgmt0 20 pod-to-pod, 0 VXLAN, 0 Geneve |
| cluster-dns | passed |  | server.rfl-20261115t164615z.svc.cluster.local -> 10.170.135.17; kubernetes.default.svc.cluster.local -> 10.170.0.1 |
| clusterip-service | passed |  | http://server.rfl-20261115t164615z.svc.cluster.local:80/ returned 'server' after 1 attempts, last exit 0 'server' |
| no-pod-traffic-on-storage-or-rail-nics | passed |  | 20 storage and rail captures, 20 with marker pings, 0 pod, Service or tunnel frames |
| dgx-containerd-defaults-to-nvidia | passed |  | 8 nodes nvidia /usr/bin/nvidia-container-runtime, 5 nodes runc /usr/local/sbin/runc; server container 0f5eb6cab333 on cel1-dgx-01 runs through /usr/bin/nvidia-container-runtime, stand-in calls create, start |
| gpu-operator-installed | passed |  | Deployments {'gpu-operator': True, 'gpu-operator-node-feature-discovery-gc': True, 'gpu-operator-node-feature-discovery-master': True}; 8 DGX kubelets register both opt-outs; found: ClusterPolicy state ready, Ready reason NoGPUNodes: No GPU node found, watching for new nodes to join the cluster.; nodes with nvidia.com/gpu.present=true: none; nodes with an NVIDIA PCI device label: none; live nvidia.com/gpu.deploy.* labels: none; DaemonSets ready/desired {'gpu-operator-node-feature-discovery-worker': '13/13'} |
| syslog-to-central-address | passed |  | markers of 13 nodes arrived at cel1-sim-syslog on 10.140.0.5:514, each over TCP from its node IP |
| test-namespace-removed | passed |  | namespace rfl-20261115t164615z and every object labeled rdma-fabric-lab/run=20261115T164615Z are gone |
| vip-holder-identified | passed |  | lease plndr-cp-lock held by cel1-cp-01, VIP 10.140.0.10 on cel1-cp-01 only |
| api-survives-holder-loss | passed |  | cel1-cp-01 powered off; 5 of 10 probes failed, API back on 10.140.0.10 after 7.534 s; lease and VIP now on cel1-cp-02 |
| cluster-works-after-loss | passed |  | from cel1-cp-02 through the VIP: nodes listed, ConfigMap written, read and deleted; etcd healthy 2 of 3 |
| holder-rejoins | passed |  | cel1-cp-01 Ready again, etcd healthy 3 of 3 |
| no-process-left | passed |  | cel1-cp-01: 0 stopped by the runner, 0 remaining; cel1-cp-02: 0 stopped by the runner, 0 remaining; cel1-cp-03: 0 stopped by the runner, 0 remaining; cel1-dgx-01: 0 stopped by the runner, 0 remaining; cel1-dgx-02: 0 stopped by the runner, 0 remaining; cel1-dgx-03: 0 stopped by the runner, 0 remaining; cel1-dgx-04: 0 stopped by the runner, 0 remaining; cel1-dgx-05: 0 stopped by the runner, 0 remaining; cel1-dgx-06: 0 stopped by the runner, 0 remaining; cel1-dgx-07: 0 stopped by the runner, 0 remaining; cel1-dgx-08: 0 stopped by the runner, 0 remaining; cel1-pcie-01: 0 stopped by the runner, 0 remaining; cel1-pcie-02: 0 stopped by the runner, 0 remaining; cel1-sim-syslog: 0 stopped by the runner, 0 remaining |

Measured on these simulation guests. No pass threshold applies to the outage length.

| metric | value | unit | absent reason |
|---|---|---|---|
| ha_vip.api_unavailable_seconds | 7.534 | s |  |

Host: Ubuntu 24.04.5 LTS 7.0.0-43-generic.
Guest cel1-cp-01: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest cel1-cp-02: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest cel1-cp-03: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest cel1-dgx-01: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest cel1-dgx-02: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest cel1-dgx-03: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest cel1-dgx-04: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest cel1-dgx-05: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest cel1-dgx-06: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest cel1-dgx-07: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest cel1-dgx-08: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest cel1-pcie-01: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest cel1-pcie-02: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
Guest cel1-sim-syslog: Ubuntu 24.04.5 LTS 6.8.0-147-generic.
