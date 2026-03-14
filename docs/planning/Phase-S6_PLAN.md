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

1. **검증 스크립트 작성** (임시, `.omc/scripts/verify_prd_paths.py`)
   ```python
   import json, os
   with open(".omc/prd.json") as f:
       prd = json.load(f)
   mismatches = []
   for story in prd["stories"]:
       for path in story.get("files", []):
           if "*" in path:  # 와일드카드 무시
               continue
           if not os.path.exists(path):
               mismatches.append((story["id"], path))
   for sid, p in mismatches:
       print(f"  {sid}: {p}")
   print(f"\nTotal mismatches: {len(mismatches)}")
   ```

2. **경로 수정**: 불일치 경로를 실제 파일 경로로 업데이트
   - 주요 패턴: `dashboard/src/pages/*.tsx` -> `dashboard/src/app/*/page.tsx` (App Router 마이그레이션)
   - `engine/src/tuning/shadow_runner.py` 등 리팩터링된 파일 확인

3. **total_stories 업데이트**: `146` -> `147` (US-156 반영)

4. **US-149 passes 업데이트**: 완료 시 `false` -> `true`

### 기대 결과
- 검증 스크립트 실행 시 `Total mismatches: 0`
- `total_stories: 147`

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
| 다음 작업 | Phase S5 US-145 + US-156 → S6 | Phase S6 Documentation (US-149~151) → TF재검증 |
| Phase 순서 | 이미 S1~S6 포함 (OK) | 유지 |
| Upbit 수수료 | (미표기, SSOT 참조) | Maker 0.05%, Taker 0.139% 명시 필요? |

### 구현 상세

1. **"현재 상태" 섹션 업데이트** (line 259~266):
   - `Tests: 4,360` -> `Tests: 4,460`
   - `PRD: 147개 US, 137 pass / 10 fail` -> `PRD: 147개 US, 142 pass / 5 fail`
   - `다음 작업: Phase S5 US-145...` -> `다음 작업: Phase S6 Documentation (US-149~151) → TF재검증`
   - Docker: `Docker 필수` 유지 (변동 없음)

2. **Upbit 수수료 검토**: SSOT.md에 이미 `Upbit | 0.05% | 0.139%` 명시. CLAUDE.md에는 거래소별 수수료 표가 없으므로 "SSOT.md §4.2 참조" 유지로 충분. prd.json AC에 "Upbit 수수료 표기 수정" 있으므로, CLAUDE.md 자주 틀리는 패턴에 항목 추가 고려.

3. **US-150 passes 업데이트**: prd.json에서 `false` -> `true`

### 기대 결과
- CLAUDE.md의 수치/상태가 SSOT.md 및 실제 프로젝트 상태와 일치
- 세션 시작 시 CLAUDE.md → SSOT.md 참조 체인 정합성 확보

### 수락 기준
- [ ] 테스트 수 현행화 (4,460)
- [ ] Phase 순서 현행화 (S1~S6 반영 — 이미 존재, 확인만)
- [ ] 다음 작업 현행화 (S6 이후 TF 재검증)
- [ ] Upbit 수수료 표기 수정 (CLAUDE.md "자주 틀리는 패턴"에 추가 또는 SSOT 참조 확인)

---

## Task 3: US-151 — SSOT.md 수식/체크 항목 코드 동기화

### GAP 분석 (코드 vs SSOT)

#### GAP-1: RiskGuardian 체크 목록 (CRITICAL)

**SSOT 현재** (§4.3):
> RiskGuardian (9-check): 자본, 마진, 스프레드, 포지션, 주문크기, 일일손실, 연속손실, 슬리피지, 롤백비용

**코드 실제** (`engine/src/risk/guardian.py`):
| # | 체크명 | Prometheus label | 설명 |
|---|--------|-----------------|------|
| 0 | halt | engine_halted | KillSwitch 활성 여부 (CANNOT bypass) |
| 1 | position_limit | position_limit | 심볼별 포지션 한도 (10% of capital) |
| 2 | drawdown_limit | drawdown_limit | 현재 DD > 2% 시 차단 |
| 3 | exposure_limit | exposure_limit | 총 노출 > 30% of capital 시 차단 |
| 4 | circuit_breaker + net_exposure | circuit_breaker_open, net_exposure_exceeded | CB 상태 + Amendment 7 상관관계 (0=비활성) |
| 5 | exchange_health | exchange_health_low | 거래소 헬스 스코어 < 90% |
| 6 | trade_size | trade_size_exceeded | 단일 거래 > 5% of capital |
| 7 | volatility | volatility_too_high | 1min/24h 변동성 비율 > 2.0x |
| 8 | rollback_cost | rollback_cost_exceeded | 최악 롤백 비용 > 2% of position |
| 9 | correlation_scale | (log only) | 전략 상관관계 스케일다운 (DynamicSizer 위임) |
| 10 | concurrent_positions | max_concurrent_positions | 동시 포지션 수 >= 20 (US-154) |

**수정 필요**: "9-check" -> "11-check (#0~#10)" + 정확한 체크명 목록으로 교체

#### GAP-2: KillSwitch Tier 설명 (HIGH)

**SSOT 현재**:
> - Tier 1: 일일 누적 손실 > 임계값 → 전체 중단
> - Tier 2: CB OPEN > 30min / 레이턴시 > 5s 연속 10회 → 자동 일시정지
> - Tier 3: 수동 halt_local() → 즉시 중단

**코드 실제** (`engine/src/risk/kill_switch.py`):
> - Tier 1: halt_local() (threading.Event) + Redis HALT key 설정. Target < 1ms. 즉시 주문 차단.
> - Tier 2: 전 거래소 미체결 주문 병렬 취소 (cancel_all_orders). Target < 500ms.
> - Tier 3: 전 거래소 오픈 포지션 마켓 청산 (close_all_positions). Target < 2000ms. 설정으로 비활성화 가능.

**수정 필요**: 트리거 조건(누적 손실, CB OPEN)이 아닌 실행 단계(halt→cancel→close) 기반으로 전면 교체

#### GAP-3: CircuitBreaker 백오프 (MEDIUM)

**SSOT 현재**:
> CircuitBreaker: CLOSED → OPEN → HALF_OPEN (지수 백오프 1s→60s cap)

**코드 실제** (`engine/src/risk/circuit_breaker.py`):
> cooldown_seconds: float = 300.0 (고정 300s)

**수정 필요**: "지수 백오프 1s→60s cap" -> "고정 cooldown 300s (5min)" + HALF_OPEN 조건 (3회 테스트 거래) 명시

#### GAP-4: ETH 네트워크 비용 (MEDIUM)

**SSOT 현재**: ETH 네트워크 비용이 구체적으로 명시되지 않음 (CLAUDE.md memory에 "$5.60" 참조)

**코드 실제** (`engine/src/friction/fee_model.py`):
| 거래소 | ETH 출금 비용 | 네트워크 |
|--------|-------------|---------|
| Binance | $0.06 | Arbitrum One |
| Bybit | $0.19 | Arbitrum |
| OKX | $0.10 | Arbitrum |
| Bitget | $0.10 | Arbitrum |
| Upbit | $4.50 | L1 only (no L2) |
| Bithumb | $2.50 | L1 only |
| Coinone | $2.50 | L1 only |

**수정 필요**: SSOT §4.2 거래소 수수료 표에 ETH 행 추가 또는 주석으로 "L2 최저경로 기준" 명시. KRW 거래소는 L2 미지원 주의사항 추가.

### 구현 상세

1. **SSOT §4.3 RiskGuardian 섹션 전면 교체**:
   - "9-check" -> "11-check (#0~#10)"
   - 정확한 체크명 테이블 삽입 (위 GAP-1 테이블)

2. **SSOT §4.3 KillSwitch 섹션 교체**:
   - 트리거 조건 설명 -> 실행 단계 설명
   - Tier별 target latency 명시

3. **SSOT §4.3 CircuitBreaker 설명 수정**:
   - "지수 백오프 1s→60s cap" -> "고정 cooldown 300s"
   - HALF_OPEN 전환 조건 (cooldown 경과 후), 3회 테스트 거래 성공 시 CLOSED 복귀 명시

4. **SSOT §4.2 네트워크 비용 보완**:
   - ETH 비용 명시: "글로벌 거래소 $0.06~$0.19 (Arbitrum L2), KRW 거래소 $2.50~$4.50 (L1 only)"
   - 또는 fee_model.py WITHDRAWAL_FEES_USD 참조 명시

5. **US-151 passes 업데이트**: prd.json에서 `false` -> `true`

### 수락 기준
- [ ] SSOT §4.3 RiskGuardian 11-check 목록이 guardian.py Check #0~#10과 1:1 매핑
- [ ] SSOT KillSwitch Tier가 코드의 실행 단계 (halt→cancel→close) 기반
- [ ] SSOT CircuitBreaker: 고정 300s cooldown 명시 (지수 백오프 제거)
- [ ] SSOT ETH 네트워크 비용: L2 최저경로 기준 명시 + KRW 거래소 L1 only 주의

---

## 검증 방법

### Stage C 간소화 (코드리뷰 불필요)
- engine/dashboard 소스 코드 변경 없음
- 문서 정합성만 확인: SSOT.md 내용이 코드와 1:1 매핑되는지 교차 검증
- **검증 주체**: verifier (haiku) — 문서 diff 기반 정합성 체크

### Stage D 간소화 (Shadow 불필요)
- 코드 변경 없으므로 pytest/Shadow 실행 불필요
- 기존 테스트가 여전히 PASS하는지만 확인: `cd engine && python -m pytest tests/ -x --tb=short --co -q | tail -5` (테스트 수집만)

### Stage E (정합성)
- prd.json: US-149/150/151 `passes: true` 확인
- prd.json: `total_stories: 147`, passes 카운트 정합성
- SSOT.md §2: Phase S6 완료 반영
- git commit + push

---

## 실행 순서

```
[동시] Task 1 (US-149) + Task 2 (US-150) + Task 3 (US-151)
  │
  ▼
[검증] 문서 정합성 교차 검증
  │   - prd.json 경로 스크립트 실행 → 0 mismatches
  │   - CLAUDE.md 수치 vs SSOT.md 수치 일치 확인
  │   - SSOT.md §4.3 vs guardian.py/kill_switch.py/circuit_breaker.py 매핑 확인
  │
  ▼
[Stage E] prd.json passes 업데이트 + SSOT §2 Phase S6 완료 반영 + git commit+push
```

---

## 예상 복잡도

- **전체**: LOW (문서 수정 전용, 코드 변경 없음)
- **소요 시간**: ~30분 (3 US 병렬 + 검증)
- **리스크**: prd.json 경로 중 리팩터링된 파일 발견 시 US 수정 범위 확대 가능 (낮은 확률)
