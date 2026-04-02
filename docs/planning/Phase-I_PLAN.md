# Phase I PLAN — 배관 정리 + 거래소 기반 완성

> 작성: leviathan-planner (Stage A) | 2026-04-01
> 개정: planner 2차 (코드베이스 탐색 결과 반영) | 2026-04-01
> 근거: `/Users/100aniv/.claude/plans/fancy-strolling-pine.md` §Phase I
> 선행: Phase H PASS (Live 파이프라인 + 4-모드 통일)

---

## 0. Entry Gate 점검 결과

### 0.1 SSOT 정합성

| 항목 | 현재 값 | 기대 값 | 상태 |
|------|---------|---------|------|
| SSOT.md Phase | "Phase H 완료" | "Phase I 진행중" | DRIFT -- ssot-keeper 업데이트 필요 |
| SSOT.md 실행 순서 | "SIT-3 -> TF QF 12차 -> ..." | "Phase H -> **Phase I** -> J -> ..." | DRIFT |
| SSOT.md 모드 체계 | "backtest -> paper -> shadow -> live" 4단계 | "backtest / paper / live" 3단계 (Phase I 완료 후) | 예정 변경 |
| `.omc/state/leviathan-active-phase.json` | `"Phase-I"` + `"in_progress"` | 일치 | OK |
| CLAUDE.md 현재 상태 | "SIT-3" 언급 | "Phase I" 반영 필요 | DRIFT |
| Tests | 5,274 passed / 0 failed / 12 skipped | -- | OK |
| PRD | 338/343 passes:true | -- | OK (5개 passes:false는 Phase I 범위 밖) |

### 0.2 PRD passes:false US 현황 (5개)

| US | Phase | 제목 | Phase I 관련 |
|----|-------|------|-------------|
| US-055 | F | LiveGate + Preflight 10항목 통과 | NO -- Live 전환 시 (Phase K) |
| US-056 | F | Live 모드 전환 (사용자 승인) | NO -- Live 전환 시 (Phase K) |
| US-332 | S26 | SF 24H Progressive Shadow 재실행 | NO -- 런타임 검증 (Phase J/K) |
| US-334 | S26 | 소액 Live 전환 기준 + Sandbox Testnet | NO -- Phase K |
| US-339 | SIT-3 | 종합테스트 Canary 72H | NO -- 이전 SIT 잔여 |

**결론**: Phase I는 신규 US를 생성해야 함. 기존 passes:false US와 충돌 없음.

### 0.3 의존성 확인

- Phase H PASS (commits: H-1 LiveMode, H-2 EngineMode 4-mode): OK
- SIT-3 410/410 GREEN: OK
- 테스트 5,274 passed / 0 failed: OK

### 0.4 파일 경계 충돌

활성 US가 없으므로 파일 경계 충돌 없음.

### 0.5 Entry Gate 판정

| Gate | 결과 |
|------|------|
| SSOT 정합성 | DRIFT -- ssot-keeper 업데이트 필요 (차단 아님, Phase 전환 시 정상 절차) |
| PRD 연결 | 신규 US 생성 필요 (Phase I 범위 US 미존재) |
| 의존성 | PASS |
| 수학 모델 | 변경 없음 -- PASS |
| 파일 경계 | PASS |
| WIRING AC | I-E 서브태스크에 해당 -- 계획에 포함됨 |

**판정: CONDITIONAL PASS -- SSOT/CLAUDE.md 드리프트 수정 + 신규 US 등록 후 Stage B 진입 허용**

---

## 1. Phase I 목표

**배관 정리 + 거래소 기반 완성**을 통해 Live 검증(Phase K)의 기반을 확립한다.

1. **설정 파편화 해소**: 5개 설정 진입점 -> 2개 (`.env` + `config/engine.json`)
2. **모드 단순화**: 4-모드(backtest/paper/shadow/live) -> 3-모드(backtest/paper/live)
3. **Dead Wiring 수정**: ExposureTracker, TCAAnalyzer, BookWalkSlippage 3개 연결
4. **거래소 확장**: BingX, LBank, OrangeX 신규 어댑터 3개 개발

---

## 2. 현재 상태 수치 (코드베이스 탐색 결과)

### 2.1 설정 시스템

| 진입점 | 파일 | 상태 |
|--------|------|------|
| 1. `.env` | `engine/.env` | 유지 (secrets + 환경 변수) |
| 2. `config/engine.json` | `engine/config/engine.json` (94줄) | 유지 (런타임 파라미터) |
| 3. `settings.toml` | `engine/settings.toml` (45줄) | **폐기 대상** -- dynaconf 프로필 |
| 4. `config/trading.json` | `engine/config/trading.json` | **폐기 대상** |
| 5. `config/strategy_params.json` | `engine/config/strategy_params.json` | **engine.json에 통합** |
| 6. `config/shadow_mode.json` | `engine/config/shadow_mode.json` (30줄) | **폐기 대상** (shadow 제거) |
| 7. `config/strategy_activation.json` | `engine/config/strategy_activation.json` | engine.json에 통합 검토 |

**os.getenv() 직접 호출**: 149회 / 34개 파일
- 최다: `main.py` (24), `shadow.py` (14), `data_quality_manager.py` (11), `telegram.py` (10)

**get_settings() 사용**: 4회 / 3개 파일 (config.py, main.py, settings.py)

### 2.2 모드 시스템

```
EngineMode(StrEnum):
  BACKTEST = "backtest"   # Historical data + SimExecutor
  PAPER = "paper"         # Live WS data + SimExecutor
  SHADOW = "shadow"       # Live WS data + AtomicExecutor small capital
  LIVE = "live"           # Live WS data + AtomicExecutor full capital
```

- `ShadowMode` 클래스: `engine/src/modes/shadow.py` -- **2,632줄** (거대)
- `LiveMode` 클래스: `engine/src/modes/live.py` -- **1,189줄**
- 중복 추정: ~70% (orderbook 처리, 신호 라우팅, 체결 로직, 요약 전송 동일)
- `ShadowMode` import 위치: `main.py` (3곳), `modes/__init__.py`, `scheduled_tuner.py`
- `shadow_mode` 속성 참조: API routes 12+개 파일에서 `ctx.shadow_mode` 접근

### 2.3 Dead Wiring

| 컴포넌트 | 생성 | 주입 | 호출 | 상태 |
|----------|------|------|------|------|
| ExposureTracker | `main.py:1204` (Redis/memory) | `self._exposure_tracker` | **호출 경로 불명** | DEAD |
| TCAAnalyzer | `main.py:1289` (window=1000) | `self._tca_analyzer` | **콜백 미연결** | DEAD |
| BookWalkSlippage | `shadow.py:480` (ShadowMode 내부) | PaperExecutor.slippage_model | ShadowMode에서만 사용 | PARTIAL (LiveMode 미연결) |

### 2.4 거래소 어댑터 현황

**기존 (14개 collector 파일)**:
- Spot: `binance`, `bybit`, `okx`, `bitget`, `upbit`, `bithumb`, `coinone`, `gateio`, `mexc`
- Futures: `binance_futures`, `bybit_futures`, `okx_futures`
- 기타: `base_collector`, `funding_rate_collector`

**신규 필요**: `bingx`, `lbank`, `orangex` (3개)

### 2.5 engine.json 내 shadow 관련 키

```json
"shadow": {
  "capital_pct": 5.0,
  "max_position_pct": 3.0,
  "max_daily_loss_pct": 5.0,
  "exchanges": ["binance", "binance_futures"]
}
```
-> Phase I 완료 후 삭제. `live` 키에 `capital_pct` 추가로 소자본/풀자본 구분.

---

## 3. 서브태스크 상세 (의존성 순서)

### I-B: 설정 통합 (최대 약점 수정) -- 1일

**목적**: 5+ 설정 진입점 -> 2개로 축소. os.getenv 직접 호출 -> get_settings() 싱글톤.

**수정 대상 파일**:

| 파일 | 변경 내용 |
|------|----------|
| `engine/src/core/config.py` | `get_settings()` 싱글톤 확장. dynaconf 로더 제거 또는 engine.json으로 통합 |
| `engine/settings.toml` | **삭제** -- 모든 값을 engine.json + .env로 이관 |
| `engine/config/trading.json` | **삭제** -- engine.json에 병합 |
| `engine/config/strategy_params.json` | **삭제** -- engine.json `strategy_params` 키로 병합 |
| `engine/config/shadow_mode.json` | **삭제** -- shadow 개념 제거 |
| `engine/config/strategy_activation.json` | engine.json `exchanges.strategies` 키로 병합 검토 |
| 34개 파일 (149회 os.getenv) | `from src.core.config import get_settings` + `get_settings().XXX` 패턴으로 교체 |

**완료 기준**:
- `grep -r "os.getenv" engine/src/ | wc -l` <= 5 (secrets 전용 잔여만 허용)
- `config/` 디렉토리에 `engine.json` 1개만 존재
- `settings.toml` 삭제
- 전체 테스트 PASS

**WIRING AC**:
- 생성: `get_settings()` 싱글톤 (이미 존재, 확장)
- 주입: 모든 모듈에서 `from src.core.config import get_settings`
- 호출: `get_settings().max_position_pct` 등 (os.getenv 대체)

---

### I-C: EngineMode 3개로 단순화 -- 0.5일

**목적**: `EngineMode.SHADOW` 삭제. shadow = live + 소자본 설정.

**수정 대상 파일**:

| 파일 | 변경 내용 |
|------|----------|
| `engine/src/core/config.py` | `EngineMode.SHADOW` 삭제. `resolve_engine_mode()` 에서 shadow -> live 매핑 제거 |
| `engine/config/engine.json` | `shadow` 키 삭제. `live.capital_pct` 추가 (소자본 구분) |
| `engine/src/main.py` | `_shadow_mode_loop()` -> `_paper_mode_loop()` 리네이밍. mode 분기 정리 |
| `engine/src/modes/__init__.py` | `ShadowMode` export 제거 |
| SSOT.md | 모드 체계 3-mode 업데이트 (ssot-keeper 통해) |

**완료 기준**:
- `grep -r "EngineMode.SHADOW" engine/src/ | wc -l` = 0
- `grep -r "SHADOW" engine/src/core/config.py | wc -l` = 0
- 전체 테스트 PASS

**주의사항**:
- `settings.toml`의 `[shadow]` 프로필은 I-B에서 이미 삭제됨
- `resolve_engine_mode()` 함수에서 sandbox -> SHADOW 매핑 제거
- engine.json `mode` 값이 `"shadow"` 인 기존 배포 -> 마이그레이션 가이드 작성

---

### I-D: ShadowMode/LiveMode 중복 제거 -- 1일

**목적**: ShadowMode(2,632줄) 폐기 -> LiveMode(1,189줄)에 통합. 공통 로직 BaseMode 추출.

**수정 대상 파일**:

| 파일 | 변경 내용 |
|------|----------|
| `engine/src/modes/base.py` | **신규 생성** -- 공통 로직 추출 (orderbook 처리, 신호 라우팅, 체결 파이프라인, 요약 전송) |
| `engine/src/modes/live.py` | `LiveMode(BaseMode)` 상속. DI executor 유지 (Paper/Atomic) |
| `engine/src/modes/shadow.py` | **삭제** -- 2,632줄 전부 LiveMode/BaseMode로 이관 |
| `engine/src/modes/progressive_shadow.py` | `ShadowMode` -> `LiveMode` 참조 변경 |
| `engine/src/main.py` | `from src.modes.shadow import ShadowMode` -> `from src.modes.live import LiveMode` |
| `engine/src/api/routes/shadow.py` | `shadow_mode` -> 통합 mode 참조. API 경로 유지 (하위 호환) |
| `engine/src/api/routes/portfolio.py` | `ctx.shadow_mode` -> `ctx.mode` 또는 `ctx.live_mode` |
| `engine/src/api/routes/trading.py` | 동일 리네이밍 |
| `engine/src/api/routes/attribution.py` | 동일 리네이밍 |
| `engine/src/api/routes/risk.py` | 동일 리네이밍 |
| `engine/src/api/routes/strategies.py` | 동일 리네이밍 |
| `engine/src/api/server.py` | `AppContext.shadow_mode` -> `AppContext.engine_mode` |
| `engine/src/modes/strategy_validation.py` | `shadow_mode` 파라미터명 변경 |
| `engine/src/strategies/base.py` | `shadow_mode` property -> `paper_mode` 또는 제거 |
| `engine/src/infra/telegram.py` | `shadow_mode_start` alert_type 유지 (하위호환) 또는 리네이밍 |

**BaseMode 추출 대상 (공통 로직)**:
1. orderbook 수신 + 라우팅 (`_on_orderbook_update`)
2. 신호 생성 + 평가 (`_signal_evaluation_loop`)
3. 체결 시뮬레이션/실행 (`_execute_signal`, `_execute_trade_request`)
4. KRW 환율 루프 (`_krw_rate_loop`)
5. 일일 요약 (`_daily_summary_loop`)
6. 통계 스냅샷 (`get_snapshot`)
7. 밸런스 트래커 (`_balance_tracker`)
8. 전략 매니저 라우팅

**LiveMode 전용**:
- DI executor (PaperExecutor / AtomicExecutor)
- LiveGate 체크 + Shadow fallback
- 실제 RiskGuardian pre-trade check

**완료 기준**:
- `engine/src/modes/shadow.py` 파일 삭제됨
- `grep -r "ShadowMode" engine/src/ | wc -l` = 0
- `grep -r "shadow_mode" engine/src/api/ | wc -l` = 0 (또는 하위호환 alias만)
- 전체 테스트 PASS
- Paper 모드 10min Shadow 실행 정상 (crash=0, 신호 흐름 동일)

**리스크**: 가장 큰 서브태스크. ShadowMode 2,632줄 -> BaseMode + LiveMode 분리 시 회귀 위험 높음. 단위테스트 + Shadow 10min 필수.

---

### I-E: Dead Wiring 수정 (3개) -- 0.5일

**목적**: 생성은 됐으나 호출되지 않는 3개 컴포넌트를 런타임 파이프라인에 연결.

#### I-E-1: ExposureTracker -> RiskGuardian 통합

| 파일 | 변경 내용 |
|------|----------|
| `engine/src/risk/guardian.py` | `check_net_exposure()` 메서드에서 ExposureTracker 호출 |
| `engine/src/main.py` | `_exposure_tracker`를 RiskGuardian 생성자에 주입 |
| `engine/src/modes/base.py` (또는 live.py) | 체결 후 `exposure_tracker.update()` 호출 추가 |

**WIRING AC**:
- 생성: `main.py:1204` (이미 존재)
- 주입: RiskGuardian 생성자에 `exposure_tracker` 파라미터 추가
- 호출: 체결 후 `update()` + pre-trade에서 `check_net_exposure()`

#### I-E-2: TCAAnalyzer -> 실행 콜백 연결

| 파일 | 변경 내용 |
|------|----------|
| `engine/src/modes/base.py` (또는 live.py) | 체결 완료 후 `tca_analyzer.record()` 호출 |
| `engine/src/main.py` | TCAAnalyzer를 mode 인스턴스에 주입 |
| `engine/src/api/routes/` | TCA 메트릭 API 엔드포인트 연결 확인 |

**WIRING AC**:
- 생성: `main.py:1289` (이미 존재)
- 주입: BaseMode/LiveMode 생성자에 `tca_analyzer` 파라미터 추가
- 호출: `_execute_signal()` 완료 후 `tca_analyzer.record(ExecutionRecord(...))` 호출

#### I-E-3: BookWalkSlippage -> LiveMode PaperExecutor 연결

| 파일 | 변경 내용 |
|------|----------|
| `engine/src/modes/live.py` | LiveMode에서 PaperExecutor 사용 시 BookWalkSlippage 주입 (현재 ShadowMode에만 존재) |

**WIRING AC**:
- 생성: BaseMode에서 `BookWalkSlippage(books=self._books)` 생성
- 주입: `PaperExecutor(slippage_model=book_walk_slippage)` (I-D에서 통합 시 자동 해결)
- 호출: PaperExecutor.execute() 내부에서 자동 호출

**주의**: BookWalkSlippage는 SSOT.md 4.4에 따라 "실행 시뮬레이션 계층"으로 정의됨. 이중 슬리피지가 아님 (CEXOrderbookSlippage = 필터, BookWalkSlippage = 체결가). PaperExecutor에서만 사용, AtomicExecutor는 실 오더북 체결이므로 불필요.

**완료 기준**:
- Shadow 10min 실행 로그에서 ExposureTracker.update() 호출 >= 1건
- TCAAnalyzer.record() 호출 >= 1건
- BookWalkSlippage 호출 >= 1건 (Paper 모드 시)
- **런타임 증거 필수** (passes:true 조건)

---

### I-G: 거래소 확장 (신규 어댑터 3개) -- 1.5일

**목적**: BingX, LBank, OrangeX 신규 WebSocket 어댑터 개발.

**기존 어댑터 패턴** (`base_collector.py` 상속):
```
class {Exchange}Collector(BaseCollector):
    EXCHANGE_ID = "{exchange}"
    WS_URL = "wss://..."
    async def _subscribe(self, symbols): ...
    async def _handle_message(self, msg): ...
    def _parse_orderbook(self, data) -> OrderBook: ...
```

#### I-G-1: BingX 어댑터

| 파일 | 내용 |
|------|------|
| `engine/src/collectors/bingx_collector.py` | **신규** -- BingX Spot WS orderbook |
| `engine/tests/unit/collectors/test_bingx_collector.py` | **신규** -- 단위테스트 |

**WIRING AC**:
- 생성: `BingXCollector(BaseCollector)` 클래스
- 주입: `engine/src/main.py` collector registry에 `"bingx": BingXCollector` 등록
- 호출: `active_exchanges` 에 `"bingx"` 포함 시 자동 활성화

#### I-G-2: LBank 어댑터

| 파일 | 내용 |
|------|------|
| `engine/src/collectors/lbank_collector.py` | **신규** -- LBank Spot WS orderbook |
| `engine/tests/unit/collectors/test_lbank_collector.py` | **신규** -- 단위테스트 |

#### I-G-3: OrangeX 어댑터

| 파일 | 내용 |
|------|------|
| `engine/src/collectors/orangex_collector.py` | **신규** -- OrangeX Spot WS orderbook |
| `engine/tests/unit/collectors/test_orangex_collector.py` | **신규** -- 단위테스트 |

**공통 완료 기준**:
- 각 어댑터 단위테스트 PASS
- Paper 모드에서 3개 거래소 WS 연결 + orderbook 수신 확인 (API 키 필요시 사장님 제공 후)
- `engine/config/engine.json` `exchanges.active`에 추가 가능 상태
- `engine/src/friction/fee_model.py`에 수수료 테이블 추가

**리서치 필요**: 각 거래소 WS API 문서 (exa.ai 활용). 어댑터 개발 전 `document-specialist`로 공식 문서 수집.

---

## 4. 의존성 순서 + 일정

```
I-B (설정 통합, 1일)
  |
  v
I-C (EngineMode 3개, 0.5일)  -- I-B 완료 후 (settings.toml 삭제 전제)
  |
  v
I-D (ShadowMode 폐기, 1일)   -- I-C 완료 후 (SHADOW enum 삭제 전제)
  |
  v
I-E (Dead Wiring, 0.5일)     -- I-D 완료 후 (BaseMode 구조 전제)
  |
  v
I-G (거래소 확장, 1.5일)      -- I-B 완료 후 (설정 시스템 안정화 전제). I-D/I-E와 병렬 가능.
```

**총 예상 소요**: 3~4일 (I-G는 I-D/I-E와 병렬 진행 가능)

**병렬화 전략**:
- Track 1: I-B -> I-C -> I-D -> I-E (직렬, 코어 배관)
- Track 2: I-G (I-B 완료 후 독립 병렬, 거래소 어댑터)

---

## 5. 신규 US 등록 계획 (prd.json)

| US ID | Phase | 제목 | 서브태스크 |
|-------|-------|------|-----------|
| US-344 | I | 설정 통합: os.getenv -> get_settings 싱글톤 | I-B |
| US-345 | I | EngineMode 3-모드 단순화 (SHADOW 삭제) | I-C |
| US-346 | I | ShadowMode/LiveMode 통합 + BaseMode 추출 | I-D |
| US-347 | I | Dead Wiring 수정 (ExposureTracker + TCA + BookWalk) | I-E |
| US-348 | I | BingX 거래소 어댑터 신규 개발 | I-G-1 |
| US-349 | I | LBank 거래소 어댑터 신규 개발 | I-G-2 |
| US-350 | I | OrangeX 거래소 어댑터 신규 개발 | I-G-3 |

---

## 6. Phase I 완료 기준

| # | 기준 | 검증 방법 |
|---|------|----------|
| 1 | `check_all` 9/9 OK | `python -m src.workflow.cli check_all` |
| 2 | 설정 변경 시 수정 파일 <= 2개 | `.env` + `config/engine.json` 만 |
| 3 | `os.getenv` 직접 호출 <= 5개 | `grep -r "os.getenv" engine/src/ \| wc -l` |
| 4 | `EngineMode` 3개만 존재 | BACKTEST, PAPER, LIVE |
| 5 | `shadow.py` 삭제됨 | 파일 미존재 |
| 6 | Dead Wiring 3개 런타임 호출 증거 | Shadow 10min 로그 |
| 7 | 신규 어댑터 3개 WS 연결 성공 | Paper 모드 데이터 수신 |
| 8 | 전체 테스트 PASS | `pytest tests/ -x --tb=short` |
| 9 | Shadow 10min crash=0, 신호 흐름 정상 | `timeout 600 python -m src.main` |

---

## 7. 리스크 및 주의사항

### 7.1 고위험

| 리스크 | 영향 | 완화 |
|--------|------|------|
| I-D ShadowMode 2,632줄 폐기 시 회귀 | API routes 12+개 파일 참조 깨짐 | 단계적 진행: 먼저 alias 유지 -> 전체 리네이밍 -> 삭제 |
| os.getenv 149회 일괄 교체 시 누락 | 런타임 설정 미로드 | ast_grep 자동화 + 테스트 전수 실행 |
| 거래소 API 변경 (BingX/LBank/OrangeX) | 어댑터 개발 지연 | exa.ai로 최신 문서 수집 후 개발 |

### 7.2 금지 사항

- **이중 슬리피지 금지**: BookWalkSlippage 연결 시 CEXOrderbookSlippage와 PnL 합산하지 않을 것
- **고정 USD 한도 금지**: `max_position_usd`, `daily_loss_cap` -> `max_position_pct`, `daily_loss_pct` (퍼센티지만)
- **settings.toml 값 유실 금지**: 삭제 전 모든 값이 engine.json 또는 .env에 이관 확인

### 7.3 하위 호환

- API 엔드포인트 `/api/shadow/*` 는 deprecation warning과 함께 유지 (Phase L에서 정리)
- `ctx.shadow_mode` -> `ctx.engine_mode` alias 제공 (1 Phase 유예)
- Telegram `shadow_mode_start` alert_type -> `paper_mode_start` 매핑

---

## 8. SSOT 업데이트 지시사항 (ssot-keeper용)

### SSOT.md 2 업데이트 항목:

1. **Phase 상태**: `Phase I 진행중 (2026-04-01) -- 배관 정리 + 거래소 기반 완성`
2. **TF Status 라인**: `Phase H -> **Phase I** (진행중) -> J -> K -> L -> M -> N`
3. **실행 순서** (헤더): `A~M -> S1~S26 -> SIT-0~3 -> Phase H -> **Phase I** (진행중) -> J -> K -> ...`
4. **Next 라인**: `Phase I 서브태스크 실행 (I-B 설정통합 -> I-C 모드단순화 -> I-D 통합 -> I-E Wiring -> I-G 거래소)`
5. **계획서**: `.claude/plans/fancy-strolling-pine.md` (Phase I~N 로드맵)
6. **Phase I 계획**: `docs/planning/Phase-I_PLAN.md`
7. **모드 체계**: Phase I 완료 후 `backtest / paper / live` 3-모드로 전환 예정 (현재는 4-모드 유지)

### CLAUDE.md 업데이트 항목:

1. **현재 상태 섹션**: `SIT-3` -> `Phase I (배관 정리 + 거래소 기반 완성)`
2. **Phase 순서**: `Phase H -> **Phase I** (진행중) -> J -> K -> L -> M -> N`
3. **다음 작업**: `Phase I: I-B 설정통합 -> I-C 모드단순화 -> I-D ShadowMode 통합 -> I-E Dead Wiring -> I-G 거래소`

---

## 9. Stage B 진입 조건 체크리스트

- [ ] SSOT.md Phase I 시작 상태 반영 (ssot-keeper)
- [ ] CLAUDE.md 현재 상태 업데이트
- [ ] prd.json에 US-344 ~ US-350 등록 (passes:false)
- [ ] `check_all` DRIFT 해소 (Phase 일치)
- [ ] exa.ai로 BingX/LBank/OrangeX WS API 문서 수집

**위 체크리스트 완료 시 Stage B 진입 승인.**

---

## 10. US-344~350 실행 세부 계획 (Executor 참조용)

> 아래 표는 PRD US ID ↔ 내부 서브태스크 ID 대응 및 executor가 참조할 구현 상세를 담는다.

### US-344: Claude Code 인프라 설정 (서브태스크 I-A)

**PRD ID**: US-344 | **담당**: leviathan-executor | **예상 소요**: 30분

**현재 상태** (settings.local.json 탐색 완료):
- `CLAUDE_CODE_NO_FLICKER=1` — 이미 설정됨
- `SubagentStop` hooks (assembly-gate.sh, shadow-evidence-gate.sh) — 이미 등록됨
- `PermissionDenied` hook — **없음** (추가 필요)
- `leviathan-gate` skill — `~/.claude/settings.json` 미등록

**수정 파일**:
- `.claude/settings.local.json` — `PermissionDenied` hook 추가
- `~/.claude/settings.json` — `leviathan-gate` skill 등록
- `.claude/hooks/assembly-gate.sh` — LSP diagnostics 체크 라인 추가

**PermissionDenied hook 추가 위치** (settings.local.json `hooks` 오브젝트 내):
```json
"PermissionDenied": [
  {
    "matcher": "",
    "hooks": [{ "type": "command", "command": "echo '[leviathan-gate] permission denied — check Stage permissions'" }]
  }
]
```

**완료 기준**:
- `settings.local.json`에 `PermissionDenied` 키 존재
- `~/.claude/settings.json`에 `leviathan-gate` skill 등록
- `assembly-gate.sh`에 `lsp_diagnostics` 또는 동등한 정적 분석 체크 존재

---

### US-345: 설정 통합 — os.getenv → get_settings() (서브태스크 I-B)

**PRD ID**: US-345 | **담당**: leviathan-executor (TeamCreate, 설정 파일별 병렬) | **예상 소요**: 1일

**탐색 결과 수치**:
- `os.getenv()` 직접 호출: **149건 / 34개 파일**
- `config/` 폐기 대상 파일: `settings.toml`, `trading.json`, `strategy_params.json`, `shadow_mode.json`
- `engine.json` 현재 키 구조: mode / env / capital / api / exchanges / risk / paper / shadow / live / live_gate / strategy_params / funding_rate / statistical_arb / book_depth

**ast_grep 교체 패턴** (비민감 런타임 설정만):
```bash
# 검색
ast-grep --pattern 'os.getenv("$ENV_KEY", "$DEFAULT")' engine/src/

# 교체 예시 (ENGINE_ENV)
os.getenv("ENGINE_ENV", "dev")  →  get_settings().env

# 교체 예시 (POWERLAW_SLIPPAGE_K — shadow.py)
float(os.getenv("POWERLAW_SLIPPAGE_K", "0.0"))  →  get_settings().powerlaw_slippage_k
```

**민감 정보 유지 파일** (교체 대상 아님):
- `infra/telegram_trade_bot.py` (11건) — BOT_TOKEN, CHAT_ID
- `infra/telegram_dev_bot.py` (8건) — BOT_TOKEN
- `infra/telegram_infra_bot.py` (4건) — BOT_TOKEN
- `infra/compliance.py` (6건) — 규제 관련 외부 서비스 키

**교체 우선순위** (단위테스트 커버리지 높은 파일 먼저):
1. `core/config.py` (3건) — 싱글톤 자체 확장
2. `core/signal.py` (5건) — 신호 생성 파라미터
3. `modes/live.py` (4건) — 모드 설정
4. `modes/shadow.py` (14건) — I-D에서 폐기 예정이나 그 전에 교체
5. `main.py` (24건) — 가장 많음, 마지막에 처리

**완료 기준**:
- `grep -r "os\.getenv" engine/src/ | grep -v "telegram\|compliance\|memory_bus" | wc -l` ≤ 5
- `config/settings.toml` 삭제 (또는 미존재)
- `config/trading.json` 삭제 (또는 engine.json에 통합)
- `config/shadow_mode.json` 삭제
- `pytest tests/ -x --tb=short` 0 failures

**WIRING AC**:
- 생성: `engine/src/core/config.py` — `get_settings()` `@lru_cache` 싱글톤 확장
- 주입: 교체된 각 파일에서 `from src.core.config import get_settings` import
- 호출: `pytest` 에서 `get_settings()` 반환값 기반 설정 로드 확인

---

### US-346: EngineMode 3개 단순화 (서브태스크 I-C)

**PRD ID**: US-346 | **담당**: leviathan-executor | **예상 소요**: 0.5일
**선행 조건**: US-345 완료 (settings.toml 삭제 전제)

**grep 탐색 결과 — EngineMode.SHADOW 참조 4건**:
```
engine/src/main.py:647   elif self._engine_mode in (EngineMode.SHADOW, EngineMode.LIVE):
engine/src/main.py:1744  EngineMode.SHADOW: DataMode.REAL_AUTHENTICATED,
engine/src/main.py:1792  elif self._engine_mode == EngineMode.SHADOW:
engine/src/core/config.py:146  return EngineMode.SHADOW   ← 기본값
```

**수정 지시**:
1. `config.py:146` — 기본값을 `EngineMode.PAPER`로 변경 (더 안전한 기본값)
2. `main.py:647` — `EngineMode.SHADOW` 제거, `LIVE`만 유지
3. `main.py:1744` — `EngineMode.SHADOW` 키 제거 (DataMode 매핑에서)
4. `main.py:1792` — `elif EngineMode.SHADOW:` 분기를 `LIVE` 분기에 통합
5. `engine.json` — `"shadow"` 섹션 삭제, `live.capital_pct: 5.0` 추가 (소자본 구분)
6. `EngineMode` enum에서 `SHADOW = "shadow"` 라인 삭제

**engine.json 변경 후 `live` 섹션 목표 구조**:
```json
"live": {
  "capital_pct": 5.0,
  "max_position_pct": 3.0,
  "max_daily_loss_pct": 5.0,
  "exchanges": ["binance", "bybit", "okx", "bitget", "upbit", "bithumb", "coinone",
                "binance_futures", "bybit_futures", "okx_futures"]
}
```

**완료 기준**:
- `grep -r "EngineMode\.SHADOW" engine/src/ | wc -l` = 0
- `grep "\"shadow\"" engine/config/engine.json` 결과 없음
- `config.py` 기본 EngineMode = `PAPER`
- `pytest` 0 failures

---

### US-347: BaseMode 추출 — ShadowMode 폐기 (서브태스크 I-D)

**PRD ID**: US-347 | **담당**: leviathan-executor (TeamCreate 필수, 규모 大) | **예상 소요**: 1일
**선행 조건**: US-346 완료 (EngineMode.SHADOW 삭제 전제)

**규모 확인**:
- `shadow.py`: ~2,632줄 (31,960 토큰)
- `live.py`: ~1,189줄 (13,186 토큰)

**신규 파일**: `engine/src/modes/base.py`

**BaseMode에 추출할 공통 메서드 목록**:
```
_setup_collectors()          — WS 수집기 초기화
_setup_risk_guardian()       — RiskGuardian 초기화
_setup_strategy_manager()    — StrategyManager 초기화
_collector_health_check_loop() — 수집기 헬스체크 루프
_daily_summary_loop()        — 일일 요약 전송 루프
_krw_rate_loop()             — KRW 환율 갱신 루프
_on_orderbook_update()       — 오더북 수신 콜백
_signal_evaluation_loop()    — 신호 평가 루프
_execute_trade_request()     — TradeRequest 실행 (executor DI)
_record_trade_to_db()        — TimescaleDB 기록
_send_telegram_summary()     — Telegram 요약 전송
get_snapshot()               — 통계 스냅샷 반환
```

**LiveMode 전용 유지**:
```
_live_gate_check()           — LiveGate 통과 검사
_shadow_fallback()           — LiveGate 실패 시 Paper fallback
executor: ExecutorProtocol   — DI (PaperExecutor or AtomicExecutor)
```

**ShadowMode 처리 방식**: 단계적 폐기
1. 1단계: `shadow.py` 상단에 `# DEPRECATED: Phase I — 내용은 LiveMode(BaseMode)로 이관됨` 추가
2. 2단계: `__init__.py`에서 `ShadowMode` export 제거
3. 3단계: `modes/__init__.py` 이후 `shadow.py` 삭제 (import 오류 없음 확인 후)
4. `BookWalkSlippage` 클래스 — `shadow.py`에서 `modes/base.py` 또는 `execution/paper.py`로 이동 보존

**API 하위 호환 처리**:
- `AppContext.shadow_mode` → `AppContext.engine_mode` alias 1 Phase 유예
- `/api/shadow/*` 엔드포인트 — deprecation warning 헤더 추가, Phase L에서 제거

**완료 기준**:
- `engine/src/modes/base.py` 존재
- `class LiveMode(BaseMode):` 상속 구조
- `grep -r "ShadowMode" engine/src/ | grep -v "DEPRECATED\|alias" | wc -l` = 0
- Paper 모드 10분 실행 crash = 0, 신호 흐름 정상 (오더북 수신 → 신호 → 체결 로그)
- `pytest` 0 failures

**WIRING AC**:
- 생성: `engine/src/modes/base.py` — `BaseMode` 클래스
- 주입: `engine/src/modes/live.py` — `class LiveMode(BaseMode):`
- 호출: `main.py` 에서 `LiveMode` 인스턴스 생성 후 `await live_mode.run()` 로그 확인

---

### US-348: Dead Wiring 3개 연결 (서브태스크 I-E)

**PRD ID**: US-348 | **담당**: leviathan-executor | **예상 소요**: 0.5일
**선행 조건**: US-347 완료 (BaseMode 구조 전제)

#### US-348a: ExposureTracker

**현황**: `main.py:1200-1210` 초기화 됨. `main.py:1489` `update_exposure()` 호출 1곳 존재.
**문제**: 주기적 루프 없음 → 체결이 없으면 갱신 안 됨.

**수정 위치**: `engine/src/modes/base.py` (또는 `live.py`)
**추가할 메서드**:
```python
async def _exposure_tracker_loop(self) -> None:
    """10초마다 포지션 집계 → ExposureTracker 갱신."""
    logger.info("exposure_tracker_loop started")
    while not self._shutdown_event.is_set():
        try:
            positions = self._get_open_positions()
            for exchange_id, pos in positions.items():
                self._exposure_tracker.update_exposure(exchange_id, pos)
        except Exception as exc:
            logger.warning("exposure_tracker_loop error: %s", exc)
        await asyncio.sleep(10)
```
- `asyncio.gather()` 태스크 목록에 `self._exposure_tracker_loop()` 추가
- RiskGuardian `check()` 호출 전 ExposureTracker 데이터가 주입되는지 확인

**WIRING AC**:
- 생성: 이미 `main.py:1204`
- 주입: `_exposure_tracker_loop`가 `self._exposure_tracker` 참조 + `asyncio.gather` 포함
- 호출: Paper 10분 로그에 `exposure_tracker_loop started` 라인 존재

#### US-348b: TCAAnalyzer

**현황**: `main.py:1289` 초기화 됨. `main.py:1545` `record_execution()` 1곳만 호출.
**문제**: 특정 성공 경로에만 호출 → 실패 체결, 부분 체결 누락.

**수정 위치**: `engine/src/modes/base.py` `_execute_trade_request()` 또는 `_record_trade_to_db()`
**수정 방향**: 모든 체결 결과(성공/실패/부분)를 `_record_trade_to_db()` 에서 TCAAnalyzer에 전달:
```python
async def _record_trade_to_db(self, result: ExecutionResult) -> None:
    ...  # 기존 DB 기록
    if self._tca_analyzer is not None:
        self._tca_analyzer.record_execution(result)   # 추가
```

**WIRING AC**:
- 생성: 이미 `main.py:1289`
- 주입: `BaseMode.__init__`에 `tca_analyzer` 파라미터 추가 → `main.py`에서 전달
- 호출: Paper 10분 후 `self._tca_analyzer.records` 길이 > 0

#### US-348c: BookWalkSlippage

**현황**: `shadow.py:112` 정의, `shadow.py:480` PaperExecutor에 주입. ShadowMode 전용.
**US-347 이후**: ShadowMode 폐기 시 이 연결도 dead code 됨.
**처리 방향**: `BookWalkSlippage`를 `execution/paper.py` 또는 `modes/base.py`로 이동하여 LiveMode PaperExecutor에 연결.

**수학 모델 확인** (SSOT §4.4):
- CEXOrderbookSlippage = 신호 필터 (허용/차단 판단, fill_price 미반영)
- BookWalkSlippage = 실행 시뮬레이션 (실제 체결가 산출, VWAP 워킹)
- 두 계층은 역할이 다름 → 이중 계산 아님 → PaperExecutor 연결 허용

**WIRING AC**:
- 생성: `BookWalkSlippage` → `execution/paper.py` 또는 `modes/base.py`로 이동
- 주입: `LiveMode` PaperExecutor 초기화 시 `slippage_model=BookWalkSlippage(books=self._books)`
- 호출: Paper 10분 실행 로그에 BookWalkSlippage 적용된 fill_price 변동 확인

**완료 기준 (US-348 전체)**:
- Paper 10분 로그: `exposure_tracker_loop started` 존재
- Paper 10분 후: `tca_analyzer.records` 또는 Prometheus `tca_*` 메트릭 > 0
- Paper 10분 로그: BookWalkSlippage fill_price 적용 증거 또는 `passes:false` 명시적 처리
- `pytest` 0 failures

---

### US-349: AutoTuner 실 데이터 활성화 (서브태스크 I-F)

**PRD ID**: US-349 | **담당**: leviathan-executor | **예상 소요**: 15분
**독립 실행 가능** (배치 1 병렬)

**수정 파일**: `engine/.env`

**추가할 환경변수**:
```bash
# AutoTuner 실 데이터 소스 (TimescaleDB)
TUNER_DATA_SOURCE=timescaledb
ENABLE_INLINE_TUNER=true
```

**선행 확인**:
```bash
docker compose up -d timescaledb redis
# TimescaleDB에 26K+ 실 거래 데이터 존재 확인
```

**WIRING AC**:
- 생성: AutoTuner 인스턴스 — 기존 코드에 존재
- 주입: `TUNER_DATA_SOURCE=timescaledb` → AutoTuner가 TimescaleDB 쿼리 경로 선택
- 호출: AutoTuner 시작 로그에 `data_source=timescaledb` 확인

**완료 기준**:
- `engine/.env`에 두 변수 존재
- AutoTuner 시작 시 `data_source=timescaledb` 로그
- `pytest` 0 failures

---

### US-350: 신규 거래소 어댑터 BingX / LBank / OrangeX (서브태스크 I-G)

**PRD ID**: US-350 | **담당**: leviathan-executor (TeamCreate, 3개 어댑터 병렬) | **예상 소요**: 1.5일
**독립 실행 가능** (US-345 완료 후, I-D/I-E와 병렬 가능)

**기존 어댑터 패턴** (gateio_collector.py / bitget_collector.py 기반):
```python
class BingXCollector(BaseCollector):
    EXCHANGE_ID = "bingx"
    WS_URL = "wss://open-api-ws.bingx.com/market"   # exa.ai 확인 필수

    async def _subscribe(self, symbols: list[str]) -> None: ...
    async def _handle_message(self, msg: dict) -> None: ...
    def _parse_orderbook(self, data: dict) -> OrderBook: ...
```

**신규 파일 목록**:

| 파일 | 거래소 |
|------|--------|
| `engine/src/collectors/bingx_collector.py` | BingX Spot WS orderbook |
| `engine/src/collectors/lbank_collector.py` | LBank Spot WS orderbook |
| `engine/src/collectors/orangex_collector.py` | OrangeX Spot WS orderbook |
| `engine/tests/unit/collectors/test_bingx_collector.py` | BingX 단위테스트 |
| `engine/tests/unit/collectors/test_lbank_collector.py` | LBank 단위테스트 |
| `engine/tests/unit/collectors/test_orangex_collector.py` | OrangeX 단위테스트 |

**exa.ai 리서치 항목** (어댑터 개발 전 document-specialist 호출):
- BingX WS API: `https://bingx-api.github.io/docs/#/spot/market-websocket`
- LBank WS API: `https://www.lbank.com/en-US/docs/` (V2 endpoint 확인)
- OrangeX WS API: 공개 문서 없음 → exa.ai `site:orangex.com api websocket` 또는 사장님 계정에서 확인

**Gate.io / Bitget / OKX 활성화** (기존 어댑터, API 키만 필요):
- 어댑터 파일: `gateio_collector.py`, `bitget_collector.py`, `okx_collector.py` 이미 존재
- 조치: `engine.json` `exchanges.active` 배열에 `"gateio"`, `"bitget"` 추가 (`"okx"` 이미 포함)
- API 키: 사장님이 `engine/.env`에 직접 입력 (`GATEIO_API_KEY`, `BITGET_API_KEY`)

**수수료 테이블 추가** (`engine/src/friction/fee_model.py`):
- BingX: Maker 0.10% / Taker 0.10% (기본값, VIP0)
- LBank: 확인 필요 (기본 0.10%/0.10% 추정)
- OrangeX: 확인 필요

**WIRING AC** (각 어댑터):
- 생성: `{Exchange}Collector(BaseCollector)` 클래스
- 주입: `engine/config/engine.json` `exchanges.active`에 거래소 ID 추가
- 호출: Paper 10분 실행 로그에 `{Exchange}Collector connected` + 오더북 수신 확인

**완료 기준**:
- 3개 신규 어댑터 파일 + 단위테스트 존재
- 각 어댑터 단위테스트 PASS
- Gate.io / Bitget / OKX Paper 모드 데이터 수신 로그 확인
- BingX / LBank / OrangeX: API 키 없이 Public WS로 오더북 수신 가능 여부 확인
- `fee_model.py`에 3개 거래소 수수료 추가
- `pytest` 0 failures

---

## 11. 실행 배치 요약 (Executor용 일정표)

```
Day 1 오전 — 배치 1 (병렬, 독립):
  ├── US-344: settings.local.json PermissionDenied hook + leviathan-gate skill (30분)
  └── US-349: engine/.env TUNER 변수 추가 (15분)

Day 1 오후 — 배치 2-1 (순차):
  └── US-345: os.getenv → get_settings() 전수 교체 (반나절)
              ↳ ast_grep 패턴으로 34개 파일 자동화
              ↳ 민감 파일(telegram/compliance) 제외
              ↳ config/ 폐기 파일 삭제

Day 2 오전 — 배치 2-2 (US-345 완료 후):
  └── US-346: EngineMode.SHADOW 4건 수정 + engine.json shadow 섹션 삭제

Day 2 오후 — 배치 2-3 (US-346 완료 후):
  └── US-347: BaseMode 추출 + LiveMode(BaseMode) 리팩터 + ShadowMode DEPRECATED
              ↳ TeamCreate 6명 병렬 (shadow.py 섹션별 분담)

Day 3 — 배치 3 + 4 (병렬):
  ├── US-348: Dead Wiring 3개 연결 (0.5일) [US-347 완료 후]
  └── US-350: BingX/LBank/OrangeX 어댑터 개발 (1.5일) [US-345 완료 후, 병렬 가능]
              ↳ TeamCreate 3명 (거래소별 담당)
              ↳ 사전: exa.ai WS API 문서 수집

Day 4 — 통합 검증:
  ├── check_all 9/9 OK
  ├── pytest 0 failures
  ├── Paper 10분 실행 crash=0
  └── ssot-keeper로 SSOT.md / CLAUDE.md 업데이트
```
