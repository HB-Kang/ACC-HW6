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
4. [ACC-HW6](https://github.com/HB-Kang/ACC-HW6) 클론 또는 스크립트 폴더 복사:

```
setup_main.sh  setup_sub.sh  hw6_config.sh
create_vms.sh  migration_dashboard.py
```

5. root로 작업: `sudo -i` 또는 `sudo bash ...`

---

## 2. 설치 — `setup_main.sh`

```bash
cd /path/to/ACC
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

## 4. 실험 — `migration_dashboard.py` (A만)

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

1. Before 스크린샷 (대시보드 3호스트 패널)  
2. 필요 시 VM 부하 (`virsh console` 후 stress-ng)  
3. `c` / `r` / `l` → 재배치 **계획** 캡처  
4. `m` → 진행률·Dirty rate 캡처  
5. After + downtime 기록  

### stress-ng (VM 안)

```bash
stress-ng --cpu $(nproc) --timeout 0 &
stress-ng --vm 1 --vm-bytes 80% --timeout 0 &
```

| Case | 부하 팁 |
|------|---------|
| Case 1 | 초기 배치 유지 |
| Case 2 | vm-1,2,3 CPU / vm-4,5,6 MEM |
| Case 3 | A 위 VM CPU 풀가동 → A 90%+ 후 `l` |

### Downtime 기록 (A)

```bash
virsh domjobinfo --completed <vm-name>
```

### 수동 migration (보조)

```bash
virsh migrate --live --persistent --undefinesource \
    vm-5 qemu+ssh://root@serverc/system
```

---

## 5. A 체크리스트

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

## 6. A 트러블슈팅

| 증상 | 조치 |
|------|------|
| B/C SSH 실패 | `ls .../hw6/keys/*.pub` 3개인지, B/C `setup_sub` 재실행 |
| 대시보드 호스트 빨강 | `cluster.conf` IP = 실제 NIC IP |
| Migration storage 오류 | B/C에 `mount \| grep libvirt` 요청 |
| NFS 문제 | `systemctl status nfs-server`, `exportfs -v` |

환경 변수: `HW6_SKIP_SSH_WAIT=1`, `HW6_ROOT_PASSWORD=...` (3대 동일 root PW 시)

---

**다음에 할 일 없으면** B/C 담당자 진행 여부만 확인하세요. Sub 절차는 [HW6_Sub.md](HW6_Sub.md)에만 있습니다.
