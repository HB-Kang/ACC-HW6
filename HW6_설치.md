# HW6 실제 설치 — Bare Metal (듀얼부팅)

[← 사전검증](HW6_사전검증.md) · [README](README.md) · 다음: [실험](HW6_실험.md)

VirtualBox에서 [사전검증](HW6_사전검증.md)을 마친 뒤, **물리 3대(또는 3파티션 듀얼부팅)** 에 동일 스크립트로 설치합니다.

> 명령·스크립트는 사전검증과 동일합니다. IP만 bare metal LAN에 맞게 바꿉니다.

---

## 1. 호스트 OS

- Rocky Linux 10 **Minimal** × 3 (Host-A/B/C)
- 3대가 **같은 L2 네트워크** (스위치/공유 Wi-Fi)
- `/etc/hosts`에 `servera` / `serverb` / `serverc` 등록 후 ping

---

## 2. 스크립트 설치

```bash
# Host-A
bash setup_main.sh <Host-B-IP> <Host-C-IP>

# Host-B, C
bash setup_sub.sh <Host-A-IP>
```

### 확인

```bash
df -h | grep libvirt          # B, C
ssh-copy-id root@serverb      # A
virsh -c qemu+ssh://root@serverb/system list --all
bash create_vms.sh            # A only
```

---

## 3. bare metal vs VirtualBox 차이

| 항목 | VirtualBox (사전검증) | Bare metal |
|------|----------------------|------------|
| Nested VT | PowerShell 설정 필요 | 불필요 |
| 성능 | 느림, Migration 시간 김 | 실습·측정용 |
| IP | 브리지 LAN | 실험실/자택 LAN |
| `HOSTS_CONFIG` | VBox IP | 물리 IP로 수정 |

---

## 트러블슈팅

[NFS / virsh / storage](HW6_사전검증.md#트러블슈팅) — 사전검증 문서와 동일
