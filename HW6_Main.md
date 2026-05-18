# HW6 — 팀원 A (Host-A · servera) 가이드

**이 문서만 읽으면 됩니다.** (B/C 담당자 → [HW6_Sub.md](HW6_Sub.md))

[README](README.md) · [전체 타임라인](HW6_전체가이드.md) (팀 공유용 요약)

| 항목 | 내용 |
|------|------|
| 담당 PC | **PC-A** |
| 호스트명 | `servera` |
| 예시 IP | `192.168.0.10` (팀 합의 D값) |
| 역할 | NFS 서버, VM 생성, Live Migration 대시보드 |

---

## 0. A가 먼저 팀과 합의할 것

- [ ] B/C 담당자에게 [HW6_Sub.md](HW6_Sub.md) 링크 전달
- [ ] `192.168.0.<D>` 세 값 확정 (A/B/C 각 1개, 서로 다름)
- [ ] 본인 PC NIC에 **servera IP** 적용
- [ ] 3대 상호 `ping` 가능한지 확인

B/C는 **A의 `setup_main.sh`가 끝난 뒤** `setup_sub.sh`를 실행합니다.

---

## 1. 사전 준비 (A PC)

1. Rocky Linux 10 **Minimal** 설치  
2. BIOS에서 CPU 가상화 **ON**  
3. NIC에 팀이 정한 IP 설정 (스크립트는 NIC IP를 **설정하지 않음**)  
4. 아래 **git 설치 → 클론** (한 번만 하면 됨)  
5. 이후 명령은 **root**로: `sudo -i` 또는 `sudo bash ...`

### 1-1. git 설치 + 저장소 받기

Rocky 터미널에서 **위에서 아래로** 그대로 실행:

```bash
# 1) git 설치
sudo dnf install -y git

# 2) 잘 설치됐는지 확인
git --version

# 3) 홈으로 이동 후 클론
cd ~
git clone https://github.com/HB-Kang/ACC-HW6.git

# 4) 폴더로 들어가서 파일 확인
cd ACC-HW6
ls
```

`ls`에 아래 파일이 보이면 OK:

- `setup_main.sh` `setup_sub.sh` `hw6_config.sh`
- `create_vms.sh` `migration_dashboard.py`

> `hw6_config.sh`는 **따로 실행하지 않습니다.** `setup_main.sh`가 알아서 불러 씁니다.

---

## 2. 설치 — `setup_main.sh`

```bash
cd ~/ACC-HW6
sudo bash setup_main.sh
```

### 입력

- Host-A / B / C 각각 **마지막 옥텟(D)** 입력 (`192.168.0.` 고정, 기본값 없음)
- 요약 확인 후 `y` → `/etc/hw6/cluster.conf` 생성

### 자동 처리

| 항목 | 내용 |
|------|------|
| `/etc/hosts` | servera, serverb, serverc |
| NFS | `/var/lib/libvirt/images` export |
| config | NFS에 `hw6/cluster.conf` 복사 (B/C용) |
| SSH | 키 생성, `servera.pub` 게시, B/C 키 대기·병합 |
| KVM | libvirtd, default 네트워크 |

### 설치 후 확인 (A)

```bash
cat /etc/hw6/cluster.conf
exportfs -v | grep libvirt
systemctl is-active nfs-server libvirtd sshd
ls /var/lib/libvirt/images/hw6/keys/
```

### B/C 설치 대기

스크립트가 B/C SSH 키를 NFS에서 기다릴 수 있습니다.  
B/C 담당자에게 **`setup_sub.sh` 완료**를 요청한 뒤:

```bash
ssh root@serverb 'virsh list --all'
ssh root@serverc 'virsh list --all'
```

실패 시: B/C `setup_sub` 완료 후 위 명령 재시도, 또는  
`HW6_SKIP_SSH_WAIT=1 sudo bash setup_main.sh` (기존 config 재사용)

---

## 3. VM 생성 — `create_vms.sh` (A만)

B/C 설치·SSH 확인 후:

```bash
sudo bash create_vms.sh
```

| VM | vCPU | RAM | 초기 배치 |
|----|------|-----|-----------|
| vm-1 | 4 | 4 GB | A |
| vm-2 | 2 | 2 GB | A |
| vm-3 | 4 | 2 GB | B |
| vm-4 | 1 | 4 GB | B |
| vm-5 | 2 | 4 GB | C |
| vm-6 | 1 | 2 GB | C |

SSH 준비되면 vm-3,4 → serverb, vm-5,6 → serverc **자동 migration** 시도.

```bash
virsh list --all
virsh -c qemu+ssh://root@serverb/system list --all
virsh -c qemu+ssh://root@serverc/system list --all
```

VM 콘솔: `virsh console vm-1` (종료 `Ctrl+]`) · root 비밀번호 `hw6pass`

---

## 4. 실험 컨셉 (과제·시연)

### 가정 용량 (Bin Packing 기준)

호스트 **3대 각각** 아래 상자 크기로 취급합니다 (`cluster.conf` 기본값).

| 항목 | 가정 |
|------|------|
| CPU | **8 vCPU** (VM에 할당한 vCPU 합) |
| MEM | **16 GB** |
| VM | 6대 (create_vms.sh 스펙) |

- **물리 PC**가 코어·RAM이 더 많아도 괜찮습니다. 알고리즘·대시보드 **◇ alloc** %는 이 **8/16** 기준입니다.
- **▲ host** %는 물리 머신 실제 사용률(참고용)입니다.
- VM vCPU 합이 물리 코어 수보다 많아도 KVM에서는 보통 문제 없습니다(오버커밋).

### Case 1~3 = Before / After 시연

| Case | 키 | 목표 (개념) |
|------|-----|-------------|
| **1** Consolidation | `c` → `m` | Host-C **IDLE**, VM은 A·B로 |
| **2** Defragmentation | `r` → `m` | CPU/MEM **쏠림 해소**, 3호스트 고르게 |
| **3** Load balancing | `l` → `m` | 한 호스트 **과부하(80%+)** 완화 |

**실제로 호스트가 과부하일 필요는 없습니다.**  
제출·시연은 **대시보드 Before → `c`/`r`/`l` 계획 → `m` Migration → After** 흐름과 스크린샷이면 충분합니다.  
Case 3 “과부하”도 **시나리오 이름**에 가깝고, 숫자를 맞추고 싶을 때만 stress-ng를 쓰면 됩니다.

---

## 5. 실험 — `migration_dashboard.py` (A만)

```bash
python3 migration_dashboard.py
```

(`/etc/hw6/cluster.conf` IP 자동 사용)

| 키 | 동작 | Case |
|----|------|------|
| `c` | Consolidation (Host-C Idle) | **Case 1** |
| `r` | 균등 분산 Bin Packing | **Case 2** |
| `l` | 과부하( CPU 80%+ ) 분산 | **Case 3** |
| `m` | Live Migration 실행 | 공통 |
| `q` | 종료 | — |

### 시연 순서 (A)

1. **Before** 스크린샷 (3호스트, ◇ alloc 위주)  
2. `c` / `r` / `l` → STATUS **재배치 계획** 캡처  
3. `m` → Migration 진행·Dirty rate 캡처  
4. **After** 스크린샷 + `domjobinfo` Downtime  

### stress-ng (선택 — 숫자 연출용)

부하 없이 Migration만 돌려도 됩니다. Case 2/3 Before를 연출하고 싶을 때만:

```bash
virsh console vm-1    # Ctrl+]
stress-ng --cpu $(nproc) --timeout 0 &
stress-ng --vm 1 --vm-bytes 80% --timeout 0 &
```

### Downtime 기록 (A)

```bash
virsh domjobinfo --completed <vm-name>
```

### 수동 migration (보조)

```bash
virsh migrate --live --persistent --undefinesource --unsafe \
    vm-5 qemu+ssh://root@serverc/system
```

(`--unsafe`: NFS 공유 디스크인데 libvirt 9+가 shared storage로 인식 못 할 때 필요)

---

## 6. A 체크리스트

### 설치

- [ ] `setup_main.sh` 완료
- [ ] `/etc/hw6/cluster.conf` 존재
- [ ] B/C `setup_sub.sh` 완료 (팀 확인)
- [ ] `ssh root@serverb` / `serverc` 비밀번호 없이 OK

### VM·실험

- [ ] `create_vms.sh` → VM 6대
- [ ] 대시보드 `r`/`c`/`l`/`m` 동작
- [ ] Case 1~3 Before/After·Downtime 수집

### 제출 (팀 PDF)

- [ ] 구성도, 스크린샷, `domjobinfo` 수치 → [README 제출 안내](README.md#제출-안내)

---

## 7. A 트러블슈팅

| 증상 | 조치 |
|------|------|
| B/C SSH 실패 | `ls .../hw6/keys/*.pub` 3개인지, B/C `setup_sub` 재실행 |
| 대시보드 호스트 빨강 | `cluster.conf` IP = 실제 NIC IP |
| Migration storage 오류 | B/C에 `mount \| grep libvirt` 요청 |
| `Unsafe migration` / shared storage | B/C NFS 마운트 확인 후 `--unsafe` 사용 (스크립트·대시보드 반영됨) |
| `Unable to resolve … dclab` | B 호스트명이 `dclab` 등으로 남음 → `hostnamectl set-hostname serverb.hw6.local` |
| `hostname … localhost` / FQDN | `127.0.0.1`에 호스트명 있으면 안 됨 → `setup_sub.sh` 재실행 또는 아래 수동 |
| C만 `channel … I/O error` | C NFS 미마운트·FQDN·`migration_host` → **Host-C** `setup_sub.sh` 후 A에서 재시도 |
| `cloud-init-vm-N.iso` Resource temporarily unavailable | NFS에 ISO + B/C에서 VM 실행 중 잠금 → `git pull` 후 `create_vms.sh` 재실행 (ISO는 `/var/tmp/hw6-cloud-init`) |
| C만 QEMU monitor closed / argument unsupported | `sudo bash hw6_diag_migration.sh` → C 호스트명·NFS·nested KVM 확인 후 `setup_sub.sh` on C |
| `special registers` / Invalid argument (C만) | `sudo bash hw6_fix_vm_cpu.sh` (`qemu64,-svm`) 후 재마이그레이션 |
| `does not provide required features: svm` | Intel 호스트인데 VM에 AMD SVM 요구 → `hw6_fix_vm_cpu.sh` (자동 `-svm`) |

**수동 수정 (B/C, git pull 후에도 migration 실패 시):**

```bash
# Host-B 예시
sudo hostnamectl set-hostname serverb.hw6.local
sudo sed -i 's/^127.0.0.1.*/127.0.0.1   localhost localhost.localdomain localhost4/' /etc/hosts
# /etc/hosts HW6 블록에 192.168.0.11  serverb.hw6.local serverb 있는지 확인
hostname -f    # serverb.hw6.local 이어야 함
getent hosts $(hostname -f)   # 127.0.0.1 이 아니어야 함
```
| NFS 문제 | `systemctl status nfs-server`, `exportfs -v` |

환경 변수: `HW6_SKIP_SSH_WAIT=1`, `HW6_ROOT_PASSWORD=...` (3대 동일 root PW 시)

---

**다음에 할 일 없으면** B/C 담당자 진행 여부만 확인하세요. Sub 절차는 [HW6_Sub.md](HW6_Sub.md)에만 있습니다.
