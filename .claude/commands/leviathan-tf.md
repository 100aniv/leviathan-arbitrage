# LEVIATHAN TF (Task Force) — Quarter-Final / Semi-Final / Pre-Final / Final

> **자동 진입**: `leviathan.md` §7에서 전 US `passes:true` 감지 시 자동 호출됨.
> TF는 기존 팀원을 TF 전용 역할로 재소집. 개발 세션과 **완전 분리** (fresh context).
> 팀 구조, 인프라 → **CLAUDE.md 참조**. TF 에스컬레이션 → **leviathan.md §7 참조**.

## 핵심 원칙: 회귀 구조

- TF는 **검사 프로세스**이지, 새 Phase를 만드는 단계가 아님.
- TF FAIL → **회귀 Phase 생성** (원본 Phase 미비점 보완).
- 회귀 Phase는 SSOT.md에서 TF 섹션 위, 원본 Phase 다음에 위치.
- 각 회귀 US에 `(← 원본 Phase US-XXX 사유)` 역추적 주석.

## 4-Round 체계 (XXX STUDIO 표준)

```
┌─────────────────────────────────────────────────────────┐
│  TF Quarter-Final (QF) — Development Verification       │
│  "코드가 올바른가?"                                      │
│  정합성, 체크리스트, 교차검증, 코드 품질, 멀티모델 감사   │
├─────────────────────────────────────────────────────────┤
│  TF Semi-Final (SF) — System Validation                 │
│  "24시간 돈을 벌 수 있나?"                               │
│  24H Shadow, 전략별 P&L, E2E 시나리오, 멀티모델 성능 평가│
├─────────────────────────────────────────────────────────┤
│  TF Pre-Final (PF) — Code Structure Refactoring         │
│  "상용급 구조인가?"                                      │
│  Settings 통합, Init 모듈화, Loop 추출, 멀티모델 감사    │
├─────────────────────────────────────────────────────────┤
│  TF Final (F) — Operations Readiness                    │
│  "문제 생기면 대응할 수 있나?"                            │
│  DR 훈련, Sandbox 실거래, 운영 매뉴얼, Canary 1~5%      │
└─────────────────────────────────────────────────────────┘
```

```
SSOT.md §7 구조:
  Phase A~M (원본, 모두 ✅)
    ↓
  회귀 Phase S1~S21 (← 원본 Phase 보완)
    ↓
  TF Quarter-Final (QF) — Development Verification
    ↓
  TF Semi-Final (SF) — System Validation
    ↓
  TF Pre-Final (PF) — Code Structure Refactoring
    ↓
  TF Final (F) — Operations Readiness → Live
```

> **통합 추적**: 각 Round별 `docs/checklists/tf-{qf|sf|pf|final}_YYYYMMDD.md`.

---

## TF 팀 [TWICE] (9명 + 차출)

| TF 역할 | 팀원 | 에이전트 (subagent_type) | 모델 | 역할 |
|---------|------|------------------------|------|------|
| TF 리더 | Nayeon | `oh-my-claudecode:architect` | opus | PASS/FAIL 최종 판정, Go/No-Go |
| 아키텍트 | Karina | `oh-my-claudecode:architect` | opus | 체크리스트, 정합성, 합동 점검 |
| 엔진 | Jeongyeon | `oh-my-claudecode:deep-executor` | opus | 엔진 무결성, 전략 로직, 24H 안정성 |
| 인프라 | Momo | `oh-my-claudecode:qa-tester` | sonnet | Docker/DB/Redis/Nginx, DR 훈련 |
| 데이터 | Sana | `oh-my-claudecode:scientist` | sonnet | PnL 통계, 전략별 Sharpe/WR/DD |
| UI/UX | Mina | QF:`designer` SF:`browser-verifier` F:`writer` | sonnet | 라운드별 역할 전환 |
| 퀀트 | Dahyun | `quant-validator` | opus | 수식/파라미터, 전략별 수익성 |
| QA #1 | Chaeyoung | `oh-my-claudecode:critic` | opus | 압박 면접, 스트레스 시나리오 |
| QA #2 | Tzuyu | `oh-my-claudecode:verifier` | sonnet | 증거 수집, Gate 통과 문서화 |

**차출**: Jisoo(BLACKPINK, security-reviewer) — QF 보안 + Final 보안 점검.
**클론**: 병렬 시 Jeongyeon2, Momo2 등 추가 가능.

---

## TF Quarter-Final (QF) — Development Verification

> "코드가 올바르고, 빠진 것이 없는가?"

### [단계 0] Smoke Test Gate
- 전체 pytest PASS + Docker healthy
- 통합 Shadow 10min (crash=0, 신호 흐름, PnL 기록)
- 실패 시 TF 소집 안 하고 해당 Phase 회귀

### [단계 1] 정합성 확인
- Karina: SSOT.md + prd.json + CLAUDE.md 3-way 정합성
- 누락 US/Phase 발견 → 새 Phase/US 생성

### [단계 2] 체크리스트 수립 (The Blueprint)
- Karina + 도메인 전문가 → '완성 기준' 수립
- Nayeon(TF 리더) 상용화 기준 최종 승인

### [단계 3] 교차 검증 (런타임 증거 기반, 병렬)

> **원칙**: "코드 읽기"가 아니라 "런타임 증거 수집". 코드 구조 검증은 단계 3.5 Assembly에서 수행.
> 단계 0 Shadow 10min 로그를 근거로 검증. 로그에 증거가 없으면 FAIL.

**A. 런타임 로그 분석 (Shadow 10min 로그 기반, grep/count)**
- 엔진 파이프라인: `grep 'Execution result'` > 0 (trades 발생), `grep 'record_loss\|record_win'` (CB 피드백), `grep 'risk_check.*rejected'` (RiskGuardian 동작)
- 시그널 품질: `grep 'min_edge_rejected'` (탈락 분포), `grep 'signal_emitted\|Signal.*emit'` > 0 (시그널 발출)
- CB/KillSwitch: `grep 'circuit_breaker'` (상태 변화), `grep 'is_halted'` (KillSwitch 미발동)
- DQM: `grep 'data_quality_rejected'` (거부 건수/사유 분포), `grep 'always_healthy'` (Paper bypass 동작)
- 전략별: `grep 'strategy='` | sort | uniq -c (전략별 trade/signal 분포)

**B. 실행 검증 (Docker/API/DB 실제 호출)**
- Momo(인프라): `docker compose ps` (healthy), `curl localhost:8000/api/v1/health` (200), DB 연결 확인
- Jisoo(보안): `curl -X POST localhost:8000/api/auth/login` (rate limit 동작), `.env` 토큰 중복/들여쓰기 검사
- Mina(UI/UX): 대시보드 4페이지 + API 200 + WS 연결 (브라우저 검증)

**C. 퀀트 검증 (Shadow 결과 수치 분석)**
- Dahyun(퀀트): Shadow PnL/Sharpe/MDD 수치 계산, 수수료 실측 vs 모델 비교, 슬리피지 분포
- Sana(데이터): Shadow 10min 13항목 복합지표 판정, 전략별 trade >= 1 확인

**D. 코드 읽기 (구조적 문제 의심 시에만, 조건부)**
- dead wiring 의심 시 → Jeongyeon 코드 추적 (기본은 Assembly Gate에서 수행)
- 새 보안 취약점 의심 시 → Jisoo 코드 리뷰

### [단계 3.5] Assembly Verification
- main.py 초기화 체인 서브시스템 non-None
- Signal Flow E2E: 7개 전략 on_signal() > 0 (5분)
- Config Flag Audit: ENABLE_* 플래그 경로
- Dead Wiring Detection: 미연결 코드 0건
- 4개 sub-check 전부 PASS. FAIL → 회귀 Phase.

### [단계 4] 최종 확인 + 회귀
- Karina → Nayeon 보고
- Chaeyoung/Tzuyu 압박 면접
- FAIL: 미비점 → 회귀 US 생성 → 3-Stage(A~C) → QF 재검증
- **PASS 기준**: CRITICAL 0, HIGH 0, MEDIUM ≤ 5

### [단계 5] 멀티모델 코드 품질 감사
- Codex/Gemini/Qwen 병렬 실행
- 초점: dead code, anti-pattern, type 불일치, 미사용 import
- quorum 2+ 지적 = MUST FIX → QF FAIL → 수정 후 재검증
- 결과 → `.omc/artifacts/tf-audit-qf-{date}.md`

### [단계 6] 기술 부채 목록
- 단계 5 결과 + Karina 검토 → PF에서 해결할 리팩토링 대상 수치화
- 산출물: `.omc/artifacts/tf-tech-debt-{date}.md`

산출물: `docs/checklists/tf-quarter-final_YYYYMMDD.md`

---

## TF Semi-Final (SF) — System Validation

> "72시간 동안 실제로 돈을 벌 수 있는가?"
> 전제: QF 통과 상태

### [단계 1-A] Delta Check
- QF 이후 변경분만 (git diff QF-PASS..HEAD)
- CRITICAL/HIGH 신규 확인 + 10분 Smoke Shadow

### [단계 1-B] 전략별 독립 검증
- 각 활성 전략 단독 10min Shadow: P&L, WR, Sharpe, MDD
- 손실 전략 → disabled_strategies 판단
- 전략 간 상관관계 분석

### [단계 1-C] 전략 상호작용 검증
- 7개 전략 동시 10min Shadow
- 합산 PnL vs 개별합: >80% PASS, <50% FAIL
- Strategy overlap = 0 확인

### [단계 2] Progressive Shadow (24H+)
- Stage 1: 1H (튜너 OFF) → crash=0, 신호 흐름, 거래소 10/10
- Stage 2: 2H (튜너 OFF) → WR>60%, PnL>0, 전략별 분리 리포트
- Stage 3: 2H (튜너 ON) → Stage 2 대비 비교 (효과: PROVEN/NEUTRAL/HARMFUL/BUG)
- Stage 4: 6H (최적 설정) → 전략별 WR>50%, 마찰력 오차<20%
- Stage 5: 12H → 메모리<100MB증가, CPU<80%, WS 재연결
- Stage 6: 24H → LiveGate 6-check + 일일 성과
  1. Sharpe ≥ 2.0
  2. MDD < 5%
  3. 총 신호 ≥ 100
  4. KillSwitch PASS
  5. CircuitBreaker 동작
  6. 거래소 건강도 ≥ 95%
- 각 Stage PASS → 자동 다음 연장
- 실패 → 회귀 Phase → 3-Stage(A~C) → SF 재검증

### [단계 3] 병렬 검증 (단계 2 Stage 2+ 통과 후)

**3-A. E2E 사용자 시나리오 (UAT):**
- Mina(browser-verifier)
- □ 로그인 → JWT 쿠키 → 리다이렉트
- □ Overview: 실시간 PnL, 승률, 활성 거래
- □ Strategies: 전략별 성과, 활성/비활성
- □ Portfolio: 거래소별 잔고
- □ Settings: 모드, 거래소 토글
- □ 모바일: 375px, 768px
- □ WebSocket 1초 갱신
- □ Kill Switch → Telegram < 5초
- □ API 전 엔드포인트 200
- □ 콘솔 에러 0건

**3-B. Master Inspection:**
- TODO/FIXME, dead code, 하드코딩 상수
- 로그 레벨, 민감 정보 미포함
- trading.json ↔ .env ↔ 기본값 일관성

**3-C. 알림 체계:**
- Telegram 거래/워크플로우 알림
- Kill Switch → 알림 → 거래 중단 < 5초
- Alertmanager 규칙 → 라우팅

**3-D. 멀티모델 성능 평가:**
- 24H Shadow 결과를 Codex/Gemini/Qwen이 독립 분석
- 초점: 전략별 Sharpe/PF/MDD, 비활성화 권고, 파라미터 최적화 제안
- quorum 2+ "비활성화 권고" = disabled_strategies 반영
- 결과 → `.omc/artifacts/tf-audit-sf-{date}.md`

**3-E. 메모리/CPU 프로파일링:**
- 24H Shadow 중 메모리 증가 < 100MB, CPU < 80%
- 메모리 누수 패턴 감지 → MUST FIX
- GC 빈도, 이벤트 루프 지연 분석

**PASS 기준:**
- 24H+ Shadow 6-Stage 전부 PASS
- 활성 전략 각각 WR>50%, Sharpe>1.0 (통합 Sharpe>2.0)
- E2E 10개 PASS + 알림 < 5초 + LiveGate 6-check PASS

산출물:
- `docs/checklists/tf-semi-final_YYYYMMDD.md`
- `docs/checklists/tf-sf-shadow-report_YYYYMMDD.md`
- `docs/checklists/tf-sf-strategy-pnl_YYYYMMDD.md`
- `docs/checklists/tf-sf-e2e-scenarios_YYYYMMDD.md`

---

## TF Pre-Final (PF) — Code Structure Refactoring

> "상용급 구조인가? 기능 변경 없이 코드를 정리할 수 있는가?"
> 전제: SF 24H ALL PASS
> **팀**: TWICE(감독) + IVE(실행) + BLACKPINK(리뷰) + NewJeans(Shadow) + Fix Loop(Wendy)
> **원칙**: 기능 변경 0. 모든 Step에 git tag. 실패 시 즉시 rollback.

### PF 팀 구성

| 역할 | 팀원 | 에이전트 | 임무 |
|------|------|---------|------|
| 감독 | Nayeon(TWICE) | architect/opus | PASS/FAIL 최종 판정 |
| 설계 | Karina(TWICE) | architect/opus | 리팩토링 범위 정의, 단계별 설계 승인 |
| 압박 | Chaeyoung(TWICE) | critic/opus | 사전 위험 분석 (pre-mortem) |
| 증거 | Tzuyu(TWICE) | verifier/sonnet | baseline vs post 증거 수집 |
| 조립 | Jeongyeon(TWICE) | deep-executor/opus | Assembly Verification (init chain 전문) |
| 리팩토링 | Wendy(Fix Loop) | code-simplifier/opus | **PRIMARY 실행자** — 구조 단순화 전문 |
| 보조 구현 | Yujin(IVE) | executor/sonnet | 병렬 추출 작업 |
| 테스트 | Wonyoung(IVE) | test-engineer/sonnet | 단계별 회귀 테스트 |
| 코드리뷰 | Jennie(BLACKPINK) | code-reviewer/opus | 기능 변경 0 검증 (behavioral equivalence) |
| Shadow | Minji(NewJeans) | shadow-tester | baseline vs post Shadow 비교 |
| 퀀트 | Dahyun(TWICE) | quant-validator/opus | 수식/파라미터 보존 검증 |
| 멀티모델 | Codex/Gemini/Qwen | CLI | 기능 변경 0 독립 감사 |

### [PF-1] Baseline 확보
- pytest 전체 PASS + Shadow 10min 13항목 기록
- `git tag pf-baseline`
- baseline 메트릭 → `.omc/artifacts/pf-baseline.json`

### [PF-2] Settings 통합
- 35개 env 직접 읽기 → EngineConfig dataclass 1곳
- `git tag pf-step-2-pre` → 실행 → pytest PASS → 기존 값 100% 보존
- FAIL: `git reset --hard pf-step-2-pre`

### [PF-3] Init Chain 모듈화
- `_init_*()` 11개 → EngineBootstrap 클래스
- main.py는 `bootstrap.run()` 호출만
- Assembly Gate 4-check (init chain 검증)

### [PF-4] Loop Manager 추출
- 13개 이벤트 루프 → LoopManager
- 생명주기 관리 (start/stop/health)
- Shadow 10min (런타임 안정성)

### [PF-5] 타입 강화
- `Any` → Protocol/TypeVar
- IDE 자동완성 + 정적 분석 가능

### [PF-6] 멀티모델 리팩토링 감사
- Codex/Gemini/Qwen: 리팩토링 전후 diff 감사
- 초점: "기능 변경이 정말 0인지"
- quorum 2+ "기능 변경 감지" = rollback
- 결과 → `.omc/artifacts/tf-audit-pf-{date}.md`

### [PF-7] 재검증
- Shadow 13항목 vs PF-1 baseline 비교
- 차이 0 (±1% 이내) → PASS → Final 진입
- 차이 있음 → `git reset --hard pf-baseline` → 10min Smoke → Final 진입

### PF 회귀
- Step FAIL: 해당 Step rollback (`git tag` 기반)
- PF-7 FAIL: 전체 rollback → PF 재시도 (최대 2회, 다른 전략)
- 2회 실패: PF 스킵 → Final 진입 (SF PASS 코드 유지)
- **prd.json US 생성하지 않음** (기능 변경 0 원칙)

### PF PASS 기준
- Shadow baseline 동일 (13항목 ±1% 이내)
- pytest 0 fail
- Assembly 4-check PASS
- 멀티모델 "기능 변경 0" 합의

산출물: `docs/checklists/tf-pre-final_YYYYMMDD.md`

---

## TF Final (F) — Operations Readiness

> "문제가 생기면 대응할 수 있는가?"
> 전제: TF PF PASS (또는 PF 스킵) + 24H Shadow ALL PASS

### [단계 0] 완성품 통합 검증 (Final 시작 전)
- Chrome 대시보드 4페이지 순회 + API 200 + WS 연결
- Telegram 3봇 (Trade/Dev/Infra) 정상 수신 확인
- Grafana 대시보드 + Alertmanager 규칙 활성
- pytest 전체 PASS + Shadow 10min 13항목 PASS
- 1건이라도 FAIL → PF 또는 SF로 회귀

### [단계 1] Operations Readiness Review (ORR)
Mina(writer):
- □ 일일 점검 절차 (매일 09:00)
- □ 주간 리포트 양식
- □ IRP: P1(자본손실, 15분), P2(서비스장애, 30분), P3(성능저하, 4시간)
- □ 에스컬레이션: 엔진→TF리더→사장님
- □ 당직 체계

### [단계 2] Disaster Recovery (DR) 훈련
Jeongyeon + Momo 주도, Chaeyoung 시나리오:
- □ DR-1: 엔진 crash → 재시작 → 포지션 복구 → DB 무결성
- □ DR-2: DB 장애 → WAL/PITR → 데이터 정합성
- □ DR-3: 거래소 API 장애 → CircuitBreaker → 자동 복구
- □ DR-4: Redis 장애 → 상태 복구 → Kill Switch 유지
- □ DR-5: 네트워크 단절 → WS 재연결 → 데이터 갭
- □ DR-6: 코드 롤백 → git revert → 포지션 청산
- □ DR-7: 전략 카니발리제이션 → overlap → 자동 비활성화
- □ DR-8: 폭주 전략 → per-strategy CB 발동
- □ DR-9: 오토튜너 오류 → params 롤백 + 튜너 비활성화

### [단계 3] Sandbox 실거래
Momo 주도:
- □ Binance Testnet: 주문→체결→잔고→PnL
- □ 주문 취소 → 잔고 복원
- □ Rate limit + 응답 지연
- □ Testnet 없는 거래소: API 조회만

### [단계 4] 자본/리스크 한도 확정
Dahyun 검증, Nayeon + 사장님 승인:
- □ 거래소별 자본 한도 (alpha: $70, beta: $750)
- □ 전략별 포지션 한도
- □ max_daily_loss_usd, max_single_loss_usd
- □ DynamicSizer (Kelly fraction, min/max)
- □ Auto-discovery 필터

### [단계 5] Canary Deployment (1%, 7일)
Sana 리콘실리에이션, Dahyun 수익성:
- □ Alpha: $70/exchange × 10 = $700
- □ 튜너 OFF 3일 → ON 4일 → A/B 비교
- □ 일일 3-way: 엔진 P&L vs 거래소 잔고 vs DB
- □ 슬리피지/수수료 실측 vs 예측
- □ PASS: P&L>0, 리콘 오차<1%, 슬리피지 오차<50%

### [단계 6] 멀티모델 운영 준비 평가
- Codex/Gemini/Qwen: 운영 매뉴얼 완성도, IRP 커버리지, 모니터링 커버리지 독립 평가
- 초점: 매뉴얼 누락 시나리오, 미커버 장애 유형, 알림 사각지대
- quorum 2+ 지적 = MUST FIX
- 결과 → `.omc/artifacts/tf-audit-final-{date}.md`

### [단계 7] Live Kick-Off
- Nayeon 최종 서명 + Jisoo 보안 점검 + 사장님 승인
- Alpha → Beta → Full Live

**PASS 기준:**
- 단계 0 통합 검증 PASS + ORR 완비 + DR 9개 PASS + Sandbox 정상 + 자본 한도 승인 + 멀티모델 운영 평가 합의 + Canary 7일 PASS

산출물:
- `docs/operations/daily-checklist.md`
- `docs/operations/incident-response.md`
- `docs/operations/weekly-report-template.md`
- `docs/checklists/tf-final_YYYYMMDD.md`
- `docs/checklists/tf-final-dr-report_YYYYMMDD.md`
- `docs/checklists/tf-final-canary-report_YYYYMMDD.md`

---

## 회귀 구조 (4-Round 공통)

> 회귀 시 별도 Skill 호출 불필요. QF/SF/Final은 prd.json 회귀. PF는 git rollback (특수 케이스).

```
QF FAIL → 회귀 US를 prd.json 추가 (passes:false) → Phase Loop(A→B→C) → 전 US passes:true → QF 재진입
SF FAIL → 회귀 US를 prd.json 추가 (passes:false) → Phase Loop(A→B→C) → SF 재진입 (QF 스킵, 구조적 결함 시 QF부터)
PF FAIL → git rollback → PF 재시도 (최대 2회, 다른 리팩토링 전략) → 2회 실패 시 PF 스킵 → Final 직행
Final FAIL → 항목별 수정 → Final 해당 단계 재검증 (코드 변경 시 SF부터, 구조 변경 시 PF부터)
```

**상태 관리** (2개 파일):
- `.omc/state/leviathan-tf-status.json` — TF 라운드별 상세 (qf/sf/pf/final status, history, assembly, regression_phase)
- `.omc/state/leviathan-progress.json` — Phase Loop 상태 (`status: "tf-entry"|"regression"`)
- 회귀 완료 후 progress.json `status: "tf-entry"` 복귀 → tf-status.json 기반 해당 라운드부터 재개
