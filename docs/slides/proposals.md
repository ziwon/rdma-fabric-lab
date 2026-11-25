# Talk proposals

Drafts for the [call for proposals](https://events.linuxfoundation.org/kubecon-cloudnativecon-europe/co-located-events/cfp-colocated-events/) of NeoCloudCon Europe 2027, a CNCF-hosted co-located event on 15 March 2027 in Barcelona. The call closes on 30 November 2026 at 23:59 CET and allows two proposals per speaker for one event. Neither proposal has been submitted or accepted.

The fields follow the call: a title in title case, a description in the third person and in full sentences, and the benefits to the ecosystem. Both drafts need the speaker's review before submission.

| | Proposal 1 | Proposal 2 |
|---|---|---|
| Format | Lightning Talk, 10 minutes | Solo Presentation, 25 minutes |
| Deck | [multus-boot-race.html](multus-boot-race.html) | [gpu-region-rehearsal.html](gpu-region-rehearsal.html) |
| Topic of the event | Fleet Operations + Reliability | High Performance Networking + Storage |
| Case study | No | No |
| Presented before | No | No |

## 1. Ready Is Not Wired: A Reboot Test for Kubernetes Secondary Networks

**Description**

A Kubernetes pod can return Ready after a worker reboot with its requested secondary interface missing. This talk reproduces that failure in a three-guest lab using Cilium and Multus with their stock CNI paths. Ordinary pods kept net1 in 20 of 20 starts only because Multus wrote its configuration before their sandboxes began. Delaying Multus by 30 seconds changed the result: 12 of 12 pods came up Ready without net1 and without an error. Restarted pods retained a stale network-status annotation that still listed net1, while pods waiting for the node had no annotation. Moving Cilium's configuration out of the directory containerd reads closed the race in every tested run, including the delayed case: 0 of 32 pod starts lost net1. The speaker shows how to reproduce the window, inspect the interface inside each pod, and make a reboot check fail before verifying the repair. The results cover this layout and test environment, not every Multus installation.

**Benefits to the ecosystem**

Operators who depend on a secondary network need to know whether it survives routine worker reboots. Pod readiness and the presence of a network-status annotation can both give false reassurance. This talk gives them a reproducible test for that gap, covering pods restarted by the kubelet and pods waiting for the node to return. It explains how the runtime chooses a CNI configuration and why delaying Multus exposes a race that an ordinary reboot can miss. The public repository includes the probe, recorded timings and checks that fail on the broken layout and pass after its repair. Teams can adapt the test to their own CNI layout and verify the interface and traffic their workloads require, before relying on a readiness signal during maintenance.

**Open source projects**

Kubernetes, containerd, Cilium, Multus CNI, Whereabouts.

**Evidence behind each claim**

| Claim | Where |
|---|---|
| Stock paths: 0 of 20 ordinary pod starts lost net1; with Multus delayed 30 s, 12 of 12 lost it, all Ready, 9 stale annotations and 3 absent | Probe runs `20261125T125248Z` and `20261125T144022Z`; `.lab/evidence/slides/boot-window/summary.tsv`, `stock-slow-keepers.tsv`, `stock-slow-waiters.tsv` and their raw run directories; committed summary `.site/src/excerpts/slides-boot-window.json` |
| Lab layout: 0 of 32 ordinary pod starts lost net1, including 0 of 12 with the delay | The same probe runs and summary; `lab-layout-keepers.tsv`, `lab-layout-waiters.tsv`, `lab-layout-slow-keepers.tsv` and `lab-layout-slow-waiters.tsv` under `.lab/evidence/slides/boot-window/` |
| Earliest pod: stock paths failed 3 checks, the other 11 passed; the lab layout passed all 14 | `k8s-multus-net1`, runs `20261125T131218Z` and `20261125T132655Z`; `.lab/evidence/slides/runs/<run>/a-soft-roce/k8s-multus-net1/summary.md`; committed excerpts `.site/src/excerpts/slides-net1-break.json` and `slides-net1-repair.json` |
| Configuration layout and the repeatable reboot probe | `docs/adr/0004-track-a-pods-and-net1.md` sections 1.4 and 3; `tests/integration/qemu-lab/probe-boot-window.sh` |
| Removing the delay and restoring the lab layout left all 14 scenario checks passing | Run `20261125T150004Z`; `.lab/evidence/slides/runs/20261125T150004Z/a-soft-roce/k8s-multus-net1/summary.md` |

Raw lab records stay local. The committed excerpts above publish the measurements used by this proposal.

## 2. Rehearsing a GPU Region on One Host Before the Racks Arrive

**Description**

Bringing up a GPU region means agreeing on hundreds of facts before any hardware works: host names, address ranges, which rail of which node goes to which InfiniBand leaf, and what Kubernetes installs on each node class. A wrong fact is usually found on site, with accelerators idle. The speaker rehearses those facts on one Linux host with open source tools. One YAML model describes an invented region of 13 nodes, 12 InfiniBand switches and 128 cables. A validator rejects overlapping ranges and a rail cabled to the wrong leaf. From the same model the speaker renders the Ansible inventory, builds the cluster with kubeadm on 13 QEMU guests, runs OpenSM against the simulated fabric through a link failure, installs the control plane of per-rail pod networks, and exports the region to NetBox with drift detection. The checks in these completed scenarios have also failed on deliberate breaks. The talk states its limits: no GPU, no HCA, no InfiniBand data path and no performance number.

**Benefits to the ecosystem**

Teams planning GPU capacity can check a region's address and cable plan before installation. The talk provides a working example in which the same model drives validation, inventory generation and a simulation of the cluster. Operators can run their actual playbooks and subnet manager against that model, then compare the resulting NetBox inventory for drift. Recorded failures show what this catches: a pod that lost its secondary network at boot, and a device plugin that could not expose software RDMA devices. The talk also identifies the checks that need hardware, so a successful simulation is not mistaken for proof of an InfiniBand data path or GPU performance. The public repository includes the model, test code and recorded results for reuse.

**Open source projects**

Kubernetes, kubeadm, containerd, Cilium, Multus CNI, NVIDIA Network Operator, NVIDIA GPU Operator, OpenSM, ibsim, NetBox, Ansible, QEMU.

**Evidence behind each claim**

| Claim | Where |
|---|---|
| 13 nodes, 12 switches, 128 cables, and the validator's rules | `docs/fleet.md` sections 1 and 2 |
| The cluster on 13 guests, 20 of 20 checks | `cel1-sim-baseline`, run `20261115T164615Z`, `docs/fleet.md` section 13 |
| OpenSM through a link failure: 13 routes moved, all 76 LIDs answered | B-04, `docs/adr/0005-track-b-fabric-simulation.md` |
| The rail networks' control plane, 10 of 10 checks | `cel1-rail-networks`, run `20261118T014617Z`, `docs/fleet.md` section 14 |
| NetBox: 1142 objects, drift found and undone, 9 of 9 checks | `cel1-netbox`, run `20261121T164015Z`, `docs/fleet.md` section 15 |
| Checks in the completed scenarios failed on deliberate breaks | `docs/adr/0003-results-and-breaks.md`; `docs/fleet.md` sections 14.2 and 15 |
| What was not run | `docs/fleet.md` section 10 |
