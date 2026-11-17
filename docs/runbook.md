# Runbook

This runbook takes an operator from a fresh Ubuntu 24.04 host to every track of the lab, and back to a clean host. It names each target in the order it runs, what it needs, how long it took on this host in M7, and how to read, clean and recover what it leaves. Every duration and size below was measured in M7 and comes from the step tables that `docs/milestones/M7.md` cites.

## 1. What runs where

| Track | Question | Runs in | Result kind |
|---|---|---|---|
| A, Kubernetes and Soft-RoCE | How do RDMA programs run over Ethernet, and how do Kubernetes pods reach them? | The `lab` profile: cp1, worker1 and worker2. The `ha` profile adds three control planes behind a VIP | `software-rdma-measurement` for RXE traffic, `software-cluster-test` for cluster behavior |
| B, InfiniBand management | How does OpenSM discover a fabric, assign LIDs and react to link faults? | A container in the guest of the `ibsim` profile | `ib-management-simulation` |
| C, RoCE congestion control | How do PFC, ECN and DCQCN change queues, rates and flow completion in a model? | A container in the guest of the `ns3` profile | `packet-model-simulation` |
| cel1 | Does the fleet code build the invented region cel1? | 13 node guests and one syslog stand-in of the `cel1` profile | `software-cluster-test` |

A result of one kind never stands in for another. RXE numbers are software results of this host. Track B shows management behavior of a simulator. Track C shows what one packet model computes. None of them says anything about HCA, switch or GPU hardware.

Every guest is an unprivileged QEMU process. Its network frames never leave the host. Guests reach each other only through AF_UNIX datagram sockets of the harness, and the host reaches each guest through a 127.0.0.1 SSH forward.

## 2. Requirements

| Need | Why | Check |
|---|---|---|
| Ubuntu 24.04 host with KVM, and `/dev/kvm` readable and writable by your user | Every guest is a KVM guest. The lab needs no sudo | `make doctor` |
| `qemu-system-x86_64`, `qemu-img`, `cloud-localds`, `ssh`, `ssh-keygen`, `curl` | The QEMU harness and its seeds, keys and image download | `make doctor` reports each one missing |
| `make`, `git`, `uv`, `shellcheck` | `make venv` builds `.venv` with uv, and `make lint` runs shellcheck over `git ls-files` | `uv --version`, `shellcheck --version`. M7 used uv 0.6.17 and ShellCheck 0.9.0 |
| `strace` | `acceptance-cel1.sh` proves the fleet targets refuse without network calls | `strace -V` |
| Internet access from the host and the guests | The guest cloud image, Ubuntu packages, pkgs.k8s.io, Helm charts, container base images and the Track C sources, each pinned in `versions.lock.yaml` | The first `make lab-up` downloads the image and checks its sha256 |
| Free cores, memory and disk for the profile you boot | Section 4 | `make doctor` warns, it does not fail |

Install the packages with your package manager once. Nothing in the lab calls sudo on the host. The host must not run another container runtime workload that you need while the lab measures RXE figures, because the figures share the host's CPUs.

## 3. Host checks

```bash
make venv          # .venv with the Python, Ansible and linters the lock pins
make doctor        # read-only: virtualization, tools, resource budget, address plan overlaps
make lint test     # static checks and unit tests, no lab needed
make tools         # helm and kubeconform at their locked versions in .tools/bin
make versions      # the lock against this environment
```

`make doctor` fails when an address range of the lab plan overlaps a host interface, route, Docker network or libvirt network. `docs/troubleshooting.md` section 1 says how to move the plan.

Put the guest disks on a file system with room. Each profile takes `LAB_DIR=<dir>` in the environment. The default is `.lab/` in the checkout, and `.lab/<profile>` for profiles other than `lab`. The base cloud image always stays in `.lab/images/`, and each guest disk is a qcow2 overlay on it. Run one profile at a time whenever RXE figures are collected. `lab.sh up` boots every guest of one profile, so check `pgrep -af '^qemu-system'` before you boot another.

## 4. Guests per profile

The vCPU, memory and disk columns are what the profile files under `tests/integration/qemu-lab/profiles/` give QEMU. Disks are thin qcow2 overlays, so the used column is what they held after the M7 runs.

| Profile | Guests | vCPU | Memory | Disk, virtual | Disk, used after M7 |
|---|---:|---:|---:|---:|---:|
| `lab` | 3 | 10 | 16 GiB | 110 GiB | 13 GiB |
| `lab` builder, the `ibsim` profile in `LAB_DIR/builder` | 1 | 4 | 6 GiB | 20 GiB | 1.1 GiB |
| `ha` | 4 | 8 | 9.5 GiB | 80 GiB | 16 GiB |
| `ibsim` | 1 | 4 | 6 GiB | 20 GiB | 1.3 GiB |
| `ns3` | 1 | 12 | 16 GiB | 100 GiB | 5.3 GiB |
| `cel1` | 14 | 27 | 34.75 GiB | 270 GiB | 39 GiB |

The builder boots only while `make rdma-tools-image` runs, beside the lab guests. Every other profile runs alone.

## 5. Order of targets

`tests/integration/qemu-lab/acceptance-all.sh` runs everything below from wiped guests, one profile at a time, and ends with `make validate` and `make report STRICT=1`. Run it with `LAB_ROOT=<dir with room>`. Each profile's guests live in `LAB_ROOT/<profile>`.

### Track A

`tests/integration/qemu-lab/acceptance-m3.sh` runs this sequence from wiped guests, with every rerun that must change nothing.

```bash
export LAB_DIR=/data/rfl-lab/lab                   # optional, the guests' files
make lab-wipe lab-up                               # boot cp1, worker1, worker2 from the pinned image
make versions SCOPE=m3                             # every component of M1 to M3 pinned
make bootstrap rxe-up                              # locked packages, lab NIC address, rxe0 by rfl-rxe.service
make k8s                                           # kubeadm cluster, admin kubeconfig in .lab/kubeconfig
make addons-template ENV=lab                       # offline render of the Helm releases
make addons ENV=lab KUBECONFIG_FILE=.lab/kubeconfig  # Cilium, Multus, Whereabouts
make rdma-tools-image                              # build the RDMA test image in the builder guest
make lab-down LAB_PROFILE=ibsim LAB_DIR=$LAB_DIR/builder   # free the builder before measuring
make rdma-tools-load                               # import the image on both workers by digest
make track-a                                       # every required Track A scenario, below
make collect                                       # host and worker facts, versions, resolved configuration
make lab-down
```

`make track-a` runs `vm-rxe-baseline`, `vm-rxe-rerun`, `k8s-baseline`, `k8s-hostnet-rdma`, `k8s-multus-net1` and `vm-rxe-reboot`, in that order, on a lab that is up. `vm-rxe-reboot` runs last because `k8s-baseline` reads the Cilium agents once, and a rebooted node's agent turns Ready up to 30 s after its boot. Each scenario has its own target too: `make rxe-test SCENARIO=<name>`, `make k8s-test`, `make k8s-hostnet-test` and `make k8s-multus-test`.

The three-control-plane profile is optional. `tests/integration/qemu-lab/acceptance-ha.sh` boots it from wiped guests, builds the cluster twice, installs the addons and runs `make k8s-ha-test LAB_PROFILE=ha`, which cuts the power of the VIP holder.

### Track B

```bash
export LAB_DIR=/data/rfl-lab/ibsim
make lab-down lab-wipe LAB_PROFILE=ibsim           # start from nothing
make track-b                                       # prepare the guest, build or reuse rfl-ibsim:m4, run B-01 to B-04
make track-b-breaks                                # every scenario must fail without its break link
make lab-down LAB_PROFILE=ibsim
```

### Track C

```bash
export LAB_DIR=/data/rfl-lab/ns3
make lab-down lab-wipe LAB_PROFILE=ns3
make track-c CLEAN=1                               # prepare the guest, build rfl-ns3:m6 from nothing, run c-01 to c-06
make track-c-breaks                                # every declared break must fail its checks
make lab-down LAB_PROFILE=ns3
```

`make track-c-smoke` runs `c-01-example` and `c-02-single-flow`, the pinned SimAI example and the single-flow baseline. `make track-c-matrix` runs the incast scenarios `c-03` to `c-06`: four congestion profiles on the same incast traffic, and the optional kmin sensitivity run. `make track-c` is the two under one run id.

### cel1

```bash
LAB_DIR=/data/rfl-lab/cel1 tests/integration/qemu-lab/acceptance-cel1.sh
```

It boots 14 guests from wiped disks, proves that every fleet target refuses without `CONFIRM=cel1` and the simulation inventory, builds the cluster with `make fleet-k8s`, `fleet-addons` and `fleet-rsyslog`, checks that a second run changes nothing, and runs `cel1-sim-baseline` before, during and after a deliberate break. `docs/fleet.md` explains each fleet target.

### The command interface

The lab design names one command per step. Each is a target, or a named target covers it.

| Command | Target today |
|---|---|
| `make doctor`, `versions`, `bootstrap`, `rxe-up`, `rxe-test`, `k8s` | The targets of the same name |
| `make cilium` | `make addons ENV=lab KUBECONFIG_FILE=.lab/kubeconfig RELEASE=cilium`. One target installs every release of `helm/releases.yaml` in order, so a second target would copy it |
| `make multus` | `make addons ENV=lab KUBECONFIG_FILE=.lab/kubeconfig RELEASE=multus`, and `RELEASE=whereabouts` for its IPAM |
| `make track-a`, `track-b`, `track-c-smoke`, `track-c-matrix` | The targets of the same name. `make track-c` is the last two together |
| `make collect`, `report`, `cleanup`, `reset-lab` | The targets of the same name |

`make help` lists every target that exists.

## 6. How long each step took

Measured in `acceptance-all.sh` run `20261116T190004Z` on this host (24 cores, 125 GiB), one profile at a time, from wiped guests. Guest disks were on a separate data volume.

| Step | Seconds | Minutes | What it covers |
|---|---:|---:|---|
| `acceptance-m3.sh` | 1280 | 21.3 | lab and builder from wiped disks, bootstrap, rxe-up, Kubernetes, the three Helm releases, the RDMA test image, both M3 scenarios, the M1 and M2 scenarios, every rerun that must change nothing |
| `make collect` | 7 | 0.1 | host and worker facts on the running lab |
| `make track-a` | 418 | 7.0 | the six required Track A scenarios on the running lab |
| `make lab-down` | 274 | 4.6 | the three lab guests shut down. The Whereabouts daemon ignores SIGTERM, so each worker waits for its stop timeout |
| `acceptance-ha.sh` | 761 | 12.7 | three control planes and a worker from wiped disks, the VIP power-cut test |
| `make track-b` | 162 | 2.7 | the Track B guest from wiped disks, the image, B-01 to B-04 |
| `make track-b-breaks` | 84 | 1.4 | every Track B break |
| `make track-c CLEAN=1` | 985 | 16.4 | the Track C guest from wiped disks, the image built with no cache, `c-01` to `c-06` |
| `make track-c-breaks` | 457 | 7.6 | every Track C break |
| `acceptance-cel1.sh` | 1591 | 26.5 | 13 cel1 guests and a syslog stand-in from wiped disks, the fleet targets, the cel1 scenario, its break and repair |
| `make validate`, `make report STRICT=1` | 4 | 0.1 | 363 run directories |
| Whole run | 6031 | 100.5 | from 19:00:04Z to 20:40:35Z on 2026-11-16 |

## 7. Reading results

Each run writes `results/<run-id>/<track>/<scenario>/`.

| File | Contents |
|---|---|
| `manifest.json` | The run id, track, scenario, `required`, `result_kind`, `deliberate_break`, the status, every declared check with its exit code, failure class and evidence paths, the host and guest environment, input checksums, timing and the security profile |
| `resolved-config.yaml` | The parameters the run used after every default and override |
| `commands.log` | Every command, where it ran, its exit code and duration |
| `stdout.log`, `stderr.log` | What the runner printed |
| `raw/` | Every capture a check cites |
| `metrics.json` | Measured or simulated figures with their source file. A figure nobody measured is null with a reason |
| `summary.md` | The checks and metrics as a table |

The status comes from the checks alone: failed, then blocked, then not_run, then passed. A passed check must cite non-empty evidence inside the run directory. `make validate` checks every run directory against `schemas/`, and `make validate RUN=<run-id>` checks one run.

`deliberate_break` is null for an ordinary run. A run that breaks something on purpose names the break and what it does, so its failed status is the expected outcome. Track B and Track C runners set it for their breaks. A script that breaks the environment before it calls a runner sets `RFL_BREAK=<name>` and `RFL_BREAK_WHAT=<text>`, as `acceptance-cel1.sh` does. Manifests of `schema_version` 1 predate the field. For them the report reads the input parameter Track B and Track C used, `break_link` and `deliberate_break`.

`make report` answers whether the MVP holds on the results of this machine. For each scenario it shows the latest finished run that is not a deliberate break, grouped by result kind, with required and optional scenarios counted apart. Each row also counts the older runs, the break runs and the unfinished runs it left out. The last lines give `required (MVP): <n> of <m> scenarios passed` and the optional count. `make report STRICT=1` exits 1 unless every required scenario passed, and both forms exit 4 when a run directory is invalid. `make report RUN=<run-id>` narrows it to one run.

## 8. Cleanup

A runner that ends normally, or on SIGINT or SIGTERM, removes what it started. A runner that dies on SIGKILL, a lost SSH session or a host crash leaves its resources. `make cleanup RUN=<run-id>` removes them, on the guests of `LAB_PROFILE` and on this host.

| Where | What it removes | How it finds it |
|---|---|---|
| Every guest | Processes and `/var/tmp/rfl/<run-id>` | `RFL_RUN=<run-id>` in the process environment |
| The Track B and Track C guests | The run's containers and run directories | `guest.sh stop-run <run-id>` inside the guest, by the `rfl.run` label and the directory name |
| The cluster | Namespaces labeled `rdma-fabric-lab/run=<run-id>`, with their pods and NetworkAttachmentDefinition, and any pod with that label elsewhere | The label |
| This host | Processes of your user, such as the decoy of a Track C `host-process` break | `RFL_RUN=<run-id>` in the process environment |

```bash
make cleanup RUN=<run-id>                                    # Track A on the lab profile
LAB_PROFILE=ibsim LAB_DIR=<dir> make cleanup RUN=<run-id>    # Track B
LAB_PROFILE=ns3 LAB_DIR=<dir> make cleanup RUN=<run-id>      # Track C
```

It runs no `kubeadm reset`, changes no network definition, and removes no container, namespace or process that does not carry the run id. It exits 1 and prints `LEFT` for anything that remains, including a guest it cannot reach. `tests/integration/qemu-lab/cleanup-check.sh a|b|c` proves this on a profile that is up: it kills a bystander run and target runs with SIGKILL, cleans the targets, and compares the rest of the profile with a snapshot from before.

## 9. Recovery

| Situation | Recovery |
|---|---|
| A run was interrupted | `make cleanup RUN=<run-id>` with the profile of the run, then run the scenario again |
| rxe0 or the lab NIC address is gone on a worker | `make rxe-up`, which restarts `rfl-rxe.service`. `docs/troubleshooting.md` sections 4 and 5 |
| A Cilium agent or Multus daemon is not Ready after a reboot | Wait for `kubectl -n kube-system rollout status daemonset/cilium` and `daemonset/kube-multus-ds`. Sections 9 and 12 of the troubleshooting guide |
| A cluster was built before Cilium moved its CNI file | `make cni-migrate` once after `make addons ENV=lab` |
| A guest hangs | `tests/integration/qemu-lab/lab.sh kill <guest>` cuts its power, `lab.sh start <guest>` boots it again. The console is in `LAB_DIR/guests/<guest>/serial.log` |
| A profile is in a state you cannot explain | `make lab-down lab-wipe LAB_PROFILE=<profile>`, then its acceptance script from the start. This is the clean-environment path M7 measured |
| The RXE setup of the workers must go | `make reset-lab CONFIRM=lab`, section 10 |

## 10. reset-lab

`make reset-lab` is the only destructive target of the lab. Without `CONFIRM=lab` it prints what it would delete, changes nothing and exits 2. With it, `ansible/playbooks/reset.yml` deletes an rxe0 bound to the lab NIC, `rfl-rxe.service`, its module load file and the lab NIC address on every worker of the lab inventory. It refuses a host without `lab_inventory: rdma-fabric-lab`, so it never reaches the cel1 inventory. It leaves the cluster, the packages and the guests. `make bootstrap rxe-up` brings the RXE setup back. `tests/integration/qemu-lab/cleanup-check.sh reset` proves both, then runs `vm-rxe-baseline` again.

Deleting the guests themselves is `make lab-down lab-wipe LAB_PROFILE=<profile>` with the profile's `LAB_DIR`. `lab.sh wipe` refuses while a guest of the profile runs, and resolves each guest directory before it deletes it.

## 11. Safety rules

- **Simulators run only in guests.** Ubuntu kernels 7.0.0-42 and 7.0.0-43 hit a NULL dereference in AppArmor when a confined process writes to a connected AF_UNIX datagram socket. ibsim and libumad2sim use that socket, and Docker confines every container, so ibsim in a host container froze this host three times. ibsim, OpenSM, the InfiniBand diagnostic tools and the ns-3 build and runs run only inside their guest, on its 6.8 kernel. `guest.sh` refuses on any machine without the guest marker and outside a VM, and every run checks the host for simulator processes at its start and end. `docs/adr/0002-execution-environments.md` has the decision.
- **No container on the host.** The RDMA test image builds in the builder guest. No target starts a container on the host.
- **No sudo on the host.** RXE devices, modules and network changes exist only inside guests.
- **One profile at a time while RXE figures are collected.** The acceptance scripts fail the step before each RXE run when another guest runs.
- **cel1 is invented.** Its addresses are never contacted from the host. Every target that changes cel1 hosts needs `CONFIRM=cel1` and a connection inventory whose hosts are simulation guests on 127.0.0.1, and it exits 2 before any SSH or Helm call otherwise.
- **Credentials stay out of results.** `make collect` keeps no kubeconfig, key, token or password, and replaces any snapshot that carries credential text with a note. Results, logs and evidence copies are never committed.

## 12. Continuous integration

`.github/workflows/checks.yml` runs on pull requests and pushes to `main`, on a GitHub-hosted `ubuntu-24.04` runner with read-only repository permission and no secret. It runs `make venv`, `make lint`, `make test`, `make fleet-check` and `make track-b-render-check`. Every action is pinned by its full commit SHA.

The tracks stay manual. They need `/dev/kvm`, a host with tens of GiB of memory and disk, hours of runtime, and network access from guests. A hosted runner does not promise any of that. A self-hosted lab runner would run pull request code with access to the lab host, so it may run only trusted changes, started by hand or from an approved branch, never code from an outside pull request. Until such a runner exists, run `tests/integration/qemu-lab/acceptance-all.sh` on the lab host before a merge that touches a track.

## 13. Where the records live

| Record | Path |
|---|---|
| M0, design lock: inventory, address plan, version lock, doctor | `docs/milestones/M0.md` |
| M1, RXE between two worker guests | `docs/milestones/M1.md` |
| M2, Kubernetes with Cilium, and the ha profile | `docs/milestones/M2.md` |
| M3, hostNetwork RDMA pods and Multus | `docs/milestones/M3.md` |
| M4, ibsim and OpenSM | `docs/milestones/M4.md` |
| M5, the ns-3 model and its single-flow baseline | `docs/milestones/M5.md` |
| M6, the four congestion profiles under incast | `docs/milestones/M6.md` |
| cel1, the region simulated on 13 guests | `docs/milestones/cel1-sim.md`, `docs/fleet.md` |
| M7, this runbook, cleanup and the clean reproduction | `docs/milestones/M7.md` |
| Decisions | `docs/adr/` |
| Failures and their causes | `docs/troubleshooting.md` |
| VMware Workstation variant, not verified on this host | `docs/setup-vmware.md` |

Each record cites run ids under `results/` and copies under `docs/milestones/evidence/`. Both stay on the machine that ran them and are not in git.

## 14. Open experiments

These are outside the MVP and did not run.

- **Leaf-spine in Track C.** Two leaves, two spines and eight hosts at 1:1 and 2:1. The driver builds a star around one switch only.
- **A switch with PFC fully off.** In this model PFC off means the hosts ignore PAUSE, while the switch still sends it. A switch without PFC needs a model change.
- **The Vagrant boot.** The guests boot through the QEMU harness only.
- **Pod-private RDMA.** A pod's own RXE device or a device plugin, instead of hostNetwork pods with the node's device.
- **NetBox as the site source.** `sites/cel1.yaml` stays the single source of cel1.
- **B-05.** The OpenSM restart and standby SM scenario.
- **IPoIB and hardware.** Nothing here creates an IPoIB data path or touches an HCA, a switch or a GPU.
