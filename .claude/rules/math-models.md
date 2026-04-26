# 수학 모델 (SSOT §4 미러)

> 이 파일은 `SSOT.md §4`의 자동 주입본입니다.
> SSOT §4 변경 시 이 파일도 반드시 동기화하세요 (`ssot-protection.md` 규칙 참조).
> `.claude/rules/` 디렉토리에 위치하므로 모든 세션에서 자동 로드됩니다.

---

## 4.1 슬리피지 모델 (3종)

**Base SlippageModel** (`execution/paper.py`)
```
slippage_pct = base_slippage_pct * (1 + random(0, 0.5) * volatility_factor)
fill_price = base_price * (1 +/- slippage_pct)
기본값: base_slippage_pct=0.001 (0.1%), volatility_factor=1.0
용도: 유닛 테스트, 기본 Paper 모드
```

**PowerLawSlippage** (`modes/shadow.py` — 코드 path는 paper.py forward shim) — **DEPRECATED 2026-04-26**
```
impact = k * size^gamma
slippage = base_slippage_pct * impact * random(0.5, 1.5)
fill_price = base_price * (1 +/- slippage)
기본값: k=0.0, gamma=0.5, base=0.001
근거: SignalGenerator가 CEXOrderbookSlippage로 사전 필터.
      PaperExecutor에서 추가 슬리피지 적용 시 이중 계산.
      k=0으로 PaperExecutor 슬리피지 제거 (Phase C 확정).
이전 용도: shadow 모드 (DEPRECATED)
현재: 모드 3개 backtest/paper/live, shadow는 폐기. paper에서 PowerLawSlippage 비활성 유지.
```

**CEXOrderbookSlippage** (`friction/slippage_model.py`)
```
impact_fraction = sigma * k * sqrt(size / ADV)
expected_abs = impact_fraction * mid_price
CI: size/ADV <= 1.0 -> +/-20%, 1-3 -> +/-50%, 3-10 -> +/-100%, >10 -> DO NOT TRADE
Impact_decay(t) = Impact_0 * (1 + t/t_0)^(-gamma)  [t_0=60s, gamma=0.5]
용도: SignalGenerator 필터 (Phase 4+)
```

## 4.2 마찰력 모델 (`friction/cost_calculator.py`)

```
Net_Profit = Gross_Spread
           - Fee_Buy - Fee_Sell
           - Slippage_Buy - Slippage_Sell
           - Network_Cost - Funding_Cost - Opportunity_Cost
           - E[Rollback_Cost]

E[Rollback_Cost] = P(rollback) * Avg_Rollback_Cost
P(rollback): 30-trade 롤링 윈도우, cold-start 5%
```

거래소별 수수료 (Tier 0, Taker):

| 거래소 | Maker | Taker | 비고 |
|--------|-------|-------|------|
| Binance | 0.10% | 0.10% | |
| Bybit | 0.10% | 0.10% | Spot VIP0 |
| OKX | 0.08% | 0.10% | |
| Bitget | 0.10% | 0.10% | Spot VIP0 |
| Bitget Futures | 0.02% | 0.06% | USDT-M VIP0 (BUG-20 수정) |
| Upbit | 0.05% | 0.139% | KRW 마켓 |
| Bithumb | 0.25% | 0.25% | KRW 마켓 |
| Coinone | 0.02% | 0.02% | API 할인 적용 (기본 0.20%) |

ETH 출금 비용 (네트워크별):

| 거래소 | ETH 비용 | 네트워크 | 비고 |
|--------|---------|---------|------|
| Binance | $0.06 | Arbitrum One | L2 최저경로 |
| Bybit | $0.19 | Arbitrum | L2 |
| OKX | $0.10 | Arbitrum | L2 |
| Bitget | $0.10 | Arbitrum | L2 |
| Upbit | $4.50 | Ethereum L1 | L2 미지원 |
| Bithumb | $2.50 | Ethereum L1 | L2 미지원 |
| Coinone | $2.50 | Ethereum L1 | L2 미지원 |

> 글로벌 거래소 $0.06~$0.19 (Arbitrum L2) vs KRW 거래소 $2.50~$4.50 (L1 only).
> 상세: `engine/src/friction/fee_model.py` WITHDRAWAL_FEES_USD 참조.

## 4.3 리스크 모델

**KillSwitch (3-tier)**:
- Tier 1 (< 1ms): halt 플래그 설정 (threading.Event + Redis SET) → 즉시 신규 주문 차단
- Tier 2 (< 500ms): 전 거래소 미체결 주문 취소 (asyncio.gather, 2s timeout)
- Tier 3 (< 2000ms): 전 거래소 오픈 포지션 시장가 청산 (asyncio.gather, 3s timeout)

**CircuitBreaker**: CLOSED → OPEN → HALF_OPEN (고정 300s cooldown)

**RiskGuardian (11-check)**: #0 halt, #1 포지션한도, #2 드로다운, #3 익스포저, #4 서킷브레이커, #4e 넷익스포저(Amendment 7), #5 거래소건강도, #6 단일거래크기, #7 변동성, #8 롤백비용, #9 전략상관(log-only), #10 최대동시포지션(US-154)

## 4.4 슬리피지 계층 규칙

> **사전 필터**: SignalGenerator의 CEXOrderbookSlippage — 통계적 시장 영향 추정 (sigma * k * sqrt(size/ADV)). 신호 허용/차단 기준으로만 사용. fill_price에 미반영.
> **실행 시뮬레이션**: BookWalkSlippage (US-060) — 실제 오더북 깊이 워킹 VWAP 체결가 산출. fill_price를 결정하는 실행 계층.
> **이중 계산 아님**: 두 계층은 서로 다른 질문에 답함 (필터 vs 체결가). PnL 계산에서 더해지지 않음.
> **금지**: PowerLawSlippage(k>0)를 PaperExecutor에 적용하는 것은 여전히 금지 (통계 모델 + 통계 모델 = 이중계산).
> BookWalkSlippage는 실제 오더북 레벨을 워킹하므로 통계 모델이 아닌 실행 시뮬레이션.

## 4.5 Sharpe 비율 (연간화)

```
Sharpe = (mu - rf) / sigma * sqrt(periods_per_year)
mu = mean(hourly_returns), sigma = std(hourly_returns)
periods_per_year = 8760 (1시간 윈도우)
```

## 4.6 Maximum Drawdown

```
MDD = max_t { (Peak_t - Cumulative_PnL_t) / Peak_t }
```

## 4.10 predicted_slippage_bps (Path-B v2, Day 9+)

After Day 9 fix (`_pred_bps=0` bug resolved):

```
predicted_slippage_bps = BookWalkSlippage.predict(book, size).expected_bps
```

- Stored on `Signal.predicted_slippage_bps: Decimal | None` (None before Day 9 activation)
- Forwarded to `TradeLeg.metadata["predicted_slippage_bps"]`
- Recorded via `SlippageFeedbackCollector.record(predicted_bps=predicted_slippage_bps, actual_bps=...)`
- Used for post-trade calibration: `actual_bps / predicted_bps` ratio tracked per exchange/symbol
- Gate criterion: mean(|actual - predicted|) < 5 bps over 100-trade rolling window before Day 13 gamma calibration
