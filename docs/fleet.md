# cel1: operations guide for the virtual GPU region

> Written: 2026-11-15, updated 2026-11-16  
> Status: the site model, inventory generation, contract check and Helm rendering ran offline and were confirmed. The three fleet targets ran against the cel1 simulation, 13 QEMU guests on this host, from wiped guests: run `20261115T164615Z` passed, and the latest, `20261116T203232Z`, passed in the full reproduction of `docs/verification.md`. Section 13 describes the simulation. No real host exists.  
> Basis: `docs/adr/0001-kubernetes-stack.md`. Its section 4 is the inventory contract.

## 1. Region

cel1 is a region that does not exist. All host names, addresses, interface names and switch names were invented for it.

| Class | Hosts | Count | OS | NVIDIA driver and container toolkit | Ports |
|---|---|---:|---|---|---|
| `control_plane` | `cel1-cp-01`–`03` | 3 | Ubuntu 24.04 | None | `mgmt0` |
| `dgx_b200` | `cel1-dgx-01`–`08` | 8 | DGX OS 7 | Installed in DGX OS | `mgmt0`, `stor0`, `stor1`, `rail0`–`rail7` |
| `pcie_gpu` | `cel1-pcie-01`–`02` | 2 | Ubuntu 24.04 | GPU Operator installs them | `mgmt0` |

There are 8 DGX nodes, not 64. The user decided that this host cannot simulate more. The cel1 simulation boots one QEMU guest per host and runs the real playbooks against them (section 13).

## 2. Site model

The single file `sites/cel1.yaml` is the single source of truth for the region. It holds the hosts and classes, the address plan, the API address, the syslog address, the InfiniBand compute fabric and the pod networks on its rails. The docstring of `scripts/fleet/site.py` defines the format.

### 2.1 Address plan

| Use | Range | Assignment |
|---|---|---|
| In-band management network `management` | `10.140.0.0/24` | API VIP `.10`, syslog `.5`, control planes `.11`–`.13`, DGX `.21`–`.28`, PCIe GPU servers `.31`–`.32` |
| Storage `storage-a`, `storage-b` | `10.141.0.0/24`, `10.141.1.0/24` | DGX `.21`–`.28` |
| Compute rails `rail0`–`rail7` | `10.142.0.0/24`–`10.142.7.0/24` | DGX `.21`–`.28` |
| Pod ranges on the rails, `pod_cidr` of `rail0`–`rail7` | `10.143.0.0/24`–`10.143.7.0/24` | nv-ipam hands each DGX node a block of 16 per rail (section 14) |
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

`make track-b-render` writes the ibsim topology `tracks/b-ibsim/topologies/cel1-compute.net` from this section, and the Track B scenario B-04 runs on it (`docs/adr/0005-track-b-fabric-simulation.md`). ibsim (`ibsim/sim_net.c`) reads node kinds and port counts, unique node names, links with the port number on each end, and optional link width and speed. The model has all of them. Each rail has one single-port HCA, `mlx5_<r>`. `make track-b-render-check` fails when the file differs from what the model renders.

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
- For the rail networks of section 14: a `pod_cidr` on a network that is no InfiniBand rail, some rails without one, `kubernetes.rail_networks` without any `pod_cidr` or the reverse, a namespace that is no RFC 1123 label, a block below 1, an empty selector or one that selects no class, a selected class that lacks a rail, a class with a rail that the selector misses, two interface names for one rail among the selected classes, and a pod range too small for the selected nodes in blocks

`tests/unit/test_fleet_site.py` has one test for each violation, and `tests/unit/test_fleet_rail_networks.py` one for each rail network rule. Each test fails if the validator misses its violation.

## 3. Generator

`make fleet-render` writes `ansible/inventories/cel1/` from `sites/cel1.yaml`. Nobody edits it by hand.

| File | Content |
|---|---|
| `hosts.yml` | `kube_control_plane` and `kube_node` under `k8s_cluster`. One group per class |
| `group_vars/all.yml` | `rfl_inventory`, the API address, port, VIP and VIP interface, the Pod and Service CIDRs, `k8s_kube_proxy`, `k8s_control_plane_schedulable`, `k8s_admin_kubeconfig`, the syslog address and port |
| `group_vars/<class>.yml` | The `k8s_node_labels` and `k8s_container_runtime_default` of that class |
| `host_vars/<host>.yml` | `k8s_node_ip` |

The same command writes `helm/rail-networks/values-cel1.yaml`, the values of the chart of section 14, from `kubernetes.rail_networks` and the `pod_cidr` of each rail. `make fleet-render-check` compares it too.

The contract groups list their hosts directly. `groups['kube_control_plane'][0]` runs `kubeadm init`, so that order must not depend on the traversal order of child groups. The class groups list the same hosts again. The sha256 of the `ansible-inventory --list` output was the same with `PYTHONHASHSEED` 0, 1, 7, 42 and 999. The host order of `ansible-playbook --list-hosts` differs between runs, but that is because the CLI uses a `set` for its output (`ansible/cli/playbook.py`). `groups['k8s_cluster']` was the same over 6 runs.

The inventory sets no `ansible_host`. The cel1 addresses are invented, so the host must not connect to them. A second inventory (`FLEET_CONNECT`) supplies the connection details. For the simulation it lists 127.0.0.1 port forwards. Section 8 describes the gate that stops every other case before ssh.

`kubernetes.admin_kubeconfig` in the model is `${XDG_STATE_HOME:-~/.local/state}/rdma-fabric-lab/cel1/kubeconfig`, the path of ADR section 5 written as the ADR writes it. The `kubeadm` role replaces that state home prefix with the state home of the control node, so the role and `CEL1_KUBECONFIG` of `mk/fleet.mk` name the same file with or without `XDG_STATE_HOME`. A class sets `container_runtime: runc|nvidia`, runc when absent. `dgx_b200` sets `nvidia`.

The generator emits the same bytes from the same model. `make fleet-render-check` compares the directory with the rendered output and fails on a difference. When a host leaves the model, the generator deletes that host's old files. It reports files it did not write and does not delete them.

## 4. Inventory contract check

`make inventory-check INVENTORY=<lab|cel1|dir>` compares the `ansible-inventory --list` output with section 4 of the ADR. It does not connect to hosts. A value that is exactly `{{ other_variable }}` resolves to that variable of the same host. The check reports any other template as impossible to evaluate offline.

| Code | Meaning |
|---|---|
| `missing-group` | A contract group is missing, or `kube_control_plane` is empty |
| `group-membership` | A host is in both roles, or `k8s_cluster` is not the union of the two groups |
| `missing-variable` | A host lacks a required variable. A host without `k8s_node_ip` lands here |
| `bad-value` | The type is wrong. For example, `k8s_kube_proxy: "false"` is a string, or `k8s_container_runtime_default` is neither `runc` nor `nvidia` |
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
| `cni.confPath`, `cni.exclusive` | `/etc/cni/multus/net.d`, `false` | Cilium's conflist goes where only Multus reads it, as in the lab (`docs/adr/0004-track-a-pods-and-net1.md` section 1.4, section 14) |

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
- The "Pre-Installed NVIDIA GPU Drivers and NVIDIA Container Toolkit" section of the same documentation says it applies to DGX. It says to set the default runtime to `nvidia` before installing the Operator. The `containerd` role does that on every host with `k8s_container_runtime_default: nvidia`, which the model sets for `dgx_b200`.
- The opt-out labels hold only on a node the Operator sees as a GPU node. In the same tag, `updateGPUStateLabels` in `controllers/nodelabeling_controller.go` calls `removeAllGPUStateLabels` on every node without `nvidia.com/gpu.present`. The simulation guests have no GPU, so the Operator removed both opt-outs from all 8 DGX Nodes when it started. The next `make fleet-k8s` put them back, and they stayed. On a DGX node with GPUs, NFD reports the NVIDIA PCI device, the Operator sets `nvidia.com/gpu.present=true`, and the opt-outs stay. A DGX node whose GPUs disappear from NFD would lose its opt-outs at the next Operator start until `make fleet-k8s` runs again.

On the PCIe GPU servers, the toolkit writes its runtime settings to the drop-in `/etc/containerd/conf.d/99-nvidia.toml` (`DefaultContainerdDropInConfigFile` in `controllers/object_controls.go`). A comment in the same source says that a containerd drop-in needs a change to the top-level configuration. The `config.toml` of the `containerd` role already imports `/etc/containerd/conf.d/*.toml`. So the inference is that the toolkit leaves the top-level file alone and that an Ansible rerun also reports `changed=0`. This is not confirmed. Only a PCIe GPU server with a GPU can confirm it.

## 7. rsyslog

The site model's `services.syslog` is the central syslog address `10.140.0.5:514/tcp`. The generator carries it into `rsyslog_client_tcp_host` and `rsyslog_client_tcp_port`.

**`rsyslog_client` runs on every node of the cluster.** That includes the control planes. The cause of a failed GPU job can sit in the kubelet, containerd or kernel messages of any node. No variable turns it off per host. The old `kube_enable_rsyslog_client` toggle is removed.

- Without a syslog address, the role fails and changes nothing. It no longer writes an empty file and passes silently as it used to.
- Forwarding uses a disk-assisted queue. While the server is down, messages accumulate and are sent again later. The Ubuntu 24.04 `/etc/rsyslog.conf` sets `$WorkDirectory /var/spool/rsyslog`.
- The playbook `ansible/playbooks/fleet/rsyslog-client.yml` rejects a host whose `rfl_inventory` is missing or `lab`. It runs with `become: true`. Its first run against the simulation failed on all 13 hosts at `apt-mark manual rsyslog`, because the play ran as the SSH user.
- The rendered configuration file went into `/etc/rsyslog.d/` of an Ubuntu 24.04 container (rsyslog 8.2312.0-3ubuntu9.4), and `rsyslogd -N1` ran on it. The exit code was 0. A file with a typo failed with `parameter 'Prot' not known`. The role does not use Ansible `validate:`, because the Ubuntu AppArmor profile blocks rsyslogd from reading files outside `/etc/rsyslog.d/`. A run on the host with a file in a temporary path gave `Permission denied`.

## 8. Make targets

| Target | Action | Host access |
|---|---|---|
| `make fleet-site-check` | Validates the site model | None |
| `make fleet-render` | Generates the inventory and the rail network values | None |
| `make fleet-render-check` | Compares the generated output with the committed inventory and values | None |
| `make inventory-check INVENTORY=<lab\|cel1\|dir>` | Contract check | None |
| `make values-check ENV=<env>` | Checks that every key of the values file exists in the `values.yaml` of the chart at its locked version | None. It downloads the chart from the public chart repository |
| `make addons-template ENV=cel1` | Renders the five releases, converts the CRDs they render into strict JSON schemas, and validates every object with kubeconform against v1.36.6. A custom resource whose kind a CRD of the environment defines fails when it finds no schema | None. It downloads the charts and schemas |
| `make fleet-sim-profile`, `make fleet-sim-profile-check` | Writes or compares `tests/integration/qemu-lab/profiles/cel1.tsv` | None |
| `make fleet-check` | All the checks above | None |
| `make fleet-sim-stand-ins CONFIRM=cel1 FLEET_CONNECT=<simulation inventory>` | `ansible/playbooks/simulation-stand-ins.yml` | Simulation guests only |
| `make fleet-k8s CONFIRM=cel1 FLEET_CONNECT=<simulation inventory>` | `make versions SCOPE=fleet`, then `ansible/playbooks/kubernetes.yml` | Simulation guests only |
| `make fleet-addons CONFIRM=cel1 FLEET_CONNECT=<simulation inventory>` | `make versions SCOPE=fleet`, then `make addons ENV=cel1` with the cel1 kubeconfig | Simulation guests only |
| `make fleet-rsyslog CONFIRM=cel1 FLEET_CONNECT=<simulation inventory>` | `ansible/playbooks/fleet/rsyslog-client.yml` | Simulation guests only |
| `make fleet-sim-test FLEET_CONNECT=<simulation inventory>` | The `cel1-sim-baseline` scenario through rfl, VIP holder power cut included | Simulation guests only |
| `make fleet-rails-test FLEET_CONNECT=<simulation inventory>` | The `cel1-rail-networks` scenario of section 14 through rfl, a clean power cycle of a rail host included | Simulation guests only |
| `make fleet-cni-migrate CONFIRM=cel1 FLEET_CONNECT=<simulation inventory>` | `ansible/playbooks/cni-migrate.yml`, which removes Cilium's old conflist from `/etc/cni/net.d` on a cluster built before Cilium got its own directory, one node at a time | Simulation guests only |

Without `CONFIRM=cel1`, the targets that change hosts run no prerequisite and stop with exit code 2. After it, `fleet sim-gate` reads the merged `ansible-inventory --list` output and, for `fleet-addons`, the kubeconfig. Every host whose `rfl_inventory` names a site model under `sites/` must have `ansible_host` 127.0.0.1 and an `ansible_port`, the first control plane must have an `rfl_kubeconfig_server` on 127.0.0.1, and every cluster server of the kubeconfig must be on 127.0.0.1. Otherwise the gate stops with exit code 2. It reads only local files. `fleet-addons` also stops when the kubeconfig (`${XDG_STATE_HOME:-~/.local/state}/rdma-fabric-lab/cel1/kubeconfig`) is missing. `ARGS=` adds arguments such as `--limit`.

The simulation inventory is the `inventory.yml` that `LAB_PROFILE=cel1 make lab-up` writes into the harness state directory. Its hosts point at 127.0.0.1 forwards, the first control plane names the forward of its API port as `rfl_kubeconfig_server`, and the group `simulation_syslog` holds the stand-in syslog server. `make fleet-sim-stand-ins` gives the hosts with `k8s_container_runtime_default: nvidia` a stand-in `/usr/bin/nvidia-container-runtime` that logs each call and execs runc, labels the registered Nodes of the rail hosts with `feature.node.kubernetes.io/pci-15b3.present=true` as NFD labels a node with a Mellanox NIC (section 14), and configures the stand-in syslog server to receive on the site syslog address. It labels only Nodes that exist, so it runs again after `make fleet-k8s`. No stand-in is part of the inventory or the fleet roles.

## 9. Offline check results

These ran in this repository on 2026-11-18, after the rail networks of section 14. `make fleet-check` ran them in this order and exited 0.

| Command | Exit code | Result |
|---|---:|---|
| `make fleet-site-check` | 0 | 13 hosts, 11 networks, 128 links, 8 pod rail ranges, valid against the lab plan |
| `make fleet-render-check` | 0 | 18 inventory files and the rail network values, 0 differences |
| `make inventory-check INVENTORY=cel1` | 0 | 13 hosts, 0 violations, every variable explicit |
| `make values-check ENV=cel1` | 0 | 5 values files, 0 unknown keys |
| `make addons-template ENV=cel1` | 0 | 16 schemas from 16 CRDs. cilium 25 of 25 valid. multus 5 valid, 1 CRD skipped. gpu-operator 28 valid, 8 CRDs skipped. network-operator 4 valid, 6 CRDs skipped. rail-networks 18 valid, 1 CRD skipped |
| `make addons-template ENV=lab` | 0 | 4 schemas from 4 CRDs. cilium 25 of 25, multus 5 and whereabouts 6 valid, 1 and 3 CRDs skipped |
| `make fleet-k8s`, `make fleet-addons`, `make fleet-rsyslog` (no CONFIRM, `CONFIRM=lab`) | 2 | The checksums of the repository and the state directory were the same before and after the run. This row is from 2026-11-15 |

Only CustomResourceDefinition objects are skipped now, because kubeconform's default schema location gave none for them. Every custom resource is checked against a schema converted from its CRD, the GPU Operator's `ClusterPolicy`, the `NicClusterPolicy`, the 8 `IPoIBNetwork` and the 8 `IPPool` among them. Before 2026-11-18 the `ClusterPolicy` was skipped too.

Each check was broken once to confirm that it fails.

| What was broken | Result |
|---|---|
| `kubeProxyReplacment` in the Cilium values | `values-check` exit code 2, `kubeProxyReplacment is not a key of the chart` |
| `devicePlugn` in the GPU Operator values | `values-check` exit code 2, `devicePlugn is not a key of the chart` |
| `upgradeCRD` in the Network Operator values | `values-check` exit code 2, 1 unknown key |
| Cilium `priorityClassName` as a list | `addons-template` exit code 2. helm rejects it with `at '/priorityClassName': got array, want string` |
| GPU Operator `operator.priorityClassName` as a list | `addons-template` exit code 2. kubeconform rejects Deployment `gpu-operator` with `got array, want null or string` |
| `enableWebhok` in the `nvIpam` block of the policy template | `addons-template` exit code 2. `NicClusterPolicy nic-cluster-policy`: `at '/spec/nvIpam': additional properties 'enableWebhok' not allowed` |
| `perNodeBlocksize` in the pool template | `addons-template` exit code 2. All 8 `IPPool` invalid with `missing property 'perNodeBlockSize'` |
| `per_node_block: 32` in the model | `fleet-site-check` exit code 2. `networks.rail0.pod_cidr: 10.143.0.0/24 holds 254 pod addresses, and 8 nodes in blocks of 32 need 256`, once per rail |
| `perNodeBlockSize: 8` edited into the rendered values | `fleet-render-check` exit code 2, `helm/rail-networks/values-cel1.yaml: differs from the site model` |
| The duplicate port, radix and rail cabling checks of the validator, each turned off | `test_fleet_site.py` fails each time |
| The rail network rules, the pod range overlap, the pool capacity rule and the values drift check, each turned off | `test_fleet_rail_networks.py` fails each time |
| The strict conversion, the check for a skipped custom resource, the chart's digest check, a different Multus directory, one byte of the vendored CRD | `test_addons_schemas.py` or `test_rail_networks_chart.py` fails each time |
| The reserved prefix, VIP rule and duplicate node IP checks of the contract check, each turned off | `test_inventory_contract.py` fails each time |

## 10. Not run

The cel1 simulation ran the three fleet targets on 13 guests (section 13). That run settled the earlier items about the targets, a second `rsyslog_client` run, Cilium native routing, kube-proxy replacement and the nvidia default runtime, and Track B builds the ibsim topology. These remain.

| Item | Status | Reason |
|---|---|---|
| Placement driven by the GPU Operator labels | `not_run` | There is no GPU. The guests have none either, so NFD reports no NVIDIA device and the Operator creates no operand |
| The real NVIDIA container toolkit on DGX nodes | `not_run` | The simulation uses a stand-in runtime that execs runc |
| The toolkit drop-in on PCIe GPU servers | `not_run` | Section 6. Only a server with a GPU can confirm it |
| InfiniBand on the rails | `not_run` | The simulation rails carry Ethernet. Track B simulates the fabric's management plane in B-04. `docs/adr/0007-cel1-ipoib-pod-networks.md` decides the pod networks on them |
| Schema validation of the CRD objects themselves | `not_run` | kubeconform's default location gave no schema for CustomResourceDefinition. The custom resources are validated (section 9) |
| An RDMA device in a pod through the device plugin, and a pod on the rails without `privileged` | `not_run` | The plugin finds RDMA devices only on PCI functions, and a guest has none (section 14) |
| The NFD label `pci-15b3.present` from a real ConnectX port | `not_run` | A stand-in sets it on the simulation's rail hosts (section 14) |

## 11. Applying this to another site

Another site gives the same code only its own files. All those files live outside the repository.

1. Write the site model in this format, for example `/srv/site-x/site.yaml`. Real MACs or PCI paths stay out of the model. The site's provisioning writes the `.link` files. The `site:` value becomes `rfl_inventory`. The contract requires this value to be an environment in `helm/releases.yaml`, so for now it must be `cel1`. Item 4 gives the reason.
2. Validate the model and generate the inventory.

   ```bash
   PYTHONPATH=scripts .venv/bin/python -m fleet site-check /srv/site-x/site.yaml
   PYTHONPATH=scripts .venv/bin/python -m fleet render /srv/site-x/site.yaml --out /srv/site-x/inventory
   make inventory-check INVENTORY=/srv/site-x/inventory
   ```

3. Put the connection details (`ansible_host`, `ansible_user`) in a second inventory. The gate of section 8 holds every host whose `rfl_inventory` names a site model under `sites/` to 127.0.0.1, so a site that borrows `cel1` as its environment name reaches only simulation guests. Item 4 is the way out.

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
| syslog target | Management network `10.140.0.5:514`, TCP. In the simulation the stand-in guest `cel1-sim-syslog` receives it |
| DGX default runtime | `container_runtime: nvidia` on `dgx_b200` in the model. The `containerd` role makes it the default |
| GPU Operator labels on a DGX node without GPUs | The Operator removes the opt-outs at its start, and `make fleet-k8s` puts them back (section 6). The role keeps converging them |
| Helm values for another site | Needs the override mechanism of section 11 |

## 13. The simulation

`tests/integration/qemu-lab/acceptance-cel1.sh` builds the region from wiped guests with the fleet targets of section 8 and runs `cel1-sim-baseline`, whose 20 checks `tracks/cel1/scenarios/cel1-sim-baseline.yaml` declares. `make fleet-sim-profile` writes the harness profile `tests/integration/qemu-lab/profiles/cel1.tsv` from the model, one guest per host and one NIC per class interface under its name, and `make fleet-sim-profile-check` fails when the profile differs from the model. `docs/runbook.md` section 4 gives the guest sizes.

| Site model | Simulation |
|---|---|
| Bare-metal hosts. DGX OS 7 on the DGX nodes | KVM guests. Ubuntu 24.04.5 on every class |
| 8 B200 GPUs per DGX node, GPUs in the PCIe servers | No GPU, no NVIDIA driver, no NVIDIA PCI device |
| The NVIDIA container toolkit of DGX OS | `/usr/bin/nvidia-container-runtime` is a stand-in that logs each call to `/var/log/rfl-nvidia-container-runtime.log` and execs `/usr/local/sbin/runc`. `make fleet-sim-stand-ins` installs it on the hosts whose `k8s_container_runtime_default` is `nvidia`. The inventory and the roles are unchanged |
| `rail0`–`rail7` are InfiniBand ports on ConnectX HCAs `mlx5_0`–`mlx5_7` | virtio NICs on one unprivileged Ethernet switch per rail. No RDMA device. The rails carry Ethernet frames |
| One L2 segment per network | `switch.py`, one per network, 11 in total, AF_UNIX datagrams only. Every QEMU and switch process runs AppArmor `unconfined` with `CapEff: 0000000000000000` |
| Interfaces renamed by `.link` files from provisioning | cloud-init netplan matches each MAC, renames the NIC and sets its model address at first boot |
| Each host has only its model interfaces | Each guest also has a QEMU user NIC `enp0s3` (`10.0.2.15`) with the default route, for SSH and package downloads. The node IP sits on `mgmt0`. This is the condition of the Cilium API defect of `docs/adr/0001-kubernetes-stack.md` section 9 |
| NFD labels a DGX node with its ConnectX ports, `feature.node.kubernetes.io/pci-15b3.present=true` | `make fleet-sim-stand-ins` sets that label on the 8 rail hosts once their Nodes exist. NFD left it alone |
| The central syslog server is outside the model, known only by `10.140.0.5:514` | The guest `cel1-sim-syslog` holds `10.140.0.5` on the management network and receives TCP syslog with rsyslog `imtcp`, one file per sending host. It is in the override group `simulation_syslog`, not in the site inventory |
| The control node reaches the API on the VIP | Port 6443 of `cel1-cp-01` is forwarded to `127.0.0.1:22340`. The kubeconfig names that forward with `tls-server-name: kubernetes` |
| Power failure | `lab.sh kill` sends SIGKILL to QEMU |
| A panic leaves a crash dump or a reboot | `-no-reboot` ends the QEMU process and keeps the console in `serial.log` |

The run below predates section 14. Multus, the Network Operator and the rail networks have not run in the simulation.

Run `20261115T164615Z` passed all 20 checks from wiped guests in `.lab/acceptance-cel1/20261115T163759Z`, 46 steps with no unexpected exit code. The second `make fleet-k8s` before the GPU Operator and a converged run after it reported `changed=0` on every host, and the second `make fleet-addons` left both releases at revision 1. The first `make fleet-k8s` after the Operator started changed only the opt-out labels of section 6 on the 8 DGX hosts. Every refusal of the gate exited 2, and strace saw no IPv4 or IPv6 call in any of them (`.lab/evidence/cel1-sim/acceptance/strace-summary.txt`, `.lab/evidence/cel1-sim/refusals-final.log`). The site's cel1 lab page holds the measured figures.

## 14. Rail networks

`docs/adr/0007-cel1-ipoib-pod-networks.md` decides how a pod on a DGX node gets the eight rails: Cilium stays primary on `mgmt0`, Multus adds one IPoIB interface per rail, and the NVIDIA Network Operator supplies the RDMA shared device plugin, nv-ipam and ipoib-cni. Phase 1 of that ADR is the offline part below, and section 14.1 is phase 2 on the simulation guests.

| Piece | Where | What it holds |
|---|---|---|
| Model | `kubernetes.rail_networks` and the `pod_cidr` of each rail in `sites/cel1.yaml` | Namespace `rdma-jobs`, the selector `rfl.example/node-class: dgx-b200`, 16 addresses per node per rail, `10.143.<r>.0/24` per rail |
| Values | `helm/rail-networks/values-cel1.yaml`, rendered by `make fleet-render` | The namespace, the resource name `cel1_rails`, the selector, the block size, and per rail its name `cel1-rail<r>`, its master `rail<r>` and its subnet |
| Chart | `helm/rail-networks/chart`, version 26.7.1, the locked Network Operator version | The namespace `rdma-jobs` at PodSecurity `privileged`, kept on uninstall. The `NicClusterPolicy` `nic-cluster-policy` with only `rdmaSharedDevicePlugin`, `nvIpam` and `secondaryNetwork.ipoib`, placed by the selector. One `IPoIBNetwork` and one `IPPool` per rail. `crds/` carries the operator's IPPool CRD |
| Operator | `helm/network-operator/values-cel1.yaml`, chart 26.7.1 | `nfd.enabled: false`, `nfd.deployNodeFeatureRules: false`, `upgradeCRDs: false`, `keepNCP: false` |
| Multus | `helm/multus/values-cel1.yaml`, the lab's chart | `multusAutoconfigDir: /etc/cni/multus/net.d`, Cilium's `cni.confPath` of section 5 |
| Lock | `versions.lock.yaml` | `addons.network_operator_chart`, the three component images by digest, the sha256 of the vendored CRD. Scope `fleet` holds them and the Multus components |

Each choice and its source follows.

- **The release order is cilium, multus, gpu-operator, network-operator, rail-networks.** Cilium's conflist sits in Multus's directory, so no pod that needs the pod network starts until Multus writes `00-multus.conf`. Multus therefore comes right after Cilium, before any release whose `--wait` needs such a pod. The lab order stays cilium, multus, whereabouts.
- **The chart carries the IPPool CRD.** The operator creates it only when it reconciles the nvIpam part of the policy (`manifests/state-nv-ipam-cni/005-nv-ipam.nvidia.com_ippools.yaml` at tag v26.7.1), so pools in the same release as the policy would find no CRD at install. Helm installs a CRD from `crds/` only when it is absent, skips it otherwise, and does not touch it on an upgrade (`pkg/action/install.go` and `upgrade.go` at Helm v4.3.2), so the operator's own copy of the same file takes over afterwards. A unit test checks the sha256 of the file against the lock.
- **The pools live in the operator's namespace.** The nv-ipam controller runs with `--ippools-namespace=$(POD_NAMESPACE)` (`040-nv-ipam-controller.yaml` at v26.7.1), and `rail-networks` installs into `nvidia-network-operator` with the operator.
- **Images by digest.** The operator turns a component `version` that starts with `sha256:` into `<repository>/<image>@<digest>` (`imagePath` in `pkg/render/render.go` at v26.7.1). The chart takes repository and tag with digest from the lock, as `make addons` sets them for every image, and refuses a tag without a digest.
- **`upgradeCRDs: false`.** The hook Job would apply every CRD of the operator image, the NetworkAttachmentDefinition CRD that the multus release owns among them. Helm's own `crds/` handling skips that one because it exists. Upgrades stay out of scope (ADR 0001 section 6).
- **Strict schemas from CRDs.** `scripts/addons/schemas.py` converts each served version of each CRD that the environment's releases render. An object that lists its properties allows no other key unless the CRD preserves unknown fields there, `nullable` becomes a null type, and `x-kubernetes-int-or-string` a string or integer type.

### 14.1 Phase 2 on the simulation

The scenario `cel1-rail-networks` (`tracks/cel1/scenarios/cel1-rail-networks.yaml`, `scripts/run/cel1_rails.py`) checks the rail networks on the simulation guests that the fleet targets built, and `tests/integration/qemu-lab/acceptance-cel1.sh` runs it after `cel1-sim-baseline`. Run `20261118T014617Z` passed all 10 checks in `.lab/acceptance-cel1/20261118T012729Z`, 52 steps from wiped guests with no unexpected exit code, the run of record below. In the same acceptance `cel1-sim-baseline` run `20261118T014243Z` passed with Multus and the Network Operator installed, a second `make fleet-addons` left all five releases unchanged at revision 1, and `make fleet-cni-migrate` changed nothing (`.lab/evidence/cel1-rails/acceptance/`). It takes every expectation from the rendered values and the site model. Two of its checks pass on a failure by design, because the rails are Ethernet NICs without RDMA devices: the device plugin exposes no device, and ipoib-cni refuses every rail master.

| Check | What the run of record showed |
|---|---|
| `cni-layout-on-every-node` | On 13 nodes `/etc/cni/net.d` holds `00-multus.conf` alone, with `/etc/cni/multus/net.d/05-cilium.conflist` as its delegate. `nv-ipam.d` is a directory and no CNI file, so containerd ignores it |
| `operator-runs-three-components-on-rail-hosts` | Policy `ready`. `rdma-shared-dp-ds`, `nv-ipam-node` and `kube-ipoib-cni-ds` 8 of 8, on the 8 DGX nodes only. The nv-ipam controller Available. No driver, Multus or CNI plugin DaemonSet |
| `pools-give-each-rail-host-a-block` | Each of the 8 pools gives each DGX node one block of 16, `.1`–`.16` up to `.113`–`.128` |
| `rail-attachments-name-ipoib-on-each-rail` | 8 NetworkAttachmentDefinitions in `rdma-jobs`, each `ipoib` on its `rail<r>` with nv-ipam from `cel1-rail<r>` |
| `rdma-resource-registered-without-a-device` | Each plugin loaded `cel1_rails` with `rdmaHcaMax` 16 and ifNames `rail0` to `rail7`, and exposes 0 devices. The 8 DGX kubelets register `rdma/cel1_rails` at 0, no other node registers it |
| `rdma-pod-waits-without-a-device` | A pod that asks for one unit, privileged false with IPC_LOCK, stays Pending with `Insufficient rdma/cel1_rails` |
| `ipoib-refuses-the-ethernet-rails` | A pod with the 8 rail networks never starts: `master device is (device) not of type ipoib` |
| `pod-at-boot-waits-for-multus` | After `lab.sh stop` and `start` of `cel1-dgx-06`, the pod created at boot+7.4 s waited with no network in all 88 samples until `00-multus.conf` at boot+53.3 s, and started with `eth0` from Cilium through Multus |

What the plan did not foresee follows. `docs/adr/0007-cel1-ipoib-pod-networks.md` section 5.1 has the sources.

- **The device plugin cannot see a software RDMA device.** It reads a NIC's RDMA devices from `/sys/bus/pci/devices/<address>/infiniband`. Soft-RoCE devices on kernel 6.8 sit under `/sys/devices/virtual/infiniband`. A probe with Soft-RoCE on e1000e rails exposed 0 devices too (`.lab/evidence/cel1-rails/probe/`), so the simulation keeps its virtio rails and no RDMA stand-in.
- **The plugin's DaemonSet needs NFD's Mellanox label.** The operator adds the node selector `feature.node.kubernetes.io/pci-15b3.present=true` and `network.nvidia.com/operator.mofed.wait=false`, and sets the wait label to `false` on every node with the PCI label when the policy has no `ofedDriver`. Without the label the DaemonSet scheduled 0 pods and the policy stayed `notReady`. On hardware the GPU Operator's NFD sets it for the ConnectX ports.
- **A forced Helm 4 apply needs `--force-conflicts`.** `make addons FORCE=1` failed on a field that `kubectl patch` had changed, until the forced apply passed the flag.
- **`make fleet-cni-migrate`** runs `ansible/playbooks/cni-migrate.yml`, which now refuses an inventory whose Helm environment installs no Multus instead of every inventory but the lab. On the cluster built from wiped guests it changed nothing.

Every check failed on a deliberate break of `tests/integration/qemu-lab/breaks-cel1-rails.sh` and passed after the repair.

| Break | Checks that failed | Repair | Broken and repaired run |
|---|---|---|---|
| Cilium's conflist copied into `/etc/cni/net.d` on `cel1-dgx-06` | `cni-layout-on-every-node`, `pod-at-boot-waits-for-multus` | `make fleet-cni-migrate`, which removed the file on that node only | `20261118T023102Z`, `20261118T023313Z` |
| The class label `rfl.example/node-class` removed from `cel1-dgx-08` | `operator-runs-three-components-on-rail-hosts`, `pools-give-each-rail-host-a-block`, `rdma-resource-registered-without-a-device` | `make fleet-k8s`, which sets the class labels of the inventory again | `20261118T020900Z`, `20261118T021123Z` |
| `kubectl patch` sets the master of `cel1-rail3` to `stor0` | `rail-attachments-name-ipoib-on-each-rail` | `make fleet-addons RELEASE=rail-networks FORCE=1` | `20261118T021306Z`, `20261118T021449Z` |
| A config whose RDMA pod asks for no unit of the resource | `rdma-pod-waits-without-a-device` | The tracked config | `20261118T021633Z`, `20261118T021817Z` |
| `/opt/cni/bin/ipoib` removed on `cel1-dgx-05` | `ipoib-refuses-the-ethernet-rails` | Delete the node's ipoib-cni pod, whose DaemonSet copies the binary again | `20261118T021957Z`, `20261118T022138Z` |
| A stray pod with the rail test label in `rdma-jobs` | `rail-test-pods-removed` | Delete the pod | `20261118T022323Z`, `20261118T022537Z` |
| A process that carries the run id and restarts on `cel1-dgx-04` | `no-process-left` | Stop its unit | `20261118T022722Z`, `20261118T022910Z` |

The series ran in `.lab/breaks-cel1-rails/20261118T020501Z` on the cluster of the acceptance run, booted again with `make lab-up`, and every step had its expected exit code. Its first broken run, `20261118T020503Z`, also failed `operator-runs-three-components-on-rail-hosts`, because the nv-ipam controller was not yet Available after that boot. On the settled cluster the same break failed only its two checks (`.lab/breaks-cel1-rails/20261118T023100Z`). Both series are in `.lab/evidence/cel1-rails/breaks/`.

## 15. NetBox

`docs/adr/0008-netbox-from-the-site-model.md` exports the site model to a NetBox that runs only inside the guest of the harness profile `netbox`. The model stays the source. The sync owns the objects with the tag `rfl-site-model` and leaves every other object alone.

| Target | Action | Host access |
|---|---|---|
| `make netbox-render` | Prints the 1142 NetBox objects of `sites/cel1.yaml` as JSON | None |
| `make netbox-guest` | Boots or reuses `netbox1` and runs `ansible/playbooks/netbox-guest.yml`: Docker and compose at the locked versions, the secrets made once in the guest, NetBox v4.7.5 with PostgreSQL and Valkey by digest | The NetBox guest only |
| `make netbox-sync` | Makes NetBox hold exactly the managed objects of the model, then compares again | NetBox on `127.0.0.1:22352` |
| `make netbox-diff` | The same comparison without writing. Exit 1 on a difference | NetBox on `127.0.0.1:22352` |
| `make netbox-test` | The scenario `cel1-netbox` | NetBox on `127.0.0.1:22352` |
| `make netbox-screenshots` | Screenshots of the model in NetBox for the learning site. `NODE_PATH` must reach `playwright-core`, and `CHROMIUM` may name a browser | NetBox on `127.0.0.1:22352` |

Every target that talks to NetBox reads the API token, or for the screenshots the superuser's password, from `/etc/rfl/netbox/secrets.json` in the guest over SSH and keeps it in memory. The guest's port 8080 reaches the host only on 127.0.0.1.

`tests/integration/qemu-lab/acceptance-netbox.sh` ran 16 steps from a wiped guest with no unexpected exit code in `.lab/acceptance-netbox/20261121T160813Z`. The second run of the playbook reported `changed=0`. Before the first sync `make netbox-diff` found 1142 differences, all creates. Run `20261121T161210Z` of `cel1-netbox` passed all 8 checks, and its first sync created the 1142 objects in less than 30 s. After the guest powered off and booted again, the containers came back on their own, the playbook changed nothing and the comparison found no difference (`.lab/evidence/netbox/acceptance/`).

Every check failed on a deliberate break of `tests/integration/qemu-lab/breaks-cel1-netbox.sh` and passed after the repair (`.lab/breaks-cel1-netbox/20261121T160012Z` and `20261121T160331Z`, copied to `.lab/evidence/netbox/`).

| Break | Checks that failed |
|---|---|
| The lock names NetBox v4.7.4 | `netbox-is-the-locked-version` |
| The sync skips the cables | `sync-makes-netbox-hold-the-model`, `netbox-equals-the-model`, `drift-is-found-exactly`, `sync-undoes-the-drift`, `run-objects-removed` |
| A second writer in the guest edits another address every half second | `netbox-equals-the-model`, `second-sync-changes-nothing`, `drift-is-found-exactly`, `sync-undoes-the-drift`, `run-objects-removed` |
| Prune off | `sync-undoes-the-drift`, `run-objects-removed` |
| The hand-made address carries the managed tag | `hand-made-objects-stay` |
| The run keeps its hand-made address | `run-objects-removed` |

The breaks ran on the guest of an earlier acceptance run of the same code, `.lab/acceptance-netbox/20261121T155427Z`, before one unused line, the superuser's e-mail address, left the playbook. Two development runs before the runner recorded its API calls broke the result contract, which requires a non-empty `commands.log` for a passed run. They were moved out of `results/` to `.lab/evidence/netbox/invalid-runs/`, and the runner now logs every API call by method, path and status, without the token or a body.
