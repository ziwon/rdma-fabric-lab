# cel1 — 가상 GPU 리전 운영 문서

> 작성일: 2026-11-15  
> 상태: 사이트 모델, 인벤토리 생성, 계약 검사, Helm 렌더링은 오프라인으로 실행해 확인했다. cel1 호스트를 바꾸는 타깃은 한 번도 실행하지 않았다(8절).  
> 근거: `docs/adr/0001-kubernetes-stack.md`. 4절이 인벤토리 계약이다.

## 1. 리전

cel1은 실재하지 않는 리전이다. 호스트 이름, 주소, 인터페이스 이름, 스위치 이름은 모두 새로 지었다.

| 클래스 | 호스트 | 수 | OS | NVIDIA 드라이버와 컨테이너 툴킷 | 포트 |
|---|---|---:|---|---|---|
| `control_plane` | `cel1-cp-01`–`03` | 3 | Ubuntu 24.04 | 없음 | `mgmt0` |
| `dgx_b200` | `cel1-dgx-01`–`08` | 8 | DGX OS 7 | DGX OS에 설치되어 있다 | `mgmt0`, `stor0`, `stor1`, `rail0`–`rail7` |
| `pcie_gpu` | `cel1-pcie-01`–`02` | 2 | Ubuntu 24.04 | GPU Operator가 설치한다 | `mgmt0` |

DGX는 64대가 아니라 8대다. 이 호스트가 그 이상을 시뮬레이션할 수 없다고 사용자가 정했다. 다음 작업이 cel1을 랩 하네스의 작은 QEMU 게스트로 부팅하고 실제 플레이북을 실행한다. 이 문서의 설계는 모두 그 부팅을 전제로 한다.

## 2. 사이트 모델

`sites/cel1.yaml` 하나가 리전의 단일 원천이다. 호스트와 클래스, 주소 계획, API 주소, syslog 주소, InfiniBand 컴퓨트 패브릭이 모두 여기에 있다. 형식은 `scripts/fleet/site.py`의 docstring이 정의한다.

### 2.1 주소 계획

| 용도 | 범위 | 할당 |
|---|---|---|
| 인밴드 관리망 `management` | `10.140.0.0/24` | API VIP `.10`, syslog `.5`, 컨트롤 플레인 `.11`–`.13`, DGX `.21`–`.28`, PCIe GPU 서버 `.31`–`.32` |
| 스토리지 `storage-a`, `storage-b` | `10.141.0.0/24`, `10.141.1.0/24` | DGX `.21`–`.28` |
| 컴퓨트 레일 `rail0`–`rail7` | `10.142.0.0/24`–`10.142.7.0/24` | DGX `.21`–`.28` |
| Pod | `10.160.0.0/16` | kube-controller-manager가 노드별 `/24`를 나눈다 |
| Service | `10.170.0.0/16` | |

각 범위는 하나의 L2 세그먼트다. 범위끼리, 랩 주소 계획(`ansible/inventories/lab/group_vars/all/lab.yml`의 `lab_address_plan`)과 겹치지 않는다. 검증기가 매번 이 두 가지를 확인한다.

호스트마다 주소를 모두 적는다. 호스트 번호로 주소를 계산하는 규칙을 쓰지 않는다. 주소를 명시해야 중복 검사와 범위 검사가 실제로 일을 한다.

### 2.2 인터페이스 이름

포트 이름은 systemd `.link` 파일이 바꿔 붙이는 이름이다. 하드웨어에서는 PCI 경로나 MAC으로, QEMU 게스트에서는 virtio NIC의 MAC으로 매칭한다. 그래서 같은 인벤토리가 두 환경에서 같다.

검증기는 15자를 넘거나 소문자와 숫자 밖의 문자를 쓰는 이름을 거부한다. `eth`, `en`, `ib`, `wl`, `ww`, `usb`, `sl`로 시작하는 이름도 거부한다. systemd.link(5)는 커널이 직접 붙일 수 있는 이름으로 바꾸면 커널과 경쟁한다고 경고한다. 나머지는 udev 예측 이름의 접두사라서 혼동을 만든다.

### 2.3 컴퓨트 패브릭

`fabrics.compute`는 레일 최적화 InfiniBand 패브릭이다.

| 항목 | 값 |
|---|---|
| 스위치 모델 | `ndr-64`, 포트 64개, 링크 4x NDR |
| 리프 | `cel1-ib-leaf-r0`–`r7`. 레일 하나에 리프 하나 |
| 스파인 | `cel1-ib-spine-01`–`04` |
| 다운링크 | DGX n의 `rail r` → 리프 `r`의 포트 n. 리프마다 8개 |
| 업링크 | 리프 포트 33–40. 스파인마다 2개. 스파인 포트는 `1 + 2r`, `2 + 2r` |
| 링크 수 | 레일 64개, 리프–스파인 64개, 합계 128개 |

리프의 다운링크 8개와 업링크 8개가 같으므로 리프에서 과다 구독이 없다. 링크는 모두 명시한 케이블 목록이다. 규칙으로 생성하지 않으므로 검증기가 실제 배선 계획을 검사한다.

M4는 이 절에서 ibsim 토폴로지를 만든다. ibsim(`ibsim/sim_net.c`)이 읽는 것은 노드 종류와 포트 수, 고유한 노드 이름, 양쪽 포트 번호를 가진 링크, 선택 사항인 링크 폭과 속도다. 모델에는 그것이 모두 있다. HCA는 레일마다 포트가 하나인 `mlx5_<r>`이다. ibsim 파일은 아직 쓰지 않았다.

### 2.4 검증기

`make fleet-site-check`가 모델을 검증한다. 모양이 틀리면 파싱 단계에서 거부한다. 같은 키가 파일에 두 번 나오는 경우도 거부한다. PyYAML은 뒤의 값을 조용히 남기기 때문이다. 모양이 맞으면 다음 위반을 각각 이름으로 보고한다.

- 인터페이스 이름 규칙 위반, 알 수 없는 네트워크·클래스·스위치 모델·노드
- 노드 네트워크에 인터페이스가 정확히 하나가 아닌 클래스, 클래스와 맞지 않는 호스트 주소
- 호스트와 스위치가 같은 이름을 쓰는 경우, Ansible 그룹 이름이 될 수 없는 클래스 이름, RFC 1123 라벨이 아닌 호스트 이름
- 겹치는 범위, 랩 계획과 겹치는 범위, 네트워크 밖 주소, 중복 주소, 노드 네트워크 밖의 VIP
- 컨트롤 플레인이 없거나 컨트롤 플레인 클래스마다 VIP 인터페이스 이름이 다른 경우
- 레일을 갖지 않은 리프나 레일을 가진 스파인, 포트 범위(`1`–라딕스) 밖의 포트, 숫자가 아닌 스위치 포트
- 다른 레일 리프에 꽂힌 레일 포트, 패브릭에 꽂힌 관리 포트, 레일–스파인과 리프–리프 링크
- 두 번 쓰인 포트, 케이블이 없는 레일 포트, 어떤 스파인에도 업링크가 없는 리프

`tests/unit/test_fleet_site.py`가 위반마다 실패하는 테스트를 하나씩 둔다.

## 3. 생성기

`make fleet-render`가 `sites/cel1.yaml`에서 `ansible/inventories/cel1/`을 쓴다. 손으로 고치지 않는다.

| 파일 | 내용 |
|---|---|
| `hosts.yml` | `k8s_cluster` 아래 `kube_control_plane`, `kube_node`. 클래스마다 그룹 하나 |
| `group_vars/all.yml` | `rfl_inventory`, API 주소·포트·VIP·VIP 인터페이스, Pod·Service CIDR, `k8s_kube_proxy`, `k8s_control_plane_schedulable`, syslog 주소와 포트 |
| `group_vars/<class>.yml` | 그 클래스의 `k8s_node_labels` |
| `host_vars/<host>.yml` | `k8s_node_ip` |

계약 그룹은 호스트를 직접 나열한다. `groups['kube_control_plane'][0]`이 `kubeadm init`을 실행하므로 그 순서가 자식 그룹 탐색 순서에 기대면 안 된다. 클래스 그룹은 같은 호스트를 다시 나열한다. `PYTHONHASHSEED` 0, 1, 7, 42, 999에서 `ansible-inventory --list` 출력의 sha256이 같았다. `ansible-playbook --list-hosts`의 호스트 순서는 실행마다 다르지만 CLI가 출력용으로 `set`을 쓰기 때문이다(`ansible/cli/playbook.py`). `groups['k8s_cluster']`는 6회 실행에서 같았다.

인벤토리는 `ansible_host`를 두지 않는다. cel1 주소는 지어낸 값이라 호스트에서 접속하면 안 된다. 접속 방법은 두 번째 인벤토리(`FLEET_CONNECT`)가 준다. 시뮬레이션은 127.0.0.1 포트 포워딩을 적는다. 두 번째 인벤토리 없이 cel1 인벤토리로 플레이북을 실행하려 하면 make가 ssh 전에 멈춘다.

생성기는 같은 모델에서 같은 바이트를 낸다. `make fleet-render-check`가 디렉터리와 렌더링 결과를 비교하고 다르면 실패한다. 호스트가 모델에서 빠지면 그 호스트의 예전 파일을 지운다. 생성기가 쓰지 않은 파일은 지우지 않고 보고한다.

## 4. 인벤토리 계약 검사

`make inventory-check INVENTORY=<lab|cel1|dir>`가 `ansible-inventory --list` 출력을 ADR 4절과 비교한다. 호스트에 접속하지 않는다. 값이 정확히 `{{ other_variable }}`이면 같은 호스트의 변수로 푼다. 그 밖의 템플릿은 오프라인으로 평가할 수 없다고 보고한다.

| 코드 | 뜻 |
|---|---|
| `missing-group` | 계약 그룹이 없거나 `kube_control_plane`이 비었다 |
| `group-membership` | 호스트가 두 역할에 모두 있거나 `k8s_cluster`가 두 그룹의 합이 아니다 |
| `missing-variable` | 호스트에 필요한 변수가 없다. `k8s_node_ip`가 없는 호스트가 여기에 걸린다 |
| `bad-value` | 형식이 틀렸다. 예를 들어 `k8s_kube_proxy: "false"`는 문자열이다 |
| `unresolved-template` | 오프라인으로 평가할 수 없는 템플릿이다 |
| `inconsistent-variable` | 클러스터 공통 변수가 호스트마다 다르다 |
| `duplicate-node-ip` | 두 호스트가 같은 `k8s_node_ip`를 쓴다 |
| `range-overlap` | Pod와 Service 범위가 겹치거나 노드 주소나 VIP가 그 안에 있다 |
| `missing-vip` | 컨트롤 플레인이 둘 이상인데 `k8s_api_vip`가 없다 |
| `vip-endpoint-mismatch` | `k8s_api_vip`와 `k8s_api_endpoint`가 다르다 |
| `unknown-environment` | `rfl_inventory`가 `helm/releases.yaml`의 환경이 아니다 |
| `reserved-label-prefix` | 라벨이 `kubernetes.io`나 `k8s.io` 또는 그 하위 도메인을 쓴다 |
| `invalid-label` | 라벨 키나 값이 Kubernetes 문법에 맞지 않거나 값이 문자열이 아니다 |
| `cilium-values`, `cilium-mismatch` | Cilium 값 파일이 없거나 인벤토리와 다르다 |

`invalid-label`은 문자열이 아닌 값을 잡는다. YAML의 `false`는 불리언이고 kubelet에는 `False`로 전달된다. GPU Operator는 `"false"`만 알아듣는다.

`cilium-mismatch`는 ADR이 Cilium과 kubeadm에 같은 값을 주라고 한 부분을 확인한다. kube-proxy와 kube-proxy 대체가 둘 다 켜지거나 둘 다 꺼진 경우, kube-proxy 대체에서 `k8sServiceHost`와 `k8sServicePort`가 API 주소와 포트가 아닌 경우, cluster-pool IPAM 범위가 `k8s_pod_cidr`가 아닌 경우, `ipam.mode`가 없어서 chart 기본값인 cluster-pool이 되는 경우, native routing의 `ipv4NativeRoutingCIDR`가 Pod 범위를 덮지 않는 경우다.

랩은 ADR이 정한 기본값(`k8s_api_port` 6443, VIP 없음, `k8s_control_plane_schedulable` 거짓, `k8s_node_labels` 빈 맵)에 기댈 수 있다. 랩 밖의 인벤토리는 `--explicit`로 검사하고 모든 계약 변수가 모든 호스트에 있어야 한다. `tests/unit/test_inventory_contract.py`가 위반마다 실패하는 테스트를 둔다. 테스트의 입력은 cel1 인벤토리에서 실제로 받은 `ansible-inventory --list` 출력이다(`tests/fixtures/fleet/README.md`).

## 5. Cilium

`helm/cilium/values-cel1.yaml`은 chart 1.20.4용이다.

| 키 | 값 | 이유 |
|---|---|---|
| `kubeProxyReplacement` | `true` | cel1은 `k8s_kube_proxy: false`다. kubeadm이 kube-proxy를 설치하지 않는다 |
| `k8sServiceHost`, `k8sServicePort` | `10.140.0.10`, `6443` | kube-proxy가 없으면 Service가 생기기 전에 API에 닿아야 한다. kube-vip VIP다 |
| `routingMode`, `autoDirectNodeRoutes` | `native`, `true` | 아래 설명 |
| `ipv4NativeRoutingCIDR` | `10.160.0.0/16` | Pod 범위. 이 범위로 가는 패킷은 SNAT하지 않는다 |
| `ipam.mode` | `kubernetes` | 노드 Pod CIDR은 kubeadm의 `podSubnet`에서 온다. chart 기본값 cluster-pool은 `10.0.0.0/8`을 쓴다 |
| `devices` | `mgmt0` | eBPF 데이터 경로를 관리 포트에만 붙인다 |

**라우팅 모드는 native다.** 모든 노드가 하나의 관리 L2 세그먼트에 있다. 그러면 `autoDirectNodeRoutes`가 각 노드에 다른 노드의 Pod CIDR로 가는 경로를 그 노드의 관리 주소로 넣는다. 라우터가 Pod CIDR을 알 필요가 없다. Cilium 1.20 문서는 모든 노드가 하나의 L2 네트워크를 공유하면 이 옵션으로 충분하다고 적는다(https://docs.cilium.io/en/v1.20/network/concepts/routing/). 캡슐화가 없으므로 Pod MTU는 1500 그대로다. VXLAN은 50바이트를 쓴다. 회선의 패킷에 실제 Pod 주소가 보이므로 tcpdump로 추적하기 쉽다. 대신 관리망이 여러 L3 세그먼트로 나뉘면 이 방식은 작동하지 않는다. 그때는 터널 모드나 BGP로 바꾼다. 랩은 PLAN A2에 따라 VXLAN 터널을 쓴다. 따라서 native routing은 랩이 증명하지 않는다. cel1 시뮬레이션이 증명한다.

**Pod 트래픽은 관리망으로만 흐른다.** `k8s_node_ip`가 관리 주소이고, 자동 직접 경로는 그 주소를 다음 홉으로 쓰고, `devices`가 레일과 스토리지 포트를 제외한다. InfiniBand 레일은 RDMA 전용으로 남는다.

## 6. GPU Operator

`helm/gpu-operator/values-cel1.yaml`은 chart v26.7.2용이다. chart 수준에서 드라이버와 툴킷을 켠다. 노드마다 라벨로 끈다.

| 클래스 | `k8s_node_labels` | 결과 |
|---|---|---|
| `dgx_b200` | `nvidia.com/gpu.deploy.driver: "false"`, `nvidia.com/gpu.deploy.container-toolkit: "false"` | DGX OS의 드라이버와 툴킷을 쓴다 |
| `pcie_gpu` | 없음 | Operator가 드라이버와 툴킷을 설치한다 |
| `control_plane` | 없음 | GPU가 없어 NFD가 `pci-10de` 라벨을 붙이지 않는다. Operator가 아무것도 배치하지 않는다 |

kubelet이 등록할 때 라벨을 붙인다. kubelet은 `--node-labels`를 처음 등록할 때만 적용한다. 등록 후 바꾸려면 `kubectl label`로 바꿔야 한다.

근거는 다음과 같다.

- 문서 "Preventing Installation of NVIDIA GPU Driver on Some Nodes"와 "Preventing Installation of Operands on Some Nodes". https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html 이 페이지는 2026-11-15에 현재 패치 릴리스를 v26.7.2로 적는다. 버전 경로(`/26.7.2/`, `/v26.7.2/`)는 404였다.
- 툴킷 라벨은 문서에 노드별 절이 없다. 그래서 소스로 확인했다. github.com/NVIDIA/gpu-operator 태그 v26.7.2(커밋 `5a9c8a8b3f5b`)의 `controllers/state_manager.go` `addGPUStateLabels`는 노드에 없거나 빈 `nvidia.com/gpu.deploy.*` 라벨만 `"true"`로 채운다. 이미 있는 `"false"`는 덮어쓰지 않는다. `assets/state-driver/0500_daemonset.yaml`과 `assets/state-container-toolkit/0500_daemonset.yaml`의 nodeSelector는 각각 `nvidia.com/gpu.deploy.driver: "true"`, `nvidia.com/gpu.deploy.container-toolkit: "true"`다.
- 같은 문서의 "Pre-Installed NVIDIA GPU Drivers and NVIDIA Container Toolkit" 절은 DGX에 해당한다고 적고 Operator 설치 전에 기본 런타임을 `nvidia`로 두라고 한다. 이 저장소의 어떤 역할도 아직 그 설정을 하지 않는다(10절).

PCIe GPU 서버의 툴킷은 런타임 설정을 drop-in `/etc/containerd/conf.d/99-nvidia.toml`에 쓴다(`controllers/object_controls.go`의 `DefaultContainerdDropInConfigFile`). 같은 소스의 주석은 containerd drop-in에 최상위 설정 수정이 필요하다고 적는다. `containerd` 역할의 `config.toml`은 이미 `/etc/containerd/conf.d/*.toml`을 import한다. 그래서 툴킷이 최상위 파일을 바꾸지 않고 Ansible 재실행도 `changed=0`일 것으로 추정한다. 확인하지 않았다. GPU가 있는 PCIe GPU 서버에서만 확인할 수 있다.

## 7. rsyslog

사이트 모델의 `services.syslog`가 중앙 syslog 주소 `10.140.0.5:514/tcp`다. 생성기가 `rsyslog_client_tcp_host`와 `rsyslog_client_tcp_port`로 옮긴다.

**`rsyslog_client`는 클러스터의 모든 노드에서 실행한다.** 컨트롤 플레인도 포함한다. GPU 작업이 실패한 원인은 어느 노드의 kubelet, containerd, 커널 메시지에도 있을 수 있다. 호스트별로 끄는 변수는 없다. 이전의 `kube_enable_rsyslog_client` 토글은 지웠다.

- 역할은 syslog 주소가 없으면 아무것도 바꾸지 않고 실패한다. 예전처럼 빈 파일을 쓰고 조용히 지나가지 않는다.
- 전달은 디스크 보조 큐를 쓴다. 서버가 내려가 있는 동안 메시지를 쌓아 두었다가 다시 보낸다. Ubuntu 24.04의 `/etc/rsyslog.conf`가 `$WorkDirectory /var/spool/rsyslog`를 정한다.
- 플레이북 `ansible/playbooks/fleet/rsyslog-client.yml`은 `rfl_inventory`가 없거나 `lab`인 호스트를 거부한다.
- 렌더링한 설정 파일을 Ubuntu 24.04 컨테이너(rsyslog 8.2312.0-3ubuntu9.4)의 `/etc/rsyslog.d/`에 넣고 `rsyslogd -N1`을 실행했다. 종료 코드 0이었다. 오타를 넣은 파일은 `parameter 'Prot' not known`으로 실패했다. Ansible `validate:`는 쓰지 않는다. Ubuntu의 AppArmor 프로필이 rsyslogd에 `/etc/rsyslog.d/` 밖 파일 읽기를 막기 때문이다. 호스트에서 임시 경로의 파일로 실행하면 `Permission denied`였다.

## 8. Make 타깃

| 타깃 | 동작 | 호스트 접속 |
|---|---|---|
| `make fleet-site-check` | 사이트 모델 검증 | 없음 |
| `make fleet-render` | 인벤토리 생성 | 없음 |
| `make fleet-render-check` | 생성 결과와 커밋된 인벤토리 비교 | 없음 |
| `make inventory-check INVENTORY=<lab\|cel1\|dir>` | 계약 검사 | 없음 |
| `make values-check ENV=<env>` | 값 파일의 모든 키가 잠근 버전 chart의 `values.yaml`에 있는지 검사 | 없음. 공개 chart 저장소에서 chart를 받는다 |
| `make addons-template ENV=cel1` | 두 릴리스 렌더링, kubeconform v1.36.6 검증 | 없음. chart와 스키마를 받는다 |
| `make fleet-check` | 위 검사 전부 | 없음 |
| `make fleet-k8s CONFIRM=cel1 FLEET_CONNECT=<inventory>` | `make versions SCOPE=fleet` 뒤 `ansible/playbooks/kubernetes.yml` | 있음 |
| `make fleet-addons CONFIRM=cel1` | `make versions SCOPE=fleet` 뒤 cel1 kubeconfig로 `make addons ENV=cel1` | 있음 |
| `make fleet-rsyslog CONFIRM=cel1 FLEET_CONNECT=<inventory>` | `ansible/playbooks/fleet/rsyslog-client.yml` | 있음 |

마지막 세 타깃은 `CONFIRM=cel1`이 없으면 어떤 전제 조건도 실행하지 않고 종료 코드 2로 멈춘다. `fleet-k8s`와 `fleet-rsyslog`는 cel1 인벤토리에 `FLEET_CONNECT`가 없어도 멈춘다. `fleet-addons`는 kubeconfig(`${XDG_STATE_HOME:-~/.local/state}/rdma-fabric-lab/cel1/kubeconfig`)가 없으면 멈춘다. `ARGS=`로 `--limit` 같은 인자를 더한다.

## 9. 오프라인 검사 결과

2026-11-15에 이 저장소에서 실행했다.

| 명령 | 종료 코드 | 결과 |
|---|---:|---|
| `make fleet-site-check` | 0 | 호스트 13, 네트워크 11, 링크 128, 랩 계획에 대해 유효 |
| `make fleet-render-check` | 0 | 렌더링 파일 18개, 차이 0 |
| `make inventory-check INVENTORY=cel1` | 0 | 호스트 13, 위반 0, 모든 변수 명시 |
| `make inventory-check INVENTORY=lab` | 0 | 호스트 3, 위반 0. 랩 작업자가 계약 변수와 `values-lab.yaml`을 추가한 뒤의 결과다 |
| `make values-check ENV=cel1` | 0 | 값 파일 2개, 모르는 키 0 |
| `make addons-template ENV=cel1` | 0 | Cilium 리소스 25개 유효. GPU Operator 36개 중 27개 유효, 9개 건너뜀 |
| `make fleet-k8s`, `make fleet-addons`, `make fleet-rsyslog` (CONFIRM 없음, `CONFIRM=lab`) | 2 | 저장소와 상태 디렉터리의 체크섬이 실행 전후 같았다 |

GPU Operator에서 건너뛴 9개는 CRD 8개와 `ClusterPolicy` 1개다. kubeconform의 기본 스키마 위치에 그 스키마가 없다. 그 9개는 검증하지 않았다.

각 검사를 한 번씩 깨서 실패를 확인했다.

| 깬 것 | 결과 |
|---|---|
| Cilium 값에 `kubeProxyReplacment` | `values-check` 종료 코드 2, `kubeProxyReplacment is not a key of the chart` |
| GPU Operator 값에 `devicePlugn` | `values-check` 종료 코드 2, `devicePlugn is not a key of the chart` |
| Cilium `priorityClassName`을 리스트로 | `addons-template` 종료 코드 2. helm이 `at '/priorityClassName': got array, want string`으로 거부 |
| GPU Operator `operator.priorityClassName`을 리스트로 | `addons-template` 종료 코드 2. kubeconform이 Deployment `gpu-operator`를 `got array, want null or string`으로 거부 |
| 검증기의 포트 중복, 라딕스, 레일 배선 검사를 각각 끔 | `test_fleet_site.py`가 각각 실패 |
| 계약 검사의 예약 접두사, VIP 규칙, 노드 IP 중복 검사를 각각 끔 | `test_inventory_contract.py`가 각각 실패 |

## 10. 실행하지 않은 것

| 항목 | 상태 | 이유 |
|---|---|---|
| `make fleet-k8s`, `make fleet-addons`, `make fleet-rsyslog` (`CONFIRM=cel1`) | `not_run` | 이 작업은 게스트를 부팅하지 않고 cel1 주소에 접속하지 않는다. 다음 시뮬레이션 작업이 실행한다 |
| `rsyslog_client` 두 번째 실행의 `changed=0` | `not_run` | 실행할 게스트가 없다 |
| Cilium native routing과 kube-proxy 대체의 동작 | `not_run` | 렌더링만 했다 |
| GPU Operator 라벨에 따른 배치 | `not_run` | GPU가 없다. 시뮬레이션 게스트에도 GPU가 없어 NFD가 GPU 노드로 보지 않는다. 그래서 시뮬레이션도 이것을 증명하지 못한다 |
| DGX 노드의 기본 런타임 `nvidia` | `not_run` | 어떤 역할도 아직 설정하지 않는다. 6절 |
| ibsim 토폴로지 | `not_run` | M4에서 만든다 |
| 9개 GPU Operator CRD와 ClusterPolicy의 스키마 검증 | `not_run` | kubeconform 스키마가 없다 |

## 11. 다른 사이트에 적용하기

다른 사이트는 같은 코드에 자기 파일만 준다. 파일은 모두 저장소 밖에 둔다.

1. 사이트 모델을 이 형식으로 쓴다. 예를 들어 `/srv/site-x/site.yaml`. 실제 MAC이나 PCI 경로는 모델에 넣지 않는다. `.link` 파일은 그 사이트의 프로비저닝이 쓴다. `site:` 값이 `rfl_inventory`가 된다. 계약은 이 값이 `helm/releases.yaml`의 환경이기를 요구하므로 지금은 `cel1`이어야 한다. 4번이 그 이유다.
2. 검증하고 인벤토리를 만든다.

   ```bash
   PYTHONPATH=scripts .venv/bin/python -m fleet site-check /srv/site-x/site.yaml
   PYTHONPATH=scripts .venv/bin/python -m fleet render /srv/site-x/site.yaml --out /srv/site-x/inventory
   make inventory-check INVENTORY=/srv/site-x/inventory
   ```

3. 접속 정보(`ansible_host`, `ansible_user`)는 두 번째 인벤토리에 둔다.

   ```bash
   make fleet-k8s CONFIRM=cel1 FLEET_INVENTORY=/srv/site-x/inventory FLEET_CONNECT=/srv/site-x/connect.yml
   ```

4. Helm 값은 아직 저장소 밖에서 줄 수 없다. `rfl_inventory`는 `helm/releases.yaml`의 환경이어야 하고 값 파일은 `helm/<release>/values-<env>.yaml`이다. `values-cel1.yaml`에는 cel1의 VIP와 Pod 범위가 있으므로 다른 사이트가 그대로 쓰면 `cilium-mismatch`가 난다. 값 덧씌우기 파일을 받는 기능이 `make addons`와 계약 검사에 필요하며 아직 없다.

## 12. 사용자가 정할 것

| 결정 | 적용한 기본값 |
|---|---|
| 라벨 접두사 | `rfl.example/node-class`. 실제 소유 도메인이 생기면 바꾼다 |
| 리프–스파인 비율 | 1:1. 리프마다 업링크 8개 |
| 스토리지 포트 구성 | 포트마다 별도 서브넷. 본딩하지 않는다 |
| syslog 대상 | 관리망 `10.140.0.5:514`, TCP. 시뮬레이션에서 이 주소를 누가 받을지는 다음 작업이 정한다 |
| DGX 기본 런타임 | `containerd` 역할이 `k8s_container_runtime_default: nvidia`를 받아 설정한다 |
| 다른 사이트의 Helm 값 | 11절의 덧씌우기 방식이 필요하다 |
