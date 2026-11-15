# Track A troubleshooting

This document records failures actually observed in M0, M1 and M2 and their causes. The evidence for each item is the run logs and result directories. Items without a confirmed cause are left out.

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

Kernel 6.8.0-147-generic of the 20261017 image has no `rdma_rxe`. `linux-modules-extra-6.8.0-147-generic` provides it (`docs/milestones/evidence/m1/probe.log`). The `common` role installs this package only when `modinfo rdma_rxe` fails and `apt-cache policy` shows a candidate version.

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

Read `journalctl -b -u rfl-rxe.service` with sudo. If the lab user is not in the `adm` or `systemd-journal` group, the system journal looks empty without sudo. The first M1 reboot test failed for this reason. The runner now reads the journal with sudo.

| Journal message | Meaning |
|---|---|
| `added rxe0 on <nic>` | The device was created in this boot |
| `rxe0 already on <nic>` | It already exists. Nothing changed |
| `<nic> is not up with <address> after 90 s` | The NIC did not come up within the time limit, or it has no address |
| `rxe0 exists on <other NIC>` | A device with the same name sits on another NIC. Neither the script nor the `rxe` role deletes this device |
| `<nic> already carries RDMA device <name>` | Another RDMA device sits on the same NIC. No second device is created |

Never delete a device by its name alone. Before deleting one by hand, check its netdev with `rdma link show`. `make reset-lab CONFIRM=lab` deletes only an `rxe0` bound to the lab NIC.

## 5. When the lab NIC link goes down

This behavior was observed in the M1 fault tests.

- When the link goes down, systemd-networkd removes the static address of the RDMA NIC, because the default is `IgnoreCarrierLoss=no`.
- Once the address is gone, the kernel sends the peer RDMA address over the default route, which is the management NIC. The `route-via-lab-nic` check catches this as `worker1 routes 10.77.0.13 via enp0s3`. RDMA CM refuses the connection because the management NIC has no RDMA device. Plain IP traffic can leave over the management network.
- When the server-side link goes down, `rping -s` ends at once with `rdma_bind_addr: Cannot assign requested address`, and the perftest server ends at once with `Port number 1 state is Down`. The failure class is `command-failed`.
- When the client-side link goes down, `rping -c` ends at once with `RDMA_CM_EVENT_ADDR_ERROR, error -19` (ENODEV). The runner stops the waiting server.
- When the link stays up and only the frames vanish (`tc qdisc ... netem loss 100%`), `rping` ends after 2 seconds with `error -110` (ETIMEDOUT), and the class is `timeout`. perftest prints only `Received 10 times ADDR_ERROR` and gives no errno, so it stays `command-failed`.

When the link comes back up, networkd restores the address and the RXE port returns to ACTIVE. The same `make rxe-test` passes again.

## 6. Processes the runner left behind

Every guest process the runner starts runs under `timeout` and carries the environment variable `RFL_RUN=<run-id>`. Output, pids and exit codes sit in `/var/tmp/rfl/<run-id>/` on the guest. When the runner exits normally or receives SIGINT or SIGTERM, it stops its own processes and deletes this directory.

If the runner dies from SIGKILL or similar, run `make cleanup RUN=<run-id>`. This command stops only the processes with that run id and deletes only that directory. It does not touch the RXE device, `rfl-rxe.service`, the network configuration, other runs or the guests.

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
| `Conditional result (True) was derived from value of type 'str'` | Since ansible-core 2.19, a conditional must be a boolean. The `ansible.utils.ipaddr` filter returns an address string | Use a test such as `is ansible.utils.in_network(...)` instead of the filter. The network role failed for this reason in the M1 regression |
| `has containerd.io installed` | A package-installed containerd is present. The two daemons fight over `/run/containerd/containerd.sock` | The role stops and changes nothing. The operator decides whether to remove the package or to take that host out of `k8s_cluster` |
| `is not the key DE15B144...` | The pkgs.k8s.io signing key changed. The current key expires on 2026-12-29 | Verify the fingerprint of the new key and update `kubeadm_apt_key_fingerprint` |
| `it does not upgrade (ADR 0001 section 6)` | The node has a kubelet, kubeadm, kubectl or cri-tools version other than the locked one | Upgrades are outside the scope of this role. Match the versions or rebuild the node |
| `was initialized with other settings` | The kubeadm-config of the cluster differs from the endpoint, CIDRs, version or kube-proxy choice in the inventory | The role does not reconfigure. Match the inventory to the cluster or rebuild the cluster |
| `could not bootstrap the admin user in file admin.conf: ... context deadline exceeded` | `kubeadm init phase upload-certs` ran without `--config`. With defaults, kubeadm looks up the version online and cannot reach the API | The role passes the rendered `/etc/kubernetes/rfl/kubeadm-init.yaml`. With it, the same command finished in 0.06 seconds |

The kubelet package depends on `kubernetes-cni`. That package unpacks plugins of a different release into `/opt/cni/bin`. The `containerd` role first writes `path-exclude=/opt/cni/bin/*` to `/etc/dpkg/dpkg.cfg.d/rfl-cni-plugins`, so dpkg leaves that path alone. If `/opt/cni/bin/loopback` does not report the locked version, first check that this file exists.

## 9. `k8s-baseline` fails

| Failed check | Observed cause |
|---|---|
| `cilium-agents-ready` | A Cilium agent pod on a node is restarting. Wait with `kubectl -n kube-system rollout status daemonset/cilium`, then run again |
| `cilium-agents-ready`, with the agent in `Init:CrashLoopBackOff` and `dial tcp 10.96.0.1:443: i/o timeout` in the `config` log | The agent reaches the API through the ClusterIP. The default route is on a NIC other than the one with the node IP, so the connection leaves with the wrong source address. Because of the Cilium mark, kube-proxy does not rewrite the address. Check that `kube-system/rfl-api-endpoint` exists, and check `k8sServiceHostRef` in the values file. `make k8s` writes the ConfigMap |
| `pod-to-pod-cross-node` and `clusterip-service` time out, and `vxlan-on-mgmt-nic` sees only one direction | One node drops UDP 8472. A DROP rule in the worker1 INPUT chain produced failures of this shape |
| `network-policy-blocks` and `network-policy-allows` are `not_run` | The earlier `clusterip-service` did not pass. A "blocked" result on traffic that is already cut proves nothing about the policy, so these checks are not attempted |

The runner runs kubectl on cp1. To view the cluster from the control node, use `.lab/kubeconfig`. In the QEMU harness, port 6443 of cp1 is forwarded to `127.0.0.1:22643`, and the kubeconfig verifies the API certificate with `tls-server-name: kubernetes`.

## 10. The API VIP does not move

In `k8s-ha-vip`, when the power of the control plane that holds the VIP is cut, the lease expires after 5 seconds and another control plane takes the VIP. The observed API gap is about 9 seconds. If the VIP does not come back, check the following.

- `/etc/kubernetes/manifests/kube-vip.yaml` on the surviving control planes. With this file removed from two nodes, the VIP was nowhere for 120 seconds. `make k8s` writes the file again.
- The holderIdentity in `kubectl -n kube-system get lease plndr-cp-lock`, and the VIP in `ip -4 addr` on each node.
- kube-vip on the first control plane uses `super-admin.conf` until init finishes, because since Kubernetes 1.29 the group permissions of `admin.conf` appear only partway through init.
