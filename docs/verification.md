# Verification: how the lab proves itself

This document says how a claim of the lab is proved, what the latest full reproduction showed, and which evidence backs each item of the MVP checklist. `docs/adr/0003-results-and-breaks.md` records why the proof works this way. `docs/runbook.md` says how to run each step.

## 1. What a proof is made of

| Part | Where | What it guarantees |
|---|---|---|
| Scenario spec | `tracks/<track>/scenarios/<scenario>.yaml`, valid against `schemas/scenario.schema.json` | Every check the run must record, whether the scenario is required for the MVP, and its result kind |
| Check | `rfl run-check` in the runner, with exit code, failure class and evidence files | A passed check cites non-empty evidence inside its run directory |
| Run directory | `results/<run-id>/<track>/<scenario>/` with `manifest.json`, `resolved-config.yaml`, `commands.log`, `stdout.log`, `stderr.log`, `raw/`, `metrics.json` and `summary.md` | The status follows from the checks alone: failed, then blocked, then not_run, then passed. A figure nobody measured is null with a reason |
| Result kind | `result_kind` in the spec and the manifest | RXE measurements, cluster tests, the InfiniBand management simulation and the packet model are reported apart and never stand in for each other |
| Deliberate break | `deliberate_break` in the manifest, the break tables of the runners, the break scripts | Every check failed once on purpose and passed after the repair |
| Evidence copy | `.lab/evidence/`, ignored by git | The lines that documents and the site quote, kept on the machine that ran them |

## 2. The commands that prove it

| Command | Proves | Fails when |
|---|---|---|
| `make lint` | yamllint, ansible-lint with the production profile, shellcheck, every scenario spec and the version lock are valid | Any of them reports a problem |
| `make test` | The parsers, verdicts, renderers and contract checks behave as the captured real output requires | A unit test fails |
| `make validate` | Every run directory under `results/` satisfies the contract in `schemas/` | A manifest, a status or an evidence path breaks the contract |
| `make report STRICT=1` | The latest finished run of every required scenario that is not a deliberate break passed | A required scenario has no such run or its latest one did not pass. Exit 4 for an invalid run directory |
| `make track-b-breaks`, `make track-c-breaks` | Every Track B and Track C check can fail | A break run ends anything but failed with its declared checks failed |
| `tests/integration/qemu-lab/breaks-k8s-rdma.sh` | Every check of `k8s-hostnet-rdma` and `k8s-multus-net1` can fail, and passes after the repair | A broken run passes or a repaired run fails |
| `tests/integration/qemu-lab/cleanup-check.sh a\|b\|c\|reset` | `make cleanup` removes exactly what a killed run left, per track, and `make reset-lab` refuses without `CONFIRM=lab` | Anything of the run remains or anything else changed |
| `tests/integration/qemu-lab/acceptance-all.sh` | Every required experiment passes again from wiped guests on the current code | Any of its steps returns another exit code than it expects |

`make fleet-check`, `make track-b-render-check` and `make site` are offline checks of the cel1 model, the Track B topology and the learning site. `.github/workflows/checks.yml` runs `make venv`, `make tools`, `make lint`, `make test`, `make fleet-check` and `make track-b-render-check` on pull requests and pushes to `main`. A local rehearsal of the workflow on a fresh clone passed every command with the 605 tests then present and none skipped (`.lab/evidence/m7/ci-local-2/steps.tsv`). No hosted run has happened, because GitHub Actions starts no job on this account until its billing is settled.

## 3. Deliberate breaks

A break changes one thing, the run must end failed with the named checks failed, and after the repair the next run must pass. Each track breaks its checks in its own way.

| Track | How it breaks | Runs |
|---|---|---|
| A, VM scenarios | `acceptance.sh` disables `rfl-rxe.service`, takes the lab link down and drops every frame with `netem loss 100%`, then repairs each | Runs `20261115T022358Z`, `20261115T022449Z` and `20261115T022539Z` failed, and the runs after each repair passed |
| A, cluster scenarios | `acceptance-k8s.sh` deletes a Cilium agent and drops VXLAN. `breaks-k8s-rdma.sh` breaks the pod scenarios | Agent and VXLAN: runs `20261116T074326Z` and `20261116T074456Z` failed, and the runs after each repair passed. Pods: 20 pairs in the series `.lab/breaks-m3/20261115T224237Z`, `20261115T231416Z` and `20261116T000255Z` covered all 30 checks of the two scenarios, and `20261116T060514Z` and `20261116T061424Z` the checks the CNI layout changed (`pairs.tsv` in each) |
| B | The simulator gets the topology without one link while every expectation keeps the full topology | Run `20261116T194838Z`, all four scenarios failed |
| C | One entry of the break table in `scripts/ns3/run.py` per run. A unit test asserts the entries together target every check | Runs `20261116T200636Z` to `20261116T201314Z`, 27 breaks, each failed the checks it declares |
| cel1 | `acceptance-cel1.sh` stops rsyslog on one DGX guest, then repairs it with `make fleet-rsyslog` | Run `20261116T202754Z` failed only `syslog-to-central-address`, run `20261116T203232Z` passed |

The unit tests break their own verdicts too. A mutation check changed one line in each of eight places of the Track B verdicts and the harness, and each change failed at least one test (`.lab/evidence/m4/unit-mutations.txt`).

## 4. The latest full reproduction

`tests/integration/qemu-lab/acceptance-all.sh` ran from 19:00:04Z to 20:40:35Z on 2026-11-16, 6031 s, from wiped guests, one harness profile at a time, with the guest disks under `LAB_ROOT` on a separate data volume. All 29 steps returned the exit code they expect (`.lab/acceptance-all/20261116T190004Z/steps.tsv`, copied to `.lab/evidence/m7/acceptance-all/`). Before every profile a step failed unless no other guest ran, and no step collected an RXE figure while a second profile ran. The host did not reboot during the run. Its first step ran the Track A acceptance, then named `acceptance-m3.sh` and now `tests/integration/qemu-lab/acceptance-track-a.sh`.

| Step | Exit | Seconds | Runs |
|---|---:|---:|---|
| Track A acceptance | 0 | 1280 | Its own step table, every step as expected |
| `make collect` | 0 | 7 | `vm-environment` `20261116T192124Z` passed, `no-credential-collected` included |
| `make track-a` | 0 | 418 | `vm-rxe-baseline` `20261116T192132Z`, `vm-rxe-rerun` `20261116T192156Z`, `k8s-baseline` `20261116T192216Z`, `k8s-hostnet-rdma` `20261116T192258Z`, `k8s-multus-net1` `20261116T192350Z`, `vm-rxe-reboot` `20261116T192626Z`, all passed |
| `acceptance-ha.sh` | 0 | 761 | `k8s-ha-vip` `20261116T193852Z` passed |
| `make track-b`, `make track-b-breaks` | 0, 0 | 162, 84 | B-01 to B-04 `20261116T194712Z` passed on `ibsim.image` `sha256:e4374db2…`. Break run `20261116T194838Z` |
| `make track-c CLEAN=1`, `make track-c-breaks` | 0, 0 | 985, 457 | `c-01` to `c-06` `20261116T200409Z` passed on `ns3.image` `sha256:da7a3245…`, built with no cache. Break runs `20261116T200636Z` to `20261116T201314Z` |
| `acceptance-cel1.sh` | 0 | 1591 | `cel1-sim-baseline` `20261116T203232Z` passed after its break and repair |
| `make validate` | 0 | 2 | 363 run directories, 0 invalid |
| `make report STRICT=1` | 0 | 2 | 15 of 15 required and 4 of 4 optional scenarios passed. Not counted: 44 deliberate break runs, 19 unfinished runs, 281 older runs |

The cleanup proofs ran on the same day before the full run (`.lab/cleanup-check/`).

| Track | Proof | Result |
|---|---|---|
| A | `20261116T182859Z-a`, 23 of 23 steps | Three runs killed with SIGKILL left `tcpdump` processes, run directories and two namespaces. Each cleanup removed only its own run's resources, and rxe0, the unit, the nodes, every other pod and every Whereabouts allocation equal the snapshot |
| B | `20261116T185843Z-b`, 15 of 15 | Two killed runs left their fabric containers in the Track B guest. `guest.sh stop-run` removed one run at a time, and the image, Docker and the marker equal the snapshot |
| C | `20261116T184404Z-c`, 28 of 28 | Three killed runs left containers in the Track C guest and a `host-process` decoy on the host. Each cleanup removed one run, the decoy included |
| reset | `20261116T183029Z-reset`, 11 of 11 | `make reset-lab` without `CONFIRM=lab` exited 2 and changed nothing. With it, rxe0, the lab address and the unit were gone and the nodes stayed Ready. `make bootstrap rxe-up` restored them, and `vm-rxe-baseline` `20261116T183055Z` passed |

## 5. MVP checklist

| Item | Met | Evidence |
|---|---|---|
| Install, source and image versions are locked, and the environment can be collected | yes | `vm-environment` `20261116T192124Z`, which compares every `lab.*` lock value with the workers |
| RXE Read, Write and Send between the worker VMs reproduce | yes | `vm-rxe-baseline` `20261116T192132Z` |
| The baseline holds after a reboot and an Ansible rerun | yes | `vm-rxe-reboot` `20261116T192626Z` and `vm-rxe-rerun` `20261116T192156Z` |
| The Kubernetes base network and the hostNetwork RDMA test each pass | yes | `k8s-baseline` `20261116T192216Z` and `k8s-hostnet-rdma` `20261116T192258Z` |
| The Multus `net1` IP test passes across nodes | yes | `k8s-multus-net1` `20261116T192350Z` |
| ibsim shows the topology, LIDs, a link fault and its recovery | yes | B-01 to B-04 `20261116T194712Z` |
| ns-3 compares the four congestion profiles with each feature's activation confirmed | yes | `c-03` to `c-05` `20261116T200409Z`, `pfc-as-configured`, `ecn-as-configured` and `profiles-differ` included |
| Raw results, inputs and versions are kept, and no failure or unfinished run is hidden | yes | `make validate` and `make report STRICT=1` of `acceptance-all.sh` run `20261116T190004Z` |
| Cleanup, rollback and troubleshooting procedures exist | yes | The cleanup proofs of section 4, `docs/runbook.md` sections 8 to 10, `docs/troubleshooting.md` |
| Software measurement, IB management simulation and packet model results are kept apart | yes | `make report` groups by result kind, `docs/adr/0003-results-and-breaks.md` section 1 |

## 6. What has not been verified

- A hosted run of `.github/workflows/checks.yml` or of the site's Pages workflow.
- The VMware Workstation and Vagrant path. `docs/setup-vmware.md` keeps it as a manual procedure.
- The open experiments of `docs/runbook.md` section 14: a switch with PFC fully off, leaf-spine, a buffer sensitivity run, pod-private RDMA, NetBox, B-05, IPoIB and hardware.
