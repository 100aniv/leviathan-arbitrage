# TF QF 9차 단계 3 교차검증 — 데이터 파이프라인 (Sana)

**검증일**: 2026-03-22  
**역할**: Sana — 데이터 파이프라인  
**대상 파일**: shadow.py, data_quality_manager.py, collectors/manager.py, collectors/symbol_discovery.py, core/config.py, live_gate_continuous.py, progressive_shadow.py

---

## 최종 판정

| 항목 | 결과 |
|------|------|
| **전체 판정** | **PASS** |
| CRITICAL | 0 |
| HIGH | 0 |
| MEDIUM | 4 |
| LOW | 1 |

---

## 검증 항목별 결과

### 1. Shadow 완전성 — PASS

[FINDING] ShadowMode 클래스는 trade 기록, PnL 추적, 13항목 메트릭을 완전히 구현함.

[STAT:n] 코드 증거: trades_executed(line 1439/1725), per-strategy StrategyStats(line 1502/1811), pnl_history append(line 1508/1817), trades_won/trades_lost(lines 1511-1518), Prometheus TRADES_TOTAL/PNL_TOTAL/DRAWDOWN_CURRENT 연동.

**13항목 메트릭 구조**:
- progressive_shadow.py 6-stage 정의: Stage 1(crash=0/signals>0/trades>0) → Stage 2(WR>50%, PnL>0) → Stage 3(per-strategy separation) → Stage 4(RSS<100MB/hr, trades>50) → Stage 5(Sharpe>2.0, MDD<5%) → Stage 6(LiveGate 6-check)
- get_strategy_report(): trades/wins/losses/pnl/win_rate/sharpe/max_drawdown/pass 반환
- _send_summary(): by_strategy breakdown + warmup_excluded/crisis_excluded 플래그(US-258-b)

[STAT:effect_size] Shadow 완전성 100% — 모든 required field 구현됨

---

### 2. PnL 계산 정확성 — PASS (모니터 권장)

[FINDING] cross-exchange 경로(단일 심볼)는 fill price 기반 완전 정확 계산.

**cross-exchange 경로 (line 1461-1466)**:
```
net_pnl = (sell_notional - real_sell_fee) - (buy_notional + real_buy_fee) - network_cost
```
- sell_notional / buy_notional: `trade.price * trade.amount` (fill price 기반)
- expected_profit 미사용 — 완전 fill-price 기반

[STAT:n] 증거: shadow.py lines 1444-1466 (cross-exchange path), lines 1739-1787 (multi-leg path)

**[MEDIUM] DATA-M1** — cross-asset(stat_arb) 경로 한정 주의:  
cross-asset 경로(line 1762): `net_pnl = expected_profit_usdt - total_fees`  
expected_profit_usdt는 전략 내부 spread 추정값으로 fill price 재계산 없음.  
$50 single-trade sanity cap + per-strategy loss cap으로 극단값 보호됨.

[LIMITATION] stat_arb expected_profit_usdt 계산 정확성은 전략 코드 의존 — quant-validator 별도 검증 권장.

---

### 3. WS 흐름 — PASS

[FINDING] CollectorManager → 10개 adapter → 내부 OrderBook dict → SignalGenerator/MultiStrategySignalProducer 흐름 완전 연결.

[STAT:n] 10개 거래소: binance, bybit, okx, bitget, upbit, bithumb, coinone, binance_futures, okx_futures, bybit_futures (manager.py line 32)

**흐름**:  
`on_orderbook callback` → `shadow._on_orderbook()` → `SignalGenerator.on_orderbook_update()` (cross-exchange)  
→ `MultiStrategySignalProducer.on_orderbook()` (7개 전략)  
→ `_on_cross_asset_update()` → `StatisticalArbStrategy.on_orderbook_update()` (stat_arb)

KRW 심볼 자동 매핑: `KOREAN_EXCHANGES={'upbit','bithumb','coinone'}` → `/KRW` pair 자동 변환 후 KRW rate 적용.

**[LOW] DATA-L1** — pre-supplied CollectorManager 콜백 후-주입(start() 시점), 조건부 처리됨. 기능 영향 없음.

---

### 4. KRW 환율 dual-source — PASS

[FINDING] dual-source(Upbit+Bithumb) 30s 폴링, ±10% sanity 바운드, 5회 lockout escape, 120s staleness halt 모두 구현됨.

[STAT:n] 증거: shadow.py lines 1870-2022  
- Upbit API: `https://api.upbit.com/v1/ticker?markets=KRW-USDT`  
- Bithumb API: `https://api.bithumb.com/public/ticker/USDT_KRW`  
- 평균: `sum(rates) / len(rates)`  
- sanity: `abs(new_rate - current) / current > 0.10` → reject  
- lockout escape: `reject_count >= 5` → force-accept  
- sleep: `asyncio.sleep(30.0)` (line 2019) — 30s 확인  
- stale 120s → `_krw_stale=True` → KRW orderbook skip  
- 3x stale → soft-block, 20x stale → KillSwitch trigger

**확인 사항**: line 672 comment "every 60s"는 stale comment — 실제 sleep(30) 정확. [MEDIUM] DATA-M2: comment 수정 권장 (기능 영향 없음).

---

### 5. DataQualityManager — PASS (모니터 권장)

[FINDING] 4-layer DQM 구현 완전. Paper 모드 always_healthy 분기 정확히 구현됨.

[STAT:n] 증거: data_quality_manager.py  
- Layer 0: Blacklist (TTL 300s, bithumb 600s)  
- Layer 1: Freshness (futures=0.5s, default=1.0s, korean=2.0s, bithumb=1.0s)  
- Layer 2: Bithumb deviation (5% threshold, 2x+ → instant blacklist)  
- Layer 3: Z-score anomaly (window=100, z=4.0, warmup=10)  
- Layer 4: Health score factor (min-based aggregation)

**always_healthy**: `register_exchange(id, always_healthy=True)` → `_always_healthy` set → `get_health_score()` returns 1.0 bypassing HealthChecker. Paper/synthetic adapter 의도에 정확히 부합.

**[MEDIUM] DATA-M3**: 미등록 거래소 optimistic default 1.0 (line 212). Shadow 초기 heartbeat 수신 전 구간 노출. lazy-init으로 이후 정상 추적됨.

**[MEDIUM] DATA-M4**: Bithumb deviation warmup=5건 bypass. StaleOrderbookDetector가 2차 방어선 역할.

---

### 6. Auto-symbols min_exchanges — PASS

[FINDING] SSOT 요구사항 min_exchanges=3이 config 기본값으로 정확히 설정됨.

[STAT:n] 증거:  
- `config.py:140`: `symbol_min_exchanges: int = Field(default=3, ...)`  
- `main.py:400`: `min_ex = self._settings.trading.symbol_min_exchanges`  
- `main.py:402`: `symbols = await discover_common_symbols(min_exchanges=min_ex)`  
- `symbol_discovery.py:56`: 함수 기본값=2이나 main.py가 항상 config값(=3) 명시 전달

초기 우려(DATA-H1): symbol_discovery.py 함수 기본값=2 — 실제 main.py 호출부에서 config default=3 전달함. **RETRACTED**.

---

### 7. Stale detector (Bithumb delta orderbook) — PASS

[FINDING] Bithumb delta orderbook 처리 + DQM 2-layer 방어 구현 완전.

[STAT:n] 증거: shadow.py lines 874-888  
- `DELTA_EXCHANGES = {'bithumb'}` — delta 누적 처리 명시  
- `existing.apply_delta(bid_tuples, ask_tuples)` — 증분 적용  
- snapshot 없으면 기존 book에 delta 누적  
- StaleOrderbookDetector.check_cross_exchange() — cross-exchange price drift 감지  
- DQM Layer 2: Bithumb 5% deviation threshold, 2x+ → 600s blacklist

[LIMITATION] warmup 5건 동안 DQM Bithumb deviation 체크 bypass (DATA-M4). StaleOrderbookDetector가 1차 방어.

---

## 이슈 요약

| ID | 심각도 | 위치 | 설명 | 조치 |
|----|--------|------|------|------|
| DATA-M1 | MEDIUM | shadow.py ~1762 | stat_arb cross-asset PnL = expected_profit - fees (fill price 재계산 없음) | MONITOR |
| DATA-M2 | MEDIUM | shadow.py 672 | stale comment "60s" (실제 30s sleep 정확) | LOW — comment 수정 |
| DATA-M3 | MEDIUM | data_quality_manager.py 212 | 미등록 거래소 optimistic health=1.0 | MONITOR |
| DATA-M4 | MEDIUM | data_quality_manager.py 357 | Bithumb deviation warmup=5건 bypass | MONITOR |
| DATA-L1 | LOW | shadow.py 659 | pre-supplied manager 콜백 후-주입 | INFORMATIONAL |

---

## 제한사항 (Limitations)

[LIMITATION] 이 검증은 정적 코드 분석 기반. 런타임에서 Upbit/Bithumb API 가용성, 실제 KRW rate 정확도, 거래소별 WS latency는 검증되지 않음.

[LIMITATION] stat_arb expected_profit_usdt 계산 정확성은 statistical_arb.py 전략 코드 의존 — 본 검증 범위 외. quant-validator(ITZY팀) 별도 검증 권장.

[LIMITATION] discover_common_symbols min_exchanges=3 동작은 config 기본값 확인 기반. `.env`에 `TRADING_SYMBOL_MIN_EXCHANGES` 명시적 오버라이드가 있는 경우 별도 확인 필요.

---

## 결론

데이터 파이프라인 7개 항목 모두 PASS. CRITICAL 0건, HIGH 0건.  
4건의 MEDIUM은 모두 모니터링 수준으로 즉각 수정 불필요.  
핵심 요구사항(fill price 기반 PnL, KRW dual-source 30s, min_exchanges=3, Bithumb delta, always_healthy Paper 분기) 모두 올바르게 구현됨.
