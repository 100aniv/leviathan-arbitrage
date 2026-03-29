# TF Quarter-Final — 1시간 종합 검증

> SIT-3 PASS 후 호출. 완료 후 `/project:tf-sf` 호출.

## 수행 (1시간, 순서 엄수)

### 1. 사전 조건 확인
- SIT-3 PASS 확인 (check_all 9/9)
- Shadow 가동 중
- 에러 0건

### 2. TeamCreate 10팀 검증
- T1: 전략 로직 검증 (quant-validator)
- T2: Shadow 성과 분석 (shadow-tester)
- T3: 대시보드 UIUX (browser-verifier)
- T4: 보안 리뷰 (security-reviewer)
- T5: 코드 리뷰 (code-reviewer)
- T6~T10: 시나리오별 검증

### 3. AI CLI 교차검증
- codex + gemini 독립 검증

### 4. 1시간 Shadow 무중단
- trades > 0
- PnL 확인
- 에러 0건

### 5. 판정
- 10팀 전부 PASS → **QF PASS**
- FAIL 있으면 → Fix Loop → /project:sit3-audit

## 완료 조건
- [ ] 1시간 Shadow 무중단
- [ ] 10팀 검증 완료
- [ ] AI CLI 2개 PASS
- [ ] 에러 0건
- [ ] "→ 다음: /project:tf-sf" 출력
