# US-043: Grafana 대시보드 프리셋 (18개 메트릭)

## Acceptance Criteria
1. Grafana 대시보드 JSON 프리셋 생성
2. 18개 핵심 메트릭 패널: PnL, WR, DD, latency, spread, trades 등
3. docker compose up 후 Grafana에서 자동 로드

## 파일 변경
| 파일 | 변경 | 담당 |
|------|------|------|
| infra/grafana/dashboards/leviathan.json | NEW — 18 metric panels | Jisoo |

## 18개 메트릭 목록
| # | 메트릭 | 타입 | PromQL |
|---|--------|------|--------|
| 1 | Total PnL | stat | leviathan_pnl_total_usd |
| 2 | Win Rate | stat | trades win/total |
| 3 | Current Drawdown | stat | leviathan_drawdown_current_pct |
| 4 | PnL per Trade | stat | pnl/trades |
| 5 | PnL Over Time | timeseries | leviathan_pnl_total_usd |
| 6 | Total Trades | stat | leviathan_trades_total |
| 7 | Trades/min | stat | rate(trades[5m]) |
| 8 | Open Positions | stat | leviathan_open_positions |
| 9 | Signals/min | stat | rate(signals[5m]) |
| 10 | Spread Distribution | timeseries | histogram p50/p95/p99 |
| 11 | Order Latency | timeseries | histogram p50/p95/p99 |
| 12 | Signal Processing Time | timeseries | histogram p50/p95 |
| 13 | Orderbook Update Time | timeseries | histogram p50/p95 |
| 14 | Kill Switch Triggers | stat | leviathan_kill_switch_triggers_total |
| 15 | Circuit Breaker State | stat | leviathan_circuit_breaker_state |
| 16 | Risk Rejections | stat | leviathan_risk_rejections_total |
| 17 | Errors/min | stat | rate(errors[5m]) |
| 18 | Exchange Health Score | timeseries | 8 exchanges |
