# HW6 — 팀원 B / C (Host-B · serverb / Host-C · serverc) 가이드

**이 문서만 읽으면 됩니다.** (A 담당자 → [HW6_Main.md](HW6_Main.md))

[README](README.md) · [전체 타임라인](HW6_전체가이드.md) (팀 공유용 요약)

| | 팀원 B | 팀원 C |
|--|--------|--------|
| 호스트명 | `serverb` | `serverc` |
| 예시 IP | `192.168.0.11` | `192.168.0.12` |
| 스크립트 | `setup_sub.sh` | `setup_sub.sh` |
| 초기 VM (create_vms 후) | vm-3, vm-4 | vm-5, vm-6 |

공통: KVM 호스트, **NFS 클라이언트**, Migration **수신** 측.  
**VM 생성·대시보드는 하지 않습니다** (A 담당).

---

## 0. B/C가 알아둘 팀 순서

```
1. A: setup_main.sh   ← 반드시 먼저
2. B: setup_sub.sh
3. C: setup_sub.sh
4. A: create_vms.sh → migration_dashboard.py
```

A가 `setup_main`을 끝내기 전에는 NFS·config가 없어 **B/C 설치가 실패**할 수 있습니다.

---

## 1. 사전 준비 (B 또는 C PC)

1. Rocky Linux 10 **Minimal** 설치  
2. BIOS CPU 가상화 **ON**  
3. NIC에 **본인 호스트 IP** 설정 (팀 합의 D값)  
4. A(servera)에 `ping` 되는지 확인  
5. 아래 **git 설치 → 클론** (한 번만)  
6. 이후 명령은 **root**: `sudo -i` 또는 `sudo bash ...`

```bash
ping -c 2 servera
# 또는 ping -c 2 192.168.0.10
```

### 1-1. git 설치 + 저장소 받기

Rocky 터미널에서 **위에서 아래로** 그대로 실행:

```bash
# 1) git 설치
sudo dnf install -y git

# 2) 확인
git --version

# 3) 클론
cd ~
git clone https://github.com/HB-Kang/ACC-HW6.git

# 4) 들어가서 확인 (hw6_config.sh 꼭 있어야 함)
cd ACC-HW6
ls
```

`ls`에 `setup_sub.sh`와 `hw6_config.sh`가 있으면 OK.

> A보다 **먼저** `setup_sub.sh`만 돌리지 마세요. A에서 `setup_main.sh`가 끝난 뒤에 실행합니다.

---

## 2. 설치 — `setup_sub.sh`

```bash
cd ~/ACC-HW6
sudo bash setup_sub.sh
```

### config 로드 (자동)

1. `/etc/hw6/cluster.conf` (있으면)  
2. NFS `.../hw6/cluster.conf` (A가 올림)  
3. 없으면 A와 **동일한 D값 3개** 직접 입력  

### 본인이 B인지 C인지

- NIC IP가 config와 같으면 **자동 감지**  
- 아니면 프롬프트에서 `b` 또는 `c` 입력  

### 자동 처리

| 항목 | 내용 |
|------|------|
| `/etc/hosts` | servera, serverb, serverc |
| NFS | fstab + `mount` → `/var/lib/libvirt/images` |
| SSH | 키 생성, `serverb.pub` 또는 `serverc.pub` NFS 게시 |
| KVM | libvirtd |

### 설치 후 확인 (B/C)

```bash
df -h | grep libvirt
mount | grep libvirt
cat /etc/hw6/cluster.conf
ls /var/lib/libvirt/images/hw6/keys/     # servera/b/c.pub 3개 목표
ssh root@servera 'virsh version'
virsh list --all
```

설치가 끝나면 **A 담당자에게 “sub 완료”** 연락 → A가 SSH·`create_vms.sh` 진행.

---

## 3. 실험 때 B/C가 할 일

대시보드·`create_vms.sh`는 **돌리지 않습니다.**  
A가 Migration 하는 동안 본인 호스트가 **정상 상태**인지만 유지합니다.

### 상시 확인

```bash
systemctl is-active libvirtd sshd
df -h | grep libvirt
virsh list --all
```

### Migration 수신 확인

| 담당 | 대표 VM | 확인 |
|------|---------|------|
| B | vm-3, vm-4 | `virsh list --all` 에 표시 |
| C | vm-5, vm-6 | 동일 |

```bash
virsh dominfo vm-3    # B 예시
```

### 스크린샷 (제출용 분담 권장)

| 담당 | 캡처 |
|------|------|
| B | vm-3,4가 serverb로 들어온 `virsh list` 화면 |
| C | vm-5,6가 serverc로 들어온 `virsh list` 화면 |

### (선택) A 쪽 VM 목록 원격 보기

```bash
virsh -c qemu+ssh://root@servera/system list --all
```

---

## 4. B/C 체크리스트

- [ ] A의 `setup_main.sh` 완료 확인 (ping servera, NFS 경로 응답)  
- [ ] `setup_sub.sh` 완료  
- [ ] `df -h | grep libvirt` OK  
- [ ] `hw6/keys/` 에 `.pub` 3개  
- [ ] A에게 설치 완료 알림  
- [ ] 실험 중 `libvirtd`·NFS 유지  
- [ ] (팀) Migration 수신·스크린샷  

제출 PDF는 팀이 함께 작성 → [README](README.md#제출-안내)

---

## 5. B/C 트러블슈팅

| 증상 | 조치 |
|------|------|
| NFS 마운트 실패 | `ping servera`, A에서 `systemctl status nfs-server` |
| config 못 찾음 | A `setup_main` 후 `showmount -e servera` |
| 역할(b/c) 잘못 잡힘 | NIC IP가 config와 같은지, 수동 `b`/`c` 입력 |
| SSH to servera 실패 | `setup_sub` 재실행, keys 3개 확인 |
| Migration 후 VM 안 보임 | `mount \| grep libvirt`, `virsh list --all` |

VirtualBox: Nested VT `VBoxManage modifyvm "이름" --nested-hw-virt on`, 어댑터 **브리지**.

### Migration hostname 오류

| 메시지 | 원인 |
|--------|------|
| `Unable to resolve 'dclab'` | PC 호스트명이 `dclab` — 다른 노드가 못 찾음 |
| `resolved to localhost` | `/etc/hosts`에 `127.0.0.1 … serverc` 형태 |

```bash
cd ~/ACC-HW6 && git pull
sudo bash setup_sub.sh    # serverb 또는 serverc FQDN 자동 설정
hostname -f                 # serverb.hw6.local 또는 serverc.hw6.local
```

### C만 `channel error: Input/output error` (B는 됨)

| 확인 (Host-C에서) | 기대 |
|-------------------|------|
| `mount \| grep libvirt` | NFS 마운트 있음 |
| `df -h /var/lib/libvirt/images` | Host-A export, **쓰기 가능** |
| `hostname -f` | `serverc.hw6.local` |
| `getent hosts $(hostname -f)` | **127.0.0.1 아님**, C의 LAN IP |
| `grep migration_host /etc/libvirt/qemu.conf` | `migration_host = "<C IP>"` |

```bash
# Host-C
sudo bash setup_sub.sh          # NFS + FQDN + migration_host 재적용
sudo systemctl restart libvirtd

# Host-A에서 점검
ssh root@serverc 'mount | grep libvirt; hostname -f; virsh version'
virsh migrate --live --unsafe --migrateuri tcp://<C_IP>:0 \
  vm-5 qemu+ssh://root@<C_IP>/system

# I/O 후 tunnelled만 쓰면 실패 → 반드시 --p2p 와 함께
virsh migrate --live --unsafe --tunnelled --p2p \
  vm-5 qemu+ssh://root@<C_IP>/system
```

`cannot perform tunnelled migration without using peer2peer flag` → 스크립트가 `--tunnelled --p2p` 로 재시도함 (`git pull` 후 재실행).

---

**A 쪽 설치·실험 전체**는 [HW6_Main.md](HW6_Main.md)만 보면 됩니다.
