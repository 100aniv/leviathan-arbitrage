# TF Semi-Final 종합 검증 보고서

**날짜**: 2026-03-13
**검증 팀**: TWICE (9명 TF)
**모드**: READ-ONLY 정적 분석 + Smoke Test

---

## 1. Smoke Test Gate (단계 0)

| 항목 | 결과 | 비고 |
|------|------|------|
| pytest | **FAIL** (1505 passed, 1 failed) | `test_backoff_doubles_delay_each_call` — jitter(±25%) 추가 후 테스트 미업데이트 |
| Docker | **PARTIAL** (8/10 healthy) | auto-tuner, monitoring 재시작 반복 |
| 엔진 기동 | PASS | import 성공 |

---

## 2. 정합성 검증 (단계 1) — Karina

| 항목 | 결과 | 상세 |
|------|------|------|
| SSOT↔prd.json | PASS | Phase 순서, US 상태 일치 |
| prd.json files↔실제 코드 | **FAIL** | 23개 파일 경로 불일치 (모듈 이동/리네임 미반영) |
| CLAUDE.md↔SSOT.md | **FAIL** | 테스트 수, Phase 순서, 다음 작업 3곳 stale |
| 전략/콜렉터/Docker/API | PASS | 구조 일치 |

---

## 3. 교차 검증 (단계 3) — 도메인별

### 3-A. 엔진 무결성 — Jeongyeon

| 심각도 | 발견 | 설명 |
|--------|------|------|
| **HIGH** | RiskGuardian PortfolioState 항상 0 | `_build_risk_check_fn()`이 hardcoded zeros로 PortfolioState 생성 → 5/9 체크 무력화 |
| MEDIUM | SlippageFeedbackLoop dead wiring | LegResult에 expected_price/fill_price 필드 없음 → EMA 업데이트 불가 |
| MEDIUM | ML ONNX Scorer 미연결 | main.py에서 SignalGenerator에 전달 안됨 |
| MEDIUM | RegimeDetector 미연결 | main.py에서 생성은 되지만 SignalGenerator에 주입 안됨 |
| MEDIUM | AtomicOrderExecutor(IOC) 미연결 | US-119 구현됐으나 main.py에서 인스턴스화 안됨 |
| MEDIUM | DynamicSizer 실행 경로 단절 | 초기화되지만 signal routing에서 호출 안됨 |
| LOW | TCA/Correlation 필드 불일치 | ExecutionResult에 없는 필드 참조 |
| LOW | InventoryRebalancer 잔고 피드 미연결 | balance_feed=NOT_CONNECTED |

### 3-B. 인프라 — Momo (24항목 중 13 FAIL)

| 심각도 | 발견 | 설명 |
|--------|------|------|
| **CRITICAL** | API 키 평문 노출 | .env에 Binance/Upbit/Bithumb/Coinone/Telegram/GitHub 실키 |
| **CRITICAL** | Redis 인증 없음 | 포트 노출 상태에서 비밀번호 미설정 |
| **CRITICAL** | DB 스키마 3-way 불일치 | migration SQL vs init SQL vs 실 DB divergence |
| **CRITICAL** | MIN_EDGE_BPS 불일치 | root .env=5 vs engine/.env=3 |
| **CRITICAL** | Nginx IP whitelist 비활성 | `allow all;` 활성 상태 |
| HIGH | Nginx WebSocket 포트 오류 | 8000 vs 8001 |
| HIGH | 백업 자동재시작 안됨 | restart: "no" |
| HIGH | Alertmanager 미연결 | 알람 무음 |
| MEDIUM | 리소스 제한 엔진만 | Redis/TimescaleDB 무제한 |
| MEDIUM | Redis dangerous commands | CONFIG/FLUSHALL 비활성화 안됨 |
| LOW | Grafana datasource 충돌 | 중복 정의 |
| LOW | Auto-tuner synthetic only | 실데이터 미사용 |
| LOW | Promtail healthcheck 없음 | 모니터링 사각 |

### 3-C. 퀀트 수식 — Dahyun (8건 불일치)

| 심각도 | 발견 | 설명 |
|--------|------|------|
| **HIGH** | PowerLaw k 기본값 | SSOT k=0.0 vs 코드 기본값 k=5.0 (env fallback). 현재 미사용이나 위험 |
| **HIGH** | Shadow MDD 단위 | SSOT: (Peak-Current)/Peak 비율 vs shadow.py: Peak-Current 절대 USD |
| **HIGH** | MIN_EDGE_BPS 기본값 | SSOT=5 vs main.py 기본값=40. .env 누락 시 거래 8배 감소 |
| MEDIUM | Upbit 수수료 문서 | CLAUDE.md "0.05%"는 Maker. 코드 Taker 0.139% 올바름 |
| MEDIUM | ETH 네트워크 비용 | SSOT $5.60(메인넷) vs 코드 $0.06(Arbitrum) |
| MEDIUM | RiskGuardian 체크 항목 | SSOT 9-check 목록과 실제 코드 불일치 (코드가 더 진보적) |
| LOW | KillSwitch Tier 설명 | SSOT 트리거 조건 vs 코드 실행 단계 |
| LOW | CircuitBreaker 백오프 | SSOT 지수 백오프 vs 코드 고정 300s |

### 3-D. 데이터/통계 — Sana

| 항목 | 결과 | 설명 |
|------|------|------|
| Shadow 모드 구현 | PASS | 1,770줄 완전 구현 |
| Progressive Shadow 6단계 | PASS | 1H~72H 게이트 모두 구현 |
| PnL 계산 | PASS | 수수료/슬리피지/네트워크 정확 |
| 백테스트/Walk-Forward | PASS | WFA + RegimeWFA + ML A/B |
| **Auto-Tuner** | **FAIL** | TimescaleDB 통합에 NotImplementedError + ScheduledTuner main.py 미연결 |
| **Capital Allocator** | **FAIL** | DynamicSizer 실행 경로 단절 + Rebalancer 잔고 미연결 |
| Performance Attribution | PARTIAL | in-memory 전용, TimescaleDB 뷰 미생성 |

### 3-E. UI/UX — Mina (3 FAIL, 9 PARTIAL)

| 심각도 | 발견 | 설명 |
|--------|------|------|
| **FAIL** | CSP 헤더 미설정 | next.config.js에 Content-Security-Policy 없음 |
| **FAIL** | API prefix 불일치 | `/kill`, `/strategies` → `/api/v1/` prefix 누락 |
| **FAIL** | PnLChart SWR key | `/trading/pnl` key로 `getPnl()` 연결 → 캐시 불일치 |
| PARTIAL | System 페이지 | Docker 컨테이너 + Resource Usage 하드코딩 목업 |
| PARTIAL | Strategy 패널 | MOCK_STRATEGIES fallback 하드코딩 |
| PARTIAL | GlobalHeatmap | 3초마다 랜덤 mock 데이터 |
| PARTIAL | OrderbookView | 5개 FALLBACK_SYMBOLS만 선택 가능 |
| PARTIAL | EquityCurve | Sharpe/MDD/Calmar 미포함 (SSOT spec 불일치) |
| PARTIAL | Portfolio Daily Returns | placeholder |
| PARTIAL | TradeDetail 모바일 | w-80 고정폭, 모바일 미최적화 |
| PARTIAL | 테스트 TS 에러 40개 | SWR v2 mock isValidating 누락 |
| PASS | 13개 페이지 라우팅 | 전부 존재 |
| PASS | JWT 미들웨어 | 인증 정상 |
| PASS | 반응형 레이아웃 | 사이드바/그리드 정상 |

### 3-F. 보안 — Security Reviewer (3 CRITICAL, 4 HIGH)

| 심각도 | 발견 | 설명 |
|--------|------|------|
| **CRITICAL** | API 키 평문 .env | 실 거래소 키 + Telegram + GitHub 토큰 |
| **CRITICAL** | /kill, /strategies 인증 없음 | 누구든 kill switch 조작 가능 |
| **CRITICAL** | 약한 JWT 시크릿 | `leviathan-dev-secret-change-in-production` |
| HIGH | Nginx IP whitelist open | `allow all;` |
| HIGH | X-Forwarded-For 스푸핑 | 무조건 신뢰, 프록시 IP 검증 없음 |
| HIGH | risk/mode/status 인증 없음 | 엔진 상태 정보 무인증 노출 |
| HIGH | /metrics 무인증 | Prometheus 메트릭 전체 노출 |
| MEDIUM | DASHBOARD_PASSWORD 기본값 | prod에서 fail-fast 없음 |
| MEDIUM | bcrypt 미설치 시 SHA-256 fallback | prod에서 silent degradation |
| MEDIUM | CSP unsafe-inline/unsafe-eval | XSS 방어 무력화 |
| LOW | In-memory rate limiter | 재시작 시 리셋 |
| LOW | OCSP stapling 비활성 | prod TLS 불완전 |

---

## 4. 종합 통계

| 카테고리 | CRITICAL | HIGH | MEDIUM | LOW | PASS |
|----------|----------|------|--------|-----|------|
| 엔진 무결성 | 0 | 1 | 5 | 2 | - |
| 인프라 | 5 | 3 | 2 | 3 | 11 |
| 퀀트 수식 | 0 | 3 | 3 | 2 | 20+ verified |
| 데이터/통계 | 1 | 1 | 1 | 0 | 4 |
| UI/UX | 0 | 0 | 3 | 9 | 13+ |
| 보안 | 3 | 4 | 3 | 2 | 10+ |
| 정합성 | 0 | 0 | 2 | 1 | 3 |
| **합계** | **9** | **12** | **19** | **19** | - |

---

## 5. 판정

**TF Semi-Final: FAIL — 신규 Phase 생성 후 개발 회귀 필요**

CRITICAL 9건 + HIGH 12건 해결 없이 TF Final 진출 불가.

### 신규 Phase 로드맵

| Phase | 이름 | 주요 내용 | US 수 |
|-------|------|----------|-------|
| S1 | Security Hardening | API 키 관리, 인증 보강, JWT, Nginx, Redis | 6 |
| S2 | Engine Wiring | RiskGuardian, DynamicSizer, RegimeDetector, ONNX, IOC | 6 |
| S3 | Infrastructure | DB 스키마, 백업, Alertmanager, 리소스, .env 동기화 | 5 |
| S4 | Dashboard Completion | CSP, mock 제거, API prefix, 모바일, 테스트 | 5 |
| S5 | Data Pipeline | Auto-Tuner TimescaleDB, Attribution DB, ScheduledTuner | 4 |
| S6 | Documentation Sync | prd.json 경로, CLAUDE.md, SSOT 수식/체크 동기화 | 3 |
| **합계** | | | **29** |
