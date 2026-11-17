# Troubleshooting

This document records failures actually observed in the lab and in the cel1 simulation, with their causes. Sections 1 to 14 cover Track A, section 15 the host and the QEMU harness, section 16 Track B, section 17 Track C, section 18 the cel1 simulation and section 19 results, the report, cleanup and CI. Each item names its evidence: a run id under `results/`, a fixture under `tests/fixtures/`, or a log under `.lab/`, whose evidence copies sit in `.lab/evidence/`. Those copies and `results/` exist only on the machine that ran them. Items without a confirmed cause are left out. The decisions these fixes led to are in `docs/adr/`.

## 1. `make doctor` fails

doctor compares each range of the address plan with the host interface addresses, all routing tables, docker networks and libvirt networks. If even one address overlaps, it prints `[fail] plan <name> <range> overlaps <host range>` and fails.

- Cause: one of the management network, the RDMA network, the Pod or Service CIDR, or the Multus pool overlaps a range the host already uses. A docker bridge that is down still holds its subnet, so it counts as a conflict.
- Fix: change `lab_address_plan` in `ansible/inventories/lab/group_vars/all/lab.yml` and the node addresses in `hosts.yml` together. To try another plan first, use `make doctor LAB_PLAN=<file>`. The file needs only the `lab_address_plan` key.
- `[fail] ... lies outside plan rdma` is a contradiction inside the plan. A node address lies outside its network, or the Multus pool lies outside the RDMA network.
- Low disk, memory or CPU is a `[warn]`. The lab resource budget is a starting allocation, so it does not fail doctor.

## 2. `make bootstrap` stops at the kernel or a package

| Message | Cause | Fix |
|---|---|---|
| `runs kernel X, but versions.lock.yaml pins lab.guest_kernel Y` | The guest booted a kernel other than the locked one | Boot the locked kernel, or probe the new kernel and pin the lock again |
| `rdma_rxe is not part of kernel X and apt has no installable linux-modules-extra-X` | The archive has no extra modules package for the running kernel. This happens with the kernel of an old image | Boot a kernel that has an extra modules package and pin `lab.guest_kernel` again |
| `Version '50.0-2ubuntu0.2' for 'rdma-core' was not found` | noble-updates moved to a new version, and the locked version dropped out of the index | Probe the new version, then pin `lab.rdma_core` again |
| Waiting for the apt lock | apt-daily is running right after the first boot | Wait up to `common_apt_lock_timeout` (600 seconds) |

Kernel 6.8.0-147-generic of the 20261017 image has no `rdma_rxe`. `linux-modules-extra-6.8.0-147-generic` provides it (`.lab/evidence/m1/probe.log`). The `common` role installs this package only when `modinfo rdma_rxe` fails and `apt-cache policy` shows a candidate version.

The `common` role holds the installed kernel metapackage (`linux-virtual`, `linux-generic` and others). If unattended upgrades installed a new kernel, the next boot would have no `rdma_rxe` package for that kernel, and RXE would disappear. To upgrade the kernel, release the hold and pin the lock again.

## 3. `[FAIL]` in `make preflight`

preflight changes nothing. Each item prints its cause by name.

| Item | Common cause |
|---|---|
| `nic-variables` | The override inventory lacks `lab_mgmt_nic` or `lab_rdma_nic` |
| `default-route-on-uplink` | The default route is on a NIC other than `lab_uplink_nic` (the management NIC when that is unset) |
| `mgmt-address` | The management NIC does not hold `k8s_node_ip`. Kubernetes registers the node with this address |
| `rdma-nic-present` | The NIC name is wrong, or the VM has no second NIC |
| `rdma-address`, `rdma-mtu` | `make bootstrap` has not run yet. Before bootstrap, preflight fails on these items and on `rdma-rxe-available` and `packages-locked` |
| `peers-routed-via-rdma-nic` | The RDMA NIC has no address, so traffic to the peer address leaves through the default route (the management NIC) |
| `peers-reachable` | The two RDMA NICs are not on the same lab network |

## 4. `rfl-rxe.service` does not create the device

Read `journalctl -b -u rfl-rxe.service` with sudo. If the lab user is not in the `adm` or `systemd-journal` group, the system journal looks empty without sudo. A reboot run during development failed for this reason. The runner now reads the journal with sudo.

| Journal message | Meaning |
|---|---|
| `added rxe0 on <nic>` | The device was created in this boot |
| `rxe0 already on <nic>` | It already exists. Nothing changed |
| `<nic> is not up with <address> after 90 s` | The NIC did not come up within the time limit, or it has no address |
| `rxe0 exists on <other NIC>` | A device with the same name sits on another NIC. Neither the script nor the `rxe` role deletes this device |
| `<nic> already carries RDMA device <name>` | Another RDMA device sits on the same NIC. No second device is created |

Never delete a device by its name alone. Before deleting one by hand, check its netdev with `rdma link show`. `make reset-lab CONFIRM=lab` deletes only an `rxe0` bound to the lab NIC.

## 5. When the lab NIC link goes down

This behavior was observed in the link fault runs `20261115T022449Z` (link down) and `20261115T022539Z` (`netem loss 100%`), and in development run `20261115T020504Z` (client link down).

- When the link goes down, systemd-networkd removes the static address of the RDMA NIC, because the default is `IgnoreCarrierLoss=no`.
- Once the address is gone, the kernel sends the peer RDMA address over the default route, which is the management NIC. The `route-via-lab-nic` check catches this as `worker1 routes 10.77.0.13 via enp0s3`. RDMA CM refuses the connection because the management NIC has no RDMA device. Plain IP traffic can leave over the management network. Keeping the address with netplan `ignore-carrier: true`, or an unreachable route for the lab network, would block that path. Neither is applied.
- When the server-side link goes down, `rping -s` ends at once with `rdma_bind_addr: Cannot assign requested address`, and the perftest server ends at once with `Port number 1 state is Down`. The failure class is `command-failed`.
- When the client-side link goes down, `rping -c` ends at once with `RDMA_CM_EVENT_ADDR_ERROR, error -19` (ENODEV). The runner stops the waiting server.
- When the link stays up and only the frames vanish (`tc qdisc ... netem loss 100%`), `rping` ends after 2 seconds with `error -110` (ETIMEDOUT), and the class is `timeout`. perftest prints only `Received 10 times ADDR_ERROR` and gives no errno, so it stays `command-failed`.

When the link comes back up, networkd restores the address and the RXE port returns to ACTIVE. The same `make rxe-test` passes again.

## 6. Processes the runner left behind

Every guest process the runner starts runs under `timeout` and carries the environment variable `RFL_RUN=<run-id>`. Output, pids and exit codes sit in `/var/tmp/rfl/<run-id>/` on the guest. When the runner exits normally or receives SIGINT or SIGTERM, it stops its own processes and deletes this directory.

If the runner dies from SIGKILL or similar, run `make cleanup RUN=<run-id>`. It stops only the processes with that run id, deletes only that directory, and deletes the namespaces labeled `rdma-fabric-lab/run=<run-id>` with the pods and the NetworkAttachmentDefinition they hold. It does not touch the RXE device, `rfl-rxe.service`, the network configuration, other runs or the guests. `docs/runbook.md` section 8 has what it removes in the other tracks.

## 7. QEMU harness

| Symptom | Check |
|---|---|
| `no SSH on 127.0.0.1:<port>` | `.lab/guests/<guest>/serial.log` |
| `cloud-init did not finish cleanly` | `sudo cloud-init schema --system` in the guest |
| `does not hash to lab.guest_image` | The downloaded image does not match the sha256 in the lock. The harness does not boot |
| `lab.sh down` does not stop a process | It stops a process only when the pid in the pidfile is alive and its command line holds the same pidfile path |

The harness lab network is two AF_UNIX datagram sockets under `.lab/net/`. Lab network frames never leave the host. Each guest's management NIC is separate QEMU user networking, so the two guests cannot reach each other over the management network. On a VMware NAT VMnet the management network is shared, so the `no-roce-on-mgmt-nic` check means more there.

## 8. `make k8s` stops

| Message | Cause | Fix |
|---|---|---|
| `Conditional result (True) was derived from value of type 'str'` | Since ansible-core 2.19, a conditional must be a boolean. The `ansible.utils.ipaddr` filter returns an address string | Use a test such as `is ansible.utils.in_network(...)` instead of the filter. The network role failed for this reason under ansible-core 2.21.6 (`.lab/evidence/m2/m1-regression-bootstrap-failure.log`) |
| `has containerd.io installed` | A package-installed containerd is present. The two daemons fight over `/run/containerd/containerd.sock` | The role stops and changes nothing. The operator decides whether to remove the package or to take that host out of `k8s_cluster` |
| `is not the key DE15B144...` | The pkgs.k8s.io signing key changed. The current key expires on 2026-12-29 | Verify the fingerprint of the new key and update `kubeadm_apt_key_fingerprint` |
| `it does not upgrade (ADR 0001 section 6)` | The node has a kubelet, kubeadm, kubectl or cri-tools version other than the locked one | Upgrades are outside the scope of this role. Match the versions or rebuild the node |
| `was initialized with other settings` | The kubeadm-config of the cluster differs from the endpoint, CIDRs, version or kube-proxy choice in the inventory | The role does not reconfigure. Match the inventory to the cluster or rebuild the cluster |
| `Configuration migrated from version 3` in the containerd log | containerd 2.4 reads configuration version 4 | The `containerd` role writes version 4. Replace a hand-written version 3 file with the role's |
| `could not bootstrap the admin user in file admin.conf: ... context deadline exceeded` | `kubeadm init phase upload-certs` ran without `--config`. With defaults, kubeadm looks up the version online and cannot reach the API | The role passes the rendered `/etc/kubernetes/rfl/kubeadm-init.yaml`. With it, the same command finished in 0.06 seconds |

The kubelet package depends on `kubernetes-cni`. That package unpacks plugins of a different release into `/opt/cni/bin`. The `containerd` role first writes `path-exclude=/opt/cni/bin/*` to `/etc/dpkg/dpkg.cfg.d/rfl-cni-plugins`, so dpkg leaves that path alone. If `/opt/cni/bin/loopback` does not report the locked version, first check that this file exists.

## 9. `k8s-baseline` fails

| Failed check | Observed cause |
|---|---|
| `cilium-agents-ready` | A Cilium agent pod on a node is restarting. Wait with `kubectl -n kube-system rollout status daemonset/cilium`, then run again |
| `cilium-agents-ready` right after a worker reboot, with the agent Running and not Ready | The chart probes the agent's readiness every 30 s, so a healthy agent waits for the next tick. In run `20261116T063900Z` worker2's agent was not Ready 15 s after it started, 3 s after `vm-rxe-reboot` ended (`.lab/evidence/m3-cni-dir/agent-readiness/summary.txt`). The cause is inferred from the probe period and the agent logs. Wait for the `cilium` rollout and every Node Ready after a reboot, as `acceptance-track-a.sh` does. `make track-a` runs `vm-rxe-reboot` last for this reason |
| `cilium-agents-ready`, with the agent in `Init:CrashLoopBackOff` and `dial tcp 10.96.0.1:443: i/o timeout` in the `config` log | The agent reaches the API through the ClusterIP. The default route is on a NIC other than the one with the node IP, so the connection leaves with the wrong source address. Because of the Cilium mark, kube-proxy does not rewrite the address. Check that `kube-system/rfl-api-endpoint` exists, and check `k8sServiceHostRef` in the values file. `make k8s` writes the ConfigMap |
| `pod-to-pod-cross-node` and `clusterip-service` time out, and `vxlan-on-mgmt-nic` sees only one direction | One node drops UDP 8472. A DROP rule in the worker1 INPUT chain produced failures of this shape |
| `network-policy-blocks` and `network-policy-allows` are `not_run` | The earlier `clusterip-service` did not pass. A "blocked" result on traffic that is already cut proves nothing about the policy, so these checks are not attempted |

The runner runs kubectl on cp1. To view the cluster from the control node, use `.lab/kubeconfig`. In the QEMU harness, port 6443 of cp1 is forwarded to `127.0.0.1:22643`, and the kubeconfig verifies the API certificate with `tls-server-name: kubernetes`.

## 10. The API VIP does not move

In `k8s-ha-vip`, when the power of the control plane that holds the VIP is cut, the lease expires after 5 seconds and another control plane takes the VIP. The measured API gaps were 6.0 to 9.0 seconds, on the ha profile and on cel1 alike (`docs/adr/0001-kubernetes-stack.md` section 10). If the VIP does not come back, check the following.

- `/etc/kubernetes/manifests/kube-vip.yaml` on the surviving control planes. With this file removed from two nodes, the VIP was nowhere for 120 seconds. `make k8s` writes the file again.
- The holderIdentity in `kubectl -n kube-system get lease plndr-cp-lock`, and the VIP in `ip -4 addr` on each node.
- kube-vip on the first control plane uses `super-admin.conf` until init finishes, because since Kubernetes 1.29 the group permissions of `admin.conf` appear only partway through init.

## 11. `k8s-hostnet-rdma` fails

| Failed check | Observed cause |
|---|---|
| `rdma-pods-ready` times out, the pod waits in `ErrImageNeverPull` | The worker's containerd has no `docker.io/library/rfl-rdma-tools@<lab.rdma_tools_image>`. The pods never pull. Run `make rdma-tools-load` |
| A pod that names the image by its manifest digest fails with `no such image` | `ctr import --digests` names the image by the sha256 of the archive's `index.json`, and CRI resolves `name@digest` only by that name (`.lab/evidence/m3/image-import-probe.txt`). `lab.rdma_tools_image` pins the index digest. Reference that one |
| `pod-tools-match-lock` | The image, the workers and the lock disagree on rdma-core or perftest. Rebuild with `make rdma-tools-image`, set the new digest and load it |
| `ibv-devinfo-in-pods`, `rxe-bound-to-lab-nic`, `rping-verified`, `rdma-*`, `roce-on-lab-nic` together | The RXE device of one worker is gone. `make rxe-up` restarts `rfl-rxe.service`, which creates it again |
| `route-via-lab-nic` | The lab address of one worker is gone, so the peer address goes out the default route. `networkctl reconfigure <lab NIC>` restores it |
| `memlock-in-pods` | The two ends run under different memlock limits. Pods inherit the limits of containerd's service, 8 MiB by default. A `LimitMEMLOCK` drop-in for containerd on one worker produced this |
| `no-roce-on-mgmt-nic` with no RoCEv2 but "may not have been live" | The marker pings over the management NIC did not leave, here because an OUTPUT rule dropped ICMP echo |

An unprivileged container sees no `/dev/infiniband`, and `ibv_devinfo` prints `No IB devices found`. Mounting `/dev/infiniband` as a hostPath shows the files, but the device cgroup refuses the open with `Operation not permitted` although the files are mode 0666. Capabilities do not change the device cgroup. Only `privileged` opens it without a device plugin.

## 12. `k8s-multus-net1` fails

| Failed check | Observed cause |
|---|---|
| `cni-dir-holds-only-multus`, then `pods-have-eth0-and-net1` | Another CNI file sorts before `00-multus.conf`, so containerd runs it and the pod gets no `net1`. Remove it |
| `cni-dir-holds-only-multus` with `05-cilium.conflist` in `/etc/cni/net.d` | The cluster was built while Cilium wrote its conflist there, and Cilium never removes it. Run `make addons ENV=lab KUBECONFIG_FILE=.lab/kubeconfig`, then `make cni-migrate` (section 14) |
| `cni-dir-holds-only-multus` with `clusterNetwork is '/host/etc/cni/net.d/05-cilium.conflist'` | The Multus daemon on that node still runs with the old daemon config. It reads the config only at start, and a ConfigMap change does not roll the DaemonSet. `make cni-migrate` restarts it |
| `pod-at-boot-waits-for-multus` with `containerd reported a network ... while 00-multus.conf was absent` | A CNI file other than Multus's sits in `/etc/cni/net.d`. After a boot, before Multus writes its file, containerd runs that file alone, and a pod started then gets eth0 and no `net1`, without an error. Remove the file |
| `pod-at-boot-waits-for-multus` with `missed the window` | Multus wrote its file before the run could look at the node after creating the pod. The check proves nothing then. Run it again. Multus writes the file about 18 s after the boot, and SSH answers about 6 s after it |
| `pod-at-boot-waits-for-multus` with `00-multus.conf survived the reboot` | The node's shutdown did not stop its pods, so Multus never removed its file. A `systemctl daemon-reload` since the kubelet started drops `Conflicts=shutdown.target` from the kubelet's slices on systemd 255. Until 2026-11-16 the `rxe` and `containerd` roles reloaded on every run, so every `make rxe-up` and `make k8s` did it. The console shows `systemd-shutdown[1]: Waiting for process: … (multus-daemon)`. Seen in run `20261116T165752Z` (`.lab/evidence/m7/multus-window/`) | `systemctl show kubepods.slice -p Conflicts` must print `shutdown.target`. A reboot after the reload restores it. The roles reload only from handlers now (`docs/adr/0001-kubernetes-stack.md` section 8) |
| `cni-dir-holds-only-multus` with `... is not JSON`, and every new pod on that node stays in `ContainerCreating` | Multus chose its default delegate when its daemon started and keeps it. If that file is gone, `00-multus.conf` names a missing file. The Multus log shows `failed to get clusterNetwork` on every ADD and no regeneration. Restart the Multus pod on that node: `kubectl -n kube-system delete pod -l name=multus --field-selector spec.nodeName=<node>` |
| `network-attachment-from-inventory` with `command-failed` | The NetworkAttachmentDefinition CRD is missing. `make addons ENV=lab KUBECONFIG_FILE=.lab/kubeconfig RELEASE=multus FORCE=1` creates it again |
| `no-duplicate-addresses` and `recreate-releases-addresses` | Whereabouts holds an address for a pod that does not exist. The detail names the address and the pod. Remove the allocation from the IPPool in `kube-system`, or let the Whereabouts reconciler remove it |
| `net1-on-lab-nic` with an empty capture on one worker | tcpdump without `--immediate-mode` hands packets to the file in batches, and a stop right after the traffic loses the last batch. The runner uses immediate mode |
| `default-route-on-eth0` passes although the network selection asks for `default-route` through the lab network | Multus `default-route` selection did not move the pod's default route off eth0, with or without a gateway in the IPAM configuration. Only IPAM routes over `net1` moved it, which is how the break of this check works | This is Multus behavior here, not a fault of the run |
| `run.multus` stops before the run with `lab_rdma_nic differs across the workers` | One attachment names one macvlan master for every node. Give the lab NIC the same name on every worker. The QEMU harness renames it `lab0` at first boot. A VMware lab renames the NICs, then sets `lab_rdma_nic` in its own override inventory |

Each worker reboot of the cluster guests takes about 97 s. The Whereabouts daemon container, `sh` with `ip-control-loop`, does not stop on SIGTERM, and systemd kills it after its 90 s stop timeout (`cri-containerd-<id>.scope: Stopping timed out. Killing.` in `journalctl -b -1` of worker2). The node's lease expires meanwhile, so after the boot the Node carries the unreachable taints first. The same timeout makes `make lab-down` of the lab profile take about 274 s.

## 13. The RDMA test image

`make rdma-tools-image` builds in the Track B guest, because no new container runs on this host. `BUILDER_LAB_DIR` moves that guest's files, by default next to `LAB_DIR` when that is set. The build has no cache and clamps every time to the archive snapshot, so a changed digest means changed inputs: the Dockerfile, the snapshot or a locked version. Set `lab.rdma_tools_image` to the printed digest only after you know which input changed.

## 14. `make cni-migrate` stops

`make cni-migrate` runs `ansible/playbooks/cni-migrate.yml` one node at a time. It changes nothing until both checks below pass, and a rerun reports `changed=0`.

| Message | Cause | Fix |
|---|---|---|
| `Multus takes its default delegate from /host/etc/cni/net.d, not /etc/cni/multus/net.d` | The cluster runs the Multus daemon config from before the move | Run `make addons ENV=lab KUBECONFIG_FILE=.lab/kubeconfig` first |
| `<node> has no /etc/cni/multus/net.d/05-cilium.conflist` | The Cilium agent on that node has not written its file to its own directory | Run `make addons` first and let the Cilium rollout finish |
| `/etc/cni/net.d on <node> holds [...]` | A CNI file other than Cilium's and Multus's sits there. The role removes only Cilium's file | Remove the other file by hand |

## 15. The host and the QEMU harness, every track

| Symptom | Cause | Fix | Evidence |
|---|---|---|---|
| The host freezes while ibsim, OpenSM or a diagnostic tool runs in a container on it | Ubuntu kernels 7.0.0-42 and 7.0.0-43 hit a NULL dereference in AppArmor when a confined process writes to a connected AF_UNIX datagram socket. ibsim and libumad2sim use that socket, and Docker confines every container. It froze the host three times on 2026-11-15 | Run Track B and Track C only through `make track-b` and `make track-c`, which run them in a guest on the guest's own kernel. Never start them on the host, in a container or not | `docs/adr/0002-execution-environments.md` |
| `guest.sh: refusing to run: /etc/rfl/track-b-guest is missing, so this machine is not the Track B guest` (or `track-c-guest`), exit 2 | A `guest.sh` command ran on the host. Both scripts refuse before they touch Docker | Use the make targets. They copy `guest.sh` into the guest and call it there | `.lab/evidence/m4/host-refusal.log`, `.lab/evidence/m5/host-refusal.log`, `tests/unit/test_ibsim_host_refusal.py` |
| RXE figures of a run differ from the earlier runs with no change to the lab | Another profile's guests competed for the host. The acceptance scripts fail the step before each RXE run with `other guests run:` when any QEMU process other than cp1, worker1 and worker2 runs | Stop the other profile with `make lab-down LAB_PROFILE=<profile>` before you measure | `only_lab_guests` in `tests/integration/qemu-lab/acceptance-track-a.sh` |
| `/cache` or `/` runs out of space during a run | Guest disks live in `.lab/` by default, and the cel1 simulation alone wrote 41 GB | Give each profile a `LAB_DIR` on a file system with room. `acceptance-all.sh` takes `LAB_ROOT` and puts each profile in `LAB_ROOT/<profile>` | `.lab/evidence/cel1-sim/wipe.log`: the cel1 directory held 41,056,558,518 bytes |
| `make track-b LAB_DIR=<dir>` boots the guest in `<dir>` but runs against `.lab/ibsim/inventory.yml`. `make rdma-tools-image LAB_DIR=<dir>` boots the builder into the lab's own `<dir>` | Make 4.3 exports no command line variable to `$(shell)`, and a sub-make takes the parent's command line `LAB_DIR` over its environment. Seen with `make -n` before the fix of 2026-11-16 | Fixed: the Track B and Track C inventory calls pass `LAB_DIR`, and the builder sub-make gets `LAB_DIR=$(BUILDER_LAB_DIR)` on its command line. The environment form `LAB_DIR=<dir> make` always worked | `tests/unit/test_lab_make.py` |

## 16. Track B

| Symptom | Cause | Fix | Evidence |
|---|---|---|---|
| `subnet-up` fails, and `opensm.log` says `disabling UPDN algorithm, no root nodes were found` | updn needs root switches. On the single-tier fabrics of B-01 to B-03 it finds none and falls back to minhop. `subnet-up` fails a run whose log names an engine other than the configured one | B-01 to B-03 use minhop. B-04, the two-tier cel1 fabric, uses updn | Runs `20261115T141931Z` and `20261115T141940Z`, `tests/fixtures/b-ibsim/b-02-updn-fallback/container/opensm.log` |
| ftree routes the healthy cel1 fabric but logs `ERR AB0B` on the degraded one and falls back to minhop | ftree rejects a fat tree with a missing link | B-04 uses updn, which routed through the fault and the recovery | `.lab/evidence/m4/probe/12-engines-under-fault.txt` |
| OpenSM rejects the `no_fallback` engine option | OpenSM 3.3.23 has no such option, so a failed engine always falls back and OpenSM keeps running | Rely on the `subnet-up` check, which reads the engine from the log | `.lab/evidence/m4/probe/10-routing-engines-cel1.txt` |
| `make track-b-image` exits 1 with an image ID other than `ibsim.image` | An input changed: the Dockerfile, the snapshot or a locked version. Until 2026-11-16 the build was not reproducible, and a wiped guest gave `sha256:0b462da8…` | Find the changed input before you pin the new ID. Two builds from wiped guests give the same ID since the fix | `docs/adr/0002-execution-environments.md` section 5, `.lab/evidence/m7/ibsim-digest/` |
| `guest.sh: container rfl-ibsim-<key> already exists. Run guest.sh stop <key> first.` | A run of that key was killed before it stopped its container | `LAB_PROFILE=ibsim make cleanup RUN=<run-id>` removes every container and run directory of that run id inside the guest | `tests/integration/qemu-lab/cleanup-check.sh b` |
| `make track-b-breaks` exits 1 and names a scenario `(exit 0)` | A break run passed, so that scenario's checks no longer see the missing link | Read the break run's failed and passed checks against the scenario's `break` link in `tracks/b-ibsim/config/scenarios.yaml`. Each break must fail discovery and port states. `routes-shortest` still passes, because every remaining route leaves one hop closer | `.lab/evidence/m4/track-b-breaks.log`, `docs/adr/0005-track-b-fabric-simulation.md` section 3 |

## 17. Track C

| Symptom | Cause | Fix | Evidence |
|---|---|---|---|
| The image build fails to link `rfl-star` with an undefined `g_e_inCriticalSection` | libpoint-to-point uses libmtp's lock without linking libmtp, and Ubuntu's gcc links with `--as-needed` | `rfl-star` calls `MtpInterface::isEnabled()`, which keeps libmtp in the link | `.lab/evidence/m5/image-build-0-link-error.log` |
| BuildKit refuses `rewrite-timestamp` | BuildKit 0.30.1 refuses it with the unpack that `type=docker` implies | `guest.sh build` exports with `type=image,rewrite-timestamp=true,unpack=false` | `.lab/evidence/m5/probe/03-rewrite-timestamp.txt` |
| The simulator dies with SIGSEGV, exit 139, before it writes any counter, in a run with PFC on | `QbbNetDevice::Resume` looked up the QP by the resumed priority index, so a host with fewer QPs than that index crashed on the first RESUME | `images/ns3/qbb-resume-qp-lookup.patch` tests the node type first. If `git apply --check` fails in the build, the patch no longer matches the pinned source | Run `20261116T135921Z`, `.lab/evidence/m5/probe/05-pfc-resume-qp-index.txt`, `.lab/evidence/m6/patch-proof/patch-proof.txt`, `docs/adr/0006-track-c-packet-model.md` section 1.3 |
| `config-applied` fails on packet sizes, 52 B per data packet and 60 B per ACK | This fork's `PppHeader` writes 14 bytes, not 2 | The constants follow the code, and the expected FCT matches to the nanosecond | Run `20261116T135412Z`, `ppp-header.cc:85-90` at ns-3-alibabacloud `3e0c7c1` (`.lab/evidence/m5/code-references.txt`) |
| With PFC off the switch still sends PAUSE, and a small buffer drops packets until the flow stalls | In this model PFC off means the hosts ignore PAUSE. The switch side is unchanged. Without a retransmission timer a refused tail stalls forever | This is model behavior, not a fault of the run. A switch without PFC needs a model change, which is an open experiment | `.lab/evidence/m5/probe/06-mmu-drop-probe.txt`, `docs/adr/0006-track-c-packet-model.md` section 1.6 |
| The receiver's ECN counts wrap | `RdmaRxQueuePair::ECNAccount` keeps them as `uint16_t` | `rfl-star` counts marks from trace sources instead | `rdma-queue-pair.h:117` at ns-3-alibabacloud `3e0c7c1` |
| `make track-c-image` exits 1 with an image ID other than `ns3.image` | An input changed, or the build reused a cache from other inputs. Until 2026-11-16 the file modes of the checkout reached the image: a checkout made with umask 002 gave `rfl-ns3` mode 0775 and `sha256:2f14f43e…` | `make track-c-image CLEAN=1` removes the tag, the base image and every BuildKit cache entry and builds from nothing. It gave the pinned ID in three separate guests. Since the fix the guest copy sends the modes Git keeps | `.lab/evidence/m7/ns3-digest/layer-diff.txt`, `docs/adr/0002-execution-environments.md` section 5 |
| `guest.sh: container rfl-ns3-<key>-<slot> already exists` | A run of that key was killed before it stopped its containers | `LAB_PROFILE=ns3 make cleanup RUN=<run-id>` | `tests/integration/qemu-lab/cleanup-check.sh c` |
| `host-untouched` fails on a `SimAI_simulator` process on the host | A `host-process` break run was killed before it killed its decoy, a copy of sleep that lives 600 s | The decoy carries `RFL_RUN=<run-id>`, so `make cleanup RUN=<run-id>` stops it | `tests/integration/qemu-lab/cleanup-check.sh c` |

## 18. The cel1 simulation

| Symptom | Cause | Fix | Evidence |
|---|---|---|---|
| A fleet target exits 2 with `Nothing was changed. Rerun with CONFIRM=cel1.`, or the simulation gate refuses an address | The targets that change cel1 hosts need `CONFIRM=cel1` and a connection inventory whose every host, kubeconfig server and API address is on 127.0.0.1 | Pass `CONFIRM=cel1 FLEET_CONNECT=<LAB_DIR>/inventory.yml` from `lab.sh up` of the cel1 profile. strace saw no IPv4 or IPv6 call in any refusal | `.lab/evidence/cel1-sim/acceptance/strace-summary.txt` |
| `make fleet-rsyslog` fails on all 13 hosts at `apt-mark manual rsyslog` | The play installed a package as the SSH user | The play has `become: true` | `.lab/evidence/cel1-sim/fleet-rsyslog-1-no-become.log` |
| The DGX Nodes lose `nvidia.com/gpu.deploy.driver=false` and `nvidia.com/gpu.deploy.container-toolkit=false` after `make fleet-addons` | The GPU Operator removes every GPU state label from a node without `nvidia.com/gpu.present`, and the guests have no GPU | The next `make fleet-k8s` puts them back and changes nothing else. The checks read the opt-outs from the kubelet registration | `.lab/evidence/cel1-sim/label-watch.log`, run `20261115T165516Z` |
| `native-routing` fails with tunnel links on a node without VXLAN | `ip -d -j link show type vxlan` prints one `{}` per link of another type | `tunnel_links` reads `ifname` | Run `20261115T161414Z`, `tests/fixtures/cel1/ip-link-type-vxlan-dgx-02.json` |
| `syslog-to-central-address` fails while every other check passes | rsyslog is stopped on a node. acceptance-cel1.sh does this on purpose on `cel1-dgx-06`, and the run's manifest names the break | `make fleet-rsyslog CONFIRM=cel1 FLEET_CONNECT=...` starts it again | Run `20261115T165039Z`, `.lab/evidence/cel1-sim/acceptance/steps.tsv` steps 39 to 42 |

## 19. Results, the report, cleanup and CI

| Symptom | Cause | Fix | Evidence |
|---|---|---|---|
| `make validate` reports `status is passed but the checks derive failed` | The manifest was edited by hand. Only the checks set the status | Run the scenario again | `tests/unit/test_rfl_manifest.py` |
| `make validate` reports `'deliberate_break' is a required property` | A manifest of `schema_version` 2 lacks the field. Every run started by `rfl run-init` writes it, as null for an ordinary run | Start runs only through `rfl run-init` or a runner | `tests/unit/test_rfl_manifest.py` |
| `make report` shows an older run than the one that just ran | The newer run is a deliberate break or it never finished, for example a run killed with SIGKILL. The report counts only finished runs that are not breaks, and prints per scenario how many of each it left out | Read the run's `manifest.json`: `finished_at` null means it was interrupted, and `deliberate_break` names a break | The killed runs of `tests/integration/qemu-lab/cleanup-check.sh`, such as `20261116T182901Z`, stay unfinished |
| `make report STRICT=1` exits 1 | A required scenario has no finished run that is not a break, or its latest such run did not pass | Run the scenario again, then read its failed checks | `tests/unit/test_rfl_manifest.py` |
| `make test` passes but skips the Multus chart tests | `.tools/bin/helm` does not exist yet. `make tools` installs it | Run `make tools` first, as the CI workflow does | `.lab/evidence/m7/ci-local-1/03-make-test.log` |
