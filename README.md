# HW6 — KVM Live Migration + 2D Bin Packing

**Advanced Cloud Computing 2026**  
강형빈 · 정선미 · 정윤민  
제출 마감: **2026년 5월 19일 13:50**

---

## 파일 구성

```
ACC/
├── README.md
├── HW6_사전검증.md    # VirtualBox: 스크립트 설치 + 오퍼레이터 Migration까지
├── HW6_설치.md        # Bare metal 실제 설치
├── HW6_실험.md        # Bare metal Case 1~3 시연·측정
├── setup_main.sh
├── setup_sub.sh
├── create_vms.sh
└── migration_dashboard.py
```

---

## 진행 순서

| 순서 | 문서 | 환경 | 내용 |
|------|------|------|------|
| 1 | [HW6_사전검증.md](HW6_사전검증.md) | **VirtualBox** | VM·네트워크 → `setup_*.sh` / `create_vms.sh` → 대시보드 `r`/`c`/`l`/`m`로 Migration 검증 |
| 2 | [HW6_설치.md](HW6_설치.md) | **Bare metal** | 동일 스크립트로 물리 3호스트 설치 |
| 3 | [HW6_실험.md](HW6_실험.md) | **Bare metal** | stress-ng + Case 1~3 시연, Downtime·제출 자료 |

---

## 제출 안내

- **파일명**: `HW6-학번-이름.pdf`
- **제출처**: https://lms.cbnu.ac.kr/
- **제출물**: 구성도 + Before/After 수치 + 시연 스크린샷

---

## 트러블슈팅

상세는 [HW6_사전검증.md — 트러블슈팅](HW6_사전검증.md#트러블슈팅)
