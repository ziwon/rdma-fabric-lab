# ADR 0001: The Kubernetes stack the lab and the fleet share

> Status: accepted, 2026-11-15. Sections 7 to 10 record how the stack converges, accepted between 2026-11-15 and 2026-11-16.

## 1. Decision

- Ansible and kubeadm install Kubernetes.
- The lab (`ansible/inventories/lab`) and the GPU cluster of the virtual region cel1 (`ansible/inventories/cel1`) use the same playbook `ansible/playbooks/kubernetes.yml` and the same roles `containerd` and `kubeadm`. They differ only in inventory variables.
- cel1 is a data center that does not exist. It has 3 control planes, 8 DGX B200 nodes and 2 previous-generation PCIe GPU servers. All names and addresses are invented.
- Another site keeps an inventory that follows the same contract outside the repository and uses the same playbook.
- Helm installs the add-ons, Cilium among them. The release list is the single file `helm/releases.yaml`, and `make addons ENV=<env>` installs the releases in that order. The values files are `helm/<release>/values-<env>.yaml`. A release takes its chart from a chart repository, an `oci://` reference, or a repo-local `./` path when the project publishes no chart for the locked version, as for Multus. Its `images` entries pin images by tag and digest from image-digest lock components.
- Every version comes only from `versions.lock.yaml`. Roles and values files do not hard-code versions.

## 2. Reasons

- The stack uses the latest Kubernetes and the latest Cilium.
- The base implementation direction of the lab chose Ansible and kubeadm for Kubernetes and a version-pinned Helm chart for Cilium.
- A lab check verifies the fleet code only when the lab runs the same code as the fleet. cel1 has no GPU hardware, so this is the only path that confirms the code by running it.

## 3. Versions

| Component | Lock key | Value |
|---|---|---|
| Kubernetes | `k8s.kubernetes` | v1.36.6 |
| containerd, runc, CNI plugins | `k8s.containerd`, `k8s.runc`, `k8s.cni_plugins` | 2.4.2, 1.5.3, v1.10.0 |
| Cilium chart | `cni.cilium_chart` | 1.20.4 |
| GPU Operator chart | `addons.gpu_operator_chart` | v26.7.2 |
| kube-vip | `k8s.kube_vip` | v1.3.1 |
| helm, kubeconform | `tooling.helm`, `tooling.kubeconform` | v4.3.2, v0.8.0 |

The latest Kubernetes is v1.37.2. The Cilium 1.20 compatibility table lists only 1.33–1.36 as covered by e2e tests. The Cilium development branch tests 1.34–1.37. So the stack takes the latest v1.36 patch. When Cilium 1.21 ships, both move up together.

## 4. Inventory contract

The playbooks and roles read only the groups and variables below. Both inventories follow this contract.

| Name | Kind | Meaning |
|---|---|---|
| `kube_control_plane` | group | Control planes. The first host runs `kubeadm init` |
| `kube_node` | group | Workers |
| `k8s_cluster` | group | The union of the two groups above |
| `rfl_inventory` | string | `lab` or `cel1`. It is also the Helm environment name. The playbook rejects a host without this value |
| `k8s_node_ip` | per-host IPv4 | The address kubelet registers. It is the management network address |
| `k8s_api_endpoint` | address or name | The host part of the kubeadm `controlPlaneEndpoint` |
| `k8s_api_port` | integer | Default 6443 |
| `k8s_api_vip` | IPv4 or absent | When set, kube-vip advertises this address from the control planes. It is required with two or more control planes and must equal `k8s_api_endpoint` |
| `k8s_api_vip_interface` | interface name | The NIC that advertises the VIP |
| `k8s_pod_cidr`, `k8s_service_cidr` | CIDR | kubeadm and Cilium IPAM get the same value |
| `k8s_kube_proxy` | boolean | When false, kubeadm does not install kube-proxy and Cilium replaces it |
| `k8s_control_plane_schedulable` | per-host boolean | Default false. When true, that control plane gets no control-plane taint |
| `k8s_node_labels` | per-host map | Default empty map. These are the labels kubelet adds when it registers. They select the hardware kind and the GPU Operator components. They never use the `kubernetes.io` or `k8s.io` prefixes, which kubelet cannot add by itself |
| `k8s_container_runtime_default` | per-host `runc` or `nvidia` | Default `runc`. With `nvidia`, the containerd configuration gets an nvidia runtime that uses `/usr/bin/nvidia-container-runtime`, and that runtime becomes the default. It is for DGX OS nodes with the toolkit preinstalled |
| `k8s_admin_kubeconfig` | control node path | Where the kubeadm role places the admin kubeconfig with mode 0600. It uses the paths in section 5 |

The lab follows its initial Kubernetes design, which keeps kube-proxy. It starts with `k8s_kube_proxy: true`, 1 control plane (`cp1`) and no VIP.

## 5. Execution boundaries

- `make k8s` and `make addons ENV=lab` change only the lab guests.
- A target that changes cel1 stops without `CONFIRM=cel1`. The cel1 addresses are invented, so the host never connects to them directly. Simulation guests are reached only through 127.0.0.1 port forwards.
- No kubeconfig lives in a path git tracks. The lab uses `.lab/kubeconfig`, and cel1 uses `${XDG_STATE_HOME:-~/.local/state}/rdma-fabric-lab/cel1/kubeconfig`.

## 6. Out of scope

- Moving a cluster that already runs onto this code.
- Cluster upgrade procedures. This ADR covers only installation and convergence.
- Network Operator and IPoIB. A later hardware profile for physical NVIDIA InfiniBand hardware covers them.

## 7. The node runtime comes from release files

**Decision.** The `containerd` role installs containerd, runc and the CNI plugins from their upstream release files, not from distribution packages. `get_url` checks each file against its sha256 in the lock (`k8s.containerd_archive`, `k8s.runc_binary`, `k8s.cni_plugins_archive`) and rejects any other bytes. The role writes a containerd configuration of version 4 with the systemd cgroup driver and imports `/etc/containerd/conf.d/*.toml`. With `k8s_container_runtime_default: nvidia` it adds the nvidia runtime and makes it the default. Before the kubelet package arrives, it writes the dpkg rule `path-exclude=/opt/cni/bin/*`. On a host with a package containerd such as `containerd.io` it stops and changes nothing.

**Reasons.**

- Every version comes only from the lock (section 1), and a release file is the only source that carries exactly the locked containerd, runc and plugin releases.
- The kubelet package depends on `kubernetes-cni`. Its 1.9.1-1.1 from pkgs.k8s.io unpacks plugins of another release into `/opt/cni/bin`. The dpkg rule keeps the locked v1.10.0 there.
- containerd 2.4 reads configuration version 4. A version 3 file leaves the warning `Configuration migrated from version 3`.
- A package containerd and this one would fight over `/run/containerd/containerd.sock`. Which one stays is the operator's choice.

**Evidence.** Step 10 of `tests/integration/qemu-lab/acceptance-k8s.sh` in `.lab/acceptance-m2/20261115T060116Z`: on all three nodes the sha256 of the cached archives and of runc equals the lock, and `tar -d` finds no difference between the archives and the unpacked files (`.lab/evidence/m2/acceptance-m2-installed-bytes.log`). After installation `/opt/cni/bin/loopback` reports v1.10.0. With the nvidia default, `containerd config dump` shows `default_runtime_name = 'nvidia'` (`.lab/evidence/m2/nvidia-probe/dump-nvidia.txt`), and `dgx-containerd-defaults-to-nvidia` passed in cel1 run `20261115T164615Z`. The role check for a package containerd has not run on a host that has one.

## 8. A second run changes nothing

**Decision.**

- The `kubeadm` role runs `kubeadm init` and `kubeadm join` only on a node without `/etc/kubernetes/admin.conf` and `kubelet.conf`. On a rerun it stops when the stored kubeadm-config differs from the inventory. It neither reconfigures nor upgrades (section 6). A join uses a fresh 15-minute token and the certificate key from `upload-certs`, and the join configuration that holds them is deleted right after the join. `kubeadm init phase upload-certs` gets the rendered init configuration with `--config`.
- No role task and no playbook runs `systemctl daemon-reload`. A handler does, and only when a unit file changed.
- `make addons` skips a release whose deployed status, chart version and user values equal the lock and the values file. For a repo-local `./` chart it also requires the objects of `helm get manifest` to equal what `helm template --no-hooks` renders now with the same values. `make addons FORCE=1` upgrades anyway.

**Reasons.**

- A drifted cluster is a question for the operator. A role that silently reconfigures or upgrades would hide it.
- `upload-certs` without `--config` looked up the Kubernetes version online and failed after 60 s while it created the admin ClusterRoleBinding (`.lab/m2/ha-k8s-1.log`). With the configuration it finished in 0.06 s. `kubeadm token create` cannot mix `--config` with other flags, so it still looks the version up and takes about 12 s.
- On systemd 255.4-1ubuntu8.18 one `daemon-reload` empties `Conflicts=shutdown.target` and `Before=shutdown.target` on the kubelet's slices. The next shutdown then stops no pod, and Multus never removes its file. Before the fix the `rxe` and `containerd` roles reloaded on every run, changed or not, so every `make rxe-up` and `make k8s` did it.
- `helm upgrade --install` writes a new revision even when nothing changed. A skip leaves drifted objects alone, which is why `FORCE=1` exists. A repo-local chart can change without a new chart version, so for it the version does not prove the content. The user values are compared as Helm v4.3.2 merges them: maps merge key by key, a later list or scalar replaces the earlier one, and a null in a later file stays in the stored values and removes the chart default at render time (`.lab/evidence/m2/helm-null-probe.txt`).

**Evidence.** The second `make k8s` reported `3 hosts, 0 with changes` on the lab profile and `4 hosts, 0 with changes` on the ha profile, and the second `make fleet-k8s` `13 hosts, 0 with changes` (`.lab/acceptance-m3/20261116T071100Z` steps 37 to 43, `.lab/acceptance-ha/20261115T060117Z/08-k8s-again-unchanged.log`, `.lab/acceptance-cel1/20261115T163759Z` steps 20 and 21). Every release stayed at revision 1 with history `[1]` in the same runs. A template-only change of the Multus chart upgraded it to revision 2, and the next run reported it unchanged (`.lab/evidence/m3-cni-dir/addons-local-chart/`). The reload defect failed `pod-at-boot-waits-for-multus` in run `20261116T165752Z`, and the fixed roles kept the slice dependencies through `make rxe-up` and `make k8s` (`.lab/evidence/m7/multus-window/step3-fixed-roles.txt`, `step4-reboot-after-fixed-roles.txt`). `tests/unit/test_ansible_daemon_reload.py` fails on a reload outside a handler.

## 9. Cilium reaches the API without the ClusterIP

**Decision.** The Cilium agent connects to the API address itself. In the lab the `kubeadm` role writes `k8s_api_endpoint` and `k8s_api_port` to the ConfigMap `kube-system/rfl-api-endpoint`, and `k8sServiceHostRef` in `helm/cilium/values-lab.yaml` reads it. In cel1, `values-cel1.yaml` sets `k8sServiceHost` to the VIP, with kube-proxy replacement.

**Reasons.** A restarted agent could not reach the API through `10.96.0.1:443`. Three conditions met. The harness default route sits on the QEMU user NIC, so a connection to a ClusterIP took `10.0.2.15` as its source. The agent's socket carried `fwmark:0x10b00`, and Cilium's `CILIUM_POST_nat` accepts the `0xa00/0xe00` mark before kube-proxy can masquerade it. So the SYN left through the management NIC with the wrong source, and the reply was lost. At first installation `CILIUM_POST_nat` did not exist yet, which is why the first install worked. `k8sServiceHost: auto` was rejected because it uses Helm `lookup`, which breaks the offline `make addons-template`. An address written into the values file was rejected because the ha profile uses the same `lab` environment with another endpoint. Fleet nodes whose default route and node IP sit on different NICs meet the same condition.

**Evidence.** The first Kubernetes acceptance, `.lab/acceptance-m2/20261115T053321Z`, failed on the restarted agent. The ha profile reproduced the cause with `ss -tnpei`, `tcpdump` and the `KUBE-POSTROUTING` MASQUERADE counter at 0 (`.lab/evidence/m2/cilium-rootcause/`). After the fix `k8s-baseline` run `20261115T055930Z` passed on the running cluster. In cel1 run `20261115T164615Z`, `cilium-api-direct` deleted an agent on a guest with the same split route, and its replacement became Ready with 0 restarts and connected to `https://10.140.0.10:6443`.

## 10. The API VIP

**Decision.** With `k8s_api_vip` set, every control plane runs kube-vip v1.3.1 as a static pod, pinned by tag and OCI index digest, in ARP mode with the lease `plndr-cp-lock`. The first control plane runs it with `super-admin.conf` until `kubeadm init` finishes and then with `admin.conf`. An inventory without a VIP gets the static pod file deleted. Control planes join one at a time.

**Reasons.** The lease lets only one node hold the VIP. Since Kubernetes 1.29 the group permissions of `admin.conf` appear only partway through init, so kube-vip on the first node needs `super-admin.conf` until then. A deleted static pod must come back with the next `make k8s`.

**Evidence.** In `k8s-ha-vip` the API answered on the VIP again 6.031 s after SIGKILL to the holder's QEMU (run `20261115T060557Z`). Other runs measured 7.538, 9.04, 9.045 and 9.041 s on the ha profile and 7.534 s on cel1 (run `20261115T164615Z`). With the kube-vip manifests removed from both surviving control planes, run `20261115T052346Z` failed with no lease holder and no VIP for 120 s, and `make k8s LAB_PROFILE=ha` rewrote them with `changed=1` on those two nodes only. The latest `k8s-ha-vip`, run `20261116T193852Z`, passed. The probes run at 0.5 s intervals, so a gap can read short by one interval.
