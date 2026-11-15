# cel1 simulation: the fleet code on 13 guests

> Date: 2026-11-16  
> Verdict: **cel1-sim-baseline passed from wiped guests.** `make fleet-k8s`, `make fleet-addons` and `make fleet-rsyslog` with `CONFIRM=cel1` built the cel1 cluster on 13 QEMU guests, and run `20261115T164615Z` passed all 20 checks. A second `make fleet-k8s` before the GPU Operator and a converged run after it reported `changed=0` on every host, and a second `make fleet-addons` left both releases at revision 1. With the power of the VIP holder cut, the API answered on the VIP again after 7.534 seconds. One interaction is open: the GPU Operator removes the DGX opt-out labels from guests without GPUs each time it starts (section 7).  
> Environment: QEMU harness profile `cel1`, generated from `sites/cel1.yaml`. Ubuntu 24.04.5, kernel 6.8.0-147-generic in every guest. Kubernetes v1.36.6, containerd 2.4.2, runc 1.5.3, Cilium chart 1.20.4, kube-vip v1.3.1, GPU Operator chart v26.7.2. The control node has ansible-core 2.21.6, Python 3.12 and helm v4.3.2. The host has kernel 7.0.0-43-generic, an Intel Core Ultra 9 285K with 24 threads and 125 GiB of memory.

## 1. What ran

| Order | What | Result | Evidence |
|---|---|---|---|
| 1 | The site model gained `kubernetes.admin_kubeconfig` and a per-class `container_runtime`. The renderer writes `k8s_admin_kubeconfig` and `k8s_container_runtime_default`, and the contract checker rejects a runtime other than `runc` or `nvidia` | `crun` on `dgx_b200` gives 8 `bad-value` violations | `evidence/cel1-sim/contract-runtime-check.log` |
| 2 | `make fleet-sim-profile` writes `tests/integration/qemu-lab/profiles/cel1.tsv` from the model | Rerunning both generators gives the same bytes. An edited profile and an edited `host_vars` file each fail their check | `evidence/cel1-sim/reproduce-and-break.log` |
| 3 | The gate of the fleet targets | 10 refusals, each exit 2, none with a connect, sendto or sendmsg call to an IPv4 or IPv6 address, in the acceptance run and again on the final code | `evidence/cel1-sim/acceptance/strace-summary.txt`, `evidence/cel1-sim/refusals-final.log` |
| 4 | The simulation stand-ins | A stand-in `/usr/bin/nvidia-container-runtime` on the 8 DGX guests, a stand-in syslog server on `10.140.0.5` | `evidence/cel1-sim/acceptance/16-stand-ins.log` |
| 5 | Development run on fresh guests | `fleet-k8s` and `fleet-addons` passed at once. `fleet-rsyslog` failed on all 13 hosts and passed after the fix of section 7. Run `20261115T161414Z` failed 3 checks, `20261115T162041Z` passed after the runner fixes | `evidence/cel1-sim/fleet-*.log` |
| 6 | `tests/integration/qemu-lab/acceptance-cel1.sh` from wiped guests | 46 steps, 0 unexpected exit codes | `evidence/cel1-sim/acceptance/steps.tsv` |
| 7 | The M2 and ha acceptances after the harness and role changes | Section 9 | Section 9 |

## 2. Host and guests

The host was measured before the first boot, after the boot and at the end of the acceptance run.

| When | Memory available | Load (1 min) | Free on `/cache`, `/`, `/data` | `/data/rfl-lab/cel1` |
|---|---:|---:|---|---:|
| Before any guest | 91.1 GiB | 1.72 | 12.3 GB, 17.6 GB, 737.5 GB | absent |
| 14 guests booted | 79.5 GiB | 5.73 | 12.3 GB, 19.3 GB, 736.8 GB | 0.72 GB |
| End of the acceptance run | 55.6 GiB | 7.21 | 12.3 GB, 19.2 GB, 696.6 GB | 41.0 GB |

Sources are `evidence/cel1-sim/host-before-boot.txt` and steps 03, 07 and 45 of the acceptance run. The host had other work running the whole time, with CPU pressure near 50 percent before any guest booted. A guard checked the three file systems every 30 seconds and would have paused every guest below 3 GiB free. It never fired (section 11). Every guest disk lives under `/data/rfl-lab/cel1` through `LAB_DIR`. The base cloud image stays at `.lab/images/` on `/cache`, and each guest disk is a qcow2 overlay on it.

| Guests | vCPU | Memory | Virtual disk |
|---|---:|---:|---:|
| `cel1-cp-01`–`03` | 2 | 3072 MiB | 20 GiB |
| `cel1-dgx-01`–`08` | 2 | 2560 MiB | 20 GiB |
| `cel1-pcie-01`–`02` | 2 | 2560 MiB | 20 GiB |
| `cel1-sim-syslog` | 1 | 768 MiB | 10 GiB |
| Total, 14 guests | 27 | 34.75 GiB | 270 GiB |

kubeadm needs 2 CPUs on a control plane, and the ha profile ran its control planes at 2.5 GiB. The control planes here hold 13 kubelets, so they got 3 GiB. The workers run kubelet, containerd, a Cilium agent, the node feature discovery worker and the test pods. 27 vCPUs on 24 threads is an overcommit of 1.1, which idle guests tolerate. Each guest used about 2.9 GB of disk at the end. `SIZES` in `scripts/fleet/sim.py` holds the sizes, and `make fleet-sim-profile` writes them into the profile.

## 3. What changed

| Path | Change |
|---|---|
| `sites/cel1.yaml` | `kubernetes.admin_kubeconfig` is the path of ADR 0001 section 5 written as the ADR writes it. `dgx_b200` sets `container_runtime: nvidia`. The `fabrics` section is unchanged, and `make track-b-render-check` passes |
| `scripts/fleet/site.py`, `render.py` | Parse and render the two new keys |
| `scripts/fleet/sim.py` | The profile generator. One guest per host, one NIC per class interface under its name, one switched network per site network, a syslog stand-in guest at `services.syslog` |
| `scripts/fleet/gate.py`, `__main__.py` | `fleet sim-profile` and `fleet sim-gate` |
| `scripts/inventory/contract.py` | `k8s_container_runtime_default` (`runc` or `nvidia`, default `runc`) and `k8s_admin_kubeconfig` |
| `ansible/inventories/cel1/` | Rendered again. Each class file carries its runtime, `all.yml` the kubeconfig path |
| `ansible/roles/kubeadm/vars/main.yml` | Replaces the state home prefix `${XDG_STATE_HOME:-~/.local/state}` of `k8s_admin_kubeconfig` with the state home of the control node |
| `ansible/playbooks/fleet/rsyslog-client.yml` | `become: true` |
| `ansible/playbooks/simulation-stand-ins.yml` | The two stand-ins. It refuses any host that is not on 127.0.0.1 |
| `mk/fleet.mk` | `fleet-sim-profile`, `fleet-sim-profile-check`, `fleet-sim-stand-ins`, `fleet-sim-test`, the gate on every target that changes cel1. `fleet-check` includes the profile check. The old `CONNECT_GATE` is gone, because the gate covers it |
| `tests/integration/qemu-lab/guests.py` | `addr=<ipv4>` on a NIC, `#group <group> <guest>...` for guests outside the profile inventory, and `rfl_kubeconfig_server` in the override inventory of a profile that forwards the API |
| `tests/integration/qemu-lab/lab.sh` | The status column fits 15-character names |
| `tests/integration/qemu-lab/profiles/cel1.tsv` | Generated. `#qemu -no-reboot` |
| `tests/integration/qemu-lab/profiles/ha/inventory/` | `k8s_container_runtime_default: runc`, so the ha inventory still has every variable explicit |
| `tests/integration/qemu-lab/acceptance-cel1.sh` | The acceptance run of section 5 |
| `scripts/run/cel1.py`, `scripts/run/ha.py` | The `cel1-sim-baseline` runner. `ha.Run` takes the spec and keeps the inventory listing, so the runner reuses the VIP checks |
| `tracks/cel1/` | The scenario and its parameters |
| `tests/unit/test_cel1_*.py`, `tests/fixtures/cel1/` | Tests of the generator, the gate and the runner parsers, with output captured from the guests |
| `tests/unit/test_fleet_*.py`, `test_inventory_contract.py`, `test_lab_harness.py`, `tests/fixtures/fleet/` | The new keys, variables and profile features. The cel1 inventory fixture was captured again |
| `docs/fleet.md` | The simulation, the gate, the stand-ins and the findings |

## 4. Simulation differences from the site model

| Site model | Simulation |
|---|---|
| Bare-metal hosts. DGX OS 7 on the DGX nodes | KVM guests. Ubuntu 24.04.5 on every class |
| 8 B200 GPUs per DGX node, GPUs in the PCIe servers | No GPU, no NVIDIA driver, no NVIDIA PCI device |
| The NVIDIA container toolkit of DGX OS | `/usr/bin/nvidia-container-runtime` is a stand-in that logs each call to `/var/log/rfl-nvidia-container-runtime.log` and execs `/usr/local/sbin/runc`. `make fleet-sim-stand-ins` installs it on the hosts whose `k8s_container_runtime_default` is `nvidia`. The inventory and the roles are unchanged |
| `rail0`–`rail7` are InfiniBand ports on ConnectX HCAs `mlx5_0`–`mlx5_7` | virtio NICs on one unprivileged Ethernet switch per rail. No RDMA device. The rails carry Ethernet frames |
| One L2 segment per network | `switch.py`, one per network, 11 in total, AF_UNIX datagrams only. 188 `u_dgr` sockets, every QEMU and switch process AppArmor `unconfined` with `CapEff: 0000000000000000` (step 06) |
| Interfaces renamed by `.link` files from provisioning | cloud-init netplan matches each MAC, renames the NIC and sets its model address at first boot |
| Each host has only its model interfaces | Each guest also has a QEMU user NIC `enp0s3` (`10.0.2.15`) with the default route, for SSH and package downloads. The node IP sits on `mgmt0`. This is the condition of the M2 Cilium API defect |
| The central syslog server is outside the model, known only by `10.140.0.5:514` | The guest `cel1-sim-syslog` holds `10.140.0.5` on the management network and receives TCP syslog with rsyslog `imtcp`, one file per sending host. It is in the override group `simulation_syslog`, not in the site inventory |
| The control node reaches the API on the VIP | Port 6443 of `cel1-cp-01` is forwarded to `127.0.0.1:22340`. The kubeconfig names that forward with `tls-server-name: kubernetes` |
| Power failure | `lab.sh kill` sends SIGKILL to QEMU |
| A panic leaves a crash dump or a reboot | `-no-reboot` ends the QEMU process and keeps the console in `serial.log` |

## 5. Commands and results

The acceptance run is `.lab/acceptance-cel1/20261115T163759Z/`, started with `LAB_DIR=/data/rfl-lab/cel1 tests/integration/qemu-lab/acceptance-cel1.sh`. `evidence/cel1-sim/acceptance/` holds its step table and the logs. For the long Ansible and Helm steps it keeps the lines that carry results: the PLAY RECAP, the release lines and the exit lines. When `make` fails, the exit code of make itself is 2.

| Steps | Command | Exit code | Result |
|---|---|---:|---|
| 01–05 | `make lab-down`, `make lab-wipe`, host, `make fleet-sim-profile-check`, `make lab-up` | 0 | 14 guests booted from wiped disks and finished cloud-init. The development boot took 32 seconds (`evidence/cel1-sim/lab-up-1.log`) |
| 06–07 | Socket, AppArmor and capability check, host | 0 | Section 4 |
| 09–11 | `make fleet-k8s`, then with `CONFIRM=cel1`, then with a connect inventory naming invented addresses | 0 | Each target exited 2 under strace with 0 IPv4 or IPv6 calls. Every traced call was `AF_NETLINK` |
| 12–15 | The same for `fleet-rsyslog`, and `fleet-sim-stand-ins` without `FLEET_CONNECT` | 0 | Same |
| 16 | `make fleet-sim-stand-ins CONFIRM=cel1 FLEET_CONNECT=...` | 0 | `changed=1` on the 8 DGX guests, `changed=3` on the stand-in |
| 17–18 | `make fleet-k8s CONFIRM=cel1 FLEET_CONNECT=...`, `stat` | 0 | 13 hosts, 0 failures. The kubeconfig is mode `600` |
| 19 | Opt-out labels before the Operator | 0 | 8 of 8 DGX Nodes carry both |
| 20–21 | `make fleet-k8s` again, recap check | 0 | `13 hosts, 0 with changes` |
| 22–24 | `make fleet-addons` without CONFIRM, without `FLEET_CONNECT`, and with a kubeconfig naming the VIP | 0 | Each exited 2 with 0 IPv4 or IPv6 calls |
| 25 | `make addons-template ENV=cel1` | 0 | 25 Cilium resources valid. 27 GPU Operator resources valid, 9 skipped |
| 26 | `make fleet-addons CONFIRM=cel1 FLEET_CONNECT=...` | 0 | `cilium` and `gpu-operator` upgraded to revision 1 |
| 27 | `make fleet-rsyslog CONFIRM=cel1 FLEET_CONNECT=...` | 0 | 13 hosts, 0 failures |
| 28 | Opt-out labels after the Operator | 0 | No DGX Node carries them. Section 7 |
| 29 | `make fleet-sim-test FLEET_CONNECT=...` | 0 | Run `20261115T164615Z` passed |
| 30–31 | `make fleet-k8s` after the Operator, check of the changed tasks | 0 | Only `Set the node labels the inventory names` changed, on the 8 DGX hosts |
| 32–33 | `make fleet-k8s` again, recap check | 0 | `13 hosts, 0 with changes` |
| 34–36 | `make fleet-addons` again, release check, `helm history` | 0 | `cilium: unchanged`, `gpu-operator: unchanged`, revisions `[1]` and `[1]` |
| 37–38 | `make fleet-rsyslog` again, recap check | 0 | `13 hosts, 0 with changes` |
| 39–40 | Stop rsyslog on `cel1-dgx-06`, `make fleet-sim-test` | 0, 2 | Run `20261115T165039Z` failed. Only `syslog-to-central-address` failed: no marker from `cel1-dgx-06` and no connection to `10.140.0.5:514` |
| 41–42 | `make fleet-rsyslog` as the repair, `make fleet-sim-test` | 0, 0 | The repair changed only `Ensure rsyslog runs` on `cel1-dgx-06`. Run `20261115T165516Z` passed |
| 43–44 | `make inventory-check INVENTORY=cel1`, `make validate` | 0 | 0 violations over 13 hosts. Every run directory validated |
| 45–46 | Host, `make lab-down` | 0 | Section 2. Every guest stopped |

After the acceptance run, a review found that the targets had `venv` as a prerequisite, so a stale venv would have been rebuilt before the `CONFIRM=cel1` check. `mk/fleet.mk` now checks `CONFIRM` first and builds the venv only inside the simulation gate, and the refusal message names any reach outside 127.0.0.1. The ten refusals ran again on that code with the same result, 10 exits of 2 and no IPv4 or IPv6 call (`evidence/cel1-sim/refusals-final.log`). The gates of section 9 ran after this change.

The development runs before the acceptance are in `evidence/cel1-sim/`: `lab-up-1.log`, `stand-ins-1.log`, `fleet-k8s-1.log` (exit 0 in 3 min 30 s), `fleet-addons-1.log` (exit 0 in 2 min 16 s), `fleet-rsyslog-1-no-become.log` (exit 2), `fleet-rsyslog-2.log` (exit 0), `fleet-sim-test-1.log` (run `20261115T161414Z`, failed), `fleet-sim-test-2.log` (run `20261115T162041Z`, passed), `fleet-k8s-2-after-operator.log` and `fleet-k8s-3.log`.

## 6. cel1-sim-baseline

`tracks/cel1/scenarios/cel1-sim-baseline.yaml` declares 20 checks, and `scripts/run/cel1.py` records them through rfl. These are the observations of run `20261115T164615Z`.

- `interfaces-match-site-model`. 93 interfaces of 13 hosts carry their model names and addresses.
- `nodes-ready-with-class-labels`. 13 Nodes Ready on their node IPs. Every kubelet registers exactly the labels of its class, 3 control-plane, 8 dgx-b200 and 2 pcie-gpu. The live `nvidia.com/gpu.deploy` labels were none (section 7).
- `control-planes-joined`, `api-on-vip`. Three control planes Ready with kube-apiserver and etcd, endpoint `10.140.0.10:6443`. `/livez` on the VIP from `cel1-pcie-01` and `/readyz` from each control plane were ok.
- `no-kube-proxy`. No kube-proxy object, 0 KUBE chains on 13 nodes, and every agent reports `KubeProxyReplacement: True [mgmt0 ...]`.
- `cilium-api-direct`. The agent DaemonSet sets `KUBERNETES_SERVICE_HOST=10.140.0.10` and `KUBERNETES_SERVICE_PORT=6443`. The agent on `cel1-dgx-03` was deleted. Its replacement became Ready with 0 container restarts, and its log connects to `https://10.140.0.10:6443` and never names `10.170.0.1:443`. The guests have the default route on another NIC than the node IP, the condition of the M2 defect. So `values-cel1.yaml` avoids that defect, confirmed on a restarted agent.
- `pod-to-pod-native-routing`. The client on `cel1-dgx-02` fetched from the server pod `10.160.3.204` on `cel1-dgx-01`. The route is `10.160.3.204 via 10.140.0.21 dev mgmt0`. Neither node has a VXLAN or Geneve link. Each `mgmt0` capture held 20 frames between the two pod IPs and 0 VXLAN and 0 Geneve frames.
- `no-pod-traffic-on-storage-or-rail-nics`. 20 captures on `stor0`, `stor1` and `rail0`–`rail7` of both nodes. All 20 held marker pings, and none held a pod, Service, VXLAN or Geneve frame.
- `cluster-dns`, `clusterip-service`. The Service name resolved to its ClusterIP and `kubernetes.default` to `10.170.0.1`. The fetch through the Service name returned `server`.
- `dgx-containerd-defaults-to-nvidia`. 8 nodes default to `nvidia` with `/usr/bin/nvidia-container-runtime` and 5 to `runc`. `crictl inspect` of the server container on `cel1-dgx-01` names `/usr/bin/nvidia-container-runtime`, and the stand-in log holds its `create` and `start`.
- `gpu-operator-installed`. The Deployments `gpu-operator`, `gpu-operator-node-feature-discovery-gc` and `-master` are Available, and the 8 DGX kubelets register both opt-outs. Found: ClusterPolicy state `ready` with reason `NoGPUNodes`, "No GPU node found, watching for new nodes to join the cluster". No node has `nvidia.com/gpu.present=true` or an NVIDIA PCI device label. The only DaemonSet is the NFD worker, 13 of 13 ready. The Operator log says "No GPU node in the cluster, do not create DaemonSets" for the driver, toolkit, validator, device plugin, MPS control daemon, DCGM exporter, GPU feature discovery and MIG manager (`evidence/cel1-sim/label-watch.log`).
- `syslog-to-central-address`. Markers of 13 nodes arrived at `cel1-sim-syslog` on `10.140.0.5:514`, and each node holds a TCP connection to it from its node IP.
- `vip-holder-identified`, `api-survives-holder-loss`, `cluster-works-after-loss`, `holder-rejoins`. The lease holder `cel1-cp-01` lost power. 5 of 10 probes failed, the API answered on the VIP again after 7.534 seconds, and the lease moved to `cel1-cp-02`. etcd kept 2 of 3 members, a ConfigMap was written, read and deleted, and `cel1-cp-01` returned Ready with 3 of 3 members healthy.
- `test-namespace-removed`, `no-process-left`. Nothing of the run remained.

| Run | Status | VIP gap | Holder lost, new holder |
|---|---|---:|---|
| `20261115T161414Z` (development) | failed | 9.042 s | `cel1-cp-01`, `cel1-cp-03` |
| `20261115T162041Z` (development) | passed | 7.535 s | `cel1-cp-03`, `cel1-cp-01` |
| `20261115T164615Z` | passed | 7.534 s | `cel1-cp-01`, `cel1-cp-02` |
| `20261115T165039Z` (rsyslog stopped) | failed | 6.032 s | `cel1-cp-02`, `cel1-cp-03` |
| `20261115T165516Z` | passed | 6.029 s | `cel1-cp-03`, `cel1-cp-01` |

The gap runs from the first failed probe to the first good one at 0.5-second intervals, so it can read short by one interval. M2 measured 6.0 to 9.0 seconds on the ha profile. These are measurements of these guests on a busy host, with no pass threshold. `evidence/cel1-sim/<run>/` holds the manifest, metrics and summary of the three acceptance runs.

## 7. Findings

**The rsyslog play had no privilege.** `ansible/playbooks/fleet/rsyslog-client.yml` installs a package and writes `/etc/rsyslog.d/` but ran as the SSH user. The first run failed on all 13 hosts at `apt-mark manual rsyslog` (`evidence/cel1-sim/fleet-rsyslog-1-no-become.log`). The play now has `become: true`, like every play of `kubernetes.yml`.

**The GPU Operator removes the DGX opt-out labels from guests without GPUs.** The kubelet of every DGX guest registers `nvidia.com/gpu.deploy.driver=false` and `nvidia.com/gpu.deploy.container-toolkit=false` (`/var/lib/kubelet/kubeadm-flags.env`), and all 8 Nodes carried them before the Operator ran (step 19). After `make fleet-addons` none carried them (step 28). In github.com/NVIDIA/gpu-operator tag v26.7.2, `updateGPUStateLabels` in `controllers/nodelabeling_controller.go` calls `removeAllGPUStateLabels` on every node without `nvidia.com/gpu.present`. The Operator logged one `Reconciling node labels` at its start and none in the 12 minutes after. The next `make fleet-k8s` put the labels back on the 8 DGX hosts and changed nothing else (steps 30–31), and the labels then stayed for 12 minutes (`evidence/cel1-sim/label-watch.log`). The Operator container restarted once during the rsyslog break run (`RESTARTS 1` in run `20261115T165516Z`), and the labels were gone again in the next run. Inferred cause of that restart: the API was unreachable on the VIP during the power cut. On a DGX node with GPUs the Operator sets `nvidia.com/gpu.present=true` and keeps the opt-outs. So the simulation cannot show the opt-outs surviving, and the checks read them from the kubelet registration and record the live labels as found. The fleet inherits one hazard: a DGX node whose GPUs vanish from node feature discovery loses its opt-outs at the next Operator start, and `make fleet-k8s` and the Operator then write the same keys.

**iproute2 prints an empty object per link of another type.** `ip -d -j link show type vxlan` on a node without VXLAN prints `[{},{},...]`, one `{}` per link. The first version of the native routing check read that as tunnel links and failed run `20261115T161414Z`. `tunnel_links` now reads `ifname` (`tests/fixtures/cel1/ip-link-type-vxlan-dgx-02.json`).

**Cilium reaches the API without a ClusterIP.** `values-cel1.yaml` sets `k8sServiceHost` to the VIP with kube-proxy replacement. On guests whose default route is on another NIC than the node IP, a restarted agent became Ready with 0 restarts and connected to the VIP (section 6). The ClusterIP path of the M2 defect is not used.

**The fleet targets reach only simulation guests.** Without `CONFIRM=cel1`, without the simulation inventory, or with any host, kubeconfig server or `rfl_kubeconfig_server` that is not on 127.0.0.1, each target exits 2 before ssh or helm. strace saw no IPv4 or IPv6 call in any refusal.

## 8. Verdict per acceptance criterion

| Criterion | Verdict | Evidence |
|---|---|---|
| 1. The profile generator and the inventory reproduce with no diff, and the contract checker rejects a bad runtime value | VERIFIED | `evidence/cel1-sim/reproduce-and-break.log`, `evidence/cel1-sim/contract-runtime-check.log`, `tests/unit/test_inventory_contract.py`, acceptance step 04 |
| 2. The fleet targets refuse without the simulation override and without `CONFIRM=cel1`, exit 2, and contact nothing | VERIFIED | Acceptance steps 09–15 and 22–24, `evidence/cel1-sim/acceptance/strace-summary.txt`, and on the final code `evidence/cel1-sim/refusals-final.log` |
| 3. From wiped guests the three fleet targets succeed and `cel1-sim-baseline` is passed and validated | VERIFIED | Steps 01–29, run `20261115T164615Z`, step 44 |
| 4. The second run of `fleet-k8s` and `fleet-addons` changes nothing | VERIFIED, with the label interaction of section 7 | Steps 20–21 and 32–38. The first `fleet-k8s` after the Operator started changed only the DGX labels the Operator had removed (steps 30–31) |
| 5. The VIP fault check passes | VERIFIED | `api-survives-holder-loss` of runs `20261115T164615Z` and `20261115T165516Z` |
| 6. A deliberate break makes the scenario fail honestly, and it passes after the repair | VERIFIED | Steps 39–42, runs `20261115T165039Z` (only `syslog-to-central-address` failed) and `20261115T165516Z` |
| 7. The M2 and ha acceptances pass after the changes | VERIFIED | Section 9, `evidence/cel1-sim/regression/` |
| 8. `make lint`, `make test`, `make validate`, `make fleet-check` exit 0 | VERIFIED | Section 9, `evidence/cel1-sim/gates/exit-codes.txt` |

## 9. Regression and gates

The M2 and ha acceptances ran again after every change to the harness, the roles and the playbooks, at the same time and after the cel1 guests had stopped.

| Test | Log | Result | Runs |
|---|---|---|---|
| `tests/integration/qemu-lab/acceptance-m2.sh` | `.lab/acceptance-m2/20261115T170024Z/` | 29 steps, 0 unexpected exit codes | `k8s-baseline` `20261115T170407Z` passed, `vm-rxe-baseline` `20261115T170513Z` passed, agent deleted `20261115T170538Z` failed, agents back `20261115T170625Z` passed, VXLAN dropped `20261115T170707Z` failed, VXLAN restored `20261115T170809Z` passed |
| `tests/integration/qemu-lab/acceptance-ha.sh` | `.lab/acceptance-ha/20261115T170044Z/` | 11 steps, 0 unexpected exit codes | `k8s-ha-vip` `20261115T170536Z` passed with a gap of 9.042 seconds |

The second `make k8s` reported `3 hosts, 0 with changes` on the lab profile and `4 hosts, 0 with changes` on the ha profile, and Cilium stayed at revision 1. The failed runs are the deliberate breaks of M2, and each failed as M2 recorded. The step tables are in `evidence/cel1-sim/regression/`.

| Gate | Exit code | Result |
|---|---:|---|
| `make lint` | 0 | yamllint, ansible-lint production profile with 0 failures, shellcheck, 11 scenario specs valid, no reference to the local design draft |
| `make test` | 0 | 409 passed |
| `make validate` | 0 | 123 run directories, 0 invalid |
| `make fleet-check` | 0 | Site model valid, 0 inventory differences, the profile equals its rendering, contract 0 violations over 13 hosts, 0 unknown values keys, addons-template valid |
| `make track-b-render-check` | 0 | `cel1-compute.net` matches `fabrics.compute` |

The logs are in `evidence/cel1-sim/gates/`.

## Result kind after this run

The runs above recorded `result_kind: software-rdma-measurement`, the kind the Track A scenarios used until then. After this milestone the coordinator added `software-cluster-test` for scenarios that check a cluster's behaviour rather than measure RDMA, and moved `k8s-baseline`, `k8s-ha-vip` and `cel1-sim-baseline` to it. The runners now copy the kind of the scenario spec into `metrics.json`. The recorded runs keep the kind they were run with. No guest run has exercised the new kind yet.

## 10. Not run

- Any GPU workload, the NVIDIA driver, the real container toolkit, NCCL and GPUDirect RDMA. The guests have no GPU.
- The GPU Operator operands. With no GPU node the Operator creates none of the 8 operand DaemonSets. Whether the toolkit leaves the top-level containerd configuration of a PCIe GPU server unchanged needs a server with a GPU.
- InfiniBand on the rails, the ConnectX HCAs, OpenSM and RDMA traffic. The rails carry Ethernet. Track B simulates the compute fabric's management plane on its own guest.
- DGX OS 7. Every guest runs Ubuntu 24.04.5.
- Storage traffic on `stor0` and `stor1`. Only their addresses and the absence of pod traffic were checked.
- NetworkPolicy on cel1. The scenario does not declare it. The lab `k8s-baseline` covers it.
- The rsyslog disk-assisted queue during an outage of the syslog server.

## 11. Cleanup

Every harness guest was stopped, and no `qemu-system-x86_64` process of the harness remained (`evidence/cel1-sim/guests-stopped.log`). `LAB_PROFILE=cel1 LAB_DIR=/data/rfl-lab/cel1 make lab-wipe` then deleted the 14 cel1 guest directories. `realpath` resolved the target to `/data/rfl-lab/cel1` before the wipe. The directory held 41,056,558,518 bytes before and 0 after, and the free space of `/data` rose from 696,346,984,448 to 737,411,629,056 bytes, 41.06 GB freed. 11 empty `switch.log` files remain under `/data/rfl-lab/cel1/net/`. The base cloud image stays at `.lab/images/` (`evidence/cel1-sim/wipe.log`). The lab and ha profile guests were stopped with their disks kept, as the M2 and ha acceptances leave them. The admin kubeconfig of the wiped cluster stays at `~/.local/state/rdma-fabric-lab/cel1/kubeconfig`, and the next `make fleet-k8s` overwrites it.

The free-space guard took 132 samples between 01:04 and 02:10. The lowest free space was 12.26 GB on `/cache`, 19.17 GB on `/` and 695.72 GB on `/data`, and it never paused a guest (`evidence/cel1-sim/space-guard.txt`).
