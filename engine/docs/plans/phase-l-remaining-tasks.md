# Phase L 미완 Tasks — 통합 Plan

**Date**: 2026-04-22
**Status**: DRAFT (Plan only, 시작 조건: Phase K 완료 + paper canary 24h PASS)
**Trigger**: Phase L (대시보드 재설계 + 운영 안정화) 4 task 미완. L-5 IRP는 이미 진행 중 (`f31d410`).

---

## 진행 상황

| Task | 상태 | 우선순위 | 시작 조건 |
|---|---|---|---|
| L-1 | 대시보드 UX 전면 재설계 | ⏳ | LOW | Live 거래 시작 후 (사용 빈도 증가) |
| L-2 | Settings hot-reload | ⏳ | MED | 24h paper canary 통과 후 |
| L-3 | OpenTelemetry 통합 | ⏳ | LOW | Live 안정 후 (관측성 강화) |
| L-4 | Zero-downtime 배포 | ⏳ | MED | Live 시작 후 (downtime 비용 발생) |
| L-5 | 운영 Runbook + IRP | 🟡 진행 | HIGH | (`f31d410` IRP 완료, runbook 기존 유지) |
| L-6 | Phase L Shadow + UAT | ⏳ | HIGH | L-1 완료 후 |

---

## L-2 Settings Hot-Reload (Plan)

**목표**: `engine.json` 변경 → 엔진 재시작 없이 적용. ConfigService.on_change 활용.

**현재 상태**: ConfigService (Day 4, `27eaa57`)에 `on_change` event 정의됨. 그러나 LiveMode/PaperMode가 subscribe 안 함.

**범위**:
1. ConfigService.on_change subscriber API 보강 (priority + filter)
2. LiveMode + PaperMode가 `min_spread_bps`, `max_holding_hours`, `disabled_strategies` 등 hot-reload-safe 필드 subscribe
3. file watcher (watchdog 또는 polling 30s) — engine.json 수정 감지
4. hot-reload-unsafe 필드 (exchanges, capital, mode) 변경 시 explicit warning + 재시작 안내

**Acceptance Criteria**:
- AC-1: engine 동작 중 `engine.json` `min_spread_bps` 30 → 50 변경 → 30s 내 strategy 적용
- AC-2: hot-reload-unsafe 필드 변경 시 warning + 무시
- AC-3: pytest 5053+ 유지

**견적**: 1.5일 (LOW-MED risk).

---

## L-3 OpenTelemetry 통합 (Plan)

**목표**: 분산 트레이싱. signal → strategy → executor → exchange 한 추적 단위.

**현재 상태**: Prometheus metrics 있음. trace_id 일부 코드에 있음 (예: order_router:133).

**범위**:
1. opentelemetry-sdk + opentelemetry-instrumentation-fastapi 추가
2. 핵심 hot path에 span 추가:
   - SignalGenerator.on_orderbook_update → Span "signal_generation"
   - Strategy.on_signal → Span "strategy_evaluation"
   - PaperExecutor.execute / AtomicExecutor.execute → Span "execution"
   - exchange adapter place_order → Span "exchange_call"
3. trace_id propagation (이미 부분 존재)
4. Jaeger 또는 OTLP exporter (docker-compose)

**Acceptance Criteria**:
- AC-1: 단일 trade의 trace tree 30+ span (signal→strategy→executor→exchange)
- AC-2: latency p50/p95/p99 trace 기반 측정
- AC-3: prometheus metrics 보존 (병행)

**견적**: 3-4일 (MED risk, 큰 dependency 추가).

---

## L-4 Zero-Downtime 배포 (Plan)

**목표**: Blue-Green 또는 Rolling 배포. live 거래 중단 없이 코드 업데이트.

**현재 상태**: docker-compose 기반 단일 engine 컨테이너. 배포 시 stop → pull → start (downtime ~30s).

**범위**:
1. Blue-Green 옵션:
   - engine_blue + engine_green 컨테이너
   - nginx upstream 수동 전환
   - position lock 동기화 (Redis)
2. Rolling 옵션 (추천):
   - engine 2-instance + dedup_lock으로 중복 주문 방지
   - leader election 또는 master/standby
   - graceful shutdown (SIGTERM → 진행 중 trade 완료 → exit)
3. live 모드 전용 — paper에서는 downtime 무관

**Acceptance Criteria**:
- AC-1: 새 버전 배포 중 live 거래 0건 중단
- AC-2: 진행 중인 trade는 이전 instance에서 완료 보장
- AC-3: 중복 주문 0건 (dedup_lock)

**견적**: 5일 (HIGH risk, 동시성 + 인프라).

**주의**: live 시작 후 진행. paper만이면 우선순위 낮음.

---

## L-1 대시보드 UX 전면 재설계 (Plan)

**목표**: 토스증권/업비트 UX 참조. 모바일 first.

**현재 상태**: Next.js 14 App Router 기반 8 페이지 (W3 `07bd710`). 기능 동작하나 UX 정제 필요.

**범위**:
1. 토스증권 디자인 시스템 분석 (색상, 타이포, 인터랙션)
2. 업비트 거래 화면 분석 (정보 밀도, 주문 panel)
3. 대시보드 8 페이지 재설계 (Overview, Positions, Strategies, Risk, Funding, System, Settings, Logs)
4. 모바일 (375px) 우선 + tablet (768px) + desktop (1280px+)
5. dark mode (OKLCH 기반, 이미 W3에 부분 구현)
6. 거래 관련 액션은 confirmation modal (실수 방지)

**Acceptance Criteria**:
- AC-1: Lighthouse mobile score ≥ 95
- AC-2: 8 페이지 모두 모바일/tablet/desktop 반응형
- AC-3: 콘솔 에러 0건
- AC-4: 사장님 UAT 승인

**견적**: 7-10일 (HIGH risk, design + 코드 + 테스트).

**주의**: Live 거래 시작 + 사용 빈도 증가 후가 효율 (사용 패턴 데이터 기반 디자인).

---

## L-6 Phase L Shadow + UAT (Plan)

L-1~L-4 완료 후 통합 검증:
1. 24h paper canary (L-2 hot-reload + L-3 trace 동작)
2. 사장님 UAT (L-1 대시보드)
3. Blue-Green 배포 dry-run (L-4 무다운타임)
4. ORR (Operations Readiness Review) 결재

---

## 우선순위 매트릭스

```
              HIGH 우선         MED 우선         LOW 우선
이번 주     | L-5 ✓ (IRP done) |                |
이번 달     |                  | L-2 hot-reload | L-1 dashboard
다음 분기   |                  | L-4 zero-down  | L-3 OTel
```

L-5 외 모든 Phase L task는 **paper canary 24h 통과 + Live 시작** 이후로 연기 권장. Live 자본 동작 확인 전 인프라 개선은 over-engineering.
