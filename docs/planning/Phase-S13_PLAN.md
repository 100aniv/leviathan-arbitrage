# Phase S13 PLAN — Shadow Loss Prevention (16 US)

> **Phase**: S13 | **US**: US-221~233, US-235~237 | **Tests baseline**: 4,795 passed
> **TF SF 2차 FAIL 근본 원인**: futures stale 진입 17건(-$850), spot_futures WR 42%, funding_rate WR 6.7%
> **목표**: Shadow 2H PnL > $0, loss_capped 0건, stale 진입 0건

---

## 1. 배치 구성 (파일 충돌 기준, 최대 5 US/배치)

### Batch 1: CRITICAL 버그 + Dead Wiring (P0 전제조건)
| US | 제목 | 담당 | 주요 변경 파일 |
|----|------|------|---------------|
| US-226 | CRITICAL 버그 5개 수정 | Yujin | `main.py`, `core/engine.py`, `execution/paper.py` |
| US-236 | Dead Wiring 수정 | Gaeul | `main.py` (`_position_manager=None`, `_redis` 오타) |

**의존성**: 없음 (다른 모든 배치의 전제조건)
**파일 충돌**: `main.py` 공유 — Yujin이 CRITICAL 버그, Gaeul이 Dead Wiring 담당. 영역 분리 가능 (US-226은 init 메서드 내부 로직, US-236은 속성 할당 라인)
**테스트**: 기존 테스트 깨짐 0건 확인 + `main.py` 관련 통합 테스트 추가 (Wonyoung)

### Batch 2: Stale 감지 + 전략별 Circuit Breaker (P1 근본 원인)
| US | 제목 | 담당 | 주요 변경 파일 |
|----|------|------|---------------|
| US-221 | Stale 감지 강화 | Yujin | `core/stale_detector.py`, `core/real_signal_producer.py` |
| US-222 | 전략별 Circuit Breaker | Gaeul | `risk/circuit_breaker.py`, `strategies/base.py`, `strategies/manager.py` |
| US-223 | 전략 비활성화 메커니즘 | Gaeul | `strategies/manager.py`, `core/config.py` |
| US-224 | Loss Cap 차등 적용 | Yujin | `modes/shadow.py`, `execution/paper.py`, `infra/metrics.py` |
| US-225 | Outlier 필터 강화 | Wonyoung | `core/real_signal_producer.py`, `risk/slippage.py` |

**의존성**: Batch 1 완료 필수 (main.py 안정화 후)
**파일 충돌**: `real_signal_producer.py` (US-221 + US-225) — US-221은 stale check 로직, US-225는 outlier filter. 함수 단위 분리 가능
**테스트**: 각 US별 단위테스트 + stale 시나리오 통합 테스트 (Wonyoung)

### Batch 3: 4계층 방어 시스템 (P2 방어 강화)
| US | 제목 | 담당 | 주요 변경 파일 |
|----|------|------|---------------|
| US-227 | 4계층 Stale 감지 체계 | Yujin | `core/stale_detector.py` (신규 계층 추가) |
| US-228 | CB 상태머신 고도화 | Gaeul | `risk/circuit_breaker.py` |
| US-229 | 시그널 필터 파이프라인 | Yujin | `core/signal.py`, `core/multi_signal.py` |
| US-230 | 스프레드 필터 강화 | Gaeul | `core/real_signal_producer.py`, `strategies/cross_exchange.py` |

**의존성**: Batch 2 완료 필수 (US-221의 stale_detector 위에 US-227 4계층 구축)
**파일 충돌**: `stale_detector.py` (US-227 단독), `circuit_breaker.py` (US-228 단독) — 충돌 없음
**테스트**: 계층별 단위테스트 + 전체 방어 체인 통합 테스트 (Wonyoung)

### Batch 4: 전략별 강화 + PositionRegistry (P3)
| US | 제목 | 담당 | 주요 변경 파일 |
|----|------|------|---------------|
| US-231 | stat_arb 강화 | Yujin | `strategies/statistical_arb.py` |
| US-232 | PositionRegistry | Gaeul | `execution/atomic.py` (신규 `core/position_registry.py`) |
| US-233 | futures 전략 강화 | Yujin | `strategies/futures_futures.py`, `strategies/spot_futures.py` |
| US-235 | cross_exchange 강화 | Gaeul | `strategies/cross_exchange.py` |

**의존성**: Batch 2 (CB 차등) + Batch 3 (stale 4계층) 완료 후 전략에 적용
**파일 충돌**: 전략 파일 각각 단독 — 충돌 없음. `atomic.py`+`position_registry.py` Gaeul 단독
**테스트**: 전략별 단위테스트 + 시나리오 테스트 (Wonyoung)

### Batch 5: 대시보드 + 레짐 게이트 (P3-P4)
| US | 제목 | 담당 | 주요 변경 파일 |
|----|------|------|---------------|
| US-237 | 대시보드 로그인/CORS/CSP | Rei | `dashboard/src/app/login/page.tsx`, `dashboard/next.config.js`, `engine/src/api/server.py` |

**의존성**: Batch 1 완료 (Dead Wiring으로 API 컨텍스트 정상화 후)
**파일 충돌**: 대시보드 전용 — 엔진 배치와 완전 독립. Batch 2~4와 병렬 가능
**테스트**: E2E 로그인 플로우 + CORS 헤더 검증 (Rei)

---

## 2. 실행 순서 + 의존성 다이어그램

```
Batch 1 (P0: CRITICAL + Dead Wiring)
  |
  ├──────────────────────┐
  v                      v
Batch 2 (P1: 근본 원인)   Batch 5 (P4: 대시보드) ← 병렬 가능
  |
  v
Batch 3 (P2: 방어 강화)
  |
  v
Batch 4 (P3: 전략별 강화)
  |
  v
[pytest 전체 + Shadow 10min 검증]
```

**예상 소요**: Batch당 ~2시간 (TeamCreate 6명 기준), 전체 ~10시간
**병렬화**: Batch 5는 Batch 1 완료 직후 Batch 2와 동시 시작 가능

---

## 3. TeamCreate 팀원 배정

| 팀원 | 역할 | Batch 1 | Batch 2 | Batch 3 | Batch 4 | Batch 5 |
|------|------|---------|---------|---------|---------|---------|
| **Yujin** | 백엔드 #1 (core/execution) | US-226 | US-221, US-224 | US-227, US-229 | US-231, US-233 | - |
| **Gaeul** | 백엔드 #2 (risk/strategy) | US-236 | US-222, US-223 | US-228, US-230 | US-232, US-235 | - |
| **Wonyoung** | 테스트 엔지니어 | B1 검증 | US-225 + B2 테스트 | B3 테스트 | B4 테스트 | - |
| **Rei** | 대시보드 | - | - | - | - | US-237 |
| **Leeseo** | 백엔드 보조 | B1 리뷰 | B2 리뷰 | B3 리뷰 | B4 리뷰 | - |
| **Liz** | 백엔드 보조 | B1 리뷰 | B2 리뷰 | B3 리뷰 | B4 리뷰 | - |

**원칙**:
- Yujin: core/, execution/, modes/ 중심 (stale, signal, shadow)
- Gaeul: risk/, strategies/, main.py 중심 (CB, strategy manager, position)
- Wonyoung: 테스트 작성 + US-225 (outlier 필터는 테스트 관점 강함)
- Rei: 대시보드 전용 (Batch 5만, 엔진과 독립)
- Leeseo/Liz: 코드 리뷰 + Batch 간 충돌 검증

---

## 4. US별 변경 파일 + 테스트 전략

### Batch 1

#### US-226: CRITICAL 버그 5개 수정
- **변경**: `main.py` (init 로직), `core/engine.py` (이벤트 라우팅), `execution/paper.py` (fill 로직)
- **테스트**: 기존 4,795 테스트 전수 통과 + CRITICAL 시나리오별 재현 테스트 5건
- **AC**: pytest 0 failures, 각 CRITICAL 버그 재현→수정→테스트 green

#### US-236: Dead Wiring 수정
- **변경**: `main.py` (`_position_manager=None` → 실제 인스턴스, `_redis_client` 오타 수정, `context.position_manager` 할당 검증)
- **테스트**: `test_main.py` — Engine 인스턴스 생성 후 `_position_manager is not None` 확인, `_redis_client` 속성 존재 확인
- **AC**: `Engine.__init__()` 후 dead reference 0건, 통합 테스트에서 position_manager 실 호출 성공

### Batch 2

#### US-221: Stale 감지 강화
- **변경**: `core/stale_detector.py` (임계값 조정, 감지 빈도 증가), `core/real_signal_producer.py` (stale check 호출 추가)
- **테스트**: stale orderbook mock → 감지 성공, false positive rate < 1%
- **AC**: futures stale 진입 시뮬레이션에서 100% 차단

#### US-222: 전략별 Circuit Breaker
- **변경**: `risk/circuit_breaker.py` (전략별 인스턴스 팩토리), `strategies/base.py` (CB 인터페이스), `strategies/manager.py` (전략별 CB 등록)
- **테스트**: 전략 A CB OPEN 시 전략 B는 CLOSED 유지 확인
- **AC**: 각 전략 독립 CB, 글로벌 CB와 전략별 CB 이중 체크

#### US-223: 전략 비활성화 메커니즘
- **변경**: `strategies/manager.py` (disable/enable API), `core/config.py` (DISABLED_STRATEGIES env var)
- **테스트**: 런타임 비활성화 → 시그널 무시 확인, 재활성화 → 정상 동작
- **AC**: `DISABLED_STRATEGIES=stat_arb,funding_rate` 시 해당 전략 시그널 0건

#### US-224: Loss Cap 차등 적용
- **변경**: `modes/shadow.py` (전략별 loss_cap 설정), `execution/paper.py` (loss_cap 체크), `infra/metrics.py` (loss_cap 카운터)
- **테스트**: 전략별 loss_cap 초과 시 해당 전략만 중단, 타 전략 정상
- **AC**: loss_capped 이벤트 발생 시 전략 식별 가능, Prometheus 메트릭 노출

#### US-225: Outlier 필터 강화
- **변경**: `core/real_signal_producer.py` (z-score 기반 outlier 필터), `risk/slippage.py` (극단 스프레드 거부)
- **테스트**: 정상 스프레드 통과, 3-sigma 초과 스프레드 거부
- **AC**: Bithumb stale data 패턴 (2-10x 가격 차이) 100% 필터링

### Batch 3

#### US-227: 4계층 Stale 감지 체계
- **변경**: `core/stale_detector.py` (L1: timestamp, L2: cross-exchange, L3: rate-of-change, L4: ML anomaly)
- **테스트**: 각 계층 단독 테스트 + 계층 조합 테스트 (4C2=6 조합)
- **AC**: 4계층 중 2개 이상 trigger 시 blacklist, false positive < 0.5%

#### US-228: CB 상태머신 고도화
- **변경**: `risk/circuit_breaker.py` (HALF_OPEN 테스트 카운트, 자동 cooldown 조정, 전략별 임계값)
- **테스트**: 상태 전이 전수 테스트 (CLOSED→OPEN→HALF_OPEN→CLOSED, HALF_OPEN→OPEN 롤백)
- **AC**: 상태머신 전이 100% 커버, cooldown 동적 조정 (연속 trip 시 증가)

#### US-229: 시그널 필터 파이프라인
- **변경**: `core/signal.py` (FilterChain 패턴), `core/multi_signal.py` (필터 등록)
- **테스트**: 필터 체인 순서 테스트, 각 필터 단독 + 조합
- **AC**: stale→outlier→spread→regime 순서 필터링, 각 필터 bypass 가능

#### US-230: 스프레드 필터 강화
- **변경**: `core/real_signal_producer.py` (동적 스프레드 임계값), `strategies/cross_exchange.py` (거래소별 min_spread)
- **테스트**: 정상 스프레드 통과, fake spread (Bithumb 패턴) 거부
- **AC**: Korean exchange fake spread 100% 필터, 정상 스프레드 95%+ 통과

### Batch 4

#### US-231: stat_arb 강화
- **변경**: `strategies/statistical_arb.py` (cross-asset 전용, mean-reversion 제거, z-score 임계값 조정)
- **테스트**: 양성 시나리오 (cross-asset pair) + 음성 시나리오 (자기상관 없는 pair)
- **AC**: stat_arb WR > 55%, 독립 Shadow 5min 테스트에서 PnL >= 0

#### US-232: PositionRegistry
- **변경**: 신규 `core/position_registry.py`, `execution/atomic.py` (registry 연동)
- **테스트**: 동시 포지션 추적, 중복 진입 방지, 포지션 한도 초과 거부
- **AC**: 전략별 동시 포지션 수 제한, registry 조회 O(1)

#### US-233: futures 전략 강화
- **변경**: `strategies/futures_futures.py` (stale guard 연동), `strategies/spot_futures.py` (WR 개선 로직)
- **테스트**: futures stale 시나리오 → 진입 차단, spot_futures 시그널 정확도
- **AC**: futures stale 진입 0건, spot_futures WR > 55%

#### US-235: cross_exchange 강화
- **변경**: `strategies/cross_exchange.py` (latency_arb 병합 최적화, 거래소별 fee 반영)
- **테스트**: cross_exchange 시그널 생성 + fee 차감 후 양성 스프레드만 통과
- **AC**: fee 반영 후 음수 스프레드 진입 0건

### Batch 5

#### US-237: 대시보드 로그인/CORS/CSP
- **변경**: `dashboard/src/app/login/page.tsx` (UI 개선), `dashboard/next.config.js` (CSP 헤더), `engine/src/api/server.py` (CORS 설정)
- **테스트**: 로그인 성공/실패 시나리오, CORS preflight 검증, CSP 헤더 존재 확인
- **AC**: 로그인 후 JWT 쿠키 설정, 잘못된 origin 요청 거부, CSP 헤더 모든 응답에 포함

---

## 5. Shadow 10min 검증 포인트

### 전제 조건
- Docker 컨테이너 전체 healthy (`docker compose up -d && docker compose ps`)
- pytest 전체 통과 (`cd engine && python -m pytest tests/ -x --tb=short`)
- `ENGINE_ENV=dev`, `DATA_MODE=shadow`

### 검증 체크리스트

| # | 검증 항목 | 임계값 | 측정 방법 |
|---|----------|--------|----------|
| 1 | **Crash** | 0건 | 프로세스 종료 없음, 미처리 예외 0건 |
| 2 | **Stale 진입** | 0건 | `stale_entry_blocked` Prometheus 카운터 > 0 (차단 증거), `stale_entry_passed` = 0 |
| 3 | **Loss Capped** | 0건 | `loss_capped_total` 카운터 = 0 |
| 4 | **PnL** | >= $0 | `shadow_pnl_total` 메트릭 >= 0 |
| 5 | **전략별 WR** | 각 > 50% | `strategy_win_rate{strategy=X}` > 0.5 (활성 전략 전체) |
| 6 | **CB 상태** | 전체 CLOSED | `circuit_breaker_state{strategy=X}` = CLOSED (10min 종료 시점) |
| 7 | **Dead Wiring** | 0건 | `position_manager` 실 호출 로그 존재, `_redis_client` 에러 0건 |
| 8 | **시그널 필터** | 동작 확인 | `signal_filtered_total{reason=stale\|outlier\|spread}` > 0 (필터 실 동작 증거) |
| 9 | **PositionRegistry** | 정합성 | `position_registry_count` == 실제 활성 포지션 수 |
| 10 | **대시보드** | 접속 가능 | `curl -s http://localhost:3000/login` HTTP 200 |

### Shadow 실행 커맨드
```bash
cd engine && timeout 600 python -m src.main
```

### FAIL 시 대응
- Crash → 로그 분석 후 해당 Batch 재작업
- Stale 진입 > 0 → US-221/US-227 재검토
- Loss Capped > 0 → US-224 임계값 조정
- PnL < 0 → 전략별 breakdown 확인 후 해당 전략 US 재작업
- CB OPEN → US-222/US-228 cooldown 파라미터 조정

---

## 6. 가드레일

### Must Have
- pytest 전체 통과 (baseline 4,795 + 신규 테스트)
- Shadow 10min crash 0건
- Stale 진입 0건
- 기존 7개 전략 모두 정상 등록 (CexDex 제외)

### Must NOT Have
- 이중 슬리피지 적용 (PaperExecutor에 PowerLaw 절대 금지)
- `ENGINE_ENV=development` 사용 (dev|staging|prod|test만)
- auto-symbols `min_exchanges=7` (3 필수)
- 전략 아키텍처 재설계 (기존 구조 내 강화만)
- ccxt 의존성 추가

---

## 7. 성공 기준 (Phase S13 완료 조건)

1. 16개 US 전체 구현 완료 + 단위테스트 통과
2. pytest 전체 통과 (0 failures)
3. Shadow 10min: crash 0, stale 진입 0, loss_capped 0, PnL >= $0
4. 코드 리뷰 통과 (BLACKPINK: Jennie + Jisoo)
5. SSOT.md 업데이트 (Phase S13 완료 기록)
6. git push + 텔레그램 알림

---

## 부록 A: 레짐 게이트 참고

- `src/tuning/regime_detector.py` 존재 (Phase K에서 구현)
- US-229 시그널 필터 파이프라인에서 regime gate 통합 가능
- `src/core/signal.py`의 `SignalGenerator`에 regime check 추가

## 부록 B: 파일 변경 매트릭스

| 파일 | Batch 1 | Batch 2 | Batch 3 | Batch 4 | Batch 5 |
|------|---------|---------|---------|---------|---------|
| `main.py` | US-226, US-236 | - | - | - | - |
| `core/stale_detector.py` | - | US-221 | US-227 | - | - |
| `core/real_signal_producer.py` | - | US-221, US-225 | US-230 | - | - |
| `core/signal.py` | - | - | US-229 | - | - |
| `core/multi_signal.py` | - | - | US-229 | - | - |
| `core/config.py` | - | US-223 | - | - | - |
| `risk/circuit_breaker.py` | - | US-222 | US-228 | - | - |
| `risk/slippage.py` | - | US-225 | - | - | - |
| `strategies/base.py` | - | US-222 | - | - | - |
| `strategies/manager.py` | - | US-222, US-223 | - | - | - |
| `strategies/statistical_arb.py` | - | - | - | US-231 | - |
| `strategies/cross_exchange.py` | - | - | US-230 | US-235 | - |
| `strategies/futures_futures.py` | - | - | - | US-233 | - |
| `strategies/spot_futures.py` | - | - | - | US-233 | - |
| `execution/paper.py` | US-226 | US-224 | - | - | - |
| `execution/atomic.py` | - | - | - | US-232 | - |
| `modes/shadow.py` | - | US-224 | - | - | - |
| `infra/metrics.py` | - | US-224 | - | - | - |
| `core/position_registry.py` (신규) | - | - | - | US-232 | - |
| `dashboard/src/app/login/page.tsx` | - | - | - | - | US-237 |
| `dashboard/next.config.js` | - | - | - | - | US-237 |
| `engine/src/api/server.py` | - | - | - | - | US-237 |
