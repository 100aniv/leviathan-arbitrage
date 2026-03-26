# SIT-3 종합테스트 전용 워크플로우

> 이 문서는 LEVIATHAN 표준 워크플로우(Stage A→B→C)와 **별도**입니다.
> 상세 플랜: `.claude/plans/streamed-dazzling-music.md`

## 실행 흐름

```
Phase 0 (사전 준비) → Shadow 시작 → 10팀 병렬 검증 → CP1~CP9 체크포인트
→ Fix Loop (문제 발견 시) → 411개 전부 GREEN + 72H 무중단 = PASS
```

## 핵심 규칙

1. **하이브리드 리셋**: CP1~CP3 부분 리셋, CP4+ 전체 0분 리셋
2. **96H 하드캡**: 72H 목표 + 24H 버퍼
3. **거짓 보고 방지**: 증거 필수, 2명 독립, AI CLI 교차
4. **동적 증원**: 리더가 필요 시 에이전트 추가

## 참조 파일

| 파일 | 용도 |
|------|------|
| `.claude/plans/streamed-dazzling-music.md` | 상세 플랜 (411 시나리오) |
| `.omc/state/sit3-checklist.json` | 시나리오 체크리스트 |
| `.omc/state/sit3-team-roster.json` | 10팀 배정표 |
| `.omc/state/sit3-reset-log.json` | 리셋 기록 |
| `.claude/agents/sit3-lead.md` | QA Lead 에이전트 |
| `engine/src/workflow/sit3_gate.py` | CP 자동 판정 |
| `scripts/sit3_canary_runner.py` | 72H 연속 실행기 |
