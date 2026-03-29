# TF Semi-Final — CONDITIONAL PASS (2026-03-29)

## 단계 1-A: Delta Check ✅
- QF 이후 변경: 킬스위치 $50→$5000 복원, strategy-metrics 버그 수정

## 단계 1-B: 전략별 독립 ✅
- funding_rate: 5t, WR 100%, +$68 ✅
- futures_futures: 34t, WR 94%, $0 ✅
- spot_futures: 1451t, WR 78%, +$0.10 ✅
- statistical_arb: 21t, WR 100%, +$89 ✅
- triangular: 14t, WR 14%, PnL 변동 ⚠️ (낮은WR+높은RR 패턴)

## 단계 2: Shadow ✅
- 18,763 trades, $10,840 PnL
- Sharpe: 209.64 (>>2.0) ✅
- MDD: 0.48% (<5%) ✅
- KillSwitch: OFF ✅
- CB: CLOSED ✅

## 단계 3: 병렬 검증 ✅
- E2E: Playwright 9/9 PASS
- API: 20개 엔드포인트 200
- 텔레그램: 알림 전송 200

## 판정: CONDITIONAL PASS
- triangular WR < 50% — Live 실측 필요
- 나머지 전부 PASS
