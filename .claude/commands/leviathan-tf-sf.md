# TF Semi-Final (SF) — Auto-Chain Wrapper

> QF PASS 후 호출. 완료 후 반드시 `/project:leviathan-tf-pf` 호출.

## 실행

**반드시 `leviathan-tf.md`의 "TF Semi-Final (SF)" 섹션 전체를 Read한 후 수행.**

```
Read(".claude/commands/leviathan-tf.md") → SF 섹션 (단계 1~3) 정독 → 순서대로 수행
```

### 요약 (상세는 leviathan-tf.md 참조)
1. [단계 1-A] Delta Check — QF 이후 코드 변경 검증
2. [단계 1-B] 전략별 독립 검증 — 7개 전략 각각
3. [단계 1-C] 전략 상호작용 검증 — 상관관계, 공유 자원
4. [단계 2] Progressive Shadow (24H+) — Stage 1~4 점진적 검증
5. [단계 3] 병렬 검증 — 보안/퀀트/인프라/UI/데이터

### TeamCreate 필수
- `TeamCreate("tf-sf")` — TWICE 팀 + Jisoo 차출

### 추가 기준 (QF 기준 + SF 추가)
- Sharpe >= 2.0
- Calmar > 0
- 전략별 WR > 50%
- Expected Edge > 0 bps

### 완료 조건
- [ ] Progressive Shadow 24H+ 무중단
- [ ] SF 추가 기준 전부 PASS
- [ ] 산출물: `docs/checklists/tf-semi-final_YYYYMMDD.md`
- [ ] TeamDelete + 좀비 0건
- [ ] "→ 다음: /project:leviathan-tf-pf" 출력

**FAIL → 회귀 Phase → 3-Stage(A~C) → SF 재검증 (구조적 결함 시 QF부터)**
