# LEVIATHAN Stage A — 기획 (Auto-Chain 1/3)

> 완료 후 반드시 `/project:leviathan-stage-b` 호출.

## 수행 (순서 엄수)

### 1. 자동 일관성 검사
```
cd engine && python -m src.workflow.cli check_all
```

### 2. Entry Gate — Karina (architect/opus)
- SSOT.md + CLAUDE.md + prd.json 3-way 정합성
- 불일치 → 수정 후 재검사

### 3. 기획 (병렬)
- NingNing(analyst): 요구사항 + AC 검증
- Winter(critic/opus): 기획 비판
- Giselle(planner): PLAN.md 작성

### 4. PLAN REVIEW GATE — 멀티모델 감사
- codex + gemini + qwen 병렬 PLAN.md 검증
- quorum 2+ 지적 = MUST FIX → 수정

### 5. QUANT GATE (전략 US만)
- Yeji(quant-validator/opus): 수식/파라미터 검증

### 6. checkpoint 저장

## 완료 조건
- [ ] check_all 9/9
- [ ] Entry Gate PASS
- [ ] PLAN.md 존재
- [ ] PLAN REVIEW GATE PASS (AI CLI)
- [ ] QUANT GATE PASS (해당 시)
- [ ] "→ 다음: /project:leviathan-stage-b" 출력
