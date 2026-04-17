# PHOENIX 구조 리팩토링 계획

> 작성: 2026-04-16 | 근거: 3개 Opus 감사 에이전트 물리적 증거 기반
> 이 문서가 리팩토링의 유일한 실행 기준. PHOENIX_PLAN.md (카나리)와 별도.

---

## 문제 진단 요약

v94~v121: 39 commits, 23 bugs. 하나 고치면 다른 데서 터짐. 근본 원인 3개:

| # | 구조 문제 | 증상 버그 | 감사 증거 |
|---|----------|----------|----------|
| 1 | Config 2개 병렬 시스템 + 유령 소스 | BUG-79,80,83,85 | trading.json 키 leak, Pydantic override 3개 누락, 직접 파일리더 3곳 |
| 2 | Position 상태 9곳 독립 추적 (PositionManager = dead code) | BUG-92 ghost, _position_sizes 롤백 누수, Reconciler 항상 빈 데이터 | open/close/rollback 시 2곳만 업데이트, 7곳은 dead |
| 3 | 실행 파이프라인 결합도 (entry/exit 롤백 의미 충돌) | ghost 무한루프, BUG-116 타이밍 레이스 | 단일 on_execution_rollback이 entry(삭제)/exit(복원) 오버로딩 |

---

## 워크스트림 3개 (순차 실행, 의존성 있음)

### WS-1: Config 단일화 (독립, 최소 리스크)

**목표**: 설정 소스를 `engine.json` + `.env`(시크릿) 2개로 완전 통합.

| # | 작업 | 파일 | 검증 기준 |
|---|------|------|----------|
| 1.1 | trading.json deep-merge 제거 — config_loader.py에서 trading.json 로드 삭제 | `config_loader.py:47-50` | `get_config("slippage.gamma")` → KeyError 또는 engine.json 값 |
| 1.2 | trading.json 고유 키 중 실제 사용되는 것만 engine.json으로 이전 | `slippage.gamma` (slippage_model.py:74가 읽음) | grep 확인: trading.json import 0건 |
| 1.3 | trading.json 파일 삭제 또는 빈 `{}` | `config/trading.json` | 파일 없어도 엔진 정상 기동 |
| 1.4 | Pydantic override 3개 추가 | `config.py:_apply_engine_json_overrides()` | `circuit_breaker_cooldown_seconds`, `circuit_breaker_api_error_rate`, `live_gate.mdd_threshold` |
| 1.5 | 직접 파일리더 3곳 → `load_engine_config()` 경유로 변경 | `main.py:55`, `engine.py:186`, `funding_rate_collector.py:239` | grep `open.*engine.json` → 0건 (config.py/config_loader.py 제외) |
| 1.6 | DEAD 키 제거 (engine.json) | `log_level`, `api.port`, `exchanges.symbols`, `exchanges.symbol_exclusions_per_exchange`, `paper.*`, `tuner`, `cors.origins` | 삭제 후 pytest PASS |
| 1.7 | GHOST 키 정의 또는 코드에서 읽기 제거 | `risk.warmup_seconds`, `risk.max_net_exposure_pct`, `monitoring.engine_url`, `db.flush_acquire_timeout_s` | 각 키가 engine.json에 존재하거나 코드에서 읽지 않음 |
| 1.8 | settings.toml 고스트 키 정리 | `max_position_usd`, `shadow_depth_fraction`, `shadow_max_trade_size` | Pydantic 필드와 1:1 대응 확인 |
| 1.9 | `_apply_trading_json_defaults()` 함수 제거 | `main.py:381` | 함수 호출 0건 |

**완료 기준**: `pytest PASS` + `grep -r "trading.json" engine/src/` → 0건 (config_loader.py 내 주석 제외) + 엔진 정상 기동

---

### WS-2: Pipeline 분리 (WS-1 완료 후)

**목표**: entry/exit 롤백 의미를 분리하고, 전략 내부 상태 직접 접근 제거.

| # | 작업 | 파일 | 검증 기준 |
|---|------|------|----------|
| 2.1 | BaseStrategy에 4개 콜백 정의 (no-op default) | `strategies/base.py` | `handle_entry_rollback(sym)`, `handle_exit_rollback(sym)`, `handle_entry_success(sym)`, `handle_exit_success(sym)` |
| 2.2 | FF: on_execution_rollback → 4개 콜백으로 분리 | `futures_futures.py:176` | entry_rollback = 삭제, exit_rollback = 복원. 의미 명확 분리 |
| 2.3 | FR: on_execution_rollback → 4개 콜백으로 분리 | `funding_rate.py:460` | settlement 복구 경로 포함 |
| 2.4 | SF: on_execution_rollback → 4개 콜백으로 분리 | `spot_futures.py:370` | pending_close vs open_positions 분리 |
| 2.5 | StatArb, Triangular: 마이그레이션 | `statistical_arb.py:1050`, `triangular.py:245` | 기존 동작 유지 |
| 2.6 | live.py: 호출부 마이그레이션 — entry/exit 구분하여 호출 | `live.py:1349-1444`, `live.py:1876-1897` | `on_execution_rollback` 호출 0건 |
| 2.7 | BaseStrategy에 `clear_ghost(sym)` 메서드 추가 | `strategies/base.py` | 각 전략이 자체 내부 상태 정리. live.py가 `_pending_exits` 직접 접근 0건 |
| 2.8 | BUG-92 ghost check: `hasattr` 직접접근 → `_strat.clear_ghost(_sym)` 호출 | `live.py:2299-2314` | `hasattr(_strat, '_pending_exits')` 0건 |
| 2.9 | 듀얼 DeduplicationGate 통합 (live.py + executor.py) | `live.py:1253`, `executor.py:843` | 동일 키 포맷, 하나만 유지 |
| 2.10 | BUG-116 조사 + 수정: on_fill vs on_execution_rollback 타이밍 레이스 | `futures_futures.py` | asyncio.Lock 또는 CAS 패턴 적용 |

**완료 기준**: `pytest PASS` + `grep "on_execution_rollback" engine/src/modes/` → 0건 + `grep "hasattr.*_pending_exits" engine/src/modes/` → 0건

---

### WS-3: Position 상태 중앙화 (WS-2 완료 후)

**목표**: PositionManager를 유일한 포지션 진실 소스로 만들기. Exchange = 검증 소스.

| # | 작업 | 파일 | 검증 기준 |
|---|------|------|----------|
| 3.1 | PositionManager.open_position() 실행 경로 연결 | `main.py:3644` (기존 TODO) | fill 콜백에서 호출 증거 (로그) |
| 3.2 | PositionManager.close_position() 실행 경로 연결 | `main.py` | exit fill 콜백에서 호출 증거 |
| 3.3 | _position_sizes 롤백 누수 수정 | `main.py:1824-1857` | ROLLED_BACK 시 exposure 감소 증거 |
| 3.4 | DualWriter 활성화 검증 (position_wal + Redis) | `dual_write.py:192` | DB에 position_wal 행 존재, Redis에 position hash 존재 |
| 3.5 | PositionReconciler 수정: PositionManager에서 읽기 | `reconciler.py:46` | engine_positions 비어있지 않음 (PositionManager 데이터) |
| 3.6 | 전략별 _open_positions → PositionManager 읽기 전환 (점진적) | FF, FR, SF | 전략 내부 dict 삭제는 별도 Phase. 먼저 PositionManager와 동기 확인 |
| 3.7 | API 엔드포인트 검증 (/positions, /portfolio, /risk) | `api/routes/` | 비어있지 않은 데이터 반환 |
| 3.8 | Recovery 경로 검증 (position_wal → Redis 복원) | `recovery.py` | 엔진 재시작 후 position_wal에서 Redis 복원 확인 |
| 3.9 | 주기적 Exchange↔PositionManager 대조 (60초) | `live.py` | 불일치 시 WARNING 로그 + 자동 보정 |

**완료 기준**: `pytest PASS` + Paper 1H 실행 중 `position_wal` INSERT 확인 + Redis position hash 확인 + API `/positions` 비어있지 않음

---

## 실행 순서 + 의존성

```
WS-1 (Config)     WS-2 (Pipeline)     WS-3 (Position)
    |                   |                    |
  1.1~1.9              ---                  ---
    |                   |                    |
  pytest PASS      2.1~2.10                ---
    ✓                   |                    |
                   pytest PASS          3.1~3.9
                       ✓                    |
                                      pytest PASS
                                          ✓
                                    Paper 1H 검증
                                          ✓
                                     카나리 재개
```

**WS-1 → WS-2**: Pipeline 분리 시 config 값이 정확해야 함 (잘못된 circuit_breaker 값으로 테스트하면 무의미)
**WS-2 → WS-3**: Position 중앙화 시 rollback 의미가 명확해야 함 (entry/exit 구분 없이 PositionManager 업데이트하면 동일 버그 재발)

---

## 워크플로우 (각 WS 동일)

```
PLAN → EXEC (Agent Teams) → TEST (pytest) → VERIFY (code-reviewer Opus) → GATE
  ↑                                                                          |
  +-------------- FAIL 시 fix loop (같은 WS 내) ←----------------------------+
```

- **EXEC**: executor(sonnet) + build-fixer(sonnet) 병렬
- **VERIFY**: code-reviewer(opus) — 자체 검증 금지, 독립 에이전트
- **GATE**: pytest PASS + grep 확인 + verify APPROVE — 3개 모두 충족 시 commit+push

---

## 금지 사항

1. 감사에서 발견하지 않은 문제를 scope에 추가하지 말 것
2. WS-1 중 WS-2/3 작업 금지 (순차)
3. 증거 없는 완료 선언 금지 (grep 결과, test 결과 첨부 필수)
4. 카나리 실행 금지 (리팩토링 완료까지)
5. on_execution_rollback 재사용 금지 (WS-2에서 교체)

---

## 예상 작업량

| WS | 수정 파일 수 | 핵심 난이도 | 리스크 |
|----|------------|-----------|--------|
| WS-1 | ~8 | 낮음 (config 경로 정리) | 낮음 (키 이름 오타만 주의) |
| WS-2 | ~10 | 중간 (인터페이스 설계 + 마이그레이션) | 중간 (모든 전략 콜백 수정) |
| WS-3 | ~8 | 높음 (PositionManager 연결 + DualWriter 활성화) | 높음 (실행 경로 핵심 변경) |

총 ~26개 파일, ~30개 작업.

---

## 2026-04-17 후속: BUG-93~96 + 멀티모델 독립 리뷰

### 추가 발견 (WS-3 이후 멀티모델 리뷰)
- **BUG-93**: LiveMode에 position_manager 파라미터 누락 → WS-3이 live 경로에서 dead
- **BUG-94**: FF `on_signal` optimistic write → v123에서 ghost 11건 발생
- **BUG-95a-d**: duplicate race + on_fill eager + exit rollback + handle_*_success 미연결
- **BUG-96 GAP#1-#3 + HIGH**: margin guard 누수, exec_result invalid 팬텀, CancelledError, 멱등성

### 해결
| BUG | 수정 내용 | 커밋 | 검증 |
|----|---------|------|------|
| 93 | LiveMode position_manager 파라미터 추가 + trade_executed 경로에서 호출 | `80df207` | FR VANA 양쪽 leg position_opened 로그 |
| 94 | `_pending_position_metadata` two-phase + `on_execution_success` 승격 | `cb0312d` | ff.position_confirmed 로그 |
| 95a | Duplicate signal reject + TTL reaper (60s/180s) | `f85017d` | - |
| 95b | **CRITICAL** ROLLED_BACK Exit → handle_exit_rollback 분리 | `c827423` | Opus+Codex+Gemini 합의 |
| 95c | **CRITICAL** on_fill no-op + pending_exits TTL reaper | `0198ff7` | Gemini CRITICAL 해결 |
| 95d | handle_entry/exit_success dispatch on success path | `5b4a788` | - |
| 96-1 | margin guard → `clear_pending_entry` (BUG-78 보존) | `021cc45` | v137 PreexecClear 39+ 실전 |
| 96-2 | **CRITICAL** exec_result invalid → defensive rollback + early return | `fa1a37a`+`8fdca69` | phantom success 방지 |
| 96-3 | CancelledError + Exception handler → rollback notify | `28be30e` | - |
| 96-H | `_notify_pre_exec_rollback` 멱등성 guard | `5eaa0b8` | 이중 호출 방지 |
| 96-T | 방어 경로 5개 unit tests | `4ed276f` | 5 new pass |

### 멀티모델 리뷰 체계 정립
- `/ultrareview` (클라우드 병렬 multi-agent)
- `ccg` skill (codex + gemini + Claude synthesis)
- Opus code-reviewer → CRITICAL 3건 (GAP#2 return 누락 등) 독립 발견
- Opus debugger → BUG-96 근본 원인 3개 GAP 전수 식별

### v131 → v137 실증 (BUG-96 효과)
| 지표 | v131 (41min) | v137 (17min+) |
|------|--------------|----------------|
| Reaped (orphan) | 38 | **0** |
| Ghost_cleared | 1 | 0 |
| ERR/CRIT | 0 | 0 |
| PreexecClear | 0 (log debug였음) | 39+ (margin guard 실작동) |
| Trades | 2 | 1+ |

### WS-4 (다음 단계, 별도 US로 스케줄)
**목표**: PositionManager durability + 단일 source of truth

1. `asyncio.Queue` + 전용 `_pm_drain_loop` task (exception surfaced)
2. `update_index_sync()` on PositionManager before queue dispatch
3. `dual_writer=self._dual_writer` wire (persistence 활성화)
4. `_position_sizes` 통합 → PositionManager 단일 읽기 (flag-gated)

- 예상 LOC: ~90
- 예상 시간: 2시간
- 파일: `.omc/plans/ws-4-position-manager-durability.md`
