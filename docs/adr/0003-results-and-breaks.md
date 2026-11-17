# ADR 0003: Results, result kinds and deliberate breaks

> Status: accepted, 2026-11-15. The `deliberate_break` field and the report by latest run, accepted 2026-11-16. The kind `software-cluster-test`, accepted 2026-11-16.

## 1. Decision

1. **A spec declares every check.** Each scenario has `tracks/<track>/scenarios/<scenario>.yaml`, valid against `schemas/scenario.schema.json`. It names the track, whether the scenario is `required`, its `result_kind`, its security profile and every check. A unit test per track pins each spec's check list to the list its runner records.
2. **Only the checks set the status.** A runner records each check through `rfl run-check` and ends with `rfl run-finalize`. The status of a run follows from its checks in this order: failed, then blocked, then not_run, then passed. A passed check must cite non-empty evidence inside its run directory. A metric nobody measured is `null` with a reason, never `0`. `make validate` checks every run directory against `schemas/`.
3. **Four result kinds, never mixed.**

   | Kind | What it is | Scenarios |
   |---|---|---|
   | `software-rdma-measurement` | RDMA traffic over RXE, measured on this host | `vm-rxe-baseline`, `vm-rxe-reboot`, `vm-rxe-rerun`, `vm-environment`, `k8s-hostnet-rdma` |
   | `software-cluster-test` | The behavior of a cluster, with no RDMA figure | `k8s-baseline`, `k8s-multus-net1`, `k8s-ha-vip`, `cel1-sim-baseline` |
   | `ib-management-simulation` | What OpenSM and the diagnostic tools do against ibsim | `b-01` to `b-04` |
   | `packet-model-simulation` | What one packet model computes | `c-01-example` to `c-06-sensitivity-kmin` |

   A result of one kind never stands in for another. Track C metrics also carry `labels.domain`: `modeled-network` for figures the model computes, `simulator-cost` for the wall time and memory of the simulator process.
4. **Every check fails once on purpose.** Each check must fail on at least one deliberate break and pass after the repair. A break changes one thing, the run must end failed with the named checks failed, and after the repair the next run must pass. Track B removes one link from the net file the simulator gets while every expectation keeps the full topology. Track C applies one entry of the break table in `scripts/ns3/run.py`, and a unit test asserts the entries together target every check. Track A and cel1 break the environment from a script.
5. **A break run says so.** A manifest of `schema_version` 2 carries `deliberate_break`: null for an ordinary run, or the break's `name` and `description`. The Track B and Track C runners set it from their break tables. A script that breaks the environment before it calls a runner exports `RFL_BREAK` and `RFL_BREAK_WHAT`, which `rfl run-init` reads. Version 2 requires the field and version 1 forbids it, so every older run directory stays valid as it is. For version 1 the report reads `inputs.parameters.break_link` of Track B and `inputs.parameters.deliberate_break` of Track C.
6. **The report counts the latest real run.** `make report` shows, per result kind and with required and optional scenarios apart, the latest finished run of each scenario that is not a deliberate break, and counts the older, break and unfinished runs it left out. `make report STRICT=1` exits 1 unless every required scenario passed. An invalid run directory gives exit 4 in both forms.
7. **Results stay local.** `results/`, `.lab/` and the evidence copies under `.lab/evidence/` are never committed. Documents cite run ids and those paths. Test fixtures captured from real runs are tracked, and each fixture README names the run it came from.

## 2. Reasons

- A check that never failed may not be able to fail. A break proves it can, and the repair proves the failure came from the break.
- Listing every run directory mixes expected break failures, interrupted runs and old runs with the current answer. The report must say whether each required scenario passes now, without hiding a failure.
- Kubernetes behavior is not an RDMA measurement. `k8s-baseline`, `k8s-ha-vip` and `cel1-sim-baseline` recorded `software-rdma-measurement` until `software-cluster-test` existed. The runners now copy the kind of the spec into `metrics.json`, and the recorded runs keep the kind they ran with.
- Results and logs are large and carry host details. The run ids and paths are enough to find them on the machine that ran them.

## 3. Evidence

- In `acceptance-all.sh` run `20261116T190004Z`, `make validate` counted 363 run directories and 0 invalid, and `make report STRICT=1` exited 0 with 15 of 15 required and 4 of 4 optional scenarios passed. It left out 44 deliberate break runs, 19 unfinished runs and 281 older runs (`.lab/acceptance-all/20261116T190004Z/steps.tsv`). `docs/verification.md` lists the runs.
- Before the report change it listed 258 rows. On the same results the new report counted 10 of 15 required scenarios passed, because no Track C run existed in that checkout, and left out 8 break runs, 6 unfinished runs and 231 older runs (`.lab/evidence/m7/report-before-m7-runs.txt`).
- The hostNetwork RDMA and Multus breaks: 20 pairs covering all 16 checks of `k8s-hostnet-rdma` and all 14 of `k8s-multus-net1` (`.lab/evidence/m3/breaks-1/`, `breaks-2/`, `breaks-3/pairs.tsv`). In the full reproduction, Track B break run `20261116T194838Z` failed all four scenarios with the break `missing-link`, the 27 Track C breaks, runs `20261116T200636Z` to `20261116T201314Z`, each failed the checks it declares, and the cel1 break `rsyslog-stopped`, run `20261116T202754Z`, failed and was repaired in run `20261116T203232Z`. The first Track B and Track C break series are run `20261115T151828Z` and runs `20261116T151446Z` to `20261116T152133Z` (`.lab/evidence/m4/track-b-breaks.log`, `.lab/evidence/m6/break-details.tsv`).
- `tests/unit/test_rfl_manifest.py` covers the latest-run choice over an older failed run, a newer break and a newer unfinished run, the strict exit, exit 4, the environment variables, version 1 validity and version 2 strictness. Removing the break filter from `latest_runs` or the version 2 rule from the schema failed it.
