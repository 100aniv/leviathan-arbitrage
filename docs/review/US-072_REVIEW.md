# US-072 Code Review — 계좌 정보/총자산/거래소별 잔고 표시

**Reviewer**: code-reviewer
**Date**: 2026-03-11
**Branch**: main

---

## Code Review Summary

**Files Reviewed:** 6
**Total Issues:** 6

### By Severity
- CRITICAL: 0
- HIGH: 0
- MEDIUM: 3 (should fix)
- LOW: 3 (optional)

---

## Stage 1 — Spec Compliance ✅

| 요구사항 | 구현 여부 |
|---------|---------|
| GET /api/v1/portfolio-summary 엔드포인트 | ✅ |
| JWT require_auth 보호 | ✅ |
| 총 잔고(total_balance_usdt) 계산 | ✅ |
| 거래소별 잔고 breakdown | ✅ |
| Shadow VirtualBalanceTracker → exchange_status fallback 순서 | ✅ |
| TypeScript 타입(ExchangeBalance, PortfolioSummaryResponse) | ✅ |
| 대시보드 10s polling 연동 | ✅ |
| 연결 상태(connected) 병합 | ✅ |
| shadow.py 라우터 패턴 일관성 | ✅ |
| 12개 단위 테스트 커버리지 | ✅ |

스펙 준수 완료. Stage 2로 진행.

---

## Stage 2 — Code Quality

### LSP / TypeScript 진단
- `portfolio.py`: 진단 없음 (clean)
- `dashboard/`: `tsc --noEmit` 0 errors, 0 warnings ✅

---

## Issues

### [MEDIUM] `daily_pnl`이 `total_pnl`과 동일한 값으로 오염된 API 계약
**File**: `engine/src/api/routes/portfolio.py:88`

```python
"total_pnl": round(total_pnl, 6),
"daily_pnl": round(total_pnl, 6),  # ← total_pnl 그대로 복사
```

`daily_pnl`은 당일 PnL(자정 이후 누적)이어야 하나, 실제로는 `total_pnl`(전체 누적)과 동일한 값을 반환함.
현재 대시보드 컴포넌트는 `portfolio.daily_pnl`을 화면에 사용하지 않아 UI 버그는 없으나, API 계약으로서 오해를 유발함. 향후 클라이언트가 이 필드를 신뢰하면 잘못된 정보를 표시하게 됨.

**Fix**: `ctx`에서 일별 PnL을 구분할 수 없는 경우 `null`을 반환하거나, 필드 자체를 제거할 것:
```python
# 옵션 A — null 반환 (정직한 계약)
"daily_pnl": None,

# 옵션 B — 필드 제거 + TypeScript 타입에서도 제거
# (대시보드가 이미 WS data?.pnl?.total 을 사용하므로 중복 불필요)
```

---

### [MEDIUM] 광의 `except Exception` 핸들러 — Phase 7.2.6 컨벤션 위반
**File**: `engine/src/api/routes/portfolio.py:79`

```python
try:
    position_count = len(pm.get_all_positions())
except Exception:          # ← 너무 넓은 포착
    position_count = len(ctx.positions)
```

Phase 7.2.6에서 코드베이스 전반의 bare `except Exception`을 구체적 타입으로 교체했음. 신규 파일에서 동일 패턴이 재도입됨.

**Fix**: 예상 가능한 예외 타입으로 좁힐 것:
```python
except (AttributeError, TypeError, RuntimeError):
    position_count = len(ctx.positions)
```

---

### [MEDIUM] `float(ctx.realized_pnl)` — None 시 unhandled TypeError
**File**: `engine/src/api/routes/portfolio.py:70-71`

```python
realized   = float(ctx.realized_pnl)    # None이면 TypeError
unrealized = float(ctx.unrealized_pnl)  # None이면 TypeError
```

`EngineContext` 기본값이 `Decimal(0)`이면 안전하나, 초기화 순서에 따라 `None`일 수 있음. `position_manager` 접근은 try/except로 보호되어 있지만 PnL 접근은 보호가 없어 일관성이 부족함.

**Fix**:
```python
realized   = float(ctx.realized_pnl   or 0)
unrealized = float(ctx.unrealized_pnl or 0)
```

---

### [LOW] 비공개 속성 `_balance_tracker` 직접 접근
**File**: `engine/src/api/routes/portfolio.py:26`

```python
tracker = getattr(shadow_mode, "_balance_tracker", None)
```

언더스코어로 시작하는 비공개 속성에 직접 접근하는 것은 구현 세부사항에 결합됨. `ShadowMode` 내부 리팩토링 시 무음 실패할 수 있음.

**Fix**: `ShadowMode`에 공개 프로퍼티/메서드(`balance_tracker` 또는 `get_balance_tracker()`) 노출 권장. 당장 변경이 어려우면 주석으로 의도 명시:
```python
# ShadowMode._balance_tracker: public API 없음, 내부 접근 (TODO: public property 추가)
tracker = getattr(shadow_mode, "_balance_tracker", None)
```

---

### [LOW] `portfolio.total_pnl` / `portfolio.daily_pnl` 대시보드에서 미사용
**File**: `dashboard/src/components/PortfolioSummary.tsx:99-100`

```tsx
const todayPnl = data?.pnl?.total ?? null;      // WS feed 사용
const totalPnl = pnlRest?.total_pnl ?? null;    // /pnl/total REST 사용
// portfolio.total_pnl, portfolio.daily_pnl → 사용하지 않음
```

`/portfolio-summary` 응답의 PnL 필드가 페칭되지만 UI에서 사용되지 않음. 네트워크 트래픽 낭비는 미미하나, API 계약과 UI 상태의 괴리가 생김.

**Fix**: 중기적으로 PnL 소싱 단일화 또는 `PortfolioSummaryResponse`에서 PnL 필드 제거 고려.

---

### [LOW] 클라이언트 잔고 fallback이 서버 계산과 乖離 가능
**File**: `dashboard/src/components/PortfolioSummary.tsx:104-110`

```tsx
const totalBalance = portfolio?.total_balance_usdt
  ?? (exchanges
    ? Object.values(exchanges).reduce((sum, ex) => {
        const usdt = ex.balance?.USDT ?? ex.balance?.usdt ?? 0;
        return sum + usdt;
      }, 0)
    : null);
```

서버의 `_get_exchange_balances()`도 `exchange_status`를 동일한 방식으로 읽으므로 일반적으로는 일치함. 그러나 서버가 Shadow Tracker를 사용할 때 클라이언트 fallback은 항상 exchange_status를 읽어 다른 값을 반환할 수 있음. 로딩 상태(portfolio가 아직 없고 exchanges도 없음)에서 `null`을 반환하는 경로는 올바름.

**Fix**: 현 구현 허용. 단, 향후 KRW 정규화가 서버 측에서만 적용되는 경우 client fallback을 제거해야 함.

---

## 패턴 일관성 검토

| 항목 | shadow.py | portfolio.py | 판정 |
|-----|-----------|--------------|------|
| `router = APIRouter(prefix="/api/v1")` | ✅ | ✅ | 일치 |
| `dependencies=[Depends(require_auth)]` | ✅ | ✅ | 일치 |
| `getattr(ctx, "shadow_mode", None)` | ✅ | ✅ | 일치 |
| `request: Request` 파라미터 | ✅ | ✅ | 일치 |
| `JSONResponse(...)` 반환 | ✅ | ✅ | 일치 |
| 라우터 등록 위치 (`server.py`) | — | ✅ | 올바른 위치 |

---

## 테스트 커버리지 평가

12개 테스트, 클래스별 분류:
- `TestPortfolioAuth` (2): 인증 없음/있음 → ✅
- `TestPortfolioEmptyContext` (1): 빈 컨텍스트 기본값 → ✅
- `TestPortfolioShadowMode` (1): VirtualBalanceTracker 반영 → ✅
- `TestPortfolioExchangeStatusFallback` (1): fallback 경로 → ✅
- `TestPortfolioCalculations` (2): pct_of_total, total 합계 → ✅
- `TestPortfolioFields` (5): PnL, mode, last_updated, position_manager, connected 병합 → ✅

누락된 테스트:
- `tracker.summary()` 파싱 실패 (ValueError/TypeError) 시 해당 거래소 제외 확인
- `ctx.realized_pnl = None` 케이스 (위 MEDIUM 이슈와 연관)

---

## Recommendation

**REQUEST CHANGES**

CRITICAL/HIGH 이슈는 없으나, MEDIUM 3건 수정 후 승인 권장:

1. `daily_pnl` 값 수정 (null 반환 또는 필드 제거)
2. `except Exception` → 구체적 타입으로 좁히기
3. `float(ctx.realized_pnl or 0)` 방어 코드 추가

보안 측면에서 `require_auth` 정상 적용됨. 데이터 소싱 우선순위(Shadow → exchange_status)도 스펙과 일치함.
