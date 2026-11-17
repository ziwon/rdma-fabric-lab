# Track A fixtures

Every file here is output captured from a real run on 2026-11-15. Nothing was typed by hand. Guests were the
QEMU harness guests (Ubuntu 24.04.5, kernel 6.8.0-147-generic, rdma-core 50.0-2ubuntu0.2, perftest
24.01.0+0.38-1build2). Run directories live under the gitignored `results/` of the machine that ran them.

| fixture | source |
|---|---|
| `perftest/ib_{read,write,send}_bw.client.stdout`, `ib_{read,send}_bw.server.stdout` | run `20261115T020022Z` vm-rxe-baseline, `raw/rdma-*/worker{2-client,1-server}.stdout` |
| `perftest/ib_read_bw.client-port-down.stderr` | run `20261115T020504Z`, lab link of worker2 down, `raw/rdma-read/worker2-client.stderr` |
| `perftest/ib_read_bw.client-addr-error.stdout` | run `20261115T020658Z`, `netem loss 100%` on worker1, `raw/rdma-read/worker2-client.stdout` |
| `rping/client.stdout`, `rping/server.stdout` | run `20261115T020022Z`, `raw/rping-verified/` |
| `rping/client-enodev.stderr` | run `20261115T020504Z`, `raw/rping-verified/worker2-client.stderr` |
| `rping/client-etimedout.stderr` | run `20261115T020658Z`, `raw/rping-verified/worker2-client.stderr` |
| `rdma/link-active.json` | run `20261115T020022Z`, `raw/rxe-bound-to-lab-nic/worker1-rdma-link.json` |
| `rdma/link-down.json` | run `20261115T020504Z`, `raw/rxe-bound-to-lab-nic/worker2-rdma-link.json` |
| `rdma/cm_id-*-listen.json` | run `20261115T020022Z`, the readiness listing saved as `raw/*/worker1-server.cm_id.json` |
| `pcap/mgmt.pcap` | run `20261115T020022Z`, `raw/traffic/worker2-mgmt.pcap`, whole file |
| `pcap/lab-first-64-frames.pcap` | run `20261115T020022Z`, `raw/traffic/worker2-lab.pcap`, cut after its first 64 records |
| `ansible/bootstrap-first-run.recap.txt` | first `make bootstrap` on fresh guests, from `PLAY RECAP` to the end |
| `ansible/bootstrap-rerun.recap.txt` | run `20261115T020040Z` vm-rxe-rerun, `raw/bootstrap-rerun-unchanged/bootstrap.yml.log` from `PLAY RECAP` on |
| `collect/guest-report.txt` | run `20261115T015858Z` vm-environment, `raw/versions/worker1.txt` |
| `pcap/mgmt-markers.pcap` | run `20261115T045337Z` vm-rxe-baseline on the Kubernetes guests, `raw/traffic/worker1-mgmt.pcap`, whole file |
| `k8s/get-nodes.json`, `k8s/cilium-pods.json`, `k8s/test-pods.json` | run `20261115T050159Z` k8s-baseline, stdout of `raw/nodes-ready/get-nodes.json`, `raw/cilium-agents-ready/pods.json`, `raw/test-pods-ready/pods.json`, each item reduced to the fields the parsers read |
| `k8s/nslookup-kubernetes-default.stdout` | run `20261115T050159Z`, stdout of `raw/cluster-dns/kubernetes.default.svc.cluster.local.txt` |
| `k8s/worker1-mgmt-vxlan.pcap`, `k8s/worker1-lab.pcap` | run `20261115T050159Z`, `raw/traffic/worker1-{mgmt,lab}.pcap`, whole files |
| `k8s/worker1-mgmt-vxlan-dropped.pcap` | run `20261115T050030Z`, UDP 8472 dropped on worker1 INPUT, `raw/traffic/worker1-mgmt.pcap` |
| `ha/probes.txt`, `ha/etcd-members.json`, `ha/ha-cp2-addr.json`, `ha/etcd-health-one-down.json` | run `20261115T052220Z` k8s-ha-vip, `raw/api-survives-holder-loss/probes.txt` and the stdout of `raw/control-planes-joined/etcd-members.json`, `raw/vip-holder-identified/ha-cp2-addr.json`, `raw/cluster-works-after-loss/etcd-health.json` |
| `host/*` | the M1 verification host: `ip -j addr show dev docker0`, `ip -j route show table all dev docker0`, `ip -j route show table 52 100.100.100.100`, `docker network inspect bridge`, `virsh net-dumpxml default` |
| `m3/podsecurity-dry-run-baseline.stderr` | run `20261115T221204Z` k8s-hostnet-rdma, stderr of `raw/pod-security-as-declared/dry-run-baseline.txt` |
| `m3/ibv-devinfo.stdout` | run `20261115T221204Z`, stdout of `raw/ibv-devinfo-in-pods/worker1-rdma-server.txt` |
| `m3/limits-pod.stdout`, `m3/limits-ssh-user.stdout` | run `20261115T222929Z` k8s-hostnet-rdma, stdout of `raw/memlock-in-pods/worker1-rdma-server.txt` and `raw/memlock-in-pods/worker1-ssh-user.txt` |
| `m3/cni-ls.stdout`, `m3/00-multus.conf`, `m3/05-cilium.conflist` | run `20261115T222245Z` k8s-multus-net1, stdout of `raw/multus-config-first/worker1-ls.txt`, `worker1-00-multus.conf` and `worker1-delegate.txt`. The CNI layout before Cilium got its own directory, which the layout check must refuse |
| `m3-cni-dir/cni-ls.stdout`, `m3-cni-dir/00-multus.conf`, `m3-cni-dir/05-cilium.conflist` | run `20261116T053228Z` k8s-multus-net1, stdout of `raw/cni-dir-holds-only-multus/worker1-ls.txt`, `worker1-00-multus.conf` and `worker1-delegate.txt` |
| `m3-cni-dir/window-waited.stdout`, `m3-cni-dir/containerd-waited.stdout` | run `20261116T053228Z`, stdout of `raw/pod-at-boot-waits-for-multus/worker2-window.txt` and `worker2-net1-client-containerd.txt`. The boot was at epoch 1794807261 (`boot-ids.json`) |
| `m3-cni-dir/cni-ls-cilium-copy.stdout`, `m3-cni-dir/window-cilium-copy.stdout`, `m3-cni-dir/containerd-cilium-copy.stdout` | run `20261116T053519Z` k8s-multus-net1, with a copy of `05-cilium.conflist` in worker2's `/etc/cni/net.d`, stdout of `raw/cni-dir-holds-only-multus/worker2-ls.txt`, `raw/pod-at-boot-waits-for-multus/worker2-window.txt` and `worker2-net1-client-containerd.txt`. The boot was at epoch 1794807432 |
| `m3-cni-dir/window-straddled.stdout`, `m3-cni-dir/containerd-straddled.stdout` | run `20261116T061046Z` k8s-multus-net1, stdout of `raw/pod-at-boot-waits-for-multus/worker2-window.txt` and `worker2-net1-client-containerd.txt`. Its last sample read NetworkReady after Multus wrote its file. The boot was at epoch 1794809558 |
| `m3-cni-dir/window-survived.stdout`, `m3-cni-dir/containerd-survived.stdout` | run `20261116T165752Z` k8s-multus-net1 of M7, stdout of `raw/pod-at-boot-waits-for-multus/worker2-window.txt` and `worker2-net1-client-containerd.txt`. A `systemctl daemon-reload` earlier in that boot had dropped the kubelet slices' shutdown ordering, so `00-multus.conf` survived the reboot. The boot was at epoch 1794848339 |
| `m3/net1-server-ip-addr.stdout` | run `20261115T222245Z`, stdout of `raw/pods-have-eth0-and-net1/net1-server-ip-addr.txt` |
| `m3/net1-server-ip-route.stdout` | run `20261115T222245Z`, stdout of `raw/default-route-on-eth0/net1-server.txt` |
| `m3/net1-server-ip-route-get.stdout` | run `20261115T223641Z` k8s-multus-net1, stdout of `raw/default-route-on-eth0/net1-server-get.txt` |
| `m3/ippools.json` | run `20261115T222245Z`, stdout of `raw/no-duplicate-addresses/ippools.json`, each item reduced to its name and spec |
| `m3/net1-pods.json` | run `20261115T222245Z`, stdout of `raw/pods-have-eth0-and-net1/pods.json`, each item reduced to its name, its `k8s.v1.cni.cncf.io/` annotations and its node |

The recaps start at `PLAY RECAP` because the lines before it carry absolute paths of the machine.
The host captures cover only default bridges and the Tailscale MagicDNS route, which name no person or site.
