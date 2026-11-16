# rdma-fabric-lab

A software-only lab for RDMA, InfiniBand management and RoCE congestion control on one Ubuntu host, with no HCA, InfiniBand switch or GPU. The same Ansible and kubeadm code that builds the lab cluster also describes `cel1`, an invented GPU region with DGX B200 nodes, so every lab run exercises that code.

This file says what exists today and how to run it. Design decisions live in `docs/adr/`.

## Tracks

| Track | Question | Stack | Runs in |
|---|---|---|---|
| A. Kubernetes and Soft-RoCE | How do RDMA programs run over plain Ethernet, and how do Kubernetes workloads reach them? | QEMU guests, Ansible, kubeadm, containerd, Cilium by Helm, RXE | QEMU guests |
| B. InfiniBand management | How does a subnet manager discover a fabric, assign LIDs and react to link faults? | ibsim, OpenSM, infiniband-diags | A container inside a dedicated QEMU guest |
| C. RoCE congestion control | How do PFC, ECN and DCQCN settings change queues, rates and flow completion? | ns-3 RDMA model | A container inside a dedicated QEMU guest |

The tracks share the repository, the result format and the version lock, not a data path. A result of one track never stands in for another.

## What this lab does not claim

- IPoIB. RXE runs over Ethernet and ibsim simulates management datagrams. Neither creates the IPoIB data path, so nothing here validates IPoIB or an IPoIB CNI.
- HCA performance. RXE numbers are software results of one host, kernel and guest combination. They say nothing about offload, InfiniBand or RoCE hardware latency, or 200, 400 or 800 Gb/s throughput.
- Lossless switching. PFC, ECN and DCQCN results in Track C are the behaviour of the simulation model, not of a real switch or of the hypervisor's virtual switch.
- GPU and NCCL performance. GPUDirect RDMA, NVLink and NCCL are out of scope. A collective pattern, if added, is a synthetic workload or a model result.

## Status

| Milestone | State | Record |
|---|---|---|
| M0 design lock | done | `docs/milestones/M0.md` |
| M1 RXE between two worker VMs | done. rping, RDMA Read, Write and Send pass over RDMA CM, RoCEv2 frames appear on the lab NIC and none on the management NIC, and the device returns after a reboot | `docs/milestones/M1.md` |
| M2 Kubernetes | done. Kubernetes v1.36.6 with Cilium 1.20.4 installed by Helm. Three control planes behind a kube-vip address answered again 6.0 s after the address holder lost power | `docs/milestones/M2.md` |
| cel1 region, offline | done. Site model, generated inventory, contract check and rendered charts pass | `docs/fleet.md` |
| M4 ibsim and OpenSM | done. B-01 to B-04 pass inside the Track B guest: discovery, unique LIDs, port states, a link fault and its recovery, and rerouting on the cel1 rail fabric. Each scenario fails on its deliberate break | `docs/milestones/M4.md` |
| cel1 region, simulated as 13 guests | done. `make fleet-k8s`, `fleet-addons` and `fleet-rsyslog` built the cluster on 13 guests from wiped disks. All 13 nodes Ready with their class labels, kube-proxy replacement with native routing, the nvidia runtime default on the DGX guests, and the API back on the VIP after its holder lost power | `docs/milestones/cel1-sim.md` |
| M3 hostNetwork RDMA pods and Multus | done. Pods on the two workers repeat the M1 baseline over the node's RXE device, and Multus adds `net1` from Whereabouts beside Cilium through pod recreation and a worker reboot. The pods need `privileged` | `docs/milestones/M3.md` |
| M5 and M6 ns-3, M7 wrap-up | not started | |

Nothing in this repository has run against GPU hardware.

## Requirements

- Ubuntu 24.04 host with KVM, and `/dev/kvm` usable by your user. No sudo is needed for the lab.
- `qemu-system-x86_64`, `qemu-img`, `cloud-localds`, `ssh-keygen`, `uv`, `make`, `git`, `curl`.
- Free memory and cores for the guests of the profile you boot. The default `lab` profile:

  | Guest | vCPU | Memory | Disk |
  |---|---:|---:|---:|
  | cp1 | 2 | 4 GiB | 30 GiB |
  | worker1 | 4 | 6 GiB | 40 GiB |
  | worker2 | 4 | 6 GiB | 40 GiB |

## Quick start

```bash
make venv              # pinned Python tooling in .venv
make lint test         # static checks and unit tests
make doctor            # read-only host check, including address plan overlaps
make tools             # pinned helm and kubeconform in .tools/bin
```

Track A from nothing, the same sequence `tests/integration/qemu-lab/acceptance-m2.sh` runs:

```bash
make lab-wipe lab-up                 # boot cp1, worker1 and worker2
make bootstrap rxe-up                # packages, lab NIC address, rxe0
make rxe-test                        # RDMA between the workers, recorded under results/
make k8s                             # kubeadm cluster, kubeconfig in .lab/kubeconfig
make addons ENV=lab KUBECONFIG_FILE=.lab/kubeconfig   # Cilium, Multus and Whereabouts by Helm
make k8s-test                        # nodes, pod traffic, DNS, Service, NetworkPolicy
make validate report                 # check and tally every run directory
make lab-down
```

M3 runs on the same cluster. The RDMA test image is built inside the Track B guest, never on the host, and loaded into the workers' containerd without a registry:

```bash
make rdma-tools-image               # build in the Track B guest, fails unless the digest is lab.rdma_tools_image
make lab-down LAB_PROFILE=ibsim     # free the builder before measuring
make rdma-tools-load                # import the image on both workers
make k8s-hostnet-test               # the M1 RDMA tests between hostNetwork pods
make k8s-multus-test                # net1 from Whereabouts beside Cilium, recreation, worker reboot
```

Cilium writes its CNI file to `/etc/cni/multus/net.d`, so `/etc/cni/net.d` holds only Multus's file and a pod never starts without `net1`. A cluster built before that change needs `make cni-migrate` once after `make addons ENV=lab`. `tests/integration/qemu-lab/acceptance-m3.sh` runs all of M1, M2 and M3 from wiped guests. The three-control-plane profile runs with `LAB_PROFILE=ha`.

Track B boots its own guest and runs everything inside it:

```bash
make track-b                         # B-01 to B-04 in the Track B guest
make track-b-breaks                  # every scenario must fail on its deliberate break
make lab-down LAB_PROFILE=ibsim
```

The cel1 simulation boots 13 guests and needs about 40 GB of disk. Put the disks where there is room:

```bash
LAB_DIR=/path/with/room bash tests/integration/qemu-lab/acceptance-cel1.sh
```

`make help` lists every target.

## The cel1 region

`sites/cel1.yaml` is the single source of the region: 3 control planes, 8 DGX B200 nodes and 2 PCIe GPU servers, the address plan and a rail-optimized InfiniBand fabric. `make fleet-render` writes `ansible/inventories/cel1/` from it. `make fleet-check` validates the model, the generated inventory, the inventory contract and the Helm values offline.

Every name and address in `cel1` is invented. The targets that would change cel1 hosts refuse to run without `CONFIRM=cel1` and a connection inventory. To apply the code to another site, keep that site's inventory outside this repository and follow `docs/fleet.md`.

## Results

Every scenario declares its checks in `tracks/<track>/scenarios/*.yaml`. A run writes `results/<run-id>/<track>/<scenario>/` with a manifest, logs, raw captures and metrics. The run status comes only from the recorded checks, and a passed check must point at evidence in the run directory. `make validate` enforces the contract in `schemas/`. Results and logs stay out of git: `results/`, `.lab/` and the evidence copies under `docs/milestones/evidence/` exist only on the machine that ran them. The milestone records in `docs/milestones/` keep the run ids, exit codes and findings, and cite those local paths.

## Safety

- Simulators that use AF_UNIX datagram sockets do not run on the host. Ubuntu kernels 7.0.0-42 and 7.0.0-43 oops in AppArmor when a confined task writes to such a socket, and ibsim in a host container froze the host. `docs/adr/0002-execution-environments.md` explains where each track runs.
- Guests run as unprivileged QEMU processes. Lab network frames never leave the host.
- `make reset-lab` needs `CONFIRM=lab`. Fleet targets need `CONFIRM=cel1`.

## Layout

| Path | Contents |
|---|---|
| `ansible/` | Inventories for `lab` and `cel1`, playbooks, and the roles of the shared stack (`containerd`, `kubeadm`) and the lab (`preflight`, `common`, `network`, `rxe`, `rdma_tools`) |
| `helm/` | `releases.yaml`, the Helm releases in install order, values per environment, and the repo-local Multus chart |
| `images/` | The pinned Track B image and the RDMA test image, both built inside a harness guest |
| `sites/` | Site models |
| `scripts/` | Result contract (`rfl`), scenario runners, doctor, addons runner, fleet and inventory checks |
| `tracks/` | Scenario specs and their configuration |
| `tests/` | Unit tests, fixtures captured from real runs, and the QEMU harness under `tests/integration/qemu-lab/` |
| `schemas/` | JSON schemas of scenario specs, run manifests, metrics and the version lock |
| `versions.lock.yaml` | Every pinned version and checksum, grouped in scopes per milestone |
| `docs/` | ADRs, milestone records, the cel1 runbook, VMware setup and troubleshooting |

## Documents

- `docs/adr/0001-kubernetes-stack.md`: one Ansible and kubeadm stack for the lab and cel1, and the inventory contract.
- `docs/adr/0002-execution-environments.md`: where each track runs and why.
- `docs/fleet.md`: the cel1 runbook.
- `docs/setup-vmware.md`: the same lab as VMware Workstation guests with two VMnets, not verified on the current host.
- `docs/troubleshooting.md`: failures seen so far and how to read them.
