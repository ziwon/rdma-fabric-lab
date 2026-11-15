# ADR 0002 — 트랙별 실행 환경

> 상태: 채택, 2026-11-15

## 1. 결정

| 대상 | 실행 환경 | 이유 |
|---|---|---|
| Track A (RXE, Kubernetes) | QEMU 게스트. Pod는 게스트 안 Kubernetes의 컨테이너 | RXE는 커널 장치다. 게스트마다 자기 커널과 RDMA 스택이 있어야 두 워커가 실제로 나뉜다. kubeadm 역할을 그대로 실행해야 cel1 코드를 증명한다 |
| cel1 시뮬레이션 | QEMU 게스트 | Track A와 같다 |
| Track B (ibsim, OpenSM) | 버전을 고정한 OCI 이미지. 전용 QEMU 게스트 안의 컨테이너 런타임에서 실행 | PLAN B0의 재현 가능한 단일 컨테이너를 유지하면서 호스트 커널과 분리한다 |
| Track C (ns-3) | Track B와 같다 | 같은 이유. 빌드와 실행 모두 게스트 안에서 한다 |
| 오프라인 검사(lint, 렌더링, kubeconform) | 호스트 프로세스 | 소켓을 쓰는 시뮬레이터가 없다 |

## 2. 이유

- 컨테이너는 호스트 커널을 함께 쓴다. 호스트 커널의 버그는 컨테이너 안에서 터져도 호스트 전체를 멈춘다.
- 2026-11-15에 실제로 일어났다. Ubuntu `7.0.0-42`, `7.0.0-43` 커널에서 AppArmor가 가둔 프로세스가 연결된 AF_UNIX 데이터그램 소켓에 `write()`하면 NULL 역참조가 난다. ibsim과 libumad2sim이 이 소켓을 쓴다. Docker는 모든 컨테이너를 `docker-default` 프로파일로 가둔다. 그래서 호스트 컨테이너 안의 `ibnetdiscover`와 `opensm`이 호스트를 세 번 멈췄다. 업스트림 수정은 "apparmor: fix NULL ctx->peer derefs in unix socket ctx updates"다.
- 게스트 안에서는 같은 버그가 나도 그 게스트만 멈춘다.
- 호스트 RDMA 네임스페이스 모드는 `shared`다. 컨테이너 둘로는 두 워커의 RDMA 장치를 나눌 수 없다.

## 3. 지키는 규칙

- 호스트에서 시뮬레이터 컨테이너를 띄우지 않는다. 수정이 들어간 커널을 VM에서 검증하고 호스트에 설치한 뒤에만 이 규칙을 다시 검토한다.
- Track B와 Track C의 make 타깃은 게스트 안에서만 실행한다. 호스트에서 직접 부르면 거부한다.
- 컨테이너는 privileged, host network, 추가 capability 없이 돈다. 게스트 안에서도 같다.

## 4. 후속: NetBox 연동

지금은 구현하지 않는다. 붙일 자리만 정해 둔다.

- 원본은 계속 `sites/cel1.yaml`이다. NetBox는 그 모델을 받아 보여 주고 대조하는 쪽으로 시작한다.
- 동기화 스크립트가 사이트 모델을 NetBox의 site, device, interface, cable, prefix, IP address로 옮긴다. InfiniBand 레일 배선도 cable로 옮긴다.
- 대조 검사가 NetBox와 사이트 모델의 차이를 보고한다. 차이가 있으면 실패한다.
- NetBox를 원본으로 바꿀지는 대조 검사가 안정된 뒤에 따로 정한다.
- NetBox 자체도 이 ADR의 규칙을 따라 게스트 안의 컨테이너로 띄운다.
