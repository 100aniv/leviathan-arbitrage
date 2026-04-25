# LEVIATHAN Operator Runbook

> 사장님이 매일 아침 확인해야 할 최소 체크리스트, 주요 메트릭 해석, 알람 대응 절차.
> 업데이트: 2026-04-22 (paper 모드 universe_matrix 함정 수정 후 기준)

---

## 0. 현재 운영 상태

| 항목 | 값 | 비고 |
|------|-----|------|
| 엔진 mode | `paper` | Path-B 리팩토링 완료까지 live 금지 |
| 오픈 포지션 | 0 | Binance 확인 필 (`scripts/check_positions.py`) |
| 누적 실손실 (24H) | -$4.92 | v227~v237 누적. 수수료 50%, realized 47%, funding 3% |
| 14h 카나리 (2026-04-21~22) | 무효 | universe_matrix=0 환경, paper trade fill 0건. 원인: paper 어댑터 2개 하드코딩 + market_type 미구현 |
| 수정 (2026-04-22) | `3d37e91` `e5a28b2` | paper 어댑터 2→7 + ExchangeAdapter Protocol 완전 구현. universe_matrix entries 0→34. 5분 실행 trade=5/$2.18 검증 |
| 다음 re-enable gate | Stage-1 canary ($10 × 48H) | universe_matrix=34 환경에서 재실행. REFACTOR_PLAN.md 끝 섹션 참조 |

---

## 0.5 Pre-canary 점검 (paper 카나리 시작 전 5분 필수)

> 14h 카나리 헛수고 재발 방지. 4 항목 모두 충족 안 하면 카나리 시작 금지.

```bash
# 5분 dry-run + grep으로 4 항목 확인:
cd /Users/100aniv/Development/arbitrage_OMC/engine
python -m src.main > /tmp/precheck.log 2>&1 &
PID=$!; sleep 300; kill $PID; sleep 3; kill -9 $PID 2>/dev/null

# 1) universe_matrix entries > 0 (paper 어댑터 + ExchangeAdapter Protocol 완전성)
grep "universe_matrix.built" /tmp/precheck.log | tail -1
# 기준: entries >= 30, exchanges == config.exchanges.active 갯수 (보통 7)

# 2) paper trade fill 발생
grep -cE "paper_mode\.trade_request_executed" /tmp/precheck.log
# 기준: >= 1 (5분 동안)

# 3) crash 0
grep -cE "CRITICAL|Traceback|FATAL" /tmp/precheck.log
# 기준: 0 (startup WS noise 제외)

# 4) PnL > 0 (참고용, 작은 sample이라 100% WR 가능)
grep -oE "total_pnl=[+-][0-9.]+" /tmp/precheck.log | tail -1
# 기준: 양수
```

4/4 PASS 후 본 카나리 시작. 1/4 라도 fail이면 entries=0 / paper adapter / strategy halt 등 root cause 조사.

---

## 1. 매일 아침 체크리스트 (2분)

### 1.1 Daily Reconciliation Report 확인
Telegram DevBot 채널에 UTC 00:05에 자동 발송. 없으면 엔진 또는 스케줄러 문제.

읽는 순서:
1. **Verified PnL** — 전략별 + 합계. 거래소 income API 기반이라 실체임.
2. **Variance decomposition** — 6항목 합 = engine_calc - exchange_income 차이
   - `commission_mismatch > $0.50` → 수수료 tier 설정 잘못 (maker/taker 오해)
   - `funding_mismatch > $0.50` → funding 창 (UTC 00/08/16) 놓쳤거나 중복 계산
   - `slippage_mismatch > $1.00` → BookWalk 모델과 실체결 괴리. 원인: 오더북 깊이 악화, toxicity 미감지
   - `fx_mismatch > $0.20` → KRW/USD 환율이 거래 시점 vs 보고 시점 간 drift
   - `rollback_mismatch > $0.50` → 스트랜디드 발생 → 즉시 포지션 수동 청산 필요
   - `unattributed > $0.10` → 원인불명 (분해식에 누락된 항목 있음)
3. **Rejections top** — 어느 gate가 가장 많이 거절했는지
   - `SYMBOL_COOLDOWN` 수천 건 → 정상 (30초 reenter 방지)
   - `UNIVERSE_MISS` > 100 → auto-symbols 설정이 잘못됨, 활성 거래소 축소 필요
   - `BUDGET_EXHAUSTED` 발생 → 해당 전략 HALT됨, 다음 UTC 00:00까지 대기
   - `NOTIONAL_BUMP_EXCEEDS_RISK` → 거래소 min이 위험 캡보다 큼, 해당 거래소 제외 검토
4. **Divergence events** — 0이어야 함. 1 이상 → 즉시 `reconciliation/pnl_ledger.py` 조사
5. **Stranded count** — 0이어야 함. 1 이상 → 수동 청산 + 원인 조사

### 1.2 거래소 앱 실측 확인
엔진 보고는 항상 거래소 API와 대조. 앱에서 직접 보이는 값이 최종 판단 기준.
- Binance Futures: Trade History → 24H Summary
- Bitget Unified: Positions / Assets / PnL 탭
- 월렛 잔고 = 어제 잔고 + 24H realized + funding + commission (대조되지 않으면 리포트 bug)

---

## 2. ReasonCode 사전 (16종)

| Code | 의미 | 조치 |
|------|-----|------|
| `STRATEGY_FILTERED` | 허용 리스트 미포함 | `strategy_activation.json`의 `active_strategies` 확인 |
| `STRATEGY_COOLDOWN` | 해당 전략 손실 쿨다운 중 (US-164) | 쿨다운 지나면 자동 복구, 간섭 불필요 |
| `KILL_SWITCH_HALT` | 전체 HALT 상태 | 사유 `leviathan_halt_reason_total` 메트릭 + 텔레그램 기록 확인 후 `/resume` |
| `CIRCUIT_BREAKER_OPEN` | 전략 또는 거래소 CB tripped | 300초 cooldown 자동. 연속 발생 시 전략/거래소 disable 검토 |
| `RATE_LIMITED` | 거래소 주문 속도 초과 | 일시적. 반복 시 `TokenBucket` 5.0/s → 낮추거나 거래소 VIP 승급 |
| `FLASH_GUARD_BLOCKED` | 가격 급변 감지 (3% 3분) | 일시적. 시장 회복까지 대기 |
| `SESSION_LOSS_LIMIT` | 세션 손실 한도 초과 (max_session_loss_usd) | **엔진 HALT**. 원인 조사 후 operator ACK 필요 |
| `RISK_GUARDIAN_REJECTED` | 11-check 중 하나 실패 | `leviathan_risk_rejection_total{reason}` 라벨로 세부 |
| `SYMBOL_COOLDOWN` | 같은 심볼 30s reenter 방지 | 정상. 문제 아님 |
| `MARGIN_INSUFFICIENT` | futures 여유증거금 < $3 | 해당 거래소 입금 또는 기존 포지션 청산 |
| `NOTIONAL_BELOW_MIN` | leg 주문 크기 < 거래소 min | auto-bump 먼저 시도, 실패 시 이 코드 |
| `NOTIONAL_BUMP_EXCEEDS_RISK` | auto-bump가 `max_position_pct`(6%) 초과 | 자본 증액 또는 해당 거래소 제외 |
| `DEDUP_COLLISION` | 같은 `collision_key` 10s 내 재시도 | 일시적. 10s 후 자동 해제 |
| `UNIVERSE_MISS` | (strategy, symbol, leg_a, leg_b) 조합 무효 | UniverseMatrix 리빌드 (재시작) |
| `ORDERBOOK_TOXIC` | imbalance > 0.7 또는 depth 변동 3× | 오더북 회복까지 대기 (대개 수초) |
| `MARKET_IMPACT_HIGH` | BookWalk VWAP vs mid > max_market_impact_bps (20) | 주문 크기 축소 또는 depth 개선 대기 |
| `BUDGET_EXHAUSTED` | 전략별 일일 손실 예산 소진 | UTC 00:00 리셋까지 해당 전략 HALT |

---

## 3. 일반적 알람 대응

### 3.1 "PnL Divergence CRITICAL — engine=X exchange=Y"
- 즉시 kill switch 활성화됨
- 원인 후보:
  - ExchangeIncomeFetcher 60s 폴링 지연 (거래소 API 응답 느림)
  - 실제 차이 (engine 계산에 commission 누락 등)
- 체크: `leviathan_pnl_divergence_usd` Prometheus gauge + 최근 3회 연속 breach 카운트
- 조치: 수동 `/manual_close_positions.py --dry-run` → 실행, 그 후 engine 재시작

### 3.2 "Stranded position — $X on Binance"
- 한쪽 leg만 체결되고 rollback 실패
- 대응: `scripts/close_open_positions.py --dry-run` 검토 → `--execute` 청산
- CSV 감사 기록이 자동 생성됨: `logs/manual_close_YYYYMMDD_HHMMSS.csv`

### 3.3 "BUDGET_EXHAUSTED strategy=X"
- 해당 전략만 HALT. 다른 전략은 계속 진행.
- UTC 00:00에 자동 리셋
- 손실 원인은 Daily Report의 variance_decomposition에서 확인

### 3.4 "SESSION_LOSS_LIMIT exceeded"
- 세션 총 손실 > `live.max_daily_loss_pct` (5%)
- 엔진 HALT. 신규 거래 불가.
- ACK 절차: 원인 파악 → `reconciliation/daily_report.py` → 수동 재시작

---

## 4. 주요 명령

```bash
# 엔진 상태
ps aux | grep "python.*src\.main" | grep -v grep

# 거래소 포지션 (직접 API 조회)
python /tmp/check_positions.py  # Binance futures

# 엔진 로그
tail -F engine/logs/canary_vNNN.log | grep -E "CRITICAL|HALT|rejected_|order_placed|position_opened"

# Daily report 수동 생성 (UTC 00:05 이전에 미리 보기)
cd engine && python -c "
import asyncio
from src.reconciliation.daily_report import DailyReconciliationReport
# ... 의존성 주입 필요
"

# Stranded 청산
cd engine && python scripts/close_open_positions.py --dry-run
cd engine && python scripts/close_open_positions.py --execute

# 전략 활성화/비활성화
# engine/config/strategy_activation.json 수정 후 엔진 재시작
```

---

## 5. 엔진 재시작 절차

1. **필수 선행**:
   - Binance + Bitget 포지션 0 확인
   - Docker `timescaledb + redis` 실행 확인: `docker compose ps`
   - `.env` 파일 존재 확인
2. **기동**:
   ```bash
   cd engine
   nohup python -m src.main > logs/canary_vNNN.log 2>&1 &
   ```
3. **검증 (최소 2분)**:
   - `live_mode.collectors_started` 로그 출력 확인 (7 거래소)
   - `Reconciliation successful — clearing HALT flag` 로그 출력 (없으면 WAL 불일치 → 수동 reconcile)
   - 2분 내 `signal_accepted` 또는 `signal_rejected` 이벤트 출력 확인 (데이터 흐름 정상성)

---

## 6. Path-B 리팩토링 Gate (live 재개 조건)

Live 거래는 다음 모두 충족 후에만 재개:

1. **REFACTOR_PLAN.md Day 1-10 완료** — 모든 체크마크 ✅
2. **Paper mode 72H 연속** — crash 0, variance unattributed < $0.10/day
3. **Stage-1 canary 48H 연속** — $10 per strategy, crash 0, divergence events 0
4. **Exchange income 대조 ±1%** — engine_pnl vs exchange_income 월별 합 반드시 ±1% 이내

이 4개 모두 통과해야 live 재개. 하나라도 fail이면 Stage 되돌림.

---

## 6.5 Incident Response Procedure (IRP) — P1/P2/P3 (Phase L L-5, 2026-04-22)

> 사장님 부재 시(외출/취침) 자동 대응 흐름. 사람이 즉시 개입할 수 없는 시간대에 엔진이 안전 측면 fallback 보장.

### P1 — 자금 손실 위험 (즉시 차단, < 1초)

| 트리거 | 자동 대응 | 알림 채널 |
|--------|----------|---------|
| `KILL_SWITCH` 발동 (수동/자동) | 신규 주문 차단 + 미체결 취소 + 시장가 청산 (Tier 1→2→3) | Telegram TradeBot 즉시 alert |
| `divergence > 5%` (engine vs exchange realized PnL) | KillSwitch Tier-1 자동 발동 + HALT | Telegram critical |
| `STRANDED total_usd > $30` | `register_halt_forwarder` 통해 supervisor halt | Telegram critical |
| live mode unexpected (mode=paper 무시 시도) | startup boot guard fail + 즉시 종료 | Console fatal log |

**운영자 행동**:
1. Binance/Bitget 앱에서 포지션 0 확인 (앱이 진실)
2. Telegram TradeBot `/positions` 명령으로 엔진 상태 확인
3. 청산 안 됐으면 수동: `python engine/scripts/close_open_positions.py --execute`
4. 24h 내 2+ P1 발생 시 `mode=paper` 강제 + 사장님 호출 (외출 중이라도)

### P2 — 운영 중단 위험 (자동 복구 시도, < 5분)

| 트리거 | 자동 대응 | 알림 |
|--------|----------|------|
| Engine crash (process exit) | systemd/docker restart_policy=unless-stopped | InfraBot 알림 |
| DB connection drop | retry with exponential backoff 3회 → JSON fallback | InfraBot warning |
| WS multi-exchange disconnect (3+) | per-exchange reconnect 60s → 모두 실패 시 paper-only mode | InfraBot warning |
| Memory leak (>2GB residual) | log_warning + restart in 1H if no resolution | DevBot info |

**운영자 행동**:
1. 자동 복구 실패 시: `docker compose ps` + `docker compose logs --tail 200 engine`
2. WAL replay 필요 여부 확인: 시작 시 `Reconciliation successful` 로그 없으면 수동
3. P2가 30분 내 미해결 시 P1으로 escalation

### P3 — 데이터 품질 / 단일 전략 (관찰 + 격리, 24h 내 결정)

| 트리거 | 자동 대응 | 알림 |
|--------|----------|------|
| Bithumb stale data 5+/min | `_stale_detector.add_blacklist(symbol)` 자동 격리 | DevBot debug |
| 단일 전략 budget exhausted | 해당 전략만 HALT, 다른 전략 계속 | DevBot info |
| Single circuit breaker OPEN | 300s cooldown 후 HALF_OPEN 복귀 시도 | DevBot debug |
| Slippage prediction error p95 > 30bps | gamma calibration 다음 cycle 트리거 | DevBot debug |

**운영자 행동**:
1. P3는 즉시 개입 불필요 — Daily Reconciliation Report 검토 시 정리
2. 동일 패턴 3일 연속 시 root cause 조사 + 전략 비활성화 검토

### 우선순위 매트릭스

```
                    빈도(매일)    빈도(주간)    빈도(드뭄)
즉시 자금 손실    P1            P1           P1
운영 중단        P2 (자동복구)   P2           P1 (불복구 시)
관찰 가능        P3            P3           P3 (격리)
```

### Escalation chain

P3 (24h 미해결) → P2 / P2 (30분 미해결) → P1 / P1 (즉시) → mode=paper 강제 + Telegram TradeBot critical + KillSwitch.

---

## 7. 긴급 연락 / 에스컬레이션

| 상황 | 조치 |
|------|-----|
| 엔진 crash + 포지션 오픈 | 즉시 수동 청산 (Binance/Bitget 앱 또는 `close_open_positions.py --execute`) |
| 24H 내 2+ divergence CRITICAL | Path-B 중단 → 전면 재검토 |
| Daily report 미수신 2일 연속 | 스케줄러 health-check: `ps aux | grep daily_report_scheduler` |
| 거래소 API 키 만료 / 차단 | `.env` 갱신 → 엔진 재시작 (paper mode에서 먼저 테스트) |

---

## References

- `engine/docs/REFACTOR_PLAN.md` — Path-B Day-by-Day 상세
- `SSOT.md` — 프로젝트 상태 진실
- `engine/src/core/reason_codes.py` — ReasonCode 정의
- `engine/config/engine.json` — 런타임 설정 (비밀번호 제외)
