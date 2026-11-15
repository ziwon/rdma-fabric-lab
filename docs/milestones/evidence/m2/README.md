# M2 evidence

The files in this directory are copies of results from runs in the QEMU harness on this host on 2026-11-15. The originals remain in the gitignored `results/` and `.lab/`. No value was made by hand.

## What changed in the copy

- The absolute repository path became `<repo>` and the home directory became `<home>`. The copy script confirmed that no user name or host path remains in the copies.
- Files with recap or failure in the name, such as `*-recap.txt` and `*-failure.log`, and `acceptance-m2-addons-again.log`, `cilium-rootcause/helm-upgrade-clusterip.log` and `cilium-rootcause/restore-addons.log` hold only the tail of the original log or the section of the relevant task.

The copies have no `raw/`, so `rfl validate` cannot check the evidence files of the copies. The contract check ran with `make validate` against the original directories.

## Files

| File | Original |
|---|---|
| `m1-regression-failed-steps.tsv`, `m1-regression-bootstrap-failure.log` | `.lab/acceptance/20261115T041815Z/`. The first M1 regression on ansible-core 2.21.6 |
| `m1-regression-{steps,runs}.tsv` | `.lab/acceptance/20261115T042645Z/`. The regression after the fix, 41 steps |
| `m1-new-harness-{steps,runs}.tsv` | `.lab/acceptance/20261115T061131Z/`. The M1 acceptance test rerun on the three-guest harness |
| `acceptance-m2-{steps,runs}.tsv` and `acceptance-m2-*.log` | `.lab/acceptance-m2/20261115T060116Z/`. The 29 steps of the M2 acceptance test |
| `acceptance-m2-first-steps.tsv`, `acceptance-m2-first-rollout-timeout.log` | `.lab/acceptance-m2/20261115T053321Z/`. The first run, before the Cilium endpoint fix |
| `acceptance-ha-*` | `.lab/acceptance-ha/20261115T060117Z/`. The 11 steps of the ha profile |
| `cilium-rootcause/` | `.lab/m2/cilium-rootcause/`. The record of the reproduction on the ha profile with Cilium switched back to the ClusterIP path, and the recovery |
| `nvidia-probe/` | `.lab/m2/nvidia-probe/`. The nvidia default runtime on ha-w1 and its revert |
| `helm-null-probe.txt` | `.lab/m2/helm-null-probe.txt`. A check with a real release of whether a null in a later values file stays in the stored values |
| `cleanup-before.txt`, `cleanup-after.log` | `.lab/m2/`. The namespace of a `k8s-baseline` run killed with SIGKILL, and `make cleanup` |
| `ha-upload-certs-failure.log` | The `upload-certs` task section of `.lab/m2/ha-k8s-1.log` |
| `<run-id>/a-soft-roce/<scenario>/` | `manifest.json`, `metrics.json` and `summary.md` from `results/<run-id>/a-soft-roce/<scenario>/` |

## Runs

| Run id | Scenario | Status | Condition |
|---|---|---|---|
| `20261115T042827Z` | vm-rxe-baseline | passed | M1 regression on ansible-core 2.21.6, two-worker harness |
| `20261115T061313Z`, `20261115T061357Z`, `20261115T061417Z` | vm-rxe-baseline, -rerun, -reboot | passed | M1 acceptance test on the three-guest harness |
| `20261115T053843Z` | k8s-baseline | failed | First M2 acceptance test. The restarted agent on worker1 could not reach the API |
| `20261115T055930Z` | k8s-baseline | passed | The same cluster right after `k8sServiceHostRef` was applied |
| `20261115T060520Z` | k8s-baseline | passed | M2 acceptance test, a cluster built fresh on wiped guests |
| `20261115T060642Z` | vm-rxe-baseline | passed | The same cluster, workers with Kubernetes and Cilium running |
| `20261115T060709Z` | k8s-baseline | failed | Right after the Cilium agent pod on worker1 was deleted |
| `20261115T060758Z` | k8s-baseline | passed | After the agent was ready again |
| `20261115T060838Z` | k8s-baseline | failed | UDP 8472 DROP on the worker1 management NIC |
| `20261115T061038Z` | k8s-baseline | passed | After the DROP rule was removed |
| `20261115T052346Z` | k8s-ha-vip | failed | Check break. The kube-vip manifests of the two surviving control planes were removed |
| `20261115T052745Z` | k8s-ha-vip | passed | After `make k8s` rewrote the manifests |
| `20261115T060557Z` | k8s-ha-vip | passed | ha acceptance test, a cluster built fresh on wiped guests |
