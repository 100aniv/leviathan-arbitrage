# SIT-3 Execute — 작업 실행

> Auto-Chaining 3/4단계. `/project:sit3-plan` 완료 후 호출. 완료 후 `/project:sit3-verify` 호출.

## 수행 (순서 엄수, 스킵 금지)

1. **SIT-3_WORKFLOW.md Read** — 매번 강제. 기억에 의존 금지.
2. **코드 수정** — sit3-plan 결과대로 실행
   - Agent Teams = **TeamCreate 필수** (직접 bash/grep 수정이 아닌 에이전트 대화)
   - 전략 수정 시 quant-validator 검증 병행
3. **pytest PASS** — `cd engine && python -m pytest tests/ -x --tb=short -q`
4. **check_all 9/9** — `python -m src.workflow.cli check_all`
5. **문서 동기화** (매번 필수):
   - SSOT.md §2 업데이트
   - prd.json 관련 US acceptanceCriteria 업데이트
   - CLAUDE.md "자주 틀리는 패턴" 업데이트 (해당 시)
   - engine/.env + 루트 .env 동기화
   - sit3-checklist.json 수정 시나리오 PENDING 리셋
6. **git commit + push** — `"SIT-3: [수정내용]"` 형식
7. **checkpoint save** — `python -m src.workflow.cli checkpoint save`

## 출력 형식

```
[EXECUTE] Fix Loop #N
- 수정: X파일, Y라인
- Tests: X passed / 0 failed
- check_all: 9/9 OK
- 문서: SSOT ✓ prd ✓ CLAUDE ✓ .env ✓
- Git: [commit hash]
- Checkpoint: [ID]
→ 다음: /project:sit3-verify
```

## 완료 조건 (DoD)
- [ ] 코드 수정 완료 (TeamCreate 사용)
- [ ] pytest PASS (0 failed)
- [ ] check_all 9/9
- [ ] SSOT + prd + CLAUDE + .env 동기화
- [ ] git commit + push
- [ ] checkpoint save
- [ ] "→ 다음: /project:sit3-verify" 출력

**DoD 미충족 시 다음 단계 진행 금지.**
