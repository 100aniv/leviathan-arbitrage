# LEVIATHAN TF (Task Force) — Quarter-Final / Semi-Final / Final

> **자동 진입**: `leviathan.md` §7에서 전 US `passes:true` 감지 시 자동 호출됨.
> TF는 기존 팀원을 TF 전용 역할로 재소집. 개발 세션과 **완전 분리** (fresh context).

## 핵심 원칙: 회귀 구조

- TF는 **검사 프로세스**이지, 새 Phase를 만드는 단계가 아님.
- TF FAIL → **회귀 Phase 생성** (원본 Phase 미비점 보완).
- 회귀 Phase는 SSOT.md에서 TF 섹션 위, 원본 Phase 다음에 위치.
- 각 회귀 US에 `(← 원본 Phase US-XXX 사유)` 역추적 주석.

## 3-Round 체계

```
┌─────────────────────────────────────────────────────────┐
│  TF Quarter-Final (QF) — Development Verification       │
│  "코드가 올바른가?"                                      │
│  정합성, 체크리스트, 교차검증, 코드 품질, 멀티모델 감사   │
├─────────────────────────────────────────────────────────┤
│  TF Semi-Final (SF) — System Validation                 │
│  "24시간 돈을 벌 수 있나?"                               │
│  24H Shadow, 전략별 P&L, E2E 시나리오                   │
├─────────────────────────────────────────────────────────┤
│  TF Final (F) — Operations Readiness                    │
│  "문제 생기면 대응할 수 있나?"                            │
│  DR 훈련, Sandbox 실거래, Canary 24H, 멀티모델 감사      │
└─────────────────────────────────────────────────────────┘
```

> **PF 재도입 기준**: Final에서 구조적 문제(init chain 붕괴, 상용급 구조 미달) 또는
> Live 후 유지보수 이슈 발생 시 PF(Code Structure Refactoring) Round를 SF→Final 사이에 재도입.

---

## TF 역할

- **tf-leader** (architect/opus): PASS/FAIL 최종 판정, Go/No-Go
- **architect** (architect/opus): 체크리스트, 정합성, 합동 점검
- **engine-verifier** (deep-executor/opus): 엔진 무결성, 전략 로직, 24H 안정성
- **infra-tester** (qa-tester/sonnet): Docker/DB/Redis/Nginx, DR 훈련
- **data-analyst** (scientist/sonnet): PnL 통계, 전략별 Sharpe/WR/DD
- **ui-tester** (designer/sonnet→browser-verifier→writer): 라운드별 역할 전환
- **quant-validator** (quant-validator/opus): 수식/파라미터, 전략별 수익성
- **critic** (critic/opus): 압박 면접, 스트레스 시나리오
- **verifier** (verifier/sonnet): 증거 수집, Gate 통과 문서화
- **security-reviewer** (security-reviewer/sonnet): QF 보안 + Final 보안 점검

---

## TF Quarter-Final (QF) — Development Verification

> "코드가 올바르고, 빠진 것이 없는가?"

### [단계 0] Smoke Test Gate
- 전체 pytest PASS + Docker healthy
- 통합 Shadow 10min (crash=0, 신호 흐름, PnL 기록)
- 실패 시 TF 소집 안 하고 해당 Phase 회귀

### [단계 1] 정합성 확인
- architect: SSOT.md + prd.json + CLAUDE.md 3-way 정합성
- 누락 US/Phase 발견 → 새 Phase/US 생성

### [단계 2] 체크리스트 수립
- architect + 도메인 전문가 → '완성 기준' 수립
- tf-leader 상용화 기준 최종 승인

### [단계 3] 교차 검증 (런타임 증거 기반, 병렬)

> **원칙**: "코드 읽기"가 아니라 "런타임 증거 수집". 단계 0 Shadow 10min 로그를 근거로 검증.

**A. 런타임 로그 분석 (Shadow 10min 로그 기반)**
- `grep 'Execution result'` > 0 (trades 발생)
- `grep 'signal_emitted\|Signal.*emit'` > 0 (시그널 발출)
- `grep 'circuit_breaker'` (CB 상태), `grep 'is_halted'` (KillSwitch 미발동)
- `grep 'data_quality_rejected'` (DQM 거부), `grep 'strategy='` | sort | uniq -c (전략별 분포)

**B. 실행 검증 (Docker/API/DB 실제 호출)**
- infra-tester: `docker compose ps` (healthy), `curl localhost:8000/api/v1/health` (200)
- security-reviewer: rate limit 동작, `.env` 토큰 검사
- ui-tester: 대시보드 4페이지 + API 200 + WS 연결

**C. 퀀트 검증**
- quant-validator: Shadow PnL/Sharpe/MDD 수치, 수수료 실측 vs 모델 비교
- data-analyst: Shadow 10min 13항목 복합지표 판정

### [단계 3.5] Assembly Verification
- main.py 초기화 체인 서브시스템 non-None
- Signal Flow E2E: 7개 전략 on_signal() > 0 (5분)
- Config Flag Audit: ENABLE_* 플래그 경로
- Dead Wiring Detection: 미연결 코드 0건
- 4개 sub-check 전부 PASS. FAIL → 회귀 Phase.

### [단계 4] 최종 확인
- architect → tf-leader 보고
- critic/verifier 압박 면접
- FAIL: 미비점 → 회귀 US 생성 → 3-Stage(A~C) → QF 재검증
- **PASS 기준**: CRITICAL 0, HIGH 0, MEDIUM ≤ 5

### [단계 5] 멀티모델 코드 품질 감사 (FROZEN RULE)
- Codex/Gemini/Qwen 병렬 실행
- 초점: dead code, anti-pattern, type 불일치, 미사용 import
- quorum 2+ 지적 = MUST FIX → QF FAIL → 수정 후 재검증
- 결과 → `.omc/artifacts/tf-audit-qf-{date}.md`

### [단계 6] 기술 부채 목록
- 단계 5 결과 + architect 검토 → 리팩토링 대상 수치화
- 산출물: `.omc/artifacts/tf-tech-debt-{date}.md`

산출물: `docs/checklists/tf-quarter-final_YYYYMMDD.md`

---

## TF Semi-Final (SF) — System Validation

> "24시간 동안 실제로 돈을 벌 수 있는가?"
> 전제: QF 통과 상태

### [단계 1-A] Delta Check
- QF 이후 변경분만 (git diff QF-PASS..HEAD)
- CRITICAL/HIGH 신규 확인 + 10분 Smoke Shadow

### [단계 1-B] 전략별 독립 검증
- 각 활성 전략 단독 10min Shadow: P&L, WR, Sharpe, MDD
- 손실 전략 → disabled_strategies 판단

### [단계 1-C] 전략 상호작용 검증
- 7개 전략 동시 10min Shadow
- 합산 PnL vs 개별합: >80% PASS, <50% FAIL

### [단계 2] Progressive Shadow (24H)
- Stage 1: 1H (튜너 OFF) → crash=0, 신호 흐름, 거래소 10/10
- Stage 2: 2H (튜너 OFF) → WR>60%, PnL>0, 전략별 분리 리포트
- Stage 3: 2H (튜너 ON) → Stage 2 대비 비교 (PROVEN/NEUTRAL/HARMFUL/BUG)
- Stage 4: 6H (최적 설정) → 전략별 WR>50%, 마찰력 오차<20%
- Stage 5: 12H → 메모리<100MB증가, CPU<80%, WS 재연결
- Stage 6: 24H → LiveGate 6-check
  1. Sharpe ≥ 2.0
  2. MDD < 5%
  3. 총 신호 ≥ 100
  4. KillSwitch PASS
  5. CircuitBreaker 동작
  6. 거래소 건강도 ≥ 95%
- 각 Stage PASS → 자동 다음 연장
- 실패 → 회귀 Phase → 3-Stage(A~C) → SF 재검증

### [단계 3] 병렬 검증 (Stage 2+ 통과 후)

**3-A. E2E 사용자 시나리오:** ui-tester — 대시보드 4페이지, JWT, WebSocket 1초 갱신, Kill Switch → Telegram < 5초, 콘솔 에러 0건

**3-B. Master Inspection:** TODO/FIXME, dead code, 하드코딩 상수, 민감 정보 미포함

**3-C. 알림 체계:** Telegram 3봇 정상, Kill Switch → 알림 → 거래 중단 < 5초

**3-D. 메모리/CPU:** 24H Shadow 중 메모리 증가 < 100MB, CPU < 80%, 메모리 누수 → MUST FIX

**PASS 기준:**
- 24H Shadow 6-Stage 전부 PASS
- 활성 전략 각각 WR>50%, Sharpe>1.0 (통합 Sharpe>2.0)
- E2E + 알림 < 5초 + LiveGate 6-check PASS

산출물: `docs/checklists/tf-semi-final_YYYYMMDD.md`, `tf-sf-shadow-report_YYYYMMDD.md`

---

## TF Final (F) — Operations Readiness

> "문제가 생기면 대응할 수 있는가?"
> 전제: SF 24H ALL PASS

### [단계 0] 완성품 통합 검증
- Chrome 대시보드 4페이지, Telegram 3봇, Grafana + Alertmanager 활성
- pytest 전체 PASS + Shadow 10min 13항목 PASS
- 1건이라도 FAIL → SF로 회귀

### [단계 1] Operations Readiness Review (ORR)
- 일일 점검 절차 (매일 09:00)
- IRP: P1(자본손실, 15분), P2(서비스장애, 30분), P3(성능저하, 4시간)
- 에스컬레이션: 엔진→tf-leader→사장님

### [단계 2] Disaster Recovery (DR) 훈련
- DR-1: 엔진 crash → 재시작 → 포지션 복구 → DB 무결성
- DR-2: DB 장애 → WAL/PITR → 데이터 정합성
- DR-3: 거래소 API 장애 → CircuitBreaker → 자동 복구

### [단계 3] Sandbox 실거래
- Binance Testnet: 주문→체결→잔고→PnL, 주문 취소, Rate limit
- Testnet 없는 거래소: API 조회만

### [단계 4] 자본/리스크 한도 확정
quant-validator 검증, tf-leader + 사장님 승인:
- 거래소별 자본 한도 (alpha: $70/거래소, beta: $750)
- max_daily_loss_usd, max_single_loss_usd, DynamicSizer

### [단계 5] Canary Deployment (24H)
- Alpha: $70/exchange × 10 = $700
- 튜너 OFF 12H → ON 12H → A/B 비교
- 일일 3-way: 엔진 P&L vs 거래소 잔고 vs DB
- PASS: P&L>0, 리콘 오차<1%, 슬리피지 오차<50%

### [단계 6] 멀티모델 운영 준비 평가 (FROZEN RULE)
- Codex/Gemini/Qwen: 운영 매뉴얼 완성도, IRP 커버리지, 모니터링 커버리지 독립 평가
- quorum 2+ 지적 = MUST FIX
- 결과 → `.omc/artifacts/tf-audit-final-{date}.md`

### [단계 7] Live Kick-Off
- tf-leader 최종 서명 + security-reviewer 보안 점검 + 사장님 승인
- Alpha → Beta → Full Live

**PASS 기준:** 단계 0 통합 검증 PASS + ORR 완비 + DR 3개 PASS + Sandbox 정상 + 자본 한도 승인 + Canary 24H PASS + 멀티모델 합의

산출물: `docs/checklists/tf-final_YYYYMMDD.md`, `tf-final-dr-report_YYYYMMDD.md`, `tf-final-canary-report_YYYYMMDD.md`

---

## 회귀 구조 (3-Round 공통)

```
QF FAIL → 회귀 US를 prd.json 추가 (passes:false) → Phase Loop(A→B→C) → 전 US passes:true → QF 재진입
SF FAIL → 회귀 US를 prd.json 추가 (passes:false) → Phase Loop(A→B→C) → SF 재진입 (QF 스킵, 구조적 결함 시 QF부터)
Final FAIL → 항목별 수정 → Final 해당 단계 재검증 (코드 변경 시 SF부터)
```

**상태 관리** (2개 파일):
- `.omc/state/leviathan-tf-status.json` — TF 라운드별 상세 (qf/sf/final status, history, assembly, regression_phase)
- `.omc/state/leviathan-progress.json` — Phase Loop 상태 (`status: "tf-entry"|"regression"`)
- 회귀 완료 후 progress.json `status: "tf-entry"` 복귀 → tf-status.json 기반 해당 라운드부터 재개

## FROZEN CRITERIA (변경 금지)

- Shadow 13항목 복합지표 (MDD%, PF, 전략별 trade>=1, 방어 레이어 활성)
- CRITICAL=0, pytest PASS, check_all 9/9
- QF 단계5 멀티모델 감사 (quorum 2+)
- Final 단계6 멀티모델 운영 준비 평가 (quorum 2+)
