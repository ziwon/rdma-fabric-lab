# VMware Workstation 실습 VM 명세

> 상태: **이 호스트에서 검증하지 않았다.** M1을 수행한 호스트에는 VMware Workstation이 없다. M1 검증은 같은 Ansible 코드를 비특권 QEMU/KVM 게스트(`tests/integration/qemu-lab/`)에 적용해 수행했다. 이 문서는 PLAN 3의 VMware 경로를 수동 절차로 남긴다.

## 1. 범위

VM 생성은 수동으로 한다. Ansible은 이미 만들어진 VM만 다룬다. 이 문서의 결과물은 SSH로 접근할 수 있는 Ubuntu Server 24.04 LTS VM 세 대와, 그 VM을 가리키는 override inventory 한 개다.

M1이 쓰는 VM은 `worker1`, `worker2` 두 대다. `cp1`은 M2부터 쓴다.

## 2. VM 자원

PLAN 3.1의 초기 할당안이다. 검증된 최소 사양이 아니다. `make doctor`가 호스트의 CPU·메모리·디스크를 이 값과 비교해 경고한다.

| VM | vCPU | 메모리 | 디스크 | NIC |
|---|---:|---:|---:|---|
| cp1 | 2 | 4GiB | 30GiB | 관리 NIC 1개 |
| worker1 | 4 | 6GiB | 40GiB | 관리 NIC, RDMA NIC |
| worker2 | 4 | 6GiB | 40GiB | 관리 NIC, RDMA NIC |

게스트 OS는 Ubuntu Server 24.04 LTS다. 설치 미디어는 날짜와 checksum을 기록한다. `versions.lock.yaml`의 `lab.guest_image`는 QEMU 하네스가 쓴 cloud image를 고정한다. VMware 설치 ISO를 쓰면 그 ISO의 checksum을 별도로 기록한다.

`lab.guest_kernel`은 실행 커널을 고정한다. 설치 후 `uname -r`가 lock 값과 다르면 `make bootstrap`이 멈춘다. 이때는 해당 커널에서 `rdma_rxe`를 제공하는 패키지가 있는지 먼저 확인하고 lock을 다시 고정한다.

## 3. VMnet 두 개

| 용도 | VMnet 종류 | 서브넷 | DHCP | 기본 경로 |
|---|---|---|---|---|
| 관리망 | NAT (예: VMnet8) | `192.168.140.0/24` | 켬. 단, 고정 주소 `.11`–`.13`은 예약하거나 DHCP 범위 밖에 둔다 | 이 망에만 둔다 |
| RDMA 실습망 | Host-only (예: VMnet2) | `10.77.0.0/24` | 끈다 | 두지 않는다 |

1. `make doctor`를 먼저 실행한다. 위 두 서브넷이나 Pod·Service CIDR이 호스트 주소·라우트·컨테이너 네트워크와 겹치면 doctor가 실패한다. 겹치면 `ansible/inventories/lab/group_vars/all/lab.yml`의 `lab_address_plan`과 `hosts.yml`의 노드 주소를 함께 바꾼다.
2. Virtual Network Editor(`vmware-netcfg`)에서 NAT VMnet의 서브넷을 `192.168.140.0/24`로 정한다. 기존 VMnet 설정을 덮어쓰지 말고 비어 있는 번호를 쓴다.
3. Host-only VMnet을 하나 추가하고 서브넷을 `10.77.0.0/24`로 정한다. "Use local DHCP service"를 끈다. Multus 주소 풀 `10.77.0.100`–`10.77.0.199`와 VM 고정 주소가 DHCP와 충돌하지 않게 하기 위해서다.
4. Host-only VMnet에는 호스트 어댑터를 연결하지 않아도 된다. 연결하면 호스트에 `10.77.0.0/24` 주소가 생기므로 `make doctor`를 다시 실행해 확인한다.

## 4. VM의 NIC

- 첫 번째 NIC는 관리용 NAT VMnet에 연결한다.
- 두 번째 NIC는 RDMA용 Host-only VMnet에 연결한다. `worker1`, `worker2`만 가진다.
- NIC 종류는 기록해 둔다. `make preflight`가 게스트 안에서 드라이버 이름(`vmxnet3`, `e1000e` 등)을 보고한다.
- 두 번째 NIC에는 설치 단계에서 주소를 주지 않는다. `network` 역할이 netplan 파일 `/etc/netplan/60-rfl-rdma.yaml`로 고정 주소를 넣는다.
- MTU는 1500으로 시작한다. MTU 변경 실험은 별도 프로파일로 분리한다.

NIC 이름은 inventory 변수다. `ens33`, `ens37`이라고 가정하지 않는다. 게스트에서 `ip -br link`로 이름을 확인하고, `.vmx`의 `ethernetN.generatedAddress` 또는 VM 설정의 MAC과 맞춰 어느 NIC가 어느 VMnet인지 정한다.

## 5. 게스트 준비

1. 관리 IP를 고정한다. `cp1` `192.168.140.11`, `worker1` `192.168.140.12`, `worker2` `192.168.140.13`이다.
2. 실습 사용자를 만들고 SSH 공개키를 넣는다. `make bootstrap`은 `become`을 쓰므로 그 사용자에게 암호 없는 sudo를 주거나, `ansible-playbook`을 직접 `-K`와 함께 실행한다.
3. 호스트에서 각 VM에 한 번 SSH로 접속해 host key를 `known_hosts`에 넣는다. `ansible.cfg`는 host key 검사를 끄지 않는다.
4. 네트워크 렌더러가 systemd-networkd인지 확인한다. Ubuntu Server 기본값이다. `network` 역할은 NetworkManager 게스트를 거부한다.

## 6. override inventory

연결 정보와 NIC 이름은 `ansible/inventories/lab/` 뒤에 두 번째 `-i`로 넘긴다. QEMU 하네스는 이 파일을 `.lab/inventory.yml`로 만든다. VMware 실습은 같은 모양의 파일을 저장소 밖에 둔다.

```yaml
all:
  hosts:
    cp1:
      ansible_user: lab
      lab_mgmt_nic: ens33
    worker1:
      ansible_user: lab
      lab_mgmt_nic: ens33
      lab_rdma_nic: ens37
    worker2:
      ansible_user: lab
      lab_mgmt_nic: ens33
      lab_rdma_nic: ens37
```

위 NIC 이름은 형식을 보여 주는 예시다. 실제 이름은 4절의 방법으로 확인한다. `ansible_host`는 `hosts.yml`의 관리 IP를 그대로 쓴다.

```bash
make doctor
make versions SCOPE=m1
make bootstrap LAB_OVERRIDE=$HOME/rdma-lab/vmware-inventory.yml
make preflight LAB_OVERRIDE=$HOME/rdma-lab/vmware-inventory.yml
make rxe-up    LAB_OVERRIDE=$HOME/rdma-lab/vmware-inventory.yml
make rxe-test  LAB_OVERRIDE=$HOME/rdma-lab/vmware-inventory.yml
make k8s       LAB_OVERRIDE=$HOME/rdma-lab/vmware-inventory.yml LAB_API_URL=
make addons ENV=lab KUBECONFIG_FILE=.lab/kubeconfig
make k8s-test  LAB_OVERRIDE=$HOME/rdma-lab/vmware-inventory.yml
```

VMware 실습에서는 관리 NIC 하나가 기본 경로, SSH, Kubernetes를 모두 나른다. 그래서 `lab_uplink_nic`을 두지 않는다. 호스트는 NAT VMnet의 호스트 어댑터로 `192.168.140.11:6443`에 직접 닿으므로 `LAB_API_URL`을 비워 kubeconfig의 주소를 그대로 둔다. QEMU 하네스와 다른 점은 `docs/milestones/M2.md` 3절에 있다.

## 7. 이 경로에서 확인되지 않은 것

- VMware Host-only VMnet이 두 게스트 사이에 RoCEv2 UDP 4791 프레임을 전달하는지.
- vmxnet3·e1000e 위에서 RXE 성능과 오류 동작.
- VMware NIC 연결 해제가 게스트에서 carrier 손실로 보이는지. QEMU 하네스에서는 carrier가 사라지면 systemd-networkd가 RDMA NIC의 고정 주소를 내린다(`docs/troubleshooting.md` 5절).

- NAT VMnet이 세 게스트 사이에 Cilium VXLAN(UDP 8472)을 전달하는지, 그리고 호스트가 API에 닿는지.

이 항목은 VMware 호스트에서 `make rxe-test`와 `make k8s-test`를 처음 실행할 때 결과 디렉터리로 판정한다.
