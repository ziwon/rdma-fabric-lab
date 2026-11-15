# M2 증거

이 디렉터리의 파일은 2026-11-15에 이 호스트의 QEMU 하네스에서 실행한 결과를 복사한 것이다. 원본은 gitignore 대상인 `results/`와 `.lab/`에 남아 있다. 손으로 만든 값은 없다.

## 복사할 때 바꾼 것

- 저장소 절대 경로를 `<repo>`로, 홈 디렉터리를 `<home>`으로 바꿨다. 복사 스크립트가 복사본에 사용자 이름과 호스트 경로가 남지 않았는지 확인했다.
- `*-recap.txt`, `*-failure.log`처럼 이름에 recap, failure가 붙은 파일과 `acceptance-m2-addons-again.log`, `cilium-rootcause/helm-upgrade-clusterip.log`, `cilium-rootcause/restore-addons.log`는 원본 로그의 끝부분 또는 해당 태스크 구간만 옮겼다.

복사본에는 `raw/`가 없으므로 `rfl validate`는 복사본의 증거 파일을 확인할 수 없다. 계약 검사는 원본 디렉터리에 대해 `make validate`로 수행했다.

## 파일

| 파일 | 원본 |
|---|---|
| `m1-regression-failed-steps.tsv`, `m1-regression-bootstrap-failure.log` | `.lab/acceptance/20261115T041815Z/`. ansible-core 2.21.6의 첫 M1 회귀 |
| `m1-regression-{steps,runs}.tsv` | `.lab/acceptance/20261115T042645Z/`. 수정 후 회귀, 41단계 |
| `m1-new-harness-{steps,runs}.tsv` | `.lab/acceptance/20261115T061131Z/`. 세 게스트 하네스에서 다시 돌린 M1 인수 시험 |
| `acceptance-m2-{steps,runs}.tsv`와 `acceptance-m2-*.log` | `.lab/acceptance-m2/20261115T060116Z/`. M2 인수 시험 29단계 |
| `acceptance-m2-first-steps.tsv`, `acceptance-m2-first-rollout-timeout.log` | `.lab/acceptance-m2/20261115T053321Z/`. Cilium 엔드포인트를 고치기 전의 첫 실행 |
| `acceptance-ha-*` | `.lab/acceptance-ha/20261115T060117Z/`. ha 프로필 11단계 |
| `cilium-rootcause/` | `.lab/m2/cilium-rootcause/`. ha 프로필에서 Cilium을 ClusterIP 경로로 되돌려 재현한 기록과 복구 |
| `nvidia-probe/` | `.lab/m2/nvidia-probe/`. ha-w1의 nvidia 기본 런타임과 되돌림 |
| `helm-null-probe.txt` | `.lab/m2/helm-null-probe.txt`. 뒤 값 파일의 null이 저장된 값에 남는지 실제 릴리스로 확인 |
| `cleanup-before.txt`, `cleanup-after.log` | `.lab/m2/`. SIGKILL로 죽인 `k8s-baseline` run의 namespace와 `make cleanup` |
| `ha-upload-certs-failure.log` | `.lab/m2/ha-k8s-1.log`의 `upload-certs` 태스크 구간 |
| `<run-id>/a-soft-roce/<scenario>/` | `results/<run-id>/a-soft-roce/<scenario>/`의 `manifest.json`, `metrics.json`, `summary.md` |

## run

| run id | scenario | 상태 | 조건 |
|---|---|---|---|
| `20261115T042827Z` | vm-rxe-baseline | passed | ansible-core 2.21.6 M1 회귀, 두 worker 하네스 |
| `20261115T061313Z`, `20261115T061357Z`, `20261115T061417Z` | vm-rxe-baseline, -rerun, -reboot | passed | 세 게스트 하네스의 M1 인수 시험 |
| `20261115T053843Z` | k8s-baseline | failed | 첫 M2 인수 시험. worker1의 재시작한 agent가 API에 닿지 못했다 |
| `20261115T055930Z` | k8s-baseline | passed | `k8sServiceHostRef`를 적용한 직후 같은 클러스터 |
| `20261115T060520Z` | k8s-baseline | passed | M2 인수 시험, 지운 게스트에서 새로 만든 클러스터 |
| `20261115T060642Z` | vm-rxe-baseline | passed | 같은 클러스터, Kubernetes와 Cilium이 올라간 worker |
| `20261115T060709Z` | k8s-baseline | failed | worker1의 Cilium agent pod를 지운 직후 |
| `20261115T060758Z` | k8s-baseline | passed | agent가 다시 준비된 뒤 |
| `20261115T060838Z` | k8s-baseline | failed | worker1 관리 NIC에서 UDP 8472 DROP |
| `20261115T061038Z` | k8s-baseline | passed | DROP 규칙 제거 뒤 |
| `20261115T052346Z` | k8s-ha-vip | failed | 검사 깨기. 살아 있는 두 컨트롤 플레인의 kube-vip manifest를 치웠다 |
| `20261115T052745Z` | k8s-ha-vip | passed | `make k8s`가 manifest를 다시 쓴 뒤 |
| `20261115T060557Z` | k8s-ha-vip | passed | ha 인수 시험, 지운 게스트에서 새로 만든 클러스터 |
