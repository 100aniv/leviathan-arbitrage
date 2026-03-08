# US-044: 자동 알림 규칙 (PnL 급락, WS 끊김, 높은 slippage)

## Acceptance Criteria
1. Prometheus alerting rules YAML 생성
2. PnL 급락 (-$10 이상) → Telegram
3. WS 3개 이상 끊김 → Telegram
4. slippage > 50bps → Telegram
5. 일일 PnL 요약 자동 전송 (UTC 0시)

## 파일 변경
| 파일 | 변경 |
|------|------|
| infra/prometheus/alerts.yml | EDIT — leviathan.auto_alerts 그룹 추가 (4 rules) |

## 신규 규칙
| Alert | Severity | Expr |
|-------|----------|------|
| pnl_sudden_drop | critical | PnL 5분 내 $10+ 하락 |
| multiple_ws_disconnected | critical | exchange_health_score==0 count >= 3 |
| high_slippage | warning | spread p95 > 50bps (2min) |
| daily_pnl_summary | info | UTC 0시 PnL 요약 |
