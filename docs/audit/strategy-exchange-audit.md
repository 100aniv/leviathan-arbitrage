# LEVIATHAN — 전략/거래소 완성도 전수 감사 보고서

> **감사 일자**: 2026-03-11
> **대상 US**: US-076 (Phase I)
> **감사자**: Executor Agent
> **기준 커밋**: `e43658c` (Phase I US-073+074+075 완료)
> **기준 문서**: SSOT.md, `.omc/prd.json`, 실제 구현 코드

---

## 요약 (Executive Summary)

| 항목 | 총계 | PASS | GAP |
|------|------|------|-----|
| 전략 구현 파일 | 8/8 | ✅ 8 | 0 |
| 전략 테스트 커버리지 | 8/8 | ✅ 8 | 0 |
| 전략 신호 라우팅 | 8/8 | ✅ 7 / ⚠️ 1 (cex_dex 조건부) | 1 |
| 전략 Shadow 동작 | 8/8 | ✅ 6 / ⚠️ 1 (cex_dex 미활성) | 1 |
| 거래소 수집기 파일 | 10/10 | ✅ 10 | 0 |
| DEFAULT_EXCHANGES 등록 | 10/10 | ✅ 10 | 0 |
| fee_model 수수료 등록 | 10/10 | ✅ 10 | 0 |
| 거래소 테스트 파일 | 10/10 | ✅ 9 / ⚠️ 1 (coinone 비표준) | 1 |
| **Plan↔SSOT↔PRD↔구현 불일치** | **8건** | — | **8** |

**결론**: 구현 자체는 완성도 높음. 주요 문제는 SSOT.md 내용이 최신 커밋(US-073~075 완료)을 반영하지 않아 문서-코드 불일치 다수 발생.

---

## 1. 전략 8개 전수 감사

### 1.1 전략 매트릭스

| # | 전략 ID | 구현 파일 | 전략 단위테스트 | 추가 테스트 파일 | 신호 라우팅 | main.py 등록 | Shadow 동작 |
|---|---------|---------|-------------|--------------|-----------|------------|------------|
| 1 | `cross_exchange` | ✅ `strategies/cross_exchange.py` | ✅ `tests/unit/strategies/test_cross_exchange.py` (8) | — | ✅ `RealDataSignalProducer` | ✅ | ✅ **ACTIVE** |
| 2 | `spot_futures` | ✅ `strategies/spot_futures.py` | ✅ `tests/unit/strategies/test_spot_futures.py` (7) | — | ✅ `_evaluate_spot_futures` | ✅ | ⚠️ **CONDITIONAL** (비용>basis 정상 필터) |
| 3 | `futures_futures` | ✅ `strategies/futures_futures.py` | ✅ `tests/unit/strategies/test_futures_futures.py` (8) | — | ✅ `_evaluate_futures_futures` | ✅ | ✅ **ACTIVE** (US-075 완료: 3개 선물거래소) |
| 4 | `triangular` | ✅ `strategies/triangular.py` | ✅ `tests/unit/test_triangular.py` (21) | `test_triangular_scanner.py` | ✅ `_evaluate_triangular` (TriangularScanner) | ✅ | ⚠️ **CONDITIONAL** (실시장 cycle 희소) |
| 5 | `funding_rate` | ✅ `strategies/funding_rate.py` | ✅ `tests/unit/strategies/test_funding_rate.py` (8) | — | ✅ `on_funding_rates_updated` | ✅ | ✅ **VERIFIED** |
| 6 | `statistical_arb` | ✅ `strategies/statistical_arb.py` | ✅ `tests/unit/strategies/test_statistical_arb.py` (11) | `test_statistical_arb_improvements.py` | ✅ `RealDataSignalProducer` | ✅ | ✅ **VERIFIED** |
| 7 | `latency_arb` | ✅ `strategies/latency_arb.py` | ✅ `tests/unit/strategies/test_latency_arb.py` (10) | — | ✅ `RealDataSignalProducer` | ✅ | ✅ **ACTIVE** |
| 8 | `cex_dex` | ✅ `strategies/cex_dex.py` (DEXAdapter Protocol stub) | ✅ `tests/unit/test_cex_dex.py` (25) | — | ⚠️ `DEX_RPC_URL` 설정 시에만 등록 | ⚠️ 조건부 | ❌ **미활성** (GAP 8, Phase L 예정) |

### 1.2 전략 신호 라우팅 상세

- `cross_exchange`, `latency_arb`, `statistical_arb`: `RealDataSignalProducer`를 통해 신호 생산. shadow.py가 직접 평가 로직 대신 위임.
- `spot_futures`, `futures_futures`: `_evaluate_spot_futures()` / `_evaluate_futures_futures()` — shadow.py:848 futures_books 분리 저장 후 `RealDataSignalProducer`로 라우팅.
- `triangular`: `TriangularScanner` (Bellman-Ford) → `_evaluate_triangular` 경유.
- `funding_rate`: `FundingRateCollector` 폴링 → `on_funding_rates_updated()` → `_evaluate_funding_rate_arb()`.
- `cex_dex`: `CexDexStrategy`는 `_build_dex_adapter()` 반환값이 `None`이 아닐 때만 등록 (`DEX_RPC_URL` 필요).

### 1.3 전략 Shadow 비활성 상태 명세

| 전략 | `SHADOW_DISABLED_STRATEGIES` 기본값 | 비고 |
|------|-----------------------------------|------|
| `spot_futures` | 미포함 (활성) | 신호 생성되나 cost>basis로 필터 → 실체결 0 |
| `futures_futures` | 미포함 (활성) | US-075 이후 3개 선물거래소 → 신호 생성 가능 |
| `triangular` | 미포함 (활성) | 신호 생성되나 실시장 cycle 희소 |
| `cex_dex` | 해당없음 (미등록) | `DEX_RPC_URL` 미설정 시 완전 제외 |
| `statistical_arb` | 미포함 (활성) | z-score 기반 신호, 2+ trades 검증됨 |

---

## 2. 거래소 10개 전수 감사

### 2.1 거래소 매트릭스

| # | 거래소 | 수집기 파일 | DEFAULT_EXCHANGES | fee_model | 테스트 파일 | 테스트 수 | 비고 |
|---|--------|-----------|:---------------:|:---------:|-----------|:-------:|------|
| 1 | `binance` | ✅ `collectors/binance_collector.py` | ✅ | ✅ | ✅ `test_native_binance.py` | 43 | Spot WS, 멀티스트림 |
| 2 | `bybit` | ✅ `collectors/bybit_collector.py` | ✅ | ✅ | ✅ `test_native_bybit.py` | 24 | Spot WS |
| 3 | `okx` | ✅ `collectors/okx_collector.py` | ✅ | ✅ | ✅ `test_native_okx.py` | 28 | Spot WS |
| 4 | `bitget` | ✅ `collectors/bitget_collector.py` | ✅ | ✅ | ✅ `test_native_bitget.py` | 32 | Spot WS |
| 5 | `upbit` | ✅ `collectors/upbit_collector.py` | ✅ | ✅ | ✅ `test_native_upbit.py` | 37 | KRW 자동매핑, 배치구독 |
| 6 | `bithumb` | ✅ `collectors/bithumb_collector.py` | ✅ | ✅ | ✅ `test_native_bithumb.py` | 36 | ⚠️ Stale data 이슈 (US-073 완료) |
| 7 | `coinone` | ✅ `collectors/coinone_collector.py` | ✅ | ✅ | ⚠️ `test_coinone_stability.py` (24) | 24 | **test_native_coinone.py 없음** (GAP-EX-1) |
| 8 | `binance_futures` | ✅ `collectors/binance_futures_collector.py` | ✅ | ✅ | ✅ `test_binance_futures_collector.py` | 17 | USDT-M Futures |
| 9 | `okx_futures` | ✅ `collectors/okx_futures_collector.py` | ✅ | ✅ | ✅ `test_okx_futures_collector.py` | 16 | US-075 추가 |
| 10 | `bybit_futures` | ✅ `collectors/bybit_futures_collector.py` | ✅ | ✅ | ✅ `test_bybit_futures_collector.py` | 18 | US-075 추가 |

### 2.2 수수료 모델 상세 (fee_model.py 실제값)

| 거래소 | Maker (구현) | Taker (구현) | SSOT §4.2 표기 | 일치 여부 |
|--------|------------|------------|--------------|---------|
| binance | 0.10% | 0.10% | 0.10% / 0.10% | ✅ |
| bybit | 0.10% | 0.10% | **0.01% / 0.06%** | ❌ **GAP-FEE-1** |
| okx | 0.08% | 0.10% | 0.08% / 0.10% | ✅ |
| bitget | 0.10% | 0.10% | 0.10% / 0.10% | ✅ |
| upbit | 0.05% | 0.139% | **0.25% / 0.25%** | ❌ **GAP-FEE-2** |
| bithumb | 0.25% | 0.25% | 0.25% / 0.25% | ✅ |
| coinone | 0.02% | 0.02% | 0.20% (API 할인 시 0.02%) | ⚠️ 표기 불명확 |
| binance_futures | 0.02% | 0.05% | (미기재) | ⚠️ SSOT 누락 |
| bybit_futures | 0.02% | 0.055% | (미기재) | ⚠️ SSOT 누락 |
| okx_futures | 0.02% | 0.05% | (미기재) | ⚠️ SSOT 누락 |

---

## 3. Plan↔SSOT↔PRD↔구현 불일치 전수 목록

| ID | 심각도 | 위치 | 불일치 내용 | 영향 |
|----|--------|------|-----------|------|
| **GAP-SSOT-1** | 🔴 HIGH | SSOT §2, §7 Phase I | US-073/074/075가 [ ] 미완으로 표시. 실제 커밋 `e43658c`으로 모두 완료됨 | SSOT 신뢰도 손상, Phase 진행 상태 오인 |
| **GAP-SSOT-2** | 🔴 HIGH | SSOT §3.3 전략 매트릭스 | `futures_futures` 상태: "대기(CONDITIONAL) — 선물 거래소 1개(binance_futures), 2+ 필요". 실제 shadow.py:491에 `{"binance_futures", "okx_futures", "bybit_futures"}` 3개 등록됨 | US-075 완료 성과 미반영 |
| **GAP-SSOT-3** | 🟡 MEDIUM | SSOT §1 개요 | "8개 네이티브 WebSocket 어댑터"로 기재. 실제 DEFAULT_EXCHANGES = 10개(7 spot + 3 futures) | 어댑터 수 오기재 |
| **GAP-SSOT-4** | 🟡 MEDIUM | SSOT §5 거래소 어댑터 테이블 | `okx_futures`, `bybit_futures` 어댑터 행 없음. US-075에서 추가됐으나 §5 테이블 미반영 | 설계 문서 불완전 |
| **GAP-FEE-1** | 🟡 MEDIUM | SSOT §4.2 수수료 테이블 | Bybit: SSOT=Maker 0.01%/Taker 0.06%, fee_model.py=0.10%/0.10%. 수치 불일치 | 비용 계산 근거 불명확 |
| **GAP-FEE-2** | 🟡 MEDIUM | SSOT §4.2 수수료 테이블 | Upbit: SSOT=0.25%/0.25%, fee_model.py=Maker 0.05%/Taker 0.139%. 수치 불일치 | 비용 계산 근거 불명확 |
| **GAP-EX-1** | 🟡 MEDIUM | coinone 테스트 | `test_native_coinone.py` 없음. 6개 spot 거래소는 모두 `test_native_*.py` 형식 보유. coinone만 `test_coinone_stability.py`로 대체 (24 tests 존재, 표준 미준수) | 테스트 일관성 결여 |
| **GAP-STR-1** | 🟢 LOW | cex_dex 대시보드 | PRD에서 8번째 전략으로 카운트되나 실질적으로 비활성. dashboard by_strategy에서 항상 0 또는 미출현 | UI 혼란 가능성, Phase L까지 허용 |

---

## 4. 대시보드 연동 현황

| 전략/거래소 데이터 | 대시보드 컴포넌트 | 연동 방식 | 상태 |
|-----------------|-----------------|---------|------|
| 전략별 PnL breakdown | `ShadowPanel.tsx` | REST `/api/v1/shadow/stats` → `by_strategy[]` | ✅ |
| 전략별 수익 기여 | `Attribution` 페이지 | REST polling | ✅ |
| 거래소 연결 상태 | `PortfolioSummary.tsx` | REST `/exchanges` + WS | ✅ |
| 거래소별 잔고 | `PortfolioSummary.tsx` | REST `/api/v1/portfolio-summary` | ✅ |
| 펀딩레이트 추적 | `Funding` 페이지 | REST polling | ✅ |
| OrderBook 실시간 | `OrderbookView` | REST fallback | ✅ |
| 전략별 신호 라우팅 로그 | 없음 | — | ⚠️ 미구현 (Phase J 예정) |

---

## 5. 조치 권고사항

### 즉시 조치 (SSOT 업데이트)

1. **SSOT §7 Phase I** US-073/074/075를 `[x]` 완료로 변경
2. **SSOT §3.3** `futures_futures` 상태를 `ACTIVE`로 변경, 선물 거래소 3개 명시
3. **SSOT §1** 어댑터 수 `8` → `10` (7 spot + 3 futures)으로 수정
4. **SSOT §4.2** Bybit/Upbit 수수료 수치를 fee_model.py 실제값으로 교정
5. **SSOT §5** okx_futures, bybit_futures 어댑터 행 추가

### Phase J 권고

6. **GAP-EX-1**: `test_native_coinone.py` 신설 — 표준 테스트 패턴 적용
7. **GAP-FEE-1/2**: 실 거래소 API 수수료 재확인 후 fee_model.py 또는 SSOT 중 하나를 정오로 결정
8. **cex_dex 대시보드**: by_strategy 목록에서 DEX_RPC_URL 미설정 시 cex_dex 행 숨김 처리

---

## 6. 검증 증거

```
파일 경로 확인:
  engine/src/strategies/          8개 파일 ✅
  engine/src/collectors/          15개 파일 (10개 거래소 + base + manager + funding_rate + symbol_discovery) ✅
  engine/src/friction/fee_model.py binance/bybit/okx/bitget/upbit/bithumb/coinone/
                                  binance_futures/bybit_futures/okx_futures/bitget_futures 등록 ✅

DEFAULT_EXCHANGES 등록:
  manager.py:32 → ["binance","bybit","okx","bitget","upbit","bithumb","coinone",
                    "binance_futures","okx_futures","bybit_futures"] (10개) ✅

신호 라우팅 확인:
  real_signal_producer.py:88  _evaluate_triangular ✅
  real_signal_producer.py:94  _evaluate_spot_futures ✅
  real_signal_producer.py:101 _evaluate_futures_futures ✅
  real_signal_producer.py:118 on_funding_rates_updated ✅
  shadow.py:491 _futures_exchanges = {"binance_futures","okx_futures","bybit_futures"} ✅

main.py 전략 등록:
  main.py:589-595 7개 전략 + cex_dex 조건부 ✅

테스트 수 (전략):
  cross_exchange=8, spot_futures=7, futures_futures=8, funding_rate=8,
  statistical_arb=11, latency_arb=10, triangular=21, cex_dex=25 ✅

테스트 수 (거래소):
  binance=43, bybit=24, okx=28, bitget=32, upbit=37, bithumb=36,
  coinone=24(stability), binance_futures=17, okx_futures=16, bybit_futures=18 ✅
```
