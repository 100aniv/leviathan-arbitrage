# SIT-3 Verify — 검증

> Auto-Chaining 4/4단계. `/project:sit3-execute` 완료 후 호출. 완료 후 `/project:sit3-audit` 재호출 (무한 루프).

## 수행 (순서 엄수, 스킵 금지, curl만으로 PASS 불가)

### 1. Shadow 실행 검증
- 엔진 재시작 (코드 변경 반영)
- Shadow 10min+ 실행
- trades > 0, PnL 확인, crash=0

### 2. 10팀 TeamCreate 검증
- **TeamCreate 필수** (Agent() 서브에이전트 아님)
- T1~T10 각 팀 시나리오 검증
- 팀 보고서 수집 후 TeamDelete + 좀비 정리

### 3. AI CLI 교차검증
- `codex exec "[검증 프롬프트]"` — 코드 리뷰
- `gemini -p "[검증 프롬프트]"` — 독립 검증
- 2개 모두 PASS 필요

### 4. 브라우저 UI/UX 검증 (curl만으로 PASS 불가)
- **Playwright MCP** — 13페이지 실제 렌더링
  - 각 페이지 로드 + 데이터 표시 확인
  - 콘솔 에러 0건 (DevTools)
  - 모바일 375px/768px 반응형
- **사용자 플로우 재현**:
  - 로그인 → 대시보드 → 각 페이지 → 기능 동작 → 모드 전환 → 설정 변경
- **스크린샷 저장**: `.omc/state/sit3-results/screenshots/`
- 도구: Playwright MCP (QA 정확도 100%) + claude-chrome (로그인 세션)

### 5. /devils-advocate
- 검증 결과에 대한 반론
- "이 검증이 놓친 것은?"
- "실전에서 다르게 동작할 가능성은?"

### 6. 결과 기록
- sit3-checklist.json 업데이트 (GREEN/FAIL)
- SSOT.md 업데이트
- Notion 업데이트 (연동 시)

### 7. 판정
- **FAIL 있음** → "→ 다음: /project:sit3-audit" (Fix Loop 재진입)
- **전부 PASS** → CP 진행 → "→ 다음: /project:sit3-audit" (다음 CP)
- **411 전부 GREEN + 24H 무중단** → **SIT-3 PASS** 선언

## 출력 형식

```
[VERIFY] CP{N} — Fix Loop #{M}
- Shadow: Xmin, trades=X, PnL=$X
- Teams: X/10 PASS
- AI CLI: Codex PASS/FAIL, Gemini PASS/FAIL
- Browser: X/13 페이지 PASS, 스크린샷 X장
- DA 반론: [요약]
- Checklist: X GREEN / X PENDING / X FAIL
- 판정: PASS / FAIL (사유)
→ 다음: /project:sit3-audit
```

## 완료 조건 (DoD)
- [ ] Shadow 10min+ 실행 (crash=0)
- [ ] 10팀 TeamCreate 검증 완료 + TeamDelete + 좀비 0건
- [ ] AI CLI 2개 PASS (codex + gemini)
- [ ] Playwright 브라우저 13페이지 검증 + 스크린샷
- [ ] /devils-advocate 반론 1건 이상
- [ ] sit3-checklist.json 업데이트
- [ ] "→ 다음: /project:sit3-audit" 출력

**DoD 미충족 시 다음 단계 진행 금지.**
