# M1 evidence

The files in this directory are copies of results from runs in the QEMU harness on this host on 2026-11-15. The original result directories remain in the gitignored `results/` and `.lab/`. No value was made by hand.

## What changed in the copy

- `environment.host.name` in `manifest.json` became `"<redacted>"`. The original value is the name of this host and contains a person's name. The copy script confirmed that the changed manifest still satisfies `schemas/run-manifest.schema.json`.
- The absolute repository path in `probe.log`, `probe-fault.txt` and `acceptance-*.tsv` became `<repo>`.

All other bytes match the originals. The copies have no `raw/`, so `rfl validate` cannot check the evidence files of the copies. The contract check ran with `make validate` against the original directories.

## Files

| File | Original |
|---|---|
| `probe.log` | `.lab/probe/20261115T012426Z/probe.log`. M0 probe |
| `probe-fault.txt` | `.lab/probe/20261115T012426Z/fault.txt`. Link fault observations with the address set by hand |
| `acceptance-steps.tsv` | `.lab/acceptance/20261115T022117Z/steps.tsv`. Expected and actual exit codes of the 41 acceptance test steps |
| `acceptance-runs.tsv` | `.lab/acceptance/20261115T022117Z/runs.tsv`. The mapping of steps to run ids |
| `<run-id>/a-soft-roce/<scenario>/` | `manifest.json`, `metrics.json` and `summary.md` from `results/<run-id>/a-soft-roce/<scenario>/` |

## Runs

| Run id | Scenario | Status | Condition |
|---|---|---|---|
| `20261115T022240Z` | vm-rxe-baseline | passed | Right after a fresh build on wiped guests |
| `20261115T022311Z` | vm-rxe-rerun | passed | bootstrap and rxe-up rerun |
| `20261115T022327Z` | vm-rxe-reboot | passed | Both workers rebooted |
| `20261115T022358Z` | vm-rxe-reboot | failed | Check break: reboot after disabling `rfl-rxe.service` |
| `20261115T022449Z` | vm-rxe-baseline | failed | worker1 lab NIC `ip link set down` |
| `20261115T022521Z` | vm-rxe-baseline | passed | After the link recovered |
| `20261115T022539Z` | vm-rxe-baseline | failed | worker1 lab NIC `netem loss 100%` |
| `20261115T022833Z` | vm-rxe-baseline | passed | After the qdisc was removed |
| `20261115T022855Z` | vm-rxe-baseline | not_run | The run whose runner was killed with SIGKILL. The target of `make cleanup` |
| `20261115T022919Z` | vm-environment | passed | `make collect` |
| `20261115T023334Z` | vm-rxe-baseline | passed | After `make reset-lab CONFIRM=lab` and recovery with bootstrap and rxe-up |
