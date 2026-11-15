# Track A 문제 해결

M0·M1·M2에서 실제로 관찰한 실패와 그 원인을 적는다. 각 항목의 근거는 실행 로그와 결과 디렉터리다. 원인을 확인하지 못한 항목은 넣지 않는다.

## 1. `make doctor`가 실패한다

doctor는 주소 계획의 각 범위를 호스트의 인터페이스 주소, 모든 라우팅 테이블, docker 네트워크, libvirt 네트워크와 비교한다. 한 주소라도 겹치면 `[fail] plan <이름> <범위> overlaps <호스트 범위>`를 출력하고 실패한다.

- 원인: 관리망·RDMA망·Pod·Service CIDR·Multus 풀 중 하나가 호스트가 이미 쓰는 범위와 겹친다. 꺼져 있는 docker bridge도 서브넷을 점유하므로 충돌로 본다.
- 조치: `ansible/inventories/lab/group_vars/all/lab.yml`의 `lab_address_plan`과 `hosts.yml`의 노드 주소를 함께 바꾼다. 다른 계획을 먼저 시험하려면 `make doctor LAB_PLAN=<파일>`을 쓴다. 파일에는 `lab_address_plan` 키만 있으면 된다.
- `[fail] ... lies outside plan rdma`는 계획 내부 모순이다. 노드 주소가 해당 망 밖에 있거나 Multus 풀이 RDMA망 밖에 있다.
- 디스크·메모리·CPU 부족은 `[warn]`이다. PLAN 3.1의 예산은 시작 할당안이므로 doctor를 실패시키지 않는다.

## 2. `make bootstrap`이 커널 또는 패키지에서 멈춘다

| 메시지 | 원인 | 조치 |
|---|---|---|
| `runs kernel X, but versions.lock.yaml pins lab.guest_kernel Y` | 게스트가 lock과 다른 커널로 부팅했다 | lock의 커널로 부팅하거나, 새 커널을 probe한 뒤 lock을 다시 고정한다 |
| `rdma_rxe is not part of kernel X and apt has no installable linux-modules-extra-X` | 실행 커널의 추가 모듈 패키지가 아카이브에 없다. 오래된 이미지의 커널에서 생긴다 | 추가 모듈 패키지가 있는 커널로 부팅하고 `lab.guest_kernel`을 다시 고정한다 |
| `Version '50.0-2ubuntu0.2' for 'rdma-core' was not found` | noble-updates가 새 버전으로 옮겨 lock 버전이 색인에서 빠졌다 | 새 버전을 probe한 뒤 `lab.rdma_core`를 다시 고정한다 |
| apt lock 대기 | 첫 부팅 직후 apt-daily가 돌고 있다 | `common_apt_lock_timeout`(600초)까지 기다린다 |

20261017 이미지의 커널 6.8.0-147-generic에는 `rdma_rxe`가 없다. `linux-modules-extra-6.8.0-147-generic`이 제공한다(`docs/milestones/evidence/m1/probe.log`). `common` 역할은 `modinfo rdma_rxe`가 실패하고 `apt-cache policy`가 후보 버전을 보여 줄 때만 이 패키지를 설치한다.

`common` 역할은 설치된 커널 메타패키지(`linux-virtual`, `linux-generic` 등)를 hold한다. 무인 업그레이드가 새 커널을 설치하면 다음 부팅에서 그 커널용 `rdma_rxe` 패키지가 없어 RXE가 사라지기 때문이다. 커널을 올리려면 hold를 풀고 lock을 다시 고정한다.

## 3. `make preflight`의 `[FAIL]`

preflight는 아무것도 바꾸지 않는다. 각 항목은 원인을 이름으로 출력한다.

| 항목 | 흔한 원인 |
|---|---|
| `nic-variables` | override inventory에 `lab_mgmt_nic`, `lab_rdma_nic`가 없다 |
| `default-route-on-uplink` | 기본 경로가 `lab_uplink_nic`(없으면 관리 NIC)가 아닌 NIC에 있다 |
| `mgmt-address` | 관리 NIC가 `k8s_node_ip`를 갖고 있지 않다. Kubernetes는 이 주소로 노드를 등록한다 |
| `rdma-nic-present` | NIC 이름이 틀렸거나 두 번째 NIC가 VM에 없다 |
| `rdma-address`, `rdma-mtu` | `make bootstrap` 전이다. bootstrap 전 preflight는 이 항목과 `rdma-rxe-available`, `packages-locked`에서 실패한다 |
| `peers-routed-via-rdma-nic` | RDMA NIC에 주소가 없어 상대 주소가 기본 경로(관리 NIC)로 나간다 |
| `peers-reachable` | 두 RDMA NIC가 같은 실습망에 있지 않다 |

## 4. `rfl-rxe.service`가 장치를 만들지 않는다

`journalctl -b -u rfl-rxe.service`를 sudo로 본다. 실습 사용자가 `adm`이나 `systemd-journal` 그룹에 없으면 sudo 없이는 시스템 journal이 비어 보인다. M1 첫 reboot 시험은 이 이유로 실패했고, 러너는 이제 sudo로 journal을 읽는다.

| journal 메시지 | 의미 |
|---|---|
| `added rxe0 on <nic>` | 이번 부팅에서 장치를 만들었다 |
| `rxe0 already on <nic>` | 이미 있다. 아무것도 바꾸지 않았다 |
| `<nic> is not up with <주소> after 90 s` | 제한 시간 안에 NIC가 올라오지 않았거나 주소가 없다 |
| `rxe0 exists on <다른 NIC>` | 같은 이름의 장치가 다른 NIC에 있다. 스크립트도 `rxe` 역할도 이 장치를 지우지 않는다 |
| `<nic> already carries RDMA device <이름>` | 같은 NIC에 다른 RDMA 장치가 있다. 두 번째 장치를 만들지 않는다 |

장치는 이름만 보고 지우지 않는다. 손으로 지우기 전에 `rdma link show`로 netdev를 확인한다. `make reset-lab CONFIRM=lab`은 lab NIC에 묶인 `rxe0`만 지운다.

## 5. 실습 NIC 링크가 내려가면

M1 장애 시험에서 관찰한 동작이다.

- 링크가 내려가면 systemd-networkd가 RDMA NIC의 고정 주소를 내린다. 기본값 `IgnoreCarrierLoss=no`이기 때문이다.
- 주소가 사라지면 커널은 상대 RDMA 주소를 기본 경로, 즉 관리 NIC로 보낸다. `route-via-lab-nic` 검사가 `worker1 routes 10.77.0.13 via enp0s3`로 이를 잡는다. RDMA CM은 관리 NIC에 RDMA 장치가 없어 연결을 거부한다. 일반 IP 트래픽은 관리망으로 나갈 수 있다.
- 서버 쪽 링크가 내려가면 `rping -s`는 `rdma_bind_addr: Cannot assign requested address`로, perftest 서버는 `Port number 1 state is Down`으로 바로 끝난다. 실패 분류는 `command-failed`다.
- 클라이언트 쪽 링크가 내려가면 `rping -c`는 `RDMA_CM_EVENT_ADDR_ERROR, error -19`(ENODEV)로 바로 끝난다. 러너는 기다리던 서버를 멈춘다.
- 링크는 살아 있고 프레임만 사라지면(`tc qdisc ... netem loss 100%`) `rping`은 2초 뒤 `error -110`(ETIMEDOUT)으로 끝나고 분류는 `timeout`이다. perftest는 `Received 10 times ADDR_ERROR`만 출력하고 errno를 주지 않으므로 `command-failed`로 남는다.

링크를 올리면 networkd가 주소를 다시 넣고 RXE 포트가 ACTIVE로 돌아온다. 같은 `make rxe-test`가 다시 통과한다.

## 6. 러너가 남긴 프로세스

러너가 시작한 게스트 프로세스는 모두 `timeout` 아래에서 돌고 환경 변수 `RFL_RUN=<run-id>`를 가진다. 출력·pid·종료 코드는 게스트의 `/var/tmp/rfl/<run-id>/`에 있다. 러너가 정상 종료하거나 SIGINT·SIGTERM을 받으면 자기 프로세스를 멈추고 이 디렉터리를 지운다.

러너가 SIGKILL 등으로 죽으면 `make cleanup RUN=<run-id>`를 실행한다. 이 명령은 그 run id를 가진 프로세스만 멈추고 그 디렉터리만 지운다. RXE 장치, `rfl-rxe.service`, 네트워크 설정, 다른 run, 게스트는 건드리지 않는다.

## 7. QEMU 하네스

| 증상 | 확인 |
|---|---|
| `no SSH on 127.0.0.1:<port>` | `.lab/guests/<guest>/serial.log` |
| `cloud-init did not finish cleanly` | 게스트에서 `sudo cloud-init schema --system` |
| `does not hash to lab.guest_image` | 받은 이미지가 lock의 sha256과 다르다. 하네스는 부팅하지 않는다 |
| `lab.sh down`이 프로세스를 멈추지 않는다 | pidfile의 pid가 살아 있고 그 명령줄에 같은 pidfile 경로가 있을 때만 멈춘다 |

하네스의 실습망은 `.lab/net/` 아래 AF_UNIX datagram 소켓 두 개다. 실습망 프레임은 호스트 밖으로 나가지 않는다. 관리 NIC는 게스트마다 별도인 QEMU user networking이라 두 게스트는 관리망으로 서로 닿지 않는다. VMware NAT VMnet에서는 관리망이 공유되므로 `no-roce-on-mgmt-nic` 검사가 더 큰 의미를 가진다.

## 6. `make k8s`가 멈춘다

| 메시지 | 원인 | 조치 |
|---|---|---|
| `Conditional result (True) was derived from value of type 'str'` | ansible-core 2.19 이후 조건식은 불리언이어야 한다. `ansible.utils.ipaddr` 필터는 주소 문자열을 돌려준다 | 필터 대신 `is ansible.utils.in_network(...)` 같은 test를 쓴다. M1 회귀에서 network 역할이 이 이유로 실패했다 |
| `has containerd.io installed` | 패키지로 깔린 containerd가 있다. 두 데몬이 `/run/containerd/containerd.sock`을 다툰다 | 아무것도 바꾸지 않고 멈춘다. 패키지를 지울지, 그 호스트를 `k8s_cluster`에서 뺄지 운영자가 정한다 |
| `is not the key DE15B144...` | pkgs.k8s.io 서명 키가 바뀌었다. 지금 키는 2026-12-29에 만료된다 | 새 키의 지문을 확인하고 `kubeadm_apt_key_fingerprint`를 갱신한다 |
| `it does not upgrade (ADR 0001 section 6)` | 노드에 lock과 다른 kubelet, kubeadm, kubectl, cri-tools가 있다 | 업그레이드는 이 역할의 범위가 아니다. 버전을 맞추거나 노드를 새로 만든다 |
| `was initialized with other settings` | 클러스터의 kubeadm-config가 인벤토리의 endpoint, CIDR, 버전, kube-proxy 선택과 다르다 | 역할은 재구성하지 않는다. 인벤토리를 클러스터에 맞추거나 클러스터를 새로 만든다 |
| `could not bootstrap the admin user in file admin.conf: ... context deadline exceeded` | `kubeadm init phase upload-certs`를 `--config` 없이 실행했다. kubeadm이 기본값으로 버전을 온라인에서 찾고 API에 닿지 못한다 | 역할은 렌더한 `/etc/kubernetes/rfl/kubeadm-init.yaml`을 넘긴다. 같은 명령이 0.06초에 끝났다 |

kubelet 패키지는 `kubernetes-cni`에 의존한다. 이 패키지는 `/opt/cni/bin`에 다른 릴리스의 플러그인을 푼다. `containerd` 역할이 먼저 `/etc/dpkg/dpkg.cfg.d/rfl-cni-plugins`에 `path-exclude=/opt/cni/bin/*`를 쓰므로 dpkg는 그 경로를 건드리지 않는다. `/opt/cni/bin/loopback`이 lock의 버전을 말하지 않으면 이 파일이 있는지 먼저 본다.

## 7. `k8s-baseline`이 실패한다

| 실패한 검사 | 관찰한 원인 |
|---|---|
| `cilium-agents-ready` | 노드의 Cilium agent pod가 다시 뜨는 중이다. `kubectl -n kube-system rollout status daemonset/cilium`으로 기다린 뒤 다시 돌린다 |
| `cilium-agents-ready`, agent가 `Init:CrashLoopBackOff`이고 `config` 로그에 `dial tcp 10.96.0.1:443: i/o timeout` | agent가 ClusterIP로 API에 가는데, 기본 경로가 노드 IP와 다른 NIC에 있어 출발 주소가 틀린 채 나간다. Cilium의 표식 때문에 kube-proxy가 주소를 바꾸지 않는다. `kube-system/rfl-api-endpoint`가 있는지와 값 파일의 `k8sServiceHostRef`를 확인한다. `make k8s`가 ConfigMap을 쓴다 |
| `pod-to-pod-cross-node`, `clusterip-service`가 timeout, `vxlan-on-mgmt-nic`이 한 방향만 본다 | 한 노드가 UDP 8472를 버린다. worker1 INPUT에 DROP 규칙을 넣으면 이 모양으로 실패했다 |
| `network-policy-blocks`, `network-policy-allows`가 `not_run` | 앞의 `clusterip-service`가 통과하지 못했다. 이미 끊긴 트래픽에서 "막혔다"는 정책을 증명하지 않으므로 시도하지 않는다 |

러너는 cp1에서 kubectl을 실행한다. 제어 노드에서 클러스터를 보려면 `.lab/kubeconfig`를 쓴다. QEMU 하네스에서는 cp1의 6443이 `127.0.0.1:22643`으로 전달되고, kubeconfig는 `tls-server-name: kubernetes`로 API 인증서를 검증한다.

## 8. API VIP가 옮겨 가지 않는다

`k8s-ha-vip`에서 VIP를 가진 컨트롤 플레인의 전원을 끊으면 lease가 5초 뒤 만료되고 다른 컨트롤 플레인이 VIP를 가져간다. 관찰한 API 공백은 9초 안팎이다. VIP가 돌아오지 않으면 다음을 본다.

- 살아 있는 컨트롤 플레인의 `/etc/kubernetes/manifests/kube-vip.yaml`. 이 파일을 두 노드에서 치우면 VIP는 120초 동안 어디에도 없었다. `make k8s`가 파일을 다시 쓴다.
- `kubectl -n kube-system get lease plndr-cp-lock`의 holderIdentity와 각 노드 `ip -4 addr`의 VIP.
- 첫 컨트롤 플레인의 kube-vip은 init이 끝날 때까지 `super-admin.conf`를 쓴다. Kubernetes 1.29 이후 `admin.conf`의 그룹 권한이 init 중간에야 생기기 때문이다.
