# LEVIATHAN 전략 가이드

## 개요

LEVIATHAN은 8가지 아비트라지 전략을 지원합니다. 각 전략은 독립적으로 활성화/비활성화할 수 있으며, 자동 튜닝을 통해 파라미터를 최적화할 수 있습니다.

## 1. Cross-Exchange Arbitrage (거래소 간 아비트라지)

**전략 ID**: `cross_exchange`

**로직**: 동일 자산이 서로 다른 거래소에서 다른 가격에 거래될 때, 저가 거래소에서 매수하고 고가 거래소에서 매도합니다.

**예시**:
- Binance BTC/USDT: $50,000 (매수)
- Upbit BTC/USDT: $50,150 (매도)
- 스프레드: 30 bps ($150)
- 수수료 차감 후 순이익: ~$50

**파라미터**:
| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `min_spread_bps` | 10 | 최소 진입 스프레드 (basis points) |
| `entry_threshold` | 0.001 | 진입 임계값 |
| `exit_threshold` | 0.0005 | 청산 임계값 |
| `max_position_usdt` | 35 | 최대 포지션 크기 (USDT) |
| `stop_loss_pct` | 0.02 | 손절 비율 |

**적합 시장**: 변동성이 높고 거래소 간 유동성 차이가 큰 시장

---

## 2. Triangular Arbitrage (삼각 아비트라지)

**전략 ID**: `triangular`

**로직**: 3개 통화 쌍을 순환 거래하여 가격 불일치를 포착합니다.

**예시**:
```
BTC/USDT → ETH/BTC → ETH/USDT
1. USDT로 BTC 매수
2. BTC로 ETH 매수
3. ETH를 USDT로 매도
→ 순환 후 USDT 증가 시 이익
```

**파라미터**:
| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `min_profit_bps` | 5 | 최소 순환 이익 (bps) |
| `max_notional_usdt` | 50 | 최대 거래 금액 |

**적합 시장**: 다수의 통화 쌍이 활발히 거래되는 단일 거래소

---

## 3. Spot-Futures Arbitrage (현물-선물 아비트라지)

**전략 ID**: `spot_futures`

**로직**: 현물과 선물 간 베이시스(가격 차이)를 포착합니다. 선물 프리미엄이 높을 때 현물 매수 + 선물 매도, 디스카운트 시 반대 포지션.

**예시**:
- 현물 BTC: $50,000
- 선물 BTC (1개월): $50,500
- 베이시스: +1% (연환산 ~12%)
- 현물 매수 + 선물 매도 → 만기 시 1% 수익

**파라미터**:
| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `min_basis_bps` | 20 | 최소 베이시스 |
| `entry_threshold` | 0.002 | 진입 임계값 |
| `max_position_usdt` | 35 | 최대 포지션 |

**적합 시장**: 선물 프리미엄이 자주 발생하는 강세장

---

## 4. Funding Rate Arbitrage (펀딩비 아비트라지)

**전략 ID**: `funding_rate`

**로직**: 무기한 선물의 펀딩비를 수취합니다. 높은 양의 펀딩비 → 현물 매수 + 선물 매도 (펀딩비 수취). 음의 펀딩비 → 반대 포지션.

**예시**:
- 펀딩비: +0.05% (8시간마다)
- 현물 매수 + 선물 매도
- 8시간마다 0.05% 수취 → 연간 ~54.75% 수익률

**파라미터**:
| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `min_funding_rate_bps` | 5 | 최소 펀딩비 |
| `max_position_usdt` | 35 | 최대 포지션 |

**적합 시장**: 펀딩비가 극단적으로 높거나 낮은 시장

---

## 5. Statistical Arbitrage (통계적 아비트라지)

**전략 ID**: `statistical_arb`

**로직**: 두 자산의 가격 비율이 과거 평균에서 크게 벗어날 때 평균 회귀를 기대하고 포지션을 취합니다. Z-score 기반 진입/청산.

**예시**:
- BTC/ETH 비율 장기 평균: 15.0
- 현재 비율: 16.5 (Z-score = 2.5)
- ETH 매수 + BTC 매도 → 비율 회귀 시 이익

**파라미터**:
| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `z_score_entry` | 2.0 | 진입 Z-score 임계값 |
| `z_score_exit` | 0.5 | 청산 Z-score |
| `lookback_period` | 100 | 이동 평균 기간 |
| `max_position_usdt` | 35 | 최대 포지션 |

**적합 시장**: 상관관계가 높은 자산 쌍이 일시적으로 괴리되는 시장

---

## 6. Latency Arbitrage (지연 아비트라지)

**전략 ID**: `latency_arb`

**로직**: 거래소 간 가격 업데이트 지연을 이용합니다. 빠른 거래소의 가격 변동을 감지하고, 느린 거래소에서 아직 반영되지 않은 가격으로 거래합니다.

**파라미터**:
| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `min_edge_bps` | 5 | 최소 엣지 |
| `max_latency_ms` | 50 | 최대 허용 지연 |
| `max_position_usdt` | 35 | 최대 포지션 |

**적합 시장**: 거래소 인프라 속도 차이가 있는 시장

---

## 7. Futures-Futures Arbitrage (선물-선물 아비트라지)

**전략 ID**: `futures_futures`

**로직**: 동일 자산의 서로 다른 만기 선물 간 스프레드를 거래합니다. 근월물-원월물 스프레드가 비정상적일 때 포지션을 취합니다.

**파라미터**:
| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `min_spread_bps` | 15 | 최소 만기 간 스프레드 |
| `max_position_usdt` | 35 | 최대 포지션 |

**적합 시장**: 다양한 만기의 선물이 거래되는 시장

---

## 8. CEX-DEX Arbitrage (중앙화-탈중앙화 거래소 아비트라지)

**전략 ID**: `cex_dex`

**로직**: 중앙화 거래소(CEX)와 탈중앙화 거래소(DEX, 예: Uniswap V3) 간 가격 차이를 이용합니다.

**파라미터**:
| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `min_spread_bps` | 20 | 최소 CEX-DEX 스프레드 |
| `gas_budget_usdt` | 5 | 가스비 예산 |
| `max_position_usdt` | 35 | 최대 포지션 |

**적합 시장**: CEX와 DEX 가격 괴리가 발생하는 시장 (가스비 고려 필요)

---

## 전략 관리

### API로 전략 제어

```bash
# 전략 목록 조회
curl http://localhost:8000/api/v1/strategies

# 전략 토글 (활성화/비활성화)
curl -X POST http://localhost:8000/api/v1/strategies/cross_exchange_v1/toggle

# 전략 설정 변경
curl -X POST http://localhost:8000/api/v1/strategies/cross_exchange_v1/config \
  -H "Content-Type: application/json" \
  -d '{"min_spread_bps": 15, "entry_threshold": 0.002}'
```

### 자동 튜닝

```bash
# 특정 전략 튜닝
python -m src.cli.tune_cli --strategy cross_exchange --trials 100 --shadow

# 결과 저장
python -m src.cli.tune_cli --strategy cross_exchange --output results.json
```

## Beta Gate 기준

> **참조**: SSOT.md §2 Shadow 통과 기준 (복합지표 — LiveGate 6-check 기반)
> 단순 PnL/WR이 아닌 시드 무관 절대 지표 기반. 사장님 지시 (2026-03-18).

전략이 프로덕션에 적용되려면 다음 **13항목 복합지표**를 충족해야 합니다:

| # | 기준 | 임계값 | 유형 |
|---|------|--------|------|
| 1 | crash | = 0 | 시스템 |
| 2 | 무중단 실행 | >= 10분 | 시스템 |
| 3 | Net PnL | >= $0 | 기본 (참고용) |
| 4 | Max Drawdown | < 5% (자본 대비) | **절대 지표** |
| 5 | Profit Factor | > 1.0 (총이익/총손실) | **절대 지표** |
| 6 | 신호 수 | >= 100/day (외삽) | 활성도 |
| 7 | Kill Switch | Not halted | 방어 레이어 |
| 8 | Circuit Breaker | CLOSED | 방어 레이어 |
| 9 | 거래소 Health | >= 95% | 인프라 |
| 10 | loss_capped | = 0 | 리스크 |
| 11 | 전략별 trade | 모든 활성 전략 trade >= 1 | 통합 검증 |
| 12 | 방어 레이어 활성 | CB/StaleDetector/OutlierFilter 로그 >= 1건 | 통합 검증 |
| 13 | 결과 파일 | `.omc/state/shadow-result-latest.json` 존재 | 검증 증거 |

**TF SF 추가**: 위 + Sharpe >= 2.0, Calmar > 0, 전략별 WR > 50%
**TF Final 추가**: 위 + Sharpe >= 2.5, Profit Factor > 1.2, 리콘실리에이션 오차 < 1%

## 수수료 구조

| 거래소 | Maker | Taker |
|--------|-------|-------|
| Binance | 0.1% | 0.1% |
| Upbit | 0.05% | 0.05% |
| Bybit | 0.1% | 0.1% |
| OKX | 0.08% | 0.1% |

> 수수료를 차감한 순 스프레드가 양수일 때만 거래가 실행됩니다.
