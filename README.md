# HW6 — KVM Live Migration + 2D Bin Packing

**Advanced Cloud Computing 2026**  
강형빈 · 정선미 · 정윤민  
제출 마감: **2026년 5월 19일 13:50**

저장소: https://github.com/HB-Kang/ACC-HW6

---

## 누가 어떤 문서를 읽나요?

| 담당 | PC | **읽을 문서** |
|------|-----|----------------|
| **팀원 A** | Host-A · `servera` | **[HW6_Main.md](HW6_Main.md)** ← 설치 + VM + 실험 전부 |
| **팀원 B** | Host-B · `serverb` | **[HW6_Sub.md](HW6_Sub.md)** ← 설치 + 실험 때 할 일 |
| **팀원 C** | Host-C · `serverc` | **[HW6_Sub.md](HW6_Sub.md)** ← 설치 + 실험 때 할 일 |
| **팀 전체** (순서·합의만) | — | [HW6_전체가이드.md](HW6_전체가이드.md) |

> A는 Sub 문서를, B/C는 Main 문서를 **읽을 필요 없습니다.**

---

## 진행 순서 (한 줄)

```
D값 합의 + NIC IP  →  A: setup_main  →  B,C: setup_sub  →  A: create_vms + 대시보드
```

---

## 파일 구성

```
ACC/
├── README.md
├── HW6_전체가이드.md    # 팀: 타임라인·합의만
├── HW6_Main.md          # 팀원 A 전용
├── HW6_Sub.md           # 팀원 B·C 전용
├── hw6_config.sh
├── setup_main.sh        # A만 실행
├── setup_sub.sh         # B·C만 실행
├── create_vms.sh        # A만 실행
└── migration_dashboard.py   # A만 실행
```

---

## 제출 안내

- **파일명**: `HW6-학번-이름.pdf`
- **제출처**: https://lms.cbnu.ac.kr/
- **제출물**: 구성도 + Before/After 수치 + 시연 스크린샷

---

## 트러블슈팅

- A: [HW6_Main.md §6](HW6_Main.md#6-a-트러블슈팅)
- B/C: [HW6_Sub.md §5](HW6_Sub.md#5-bc-트러블슈팅)
- 팀 공통: [HW6_전체가이드.md §7](HW6_전체가이드.md#7-트러블슈팅-요약)
