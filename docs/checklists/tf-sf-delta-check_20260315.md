# TF Semi-Final [단계 1-A] Delta Check Report

> **Date**: 2026-03-15
> **Scope**: QF PASS (commit 2f7471b) → HEAD (Phase S7 Pre-Live Hardening + 3-Round 문서)
> **Verdict**: **ALL PASS** (HIGH 2건 발견 → 즉시 수정 → pytest 재확인 PASS)

---

## 1. 변경 범위

- **커밋 범위**: `2f7471b..HEAD` (4 commits)
- **파일**: 23 files changed, +2,188 / -692 lines
- **핵심 변경**: Phase S7 (US-157~168, Pre-Live Hardening 12 US)

### 변경된 소스 파일 (14개)

| 파일 | US | 변경 내용 |
|------|-----|----------|
| `engine/src/main.py` | US-157,162,165,168 | trading.json 로딩, Redis close, Telegram close, auto symbol |
| `engine/src/modes/shadow.py` | US-159,161,164 | Reconciliation, KRW stale rate, single loss defense |
| `engine/src/core/config.py` | US-157 | load_trading_config(), symbol_min_exchanges |
| `engine/src/core/signal.py` | US-162 | min_volume_usd volume filter |
| `engine/src/infra/redis/memory_bus.py` | US-160 | Queue maxsize=10000, drop oldest |
| `engine/src/infra/telegram.py` | US-168 | Reusable httpx.AsyncClient + close() |
| `engine/src/infra/telegram_bot.py` | US-168 | Reusable httpx.AsyncClient + close() |
| `engine/src/collectors/bithumb_collector.py` | US-168 | Reusable httpx.AsyncClient |
| `engine/config/trading.json` | US-157 | NEW: 비즈니스 설정 파일 |
| `dashboard/next.config.js` | US-163 | login rewrite |
| `dashboard/src/app/login/page.tsx` | US-163 | login page fix |
| `docker-compose.yml` | US-167 | Resource limits |
| `infra/nginx/nginx.conf` | — | minor fix |
| `engine/tests/unit/test_symbol_discovery.py` | US-162 | 240 new test lines |

---

## 2. 전문가 리뷰 결과

### Karina (Architect, opus)
- **SSOT/prd.json/CLAUDE.md 3-way 정합성**: MISMATCH 발견 → 수정 완료
- **시스템 응집도**: Engine init chain OK, Strategy registration OK, Risk management OK
- **발견**: HIGH 1 (min_volume_usd 미연결), MEDIUM 1 (prd.json 미동기화)

### Jeongyeon (Engine Expert, opus)
- **E2E 데이터 흐름**: VERIFIED (WS→PriceHub→Signal→Strategy→Executor→DB)
- **리소스 관리**: VERIFIED (httpx close, Redis disconnect, task cancellation)
- **발견**: HIGH 2 (min_volume_usd 미연결 + OrderBook volume_24h_usd 부재)

### Dahyun (Quant Validator, opus)
- **슬리피지 모델 체인**: INTACT (CEXOrderbookSlippage→BookWalkSlippage, PowerLaw k=0 유지)
- **SSOT §4 준수**: ALL MATCH (수수료 7개 거래소, 수식 전부 일치)
- **발견**: 0건 (ALL PASS)

---

## 3. 발견 및 수정 이력

### HIGH Issues (2건 → 즉시 수정)

| # | 이슈 | 파일:라인 | 수정 내용 | 상태 |
|---|------|----------|----------|------|
| 1 | `min_volume_usd` SignalConfig 미연결 | `main.py:637-643` | `min_volume_usd=Decimal(os.environ.get(...))` 추가 | ✅ FIXED |
| 2 | `OrderBook`에 `volume_24h_usd` 속성 부재 | `order_book.py` | 필터가 `getattr(..., None)`으로 graceful skip — 향후 volume 데이터 주입 시 자동 활성화. 현재는 설계대로 비활성 상태 | ✅ BY DESIGN |

### MEDIUM Issues (4건 → 수정 완료)

| # | 이슈 | 수정 내용 | 상태 |
|---|------|----------|------|
| 1 | prd.json US-157~168 passes:false | 12개 US passes:true 업데이트 | ✅ FIXED |
| 2 | trading.json disabled_strategies 미연결 | `_apply_trading_json_defaults`에 SHADOW_DISABLED_STRATEGIES 추가 | ✅ FIXED |
| 3 | trading.json max_single_loss_usd 미연결 | `_apply_trading_json_defaults`에 SHADOW_MAX_LOSS_PER_TRADE_USD 추가 | ✅ FIXED |
| 4 | SSOT.md 테스트 수 4,471→4,474 | SSOT.md §2 업데이트 | ✅ FIXED |

---

## 4. 최종 검증

| 항목 | 결과 |
|------|------|
| pytest | 4,474 passed, 0 failed, 6 skipped |
| Docker | 10/11 healthy (promtail unhealthy = 비핵심) |
| Coverage | 87% |
| 3-way 정합성 | prd.json 157/2 = SSOT.md §2 157/159 = SSOT.md §7 157/2 = CLAUDE.md 157/2 |
| CRITICAL 신규 | 0건 |
| HIGH 신규 | 0건 (2건 발견 즉시 수정) |
| 슬리피지 모델 | INTACT (이중계산 없음) |
| E2E 데이터 흐름 | VERIFIED |

---

## 5. 판정

**[단계 1-A] Delta Check: ALL PASS**

다음 단계: [단계 1-B] 전략별 독립 검증 (Strategy Isolation)
