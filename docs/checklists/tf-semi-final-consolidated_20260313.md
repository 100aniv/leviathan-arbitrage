# TF Semi-Final 통합 추적 문서

> **날짜**: 2026-03-13
> **판정**: FAIL — 회귀 개발 필요 (6개 회귀 Phase, 35개 US)
> **TF 리더**: Nayeon (TWICE)
> **원본 보고서**: `docs/checklists/tf-semi-final_20260313.md` (교차검증 상세)
> **원본 판정문**: `docs/checklists/tf-semi-final-verdict_20260313.md` (Nayeon 서명)

---

## TF 프로세스 결과 (단계 0~4)

### [단계 0] Smoke Test Gate — PARTIAL

| 항목 | 결과 | 비고 |
|------|------|------|
| pytest | FAIL | 1,505 passed / 1 failed (backoff jitter ±25%) → US-128 |
| Docker | PARTIAL | 핵심 8/10 healthy (auto-tuner, monitoring 재시작 반복) |
| 엔진 기동 | PASS | import + 초기화 성공 |

### [단계 1] 정합성 확인 — PARTIAL

| 항목 | 결과 | 비고 |
|------|------|------|
| SSOT↔prd.json | PASS | Phase 순서, US 상태 일치 |
| prd.json files↔실제 코드 | FAIL | 23개 파일 경로 불일치 → US-149 |
| CLAUDE.md↔SSOT.md | FAIL | 테스트 수/Phase 순서/다음 작업 3곳 stale → US-150 |
| 전략/콜렉터/Docker/API | PASS | 구조 일치 |

### [단계 2] 체크리스트 수립 — DONE

- Karina(아키텍트) + 7명 도메인 전문가 협의 → '완성 기준' 수립
- `docs/checklists/tf-semi-final_20260313.md` 작성 완료

### [단계 3] 교차 검증 — DONE (7명 전문가 병렬)

| 전문가 | 분야 | CRITICAL | HIGH | MEDIUM | LOW |
|--------|------|----------|------|--------|-----|
| Jeongyeon | 엔진 무결성 | 0 | 1 | 5 | 2 |
| Momo | 인프라 | 5 | 3 | 2 | 3 |
| Dahyun | 퀀트 수식 | 0 | 3 | 3 | 2 |
| Sana | 데이터/통계 | 1 | 1 | 1 | 0 |
| Mina | UI/UX | 0 | 0 | 3 | 9 |
| Security | 보안 | 3 | 4 | 3 | 2 |
| Karina | 정합성 | 0 | 0 | 2 | 1 |
| **합계** | | **9** | **12** | **19** | **19** |

### [단계 4] 최종 판정 — FAIL → 회귀 결정

- CRITICAL 9건 + HIGH 12건 해결 없이 TF Final 진출 불가
- 35개 신규 US 생성 (US-123~US-155)
- 6개 회귀 Phase 생성 (S1~S6)
- S1~S6 완료 후 TF Semi-Final **재검증** 필요

---

## 회귀 매핑 (S1~S6 → 원본 Phase 역추적)

> **핵심**: S1~S6은 "새로운 Phase"가 아니라, TF 검증에서 발견된 **원본 Phase(A~M)의 미비점 회귀 수정**.
> 각 회귀 Phase가 어떤 원본 Phase의 어떤 영역을 보완하는지 추적한다.

### S1: Security Hardening (US-123~128, US-152) — 7개 US

| 회귀 US | 원본 Phase | 원본 영역 | 발견 전문가 | 심각도 |
|---------|-----------|----------|------------|--------|
| US-152 | J-EXT W1 (US-105,106) | API 키 로테이션 + .gitignore | Momo + Security | CRITICAL |
| US-123 | J-EXT W1 (US-105,106) | 전 엔드포인트 JWT 인증 강제 | Security (C6,C7,C8) | CRITICAL |
| US-124 | J-EXT W1 (US-105) | JWT 시크릿 강화 + prod fail-fast | Security (C9) | HIGH |
| US-125 | Phase I (US-075) | Nginx IP whitelist + X-Forwarded-For | Momo (C5) + Security | CRITICAL+HIGH |
| US-126 | Phase E-1 (US-042~044) | Redis 인증 + dangerous commands | Momo (C2) | CRITICAL |
| US-127 | Phase D (US-037~041) | CSP 헤더 강화 | Mina (FAIL) | MEDIUM |
| US-128 | Phase I (US-074) | pytest backoff jitter 테스트 | Smoke Test | LOW |

**원본 의존성**: J-EXT Wave 1(보안 기반) + Phase E-1(인프라 모니터링) + Phase D(대시보드) + Phase I(거래소)
**실행 순서**: 최우선 (production safety)

---

### S2: Engine Wiring Completion (US-129~134, US-153~155) — 9개 US

| 회귀 US | 원본 Phase | 원본 영역 | 발견 전문가 | 심각도 |
|---------|-----------|----------|------------|--------|
| US-129 | Phase E-3 (US-049) | RiskGuardian PortfolioState 실제 값 | Jeongyeon (H1) | HIGH |
| US-130 | J-EXT W3 (US-114) | DynamicSizer 실행 경로 연결 | Jeongyeon + Sana | MEDIUM |
| US-131 | Phase K (US-084) + Phase M (US-094) | RegimeDetector + ONNX main.py 주입 | Jeongyeon | MEDIUM |
| US-132 | J-EXT W3 (US-115) | SlippageFeedbackLoop LegResult 필드 | Jeongyeon | MEDIUM |
| US-133 | J-EXT W3 (US-119) | AtomicOrderExecutor(IOC) main.py 연결 | Jeongyeon | MEDIUM |
| US-134 | J-EXT W3 (US-116,118) | TCA/Correlation ExecutionResult 필드 | Jeongyeon | LOW |
| US-153 | Phase B-5 (US-027~029) | 주문 중복 방지 (Idempotency Key) | QA MISSED | CRITICAL |
| US-154 | Phase E-3 (US-049) | RiskGuardian max_concurrent_positions | QA MISSED | HIGH |
| US-155 | Phase E-3 (US-049~050) | Graceful shutdown 오픈 포지션 정리 | QA MISSED | HIGH |

**원본 의존성**: Phase E-3(Production Readiness) + J-EXT W3(엔진 강화) + Phase K(Regime) + Phase M(ML) + Phase B-5(Multi-Leg)
**실행 순서**: S1 이후 (보안 먼저)

---

### S3: Infrastructure Hardening (US-135~139) — 5개 US

| 회귀 US | 원본 Phase | 원본 영역 | 발견 전문가 | 심각도 |
|---------|-----------|----------|------------|--------|
| US-135 | Phase A (US-002) + Phase E-2 | DB 스키마 3-way divergence 통합 | Momo (C3) | CRITICAL |
| US-136 | Phase SR + Phase 7.3h | .env MIN_EDGE_BPS 동기화 + PowerLaw k | Momo (C4) + Dahyun (H2~H4) | CRITICAL+HIGH |
| US-137 | Phase E-1 (US-042~044) | Nginx WS 포트 + 백업 자동재시작 | Momo (H5,H6) | HIGH |
| US-138 | Phase E-1 (US-044) | Alertmanager 연결 + Grafana datasource | Momo (H7) | HIGH |
| US-139 | Phase E-1 (US-042) | Docker 리소스 제한 + healthcheck | Momo | MEDIUM |

**원본 의존성**: Phase A(SSOT/인프라 기반) + Phase E-1(모니터링) + Phase E-2(Auto-Tuning) + Phase SR(Shadow Realism)
**실행 순서**: S1 이후, S2와 병렬 가능 (Nayeon 소견: 순차 실행이 안전)

---

### S4: Dashboard Completion (US-140~144) — 5개 US

| 회귀 US | 원본 Phase | 원본 영역 | 발견 전문가 | 심각도 |
|---------|-----------|----------|------------|--------|
| US-140 | Phase D (US-037~041) | API prefix 통일 + SWR key 수정 | Mina (FAIL) | MEDIUM |
| US-141 | Phase H (US-069~071) | System 페이지 mock 제거 → 실데이터 | Mina (PARTIAL) | MEDIUM |
| US-142 | Phase H (US-071) | Heatmap/Orderbook 175 심볼 연동 | Mina (PARTIAL) | MEDIUM |
| US-143 | Phase H (US-069) + J-EXT (US-108) | Strategy/Portfolio/EquityCurve mock 제거 | Mina (PARTIAL) | MEDIUM |
| US-144 | Phase D (US-041) | SWR v2 테스트 수정 + 모바일 최적화 | Mina (PARTIAL) | LOW |

**원본 의존성**: Phase D(Dashboard UX) + Phase H(Dashboard Integration) + J-EXT W2(UX 강화)
**실행 순서**: S1 이후 (CSP 의존), S2/S3 API 변경 반영 필요

---

### S5: Data Pipeline & Auto-Tuner (US-145~148) — 4개 US

| 회귀 US | 원본 Phase | 원본 영역 | 발견 전문가 | 심각도 |
|---------|-----------|----------|------------|--------|
| US-145 | Phase E-2 (US-045~046) | Auto-Tuner TimescaleDB async loader | Sana (FAIL) + Jeongyeon (H12) | HIGH |
| US-146 | Phase E-2 (US-045) | ScheduledTuner main.py 연결 | Sana | MEDIUM |
| US-147 | Phase E-3 (US-051,053) | Attribution TimescaleDB + materialized views | Sana (PARTIAL) | MEDIUM |
| US-148 | Phase SR (US-061) + Phase E-3 (US-050) | Shadow MDD 비율 + Rebalancer balance feed | Dahyun (H3) + Jeongyeon | HIGH+MEDIUM |

**원본 의존성**: Phase E-2(Auto-Tuning) + Phase E-3(Production Readiness) + Phase SR(Shadow Realism)
**실행 순서**: S2(엔진 wiring) + S3(DB 스키마) 완료 후

---

### S6: Documentation Sync (US-149~151) — 3개 US

| 회귀 US | 원본 Phase | 원본 영역 | 발견 전문가 | 심각도 |
|---------|-----------|----------|------------|--------|
| US-149 | Phase A (US-006) | prd.json 23개 파일 경로 수정 | Karina (FAIL) | MEDIUM |
| US-150 | Phase A (US-005) | CLAUDE.md 현행화 | Karina (FAIL) | MEDIUM |
| US-151 | Phase A (US-002) | SSOT.md 수식/체크 항목 코드 동기화 | Dahyun + Karina | MEDIUM |

**원본 의존성**: Phase A(인프라 재정비) — 문서의 원본이 Phase A에서 만들어짐
**실행 순서**: 최후 (S1~S5 모든 변경 완료 후 문서화)

---

## 회귀 의존성 그래프

```
S1 (Security) ─────────────────────────────┐
  │                                         │
  ├──→ S2 (Engine Wiring) ──┐               │
  │                          │              │
  ├──→ S3 (Infrastructure) ─┤              │
  │                          │              │
  ├──→ S4 (Dashboard) ◄─────┤  (S2/S3 API 변경 반영)
  │                          │              │
  │    S5 (Data Pipeline) ◄──┘  (S2 wiring + S3 DB 필요)
  │                                         │
  └──→ S6 (Documentation) ◄────────────────┘  (전부 완료 후)
```

**실행 순서**: S1 → (S2 ∥ S3) → S4 → S5 → S6
- S2/S3 병렬 가능하나 순차 실행이 안전 (Nayeon 소견)
- S4는 S1(CSP) + S2/S3(API 변경) 반영 필요
- S5는 S2(엔진 wiring) + S3(DB 스키마) 완료 후
- S6은 최후 (모든 변경 반영 후 문서 동기화)

---

## 원본 Phase ↔ 회귀 Phase 역참조표

> 어떤 원본 Phase에서 어떤 회귀가 발생했는지 역추적

| 원본 Phase | 원본 내용 | 회귀 Phase | 회귀 US | 미비 유형 |
|-----------|----------|-----------|---------|----------|
| Phase A | 인프라 재정비 | S3, S6 | US-135, US-149~151 | DB 스키마 divergence, 문서 stale |
| Phase B-5 | Multi-Leg Executor | S2 | US-153 | 주문 중복 방지 누락 |
| Phase D | Dashboard UX | S1, S4 | US-127, US-140, US-144 | CSP 미설정, API prefix, SWR |
| Phase E-1 | Production Monitoring | S1, S3 | US-126, US-137~139 | Redis 인증, Nginx, Alertmanager |
| Phase E-2 | Auto-Tuning | S3, S5 | US-135, US-145~146 | DB 스키마, NotImplementedError |
| Phase E-3 | Production Readiness | S2, S5 | US-129, US-148, US-154~155 | RiskGuardian, Rebalancer, Graceful shutdown |
| Phase SR | Shadow Realism | S3, S5 | US-136, US-148 | MIN_EDGE 불일치, MDD 단위 |
| Phase H | Dashboard Integration | S4 | US-141~143 | Mock 데이터 미제거 |
| Phase I | 거래소 완성도 | S1 | US-125, US-128 | Nginx whitelist, backoff test |
| Phase K | Regime Detection | S2 | US-131 | RegimeDetector main.py 미주입 |
| Phase M | ML Signal Pipeline | S2 | US-131 | ONNX Scorer main.py 미연결 |
| J-EXT W1 | 보안 | S1 | US-123~124, US-152 | 인증 미완료, 키 로테이션 |
| J-EXT W2 | 대시보드 UX | S4 | US-143 | EquityCurve spec 불일치 |
| J-EXT W3 | 엔진 강화 | S2 | US-130, US-132~134 | DynamicSizer/Slippage/IOC/TCA 미연결 |
| Phase 7.3h | MIN_EDGE 최적화 | S3 | US-136 | .env 값 불일치 |

---

## 진행 현황 (Phase별 Stage 추적)

| Phase | Stage A | Stage B | Stage C | Stage D | Stage E | 판정 |
|-------|---------|---------|---------|---------|---------|------|
| S1 | - | - | - | - | - | 대기 |
| S2 | - | - | - | - | - | 대기 |
| S3 | - | - | - | - | - | 대기 |
| S4 | - | - | - | - | - | 대기 |
| S5 | - | - | - | - | - | 대기 |
| S6 | - | - | - | - | - | 대기 |

> Stage 완료 시 PASS/FAIL + 날짜 기입. 전 Phase PASS 후 TF Semi-Final 재검증.

---

## TF Semi-Final 재검증 조건 (Nayeon 서명)

1. 35개 US (US-123~US-155) 전부 `passes:true` (증거 포함)
2. pytest 0 failures
3. Docker 전 서비스 healthy (재시작 반복 0건)
4. API 키 로테이션 완료 + `.env` git history 정리
5. 각 Phase는 5-Stage(A~E) 사이클 완주
6. 재검증 시 동일 7명 전문가 재소집 + 전체 4단계 재실행

**재검증 통과 → TF Final → Progressive Shadow 72H → Live**

---

## 변경 이력

| 날짜 | 변경 | 비고 |
|------|------|------|
| 2026-03-13 | 초판 작성 | TF Semi-Final FAIL → 6 Phase 회귀 결정 |
| 2026-03-13 | US-152~155 추가 | Nayeon verdict에서 API 키 로테이션 + QA MISSED 3건 |
