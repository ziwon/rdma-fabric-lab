# cel1: operations guide for the virtual GPU region

> Written: 2026-11-15  
> Status: the site model, inventory generation, contract check and Helm rendering ran offline and were confirmed. No target that changes cel1 hosts has ever run (section 8).  
> Basis: `docs/adr/0001-kubernetes-stack.md`. Its section 4 is the inventory contract.

## 1. Region

cel1 is a region that does not exist. All host names, addresses, interface names and switch names were invented for it.

| Class | Hosts | Count | OS | NVIDIA driver and container toolkit | Ports |
|---|---|---:|---|---|---|
| `control_plane` | `cel1-cp-01`–`03` | 3 | Ubuntu 24.04 | None | `mgmt0` |
| `dgx_b200` | `cel1-dgx-01`–`08` | 8 | DGX OS 7 | Installed in DGX OS | `mgmt0`, `stor0`, `stor1`, `rail0`–`rail7` |
| `pcie_gpu` | `cel1-pcie-01`–`02` | 2 | Ubuntu 24.04 | GPU Operator installs them | `mgmt0` |

There are 8 DGX nodes, not 64. The user decided that this host cannot simulate more. The next task boots cel1 as small QEMU guests of the lab harness and runs the real playbooks. Every design in this document assumes that boot.

## 2. Site model

The single file `sites/cel1.yaml` is the single source of truth for the region. It holds the hosts and classes, the address plan, the API address, the syslog address and the InfiniBand compute fabric. The docstring of `scripts/fleet/site.py` defines the format.

### 2.1 Address plan

| Use | Range | Assignment |
|---|---|---|
| In-band management network `management` | `10.140.0.0/24` | API VIP `.10`, syslog `.5`, control planes `.11`–`.13`, DGX `.21`–`.28`, PCIe GPU servers `.31`–`.32` |
| Storage `storage-a`, `storage-b` | `10.141.0.0/24`, `10.141.1.0/24` | DGX `.21`–`.28` |
| Compute rails `rail0`–`rail7` | `10.142.0.0/24`–`10.142.7.0/24` | DGX `.21`–`.28` |
| Pod | `10.160.0.0/16` | kube-controller-manager hands each node a `/24` |
| Service | `10.170.0.0/16` | |

Each range is one L2 segment. The ranges overlap neither each other nor the lab address plan (`lab_address_plan` in `ansible/inventories/lab/group_vars/all/lab.yml`). The validator checks both on every run.

Every host lists all its addresses. No rule computes an address from a host number. The duplicate check and the range check do real work only when the addresses are explicit.

### 2.2 Interface names

A port name is the name a systemd `.link` file assigns as a rename. On hardware it matches by PCI path or MAC. In a QEMU guest it matches by the MAC of the virtio NIC. So the inventory is the same in both environments.

The validator rejects a name longer than 15 characters or with characters other than lowercase letters and digits. It also rejects names that start with `eth`, `en`, `ib`, `wl`, `ww`, `usb` or `sl`. systemd.link(5) warns that a rename to a name the kernel can assign itself races with the kernel. The rest are prefixes of udev predictable names, so they cause confusion.

### 2.3 Compute fabric

`fabrics.compute` is a rail-optimized InfiniBand fabric.

| Item | Value |
|---|---|
| Switch model | `ndr-64`, 64 ports, 4x NDR links |
| Leaves | `cel1-ib-leaf-r0`–`r7`. One leaf per rail |
| Spines | `cel1-ib-spine-01`–`04` |
| Downlinks | `rail r` of DGX n → port n of leaf `r`. 8 per leaf |
| Uplinks | Leaf ports 33–40. 2 per spine. The spine ports are `1 + 2r` and `2 + 2r` |
| Link count | 64 rail, 64 leaf–spine, 128 in total |

Each leaf has 8 downlinks and 8 uplinks, an equal number, so the leaves have no oversubscription. Every link is an entry in an explicit cable list. No rule generates them, so the validator checks the real cabling plan.

M4 builds the ibsim topology from this section. ibsim (`ibsim/sim_net.c`) reads node kinds and port counts, unique node names, links with the port number on each end, and optional link width and speed. The model has all of them. Each rail has one single-port HCA, `mlx5_<r>`. No ibsim file is written yet.

### 2.4 Validator

`make fleet-site-check` validates the model. It rejects a wrong shape at the parsing stage. It also rejects a key that appears twice in the file, because PyYAML silently keeps the later value. When the shape is right, it reports each of the following violations by name.

- Interface name rule violations, unknown networks, classes, switch models and nodes
- A class without exactly one interface on the node network, host addresses that do not match their class
- A host and a switch with the same name, class names that cannot be Ansible group names, host names that are not RFC 1123 labels
- Overlapping ranges, ranges that overlap the lab plan, addresses outside their network, duplicate addresses, a VIP outside the node network
- No control plane, or a VIP interface name that differs between control plane classes
- A leaf without a rail or a spine with a rail, ports outside the port range (`1`–radix), switch ports that are not numbers
- A rail port cabled to the leaf of another rail, a management port cabled to the fabric, rail–spine and leaf–leaf links
- A port used twice, a rail port without a cable, a leaf that lacks an uplink to a spine

`tests/unit/test_fleet_site.py` has one test for each violation. Each test fails if the validator misses its violation.

## 3. Generator

`make fleet-render` writes `ansible/inventories/cel1/` from `sites/cel1.yaml`. Nobody edits it by hand.

| File | Content |
|---|---|
| `hosts.yml` | `kube_control_plane` and `kube_node` under `k8s_cluster`. One group per class |
| `group_vars/all.yml` | `rfl_inventory`, the API address, port, VIP and VIP interface, the Pod and Service CIDRs, `k8s_kube_proxy`, `k8s_control_plane_schedulable`, the syslog address and port |
| `group_vars/<class>.yml` | The `k8s_node_labels` of that class |
| `host_vars/<host>.yml` | `k8s_node_ip` |

The contract groups list their hosts directly. `groups['kube_control_plane'][0]` runs `kubeadm init`, so that order must not depend on the traversal order of child groups. The class groups list the same hosts again. The sha256 of the `ansible-inventory --list` output was the same with `PYTHONHASHSEED` 0, 1, 7, 42 and 999. The host order of `ansible-playbook --list-hosts` differs between runs, but that is because the CLI uses a `set` for its output (`ansible/cli/playbook.py`). `groups['k8s_cluster']` was the same over 6 runs.

The inventory sets no `ansible_host`. The cel1 addresses are invented, so the host must not connect to them. A second inventory (`FLEET_CONNECT`) supplies the connection details. For the simulation it lists 127.0.0.1 port forwards. If a playbook run uses the cel1 inventory without the second inventory, make stops before ssh.

The generator emits the same bytes from the same model. `make fleet-render-check` compares the directory with the rendered output and fails on a difference. When a host leaves the model, the generator deletes that host's old files. It reports files it did not write and does not delete them.

## 4. Inventory contract check

`make inventory-check INVENTORY=<lab|cel1|dir>` compares the `ansible-inventory --list` output with section 4 of the ADR. It does not connect to hosts. A value that is exactly `{{ other_variable }}` resolves to that variable of the same host. The check reports any other template as impossible to evaluate offline.

| Code | Meaning |
|---|---|
| `missing-group` | A contract group is missing, or `kube_control_plane` is empty |
| `group-membership` | A host is in both roles, or `k8s_cluster` is not the union of the two groups |
| `missing-variable` | A host lacks a required variable. A host without `k8s_node_ip` lands here |
| `bad-value` | The type is wrong. For example, `k8s_kube_proxy: "false"` is a string |
| `unresolved-template` | A template that cannot be evaluated offline |
| `inconsistent-variable` | A cluster-wide variable differs between hosts |
| `duplicate-node-ip` | Two hosts use the same `k8s_node_ip` |
| `range-overlap` | The Pod and Service ranges overlap, or a node address or the VIP lies inside them |
| `missing-vip` | There are two or more control planes but no `k8s_api_vip` |
| `vip-endpoint-mismatch` | `k8s_api_vip` and `k8s_api_endpoint` differ |
| `unknown-environment` | `rfl_inventory` is not an environment in `helm/releases.yaml` |
| `reserved-label-prefix` | A label uses `kubernetes.io`, `k8s.io` or a subdomain of them |
| `invalid-label` | A label key or value breaks the Kubernetes syntax, or a value is not a string |
| `cilium-values`, `cilium-mismatch` | The Cilium values file is missing or disagrees with the inventory |

`invalid-label` catches values that are not strings. In YAML, `false` is a boolean, and kubelet receives it as `False`. GPU Operator understands only `"false"`.

`cilium-mismatch` checks the places where the ADR requires the same value for Cilium and kubeadm. These are the cases. kube-proxy and kube-proxy replacement are both on or both off. With kube-proxy replacement, `k8sServiceHost` and `k8sServicePort` are not the API address and port. The cluster-pool IPAM range is not `k8s_pod_cidr`. `ipam.mode` is missing, so the chart default cluster-pool applies. With native routing, `ipv4NativeRoutingCIDR` does not cover the Pod range.

The lab may rely on the defaults the ADR sets (`k8s_api_port` 6443, no VIP, `k8s_control_plane_schedulable` false, `k8s_node_labels` an empty map). An inventory outside the lab is checked with `--explicit`, and every contract variable must be present on every host. `tests/unit/test_inventory_contract.py` has a test for each violation. Each test fails if the check misses its violation. The test input is real `ansible-inventory --list` output from the cel1 inventory (`tests/fixtures/fleet/README.md`).

## 5. Cilium

`helm/cilium/values-cel1.yaml` targets chart 1.20.4.

| Key | Value | Reason |
|---|---|---|
| `kubeProxyReplacement` | `true` | cel1 has `k8s_kube_proxy: false`. kubeadm does not install kube-proxy |
| `k8sServiceHost`, `k8sServicePort` | `10.140.0.10`, `6443` | Without kube-proxy, Cilium must reach the API before any Service exists. This is the kube-vip VIP |
| `routingMode`, `autoDirectNodeRoutes` | `native`, `true` | Explained below |
| `ipv4NativeRoutingCIDR` | `10.160.0.0/16` | The Pod range. Packets to this range are not SNATed |
| `ipam.mode` | `kubernetes` | Node Pod CIDRs come from the kubeadm `podSubnet`. The chart default cluster-pool uses `10.0.0.0/8` |
| `devices` | `mgmt0` | Attaches the eBPF datapath to the management port only |

**The routing mode is native.** All nodes sit on one management L2 segment. So `autoDirectNodeRoutes` gives each node a route to every other node's Pod CIDR via that node's management address. Routers do not need to know the Pod CIDRs. The Cilium 1.20 documentation states that this option suffices when all nodes share one L2 network (https://docs.cilium.io/en/v1.20/network/concepts/routing/). There is no encapsulation, so the Pod MTU stays 1500. VXLAN costs 50 bytes. Packets on the wire show the real Pod addresses, so tcpdump tracing is easy. The trade-off is that this mode does not work when the management network splits into several L3 segments. In that case, switch to tunnel mode or BGP. The lab uses a VXLAN tunnel in its initial Cilium setup. So the lab does not prove native routing. The cel1 simulation proves it.

**Pod traffic flows only over the management network.** `k8s_node_ip` is the management address. The automatic direct routes use that address as the next hop. `devices` excludes the rail and storage ports. The InfiniBand rails stay dedicated to RDMA.

## 6. GPU Operator

`helm/gpu-operator/values-cel1.yaml` targets chart v26.7.2. It turns the driver and the toolkit on at the chart level. Node labels turn them off per node.

| Class | `k8s_node_labels` | Result |
|---|---|---|
| `dgx_b200` | `nvidia.com/gpu.deploy.driver: "false"`, `nvidia.com/gpu.deploy.container-toolkit: "false"` | Uses the driver and toolkit of DGX OS |
| `pcie_gpu` | None | The Operator installs the driver and the toolkit |
| `control_plane` | None | With no GPU, NFD adds no `pci-10de` label. The Operator deploys nothing |

kubelet adds the labels when it registers. kubelet applies `--node-labels` only at the first registration. A change after registration needs `kubectl label`.

The evidence follows.

- The documentation sections "Preventing Installation of NVIDIA GPU Driver on Some Nodes" and "Preventing Installation of Operands on Some Nodes", at https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html. On 2026-11-15 this page names v26.7.2 as the current patch release. The versioned paths (`/26.7.2/`, `/v26.7.2/`) returned 404.
- The documentation has no per-node section for the toolkit label, so this was confirmed in the source. In github.com/NVIDIA/gpu-operator tag v26.7.2 (commit `5a9c8a8b3f5b`), `addGPUStateLabels` in `controllers/state_manager.go` sets to `"true"` only the `nvidia.com/gpu.deploy.*` labels that are missing or empty on a node. It does not overwrite an existing `"false"`. The nodeSelectors in `assets/state-driver/0500_daemonset.yaml` and `assets/state-container-toolkit/0500_daemonset.yaml` are `nvidia.com/gpu.deploy.driver: "true"` and `nvidia.com/gpu.deploy.container-toolkit: "true"`, respectively.
- The "Pre-Installed NVIDIA GPU Drivers and NVIDIA Container Toolkit" section of the same documentation says it applies to DGX. It says to set the default runtime to `nvidia` before installing the Operator. No role in this repository makes that setting yet (section 10).

On the PCIe GPU servers, the toolkit writes its runtime settings to the drop-in `/etc/containerd/conf.d/99-nvidia.toml` (`DefaultContainerdDropInConfigFile` in `controllers/object_controls.go`). A comment in the same source says that a containerd drop-in needs a change to the top-level configuration. The `config.toml` of the `containerd` role already imports `/etc/containerd/conf.d/*.toml`. So the inference is that the toolkit leaves the top-level file alone and that an Ansible rerun also reports `changed=0`. This is not confirmed. Only a PCIe GPU server with a GPU can confirm it.

## 7. rsyslog

The site model's `services.syslog` is the central syslog address `10.140.0.5:514/tcp`. The generator carries it into `rsyslog_client_tcp_host` and `rsyslog_client_tcp_port`.

**`rsyslog_client` runs on every node of the cluster.** That includes the control planes. The cause of a failed GPU job can sit in the kubelet, containerd or kernel messages of any node. No variable turns it off per host. The old `kube_enable_rsyslog_client` toggle is removed.

- Without a syslog address, the role fails and changes nothing. It no longer writes an empty file and passes silently as it used to.
- Forwarding uses a disk-assisted queue. While the server is down, messages accumulate and are sent again later. The Ubuntu 24.04 `/etc/rsyslog.conf` sets `$WorkDirectory /var/spool/rsyslog`.
- The playbook `ansible/playbooks/fleet/rsyslog-client.yml` rejects a host whose `rfl_inventory` is missing or `lab`.
- The rendered configuration file went into `/etc/rsyslog.d/` of an Ubuntu 24.04 container (rsyslog 8.2312.0-3ubuntu9.4), and `rsyslogd -N1` ran on it. The exit code was 0. A file with a typo failed with `parameter 'Prot' not known`. The role does not use Ansible `validate:`, because the Ubuntu AppArmor profile blocks rsyslogd from reading files outside `/etc/rsyslog.d/`. A run on the host with a file in a temporary path gave `Permission denied`.

## 8. Make targets

| Target | Action | Host access |
|---|---|---|
| `make fleet-site-check` | Validates the site model | None |
| `make fleet-render` | Generates the inventory | None |
| `make fleet-render-check` | Compares the generated output with the committed inventory | None |
| `make inventory-check INVENTORY=<lab\|cel1\|dir>` | Contract check | None |
| `make values-check ENV=<env>` | Checks that every key of the values file exists in the `values.yaml` of the chart at its locked version | None. It downloads the chart from the public chart repository |
| `make addons-template ENV=cel1` | Renders both releases and validates them with kubeconform against v1.36.6 | None. It downloads the charts and schemas |
| `make fleet-check` | All the checks above | None |
| `make fleet-k8s CONFIRM=cel1 FLEET_CONNECT=<inventory>` | `make versions SCOPE=fleet`, then `ansible/playbooks/kubernetes.yml` | Yes |
| `make fleet-addons CONFIRM=cel1` | `make versions SCOPE=fleet`, then `make addons ENV=cel1` with the cel1 kubeconfig | Yes |
| `make fleet-rsyslog CONFIRM=cel1 FLEET_CONNECT=<inventory>` | `ansible/playbooks/fleet/rsyslog-client.yml` | Yes |

Without `CONFIRM=cel1`, the last three targets run no prerequisite and stop with exit code 2. `fleet-k8s` and `fleet-rsyslog` also stop when the cel1 inventory has no `FLEET_CONNECT`. `fleet-addons` stops when the kubeconfig (`${XDG_STATE_HOME:-~/.local/state}/rdma-fabric-lab/cel1/kubeconfig`) is missing. `ARGS=` adds arguments such as `--limit`.

## 9. Offline check results

These ran in this repository on 2026-11-15.

| Command | Exit code | Result |
|---|---:|---|
| `make fleet-site-check` | 0 | 13 hosts, 11 networks, 128 links, valid against the lab plan |
| `make fleet-render-check` | 0 | 18 rendered files, 0 differences |
| `make inventory-check INVENTORY=cel1` | 0 | 13 hosts, 0 violations, every variable explicit |
| `make inventory-check INVENTORY=lab` | 0 | 3 hosts, 0 violations. This is the result after the lab worker added the contract variables and `values-lab.yaml` |
| `make values-check ENV=cel1` | 0 | 2 values files, 0 unknown keys |
| `make addons-template ENV=cel1` | 0 | 25 Cilium resources valid. Of 36 GPU Operator resources, 27 valid and 9 skipped |
| `make fleet-k8s`, `make fleet-addons`, `make fleet-rsyslog` (no CONFIRM, `CONFIRM=lab`) | 2 | The checksums of the repository and the state directory were the same before and after the run |

The 9 skipped GPU Operator resources are 8 CRDs and 1 `ClusterPolicy`. The default schema location of kubeconform has no schema for them. Those 9 were not validated.

Each check was broken once to confirm that it fails.

| What was broken | Result |
|---|---|
| `kubeProxyReplacment` in the Cilium values | `values-check` exit code 2, `kubeProxyReplacment is not a key of the chart` |
| `devicePlugn` in the GPU Operator values | `values-check` exit code 2, `devicePlugn is not a key of the chart` |
| Cilium `priorityClassName` as a list | `addons-template` exit code 2. helm rejects it with `at '/priorityClassName': got array, want string` |
| GPU Operator `operator.priorityClassName` as a list | `addons-template` exit code 2. kubeconform rejects Deployment `gpu-operator` with `got array, want null or string` |
| The duplicate port, radix and rail cabling checks of the validator, each turned off | `test_fleet_site.py` fails each time |
| The reserved prefix, VIP rule and duplicate node IP checks of the contract check, each turned off | `test_inventory_contract.py` fails each time |

## 10. Not run

| Item | Status | Reason |
|---|---|---|
| `make fleet-k8s`, `make fleet-addons`, `make fleet-rsyslog` (`CONFIRM=cel1`) | `not_run` | This task boots no guest and does not connect to cel1 addresses. The next simulation task runs them |
| `changed=0` on a second `rsyslog_client` run | `not_run` | There is no guest to run it on |
| Behavior of Cilium native routing and kube-proxy replacement | `not_run` | Only rendered |
| Placement driven by the GPU Operator labels | `not_run` | There is no GPU. The simulation guests have no GPU either, so NFD does not treat them as GPU nodes. So the simulation cannot prove this either |
| Default runtime `nvidia` on DGX nodes | `not_run` | No role sets it yet. Section 6 |
| ibsim topology | `not_run` | M4 builds it |
| Schema validation of the 9 GPU Operator CRDs and ClusterPolicy | `not_run` | No kubeconform schema exists |

## 11. Applying this to another site

Another site gives the same code only its own files. All those files live outside the repository.

1. Write the site model in this format, for example `/srv/site-x/site.yaml`. Real MACs or PCI paths stay out of the model. The site's provisioning writes the `.link` files. The `site:` value becomes `rfl_inventory`. The contract requires this value to be an environment in `helm/releases.yaml`, so for now it must be `cel1`. Item 4 gives the reason.
2. Validate the model and generate the inventory.

   ```bash
   PYTHONPATH=scripts .venv/bin/python -m fleet site-check /srv/site-x/site.yaml
   PYTHONPATH=scripts .venv/bin/python -m fleet render /srv/site-x/site.yaml --out /srv/site-x/inventory
   make inventory-check INVENTORY=/srv/site-x/inventory
   ```

3. Put the connection details (`ansible_host`, `ansible_user`) in a second inventory.

   ```bash
   make fleet-k8s CONFIRM=cel1 FLEET_INVENTORY=/srv/site-x/inventory FLEET_CONNECT=/srv/site-x/connect.yml
   ```

4. Helm values cannot come from outside the repository yet. `rfl_inventory` must be an environment in `helm/releases.yaml`, and the values files are `helm/<release>/values-<env>.yaml`. `values-cel1.yaml` holds the cel1 VIP and Pod range, so another site that uses it unchanged gets `cilium-mismatch`. `make addons` and the contract check need support for a values override file, and that support does not exist yet.

## 12. Decisions for the user

| Decision | Default applied |
|---|---|
| Label prefix | `rfl.example/node-class`. Change it once a real owned domain exists |
| Leaf–spine ratio | 1:1. 8 uplinks per leaf |
| Storage port layout | A separate subnet per port. No bonding |
| syslog target | Management network `10.140.0.5:514`, TCP. The next task decides who receives this address in the simulation |
| DGX default runtime | The `containerd` role takes `k8s_container_runtime_default: nvidia` and sets it |
| Helm values for another site | Needs the override mechanism of section 11 |
