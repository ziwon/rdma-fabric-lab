# ADR 0004: RDMA in pods and the secondary network of Track A

> Status: accepted, 2026-11-16. The Cilium CNI directory of section 1.4, accepted 2026-11-16 after the boot window of section 2.4.

These decisions sit on the shared stack of `docs/adr/0001-kubernetes-stack.md`. They hold for the lab only. cel1 runs no Multus and no RDMA test pods.

## 1. Decision

1. **The pod RDMA baseline is a hostNetwork pod with the node's RXE device.** `k8s-hostnet-rdma` runs one pod per worker in the namespace `lab_k8s_namespace` (`rdma-lab`) with `hostNetwork: true`, `dnsPolicy: ClusterFirstWithHostNet` and `privileged: true`, as uid and gid 65534, with no hostPID, no host IPC, no volume and no runtime socket. The pods repeat `vm-rxe-baseline` unchanged against the lab NIC addresses.
2. **The RDMA test image reaches the pods by digest, without a registry.** The pods reference `docker.io/library/rfl-rdma-tools@<lab.rdma_tools_image>` with `imagePullPolicy: Never`. `docs/adr/0002-execution-environments.md` section 5 says how the image is built and imported.
3. **Multus and Whereabouts give pods a second IP network.** Multus v4.3.2 runs as its thick daemon from the repo-local chart `helm/multus/chart`, which carries the upstream `deployments/multus-daemonset-thick.yml` of that tag byte for byte. The chart replaces only the two image references with `cni.multus_image` and the daemon config line of `multusAutoconfigDir`, and refuses a manifest that does not name that image exactly twice or hold that line exactly once. Whereabouts v0.9.4 comes from its OCI chart with the node slice controller off. One NetworkAttachmentDefinition, rendered from the inventory alone, gives each pod `net1`: macvlan in bridge mode on `lab_rdma_nic`, with the Whereabouts range of `lab_address_plan`. The render refuses when the workers' lab NIC names differ or the pool leaves the lab network or holds a node address.
4. **Cilium writes its CNI file where only Multus reads it.** `cni.confPath: /etc/cni/multus/net.d` in `helm/cilium/values-lab.yaml`, and `daemonConfig.multusAutoconfigDir` of the same directory in `helm/multus/values-lab.yaml`. `/etc/cni/net.d`, the directory containerd reads, holds only Multus's `00-multus.conf`. `cni.exclusive` stays `false`. A cluster built before this layout keeps Cilium's old file, and `make cni-migrate` removes it once after `make addons ENV=lab`.
5. **`net1` carries IP only.** No pod gets an RDMA device on `net1`. A pod-private RDMA path, a device plugin or the RDMA CNI is an open experiment, not part of this decision.

## 2. Reasons

### 2.1 Why `privileged`

The profiles were tried from narrowest to broadest (`.lab/evidence/m3/pod-security-probe.txt`).

| # | Profile | Result |
|---|---|---|
| 1 | Namespace at PodSecurity `restricted` | Refused at admission: hostNetwork is a host namespace |
| 2 | Namespace at PodSecurity `baseline` | Refused at admission for the same reason. hostNetwork needs the `privileged` level |
| 3 | `privileged` level, uid 65534, every capability dropped, no privilege escalation, `RuntimeDefault` seccomp, no device | No `/dev/infiniband` in the container. `ibv_devinfo` prints `No IB devices found` and exits 255 |
| 4 | Profile 3 with `/dev/infiniband` as a hostPath | The files appear with mode 0666, but the device cgroup refuses `uverbs0` and `rdma_cm` with `Operation not permitted` |
| 5 | `privileged: true`, uid and gid 65534, no hostPID, no host IPC, no volume | `rxe0` is `PORT_ACTIVE`, the process has `CapEff: 0000000000000000`, and `ib_write_bw` completes between the two workers |

Kubernetes has no field that admits one character device to a container without a device plugin, and capabilities do not change the device cgroup. So profile 5 is the narrowest that works. The user accepted `privileged` for the lab RDMA pods on 2026-11-16. Every run proves the narrower profiles still fail in `narrower-profiles-fail`, and proves with a dry-run relabel that `baseline` and `restricted` refuse both pods in `pod-security-as-declared`.

The pods run with the memlock limit of containerd's service, 8388608 bytes soft and hard. With it lowered to 65536 bytes on one worker, rping and Read, Write and Send of 4096 B x 1000 still passed (break run `20261115T225629Z`), so these tests register less than 64 KiB. `memlock-in-pods` fails when the two ends differ.

### 2.2 Why by digest without a registry

`ctr -n k8s.io images import --digests` names the image `docker.io/library/rfl-rdma-tools@<sha256 of index.json>`, and CRI resolves a pod's `name@digest` only by that name. A reference by the manifest digest failed with `no such image` (`.lab/evidence/m3/image-import-probe.txt`). No registry runs in the lab.

### 2.3 Why a repo-local Multus chart and macvlan

Multus publishes no Helm chart for v4.3.2. The plumbing working group chart is 0.1.2 with appVersion 0.1.0 and image v3.8. Carrying the pinned manifest unchanged keeps the chart equal to upstream, and `tests/unit/test_multus_chart.py` shows the chart renders the same objects as the manifest with only the image pinned. macvlan children carry their own MAC addresses. A probe put one child on the lab NIC of each worker, and each side got 5 of 5 replies, with the children's own source MACs on the wire while `lab0` stayed at promiscuity 0 (`.lab/evidence/m3/probe-macvlan.log`). So macvlan stays the type, and ipvlan, the IP alternative, was never enabled. The Whereabouts node slice controller has no work, because the attachment sets no `node_slice_size`.

### 2.4 Why Cilium's file moved

- The Multus thick daemon removes `00-multus.conf` when its config watcher ends on SIGTERM (`pkg/server/config/manager.go` at v4.3.2). After a boot it writes the file again about 18 s later.
- With Cilium's file in `/etc/cni/net.d`, containerd ran Cilium alone in that window. A pod that tolerated every NoSchedule taint, created 5.9 s after a boot, started with eth0 and no `net1`, without an error (`.lab/evidence/m3-cni-dir/probe/08-boot-window-cilium-copy/`).
- With the file moved, containerd finds no network in the window, the kubelet holds the pod, and it starts with eth0 and `net1` once Multus writes its file (`.lab/evidence/m3-cni-dir/probe/07-boot-window-tolerating/`).
- Multus keeps the delegate it found at start. A stray file that sorted before Cilium's at boot became the delegate, and after that file was deleted every new pod on the node stayed in `ContainerCreating` (`.lab/evidence/m3/shadow/`). In the new layout only Cilium writes the directory Multus takes its delegate from.
- `cni.exclusive: true` no longer reaches Multus's file, because the agent mounts only its own directory. It would still rename every other file in `/etc/cni/multus/net.d`, which is also Multus's `confDir`, where it looks up a network named by file (`.lab/evidence/m3-cni-dir/probe/09-exclusive-true/`). So it stays `false`.

`make cni-migrate` runs `ansible/playbooks/cni-migrate.yml`, which applies the role `cni_layout` to one node at a time. It refuses, and changes nothing, unless the cluster's Multus daemon config already takes its delegate from the new directory and the node holds Cilium's file there. It removes `/etc/cni/net.d/05-cilium.conflist` first and restarts the node's Multus pod second, so a pod started in between finds no network and waits instead of running Cilium alone. The file lives on each node, outside every object Helm owns, and Cilium never removes it (`cni.uninstall: false`), so Ansible, which owns the node configuration, removes it. The role reads Cilium's directory from the same values file the runner reads.

`make addons` still installs from a fresh cluster in release order, Cilium, Multus, Whereabouts, with Helm's wait on every release. After Cilium alone every node reports the network not ready, and Helm's wait passes anyway, because every Cilium pod uses the host network and tolerates the not-ready taint. Multus does too, and once it writes its file the nodes turn Ready before Whereabouts installs (`.lab/evidence/m3-cni-dir/probe/02-addons-cilium.log`, `04-addons-all.log`).

## 3. Evidence

- `k8s-hostnet-rdma` passed 16 of 16 checks in run `20261116T002950Z`, with 15336 RoCEv2 frames on each lab NIC and 0 on the management and uplink NICs. The latest run, `20261116T192258Z`, passed in `acceptance-all.sh` run `20261116T190004Z`.
- `k8s-multus-net1` passed 14 of 14 in run `20261116T072640Z` on the new layout: a pod created at boot+6.5 s, no network in all 25 samples, `00-multus.conf` at boot+19.0 s and the sandbox at boot+20.6 s. With Cilium's file put back, runs `20261116T053519Z` and `20261116T061429Z` failed `cni-dir-holds-only-multus`, `pod-at-boot-waits-for-multus` and `new-pod-after-reboot-gets-net1`, and the pod started with eth0 only. The latest run, `20261116T192350Z`, passed.
- The migration on a cluster built with the old layout: refused before `make addons`, then `changed=2` per node and `changed=0` on the rerun, and both scenarios passed afterwards in runs `20261116T060135Z` and `20261116T060225Z` (`.lab/evidence/m3-cni-dir/migration/`).
- Every check of both scenarios failed on a deliberate break and passed after the repair (`docs/adr/0003-results-and-breaks.md` section 3).
