# HW6 전체 실험 진행 가이드 (팀 공통)

**담당별 상세 문서는 파일이 나뉘어 있습니다. 본인 역할 문서만 읽으세요.**

| 담당 | 읽을 문서 |
|------|-----------|
| **팀원 A** (servera) | **[HW6_Main.md](HW6_Main.md)** |
| **팀원 B** (serverb) | **[HW6_Sub.md](HW6_Sub.md)** |
| **팀원 C** (serverc) | **[HW6_Sub.md](HW6_Sub.md)** |

[README](README.md)

---

## 1. 한 줄 요약

```
[팀] 192.168.0.<D> × 3 합의 + 각 PC NIC 설정
  →  A: setup_main.sh
  →  B,C: setup_sub.sh
  →  A: create_vms.sh → migration_dashboard.py
  →  제출 PDF
```

---

## 2. 역할 표

| PC | 사람 | 호스트 | 설치 | VM·실험 UI |
|----|------|--------|------|------------|
| A | 팀원 A | servera | `setup_main.sh` | `create_vms.sh`, 대시보드 |
| B | 팀원 B | serverb | `setup_sub.sh` | 수신·관찰만 |
| C | 팀원 C | serverc | `setup_sub.sh` | 수신·관찰만 |

---

## 3. 공통 사전 준비 (3대)

- Rocky Linux 10 Minimal, VT-x/AMD-V ON, RAM 8GB+ 권장  
- **같은 LAN** (물리 스위치 / VBox 브리지)  
- 주소: **`192.168.0.<D>`** — D값 3개 팀 합의, **NIC에 수동 설정**  
- 스크립트 받기 (각 PC에서 한 번):

  ```bash
  sudo dnf install -y git
  cd ~ && git clone https://github.com/HB-Kang/ACC-HW6.git && cd ACC-HW6
  ```

  자세한 단계: A → [HW6_Main.md §1-1](HW6_Main.md#1-1-git-설치--저장소-받기), B/C → [HW6_Sub.md §1-1](HW6_Sub.md#1-1-git-설치--저장소-받기)

- 실행: **root** (`cd ~/ACC-HW6` 후 `sudo bash ...`)

---

## 4. 타임라인

```
[Phase 0] D값 합의 · NIC IP · ping
[Phase 1] A: setup_main  →  B: setup_sub  →  C: setup_sub
[Phase 2] A: create_vms  →  A: dashboard Case 1~3
[Phase 3] 제출 PDF
```

---

## 5. config / SSH (팀이 알면 좋은 것)

A의 `setup_main.sh`가 `/etc/hw6/cluster.conf`를 만들고 NFS에 복사합니다.

| 경로 | 용도 |
|------|------|
| `/etc/hw6/cluster.conf` | IP·용량 (대시보드가 읽음) |
| `.../hw6/cluster.conf` on NFS | B/C가 sub 때 로드 |
| `.../hw6/keys/*.pub` | SSH 무암호 (공개키만) |

---

## 6. VirtualBox (선택)

1. Nested VT: `VBoxManage modifyvm "VM이름" --nested-hw-virt on`  
2. 네트워크: **브리지**  
3. Rocky IP를 합의한 `.10/.11/.12`에 맞춤  
4. 이후 **§4 타임라인**과 동일 (문서는 A→Main, B/C→Sub)

---

## 7. 트러블슈팅 (요약)

| 증상 | 누가 보나 |
|------|-----------|
| ping 실패 | 전원 — NIC·LAN |
| NFS 마운트 | B/C → [Sub](HW6_Sub.md); A NFS 서비스 → [Main](HW6_Main.md) |
| SSH/virsh 원격 | A → [Main](HW6_Main.md); keys 3개 → B/C [Sub](HW6_Sub.md) |
| 대시보드 오류 | A — `cluster.conf` IP |

---

## 8. 문서 목록

| 파일 | 대상 |
|------|------|
| **HW6_Main.md** | 팀원 A 전용 (설치+실험) |
| **HW6_Sub.md** | 팀원 B·C 전용 (설치+실험) |
| **본 문서** | 팀 전체 타임라인·합의 사항 |
| **README.md** | 제출·저장소 |
