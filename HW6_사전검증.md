# HW6 사전검증 — VirtualBox 전체 파이프라인

[← README](README.md) · 다음: [실제 설치](HW6_설치.md)

**목적:** 듀얼부팅 bare metal 실습 전에, VirtualBox 3대에서 **스크립트 설치 → VM 생성 → 대시보드 오퍼레이터(`r`/`c`/`l`/`m`)로 Live Migration**까지 한 번에 검증합니다.

> IP·호스트명은 예시(`192.168.0.x`)입니다. 본인 LAN 값으로 바꿉니다.

---

## 1. VirtualBox VM 준비 (Windows)

1. Rocky Linux 10 ISO: https://rockylinux.org/download
2. VM 3개 생성

| VM 이름   | CPU  | RAM  | 디스크 |
|----------|------|------|--------|
| Rocky10-A | 4코어 | 8GB  | 60GB  |
| Rocky10-B | 4코어 | 8GB  | 40GB  |
| Rocky10-C | 4코어 | 8GB  | 40GB  |

### Nested Virtualization (필수)

VM **완전 종료** 후 PowerShell(관리자):

```powershell
VBoxManage modifyvm "Rocky10-A" --nested-hw-virt on
VBoxManage modifyvm "Rocky10-B" --nested-hw-virt on
VBoxManage modifyvm "Rocky10-C" --nested-hw-virt on
```

### 네트워크

어댑터1: **브리지 어댑터** → 실제 이더넷/Wi-Fi 선택

### Rocky Linux 10

- **Minimal Install**, 파티션 자동, 네트워크 ON

---

## 2. 호스트 네트워크 확인

```bash
ip addr show
```

예시:

```
Host-A (servera): 192.168.0.10
Host-B (serverb): 192.168.0.11
Host-C (serverc): 192.168.0.12
```

각 VM `/etc/hosts`:

```bash
echo "192.168.0.10  servera" >> /etc/hosts
echo "192.168.0.11  serverb" >> /etc/hosts
echo "192.168.0.12  serverc" >> /etc/hosts
```

```bash
# Host-A
ping -c 3 serverb && ping -c 3 serverc
```

### 스크립트 복사

ACC 폴더의 스크립트를 Host-A에 올린 뒤, A→B/C로 복사하거나 각 호스트에 동일하게 둡니다.

```bash
# Host-A 예시
scp setup_sub.sh create_vms.sh migration_dashboard.py root@serverb:/root/
scp setup_sub.sh create_vms.sh root@serverc:/root/
```

---

## 3. 스크립트로 설치

### 3.1 Host-A — `setup_main.sh`

```bash
bash setup_main.sh 192.168.0.11 192.168.0.12
```

- KVM / libvirt / virt-install  
- NFS 서버 (`/var/lib/libvirt/images`)  
- 방화벽·SELinux off, Python `rich`, SSH 키 생성  

### 3.2 Host-B, C — `setup_sub.sh`

```bash
# Host-B
bash setup_sub.sh 192.168.0.10

# Host-C
bash setup_sub.sh 192.168.0.10
```

### 3.3 설치 확인

```bash
# B, C
df -h | grep libvirt
ls /var/lib/libvirt/images/

# Host-A — SSH·libvirt 원격
ssh-copy-id root@serverb
ssh-copy-id root@serverc
virsh -c qemu+ssh://root@serverb/system list --all
virsh -c qemu+ssh://root@serverc/system list --all
```

### 3.4 VM 생성 — `create_vms.sh`

```bash
# Host-A
bash create_vms.sh
```

| VM   | vCPU | RAM  | 초기 호스트 |
|------|------|------|------------|
| vm-1 | 4    | 4 GB | A |
| vm-2 | 2    | 2 GB | A |
| vm-3 | 4    | 2 GB | B |
| vm-4 | 1    | 4 GB | B |
| vm-5 | 2    | 4 GB | C |
| vm-6 | 1    | 2 GB | C |

```bash
virsh list --all
virsh -c qemu+ssh://root@serverb/system list --all
virsh -c qemu+ssh://root@serverc/system list --all
```

### 3.5 Nested KVM (Rocky 안)

```bash
egrep -c '(vmx|svm)' /proc/cpuinfo   # 0이면 1절 Nested VT 재확인
```

---

## 4. 오퍼레이터로 Migration 검증

대시보드 TUI의 **키 오퍼레이터**로 Bin Packing 계획 수립 후 Live Migration을 실행합니다. (`virsh` 수동 명령은 선택)

### 4.1 대시보드 실행

```bash
# Host-A — migration_dashboard.py 상단 HOSTS_CONFIG IP가 본인 IP와 일치하는지 확인
python3 migration_dashboard.py
```

| 키 | 오퍼레이터 | 동작 |
|----|-----------|------|
| `r` | 균등 분산 | Bin Packing (Defragmentation) |
| `c` | Consolidation | Host-C Idle 목표 재배치 |
| `l` | Load balance | 과부하(80%↑) 감지 후 재배치 |
| `m` | **Migrate** | STATUS에 표시된 계획대로 Live Migration 실행 |
| `q` | Quit | 종료 |

### 4.2 스모크 테스트 순서

부하 없이도 Migration 동작만 확인할 수 있습니다.

1. 대시보드 실행 → 3호스트·6 VM 상태 표시 확인  
2. `r` 또는 `c` → STATUS 패널에 **이동 계획** 표시 확인  
3. `m` → Migration 진행률·Dirty page rate 표시 확인  
4. 완료 후 해당 VM이 **대상 호스트**에 있는지 확인  

```bash
# 별도 터미널에서 (예: vm-5가 A로 옮겨졌을 때)
virsh -c qemu+ssh://root@servera/system list --all
virsh domjobinfo --completed vm-5   # Downtime(ms), Total time(ms)
```

### 4.3 (선택) 간단 부하 후 재검증

```bash
virsh console vm-1    # Ctrl+]
stress-ng --cpu $(nproc) --timeout 60 &
```

1. `l` → 과부하 호스트 완화 계획 확인  
2. `m` → Migration 완료  
3. 호스트 CPU/MEM 패널 수치 변화 확인  

---

## 5. 사전검증 완료 체크리스트

- [ ] 3호스트 ping·`servera/b/c` 해석 OK  
- [ ] NFS 공유 마운트 (`df | grep libvirt`)  
- [ ] `virsh` 원격 3호스트 VM 목록 OK  
- [ ] VM 6대 running  
- [ ] 대시보드 `r`/`c` → 재배치 계획 표시  
- [ ] `m` → Live Migration 성공, `domjobinfo`에 Downtime 기록  

위 항목이 되면 [HW6_설치.md](HW6_설치.md) bare metal 환경으로 넘어갑니다.

---

## 트러블슈팅

| 증상 | 조치 |
|------|------|
| Nested KVM 0 | PowerShell `VBoxManage modifyvm ... --nested-hw-virt on` |
| NFS 마운트 실패 | Host-A: `exportfs -v`, `systemctl status nfs-server` |
| virsh 원격 실패 | `ssh root@serverb`, `ssh-keyscan serverb >> ~/.ssh/known_hosts` |
| Migration storage 오류 | B/C: `mount \| grep libvirt`, `mount -a` |
| 대시보드 호스트 미표시 | `migration_dashboard.py`의 `HOSTS_CONFIG` IP 수정 |
