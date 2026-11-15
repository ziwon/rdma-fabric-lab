# M1 증거

이 디렉터리의 파일은 2026-11-15에 이 호스트의 QEMU 하네스에서 실행한 결과를 복사한 것이다. 원본 결과 디렉터리는 gitignore 대상인 `results/`와 `.lab/`에 남아 있다. 손으로 만든 값은 없다.

## 복사할 때 바꾼 것

- `manifest.json`의 `environment.host.name`을 `"<redacted>"`로 바꿨다. 원래 값은 이 호스트의 이름이고 사람 이름을 담고 있다. 바꾼 manifest가 `schemas/run-manifest.schema.json`을 여전히 만족하는지 복사 스크립트에서 확인했다.
- `probe.log`, `probe-fault.txt`, `acceptance-*.tsv`에 나오는 저장소 절대 경로를 `<repo>`로 바꿨다.

그 밖의 바이트는 원본과 같다. 복사본에는 `raw/`가 없으므로 `rfl validate`는 복사본의 증거 파일을 확인할 수 없다. 계약 검사는 원본 디렉터리에 대해 `make validate`로 수행했다.

## 파일

| 파일 | 원본 |
|---|---|
| `probe.log` | `.lab/probe/20261115T012426Z/probe.log`. M0 probe |
| `probe-fault.txt` | `.lab/probe/20261115T012426Z/fault.txt`. 주소를 손으로 넣었을 때의 링크 장애 관찰 |
| `acceptance-steps.tsv` | `.lab/acceptance/20261115T022117Z/steps.tsv`. 인수 시험 41단계의 기대·실제 종료 코드 |
| `acceptance-runs.tsv` | `.lab/acceptance/20261115T022117Z/runs.tsv`. 단계와 run id의 대응 |
| `<run-id>/a-soft-roce/<scenario>/` | `results/<run-id>/a-soft-roce/<scenario>/`의 `manifest.json`, `metrics.json`, `summary.md` |

## run

| run id | scenario | 상태 | 조건 |
|---|---|---|---|
| `20261115T022240Z` | vm-rxe-baseline | passed | 지운 게스트에서 새로 만든 직후 |
| `20261115T022311Z` | vm-rxe-rerun | passed | bootstrap·rxe-up 재실행 |
| `20261115T022327Z` | vm-rxe-reboot | passed | 두 worker 재부팅 |
| `20261115T022358Z` | vm-rxe-reboot | failed | 검사 깨기: `rfl-rxe.service`를 disable한 뒤 재부팅 |
| `20261115T022449Z` | vm-rxe-baseline | failed | worker1 실습 NIC `ip link set down` |
| `20261115T022521Z` | vm-rxe-baseline | passed | 링크 복구 후 |
| `20261115T022539Z` | vm-rxe-baseline | failed | worker1 실습 NIC `netem loss 100%` |
| `20261115T022833Z` | vm-rxe-baseline | passed | qdisc 제거 후 |
| `20261115T022855Z` | vm-rxe-baseline | not_run | 러너를 SIGKILL로 죽인 run. `make cleanup`의 대상 |
| `20261115T022919Z` | vm-environment | passed | `make collect` |
| `20261115T023334Z` | vm-rxe-baseline | passed | `make reset-lab CONFIRM=lab` 뒤 bootstrap·rxe-up으로 복구한 후 |
