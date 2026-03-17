# Phase S6: Documentation Sync — PLAN.md

> **Phase**: S6 (Documentation Sync)
> **US**: US-149, US-150, US-151
> **의존성**: 3개 US 완전 독립 (파일 교집합 없음) — 병렬 실행 가능
> **코드 변경**: 없음 (문서 전용). engine/dashboard 소스 수정 없음
> **Stage C/D 간소화**: 코드 변경 없으므로 pytest/Shadow 불필요. 문서 정합성 검증만 수행

---

## 배치 구조

| 배치 | US | 대상 파일 | 실행 주체 |
|------|-----|----------|----------|
| Batch-1 | US-149 | `.omc/prd.json` | 리드 직접 (스크립트 기반 자동 검증) |
| Batch-2 | US-150 | `.claude/CLAUDE.md` | 리드 직접 (텍스트 수정) |
| Batch-3 | US-151 | `SSOT.md` | 리드 직접 (코드 읽기 + 텍스트 수정) |

**TeamCreate 불필요**: 3개 US 모두 문서 수정이므로 리드 직접 수행 권장.
US-151은 engine/src/risk/ 코드 참조 필요하지만, 읽기 전용이므로 executor 불필요.

---

## Task 1: US-149 — prd.json 파일 경로 수정

### 현재 상태
- prd.json `total_stories: 146` (US-156 추가 후 147개로 업데이트 필요)
- `passes: false` 5건: US-055, US-056 (Phase F), US-149, US-150, US-151 (Phase S6)
- 23개 이상의 US에 `files` 배열 존재 — 일부 경로가 실제 파일 시스템과 불일치 가능

### 구현 상세

1. **검증 스크립트 실행** (engine/ 기준):
   - prd.json의 모든 `files` 배열 항목에 대해 os.path.exists() 검증
   - 와일드카드(`*`) 포함 경로는 glob으로 1개 이상 매치 확인
   - 불일치 경로 목록 출력

2. **경로 수정**: 불일치 경로를 실제 파일 경로로 업데이트
   - 주요 패턴: `dashboard/src/pages/*.tsx` -> `dashboard/src/app/*/page.tsx` (App Router)
   - `engine/src/tuning/shadow_runner.py` 등 리팩터링된 파일 확인

3. **total_stories 업데이트**: `146` -> `147` (US-156 반영)

4. **US-149 passes 업데이트**: 완료 시 `false` -> `true`

### 수락 기준
- [ ] prd.json의 모든 files 경로가 실제 파일 경로와 일치 (와일드카드 제외)
- [ ] 검증 스크립트: 모든 files 항목이 os.path.exists() True

---

## Task 2: US-150 — CLAUDE.md 현행화

### 현재 상태 vs 기대 상태

| 항목 | 현재 (CLAUDE.md) | 기대 (Phase S5 완료 후) |
|------|-----------------|----------------------|
| Tests | 4,360 passed | 4,460 passed |
| PRD | 147개 US, 137 pass / 10 fail | 147개 US, 142 pass / 5 fail |
| 다음 작업 | Phase S5 US-145 + US-156 | Phase S6 Documentation (US-149~151) → TF재검증 |
| Upbit 수수료 | (미표기, SSOT 참조) | Maker 0.05%, Taker 0.139% 주의사항 추가 |

### 구현 상세

1. **"현재 상태" 섹션 업데이트** (line 259~266):
   - `Tests: 4,360` -> `Tests: 4,460`
   - `PRD: 147개 US, 137 pass / 10 fail` -> `PRD: 147개 US, 142 pass / 5 fail`
   - `다음 작업: Phase S5...` -> `다음 작업: Phase S6 Documentation (US-149~151) → TF재검증`

2. **Upbit 수수료**: CLAUDE.md "자주 틀리는 패턴"에 항목 추가 고려

3. **US-150 passes 업데이트**: prd.json에서 `false` -> `true`

### 수락 기준
- [ ] 테스트 수 현행화 (4,460)
- [ ] Phase 순서 현행화 (S1~S6 반영 확인)
- [ ] 다음 작업 현행화 (S6 이후 TF 재검증)
- [ ] Upbit 수수료 표기 수정

---

## Task 3: US-151 — SSOT.md 수식/체크 항목 코드 동기화

### GAP 분석 결과

#### GAP-1: RiskGuardian 체크 목록 (CRITICAL)
- SSOT: "9-check: 자본, 마진, 스프레드, 포지션, 주문크기, 일일손실, 연속손실, 슬리피지, 롤백비용"
- 코드: 11-check (#0 halt, #1 position, #2 drawdown, #3 exposure, #4 CB+net_exposure, #5 exchange_health, #6 trade_size, #7 volatility, #8 rollback_cost, #9 correlation, #10 concurrent_positions)
- **수정**: 전면 교체 -> 코드 기반 11-check 테이블

#### GAP-2: KillSwitch Tier (HIGH)
- SSOT: Tier 1=일일손실, Tier 2=CB OPEN/레이턴시, Tier 3=수동 halt
- 코드: Tier 1=halt_local+Redis(<1ms), Tier 2=cancel_all_orders(<500ms), Tier 3=close_all_positions(<2000ms)
- **수정**: 실행 단계 기반으로 전면 교체

#### GAP-3: CircuitBreaker (MEDIUM)
- SSOT: "지수 백오프 1s→60s cap"
- 코드: 고정 cooldown_seconds=300.0, half_open_test_count=3
- **수정**: 고정 300s + HALF_OPEN 3회 테스트 명시

#### GAP-4: ETH 네트워크 비용 (MEDIUM)
- 글로벌 거래소: $0.06~$0.19 (Arbitrum L2)
- KRW 거래소: $2.50~$4.50 (L1 only)
- **수정**: L2 최저경로 기준 명시 + KRW L1 only 주의

### 수락 기준
- [ ] SSOT §4.3 RiskGuardian 11-check 목록이 guardian.py #0~#10과 매핑
- [ ] SSOT KillSwitch Tier: halt→cancel→close 실행 단계 기반
- [ ] SSOT CircuitBreaker: 고정 300s cooldown (지수 백오프 제거)
- [ ] SSOT ETH 네트워크 비용: L2 최저경로 + KRW L1 only 명시

---

## 검증 방법

- **Stage C**: 코드리뷰 불필요 (코드 변경 없음). verifier 문서 diff 교차검증만 수행
- **Stage D**: Shadow 불필요. 기존 테스트 수집만 확인 (pytest --co)
- **Stage E**: prd.json passes 업데이트 + SSOT §2 Phase S6 완료 + git commit+push

## 실행 순서

```
[동시] Task 1 + Task 2 + Task 3
  → [검증] 문서 정합성 교차 검증
  → [Stage E] prd.json 업데이트 + SSOT 업데이트 + git commit+push
```

## 예상 복잡도: LOW (~30분)
