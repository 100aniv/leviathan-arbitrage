# SIT-3 Audit — 현재 상태 감사

> Auto-Chaining 1/4단계. 완료 후 반드시 `/project:sit3-plan` 호출.

## 수행 (순서 엄수, 스킵 금지)

1. **SSOT.md Read** — `Read("SSOT.md")` §2 현재 상태 확인
2. **SIT-3_WORKFLOW.md Read** — `Read("engine/docs/planning/SIT-3_WORKFLOW.md")` 워크플로우 규칙 재확인
3. **sit3-checklist.json** — 진행률 확인: GREEN/PENDING/FAIL 카운트
4. **check_all 9/9** — `cd engine && python -m src.workflow.cli check_all`
5. **Shadow stats** — 엔진 가동 중이면 API 확인 (trades, PnL, 전략별)
6. **에러 확인** — `grep -c ERROR /tmp/leviathan-shadow.log`
7. **/devils-advocate** — 현재 상태에 대해 반론:
   - "이 상태에서 놓치고 있는 것은?"
   - "거짓 양성으로 PASS된 항목은?"
   - "다음에 가장 터질 가능성이 높은 것은?"

## 출력 형식

```
[AUDIT] 2026-03-XX HH:MM
- SSOT: Phase=SIT-3, Tests=X, PRD=X/343
- Checklist: X GREEN / X PENDING / X FAIL (총 411)
- check_all: X/9 OK
- Shadow: Xmin, trades=X, PnL=$X
- Errors: X건
- DA 반론: [요약]
→ 다음: /project:sit3-plan
```

8. **Notion 확인** — Notion 페이지에서 현재 진행 상태 확인 (연동 시)
9. **DevBot 확인** — DevBot watchdog 상태 + `/go` 재개 필요 여부

## 완료 조건 (DoD)
- [ ] SSOT.md Read 완료
- [ ] SIT-3_WORKFLOW.md Read 완료
- [ ] checklist 진행률 출력
- [ ] check_all 실행 + 결과
- [ ] /devils-advocate 반론 1건 이상
- [ ] Notion 상태 확인 (연동 시)
- [ ] "→ 다음: /project:sit3-plan" 출력

**DoD 미충족 시 다음 단계 진행 금지.**
