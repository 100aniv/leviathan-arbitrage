# Phase K PLAN — 종합 테스트 사이클 + Live 전환

> 작성: leviathan-planner (Stage A) | 2026-04-02
> 근거: `/Users/100aniv/.claude/plans/fancy-strolling-pine.md` §Phase K
> 선행: Phase J PASS (WFA + ML A/B + Sharpe sqrt(8760) + Coinone CB fix)
> Entry Gate: Karina CONDITIONAL PASS (SSOT line 6 drift 수정 + US-358 신규 추가)

---

## 0. Entry Gate 점검 결과

| 항목 | 상태 | 비고 |
|------|------|------|
| SSOT.md line 6 drift | FIXED | Phase J 완료 반영 |
| US-358 신규 추가 | DONE | live.py record_execution CRITICAL |
| PRD total | 364 | 353 passes:true / 11 passes:false |
| Tests | 5,379 passed / 0 failed / 12 skipped | Phase J 기준 |

---

## 1. Phase K 개요

**목표**: Backtest → Paper → Live 3단계 종합 테스트 사이클 완성 + 첫 Live 체결

**범위**: 15 exchanges × 7 strategies, 신규 US 10개 (US-332, US-334, US-358~365)

**완료 기준**:
- pytest 0 failures
- Shadow 13항목 복합지표 PASS
- LiveGate Preflight 10/10 PASS
- 첫 Live 체결 확인 (로그/메트릭 증거)

---

## 2. US 목록 (passes:false 11개 중 Phase K 담당)

| US | 제목 | 우선순위 | 의존 |
|----|------|----------|------|
| US-358 | live.py `_execute_trade_request` → `record_execution(mode='live')` | CRITICAL | 없음 |
| US-334 | engine.json capital 필드 (spot_usd=20, futures_usd=30, spot_krw=28000) | HIGH | 없음 |
| US-332 | Paper 24H 무중단 (Sharpe≥2.0, crash=0) | HIGH | US-334 후 |
| US-359 | config.py API 키 18개 필드 + DI 배선 | HIGH | US-358 후 |
| US-361 | POST /api/backtest/start + BacktestResult meta 5필드 | HIGH | 없음 |
| US-360 | Tier4 어댑터 5개 (MEXC/Gate.io/BingX/LBank/OrangeX) | MEDIUM | US-359 후 |
| US-362 | OHLCVDownloader (Binance OHLCV → synthetic orderbook) | MEDIUM | US-361 후 |
| US-363 | POST /api/paper/start 엔드포인트 | MEDIUM | US-361 후 |
| US-364 | iMessage 승인 게이트 | MEDIUM | US-363 후 |
| US-055 | LiveGate Preflight 10항목 PASS | HIGH | US-364 후 |
| US-056 | 첫 Live 체결 | HIGH | US-055 후 |

---

## 3. 실행 순서 (배치별)

### Batch 0 — 선행 필수 (순차)

```
US-358 → US-334 → US-332
```

- **US-358**: `live.py` `_execute_trade_request` 내 `record_execution` 누락 — 모든 Live 데이터 기록의 전제
  - 참조: `shadow.py:1603` 패턴 (`self._market_recorder.record_execution(execution_result, mode='live')`)
  - WIRING: 생성(RecorderInst) → 주입(LiveMode.__init__) → 호출(_execute_trade_request 말미)
- **US-334**: `engine/config/engine.json` capital 블록 추가
  - `{"spot_usd": 20, "futures_usd": 30, "spot_krw": 28000}`
  - WIRING: 생성(JSON 필드) → 주입(config.py 파싱) → 호출(CapitalManager 읽기)
- **US-332**: Paper 24H 무중단 Shadow 실행
  - 선행: US-334 capital 필드 확정 후
  - **PRIMARY 완료 기준 (필수)**: crash=0, API rate limit 위반 없음, OOM 없음, 13항목 복합지표 PASS
  - **SECONDARY 기준 (달성 시 보너스)**: Sharpe≥2.0 (sqrt(8760) 연간화 기준)
  - ⚠️ MUST FIX 반영: 24H 데이터 Sharpe는 통계적 유의성 낮음 → 안정성 검증이 주목적

### Batch 1 — 독립 병렬

```
US-359 ‖ US-361
```

- **US-359**: `config.py` API 키 18개 필드 추가 + Engine DI 배선
  - 대상: MEXC, Gate.io, BingX, LBank, OrangeX 각 2개 (key/secret) + 기타 3개
  - WIRING: 생성(.env 필드) → 주입(Config 객체) → 호출(Tier4AdapterFactory)
- **US-361**: `POST /api/backtest/start` + `BacktestResult` meta 5필드
  - meta 필드: `total_trades`, `sharpe`, `mdd`, `win_rate`, `profit_factor`
  - WIRING: 생성(BacktestResult 모델) → 주입(router 등록) → 호출(backtest endpoint)

### Batch 2 — 의존 병렬

```
US-360 (after US-359) ‖ US-362 (after US-361) ‖ US-363 (after US-361)
```

- **US-360**: Tier4 어댑터 5개
  - ⚠️ MUST FIX 반영: API 키 없음 → 런타임 실연결 불가. 완료 기준 = mock 단위테스트 PASS
  - 패턴: `NativeAdapterBase` 상속, Bitget 참조 (`engine/src/infra/exchange/native_bitget.py`)
  - 순서: MEXC → Gate.io → BingX → LBank → OrangeX
  - WIRING: 생성(NativeXxxAdapter) → 주입(_NATIVE_ADAPTER_MAP 등록) → 호출(mock HTTP 단위테스트 PASS)
  - **Phase K 완료 기준**: 5개 어댑터 파일 존재 + mock 단위테스트 통과 + _NATIVE_ADAPTER_MAP 등록 확인
  - API 키 발급 시 즉시 Live 가능 상태 유지 (Phase L에서 실연결 검증)
- **US-362**: `OHLCVDownloader`
  - synthetic orderbook: `bid = mid * 0.9995`, `ask = mid * 1.0005`, `source='ohlcv_synthetic'`
  - WIRING: 생성(OHLCVDownloader) → 주입(BacktestEngine) → 호출(download_ohlcv)
- **US-363**: `POST /api/paper/start` 엔드포인트
  - WIRING: 생성(PaperStartRequest 모델) → 주입(router) → 호출(paper mode init)

### Batch 3 — 통합

```
US-364 (after US-363)
```

- **US-364**: iMessage/Telegram 승인 게이트
  - **기본 채널**: Telegram DevBot `/approve K-L` 명령 (이미 구현된 `/approve` 플로우 활용)
  - **알림 전용**: iMessage = 결과 공유 전용 (AppleScript 제거, Linux/Docker 호환성)
  - ⚠️ MUST FIX 반영: AppleScript macOS 종속성 제거 → Telegram DevBot 단일 승인 채널
  - WIRING: 생성(ApprovalGate) → 주입(LiveMode.start() 진입 시 fail-closed) → 호출(Telegram /approve)

### Batch 4 — 최종 (순차)

```
US-055 → US-056
```

- **US-055**: LiveGate Preflight 10항목
  - 항목: API 연결, 잔고, KillSwitch, CircuitBreaker, Guardian, capital 한도, slippage guard, orderbook depth, network latency, Telegram 알림
- **US-056**: 첫 Live 체결
  - ⚠️ MUST FIX 반영: 로그 1건만으로 부족 → 체결 품질 검증 필수
  - 완료 기준: 주문 ID 조회 성공 + filled_qty > 0 + 잔고 반영 확인 + `mode='live'` record_execution 존재
  - 부분체결 처리: filled_qty < ordered_qty 시 잔여 수량 취소 또는 재주문 로직 명시
  - 실패 경로: 체결 실패 시 `mode='live_failed'` 기록 + KillSwitch 트리거 없음 확인

---

## 4. 핵심 기술 결정

| 결정 | 내용 | 근거 |
|------|------|------|
| record_execution 패턴 | `shadow.py:1603` 복사 | 동일 RecorderInst 재사용 |
| Tier4 어댑터 기반 | `native_bitget.py` 패턴 | NativeAdapterBase 준수 |
| OHLCV synthetic | `bid=mid*0.9995, ask=mid*1.0005` | ±0.05% 대칭 spread (⚠️ triangular fee=0.06% 이상 → backtest 신호 0건 가능, architecture validation 용도) |
| iMessage gate | Telegram DevBot 단일 승인 채널 | AppleScript 제거 (Linux/Docker 비호환), iMessage=알림 전용 |
| capital 단위 | spot_usd/futures_usd(USD), spot_krw(KRW) | 퍼센티지 기반 한도 연동 |

---

## 5. 위험 요소

| 위험 | 수준 | 완화 |
|------|------|------|
| Tier4 API 키 미보유 | HIGH | US-360 단위 테스트는 mock 사용, 실연결은 선택적 |
| ~~iMessage AppleScript 종속성~~ (제거됨) | RESOLVED | Telegram DevBot 단일 채널로 대체 |
| triangular synthetic data 한계 | HIGH | ±0.05% spread < 수수료 → trades=0 예상. backtest=architecture 검증용, 실질검증=Paper(WS) |
| Paper 24H crash 재발 | HIGH | US-334 capital 확정 후 US-332 시작 |
| Live 첫 체결 실패 | HIGH | LiveGate 10/10 PASS 없이 US-056 진입 금지 |

---

## 6. WIRING AC 체크포인트 요약

각 US의 passes:true 조건은 코드 존재가 아닌 런타임 호출 증거:

| US | 생성 | 주입 | 호출 증거 |
|----|------|------|----------|
| US-358 | RecorderInst | LiveMode.__init__ | live 로그 `record_execution mode=live` |
| US-334 | JSON 필드 | config.py 파싱 | CapitalManager 읽기 로그 |
| US-332 | — | — | 24H Shadow Sharpe≥2.0 메트릭 파일 |
| US-359 | .env 18필드 | Config 객체 | AdapterFactory 호출 로그 |
| US-360 | NativeXxxAdapter | AdapterRegistry | connect/subscribe 로그 |
| US-361 | BacktestResult | router | POST /api/backtest/start 200 응답 |
| US-362 | OHLCVDownloader | BacktestEngine | download_ohlcv 호출 로그 |
| US-363 | PaperStartRequest | router | POST /api/paper/start 200 응답 |
| US-364 | ApprovalGate | LiveMode.start() fail-closed | Telegram `/approve K-L` 응답 수신 로그 |
| US-055 | LiveGate (6체크+4추가) | preflight | Preflight PASS 체크리스트 파일 |
| US-056 | — | — | 주문 ID 조회 + filled_qty>0 + 잔고 반영 확인 |

---

## 7. 완료 기준 (Phase K Exit Gate)

1. `cd engine && python -m pytest tests/ -x --tb=short` → 0 failures
2. Shadow 13항목 복합지표 PASS (MDD<5%, PF>1.2, 전략별 trade≥1)
3. `python -m src.workflow.cli check_all` → 9/9 OK
4. LiveGate Preflight 10/10 PASS 로그 파일 존재
5. Live 체결 로그 1건 (`record_execution mode='live'`) — 물리적 증거 필수
6. SSOT.md Phase K → L 업데이트 + git push
