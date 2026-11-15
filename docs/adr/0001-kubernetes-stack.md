# ADR 0001 — 랩과 플릿이 함께 쓰는 Kubernetes 스택

> 상태: 채택, 2026-11-15

## 1. 결정

- Kubernetes는 Ansible과 kubeadm으로 설치한다.
- 랩(`ansible/inventories/lab`)과 가상 리전 cel1의 GPU 클러스터(`ansible/inventories/cel1`)는 같은 플레이북 `ansible/playbooks/kubernetes.yml`과 같은 역할 `containerd`, `kubeadm`을 쓴다. 차이는 인벤토리 변수에만 둔다.
- cel1은 실재하지 않는 데이터센터다. 컨트롤 플레인 3대, DGX B200 8대, 이전 세대 PCIe GPU 서버 2대로 이뤄진다. 이름과 주소는 모두 지어낸 값이다.
- 다른 사이트는 같은 계약을 지키는 인벤토리를 저장소 밖에 두고 같은 플레이북을 쓴다.
- Cilium을 비롯한 애드온은 Helm으로 설치한다. 릴리스 목록은 `helm/releases.yaml` 하나이고 `make addons ENV=<env>`가 그 순서대로 설치한다. 값 파일은 `helm/<release>/values-<env>.yaml`이다.
- 모든 버전은 `versions.lock.yaml`에서만 온다. 역할과 값 파일에 버전을 직접 쓰지 않는다.

## 2. 이유

- 최신 Kubernetes와 최신 Cilium을 쓴다.
- PLAN 1.1이 Kubernetes는 Ansible과 kubeadm, Cilium은 버전을 고정한 Helm chart로 정했다.
- 랩이 플릿과 같은 코드를 실행해야 랩의 검증이 플릿 코드의 검증이 된다. cel1에는 GPU 하드웨어가 없으므로 이것이 실행으로 확인하는 유일한 경로다.

## 3. 버전

| 구성 요소 | 잠금 키 | 값 |
|---|---|---|
| Kubernetes | `k8s.kubernetes` | v1.36.6 |
| containerd, runc, CNI plugins | `k8s.containerd`, `k8s.runc`, `k8s.cni_plugins` | 2.4.2, 1.5.3, v1.10.0 |
| Cilium chart | `cni.cilium_chart` | 1.20.4 |
| GPU Operator chart | `addons.gpu_operator_chart` | v26.7.2 |
| kube-vip | `k8s.kube_vip` | v1.3.1 |
| helm, kubeconform | `tooling.helm`, `tooling.kubeconform` | v4.3.2, v0.8.0 |

Kubernetes 최신은 v1.37.2다. Cilium 1.20의 호환 표는 1.33–1.36만 e2e 시험 대상으로 적는다. Cilium 개발 브랜치는 1.34–1.37을 시험한다. 그래서 v1.36의 최신 패치를 골랐고, Cilium 1.21이 나오면 둘을 함께 올린다.

## 4. 인벤토리 계약

플레이북과 역할은 아래 그룹과 변수만 읽는다. 두 인벤토리 모두 이 계약을 지킨다.

| 이름 | 종류 | 뜻 |
|---|---|---|
| `kube_control_plane` | 그룹 | 컨트롤 플레인. 첫 호스트가 `kubeadm init`을 실행한다 |
| `kube_node` | 그룹 | 워커 |
| `k8s_cluster` | 그룹 | 위 두 그룹의 합 |
| `rfl_inventory` | 문자열 | `lab` 또는 `cel1`. Helm 환경 이름이기도 하다. 플레이북은 이 값이 없는 호스트를 거부한다 |
| `k8s_node_ip` | 호스트별 IPv4 | kubelet이 등록하는 주소. 관리망 주소다 |
| `k8s_api_endpoint` | 주소 또는 이름 | kubeadm `controlPlaneEndpoint`의 호스트 부분 |
| `k8s_api_port` | 정수 | 기본 6443 |
| `k8s_api_vip` | IPv4 또는 없음 | 있으면 kube-vip이 컨트롤 플레인에서 이 주소를 광고한다. 컨트롤 플레인이 둘 이상이면 반드시 있어야 하고 `k8s_api_endpoint`와 같아야 한다 |
| `k8s_api_vip_interface` | 인터페이스 이름 | VIP를 광고할 NIC |
| `k8s_pod_cidr`, `k8s_service_cidr` | CIDR | kubeadm과 Cilium IPAM에 같은 값을 준다 |
| `k8s_kube_proxy` | 불리언 | 거짓이면 kubeadm이 kube-proxy를 설치하지 않고 Cilium이 대체한다 |
| `k8s_control_plane_schedulable` | 호스트별 불리언 | 기본 거짓. 참이면 그 컨트롤 플레인에 control-plane taint를 두지 않는다 |
| `k8s_node_labels` | 호스트별 맵 | 기본 빈 맵. kubelet이 등록할 때 붙이는 라벨이다. 하드웨어 종류와 GPU Operator 구성요소 선택에 쓴다. kubelet이 스스로 붙일 수 없는 `kubernetes.io`, `k8s.io` 접두사는 쓰지 않는다 |
| `k8s_container_runtime_default` | 호스트별 `runc` 또는 `nvidia` | 기본 `runc`. `nvidia`이면 containerd 설정에 `/usr/bin/nvidia-container-runtime`을 쓰는 nvidia 런타임을 넣고 기본 런타임으로 둔다. 툴킷이 미리 깔린 DGX OS 노드에 쓴다 |
| `k8s_admin_kubeconfig` | 제어 노드 경로 | kubeadm 역할이 admin kubeconfig를 권한 0600으로 가져다 두는 곳. 5절의 경로를 쓴다 |

랩은 PLAN A2에 따라 `k8s_kube_proxy: true`, 컨트롤 플레인 1대(`cp1`), VIP 없음으로 시작한다.

## 5. 실행 경계

- `make k8s`와 `make addons ENV=lab`은 랩 게스트만 바꾼다.
- cel1을 바꾸는 타깃은 `CONFIRM=cel1` 없이는 멈춘다. cel1의 주소는 지어낸 값이라 호스트에서 그 주소로 직접 접속하지 않는다. 시뮬레이션 게스트에는 127.0.0.1 포트 포워딩으로만 접속한다.
- kubeconfig는 git 추적 경로에 두지 않는다. 랩은 `.lab/kubeconfig`, cel1은 `${XDG_STATE_HOME:-~/.local/state}/rdma-fabric-lab/cel1/kubeconfig`다.

## 6. 하지 않는 것

- 이미 돌고 있는 클러스터를 이 코드로 옮기는 일.
- 클러스터 업그레이드 절차. 설치와 수렴만 다룬다.
- Network Operator와 IPoIB. PLAN 13의 하드웨어 프로파일에서 다룬다.
