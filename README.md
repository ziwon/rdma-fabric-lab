# rdma-fabric-lab

A software-only lab for RDMA, InfiniBand management and RoCE congestion control on one Ubuntu host, with no HCA, InfiniBand switch or GPU. The same Ansible and kubeadm code that builds the lab cluster also describes `cel1`, an invented GPU region with DGX B200 nodes, so every lab run exercises that code.

`PLAN.md` is the design. This file says what exists today and how to run it.

## Tracks

| Track | Question | Stack | Runs in |
|---|---|---|---|
| A. Kubernetes and Soft-RoCE | How do RDMA programs run over plain Ethernet, and how do Kubernetes workloads reach them? | QEMU guests, Ansible, kubeadm, containerd, Cilium by Helm, RXE | QEMU guests |
| B. InfiniBand management | How does a subnet manager discover a fabric, assign LIDs and react to link faults? | ibsim, OpenSM, infiniband-diags | A container inside a dedicated QEMU guest |
| C. RoCE congestion control | How do PFC, ECN and DCQCN settings change queues, rates and flow completion? | ns-3 RDMA model | A container inside a dedicated QEMU guest |

The tracks share the repository, the result format and the version lock, not a data path. A result of one track never stands in for another. `PLAN.md` section 2 lists what this lab does not claim, such as hardware RDMA performance or real lossless switching.

## Status

| Milestone | State | Record |
|---|---|---|
| M0 design lock | done | `docs/milestones/M0.md` |
| M1 RXE between two worker VMs | done. rping, RDMA Read, Write and Send pass over RDMA CM, RoCEv2 frames appear on the lab NIC and none on the management NIC, and the device returns after a reboot | `docs/milestones/M1.md` |
| M2 Kubernetes | done. Kubernetes v1.36.6 with Cilium 1.20.4 installed by Helm. Three control planes behind a kube-vip address answered again 6.0 s after the address holder lost power | `docs/milestones/M2.md` |
| cel1 region, offline | done. Site model, generated inventory, contract check and rendered charts pass | `docs/fleet.md` |
| M4 ibsim and OpenSM | in progress, inside a guest | |
| cel1 region, simulated as 13 guests | not started | |
| M3, M5, M6, M7 | not started | `PLAN.md` section 10 |

Nothing in this repository has run against GPU hardware.

## Requirements

- Ubuntu 24.04 host with KVM, and `/dev/kvm` usable by your user. No sudo is needed for the lab.
- `qemu-system-x86_64`, `qemu-img`, `cloud-localds`, `ssh-keygen`, `uv`, `make`, `git`, `curl`.
- About 16 GiB of free memory and 10 cores for the default lab profile. `PLAN.md` section 3.1 has the budget per guest.

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
make addons ENV=lab KUBECONFIG_FILE=.lab/kubeconfig   # Cilium by Helm
make k8s-test                        # nodes, pod traffic, DNS, Service, NetworkPolicy
make validate report                 # check and tally every run directory
make lab-down
```

The three-control-plane profile runs with `LAB_PROFILE=ha`. `make help` lists every target.

## The cel1 region

`sites/cel1.yaml` is the single source of the region: 3 control planes, 8 DGX B200 nodes and 2 PCIe GPU servers, the address plan and a rail-optimized InfiniBand fabric. `make fleet-render` writes `ansible/inventories/cel1/` from it. `make fleet-check` validates the model, the generated inventory, the inventory contract and the Helm values offline.

Every name and address in `cel1` is invented. The targets that would change cel1 hosts refuse to run without `CONFIRM=cel1` and a connection inventory. To apply the code to another site, keep that site's inventory outside this repository and follow `docs/fleet.md`.

## Results

Every scenario declares its checks in `tracks/<track>/scenarios/*.yaml`. A run writes `results/<run-id>/<track>/<scenario>/` with a manifest, logs, raw captures and metrics. The run status comes only from the recorded checks, and a passed check must point at evidence in the run directory. `make validate` enforces the contract in `schemas/`. Raw results stay out of git. Selected evidence lives under `docs/milestones/evidence/`.

## Safety

- Simulators that use AF_UNIX datagram sockets do not run on the host. Ubuntu kernels 7.0.0-42 and 7.0.0-43 oops in AppArmor when a confined task writes to such a socket, and ibsim in a host container froze the host. `docs/adr/0002-execution-environments.md` explains where each track runs.
- Guests run as unprivileged QEMU processes. Lab network frames never leave the host.
- `make reset-lab` needs `CONFIRM=lab`. Fleet targets need `CONFIRM=cel1`.

## Layout

| Path | Contents |
|---|---|
| `ansible/` | Inventories for `lab` and `cel1`, playbooks, and the roles of the shared stack (`containerd`, `kubeadm`) and the lab (`preflight`, `common`, `network`, `rxe`) |
| `helm/` | `releases.yaml`, the Helm releases in install order, and values per environment |
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
- `docs/setup-vmware.md`: the VMware Workstation layout of `PLAN.md`, not verified on the current host.
- `docs/troubleshooting.md`: failures seen so far and how to read them.
