# Giselle — Phase J Plan Artifact

> 생성일: 2026-04-01

## 생성한 US 목록

| ID | 제목 | 핵심 AC | WIRING AC |
|----|------|---------|-----------|
| US-351 | BacktestMode main.py 배선 + BACKTEST 경로 TimescaleDB 활성화 | `backtest.starting` 로그 + `await backtest_mode.run()` 실제 호출 | 생성/주입/호출 3개 포함 |
| US-352 | orderbook_snapshots 데이터 충분성 검증 + fallback 전략 | `backtest.data_check` 로그 + 0건 graceful return | 없음 (기존 클래스 확장) |
| US-353 | 전 6전략 WFA 실행 엔진 + 결과 JSON 저장 | `wfa.starting` 로그 + `/api/backtest/wfa` 200 OK + `backtest_results.json` | 없음 (기존 analyzer 호출) |
| US-354 | ML A/B 비교 프레임워크 (ML 활성/비활성 WFA) | `ml_backtest.ab_result fold=5/5` 로그 + `/api/backtest/ab-test` 200 OK | 없음 |
| US-355 | 대시보드 /backtest 페이지 (Sharpe/MDD/PF 시각화) | `page.tsx` 존재 + 브라우저 200 응답 | 없음 (Next.js 페이지 신규) |
| US-356 | Phase J 통합 검증 + LiveGate WFA 연동 확인 | `crash 0건` + `backtest.completed` 로그 순서 | 없음 |

## 저장 경로
- PLAN.md: `engine/docs/planning/Phase-J_PLAN.md`
- prd.json: `/Users/100aniv/Development/arbitrage_OMC/.omc/prd.json`

## prd.json 업데이트 확인
- 추가 US 수: 6개 (US-351 ~ US-356)
- 신규 total_stories: 356
- 모두 `passes: false` (런타임 증거 전 선언 금지 원칙)
