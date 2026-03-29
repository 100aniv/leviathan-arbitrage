# TF Quarter-Final (QF) — Auto-Chain Wrapper

> SIT-3 PASS 후 호출. 완료 후 반드시 `/project:leviathan-tf-sf` 호출.

## 실행

**반드시 `leviathan-tf.md`의 "TF Quarter-Final (QF)" 섹션 전체를 Read한 후 수행.**

```
Read(".claude/commands/leviathan-tf.md") → QF 섹션 (단계 0~6) 정독 → 순서대로 수행
```

### 요약 (상세는 leviathan-tf.md 참조)
1. [단계 0] Smoke Test Gate — pytest + Docker + Shadow 10min
2. [단계 1] 정합성 확인 — Karina 3-way
3. [단계 2] 체크리스트 수립 — The Blueprint
4. [단계 3] 교차 검증 — 런타임 증거 기반 (A~D 병렬)
5. [단계 3.5] Assembly Verification — 4 sub-check
6. [단계 4] 최종 확인 — Karina→Nayeon, 압박 면접
7. [단계 5] 멀티모델 감사 — codex/gemini/qwen
8. [단계 6] 기술 부채 목록

### TeamCreate 필수
- `TeamCreate("tf-qf")` — TWICE 팀 스폰 (leviathan-tf.md 팀 로스터 참조)

### AI CLI 필수
- 단계 5에서 codex + gemini + qwen 병렬 실행
- quorum 2+ = MUST FIX

### 완료 조건 (leviathan-tf.md 기준)
- [ ] CRITICAL 0, HIGH 0, MEDIUM ≤ 5
- [ ] Assembly 4-check PASS
- [ ] 멀티모델 MUST FIX 0건
- [ ] 산출물: `docs/checklists/tf-quarter-final_YYYYMMDD.md`
- [ ] TeamDelete + 좀비 0건
- [ ] "→ 다음: /project:leviathan-tf-sf" 출력

**FAIL → 회귀 Phase → 3-Stage(A~C) → QF 재검증**
