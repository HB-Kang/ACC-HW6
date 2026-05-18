# HW6 실제 실험 — Bare Metal 시연·측정

[← 설치](HW6_설치.md) · [README](README.md)

사전검증에서 확인한 **오퍼레이터 흐름**을 bare metal에서 반복하고, **Case 1~3** 부하·수치·스크린샷을 제출용으로 수집합니다.

---

## 1. 워크로드 (stress-ng)

```bash
virsh console vm-1    # Ctrl+]

stress-ng --cpu $(nproc) --timeout 0 &
stress-ng --vm 1 --vm-bytes 80% --timeout 0 &
```

| 케이스 | 부하 |
|--------|------|
| Case 1 | 초기 배치 유지 |
| Case 2 | vm-1,2,3 CPU / vm-4,5,6 MEM |
| Case 3 | Host-A VM 전체 CPU → 90%+ |

---

## 2. 오퍼레이터 시연 (대시보드)

```bash
python3 migration_dashboard.py
```

| 케이스 | 키 | 목표 |
|--------|-----|------|
| Case 1 Consolidation | `c` → `m` | Host-C Idle |
| Case 2 Defragmentation | `r` → `m` | CPU/MEM 균등 분산 |
| Case 3 Load Balancing | `l` → `m` | 최대 부하 80% 이하 |

### 시연·기록 순서

1. Before 스크린샷 (호스트 CPU/MEM, VM 배치)  
2. stress-ng 투입  
3. `c` / `r` / `l` → STATUS 재배치 계획 캡처  
4. `m` → 진행률·Dirty rate 캡처  
5. After + `virsh domjobinfo --completed <vm>` (Downtime, Total time)  

---

## 3. 수동 Migration (보조)

대시보드 없이 `virsh`만 쓸 때:

```bash
virsh migrate --live --persistent --undefinesource \
    vm-5 qemu+ssh://root@servera/system
watch -n 0.5 'virsh domjobinfo vm-5'
```

---

## 4. Downtime 측정

```bash
virsh domifaddr vm-5
ping <VM-IP>    # Migration 중 패킷 드롭 ≈ Downtime
virsh domjobinfo --completed vm-5
```

---

## 5. 케이스별 Before / After (참고)

**Case 1** (`c`): A(6/8 CPU, 6G) B(5/8, 6G) C(3/8, 6G) → C **IDLE**  
**Case 2** (`r`): A(CPU 87%, MEM 12%) … → 3호스트 균등  
**Case 3** (`l`): A(CPU 94%) … → A~56%, B~56%, C~44%

제출 PDF: 구성도 + 위 수치 + 시연 캡처 → [README 제출 안내](README.md#제출-안내)
