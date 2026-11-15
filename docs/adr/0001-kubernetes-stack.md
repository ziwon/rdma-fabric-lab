# ADR 0001: The Kubernetes stack the lab and the fleet share

> Status: accepted, 2026-11-15

## 1. Decision

- Ansible and kubeadm install Kubernetes.
- The lab (`ansible/inventories/lab`) and the GPU cluster of the virtual region cel1 (`ansible/inventories/cel1`) use the same playbook `ansible/playbooks/kubernetes.yml` and the same roles `containerd` and `kubeadm`. They differ only in inventory variables.
- cel1 is a data center that does not exist. It has 3 control planes, 8 DGX B200 nodes and 2 previous-generation PCIe GPU servers. All names and addresses are invented.
- Another site keeps an inventory that follows the same contract outside the repository and uses the same playbook.
- Helm installs the add-ons, Cilium among them. The release list is the single file `helm/releases.yaml`, and `make addons ENV=<env>` installs the releases in that order. The values files are `helm/<release>/values-<env>.yaml`.
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
