# Paper Canary Cheat Sheet

**1-page 운영 가이드** (2026-04-22 작성). 다음 카나리 진행 시 이것만 참조.

---

## TL;DR

```bash
cd engine
bash scripts/auto_canary_chain.sh 1   # 모든 stage 자동 진행 (5min → 30min → 60min → 6h → 24h)
# 또는 수동 stage 시작:
bash scripts/auto_canary_chain.sh 3   # 60min부터 시작 (이미 30min PASS 받았을 때)
```

각 stage 종료 시 `engine/.omc/evidence/pre_canary_*.json` 자동 저장. 1/4 FAIL이면 즉시 abort.

---

## 5-Stage 진행

| Stage | 시간 | 측정 | PASS 기준 |
|---|---|---|---|
| 1 | 5min | Pre-canary check | 4/4 (entries>0, fill≥1, crash=0, PnL>0) |
| 2 | 30min | Initial measurement | 4/4 |
| 3 | 60min | Stability | 4/4 |
| 4 | 6h | Sustained | 4/4 + Sharpe>2.0 + MDD<5% |
| 5 | 24h | Statistical | 4/4 + LiveGate 6-check |

24h 통과 → US-055 LiveGate Preflight (10항목 manual) → US-056 첫 Live 체결.

---

## 4-Item Pre-canary Check (RUNBOOK §0.5)

| AC | 항목 | 임계값 | 실패 시 root cause 후보 |
|---|---|---|---|
| 1 | universe_matrix.built entries | > 0 | paper 어댑터 갯수 / ExchangeAdapter Protocol |
| 2 | paper_mode.trade_request_executed | ≥ 1 | strategy 비활성 / signal generation / risk_guardian |
| 3 | CRITICAL/FATAL/Traceback | == 0 | engine startup / collector crash |
| 4 | total_pnl | > 0 | 시장 조건 (사실상 양수가 짧은 sample이면 우연) |

---

## 14h 카나리 헛수고 재발 방지

**역사적 사고** (2026-04-21~22, PID 45822, 14h 14m elapsed): trade 0건. universe_matrix entries=0 root cause. paper 어댑터 2개 하드코딩 + ExchangeAdapter Protocol 미완성.

**영구 룰** (`.claude/CLAUDE.md`):
1. paper 어댑터는 config 기반 — 하드코딩 금지
2. PaperExchangeAdapter는 Protocol 완전 구현 필수
3. universe_matrix entries=0 → 즉시 카나리 중단
4. Day N 완료 게이트: pytest pass + Shadow 10분 + entries>0 + trade≥1
5. ac_override 사용 금지

---

## 통과 후 (24h Stage 5 PASS)

```bash
# US-055 LiveGate Preflight 10항목 (manual)
# US-056 첫 Live 체결 (operator 수동 승인)
```

---

## 실패 시 (1/4 FAIL at any stage)

1. `engine/.omc/evidence/pre_canary_*.json` 마지막 결과 확인
2. 해당 AC 실패 원인 조사 (위 표 참조)
3. fix → 5min Pre-canary 재실행 → 통과 시 다음 stage 재개
4. 동일 fail 2회 연속 → Phase A (재기획) 필요

---

## 현재 작업 (2026-04-22 기준)

- ✅ paper 어댑터 7개 확장 (3d37e91)
- ✅ universe_matrix entries 0 → 34
- ✅ 5분 dry-run trade=5/$2.18 검증
- 🟡 30min measurement (BG 진행 중 PID 66873)
- ⏳ 60min/6h/24h/LiveGate (operator 결정)

---

## 참조

- `OPERATOR_RUNBOOK.md §0.5` — Pre-canary 점검 상세
- `OPERATOR_RUNBOOK.md §6.5` — IRP P1/P2/P3 (자동 대응)
- `engine/scripts/pre_canary_check.py` — 자동 4-item validation
- `engine/scripts/auto_canary_chain.sh` — 5-stage 자동 chain
- `plans/session-2026-04-22-paper-fix-retro.md` — 14 commits retro
