# PHOENIX v3 — 카나리 실행 계획 (단일 SSOT)

> 400줄 이내. §1~6 = 영구 계획. §7 = 카나리 이력 요약 테이블.  
> 최종 수정: 2026-04-13 (v94 BUG-76 수정 기준 — adaptive exit threshold)

---

## §1. 목적 + Phase 개요

**목표**: 7거래소 × 4전략 실전 검증 후 풀 운영 전환.

| Phase | 내용 | 상태 |
|-------|------|------|
| 0 | 배관 준비 (모드분리, 자본%, 심볼제외) | ✅ 완료 |
| 1 | 배관 뚫기 (첫 Live 체결) | ✅ 완료 (live20, FR 1건) |
| 2 | 카나리 단계 확장 | 🔄 진행중 (Step 2-2) |
| 3 | 풀 통합 72H + 튜너 | ⏳ 대기 |

**현재**: Phase 2 Step 2-2 — FF(45bps) + FR(5bps) 동시 운영 (카나리 재개 대기)  
**설정**: max_hold=1800s, edge=10bps, min_spread=45bps (v108 수익 설정)  
**구조 리팩토링 v2/v3**: WS-1/2/3 + BUG-93/94/95/96/97/98 완료  
**70+ commits** (v94~v145), 29 bugs fixed (BUG-73~98)  
**v141~v145 인프라 튜닝**: Redis client(retry/keepalive/timeout 5s/pool 100), Freshness guard FF+SF 3s→5s, recovery.py native-adapter 호환 (duck-typing), ERROR→WARNING (transient timeout)

### 구조 리팩토링 결과 (2026-04-17, 5개 감사 → 4개 워크스트림 + BUG-93~96)
| WS / BUG | 내용 | 커밋 | 테스트 |
|----|------|------|--------|
| WS-1 | Config 단일화: trading.json leak 제거, Pydantic override 3개, 직접리더→get_config() | `3cfb65c` | 4,793 pass |
| WS-2 | Pipeline 분리: 4-콜백(handle_entry/exit_rollback/success) + clear_ghost, on_execution_rollback 호출 0건 | `3cfb65c` | 4,792 pass |
| WS-3 | Position 중앙화: PositionManager.open/close 실행 경로 연결, _position_sizes 롤백 누수 수정 | `1a5c80a` | 4,792 pass |
| BUG-93 | LiveMode position_manager param 누락 | `80df207` | 4,792 pass |
| BUG-94 | FF optimistic write 제거 (_pending_position_metadata two-phase) | `cb0312d` | 29 pass |
| BUG-95a | duplicate signal race + TTL reaper | `f85017d` | - |
| BUG-95b | **CRITICAL** Exit rollback ghost (Codex+Gemini+Opus 합의) | `c827423` | - |
| BUG-95c | **CRITICAL** on_fill eager cleanup → TTL reaper pending_exits | `0198ff7` | 21 pass |
| BUG-95d | handle_entry/exit_success dispatch | `5b4a788` | 173 pass |
| BUG-96 GAP#1 | margin guard clears _pending_position_metadata | `021cc45` | - |
| BUG-96 GAP#2 | **CRITICAL** defensive rollback + early return (phantom success 방지) | `fa1a37a`+`8fdca69` | 46 pass |
| BUG-96 GAP#3 | CancelledError + Exception handler | `28be30e` | 221 pass |
| BUG-96 HIGH | _notify_pre_exec_rollback 멱등성 guard | `5eaa0b8` | 46 pass |
| BUG-96 tests | 방어 경로 regression 커버리지 5개 | `4ed276f` | **5 new** |
| BUG-97 | recovery.py ccxt-only `fetch_position` → native adapter 호환 (duck-type + CLOSE skip) | v142/v143 | 20 pass |
| BUG-98 | SF freshness guard 3s→5s (FF 일치, 저유동성 SF 지원) | v145 | — |
| BUG-100 | **CRITICAL** SF `all_books` 파라미터 구조 오류 (live.py:1002) — pre-indexed dict 전달 → spot_books 영구 빈값 → SF 시그널 0건 (5분 v145 가동 중 0) | v146 | — |
| BUG-101 | `get_lot_step` 2-leg sequential → parallel (asyncio.gather) — cross-exchange 마이너 latency 감소 | v147 | — |
| BUG-103 | `dict changed size during iteration` — BUG-100 fix 후 race condition 발생, shallow copy로 해결 | v147 | — |
| BUG-103.2 | Inner dict race (SF/FF) — producer 내 `dict(all_books.get(symbol, {}))` inner snapshot 추가 | v148 | 13 pass |
| BUG-103.3 | Inner race 확장 (FR/LatencyArb/XE-KRW) — 모든 evaluator inner snapshot 적용 | v149 | 13 pass |
| **BUG-104** | SF fut_ex에 futures 필터 없음 → spot-spot 잘못된 basis signal 생성 | v150 | 13 pass |
| **BUG-106** | XE-KRW backtest-only gate → live mode KRW signal 0건 | v151 | - |
| **BUG-107** | cross_exchange_krw strategy_id 불일치 → signal silent drop | v152 | - |
| **BUG-108** | FX rate 하드코딩 `Decimal("0.000714")` → engine.json config | v153 | - |
| **BUG-109** | risk_rejected 로그에 symbol/legs 컨텍스트 누락 | v154 | 24 pass |
| **BUG-110** | SF config None fallback → default max_position_size=50000 (notional $100 > max $12.60 리젝) | v155 | - |
| **BUG-112** | FX rate config 수동 업데이트 → **KRWRateProvider live oracle** (Upbit 30s poll + 60s stale fallback) | v157 | 37 pass |
| **Step 2-3** | XE-KRW staged activation: engine.json xe_krw_enabled=false (default). USDT XE only per PHOENIX §2. Gemini+Codex review PASS. | v157 | — |
| **BUG-97.2** | Native Position field `size` (not `quantity`) — reconciler exchange_qty=0 false alarm | v159 | 9 pass |
| **BUG-97.4** | Startup reconciliation hard-halt → warn (continuous reconciler 60s authoritative) | v160 | 9 pass |
| **BUG-113** | HTTP keepalive_connections 10→20 + expiry 120s — Bitget fresh TCP 1000ms → keepalive hit 500ms | v161 | — |
| **BUG-114** | DualWriter PG timeout 100→500ms — v161 FR trades rejected by timeout (TimescaleDB hypertable 200-300ms typical) | v162 | — |
| **v160 실거래 증명** | FF THETA/WLD 체결 성공, FR WLD 2건, **FF pnl=+$0.0289** (세션 첫 수익), Bitget latency bimodal 확인 | v160 | — |
| **v162 안정** | PG timeout=0, trade_executed 2건 FR, FX oracle live (USDT/KRW=1479), reconciliation PASS | v162 | — |
| **레이턴시 물리 한계** | Bitget REST 500-1000ms (거래소 인프라), 한국 노트북→싱가포르. VPS Tokyo 이전 시 200-400ms 가능. | — | — |
| **v146 실증** | BUG-100 fix 후 **3분 SF 11,087 signals** (이전 v145 0건) → Step 2-1.5 활성 확인 | v146 | 175 exec tests |
| Redis client | retry_on_timeout, health_check_interval=30, socket_keepalive, pool=100, transient ERR→WARNING | v141~ | 20 pass |
| FF freshness | 3s→5s (fresh_drop 70%→35% 증명, v143) | v143 | — |

근거: Opus 감사 에이전트 5개 (Config/Position/Pipeline/Execute_callback/Next_improvement) + Opus Critic/code-reviewer 리뷰 + Codex + Gemini 멀티모델 합의 + /ultrareview 8건 피드백 + v131→v137 실증 측정

### v137 실증 결과 (9분+ 가동)
- **Reaped (orphan)**: v131 **38건/41min → v137 0건** (BUG-96 GAP#1 효과)
- **PreexecClear 11건**: margin guard → clear_pending_entry 실시간 작동 확인
- **Ghost/ERR/CancelledError/ExecInvalid**: 모두 0
- **체결**: FR VANA/USDT 1건, PositionManager 2 legs 기록 (WS-3 효과)

---

## §2. 카나리 방법론 (확정)

### 원칙: 배관 검증 vs 전략 결과 기반

| 구분 | 목적 | 기간 | 성공 조건 |
|------|------|------|----------|
| 배관 검증 | 신전략 배선 확인 (신호→체결→정산) | 1H | crash=0, 체결≥1 (손실 허용) |
| 전략 검증 | 수익 조건 충족 시 자동 체결, 결과 누적 | 조건 기반 | crash=0, PnL>0 (3사이클 이상) |

**핵심**: BPS 임계값 = ON/OFF 게이트. 수익 조건에서만 체결 → 임계값 충족 전까지 손실 0.  
**24H 고정 기간 = 비효율**: 구조적으로 손실이 확정된 전략에 24H 소비 금지.

### 전략별 검증 완료 조건

| 전략 | 완료 조건 | 현재 |
|------|----------|------|
| futures_futures | spread_exit 3회 + crash=0 + PnL≥0 | spread_exit 0/3 (BUG-76 즉시청산 artifacts, v95에서 재검증) |
| funding_rate | 결산 3회 (UTC 0/8/16) + PnL>0 | 배관 완료, 기회 대기중 (<2bps) |
| spot_futures | 체결 5건 + PnL≥0 | Step 2-1.5 대기 |
| cross_exchange | 체결 1건 + kimchi premium 확인 | Step 2-3 대기 |

### Step 2-2 게이트 조건 (현재 스텝)

- crash=0, KillSwitch=0, CB OPEN < 5
- 손실 tier: $6 (총자본 $120의 5%)
- FF spread_exit 3회 확인 OR 시장 27bps+ 도달
- FR 결산 3회 (2026-04-13 16:00 UTC 기준 3번째)
- 완료 후 → Step 2-1.5로 **역행** (spot_futures 추가)

### Phase 2 전체 순서

```
Step 2-1:   FF 단독 (완료 — v85~v94)
Step 2-1.5: FF + SF  ← 다음
Step 2-2:   FF + FR  (현재)
Step 2-3:   + XE Bin↔Bitget (글로벌, KRW 제외)
Step 2-4:   + CE Coinone
Step 2-5:   + CE Upbit (키 갱신 필요)
Step 2-6:   + CE Bithumb
Step 2-8:   전체 72H 통합
```

---

## §3. 전략별 현황 + 임계값

### 활성 전략 (현재 v94)

#### futures_futures (FF)
- **임계값**: `min_spread_bps = 27` (engine.json + strategy_params.json)
- **계산 근거**: 왕복 수수료 22bps (Binance5+Bitget6=11bps × 2) + exit_threshold 4.5bps = 26.5 → 27
- **현재 시장**: 10~17bps (< 27bps → 자동 거절, 손실 없음)
- **배관**: v85~v93에서 완전 검증. 체결 200건+, rollback 경로 확인
- **다음 체결 조건**: 시장 스프레드 ≥ 27bps (주로 급변동 시간대)
- **미검증**: spread_exit 경로 (조기청산, 시장 조건 의존)

#### funding_rate (FR)
- **임계값**: `funding_min_diff_bps = 2.0` (engine.json), `min_funding_rate_bps = 6.55` (strategy_params)
- **현재 시장**: Binance↔Bitget 최대 1.71bps (ADA) — 전 심볼 2bps 미달
- **배관**: FundingRateCollector 462심볼 폴링 정상 (66초 주기)
- **다음 체결 조건**: diff ≥ 2bps (결산 직전 1~2시간에 확대 가능)
- **결산 일정**: 매일 UTC 00:00 / 08:00 / 16:00

### 비활성 전략 (Phase 2 순서대로 추가)

| 전략 | 상태 | 임계값 | 비고 |
|------|------|--------|------|
| spot_futures | DISABLED_PHASE2 | min_spread 12.39bps | Step 2-1.5 |
| cross_exchange | DISABLED_PHASE2 | min_spread 5bps | Step 2-3 |
| triangular | DISABLED_PHASE2 | — | Bithumb AuthCollector 미구현 |
| statistical_arb | DISABLED | — | 데이터 부족 |
| cex_dex | MONITOR | — | DEX 미연동 |

### 자본 배분 ($120 풀, 퍼센트 기반)

```
총자본: $120 (BinFut $20.45 + BitFut $33.00 + spot 잔고)
reserve_pct: 20%
funding_rate: 35% | futures_futures: 20% | spot_futures: 20% | cross_exchange: 25%
base_position_pct: 5% → 거래당 $6 (20x 레버리지 → 마진 $0.30)
min_trade_notional_usd: $5
```

### 거래소 구성

| 거래소 | Taker | 역할 |
|--------|-------|------|
| Binance Futures | 0.10% | FF + FR |
| Bitget Futures | 0.06% | FF + FR (BUG-20 수정) |
| Binance Spot | 0.10% | SF + XE |
| Bitget Spot | 0.10% | SF + XE |
| Upbit | 0.139% | CE (KRW) |
| Coinone | 0.02% | CE (KRW, API할인) |
| Bithumb | 0.25% | CE (KRW, stale guard) |

---

## §4. 카나리 이력 요약

| 버전 | 기간 | 전략 | Fills | PnL | 종료 사유 |
|------|------|------|-------|-----|----------|
| live20 | 2026-04-07 | FR | 1 | -$0.31 | Phase1 완료 |
| v1~v4 | 2026-04-08 | FF | 4 | — | Bug25a/b/c (same-ex check, rollback) |
| v5~v9 | 2026-04-09 | FF | 6 | — | BUG-A~G (자본$1.20, spread 150bps) |
| v10~v17 | 2026-04-09~11 | FF | 193 | -$1.90 | Bug28~32 수정 후 안정화 |
| v18~v84 | 2026-04-11~12 | FF | 200+ | 누적손실 | 레이턴시 3-4초, 체결빈도 이슈 |
| v85~v91 | 2026-04-12 | FF | 12 | -$0.04 | min_spread 재조정 과정 |
| v92 | 2026-04-13 | FF | 0 | $0.00 | min_spread=30bps → 시장없음 |
| v93 | 2026-04-13 | FF | 193 | -$1.17 | **BUG-73**: 15bps → 22bps 수수료 미반영 |
| **v94** | 2026-04-13 | FF+FR | 316 | -$0.84 | BUG-76 즉시청산 루프, FF API 비활성화 |
| v95~v97 | 2026-04-13~14 | FF+FR | 41 | -$0.04 | BUG-74~78 수정, 결산 cooldown 검증 |
| **v108** | 2026-04-14 | FF+FR | **59+2** | **+$0.26** | FF 8H 수익! edge 242건 방어 |
| v109~v115 | 2026-04-15 | FF+FR | varies | ~$0.00 | BUG-85~89 수정, FR 시그널 복구 |
| **v117** | 2026-04-15~ | FF+FR | 3+ | $0.00 | **구조 리팩토링 적용** (Phase 1+2) |
| v118~v140 | 2026-04-16~17 | FF+FR | 4 | ~0 | BUG-93~96 Ghost 구조 수정, 2만+ FR/FF signal |
| v141~v150 | 2026-04-17~18 | FF+FR+SF | ~10 | ~0 | **BUG-100 CRITICAL** (SF signal 0 → 11k/3min), Redis tuning, race fix |
| v151~v156 | 2026-04-18 | FF+FR+SF+XE-USDT | — | — | BUG-106/107/108/109/110 fix + XE activation |
| **v157** | 2026-04-18 | FF+FR+SF+XE-USDT | — | — | **FX oracle live** (KRWRateProvider Upbit 30s), Step 2-3 정렬 |
| v158~v159 | 2026-04-18 | FF+FR+SF+XE | — | — | BUG-97.2/97.4 recovery native compat |
| **v160** | 2026-04-18 | FF+FR+SF+XE | **3+** | **+$0.02** | **FF THETA +$0.0289 세션 첫 수익!** Bitget bimodal latency |
| v161 | 2026-04-18 | FF+FR+SF+XE | pending | — | BUG-113 HTTP keepalive tuning (expected 1000→500ms) |

> v94→v117: 34 commits, 20+ bugs (BUG-73~89), 구조 리팩토링 (Config 단일화 + FF exit 통합)
> 10/10 상용급 슬리피지 제어 구현 + 독립 검증 15/15 PASS
> v108: FF 8시간 +$0.26 수익 (첫 안정 수익 카나리)
> v117: Phase 1(Config)+Phase 2(Position) 반영, GATE 10/10 PASS

---

## §5. 버그 이력 (v117 기준, 전부 수정완료)

| ID | 설명 | 상태 |
|----|------|------|
| BUG-73 | FF entry_only=True → round-trip 수수료 50% 과소 | ✅ entry_only=False + min_spread 45bps |
| BUG-74 | margin guard 없음 → retry loop | ✅ $3 가드 |
| BUG-75 | max_hold_seconds config 캐시 | ✅ 재시작 시 해결 |
| BUG-76 | adaptive exit > min_spread → 즉시 청산 | ✅ static 4.05bps |
| BUG-77 | settlement race condition | ✅ 120s cooldown + 테스트 |
| BUG-78 | margin rollback → retry loop | ✅ rollback 제거 |
| BUG-79 | allocation_pct 미연결 | ✅ per-strategy sizing |
| BUG-80 | RiskGuardian 10파라미터 미연결 | ✅ 전파라미터 wiring |
| BUG-81 | DB health gate 없음 | ✅ live 시 SystemExit |
| BUG-82 | adapter qty desync | ✅ MIN_NOTIONAL ceil 제거 |
| BUG-83 | Pydantic exchanges default overrode engine.json | ✅ _active_exchanges |
| BUG-84 | Bitget WS 181 개별 구독 → disconnect | ✅ batch subscribe |
| BUG-85 | FR 이중 필터 (5/10bps) | ✅ 5bps 통일 |
| BUG-86 | slippage kill FF false halt | ✅ 100bps/20 window |
| BUG-87 | -2022 ReduceOnly ERROR 로그 | ✅ WARNING 다운그레이드 |
| BUG-88 | FR max_positions 무제한 → 마진 고갈 | ✅ max=2 |
| BUG-89 | slippage kill FR carry trade HALT | ✅ spread-arb only |
| BUG-A | risk config trading.json만 읽음 | ✅ engine.json merge |
| BUG-B | min_edge dead code | ✅ 10bps + orderbook recheck |
| BUG-C | Bitget fee 미추출 | ✅ poll/fill-history 추출 |
| BUG-90 | ROLLED_BACK slippage 누적 오류 | ✅ accumulator 제외 |
| BUG-91 | max_hold_seconds FF config 미전달 | ✅ main.py wiring |
| BUG-92 | ghost position exit 무한루프 | ✅ clear_ghost + 4-콜백 분리 (WS-2) |
| Phase1 | Config 3곳 분산 | ✅ engine.json 단일화 + trading.json 제거 (WS-1) |
| Phase2 | FF exit 이중경로 → ghost-clear | ✅ monitor 단일경로 |
| WS-1 | trading.json leak + Pydantic override 누락 | ✅ deep-merge 제거 + 3개 override 추가 |
| WS-2 | entry/exit rollback 의미 충돌 | ✅ 4-콜백 분리 + clear_ghost |
| WS-3 | PositionManager dead code + 롤백 누수 | ✅ 실행 경로 연결 + _position_sizes 수정 |
| BUG-93 | LiveMode position_manager param 누락 | ✅ main.py → live.py wire |
| BUG-94 | FF optimistic write (_pending_position_metadata two-phase) | ✅ ghost=0 설계 |
| BUG-95 (x4) | duplicate signal race, exit rollback ghost, on_fill eager cleanup, dispatch | ✅ clear_pending_entry + TTL reaper + 4-callback |
| BUG-96 (x4) | margin guard missing pre-exec clear, defensive rollback, CancelledError, idempotency | ✅ clear_pending_entry + early return + try/except + guard |
| **BUG-97** | recovery.py ccxt-only `fetch_position` → native adapter 호환 (duck-type + CLOSE skip) | ✅ v143 / 20 pass |
| **BUG-98** | SF freshness guard 3s→5s (FF 일치, 저유동성 SF 지원) | ✅ v145 |
| **BUG-100** | **CRITICAL** SF all_books 파라미터 구조 오류 (live.py:1002) | ✅ v146 / SF 11k signals 증명 |
| **BUG-101** | `get_lot_step` 2-leg sequential → parallel | ✅ v147 / ~200ms 절감 |
| **BUG-103** | dict changed size during iteration (outer shallow copy) | ✅ v147 |
| **BUG-103.2** | SF/FF inner dict race (snapshot) | ✅ v148 / 13 pass |
| **BUG-103.3** | FR/LatencyArb/XE inner race (모든 evaluator snapshot) | ✅ v149 / 13 pass |
| Redis tuning | retry_on_timeout + health_check_interval + socket_keepalive + pool=100 + timeout=5s | ✅ v141~ / 20 pass |
| Redis logs | transient xadd/xreadgroup ERROR→WARN (auto-recovery 있음) | ✅ v141~ |
| FF freshness | 3s→5s (fresh_drop 70%→35% 증명) | ✅ v143 |
| **BUG-114** | DualWriter PG timeout 100→500ms (TimescaleDB typical 200-300ms) | ✅ v162 |
| **BUG-116** | Edge recheck REST → WS in-memory book (AtomicExecutor.set_books_provider) | ✅ v163 |
| **BUG-120** | WebSocket order placement 전환 — Phase 1-7 완료 + v164 ACTIVATED. REST 350-1000ms → WS 100-300ms 목표. engine.json execution.ws_order_enabled=true. Fallback REST 정상 작동. | ✅ v164 활성화 |
| **BUG-121** | Bitget V2 futures WS — `marginMode` 파라미터 필수 (code=41101 'Param marginMode=null error') | ✅ v165 |
| **BUG-122** | Binance WS order response — MARKET 주문 avgPrice=0 → Trade 모델 validation fail. order.price fallback | ✅ v165 |
| **ccxt deprecation** | ccxt_adapter.py / okx.py / bybit.py / upbit.py / bithumb.py = dead code (runtime 사용 0). 문서화 완료. | ✅ v163+ |

### BUG-74 수정 (v95 자동 적용 — 코드 이미 완료)
```python
# live.py:181 — 클래스 상수
_MIN_MARGIN_ENTRY_USD: float = 3.0  # BUG-74

# live.py:117 — LiveModeStats 카운터
trades_margin_blocked: int = 0  # BUG-74

# live.py:1118-1131 — 가드 블록 (if not _is_close_req 먼저, 마진 루프 다음)
if not _is_close_req:
    for _leg in trade_request.legs:
        if _leg.exchange_id and "futures" in _leg.exchange_id:
            _cached = float(self._cached_margin.get(_leg.exchange_id, float("inf")))
            if _cached < self._MIN_MARGIN_ENTRY_USD:
                self._stats.trades_margin_blocked += 1
                return
```
> 크리틱 검토 결과(2026-04-13): ACCEPT. 클래스상수/카운터/순서 모두 정확. 스타트업 60s 공백은 safe-side (inf 기본값). 추가 수정 불필요.

---

## §6. 리스크 기준

```
KillSwitch 3-tier: 항상 활성 (bypass 불가)
  Tier1 (<1ms): halt 플래그 → 신규 주문 차단
  Tier2 (<500ms): 미체결 주문 취소
  Tier3 (<2000ms): 오픈 포지션 시장가 청산

CircuitBreaker: CLOSED→OPEN→HALF_OPEN (300s cooldown)
RiskGuardian: 11-check 매 거래 전 자동

손실 한도:
  단일 전략 > 5% → 전략 비활성
  총 손실 > 10% → KillSwitch
  5회 동일 문제 → 텔레그램 L2 에스컬레이션

Graceful shutdown: SIGTERM → 30s timeout → 미체결취소 → 포지션청산
close_positions.py: crash/SIGKILL 이후 fallback 전용 (정상 경로 아님)
```

**운영 금지 사항**:
- 카나리 실행 중 코드 수정 (모니터링만)
- 치명 버그 시 → graceful shutdown → Fix → 재시작
- Paper/Live 동시 실행 (Redis 글로벌 키 충돌)

---

## §7. 아키텍처 핵심

### 실행 파이프라인
```
WS Orderbook → SignalGenerator + RealSignalProducer
             → DeduplicationGate (asyncio.Lock per symbol+exchange_pair)
             → RiskGuardian 11-check
             → MarginTracker.reserve()
             → AtomicExecutor
             → 성공: MarginTracker.release()
             → 실패: StrandedPositionTracker (total>$30 시만 HALT)
```

### 멀티전략 동시 운영
- 자본: $120 공유 풀 (전략별 % 할당)
- 동시 체결: 글로벌 semaphore max=2 (선착순, 우선순위 없음)
- 신호 랭킹: Phase 3 기능 (현재 없음)
- **collision_key 버그**: strategy_id 미포함 → P1 수정 대기

### 모드
| Mode | Executor | 용도 |
|------|----------|------|
| live | AtomicExecutor | 실거래 |
| paper | PaperExecutor (BookWalkSlippage) | 시뮬 |
| backtest | SimExecutor | 과거 리플레이 |

> shadow 모드 없음. "shadow" 언급 금지.

### 설정 소스 (WS-1 단일화 완료)
- `engine/.env`: API 키/시크릿 전용
- `engine/config/engine.json`: 모든 운영 설정 — `get_config()` 또는 `load_engine_config()` 경유
- `engine/config/strategy_params.json`: 전략별 임계값
- `engine/config/strategy_activation.json`: 활성/비활성 목록
- ~~`engine/config/trading.json`~~: **제거됨** (deep-merge leak 방지, WS-1)

### 포지션 추적 (WS-3 중앙화)
- **PositionManager**: open/close 실행 결과에서 호출 (in-memory, dual_writer 선택적)
- **_position_sizes**: RiskGuardian 전용 (롤백 시 감소 — WS-3.3)
- **전략 _open_positions**: 전략 로컬 추적 (PositionManager와 병행, 점진적 통합)
- **Exchange get_positions()**: 유일한 진실 소스 (ghost check용)

### 실행 콜백 (WS-2 분리 완료)
- `handle_entry_rollback(sym)`: 진입 실패 → 추적 삭제
- `handle_exit_rollback(sym)`: 청산 실패 → 추적 복원
- `handle_entry_success(sym)` / `handle_exit_success(sym)`: 성공 정리
- `clear_ghost(sym)`: 거래소에 포지션 없음 → 모든 추적 강제 삭제
- ~~`on_execution_rollback`~~: legacy (내부 위임만, 직접 호출 0건)

---

## §8. 다음 실행 순서

### v95 실행 중 (2026-04-13 17:42 KST~)
- **PID**: 39440 | **FF+FR 동시 활성**
- **FR**: ID/USDT 1건 체결 ($0.09), 0G/USDT 시도 → BUG-74 margin guard 차단 ($2.55 < $3.00)
- **FF**: 27bps 미달 정상 대기 (시장 10.47bps)
- **v95 결과 (7H)**: ERR=90(-2019 margin), Trades=41, FR 4포지션 보유→결산, crash=0, KS=0
- **v95 검증**: BUG-74 ✅ BUG-75 ✅ BUG-76 ✅ **BUG-77 결산 cooldown 실전 확인** ✅
- **v97 (현재)**: BUG-74~80 + BUG-A + P0 config wiring 전수조사 수정 반영
- **config 감사**: 4개 에이전트 병렬, ~100개 이슈 발견, P0 수정 완료
- **수정 반영**: BUG-73(27bps), BUG-74(margin guard), BUG-75(300s), BUG-76(4.05bps), BUG-77(120s cooldown)
- **다음 검증**: UTC 16:00 (KST 01:00) FR 결산 → BUG-77 cooldown 실전 테스트

### v95+ 운영 원칙 (대기 시간 최소화)
- **버그 발견 즉시**: close_positions.py --execute → SIGTERM → fix → 재시작
- **FR 결산 대기 금지**: 결산 주기(8H)를 재시작 조건으로 쓰지 않음
- **빠른 반복**: vN 종료 → git commit+push → vN+1 시작 (결산 무관)
- **완료 조건**: crash=0 + KS=0 + FF spread_exit 3회 + FR 결산 1회 이상 + PnL≥0

### 운영 프롬프트 (자율 모드)
```
PHOENIX_PLAN.md §1~8 읽어. 이 문서가 유일한 실행 기준.

규칙:
1. §5 버그 최우선 (P0→P1 순서)
2. collision_key 수정 → pytest → v95 준비
3. 체결/크래시/KS 모니터링 — 문제 시 graceful shutdown → Fix → 재시작
4. 카나리 vN 종료 시 반드시 git commit + push + checkpoint save
5. PHOENIX_PLAN.md에 없는 작업 금지

금지: shadow 언급, TeamCreate, 증거없는 완료선언, scope 확장
보고: [vN] ✅/❌ + 로그증거 + 다음조치
```
