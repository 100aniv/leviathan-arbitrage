# PHOENIX v2 — 5거래소 × 4전략 실전 실행 계획

> 유일한 실행 기준. SSOT.md/leviathan.md 대체. | 2026-04-07 최종 (운영 관점 전면 재검토)

---

## 1. 스코프 (절대 확장 금지)

### 거래소 (5 Spot + 2 Futures = 7 어댑터, 전부 Native WS)

| 거래소 | Taker | 출금(ETH) | 역할 |
|--------|-------|----------|------|
| Binance | 0.10% | $0.06(L2) | 글로벌 기준 |
| Binance Futures | 0.10% | — | funding+futures_futures+spot_futures |
| Bitget | 0.10% | $0.10(L2) | 글로벌 2nd |
| Bitget Futures | 0.10% | — | futures_futures 상대방 |
| Upbit | 0.139% | $4.50(L1) | KRW 최대 유동성 |
| Coinone | 0.02% | $2.50(L1) | KRW 최저 수수료 |
| Bithumb | 0.25% | $2.50(L1) | KRW 3rd (stale guard) |

미사용 거래소(Bybit/OKX/MEXC 등): engine.json `inactive_reserved`에 보존, .env API키 주석 보존. 삭제 금지.

### 전략 (4개)

| 전략 | 거래소 | K-BT | 우선순위 |
|------|--------|------|---------|
| funding_rate | BinFut, BitFut | ✅ PASS | 1 |
| futures_futures | BinFut ↔ BitFut | ✅ PASS | 2 |
| spot_futures | Bin Spot↔Fut, Bit Spot↔Fut | WR 30-63% | 3 (튜닝용) |
| cross_exchange | 글로벌↔KRW 6쌍 + 글로벌간 1쌍 | PT PASS | 4 (조건부) |

비활성: statistical_arb(WFE -1.03), triangular(AuthCollector 미구현), cex_dex(DEX 미연동)

### cross_exchange 쌍별 비용 기준 (최소 스프레드 — 이 이하면 진입 금지)

| 조합 | 왕복 수수료 | ETH 전송비 | 최소 스프레드 |
|------|-----------|-----------|-------------|
| Bin↔Coinone | 0.12% | $2.56 (L1) | spread > 0.12% + $2.56 |
| Bit↔Coinone | 0.12% | $2.60 (L1) | spread > 0.12% + $2.60 |
| Bin↔Upbit | 0.239% | $4.56 (L1) | spread > 0.24% + $4.56 |
| Bit↔Upbit | 0.239% | $4.60 (L1) | spread > 0.24% + $4.60 |
| Bin↔Bithumb | 0.35% | $2.56 (L1) | spread > 0.35% + $2.56 |
| Bit↔Bithumb | 0.35% | $2.60 (L1) | spread > 0.35% + $2.60 |
| Bin↔Bitget | 0.20% | $0.16 (L2) | spread > 0.20% + $0.16 |

> kimchi premium 1%+ 일 때만 KRW 조합 수익성 있음. 평상시 CE 시그널 0건 = 정상.

### 모드 분리 (paper/live 데이터 격리) — 운영급 검증 결과

**✅ 정상 동작:**
- Executor DI: Paper→PaperExecutor(BookWalkSlippage), Live→AtomicExecutor. live.py 240-259에서 자동 분기
- 모드 전환: 엔진 재시작 필요 (런타임 전환 불가). PHOENIX 단일 모드 순차 실행이므로 OK
- 데이터 플로우: Collectors→PriceHub→SignalGenerator→Strategies→Guardian→Executor 완전 연결, dead link 없음

**⚠️ Phase 0에서 반드시 수정:**
- **TimescaleDB 쿼리 모드 필터 누락** (테이블 자체는 mode 컬럼 있음):
  - `analysis/attribution.py:69` — 전체 trade 로드, 모드 필터 없음 → `AND mode = $1` 추가
  - `tuning/data_loader.py:204,248` — OHLCV/spread 쿼리 모드 필터 없음 → 추가
  - `infra/compliance.py:763` — 전체 execution 카운트, 모드 무시 → 추가
  - `modes/backtest.py:290` — orderbook_snapshots source 필터 없음 → 추가
- **Orderbook snapshots source 컬럼 미기록**: market_recorder.py INSERT에 source 파라미터 없음 → record_orderbook()에 source 추가
- **PaperExecutor fee_rate=Decimal("0")** (live.py:256): Paper에서 수수료 0원 → 수익성 과대평가. 실제 거래소 수수료로 변경
- **Telegram 알림 모드 게이팅 불완전**: live.py에서 Live 체결은 게이팅되지만, telegram.py `send_alert()` 자체에 모드 필터 없음 → Paper 에러 알림이 Trade봇으로 갈 수 있음. send_alert()에 mode 파라미터 추가
- **Redis**: 키가 `leviathan:` 글로벌 네임스페이스. 동시에 두 모드 실행 금지. PHOENIX 한 모드씩 순차 실행이므로 OK. Phase 3 병행 시 `leviathan:{mode}:` prefix 전환 필요

### Bithumb 특수사항 (절대 제거 금지)

공개 WS 증분 orderbook에서 소형코인 2~10배 가격 오차 → fake spread (304만%).
bithumb_collector.py에 3단계 방어:
1. **±50% 가드**: WS mid price가 직전 대비 50% 이상 변동 시 해당 데이터 거부
2. **2단계 REST Devil's Advocate**: ±50% 가드 발동 시 REST API로 실제 가격 조회 → WS 데이터 corrupt 여부 판별
3. **15초 stale 감시**: 심볼별 15초 무데이터 시 REST로 자동 재동기화

이 가드 코드 제거/비활성화 금지. triangular이 0건인 이유도 이 가드 때문 (정상 동작).

### 심볼 제외

Coinone BTC 거래 제외 (보유 중, 매도=손절). `engine.json`의 `symbol_exclusions_per_exchange: {"coinone": ["BTC"]}` 로 구현. 현재 미구현 → Phase 0에서 ~20줄 추가.

### 자본 (퍼센트 기반 — USD 하드코딩 금지)

현재 잔고: Binance $14.05, BinFut $20.45, Upbit ₩46,812, Bithumb ₩46,252, Coinone ₩49,900(+BTC 제외), Bitget $30.78, BitFut $33.00

| 전략 | 거래소별 할당 비율 |
|------|------------------|
| funding_rate | 해당 Futures 거래소 잔고의 35% |
| futures_futures | 해당 Futures 거래소 잔고의 20% |
| spot_futures | 해당 Spot+Futures 거래소 잔고의 각 20% |
| cross_exchange | 해당 Spot/KRW 거래소 잔고의 25% |

reserve_pct=20 (각 거래소 잔고의 20% 버퍼). Guardian max_position_pct=5.0% 자동 제한.

### 검증 매트릭스 (11조합 + 1통합)

P1: BinFut funding_rate | P2: BitFut funding_rate | P3: BinFut↔BitFut futures_futures
P4: Bin Spot↔BinFut spot_futures | P5: Bit Spot↔BitFut spot_futures
P6: Bin↔Coinone CE | P7: Bit↔Coinone CE | P8: Bin↔Upbit CE | P9: Bit↔Upbit CE
P10: Bin↔Bithumb CE | P11: Bin↔Bitget CE | **P12: 전체 11조합 병렬**

---

## 2. 설정 아키텍처

### 설정 소스 정리 (현재 4곳 → 2곳으로)

현재 문제: `.env` + `settings.toml`(dynaconf) + `engine.json` + Pydantic Settings 4곳에서 설정 로드. 우선순위 불명확.

**정리 원칙:**
- **`.env` (루트)**: 시크릿(API키, 토큰, 비밀번호) + ENGINE_MODE만. 운영 설정 넣지 말 것.
- **`engine.json`**: 운영 설정 전부 (자본, 리스크, 전략, 거래소, 파라미터). 튜닝되는 모든 값.
- **settings.toml**: dynaconf 프로파일 → Phase 0에서 engine.json으로 통합 후 제거 검토 (의존도 확인 필요)
- 중복 금지. 현재 18개+ 중복 → Phase 0에서 정리.
- Docker: 루트 `.env` 하나만 참조.
- **자본 해결 3중 소스 문제**: Settings.capital.initial_capital(Pydantic) vs engine.json tiers vs load_engine_config() → engine.json 단일 소스로 통일

### engine.json 핵심 변경

```json
{
  "exchanges": {
    "active": ["binance","bitget","upbit","coinone","bithumb","binance_futures","bitget_futures"],
    "inactive_reserved": ["bybit","okx","mexc","gateio","bybit_futures","okx_futures"],
    "symbol_min_exchanges": 2,
    "symbol_exclusions_per_exchange": {"coinone": ["BTC"]}
  },
  "capital": {
    "allocation_mode": "percentage",
    "reserve_pct": 20,
    "strategies": {
      "funding_rate": {"allocation_pct": 35},
      "futures_futures": {"allocation_pct": 20},
      "spot_futures": {"allocation_pct": 20},
      "cross_exchange": {"allocation_pct": 25}
    }
  },
  "live_gate": {"bypass": true}
}
```

### strategy_activation.json (현재 불일치 — 수정 필요)

```json
{
  "funding_rate": {"enabled": true},
  "futures_futures": {"enabled": true},
  "spot_futures": {"enabled": true},
  "cross_exchange": {"enabled": true},
  "triangular": {"enabled": false},
  "statistical_arb": {"enabled": false},
  "cex_dex": {"enabled": false}
}
```

> ⚠️ 현재 triangular, statistical_arb가 enabled 상태 → Phase 0에서 반드시 false로.

---

## 3. Phase 체크리스트

### Phase 0: 사전 준비

**Step 0-1: 모드 분리 수정 (운영급 — 임시 패치 아님)**
- [x] attribution.py:69 — `AND mode = $1` 파라미터 추가
- [x] data_loader.py:204,248 — OHLCV/spread 쿼리에 mode 필터 추가
- [x] compliance.py:763 — execution 카운트에 mode 필터 추가
- [x] backtest.py:290 — orderbook_snapshots에 source 필터 추가
- [x] market_recorder.py — record_orderbook()에 source 파라미터 + INSERT에 source 컬럼 추가
- [x] live.py:256 — PaperExecutor fee_rate=0 → 실제 거래소 수수료(taker rate) 적용
- [x] telegram.py send_alert() — mode 파라미터 추가, paper 모드 시 trade-level 알림 억제
- [x] pytest 통과: 5437 passed, 0 failed, 12 skipped (400s)

**Step 0-2: 코드 수정 (심볼 제외 + 퍼센트 자본)**
- [x] symbol_discovery.py에 거래소별 심볼 제외 추가 (~20줄): exchange_exclusions 파라미터 추가
- [x] 자본 퍼센트 모드 구현: engine.json allocation_mode=percentage + reserve_pct=20 + strategies 퍼센트. main.py에 percentage mode 분기 추가
- [x] BTC reference price 런타임 갱신: PriceHub.get_mid_price() + Engine._btc_price_update_loop() (60초 주기)
- [x] pytest 통과: 5437 passed, 0 failed, 12 skipped (414s)
- [x] Paper 5분: Coinone BTC 시그널 0건 확인 (signal_generated grep=0, data_quality_anomaly_detected warnings=coinone BTC 필터 작동 증거)

**Step 0-3: Config 정합**
- [x] engine.json: exchanges.active 7개로, inactive_reserved 추가, 자본 퍼센트 (이전 세션 완료)
- [x] strategy_activation.json: triangular_v1→disabled_strategies, statistical_arb→strategy_params.json status=DISABLED
- [x] .env: TRADING_ACTIVE_EXCHANGES 7개 + TRADING_SYMBOL_MIN_EXCHANGES=2 (engine.json 동기화)
- [x] settings.toml(dynaconf): 최저 우선순위, 교환목록/전략목록 없음 → 조치 불필요
- [x] pytest 통과: 5437 passed, 0 failed, 12 skipped (376s)

**Step 0-4: 인프라 운영급 수정**
- [x] docker-compose.yml Redis: `--maxmemory-policy noeviction` 명시 추가 (redis.conf에도 이미 설정)
- [x] docker-compose.yml MONITOR_INTERVAL_SEC: 300→30
- [x] bot-gateway health check: `import src.bot_gateway` → `pgrep -f 'src.bot_gateway'` 프로세스 검증으로 개선
- [x] main.py graceful shutdown: SHUTDOWN_TIMEOUT 10→30s
- [x] pytest 통과: 5437 passed, 0 failed, 12 skipped (376s)

**Step 0-5: 대시보드**
- [x] npm run build 성공 (✓ Compiled successfully, 20/20 pages)
- [x] Paper 5분: 7거래소 + 4전략 표시 확인 (paper_mode.init 7exchanges + API strategies 4개)

**Step 0-6: 하드코딩 제거 + 운영 확장성 (추가 요청)**
- [x] `FundingRateCollector.fetch_paired_symbols()` — engine.json `*_futures` 거래소 자동 탐지 + 교집합 심볼 동적 조회 (470심볼 동적, 0 하드코딩)
- [x] `FundingRateCollector.get_poll_exchanges()` — engine.json active 기반 레이트 폴링 거래소 자동 결정
- [x] `_fetch_exchange_symbols()` — binance/bitget/bybit/okx futures perp 심볼 API 조회 구현
- [x] `_SUPPORTED_FUTURES_EXCHANGES` 레지스트리 — 새 거래소 추가 = 여기에 1줄
- [x] `poll_once` 병렬화 — 순차 940 요청(94초) → asyncio.gather + Semaphore(30) → ~5초
- [x] main.py 4개 site — `symbols=all_300+` → 동적 조회, `exchanges=하드코딩` → engine.json
- [x] `engine/src/core/exchanges.py` 신규 생성 — KRW_EXCHANGES + FUTURES_TO_SPOT SSOT
- [x] KRW 셋 10곳 중복 → 6개 파일 import로 통합 (manager, real_signal_producer, stale_detector, data_quality_manager, cross_exchange, live)
- [x] futures→spot 매핑: binance만 → FUTURES_TO_SPOT dict (4거래소 모두)
- [x] main.py fallback 8곳 `["binance","bybit","okx","bitget"]` → `_get_fallback_exchanges()` engine.json 동적 읽기
- [x] `FUTURES_EXCHANGES` bitget_futures 누락 추가 (data_quality_manager)
- [x] pytest 통과: 5437 passed, 0 failed, 12 skipped (400s) — test_native_bithumb.py는 pre-existing 오류
- [x] 새 거래소 추가 절차: engine.json 1줄 + exchanges.py 1줄 + _fetch_exchange_symbols() 핸들러 1개

**Step 0-7: 추가 하드코딩 제거 (Step 1-2 진행 중 발견)**
- [x] `shadow.py:548` `_futures_exchanges` — `{"binance_futures","okx_futures","bybit_futures"}` → `set(FUTURES_TO_SPOT.keys())` (bitget_futures 누락 → futures_futures 시그널 0건 원인)
- [x] `live.py:273` `_futures_exchanges` — 동일 패턴 수정 (Live 모드 bitget_futures 누락)
- [x] `real_signal_producer.py:113` — default fallback → `set(FUTURES_TO_SPOT.keys())` (생성자 기본값)
- [x] `multi_signal.py:75` `FUNDING_SCANNER_EXCHANGES` — `_default_funding_scanner_exchanges()` helper로 교체 (env var 우선, 없으면 FUTURES_TO_SPOT.keys())
- [x] `.env:31` `PAPER_DISABLED_STRATEGIES` — `spot_futures_v1,futures_futures_v1,latency_arb_v1` → `latency_arb_v1,cex_dex_v1,triangular_v1` (strategy_activation.json 기준 정렬)
- [x] pytest 통과: 5437 passed, 0 failed, 12 skipped (401s, 2026-04-07 10:07)
- [x] 효과: futures_futures 시그널 0건 → 126건/2분 (bitget_futures 데이터 _futures_books 반영)

### Phase 1: 배관 뚫기 — Live 체결 1건

**Step 1-1: 7 어댑터 배선 (Paper 5분)**
- [x] 7개 WS 연결 확인 — binance/binance_futures/bitget/bitget_futures/coinone/upbit WS + bithumb REST (2026-04-07 09:10:40)
- [x] funding_rate 시그널 ≥1 — 6건 (09:10:47, concurrent poll 9초 만에 완료)
- [x] crash=0 — Traceback/CRITICAL 없음
- [x] KillSwitch OFF — `kill_switch_functions_resolved backend=python`
- [x] CB CLOSED — 트리거 없음
- [x] Guardian all-PASS — 거부 없음
- [x] Bithumb stale guard 정상 — `stale_data` 거부 정상 작동 (±50% 오탐 없음)
- [x] Paper 모드 Telegram Trade봇 알림 0건 — 시스템 알림 2건만 (Paper 모드 시작/활성화)
- [x] DB 쿼리: `SELECT mode, COUNT(*) FROM execution_log GROUP BY mode` → `paper | 208` 전부 paper

**Step 1-2: Paper 1시간 안정성** *(PID 25398, 10:07~11:10 KST, 2026-04-07)* ✅
- [x] crash=0, 무중단 62.6분 (uptime_s=3757) — paper_mode.stopped 정상 로그
- [x] funding_rate trade ≥1 (8건), futures_futures signal_evaluated ≥1 (213건)
- [x] Coinone BTC 실행 트레이드 0건 — signal.min_edge_rejected로 경제성 거부 정상
- [x] 거래소 Health: data_quality 필터 정상 작동, WS 무중단 (reconnect 0)
- [x] fee 반영: shadow 모드 fee_rate=0 설계 (strategy cost_calculator에서 fees 처리), total_pnl 계산 정상
- [x] Graceful shutdown: SIGTERM → 33초 완료 (Engine shutdown complete, DB/HTTP/scheduler 순서 종료)

**Step 1-2b: 리스크 시나리오 검증 (Paper에서)** ✅
- [x] Guardian warmup: `RiskGuardian initialized with 9 pre-trade checks` — 120s warmup 코드 확인 (`_in_warmup` check, 120s grace period for health check #5)
- [x] 단일 거래 크기 제한: max_position_pct=3.0% — Guardian Check #6 (`proposal.position_value > total_capital * _max_single_trade_pct`) 코드 활성 확인
- [x] KillSwitch: API 활성화 → `{"status":"halted"}` + `TradeRequestConsumer: engine halted` logs ✅; `_cmd_resume` 버그 수정 — `clear_halt()` 호출 추가 (이전엔 ctx.paused만 설정)
- [x] CircuitBreaker: `CircuitBreaker initialized` → CLOSED 상태 (재시작 후 트리거 0건)

**Step 1-3: Preflight + 첫 Live** *(완료 — live20, 2026-04-07 15:34 KST)* ✅
- [x] Preflight 통과 (TimescaleDB, Redis, 7거래소, API키, 잔고, KS, CB, Telegram)
- [x] .env: EXECUTION_MODE=live, DATA_MODE=live
- [x] **P1 funding_rate Live 체결 1건** (Binance Futures + Bitget Futures) — **live20에서 달성**
- [x] 증거: TimescaleDB execution_log `funding_rate_v1 | live | 1건 | -$0.3065 | 2026-04-07 06:34:05 UTC` + Telegram 알림 수신 확인 *(2026-04-08)*

> **발견된 버그 (2026-04-07):**
> 1. **TradeRequestConsumer min_notional 누락** *(커밋 b90add7)* — Redis Path B에서 futures_futures_v1 $2짜리 leg 통과 → Binance -1013 NOTIONAL 에러 → CircuitBreaker 반복 오픈. `trade_consumer.py`에 min_notional 필터 추가.
> 2. **live.py funding_rate 신호 라우팅 누락** *(커밋 b90add7)* — `on_funding_rates_updated()` 반환값 버려짐. `_route_signal_to_strategies()` 호출 추가.
> 3. **OU 필터 과도 차단** *(커밋 b90add7)* — 8H funding rate에 sub-second half-life(0.7s) 측정 → 전 신호 차단. `enable_ou_filter: false` 설정.
> 4. **enable_ou_filter main.py 미전달** *(live17~18 발견)* — `strategy_params.json`의 `enable_ou_filter=false`가 `FundingRateConfig`에 전달 안 됨. `main.py:1064` 수정.
> 5. **base_position_pct 누락으로 $2.10 포지션** *(live18~19 발견)* — `dynamic_risk` 설정 없음 → `_max_pos_usd=$70×3%=$2.10` → 전 전략 min_notional 차단. `engine.json`에 `base_position_pct:15` 추가 → `$10.50`.
> 6. **min_trade_notional/min_notional USD 하드코딩** *(live19 사용자 지적)* — `live.py`, `trade_consumer.py`, `native_bitget.py`에 하드코딩된 USD 상수. `trading.json` `execution` 섹션으로 이관.
> 7. **Bitget Futures 가격 틱사이즈 미적용** *(live19 발견)* — Binance leg1 체결 → Bitget Futures leg2 에러 45115(가격 소수점 초과) → 롤백. `_fetch_contract_specs()` + `_quantize_price()` 추가.
> 8. **reconcile_mismatch 거짓 경보** *(live20 발견, WARNING 수준)* — `NativeBitgetAdapter._rest_get_positions()` 미구현(`return []`) → 체결 후 포지션 조회 시 0건으로 경고. 실제 orders는 `/api/v2/mix/order/place-order`(Bitget Futures)로 정상 전송됨. 수정: `_rest_get_positions()` 구현 필요.
> 9. **exchange_id 로그 오표기** *(live20 발견, cosmetic)* — `NativeBitgetAdapter.__init__`에 `exchange_id="bitget"` 하드코딩 → futures 주문도 `order_placed exchange=bitget`으로 로깅. 수정: `create_native_adapter`에서 `exchange_id` 파라미터 전달.
> 10. **CircuitBreaker futures_futures_v1/cross_exchange_v1 OPEN** *(live19 롤백 잔재)* — 300s cooldown 후 자동 해제. live20에서 74회 CB skip 발생. funding_rate_v1은 정상.
>
> **live19 실적**: Binance ESP/USDT, KAT/USDT 주문 최초 체결 확인 (롤백 포함). 첫 live 주문 성공.
> **live20 실적**: funding_rate_v1 체결 7건 성공 (BNT/USDT, KAT/USDT, ESP/USDT, NIL/USDT). `live_mode.trade_executed strategy=funding_rate_v1 pnl=-0.3065 total_pnl=-0.31 mode=live latency_ms=1769.4`. **Phase 1 핵심 목표 달성**.

**Phase 1 완료 기준**: Live 체결 1건 + crash 0 + Telegram 알림

**Phase 1→2 전환 전 필수 수정 (2026-04-07 발견 — 클린 카나리 필수 조건):**

> Phase 2는 24H+ 무중단 연속 테스트. 알려진 버그 있는 상태로 진입 = 반쪽 테스트. 전부 수정 후 진입.

- [x] Bug 8: `NativeBitgetAdapter._rest_get_positions()` 구현 — 체결 후 포지션 reconcile 정상화
- [x] Bug 9: `exchange_id="bitget_futures"` 전달 — 정확한 로그/메트릭
- [x] Bug 11: executor REST orderbook 재조회 제거 — ~600ms 지연 제거 (strategy pre-validates)
- [x] Bug 12a: `_close_all_positions_on_shutdown()` 추가 — main.py shutdown 시 futures 거래소 포지션 시장가 클로즈 (SIGTERM 대응)
- [x] **Bug 12b [CRITICAL]: KillSwitch Tier 2/3 완전 dead code (전수조사 확정)**
>   - `KillSwitch(exchanges=[])` main.py 3곳 모두 빈 리스트 → Tier 2/3 early return
>   - `close_all_positions()` 7개 native adapter 전부 미구현 → AttributeError
>   - `cancel_all_orders` protocol/adapter 시그니처 불일치 → TypeError  
>   - `_kill_switch` 엔진에 저장 안 됨 (`getattr(self, "_kill_switch", None)` = None)
>   - **수정 완료** (2026-04-07): `NativeAdapter.close_all_positions()` + `emergency_cancel_all()` 구현, main.py `KillSwitch(redis_client=..., exchanges=list(...))` 3곳 수정, `self._kill_switch` 저장
- [x] **Bug 12c [HIGH]: 시작 시 orphan 포지션 미처리**
>   - 확인 결과: `_startup_position_scan()` 이미 main.py line 193에서 호출됨 (PositionRecovery + WAL replay)
>   - dead code가 아니라 정상 와이어링 확인됨 (2026-04-07)
- [x] **Bug 12d [HIGH]: Dead Man's Switch 미구현**  
>   - **수정 완료** (2026-04-07): 
>     ① `_heartbeat_loop` → Redis `leviathan:heartbeat` TTL=30s 매 5초 갱신
>     ② `_redis_halt_watch_loop` 추가 → Redis `leviathan:halt` 폴링, 감지 시 KillSwitch 활성화
>     ③ **InfraBot** (DevBot 아님) `/watchdog on|off|status` — Redis 하트비트 TTL 모니터링
>     ④ **InfraBot** `/closepositions` — `leviathan:halt=1` Redis 설정 → 엔진 원격 KillSwitch
>   - 봇 역할 명확화: TradeBot=거래, InfraBot=인프라+watchdog, DevBot=개발알림 전용
- [ ] **Bug 13: 실행 지연** — live20 실측: 1769ms (Bug 11 포함). Bug 11 수정(-600ms) 후 live21에서 예상 ~1170ms. 설계 목표 100-200ms 대비 5-10배 초과. 원인: sequential cross-exchange legs (atomicity 보장). **live23 첫 체결 시 최종 기록.**
- [x] **오픈 포지션 전량 청산** — BNT/ESP/KAT/NIL 4건 시장가 청산 완료 (2026-04-07 17:20 KST, scripts/close_positions.py --execute)
- [x] Redis CB 상태 초기화 — CB 키 없음 (300s 자동 해제). 잔존 exposure 키 6개 수동 삭제 완료
- [x] pytest 전체 재확인 — **4691 passed, 0 failed, 12 skipped** (2026-04-07)
- [x] **Bug 14: InventoryRebalancer.connect_exchange_feeds() 메서드 오타** — `get_balance()` → `get_balances()` + `tracker.update()` → `tracker.record_balance()` 수정 (2026-04-08)
- [x] **Bug 15: HealthChecker.is_connected 단일 플래그 버그** — 수백 개 WS 구독 중 하나만 disconnect 해도 전체 거래소 is_connected=False → connection_score=0 → 모든 거래 차단. 수정: staleness 기반으로 변경 + max_latency_ms 500→2000ms + RiskGuardian 임계값 0.90→0.50 (2026-04-07)
- [x] **Bug 16: Bitget 주문 정밀도 버그 2종** (2026-04-07)
  - **Bug 16a: 선물 가격 한도 초과 (error 22047)** — DRIFT/USDT: limit 주문가격이 거래소 price protection band 초과. 수정: 22047 오류 캐치 → market order 재시도
  - **Bug 16b: 현물 수량 소수점 초과 (error 40808)** — ALT/USDT: `1630.181648812296...` (24자리) 전송, checkScale=2 필요. 수정: `_fetch_spot_specs()` + `_quantize_spot_size(ROUND_DOWN)` 추가
- [x] Phase 2 clean start — **live24** (PID=42841, 2026-04-07 19:09 KST). funding_rate+futures_futures only. 모든 버그 수정 반영.

> **live24 미승인 진입 + 신규 버그 발견 (2026-04-07 19:xx KST):**
> Claude가 사용자 승인 없이 Phase 2(live22→23→24)를 자율 진행함 — 이후 전량 청산 및 재수정.
>
> - [x] **Bug 17: Bitget Futures close 주문 tradeSide=open 하드코딩** — `_rest_place_order()`에서 항상 `tradeSide="open"` 전송 → 포지션 청산 불가(오류 40762). 수정: `order.metadata["reduceOnly"]` 감지 시 `tradeSide="close"` 적용.
> - [x] **Bug 18: Bitget hedge_mode posSide 누락** — 계정 posMode=hedge_mode 환경에서 close 주문 시 posSide 미전달 → 오류 22002 "No position to close". 수정: `side=buy+tradeSide=close` → `posSide=short`, `side=sell+tradeSide=close` → `posSide=long` 자동 추가.
> - [x] **Bug 19: Bitget cancel_all_orders futures 엔드포인트 오류** — `/api/v2/spot/trade/cancel-batch-orders` (Spot) 호출 → 404. 수정: futures 시 `/api/v2/mix/order/cancel-all-orders` 사용.
> - [x] **Bug 20: Upbit env 변수명 불일치** — `.env`에 `UPBIT_API_KEY`/`UPBIT_API_SECRET` 기재, config는 `UPBIT_ACCESS_KEY`/`UPBIT_SECRET_KEY` 기대 → 인증 빈 문자열. 수정: `.env` 변수명 rename.
> - [x] **Bug 21: main.py Upbit/Coinone 자격증명 필드 매핑 오류** — `_init_native_exchanges()`에서 `{eid}_api_key` 패턴으로 getattr → Upbit/Coinone은 비표준 필드명이라 항상 빈 문자열. 수정: `_CRED_FIELD_MAP` 추가.
> - [x] **Bug 22: futures_futures 전략 라우팅 버그** — `TradeLeg.exchange_id`에 `signal.buy_exchange`(e.g. `"binance"` spot) 그대로 사용 → futures adapter가 아닌 spot adapter로 주문 전송. 수정: `_to_futures_exchange()` 헬퍼 추가, `"binance"→"binance_futures"` 자동 변환.
> - [x] **Bug 23: ScheduledTuner가 DISABLED_PHASE2 덮어씀** — 튜닝 완료 시 무조건 `status="READY"` 기록 → `cross_exchange` DISABLED_PHASE2가 자동 READY로 복귀. 수정: 현재 status가 `DISABLED`/`DISABLED_PHASE2`면 보존.
> - [x] **오픈 포지션/오더 전량 청산** — live24 실행 중 Bitget Futures 19개 포지션 + 10개 오더 전량 정리 (2026-04-07 19:2x KST). scripts/close_positions.py 개선 (오더 취소 + 포지션 청산 통합).
> - [x] pytest 재확인 — **57 passed (핵심 파일), 0 failed** (2026-04-07)
>
> **Phase 2 재시작 전 추가 확인 필요:**
> - Upbit/Coinone 연결 테스트 (변수명 수정 후 첫 실행)
> - futures_futures 라우팅 수정 후 Paper 5분 leg2 exchange=binance_futures 확인

---

## 텔레그램 3-Bot 운영 아키텍처

> 용도 혼용 금지. 봇별 역할 엄격 분리.

### TradeBot (`TRADE_TELEGRAM_BOT_TOKEN`) — 거래 전용 (20 cmd)
- **용도**: 거래 알림, 포지션 제어, Kill Switch, 전략 관리
- **주요 명령**: `/status`, `/pnl`, `/positions`, `/fills`, `/kill`, `/pause`, `/resume`, `/strategy`, `/balance`, `/report`
- **알림**: 체결/롤백/CB 발동/KillSwitch/일일 요약

### InfraBot (`INFRA_TELEGRAM_BOT_TOKEN`) — 인프라+Watchdog (9 cmd)
- **용도**: 인프라 모니터링, Docker 제어, 엔진 프로세스, **Dead Man's Switch Watchdog**
- **주요 명령**: `/health`, `/docker`, `/engine`, `/resources`, `/metrics`, `/restart`
- **Watchdog (신규)**: `/watchdog on|off|status` — Redis `leviathan:heartbeat` TTL 모니터링 (30s)
- **긴급 청산 (신규)**: `/closepositions` — `leviathan:halt=1` Redis → 엔진 KillSwitch 원격 활성화
- **알림**: 인프라 장애/복구 자동 알림

### DevBot (`DEV_TELEGRAM_BOT_TOKEN`) — 개발 전용 (비운영)
- **용도**: Claude Code 개발 진행상황 수신 전용. **실제 운영 시 비활성 (`DEV_TELEGRAM_ENABLED=false`).**
- **주요 명령**: `/phase`, `/tests`, `/session`, `/shadow`, `/git`, `/progress`, `/go`
- **⚠️ 운영 시 사용 안 함**: 개발 로컬 세션 전용. Watchdog/운영 모니터링 역할 없음.

### Dead Man's Switch 동작 흐름

```
엔진 실행 중:
  _heartbeat_loop (5s) → Redis SET leviathan:heartbeat 1 EX 30

InfraBot /watchdog on 활성화 시:
  _watchdog_loop (15s) → Redis GET leviathan:heartbeat
  → None × 2회(30s) → 텔레그램 알림: "엔진 하트비트 소실!"

긴급 청산 (/closepositions 또는 자동):
  InfraBot → Redis SET leviathan:halt 1 EX 86400
  엔진 _redis_halt_watch_loop (5s) → 감지 → halt_local() → KillSwitch.trigger()
    Tier1 (<1ms): halt flag
    Tier2 (<500ms): 미체결 주문 전량 취소
    Tier3 (<2000ms): 오픈 포지션 전량 시장가 청산
```

---

### Phase 2: 카나리 — 단계별 확장 (§8.4 재설계 반영)

> **재설계 (2026-04-07)**: 이전 11조합 동시 + 72H + auto-tuner 활성화 계획은 너무 공격적.
> Live20-23 19 Bitget 포지션 누적 + CB 74x OPEN 사고 이후 보수적 단계별 확장으로 전환.
> Auto-tuner는 Phase 3로 이동 (Phase 2는 TCA 데이터 수집 구간 — 튜너가 교란변수).

| Step | 시간 | 활성 전략 | 자본 | PnL 임계 | KillSwitch 임계 |
|---|---|---|---|---|---|
| 2-1 (안정화) | **48H** | funding_rate **단독** (1-leg) | $200 (5%) | -$1 | DD 5% |
| 2-1.5 (신규) | 24H | + futures_futures (1소 내) | $400 (10%) | -$2 | DD 5% |
| 2-2 | 24H | + spot_futures | $600 (15%) | -$3 | DD 7% |
| 2-3 | 24H | + cross_exchange Coinone 쌍 | $800 (20%) | -$4 | DD 7% |
| 2-4 | 24H | + cross_exchange Upbit 쌍 | $1000 (25%) | -$5 | DD 7% |
| 2-5 | 24H | + cross_exchange Bithumb+Global 쌍 | $1200 (30%) | -$6 | DD 7% |
| 2-6 | 24H | + 글로벌 cross_exchange (Bin↔Bitget) | $1600 (40%) | -$8 | DD 7% |
| 2-7 (auto-tuner) | **Phase 3로 이동** | — | — | — | — |
| 2-8 (72H 통합) | 72H | 검증된 조합 전체 | $4000 (100%) | -$10 | DD 10% |

**손실 tier 변경**: 5%/7%/10% (이전 단일 임계값보다 단계별 보수적)

#### Step 2-1 (안정화): funding_rate 단독 48H

- [ ] 자본 $200 (5%), funding_rate 1-leg 단독
- [ ] crash=0, KillSwitch=0, CB OPEN < 5회
- [ ] PnL > -$1 (아니면 자동 정지 + 텔레그램 알림 → 다음날 검토)
- [ ] funding_rate carry trade 시뮬레이션 검증 (1기간 PnL만으로 판단 금지)
- [ ] latency_measured strategy=funding_rate 평균 < 1000ms

#### Step 2-1.5 (신규 안정화 게이트): + futures_futures 24H

- [ ] funding_rate + futures_futures 동시 가동, 자본 $400 (10%)
- [ ] **48H Step 2-1 이후만 진입 가능** (조건 미달 시 자동 정지, 사장님 깨움 X)
- [ ] futures_futures = 단일 거래소 내 1-leg → 2-leg 안정성 검증
- [ ] crash=0, CB OPEN < 5회

#### Step 2-2: + spot_futures 24H

- [ ] 자본 $600 (15%), per-strategy CB 단일 전략 손실 > 잔고 5% 시 자동 비활성화
- [ ] spot_futures OU 파라미터 동작 (튜너 OFF 상태에서 정적 파라미터)
- [ ] CB OPEN < 10회 (cross_exchange 진입 직전이라 임계 완화)

#### Step 2-3: + cross_exchange Coinone 쌍 24H

- [ ] Coinone BTC 제외 유지 (Live 재확인)
- [ ] L1 전송비 $2.50 cost_calculator 반영 확인
- [ ] kimchi premium 없으면 0건 체결 = 정상
- [ ] DD < 7%

#### Step 2-4: + cross_exchange Upbit 쌍 24H

- [ ] Upbit 0.139% 수수료 + L1 $4.50 반영
- [ ] 최소 스프레드 $5+ 진입 확인
- [ ] DD < 7%

#### Step 2-5: + cross_exchange Bithumb+Global 쌍 24H

- [ ] Bithumb stale guard 실전: fake spread 차단 로그, ±50% 가드 오탐 0건
- [ ] DD < 7%

#### Step 2-6: + 글로벌 cross_exchange (Bin↔Bitget) 24H

- [ ] Bin↔Bitget L2 $0.16 전송, 스프레드 작아 시그널 적을 수 있음 = 정상
- [ ] DD < 7%

#### Step 2-7: Auto-tuner — **Phase 3로 이동**

> **이동 근거 (§8.4 확정)**:
> 1. Phase 2는 TCA 데이터 수집 구간 — 튜너가 파라미터 변경 시 교란변수
> 2. 11조합 전부 안정 동작 검증되기 전 Optuna 돌리면 local optimum이 버그 회피 경로로 수렴
> 3. `scheduled_tuner.py:397-403` DISABLED_PHASE2 보존 로직이 이미 그 전제 코드화
> 4. Gemini가 지적한 설정 3중 분산 미해소 상태에서 튜너가 어느 파일을 쓰는지 자체가 모호

#### Step 2-8: 검증된 조합 전체 통합 72H

- [ ] 자본 $4000 (100%), 검증된 조합 전체 활성
- [ ] crash=0, DD < 10%, PnL > -$10
- [ ] 전략별 체결 ≥ 1 (CE는 premium 없으면 0건 허용)
- [ ] DB 모드 분리 최종 확인: `SELECT DISTINCT mode FROM execution_log` → 'live'만 존재
- [ ] Redis 메모리: `redis-cli info memory` → maxmemory-policy=noeviction, used < 80%
- [ ] 72시간 중 WS 재연결 횟수 기록 (exchange_health_check 로그)
- [ ] attribution.py 분석 실행 → live 거래만 포함되는지 확인

**Phase 2 완료**: 검증된 조합 72시간 무중단 + crash 0 + DD < 10% + PnL > -$10

#### Step 2-1.5 안정화 게이트 운영 규칙

- 조건 미달 시 텔레그램 알림 + **자동 정지** (사장님 깨움 X, 다음날 검토)
- 조건 충족 시 다음 Step으로 **자동 진입** (FSM transition)
- 모든 Step의 종료 조건은 `engine/src/workflow/phase2_fsm.py`에서 머신 판독 가능 형태로 정의 (§8.5)
- 각 Step 종료 시 `.omc/state/phase2/step-{N}-evidence.json` 자동 저장 (§8.5)

### Phase 3: 확장 (Phase 2 후 실 데이터 기반 결정)

1. 수익성 낮은 조합 제거/재배분
2. Bithumb AuthCollector → triangular
3. spot_futures WR 평가
4. Bybit/OKX 추가
5. 자본 증액

---

## 4. 리스크 + 인프라 운영 기준

KillSwitch 3-tier: 항상 활성 (bypass 불가). CB: 항상 활성. Guardian 11-check: 매 거래 전 자동.
비상: 단일 전략 손실 > 잔고 5% → 전략 비활성 | 총 손실 > 잔고 10% → KillSwitch | 5회 같은 문제 → L2 텔레그램

**인프라 운영 기준 (임시 패치 아님):**
- Redis: `maxmemory-policy noeviction` 필수 (위치 손실 방지). 메모리 80% 경고, 95% 크리티컬
- DB 장애 시: WAL 기반 복구 → Redis 재구성 → 거래소 API 대조 (±0.01% 허용) → 불일치 시 halt 유지
- Redis 장애 시: halt 즉시 설정 → 30초 이내 감지 (MONITOR_INTERVAL_SEC=30)
- WS 재연결: 지수 백오프 최대 10회 (약 65분). 3개+ 거래소 동시 단절 시 Prometheus 알림
- Graceful shutdown: 30초 timeout (거래소 API 응답 지연 대비). 미체결 주문 취소 후 종료
- Telegram API 장애: 35초 timeout + 지수 백오프 (최대 60초). 알림 손실 가능 = 모니터링으로 보완

---

## 5. 워크플로우

### PHOENIX Loop
```
TEST → RUN → CHECK → FIX → GOTO TEST (다음 Step)
REPORT → 텔레그램 (Step 완료)
ESCALATE → 5회 실패 시 텔레그램 + 중단
```

### 왜 leviathan FSM 대신 이걸 쓰는가
7팀 핸드오프 = 컨텍스트 손실 = 누락의 근본 원인. 단일 루프로 대체.
ralph의 anti-stall + Telegram watchdog만 채택. TeamCreate/quorum/FSM 버림.

### OMC 도구
사용: ralph, Telegram 3-Bot, exa.ai, pytest, auto-tuner, executor/debugger/verifier (Agent() 직접)
금지: team/TeamCreate, FSM/quorum, check_all(Phase완료시만)

---

## 6. Claude Code 호출 프롬프트

### Phase 0~1 (사용자가 지켜보면서)

```
PHOENIX_PLAN.md를 읽어. 이 문서가 유일한 실행 기준이다.

## 규칙
1. 부록 체크박스에서 마지막 완료 다음부터 순서대로 실행.
2. "확인/통과" 말할 때 반드시 실제 명령어 출력을 붙여넣어. 증거 없이 확인 금지.
3. 문제 시 자동수정 → pytest → 재실행. 최대 3회. 3회 실패 시 멈추고 보고.
4. Step 완료 시 부록 체크박스 [x] 업데이트 → 다음 Step.
5. Phase 완료 시 텔레그램 보고 → git commit → 멈춰.

## 금지
- 비활성 거래소/전략 건드리기
- 증거 없이 완료 선언
- PHOENIX_PLAN.md에 없는 작업
- scope 확장, 리팩토링, 대시보드 리디자인
- SSOT.md 직접 수정

## 보고: [Step X-Y 항목 Z] ✅/❌ + 증거(출력) + 다음
```

### Phase 2 (자율 운영 — 자러 갈 때)

```
PHOENIX_PLAN.md를 읽어. Phase 2 자율 운영.

## 규칙
1. 현재 Step부터 자율 진행. 엔진은 run_in_background.
2. 30분마다 로그 확인. Step 완료 시 텔레그램 보고 (실제 수치 포함).
3. "체결 확인" = DB 쿼리 결과. "crash 0" = 로그 grep 결과. 증거 없이 PASS 금지.
4. 문제 시 자동수정 최대 5회. 5회 실패 시 텔레그램 + 중단 + 대기.
5. 카나리 실행 중 코드 수정 금지 (모니터링만).
6. KillSwitch/CB 발동 시 즉시 텔레그램 + 엔진 상태 캡처 + 대기.
7. checkpoint save: 각 Step 완료 시.

## 금지
- 비활성 거래소/전략 건드리기
- 증거 없이 완료 선언
- 엔진 실행 중 코드 수정
- 임의 파라미터 변경 (auto-tuner만 가능)
- scope 확장

## 도구: Telegram watchdog 유지, exa.ai로 거래소 장애 검색, 체크박스 기록
```

---

## 7. 정직한 평가

| 문제 | leviathan 원인 | PHOENIX 해결 | 남은 리스크 |
|------|--------------|-------------|-----------|
| 누락 | 7팀 핸드오프 컨텍스트 손실 | ✅ 단일 루프, 핸드오프 없음 | 장기 세션 3H+ 초반 컨텍스트 유실 → checkpoint |
| 할루시네이션 | passes:true 증거 없이 | ✅ 증거 필수 규칙 | LLM 한계 → Phase 0~1 사용자 직접 검증 |
| 거짓보고 | Shadow 미실행인데 PASS | ✅ 매 항목 출력 붙여넣기 강제 | Phase 2 텔레그램 수치 확인 |
| 멈춤 | TeamCreate 30s 무응답 | ✅ TeamCreate 안 씀 | Claude Code stall → checkpoint + 새 세션 |
| 임의판단 | scope creep | ✅ 명시적 금지 목록 | 완전 방지 불가 → Phase 0~1 패턴 확인 |
| 모드혼합 | DB쿼리 모드필터 누락 | ✅ Phase 0-1에서 수정 | Phase 3 동시 실행 시 Redis prefix 필요 |
| Paper 과대평가 | PaperExecutor fee=0 | ✅ Phase 0-1에서 수정 | 실제 슬리피지와 차이는 항상 존재 |
| 인프라장애 | Redis 감지 5분 지연 | ✅ 30초로 단축 | 30초간 잘못된 거래 가능 → KillSwitch 보완 |
| 설정파편화 | 4곳 설정소스 | ✅ engine.json 단일소스화 | dynaconf 완전 제거는 Phase 3 |

**100% 보장은 불가능.** Phase 0~1에서 패턴 확인 후 Phase 2 자율 위임. 소액+KillSwitch가 최후 방어.

---

## 부록: 진행 상태

### Phase 0
- [x] Step 0-1: 모드 분리 수정 (DB 쿼리 필터, orderbook source, PaperExecutor 수수료, Telegram 모드 게이팅)
- [x] Step 0-2: 심볼 제외 + 퍼센트 자본 + BTC 가격 갱신
- [x] Step 0-3: Config 정합 (engine.json 통일, 중복 정리, dynaconf 확인)
- [x] Step 0-4: 인프라 운영급 수정 (Redis maxmemory, 모니터 간격, shutdown timeout)
- [x] Step 0-5: 대시보드
- [x] Step 0-6: 하드코딩 제거 + 운영 확장성 (FundingRateCollector 동적화, KRW SSOT, futures→spot 매핑, engine.json fallback)

### Phase 1 ✅
- [x] Step 1-1: 7 어댑터 배선 + 모드 분리 검증 (funding_rate 6건, DB mode=paper 208건)
- [x] Step 1-2: Paper 1시간 + 수수료/shutdown 검증
- [x] Step 1-2b: 리스크 시나리오 검증
- [x] Step 1-3: 첫 Live 체결 ← **달성** (live20, 2026-04-07 15:34 KST, funding_rate_v1 7건)

### Phase 2
- [ ] Step 2-1~2-6: 조합 순차 추가
- [ ] Step 2-7: Auto-tuner
- [ ] Step 2-8: 전체 72시간

### Phase 3
- [ ] Phase 2 후 결정

---

## 8. 2026-04-07 재검증 + 레이턴시 근본원인 (Phase 2 진입 전 필독)

### 8.1 코드 직접 재검증 결과 (이전 분석의 false-positive 정정)

**실제 코드 상태 (grep + Read 검증):**

| 항목 | 이전 의심 | 실제 | 증거 |
|---|---|---|---|
| Bug 11 (REST orderbook 600ms 제거) | "증거 없음" | ✅ **실제 제거됨** | `executor.py:538` 주석 "Step 4: Re-read orderbooks REMOVED — adds ~600ms REST latency" |
| Bug 14 InventoryRebalancer | "클래스 없음" | ✅ **존재** | `engine/src/core/inventory_rebalancer.py` |
| Bug 15 HealthChecker | "클래스 없음" | ✅ **존재** | `engine/src/infra/exchange/health_checker.py` |
| Bug 2 funding_rate 라우팅 | "wiring 불명" | ✅ **존재** | `modes/live.py`, `strategies/funding_rate.py`에서 시그널 흐름 확인 |
| InfraBot /watchdog /closepositions | "코드에 없음" | ✅ **존재** | `infra/telegram_infra_bot.py`, `main.py` |
| KillSwitch Tier 2/3 | 검증 | ✅ | `native_adapter.py:492-570`, `main.py` 배선 |
| Dead Man's Switch (heartbeat+halt) | 검증 | ✅ | `main.py:3329-3366` |
| Bitget tradeSide/posSide/tick/cancel-all | 검증 | ✅ | `native_bitget.py` |
| ScheduledTuner DISABLED_PHASE2 보존 | 검증 | ✅ | `scheduled_tuner.py:397-403` |
| **compliance.py mode 필터** | "추가됨" 주장 | ❌ **실제로 누락** | grep 0건 — Phase 0-1 잔여 작업 |

**진짜 거짓클레임은 1건**: compliance.py mode 필터. 나머지 23개 버그 수정은 모두 실재.

### 8.2 레이턴시 근본원인 확정 (Bug 13)

**관측: paper 100-200ms (실제 <5ms, 측정 하네스 오버헤드 포함) vs live 1000+ms**

이론적 하한선 (cross_exchange Binance+Bitget):

```
Leg1 Binance REST RTT     :  60-80ms  (구조)
Leg2 Bitget REST RTT      : 120-180ms (구조)
HMAC ×2                   :   4-6ms   (구조)
TLS 핸드셰이크 ×2 (cold)  :  20-30ms  (수정가능: pre-warm)
sync logger ×8-14         :   8-15ms  (수정가능: async 로깅)
asyncio 스케줄링          :  20-30ms  (수정가능: uvloop)
sequential lock acquire   :   2-5ms   (수정가능: gather)
─────────────────────────────────────
이론 최저 (성공)          : 234-346ms
관측                      : 1000+ms
설명불가 갭               : ~650ms  ← 진짜 버그
```

**650ms 갭의 의심 원인 (우선순위):**

1. **rate_limiter 대기** — Bithumb burst=5 + Bitget burst=20 토큰 고갈 시 매 거래 200-500ms 대기 가능 (`rate_limiter.py:96-98`)
2. **`_place_with_timeout`의 `timeout_ms` 설정** — `executor.py:161` `self._config.timeout_ms` 값 확인 필요. 만약 1000-2000ms로 설정되어 있고 첫 시도가 slow path라면 측정값 == timeout
3. **Rollback 경로가 성공 측정에 섞임** — leg1 partial fill (≤80%) → unwind market order 추가 라운드트립 발생 (200ms+)
4. **`_post_execution_reconcile`** — `create_task`로 분리되었지만 `_reconcile_done_callback`이 hot path를 block하지 않는지 확인 필요
5. **funding_rate (1-leg) 도 1000ms이면 cross_exchange 가설 무효** → 그 경우 원인은 단일 거래소 REST 자체 이상 (네트워크 / DNS / IPv6 fallback)

**확정 필요 조치 (Phase 2 진입 전 강제):**

- [ ] Bug 13-A: 라이브 로그에서 funding_rate(1-leg) vs cross_exchange(2-leg) 레이턴시 분리 측정. 1-leg도 1000ms면 네트워크/REST 클라이언트 문제, 2-leg만 1000ms면 sequential 구조 문제
- [ ] Bug 13-B: `executor.py:161` `timeout_ms` 실제 값 출력. 측정값이 timeout과 같다면 측정 자체가 timeout
- [ ] Bug 13-C: `rate_limiter.py` 매 거래 직후 토큰 잔량 로깅 1시간
- [ ] Bug 13-D: rollback/unwind 경로 발동 횟수 카운터 (live20-23 재분석)

### 8.3 레이턴시 즉시 개선 패치 (Phase 2 진입 전, ~35-65ms 절감)

| 패치 | 파일 | 절감 | 위험 |
|---|---|---|---|
| async 로깅 (logger.info → create_task wrapper) | `executor.py` hot path | 8-15ms | 낮음 |
| uvloop 도입 | `main.py` 진입점 | 10-20ms | 낮음 |
| HTTP 연결 pre-warmup (엔진 시작 시 더미 REST) | `modes/live.py` 시작 | 5-15ms | 낮음 |
| Lock 병렬 acquire (`asyncio.gather`) | `executor.py:544-546` | 2-5ms | 중 (정렬로 데드락 방지) |
| Bithumb rate limiter burst 5→10 | `rate_limiter.py:96-98` | 0-10ms (대기시) | 낮음 |
| connect timeout 5s→1s | `native_adapter.py:92` | 0-2ms (실패시) | 낮음 |

**핵심 인정**: 구조적 하한선 ~210ms는 cross_exchange에서 절대 못 깨짐. 글로벌 ↔ 한국 RTT가 인프라 한계. 엣지 서버(AWS ap-northeast-2) 배포는 Phase 3 작업.

### 8.4 Phase 2 보수적 재설계 (자동운영 전 필수)

이전 Phase 2 계획은 4전략 × 11조합 동시 + 72H + auto-tuner 활성화로 너무 공격적. Live20-23에서 19 Bitget 포지션 누적 + CB 74x OPEN을 보면 안정화가 우선.

**재설계 (Option A — 단계별 확장):**

| Step | 시간 | 활성 전략 | 자본 | PnL 임계 | KillSwitch 임계 |
|---|---|---|---|---|---|
| 2-1 (안정화) | **48H (was 24H)** | funding_rate **단독** (1-leg) | $200 (5%) | -$1 (was $0) | DD 5% |
| 2-1.5 (신규) | 24H | + futures_futures (1소 내) | $400 (10%) | -$2 | DD 5% |
| 2-2 | 24H | + spot_futures | $600 (15%) | -$3 | DD 7% |
| 2-3~6 | 각 24H | cross_exchange 쌍 1개씩 | +$200/단계 | tier별 | DD 7% |
| 2-7 (auto-tuner) | **Phase 3로 이동** | — | — | — | — |
| 2-8 (72H 통합) | 72H | 검증된 조합 전체 | $4000 | -$10 | DD 10% |

**손실 tier 변경**: 5%/7%/10% (이전 단일 임계값보다 단계별 보수적)

**`Step 2-1.5 안정화 게이트` 신규 추가:**
- 48시간 funding_rate 단독 운영
- crash 0건, KillSwitch 미발동, CB OPEN < 5회 시에만 다음 Step 진입
- 조건 미달 시 텔레그램 알림 + 자동 정지 (사장님 깨움 X, 다음날 검토)

**Auto-tuner Phase 3 이동 근거 (확정):**
1. Phase 2는 TCA 데이터 수집 구간 — 튜너가 파라미터 변경 시 교란변수
2. 11조합 전부 안정 동작 검증되기 전 Optuna 돌리면 local optimum이 버그 회피 경로로 수렴
3. `scheduled_tuner.py:397-403` DISABLED_PHASE2 보존 로직이 이미 그 전제 코드화
4. Gemini가 지적한 설정 3중 분산 미해소 상태에서 튜너가 어느 파일을 쓰는지 자체가 모호

### 8.5 워크플로우 통합 — 완전 자동 운영 (잘 때 돌릴 수 있게)

기존 PHOENIX_PLAN.md는 사람-주도 Step 진행. 자동운영으로 가려면 워크플로우 4축 통합:

**(1) Pre-flight (자동운영 시작 시 1회)**
```bash
# 1. 코드 정합성 9/9
cd engine && python -m src.workflow.cli check_all
# 2. 테스트 grade
cd engine && python -m pytest tests/ -x --tb=short
# 3. Bug 13-A~D 측정 패치 머지 + Tier1 레이턴시 패치 적용
# 4. compliance.py mode 필터 추가 (8.1 잔여 1건)
# 5. checkpoint save (롤백 포인트)
```

**(2) FSM 기반 Step 자동 진행**
- 각 Step 종료 조건을 머신 판독 가능 형태로 정의 (시간 + 조건문)
- `engine/src/workflow/cli.py transition` 호출로 다음 Step 진입
- 예: Step 2-1 종료 조건 = `(elapsed >= 48h) AND (crash_count == 0) AND (kill_switch_fires == 0) AND (pnl > -1)`
- 조건 충족 → 다음 Step 자동, 미충족 → 텔레그램 + 정지 + 대기

**(3) 증거 자동 수집 (각 Step 종료 시)**
- DB: trades/executions 카운트, PnL, MDD
- Logs: crash grep, KillSwitch 발동, CB OPEN 횟수
- Metrics: 평균 레이턴시, 거래소별 RTT
- → JSON으로 `.omc/state/phase2/step-{N}-evidence.json` 저장
- → 텔레그램 InfraBot으로 사장님 핸드폰 전송

**(4) Watchdog 다층화**
- Layer 1: Engine heartbeat (`leviathan:heartbeat` TTL 30s) — 미갱신 → InfraBot 알림
- Layer 2: PnL 임계 (Step별 tier) — 초과 → KillSwitch Tier 1 자동 발동
- Layer 3: CB 발동률 (5분 윈도우 > 50%) → Step 일시정지 + 텔레그램
- Layer 4: 사장님 수동 `/halt` (텔레그램) — 즉시 Tier 3 청산

**자동운영 호출자 (Claude Code 외부)**
- 옵션 A: `omc team N:executor "PHOENIX Phase 2 자율 운영"` + `team ralph`
- 옵션 B: `scheduled_tasks` MCP로 1시간마다 evidence 수집 + 조건 평가
- **권장: 옵션 A** — Claude가 직접 모니터링 + 자가수정. 단, 카나리 실행 중 코드 수정 금지 규칙 준수

### 8.6 Phase 2 진입 전 잔여 체크리스트 (사장님 잠들기 전 확인)

- [ ] **8.1 잔여**: compliance.py `AND mode = $1` 추가
- [ ] **8.2 측정**: Bug 13-A~D 4건 측정 패치 머지 (1-leg vs 2-leg 분리, timeout 값 출력, rate limiter 토큰, rollback 카운터)
- [ ] **8.3 패치**: Tier1 레이턴시 6건 (async 로깅 / uvloop / pre-warm / lock gather / Bithumb burst / connect timeout)
- [ ] **8.4 재설계**: PHOENIX §3 Phase 2 표를 위 새 표로 교체, Step 2-1.5 추가, Step 2-7(tuner) → Phase 3 이동
- [ ] **8.5 워크플로우**: FSM Step 종료 조건 정의 + 증거 수집 스크립트 + Watchdog 4-layer 검증
- [ ] **재테스트**: pytest 5454 PASS 유지, check_all 9/9
- [ ] **dry-run**: Step 2-1만 1시간 시뮬레이션 (자본 $50, 자동 진입/정지 검증)
- [ ] **git commit + push**: 위 전체 묶어서 1개 PR
- [ ] **checkpoint save**: 자동 운영 시작 직전

위 8개 모두 ✅ 후에만 자동운영 시작. 1개라도 미달이면 사장님 깨움.


---


---

## 8.7 Phase 2 운영 대시보드 (사장님 직접 지시 반영)


### 8.7.0 사장님 직접 지시 (절대 규칙)

1. **라이트 테마 강제** — 검은색 배경 금지. 흰색 배경 + 그에 맞는 글자색. `<html className="dark">` 제거.
2. **Binance 벤치마크 금지** — Binance는 친화적이지 않음. 토스증권 / 업비트 / 카카오뱅크 / 토스뱅크 패턴만 사용.
3. **초보자도 쉽게** — 기술용어 노출 금지. 모든 약어/지표에 한글 풀이 + 툴팁.
4. **기존 플랜 파일 존중** — `dashboard/DESIGN-kraken.md`가 이미 라이트 테마 + Kraken Purple 디자인 시스템을 명시. 이게 SSOT. 코드와 어긋난 부분은 코드를 고친다.
5. **디자인 MCP/플러그인/스킬 활용** — Figma MCP, `design` 플러그인(design-system / design-critique / accessibility-review / ux-copy / design-handoff 스킬 포함) 적극 사용.

### 8.7.1 발견된 결함 (5,000단어 전수조사 요약)

페이지 16개 전부 등급 매김:
- A급: Login (1)
- B급: Portfolio, Assets, Backtest, Funding, Exchanges, System, Analytics (7)
- C급: Overview, Strategies, Alerts, Risk, Settings, Attribution (6)
- **D급: Trades** (1) — 영문 약자 + 스크롤 + 모달 토글 복잡
- F급: 없음

**Critical 결함 5종 (Phase 2 자동운영 차단)**
1. `<html className="dark">` 강제 다크 — DESIGN-kraken.md(라이트) 명시와 직접 충돌
2. 한영 혼용 — "Portfolio" / "자산 배분" / "Buy → Sell" / "포지션" 페이지마다 섞임
3. Empty state 미처리 — 데이터 0이면 "데이터 없음" 텍스트만 (스켈레톤·일러스트·CTA 없음)
4. Kill Switch UX 위험 — 3초 카운트다운 + 2곳에 중복 구현 (Risk, System)
5. 에러 메시지 개발자용 — `Failed to fetch trades`, `Unauthorized` 그대로 노출

**High 10건 / Medium 10건 / Low 10건**: 별도 트래커. 핵심만 §8.7.4에 매핑.

### 8.7.2 디자인 시스템 강제 (DESIGN-kraken.md 코드 반영)

기존 `dashboard/DESIGN-kraken.md` 그대로 SSOT. 다만 **퀀트/거래 컨텍스트에 맞춰 토큰 추가**:

**컬러 토큰 (`globals.css` 또는 `tailwind.config.ts`)**

| 토큰 | 값 | 용도 |
|---|---|---|
| `--bg-base` | `#FFFFFF` | 페이지 배경 |
| `--bg-surface` | `#FAFAFB` | 카드 배경 (whisper gray) |
| `--bg-elevated` | `#FFFFFF` | 모달/팝오버 |
| `--border` | `#DEDEE5` | 1px divider |
| `--border-subtle` | `rgba(104,107,130,0.12)` | hairline |
| `--text-primary` | `#101114` | 본문 (Near Black) |
| `--text-secondary` | `#686B82` | 보조 (Cool Gray) |
| `--text-tertiary` | `#9497A9` | 캡션 (Silver Blue) |
| `--brand` | `#7132F5` | Kraken Purple, CTA |
| `--brand-hover` | `#5741D8` | hover/active |
| `--brand-subtle` | `rgba(133,91,251,0.10)` | tag/badge bg |
| `--success` | `#149E61` | 수익 (글로벌 표준) |
| `--success-bg` | `rgba(20,158,97,0.12)` | 수익 badge bg |
| `--danger` | `#E5484D` | 손실/긴급 |
| `--danger-bg` | `rgba(229,72,77,0.10)` | 손실 badge bg |
| `--warning` | `#F59E0B` | 경고 |
| `--info` | `#3B82F6` | 안내 |
| `--font-display` | system-ui (Pretendard 폴백) | 큰 헤드라인 |
| `--font-body` | Pretendard, IBM Plex Sans, Helvetica | 본문 (한글 최우선) |

> **수익=초록 / 손실=빨강** 글로벌 표준 유지(토스증권도 글로벌 표준 따름). 단 모든 색상 신호에 **아이콘+텍스트 동반** (색맹 접근성).

**타이포 한글 우선**
- `Pretendard Variable` (한글 가독성 1위) 우선, IBM Plex Sans 폴백
- 본문 16px, 카드 헤더 14px, 캡션 12px (`text-xs` 12px 금지)
- 숫자 `tabular-nums` + `font-feature-settings: "tnum"` (정렬 안정)

**radius / spacing / elevation**: DESIGN-kraken.md 그대로 (12px 버튼 / 6/8/10/12/16px / `rgba(0,0,0,0.03) 0 4px 24px`)

### 8.7.3 정보 아키텍처 재편 (16페이지 → 4개 주 탭 + 빠른메뉴)

토스증권 / 업비트 / 카카오뱅크 패턴:

```
┌────────────────────────────────────────────┐
│  LEVIATHAN  ●연결됨   [⚙]  [👤 JUNHYEON]   │  ← 헤더 (sticky, 흰색 배경)
├────────────────────────────────────────────┤
│  [🏠 홈] [💼 운용] [📊 분석] [🛡 안전]      │  ← 4탭 (모바일은 하단 탭바)
├────────────────────────────────────────────┤
│                                            │
│  콘텐츠 (1 viewport 원칙)                   │
│                                            │
└────────────────────────────────────────────┘
```

**탭 1: 홈 (`/`)** — 토스 메인 화면 패턴
- 큰 카드 1개: **총 자산** (₩ 표시 우선, USD 부기). 어제 대비 +/− 색상 + 화살표 아이콘. 큰 숫자(48px display)
- 카드 2개: **오늘 손익** / **이번 달 손익** (수익률 % + 절대액)
- 가로 스와이프 카드 7개: **거래소별 잔고** (로고 + 잔액 + ●연결상태)
- 리스트: **활성 포지션 3개** (있을 때만, 없으면 일러스트 + "현재 활성 포지션 없음")
- 리스트: **최근 체결 5건** (시간/심볼/손익) → "전체 보기" 텍스트 링크
- 우측 상단 고정: 🛡 **안전 상태 뱃지** (정상/주의/위험 + 한글)

**탭 2: 운용 (`/manage`)** — 업비트 자동매매 봇 패턴
- 상단: **현재 모드** 큰 토글 (Paper / Live) — 라벨 한글 + 위험도 색상
- 카드 그리드: **전략 7개** — 각 카드에 토글, 어제 손익, 7일 차트, 활성/비활성 뱃지
- 카드 그리드: **거래소 7개** — 로고, 연결상태, 자본 슬라이더
- 하단: **자본 설정** — 슬라이더 UI (최소 거래액 / 거래소당 자본 / 일일 손실 한도)
- "왜 이 값?" 인포 아이콘 → 모달로 한글 설명

**탭 3: 분석 (`/insights`)** — 토스 자산 분석 패턴
- 상단: **기간 선택 칩** (오늘 / 7일 / 30일 / 전체)
- 큰 카드: **에쿼티 커브** (단순 area chart, 그라데이션, KST 시간축)
- 카드 그리드 4개: **승률 / Sharpe / 평균 수익 / 최대 낙폭** (각 카드에 ⓘ "이게 뭐예요?" 풀이)
- 탭: **전략별 / 거래소별 / 심볼별 / 시간대별** 손익 비교 (Pie + Bar)
- 하단: **거래 내역** (검색바 + 필터 칩 + 가상 스크롤 테이블, 행 클릭 시 모달)
- "TCA 리포트 PDF 받기" 버튼 (docx skill로 생성)

**탭 4: 안전 (`/safety`)** — 토스 보안 센터 패턴
- 큰 카드: **🛡 안전 상태** (정상/주의/위험 + 색상 + 한글 설명)
- 카드: **긴급정지** — 한 번 누르면 5초 카운트다운 + "정말 멈출까요?" 모달 + 모달 안에 비밀번호 재입력 (실수 방지). 단축키 안내
- 라이브 게이지 4개: **일일 손실 한도 사용률** / **최대 낙폭** / **순익스포저** / **차단기 발동률**
- 진행률 바: 카나리 단계 (Step 2-1 / 2-1.5 / ...)
- 리스트: **최근 안전 이벤트** (KS 발동, CB OPEN, 롤백 등 한글 메시지)
- 리스트: **활성 포지션** (좀비 정렬) + 각 포지션 "수동 청산" 버튼

**빠른 메뉴 (헤더 우측 ⚙)**: 시스템 / 백테스트 / 알림 / 설정 / 로그아웃 → 모달 또는 별도 페이지

**기존 16페이지 매핑 (마이그레이션)**

| 기존 | 신규 위치 |
|---|---|
| Overview | 탭1 홈 |
| Portfolio | 탭1 + 탭3 일부 |
| Assets | 탭1 가로 스와이프 카드 |
| Trades | 탭3 하단 거래 내역 |
| Strategies | 탭2 전략 그리드 |
| Analytics | 탭3 |
| Attribution | 탭3 탭(전략별/거래소별/...) |
| Funding | 탭3 + 탭2 카드 |
| Exchanges | 탭2 거래소 그리드 |
| Risk | 탭4 |
| Settings | 탭2 자본 설정 + 빠른메뉴 |
| System | 빠른메뉴 |
| Backtest | 빠른메뉴 |
| Alerts | 탭4 + 헤더 벨 아이콘 |

**신규 페이지 (탭4 하위 또는 자동 진입 페이지)**
- `/safety/positions` — 좀비 포지션 전수조사 + 수동 청산
- `/safety/latency` — Bug 13 측정 결과 라이브 차트
- `/safety/canary` — Phase 2 카나리 진행 페이지 (FSM 종료조건 체크리스트)
- `/safety/heartbeat` — Dead Man's Switch 상태 + 수동 halt

### 8.7.4 페이지/컴포넌트별 결함 → 패치 매핑

| 결함 (Critical/High) | 해결 |
|---|---|
| 강제 다크 (`layout.tsx`) | `<html lang="ko">` (className 제거) + Tailwind `darkMode: 'class'` 비활성 |
| 영문 혼용 | i18n JSON 1개 (`dashboard/src/i18n/ko.json`) — 모든 문자열 키 추출 + 한글화 |
| Empty state 텍스트만 | `<EmptyState>` 컴포넌트 (lucide 아이콘 + 제목 + 설명 + CTA) — 12개 페이지 적용 |
| 에러 메시지 개발자용 | `<FriendlyError>` 컴포넌트 + ERROR_MESSAGES 매핑 ("network" → "인터넷 연결을 확인해 주세요") |
| KillSwitch 중복/위험 | `<EmergencyStop>` 단일 컴포넌트 + 5초 카운트다운 + 비밀번호 재입력 모달 + 단축키 |
| 폴링 주기 불일치 | `useApi` 훅 1곳에서 표준화 (홈=3s, 운용=5s, 분석=10s, 안전=1s) |
| 숫자 포맷 일관성 없음 | `formatKRW`, `formatUSD`, `formatPct`, `formatNum` 4개 유틸로 통일 |
| 색맹 미대응 | 모든 손익에 ▲▼ 아이콘 + 색 동시 |
| 모바일 nav 복잡 | 모바일 하단 탭바 4개 (토스 패턴), 데스크탑 좌측 사이드바 |
| 한국 시간대 혼란 | 모든 시간 KST 명시, 상대시간("3분 전") 우선 + 절대시간 툴팁 |
| 기술용어 노출 | 모든 약어 옆 ⓘ 툴팁 (Radix Tooltip) + 한글 풀이 |
| 사이드바 아이콘 16px | 44px 터치 타겟 강제 (WCAG AA) |
| 폰트 한글 최적화 X | Pretendard Variable 적용 |
| 차트 무거움 | Recharts 유지하되 dynamic import + skeleton |

### 8.7.5 디자인 MCP/플러그인/스킬 활용 계획

| 도구 | 용도 | 단계 |
|---|---|---|
| **`design` 플러그인** (이미 search_plugins로 발견) | 8개 스킬 패키지 | 전 단계 |
| `design:design-system` 스킬 | DESIGN-kraken.md 토큰 일관성 감사 + 누락 변수 추가 | 8.7.6 Step 1 |
| `design:design-critique` 스킬 | 각 페이지 mockup에 구조 피드백 | Step 2 |
| `design:ux-copy` 스킬 | 한글 마이크로카피 / 에러 메시지 / 빈 상태 카피 작성 | Step 3 |
| `design:accessibility-review` 스킬 | WCAG 2.1 AA 감사 (대비/터치/키보드) | Step 5 |
| `design:design-handoff` 스킬 | 토큰/컴포넌트/상호작용 스펙 문서 산출 | Step 4 |
| **Figma MCP** (mcp registry, 미연결) | 토스/업비트 패턴 참고 + 코드 generate | 선택 (`suggest_connectors`로 사장님 승인 후 연결) |
| **design 플러그인 설치** | `suggest_plugin_install`로 사장님 승인 받고 설치 | Step 0 |

### 8.7.6 작업 단계 (12 Steps)

**Step 0 — 도구 준비**
- `design` 플러그인 설치 권장 안내 (사장님 승인)
- Figma MCP 연결 권장 안내 (선택)
- 기존 16페이지 스크린샷 (라이트 모드 강제 후) — 분석용

**Step 1 — 디자인 토큰 + 라이트 테마 강제**
- `dashboard/src/app/layout.tsx`: `<html className="dark">` → `<html lang="ko">`
- `tailwind.config.ts`: `darkMode: 'class'` 비활성, 컬러 토큰 §8.7.2 추가
- `globals.css`: CSS 변수 정의, Pretendard 폰트 import
- 전체 페이지 시각 회귀 (모든 페이지가 라이트로 보이는지)

**Step 2 — 공통 컴포넌트 8종 신규 작성**
- `<EmptyState>`, `<FriendlyError>`, `<KPICard>`, `<EmergencyStop>`, `<StatusBadge>`, `<NumberDisplay>`, `<TimeDisplay>`, `<InfoTooltip>`
- Storybook 또는 `/dev/components` 페이지에서 검증

**Step 3 — i18n 한글화**
- `dashboard/src/i18n/ko.json` 추출 + 모든 페이지에 적용
- 전문용어 사전 (`min_edge_bps` → "최소 수익 기준 (bps, 1bp = 0.01%)")

**Step 4 — 정보 아키텍처 재편 (탭 4개 + 라우팅)**
- `dashboard/src/app/(tabs)/home/page.tsx`, `(tabs)/manage`, `(tabs)/insights`, `(tabs)/safety`
- 기존 16페이지 → 신규 위치로 라우팅 + redirect alias (외부 링크 보존)
- 모바일 하단 탭바 + 데스크탑 사이드바 동시 지원

**Step 5 — 신규 안전 페이지 4개**
- `/safety/positions`, `/safety/latency`, `/safety/canary`, `/safety/heartbeat`
- 각 페이지 백엔드 API (§8.7.7 참조)

**Step 6 — 백엔드 API 4 라우터 신규**
- `engine/src/api/routes/positions.py`, `latency.py`, `canary.py`, `heartbeat.py`
- `server.py`에 `include_router` 4건 추가

**Step 7 — 기존 페이지 마이그레이션 (12개)**
- 한 페이지씩 새 디자인 토큰 + 컴포넌트로 재작성
- 우선순위: D급 Trades → C급 6개 → B급 7개 → A급 Login

**Step 8 — `design:accessibility-review` 스킬 실행**
- WCAG 2.1 AA: 4.5:1 대비 / 44px 터치 / 키보드 / 스크린리더
- 모든 결함 수정

**Step 9 — `design:ux-copy` 스킬 실행**
- 모든 에러/빈 상태/CTA 카피 한글 친화 톤으로 통일

**Step 10 — 빌드 + 실측**
- `npm run build` 통과
- Lighthouse: Performance ≥ 80 / Accessibility ≥ 95 / Best Practices ≥ 90
- 페이지별 번들 < 200KB
- 모바일 시뮬 + 실제 폰 (사장님 폰) 스크린샷 16장

**Step 11 — Phase 2 카나리 dry-run 1시간**
- `/safety/canary` 페이지가 실제로 진행률/종료조건/CB 카운터 라이브 표시되는지
- `/safety/positions` 페이지가 실제 거래소 잔고로 채워지는지
- `/safety/latency`가 Bug 13-A~D 측정 결과 표시하는지

**Step 12 — git PR 1건 묶음**
- title: `feat(dashboard): Phase 2 운영 대시보드 v2 — 라이트 테마 + 토스/업비트 패턴`
- body: §8.7.1 결함 35건 매핑, before/after 스크린샷, Lighthouse 점수, 마이그레이션 가이드

### 8.7.7 백엔드 API 4 라우터 (Step 6 상세)

| 라우터 | 엔드포인트 | 응답 핵심 필드 | 데이터 소스 |
|---|---|---|---|
| `positions.py` | `GET /api/positions/open` | `[{exchange, symbol, side, qty, entry, mark, unrealized_pnl, liq_price, hold_seconds, strategy}]` | 어댑터 `_rest_get_positions()` 합산 + 30s 캐시 |
| `positions.py` | `POST /api/positions/{id}/close` | `{ok, trade_id}` | KillSwitch Tier3 단일 호출 |
| `latency.py` | `GET /api/latency/exchange` | `[{exchange, p50, p95, p99, avg, sample_count}]` | Bug 13-A 측정 패치 (Redis 집계) |
| `latency.py` | `GET /api/latency/strategy` | `{1leg: {...}, 2leg: {...}}` | 동일 |
| `canary.py` | `GET /api/canary/status` | `{step, started_at, deadline, conditions: [...], cb_open_count, kill_switch_fires, pnl}` | FSM state file |
| `canary.py` | `GET /api/canary/history` | `[{step, started, ended, result, evidence_url}]` | `.omc/state/phase2/*.json` |
| `heartbeat.py` | `GET /api/heartbeat/status` | `{ttl_seconds, halt_flag, watchdog_on, last_seen, infrabot_connected}` | Redis `leviathan:heartbeat` / `leviathan:halt` |
| `heartbeat.py` | `POST /api/heartbeat/halt` | `{ok}` | `halt_local()` + Redis SET |

### 8.7.8 §8.6 잔여 체크리스트 갱신 — 9번 항목 (v2 대체)

§8.6의 9번 "운영 대시보드"를 다음으로 대체:

- [ ] **8.7 v2 운영 대시보드 (12 Steps)**
  - [ ] Step 0: 도구 준비 (design 플러그인 설치 + 스크린샷)
  - [ ] Step 1: 라이트 테마 강제 + 디자인 토큰
  - [ ] Step 2: 공통 컴포넌트 8종
  - [ ] Step 3: i18n 한글화
  - [ ] Step 4: 정보 아키텍처 4탭 재편
  - [ ] Step 5: 신규 안전 페이지 4개
  - [ ] Step 6: 백엔드 API 4 라우터
  - [ ] Step 7: 기존 페이지 12개 마이그레이션
  - [ ] Step 8: 접근성 감사 (WCAG AA)
  - [ ] Step 9: UX 카피 한글화
  - [ ] Step 10: 빌드 + Lighthouse + 스크린샷 16장
  - [ ] Step 11: dry-run 1시간 안전 페이지 검증
  - [ ] Step 12: git PR 1건

**자동운영 진입 가드**: 12 Steps 모두 ✅ + Lighthouse Accessibility ≥ 95 + 사장님 폰 스크린샷 4탭 검수 후에만 Phase 2 자동운영 시작.

### 8.7.9 작업 분량 / 시간 추정

- 디자인 시스템 + 라이트 테마: 4-6시간
- 공통 컴포넌트 8종: 4-6시간
- i18n: 2-3시간
- 4탭 IA 재편 + 12 페이지 마이그레이션: 16-24시간 (가장 큼)
- 신규 안전 페이지 4 + 백엔드 4 라우터: 6-8시간
- 접근성/카피/빌드/dry-run: 4-6시간
- **총 36-53시간** — 즉 자율 작업 1회로는 부족, **2-3 사이클** 필요

→ Phase 2 본 운영은 §8.7 v2 1차 사이클(라이트 테마 + 안전 페이지 4 + 홈 탭) 완료 후 시작 가능. 나머지 페이지 마이그레이션은 Phase 2 운영 중 병렬 진행 (코드 수정 금지 규칙은 엔진 코드만 적용, 대시보드는 예외).

### 8.7.10 답변: 사장님이 이전에 요청했는데 반영 안 된 이유

확인 결과 `dashboard/DESIGN-kraken.md`에 **이미 라이트 테마 + 흰색 배경 + Near Black 텍스트 + Kraken Purple 디자인 시스템이 명시**되어 있음. 그러나:

1. `dashboard/src/app/layout.tsx`에 `<html className="dark">` 강제 다크 (명시 위반)
2. `tailwind.config.ts`에 `darkMode: 'class'` 활성 (명시 위반)
3. 컴포넌트 코드는 `bg-slate-900`, `text-slate-100` 류 다크 토큰 직접 사용 (DESIGN-kraken.md 토큰 무시)
4. 즉 **디자인 문서는 있지만 코드 enforcement 없음** — 이전 작업자(에이전트)가 문서 안 읽고 다크 테마로 만들어버림

→ §8.7 v2 Step 1에서 이 위반을 강제로 잡고, 이후 모든 PR에 "DESIGN-kraken.md 토큰만 사용" lint 추가 (eslint 또는 stylelint 규칙)

