# 문서 정합성 감사 보고서 (US-077)

**감사 일시**: 2026-03-11
**감사 범위**: SSOT.md ↔ PRD (.omc/prd.json) ↔ 구현 코드 ↔ CLAUDE.md
**감사자**: executor (US-077)
**결과**: 불일치 5건 발견 → 전부 수정 완료

---

## 1. 감사 요약

| 항목 | 불일치 | 수정 |
|------|-------|------|
| SSOT §4.2 Bybit 수수료 | 0.01%/0.06% → 실제 0.10%/0.10% | ✅ 수정 |
| SSOT §4.2 Upbit 수수료 | 0.25%/0.25% → 실제 0.05%/0.139% | ✅ 수정 |
| SSOT §4.2 Coinone 수수료 | 0.20%/0.20% (표기) → 실제 0.02%/0.02% | ✅ 수정 |
| SSOT §2 현재 상태 | US-076 미완료로 표시 → 실제 완료 (96a872a) | ✅ 수정 |
| CLAUDE.md 거래소 수 | 8 native WS adapters → 실제 10개 | ✅ 수정 |

---

## 2. 상세 불일치 내역

### 2.1 SSOT §4.2 수수료 테이블 — Bybit

- **발견**: SSOT에 Bybit Maker 0.01%, Taker 0.06%로 기록
- **실제**: `engine/src/friction/fee_model.py:44` — `FeeConfig("bybit", 0, Decimal("0.0010"), Decimal("0.0010"))` → Spot VIP0 기준 0.10%/0.10%
- **수정**: SSOT §4.2 `| Bybit | 0.01% | 0.06% |` → `| Bybit | 0.10% | 0.10% | Spot VIP0 |`

### 2.2 SSOT §4.2 수수료 테이블 — Upbit

- **발견**: SSOT에 Upbit 0.25%/0.25%로 기록
- **실제**: `engine/src/friction/fee_model.py:52` — `FeeConfig("upbit", 0, Decimal("0.0005"), Decimal("0.00139"))` → Maker 0.05%, Taker 0.139%
- **수정**: SSOT §4.2 `| Upbit | 0.25% | 0.25% |` → `| Upbit | 0.05% | 0.139% |`

### 2.3 SSOT §4.2 수수료 테이블 — Coinone

- **발견**: SSOT에 "0.20% | 0.20% | API 할인 시 0.02%"로 기록 — 표면 수수료와 실제 적용 수수료가 혼재하여 혼란 야기
- **실제**: `engine/src/friction/fee_model.py:59` — `FeeConfig("coinone", 0, Decimal("0.0002"), Decimal("0.0002"))` → 0.02%가 기본값으로 이미 적용
- **수정**: `| Coinone | 0.02% | 0.02% | API 할인 적용 (기본 0.20%) |`

### 2.4 SSOT §2 현재 상태 — Phase/US 진행도

- **발견**:
  - `Phase: I (거래소/전략 완성도) ← CURRENT`
  - `다음 작업: Phase I — US-076`
  - `완료된 US: US-065~075`
  - `최신 커밋: e43658c Phase I US-073+074+075`
  - US-076이 `[ ]`로 미완료 표시 (SSOT §7)
- **실제**: `git log` → `96a872a Phase I US-076: 전략/거래소 완성도 전수 감사 완료`
- **수정**:
  - Phase: J (운영 안정성) ← CURRENT [Phase I ✅ 완료]
  - 최신 커밋: 96a872a
  - 다음 작업: Phase J — US-077 (문서 정합성 감사)
  - 완료된 US: US-065~076
  - SSOT §7 US-076: `[ ]` → `[x]`

### 2.5 CLAUDE.md 거래소 어댑터 수

- **발견**: `- **거래소**: 8 native WS adapters (ccxt 미사용)`
- **실제**: `engine/src/collectors/` — 10개 파일 (binance, bybit, okx, bitget, upbit, bithumb, coinone, binance_futures, okx_futures, bybit_futures)
  - `engine/src/collectors/manager.py:32` — `DEFAULT_EXCHANGES = [...10개...]`
- **수정**: `10 native WS adapters (7 spot + 3 futures, ccxt 미사용)`

---

## 3. 정합성 확인 항목 (이상 없음 ✅)

| 항목 | 확인 결과 |
|------|----------|
| SSOT §1 어댑터 수 | "10개 네이티브 어댑터" ✅ 정확 |
| SSOT §5 okx_futures 행 | 존재 (US-075, -SWAP 접미사) ✅ |
| SSOT §5 bybit_futures 행 | 존재 (US-075, futures_futures 활성) ✅ |
| SSOT §3.3 futures_futures 상태 | **활성** ✅ (US-075 완료) |
| fee_model.py bybit_futures | 존재 (maker=0.02%, taker=0.055%) ✅ |
| fee_model.py okx_futures | 존재 (maker=0.02%, taker=0.05%) ✅ |
| CollectorManager DEFAULT_EXCHANGES | 10개 ✅ |
| SSOT §9 GAP 현황 | GAP 1~7,9,10 RESOLVED, GAP 8(DEX) 미해결 — 정확 ✅ |
| SSOT §9 SG-1~6 | 전부 RESOLVED ✅ |
| PRD prd.json US-076 | 감사 완료 (git log 확인) ✅ |
| SSOT §3.3 전략 매트릭스 | 8개 전략, 상태 정확 ✅ |
| Docker Compose 컨테이너 수 | 8개 (engine, redis, redis-exporter, timescaledb, dashboard, prometheus, grafana, nginx) ✅ |

---

## 4. 수정된 파일

| 파일 | 변경 내용 |
|------|----------|
| `SSOT.md` §2 | Phase I→J, 커밋 해시, 다음 작업, 완료 US 범위 업데이트 |
| `SSOT.md` §4.2 | Bybit(0.01%→0.10%), Upbit(0.25%→0.05%/0.139%), Coinone 표기 명확화 |
| `SSOT.md` §7 | US-076 `[ ]` → `[x]` |
| `.claude/CLAUDE.md` | 거래소 어댑터 수 8→10 |

---

## 5. 불일치 0건 달성 확인

모든 발견된 불일치 항목이 수정되었습니다.

- SSOT.md ↔ 구현 코드 (fee_model.py, manager.py): ✅ 동기화 완료
- SSOT.md ↔ PRD (prd.json): ✅ 동기화 완료
- CLAUDE.md ↔ SSOT.md: ✅ 동기화 완료
