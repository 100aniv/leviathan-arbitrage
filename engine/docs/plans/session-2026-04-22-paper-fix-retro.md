# Session Retro — 2026-04-22 Paper universe_matrix Fix + 14-Doc Sync

**Date**: 2026-04-22 (사장님 외출 동안 진행)
**Trigger**: 14h 카나리(PID 45822) trade 0건 발생 + 사장님 "리팩토링 이후 개판" 진단

---

## TL;DR (30초 요약)

LEVIATHAN paper 모드의 14h 카나리가 universe_matrix entries=0 환경으로 trade fill 0건 발생한 root cause를 찾아서 수정. 5분 dry-run에서 trade=5/$2.18 검증. SSOT/PRD/CHANGELOG/CLAUDE.md/RUNBOOK 6 mismatch 정합 정정. K-PT 4 거짓양성 리셋. IRP P1/P2/P3 신설. pre_canary_check.py 자동화 도구 추가. 14 commits.

다음 단계: 30min/60min/6h/24h paper canary stage → US-055 LiveGate Preflight → US-056 첫 Live 체결.

---

## 14 Commits 분류

### Code fix (2)
- `3d37e91` _init_paper_exchanges 하드코딩 2개 → config 기반 7개 + PaperExchangeAdapter `_market_type`/`supports_symbol`/`get_min_notional` 추가. universe_matrix entries 0 → 34.
- `e5a28b2` paper-adapter 확장으로 깨진 2 테스트 갱신. regression 5053 pass / 14 skipped.

### Doc sync (8)
- `f304355` SSOT.md 6 mismatch 정정 (Day-16 후속 commits, Gate 상태, US-386, K-PT 거짓양성, 모드명칭, tests count) — ssot-keeper agent
- `54193c2` CHANGELOG.md 14h canary 무효 + universe_matrix fix + SSOT 동기화
- `cec59ae` .claude/CLAUDE.md Path-B v2 Refactor Rules에 paper 모드 + universe_matrix 게이트 룰 영구 추가
- `2c9e12f` engine/docs/REFACTOR_PLAN.md Post-Day-15 review remediation + Paper fix
- `3d79cbe` OPERATOR_RUNBOOK §0 14h canary 무효 인정 + §0.5 Pre-canary 점검 절차
- `624af46` engine/README.md Path-B v2 Status table 전면 갱신
- `d8c237f` README.md Quick Start §4 + Path-B v2 status 갱신
- `3c05ba0` SSOT.md line 123 PRD 분포 정합 정정 (이전 정정에서 누락)

### PRD reset (1)
- `45a83a6` PRD 5 거짓양성 리셋 (US-386 부분 리네임 + US-407/409/410/419 K-PT ac_override). 분포 429→424 pass / 8→13 fail.

### IRP / Plan / Automation (3)
- `f31d410` OPERATOR_RUNBOOK §6.5 IRP P1/P2/P3 신설 (Phase L L-5 진행)
- `7b2290d` engine/docs/plans/us-386-shadow-to-paper-migration.md (5단계 plan, Phase L Live 후 시작)
- `d5bbf68` engine/scripts/pre_canary_check.py + RUNBOOK §0.5 자동화. 5분 dry-run 4 항목 측정 → JSON evidence

---

## 검증

**5분 dry-run** (`/tmp/leviathan_test2.log`):
- universe_matrix.built entries=34 strategies=4 exchanges=7 ✓
- paper_mode.trade_request_executed = 5 ✓
- crash count = 0 ✓
- total_pnl = +$2.18 ✓
- 4/4 PASS (pre_canary_check.py 검증)

**전체 regression**: pytest 5053 pass / 14 skipped (e5a28b2 commit 후).

**진행 중**: 30min engine BG (PID 66873, ~28분 elapsed at retro 작성 시점)

---

## 사장님 외출 후 결정 의존 항목

1. 30min engine 결과 (post_30min_analyzer PID 71199 watchdog 자동 분석 → /tmp/post_30min_report.txt)
2. 결과 양호 시 → 60min → 6h → 24h paper canary 진행
3. 24h 통과 시 → US-055 LiveGate Preflight → US-056 첫 Live 체결
4. push 결정 (현재 99 commits ahead of origin/main, 사장님 명시 승인 대기)

---

## 미완 (다음 Day plan)

- Phase L L-1: 대시보드 UX 전면 재설계
- Phase L L-2: Settings hot-reload
- Phase L L-3: OpenTelemetry 통합
- Phase L L-4: Zero-downtime 배포
- ~~Phase L L-5: 운영 Runbook + IRP~~ ✓ 진행 (`f31d410`)
- US-386 진정 완료 (shadow.py → paper.py 모놀리스 이전, plan 작성됨 `7b2290d`)
- 14-doc sync 5/14 잔여 (paper universe_matrix 영향 무관: dashboard/.env.example/docker-compose/grafana/math-models)

---

## 영구 룰 추가 (.claude/CLAUDE.md)

1. Paper 어댑터는 config 기반 — paper_binance/paper_okx 같은 하드코딩 절대 금지
2. PaperExchangeAdapter는 ExchangeAdapter Protocol 완전 구현 강제
3. universe_matrix entries=0 즉시 카나리 중단
4. Day N 완료 게이트: pytest pass + Shadow 10분 + entries>0 + trade_request_executed>=1 동시 충족
5. ac_override 사용 금지

이 5 룰은 14h 카나리 헛수고 영구 재발 방지.

---

## Architect 미실행

CRITICAL/HIGH/MED 분리 review가 시간 부족으로 진행 안 됨. 30min 결과 받은 후 별도 review 라운드 권장.

---

## 사장님 진단 "리팩토링 이후 개판"의 정체 (정리)

1. **shadow.py 2,679 LOC 모놀리스가 그대로** — "전면 리네임" 선언 후에도 분리 미실행 (US-386 부분 완료)
2. **paper 어댑터 2개 하드코딩이 universe_matrix=0 야기** — 14h trade 0건 root cause
3. **SSOT-PRD 정합 mismatch 6건** — Day-16 후속 commits 누락, K-PT 거짓양성 ac_override, 모드명칭 혼용 등
4. **Day 6-15 commits는 존재하지만 universe_matrix=0 환경에서 작성** → flag-on 경로 한 번도 exercise 안 됨

이 4가지가 누적되어 "개판"의 인상. 오늘 세션은 (1) 외 (2) (3) (4) 정정 + 영구 룰화 + plan 작성으로 정리.
(1) shadow.py 모놀리스 이전은 plan만 작성 — Phase L Live 후 시작.
