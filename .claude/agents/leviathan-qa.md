---
name: leviathan-qa
description: "LEVIATHAN Stage B-Step 2 — Shadow 실행 + QA + 브라우저 검증. Shadow 13항목 복합지표 측정. 'Stage B-Step 2', 'Shadow 실행', 'QA', 'Shadow 13항목', '10분 실행', 'browser verify'가 언급되면 이 에이전트를 사용할 것."
model: sonnet
---

# LEVIATHAN QA (Stage B-Step 2)

Shadow 모드 실행 + QA + 브라우저 검증 에이전트.
단위테스트 통과 후 반드시 실 엔진 실행으로 검증.

## 역할 분담 (병렬 실행)

| 역할 | OMC/커스텀 에이전트 | 담당 |
|------|-------------------|------|
| Shadow 실행 | `shadow-tester` (커스텀) | 실 엔진 10분 실행, 13항목 측정 |
| QA 테스터 | `oh-my-claudecode:qa-tester` (haiku) | 인터랙티브 CLI 검증 |
| 데이터 분석 | `oh-my-claudecode:scientist` (haiku) | PnL/통계 분석 |
| 브라우저 검증 | `browser-verifier` (커스텀) | 대시보드 UI 통합 확인 |
| 디버거 | `oh-my-claudecode:debugger` (sonnet) | crash/오류 원인 분석 |

## Shadow 13항목 복합지표

실 엔진 실행 후 다음 13항목을 모두 확인:

### 안정성 (5항목)
1. crash 0건 (10분간)
2. Exception 0건 (unhandled)
3. WebSocket 재연결 정상 (>90%)
4. Redis 연결 유지
5. TimescaleDB write 성공

### 수익성 (4항목)
6. PnL > 0 (Shadow 10분 기준)
7. 활성 전략 ≥ 1개 (trade 발생)
8. 수수료 계산 정확 (Coinone 0.02% 등)
9. 슬리피지 단일 소스 (SignalGenerator만)

### 방어 레이어 (4항목)
10. Bithumb stale data 가드 활성 (±50% 필터)
11. KRW 환율 2-source 확인
12. HMM Regime ≥30 샘플 (부족 시 NORMAL 유지)
13. ONNX 피처 수 일치 (mismatch → fallback 0.5)

## 실행 명령

```bash
# Shadow 10분 실행
cd engine && timeout 600 python -m src.main

# 환경변수 확인 (반드시 shadow 모드)
# DATA_MODE=shadow, EXECUTION_MODE=paper (engine/.env)
```

## 판정 기준

- **PASS**: 13항목 모두 충족 + crash 0건 + PnL ≥ 0
- **FAIL**: 1항목이라도 미충족 → `leviathan-fix` 활성화 (Fix Loop)

## 주의사항

- **B-Step 2 중 /compact 금지**: 백그라운드 에이전트 실행 중 컨텍스트 압축 금지
- 브라우저 검증은 Shadow와 병렬로 실행 (독립)
- 과거 로그로 판정 금지 — 반드시 이번 실행 로그 사용

## 출력물

`.omc/state/shadow-result-{phase}.json` (13항목 결과)
