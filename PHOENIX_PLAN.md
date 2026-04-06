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

### Phase 1: 배관 뚫기 — Live 체결 1건

**Step 1-1: 7 어댑터 배선 (Paper 5분)**
- [ ] 7개 WS 연결 확인 (각 거래소 "connected" 로그)
- [ ] funding_rate 시그널 ≥1, crash=0
- [ ] KillSwitch OFF, CB CLOSED, Guardian all-PASS (로그)
- [ ] Bithumb stale guard 정상 (±50% 오탐 없음)
- [ ] Paper 모드에서 Telegram Trade봇 알림 0건 확인 (모드 게이팅 검증)
- [ ] DB 쿼리 검증: `SELECT mode FROM execution_log LIMIT 5` → 전부 'paper'

**Step 1-2: Paper 1시간 안정성**
- [ ] timeout 3600 실행, crash=0, 무중단 ≥60분
- [ ] funding_rate trade ≥1, futures_futures signal_evaluated ≥1
- [ ] Coinone BTC 시그널 = 0건 (심볼 제외 재확인)
- [ ] 거래소 Health ≥95% (7개 모두)
- [ ] PaperExecutor 수수료 반영 확인: trade 로그에 fee>0 (fee_rate=0 수정 검증)
- [ ] Graceful shutdown 테스트: Ctrl+C → 30초 이내 정상 종료 확인

**Step 1-2b: 리스크 시나리오 검증 (Paper에서)**
- [ ] Guardian warmup: 엔진 시작 후 120초간 exchange health check 우회 → 120초 후 정상 작동 확인
- [ ] 단일 거래 크기 제한: max_position_pct=5% 초과 주문 시도 → Guardian #6 차단 로그
- [ ] KillSwitch 수동: Telegram `/kill` → halt_flag=True 확인 → `/resume` → 복귀 확인
- [ ] CircuitBreaker 상태: CLOSED 유지 확인 (인위적 트리거 불필요, 상태 로그만)

**Step 1-3: Preflight + 첫 Live**
- [ ] Preflight 통과 (TimescaleDB, Redis, 7거래소, API키, 잔고, KS, CB, Telegram)
- [ ] .env: EXECUTION_MODE=live, DATA_MODE=live
- [ ] **P1 funding_rate Live 체결 1건** (Binance Futures)
- [ ] 증거: TimescaleDB execution_log + Telegram 알림 + 대시보드 표시

**Phase 1 완료**: Live 체결 1건 + crash 0 + Telegram 알림

### Phase 2: 카나리 — 11조합 72시간

**Step 2-1**: P1+P2 (Futures FR) 24시간
- [ ] 체결≥3건, 수수료 실측≤예상×1.5, crash=0
- [ ] TCA 리포트: 슬리피지 실측 vs 예측 비교 (slippage_feedback.py)

**Step 2-2**: +P3 (Futures-Futures) 48시간
- [ ] BinFut↔BitFut 양 레그 동시 체결 확인 (AtomicExecutor partial fill 대응)
- [ ] crash=0, 이전 P1+P2 안정 유지

**Step 2-3**: +P4+P5 (Spot-Futures) 48시간
- [ ] Bin/Bit 각 Spot↔Fut 베이시스 체결 확인, OU 파라미터 반응 기록
- [ ] per-strategy CB: 단일 전략 손실 > 잔고 5% 시 자동 비활성화 확인

**Step 2-4**: +P6+P7 (CE Coinone) 48시간
- [ ] KRW↔USDT 환산 정확도, Coinone BTC 제외 유지 (Live 재확인)
- [ ] L1 전송비 $2.50 cost_calculator 반영 확인, kimchi premium 없으면 0건 = 정상

**Step 2-5**: +P8+P9 (CE Upbit) 48시간
- [ ] Upbit 0.139% 수수료 + L1 $4.50 반영, 최소 스프레드 $5+ 진입 확인

**Step 2-6**: +P10+P11 (CE Bithumb+Global) 48시간
- [ ] Bithumb stale guard 실전: fake spread 차단 로그, ±50% 가드 오탐 0건
- [ ] Bin↔Bitget L2 $0.16 전송, 스프레드 작으므로 시그널 적을 수 있음 = 정상

**Step 2-7**: Auto-tuner 활성화
- [ ] .env: ENABLE_INLINE_TUNER=true, optuna+apscheduler 설치 확인
- [ ] 초기 튜닝 (엔진 시작 5분 후) + spot_futures OU 파라미터 개선 여부 기록
- [ ] Devil's Advocate rollback 정상, Telegram 결과 알림

**Step 2-8**: P12 전체 11조합 72시간
- [ ] crash=0, MDD<5%, PnL≥$0, 전략별 체결≥1 (CE는 premium 없으면 0건 허용)
- [ ] auto-tuner 1회+ 완료, Bithumb stale guard 오탐 0건
- [ ] DB 모드 분리 최종 확인: `SELECT DISTINCT mode FROM execution_log` → 'live'만 존재
- [ ] Redis 메모리: `redis-cli info memory` → maxmemory-policy=noeviction, used < 80%
- [ ] 72시간 중 WS 재연결 횟수 기록 (exchange_health_check 로그)
- [ ] attribution.py 분석 실행 → live 거래만 포함되는지 확인

**Phase 2 완료**: 11조합 72시간 무중단 + crash 0 + MDD < 5%

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
- [ ] Step 0-1: 모드 분리 수정 (DB 쿼리 필터, orderbook source, PaperExecutor 수수료, Telegram 모드 게이팅)
- [ ] Step 0-2: 심볼 제외 + 퍼센트 자본 + BTC 가격 갱신
- [ ] Step 0-3: Config 정합 (engine.json 통일, 중복 정리, dynaconf 확인)
- [ ] Step 0-4: 인프라 운영급 수정 (Redis maxmemory, 모니터 간격, shutdown timeout)
- [ ] Step 0-5: 대시보드

### Phase 1
- [ ] Step 1-1: 7 어댑터 배선 + 모드 분리 검증
- [ ] Step 1-2: Paper 1시간 + 수수료/shutdown 검증
- [ ] Step 1-2b: 리스크 시나리오 검증
- [ ] Step 1-3: 첫 Live 체결 ← 핵심 마일스톤

### Phase 2
- [ ] Step 2-1~2-6: 조합 순차 추가
- [ ] Step 2-7: Auto-tuner
- [ ] Step 2-8: 전체 72시간

### Phase 3
- [ ] Phase 2 후 결정
