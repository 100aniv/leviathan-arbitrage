# SIT-3 종합테스트 전용 워크플로우

> 이 문서는 LEVIATHAN 표준 워크플로우(Stage A→B→C)와 **별도**입니다.
> 상세 플랜: `.claude/plans/streamed-dazzling-music.md`

## 실행 흐름

```
Phase 0 (사전 준비) → Shadow 시작 → 10팀 병렬 검증 → CP1~CP9 체크포인트
→ Fix Loop (문제 발견 시) → 411개 전부 GREEN + 72H 무중단 = PASS
```

## 핵심 규칙

1. **플랜 = 유일한 법전**: `streamed-dazzling-music.md`가 모든 판단의 기준. 임의 판단/축약/누락 절대 금지
2. **72H는 결과이지 목적이 아님**: 완벽한 프로그램이 목적. 수정→재시작→검증 무한 반복. SKIP/FAIL이 있으면 즉시 수정. 더 이상 수정 없을 때 자연스럽게 72H 달성
3. **엔진 중단 = 아무 문제 없음**: 수정이 우선. 연속 실행 시간은 신경 쓰지 않는다. 수정할 것 있으면 수정하고 다시 시작하고 무한 반복. 실행을 해 놔야 플로우가 보이고 제대로 동작하는지 시나리오 점검 가능하니 실행하는 것. "리셋 최소화" 따위 생각하지 말 것
4. **매 CP 전환 시 플랜 정독**: 해당 CP 섹션을 반드시 Read한 후 수행. 기억에 의존하지 않음
5. **Agent Teams 구조 필수**: 직접 bash/grep 검증이 아닌 TeamCreate + 에이전트 대화로 수행. 무조건 TeamCreate
6. **하이브리드 리셋**: CP1~CP3 부분 리셋, CP4+ 전체 0분 리셋
7. **96H 하드캡**: 72H 목표 + 24H 버퍼
8. **거짓 보고 방지**: 증거 필수, 2명 독립, AI CLI 교차. 로그 0건 = FAIL (PASS 아님)
9. **동적 증원**: 리더가 필요 시 에이전트 추가

## Fix Loop 핵심 원칙

> 사장님 지시: "단순 연속 실행이 중요한게 아니라 수정할게 있으면 수정하고 다시 시작하고 무한 반복. 그렇게 해서 완벽하게 다 되었을 때 연속 실행이 되어야 의미가 있는거지 완성된 프로그램에서."

```
WHILE NOT 완벽:
    1. 문제 발견 → 엔진 즉시 중단 (두려워하지 마라)
    2. 코드 수정 (Agent Teams: debugger + build-fixer + executor)
    3. pytest PASS 확인
    4. Docker 리빌드
    5. Shadow 재시작 (타이머 0분)
    6. 10팀 병렬 재검증
    7. 문제 없으면 자연스럽게 시간이 쌓임
    8. 모든 것 완벽 → 72H 자연 달성
```

## 체크포인트 Git 규칙

> 모든 CP 전환 + Fix Loop 완료 시 반드시 git commit + push

```
1. 코드 수정 완료 시: git add → commit → push
2. CP 도달 시: checkpoint save → git add → commit → push
3. Fix Loop 완료 시: pytest PASS 확인 → git add → commit → push
4. 커밋 메시지: "SIT-3: CP{N} {설명}" 또는 "fix: SIT-3 {수정내용}"
```

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
