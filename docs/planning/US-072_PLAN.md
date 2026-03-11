# US-072: 계좌 정보/총자산/거래소별 잔고 표시 — 구현 계획

> Phase H | Priority 76 | Architect: Claude Opus 4.6
> 작성일: 2026-03-11

---

## 1. 현재 상태 분석

### 1.1 기존 데이터 소스

현재 PortfolioSummary 컴포넌트(`dashboard/src/components/PortfolioSummary.tsx:38-109`)는 두 가지 데이터 소스를 사용:

1. **WebSocket (`useEngineWs`)**: `StateUpdateData` — running, kill_switch, pnl.total, position_count
2. **REST (`GET /api/v1/exchanges`)**: `Record<string, ExchangeStatus>` — 거래소별 connected, latency_ms, balance

총 잔고 계산은 프론트엔드에서 수행(`PortfolioSummary.tsx:58-63`):
```typescript
const totalBalance = exchanges
  ? Object.values(exchanges).reduce((sum, ex) => {
      const usdt = ex.balance?.USDT ?? ex.balance?.usdt ?? 0;
      return sum + usdt;
    }, 0)
  : null;
```

### 1.2 문제점

| # | 문제 | 영향 |
|---|------|------|
| 1 | `/exchanges`의 `balance` 필드가 `exchange_status` dict에서 직접 전달되지만, **main.py에서 `exchange_status`를 채우는 로직이 없음** | Shadow/Paper 모드에서 balance가 항상 빈 dict |
| 2 | Shadow 모드에서 VirtualBalanceTracker(`shadow.py:193-247`)가 거래소별 잔고를 추적하지만, 이 데이터가 API로 노출되지 않음 | 대시보드에서 Shadow 잔고 확인 불가 |
| 3 | 총 포트폴리오 가치 계산이 클라이언트 측에서만 수행됨 | 서버-클라이언트 간 계산 불일치 가능 |
| 4 | 거래소별 잔고 breakdown UI가 없음 (ExchangeStatusBar는 연결 상태만 표시) | AC 2 미충족 |

### 1.3 데이터 흐름 파악

```
[현재]
EngineContext.exchange_status (dict, 미채워짐)
  → GET /api/v1/exchanges
    → PortfolioSummary.tsx: client-side 합산

[Shadow 모드]
ShadowMode._balance_tracker._balances (dict[str, Decimal])
  → ShadowMode.get_snapshot() (balance 미포함!)
    → GET /api/v1/shadow/stats
    → WS state_update.shadow_stats
```

---

## 2. 설계: GET /api/v1/portfolio-summary

### 2.1 응답 스키마

```python
# engine/src/api/routes/portfolio.py

PortfolioSummaryResponse = {
    "total_balance_usdt": float,         # 전 거래소 USDT 잔고 합산
    "total_pnl": float,                  # realized + unrealized
    "daily_pnl": float,                  # 당일 PnL (WS state_update.pnl.total과 동일)
    "active_positions": int,             # 현재 활성 포지션 수
    "exchange_balances": [               # 거래소별 잔고 배열
        {
            "exchange_id": str,          # "binance", "upbit", etc.
            "balance_usdt": float,       # 해당 거래소 USDT 잔고
            "connected": bool,           # 연결 상태
            "pct_of_total": float,       # 전체 대비 비율 (0.0~1.0)
        }
    ],
    "mode": str,                         # "shadow" | "paper" | "live"
    "last_updated": str,                 # ISO 8601 타임스탬프
}
```

### 2.2 데이터 소싱 전략

모드에 따라 잔고 소스가 달라짐:

| 모드 | 잔고 소스 | 근거 |
|------|----------|------|
| **Shadow** | `ShadowMode._balance_tracker._balances` | VirtualBalanceTracker가 거래소별 가상 잔고를 추적 (US-061) |
| **Paper** | `EngineContext.exchange_status[ex].balance` | Paper 모드에서 채워지면 사용, 없으면 fallback |
| **Live** | 거래소 REST API (향후) | Live 모드 미구현 — fallback 사용 |
| **Fallback** | `SHADOW_INITIAL_BALANCE_USDT` (기본 $10M) per exchange | 데이터 없을 때 초기값 |

### 2.3 백엔드 구현 상세

#### 파일: `engine/src/api/routes/portfolio.py` (신규)

```python
"""Portfolio summary route — total balance, per-exchange breakdown."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from src.api.auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


def _get_exchange_balances(ctx: Any) -> list[dict[str, Any]]:
    """Build per-exchange balance list from available data sources."""
    balances: dict[str, float] = {}

    # Source 1: Shadow mode VirtualBalanceTracker (highest priority)
    shadow_mode = getattr(ctx, "shadow_mode", None)
    if shadow_mode is not None:
        tracker = getattr(shadow_mode, "_balance_tracker", None)
        if tracker is not None:
            for ex_id, bal in tracker._balances.items():
                balances[ex_id] = float(bal)

    # Source 2: exchange_status balance field (fallback / Paper / Live)
    if not balances and ctx.exchange_status:
        for ex_id, status in ctx.exchange_status.items():
            bal = status.get("balance", {})
            usdt = bal.get("USDT", bal.get("usdt", 0.0))
            balances[ex_id] = float(usdt)

    # Build response with connection info
    result = []
    total = sum(balances.values()) if balances else 0.0

    for ex_id, bal in sorted(balances.items()):
        connected = False
        if ctx.exchange_status and ex_id in ctx.exchange_status:
            connected = ctx.exchange_status[ex_id].get("connected", False)

        result.append({
            "exchange_id": ex_id,
            "balance_usdt": round(bal, 2),
            "connected": connected,
            "pct_of_total": round(bal / total, 4) if total > 0 else 0.0,
        })

    return result


@router.get("/portfolio-summary", dependencies=[Depends(require_auth)])
async def get_portfolio_summary(request: Request) -> JSONResponse:
    """Return portfolio summary with per-exchange breakdown."""
    ctx = request.app.state.engine_context

    exchange_balances = _get_exchange_balances(ctx)
    total_balance = sum(eb["balance_usdt"] for eb in exchange_balances)

    # PnL: reuse trading route logic
    realized = float(ctx.realized_pnl)
    unrealized = float(ctx.unrealized_pnl)
    total_pnl = realized + unrealized

    # Position count
    position_count = 0
    if ctx.position_manager is not None:
        try:
            position_count = len(ctx.position_manager.get_all_positions())
        except Exception:
            pass
    else:
        position_count = len(ctx.positions)

    return JSONResponse({
        "total_balance_usdt": round(total_balance, 2),
        "total_pnl": round(total_pnl, 6),
        "daily_pnl": round(total_pnl, 6),  # same as total in current session
        "active_positions": position_count,
        "exchange_balances": exchange_balances,
        "mode": ctx.execution_mode,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    })
```

#### 라우터 등록: `engine/src/api/server.py`

`create_app()` 함수에 추가:
```python
from src.api.routes.portfolio import router as portfolio_router
app.include_router(portfolio_router)
```

위치: `server.py:123` 부근, 기존 라우터 import/include 블록 끝에 추가.

### 2.4 프론트엔드 확장

#### 2.4.1 타입 추가: `dashboard/src/types/index.ts`

```typescript
// ─── Portfolio Summary Types ─────────────────────────────────────────────────

export interface ExchangeBalance {
  exchange_id: string;
  balance_usdt: number;
  connected: boolean;
  pct_of_total: number;
}

export interface PortfolioSummaryResponse {
  total_balance_usdt: number;
  total_pnl: number;
  daily_pnl: number;
  active_positions: number;
  exchange_balances: ExchangeBalance[];
  mode: string;
  last_updated: string;
}
```

#### 2.4.2 API 함수 추가: `dashboard/src/lib/api.ts`

```typescript
// ─── Portfolio ───────────────────────────────────────────────────────────────

export const getPortfolioSummary = () =>
  request<PortfolioSummaryResponse>("/api/v1/portfolio-summary");
```

#### 2.4.3 PortfolioSummary.tsx 확장

**변경 범위**: `PortfolioSummary.tsx` 전체 리팩터링

핵심 변경사항:
1. **데이터 소스 전환**: `getExchangeStatus` -> `getPortfolioSummary` (서버 측 합산)
2. **거래소별 잔고 breakdown 테이블 추가**: ExchangeStatusBar 아래에 펼침/접힘 가능한 상세 테이블
3. **기존 4 KPI 유지**: Total Balance, Today PnL, Total PnL, Active Positions
4. **WS 실시간 데이터와 병합**: WS에서 running/kill_switch/position_count, REST에서 balance

```typescript
// 주요 변경 부분

// 기존: getExchangeStatus + client-side 합산
// 변경: getPortfolioSummary (서버 합산) + getExchangeStatus (연결 상태 바 유지)

const { data: portfolio } = useApi<PortfolioSummaryResponse>(
  '/portfolio-summary',
  getPortfolioSummary,
  { refreshInterval: 10000 },
);

// Total Balance는 이제 서버에서 계산
const totalBalance = portfolio?.total_balance_usdt ?? null;

// 거래소별 잔고 breakdown 섹션 (접기/펼치기)
function ExchangeBalanceBreakdown({ balances }: { balances: ExchangeBalance[] }) {
  const [expanded, setExpanded] = useState(false);
  if (balances.length === 0) return null;

  return (
    <div className="mt-3">
      <button onClick={() => setExpanded(!expanded)} className="...">
        Exchange Balances ({balances.length}) {expanded ? '▲' : '▼'}
      </button>
      {expanded && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-2">
          {balances.map(eb => (
            <div key={eb.exchange_id} className="...">
              <span>{eb.exchange_id}</span>
              <span>${eb.balance_usdt.toLocaleString()}</span>
              <span>{(eb.pct_of_total * 100).toFixed(1)}%</span>
              <span className={eb.connected ? 'text-profit' : 'text-loss'}>
                {eb.connected ? '●' : '○'}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
```

---

## 3. 테스트 계획

### 3.1 백엔드 테스트: `engine/tests/unit/api/test_portfolio.py` (신규)

| # | 테스트 | 검증 내용 |
|---|--------|----------|
| 1 | `test_returns_200` | 인증 상태에서 200 OK |
| 2 | `test_requires_auth` | 미인증 시 401 |
| 3 | `test_empty_context_returns_defaults` | 빈 context에서 total_balance=0, empty exchange_balances |
| 4 | `test_shadow_balance_tracker` | shadow_mode._balance_tracker 데이터 반영 확인 |
| 5 | `test_exchange_status_fallback` | shadow 없을 때 exchange_status.balance 사용 |
| 6 | `test_pct_of_total_calculation` | 비율 합산 = 1.0 |
| 7 | `test_total_balance_sum` | total_balance_usdt == sum(exchange_balances[].balance_usdt) |
| 8 | `test_position_count_from_manager` | position_manager 있을 때 정확한 카운트 |
| 9 | `test_pnl_fields` | total_pnl, daily_pnl 값 정확성 |
| 10 | `test_mode_field` | execution_mode 반영 |
| 11 | `test_last_updated_iso_format` | ISO 8601 형식 검증 |
| 12 | `test_connected_status_merged` | exchange_status 연결 상태가 balance에 병합 |

예상 테스트 수: **12개**

### 3.2 프론트엔드 테스트: `dashboard/src/__tests__/components/PortfolioSummary.test.tsx` (확장)

| # | 테스트 | 검증 내용 |
|---|--------|----------|
| 1 | 기존 6개 유지 | 상태 배지, KPI 카드, 교환소 상태 바 |
| 2 | `test_renders_total_balance_from_api` | 서버 합산 잔고 표시 |
| 3 | `test_renders_exchange_breakdown_toggle` | 펼침/접힘 동작 |
| 4 | `test_renders_exchange_balance_list` | 거래소별 잔고 + % |
| 5 | `test_fallback_when_portfolio_api_unavailable` | API 실패 시 dash 표시 |

예상 추가 테스트 수: **4개**

---

## 4. 변경 파일 목록

| 파일 | 변경 유형 | 설명 |
|------|----------|------|
| `engine/src/api/routes/portfolio.py` | **신규** | GET /api/v1/portfolio-summary 엔드포인트 |
| `engine/src/api/server.py` | 수정 (2줄) | portfolio_router import + include |
| `engine/tests/unit/api/test_portfolio.py` | **신규** | 백엔드 12개 테스트 |
| `dashboard/src/types/index.ts` | 수정 (+12줄) | ExchangeBalance, PortfolioSummaryResponse |
| `dashboard/src/lib/api.ts` | 수정 (+3줄) | getPortfolioSummary 함수 |
| `dashboard/src/components/PortfolioSummary.tsx` | 수정 | 서버 API 연동 + 거래소별 breakdown |
| `dashboard/src/__tests__/components/PortfolioSummary.test.tsx` | 수정 | 4개 테스트 추가 |

예상 총 변경량: ~250줄 (신규 ~200 + 수정 ~50)

---

## 5. 수용 기준 매핑

| AC | 구현 | 검증 방법 |
|----|------|----------|
| GET /api/v1/portfolio-summary 엔드포인트 구현 | `engine/src/api/routes/portfolio.py` | `test_portfolio.py` 12개 테스트 |
| 거래소별 잔고 합산 표시 | `exchange_balances[]` + ExchangeBalanceBreakdown 컴포넌트 | 프론트엔드 테스트 + Chrome 확인 |
| 총 포트폴리오 가치 계산 | `total_balance_usdt` 서버 합산 | `test_total_balance_sum` |
| Chrome 브라우저에서 정상 렌더링 | PortfolioSummary 확장 + 반응형 grid | `browser-verifier` 또는 수동 확인 |

---

## 6. 구현 순서

```
Step 1: engine/src/api/routes/portfolio.py 생성 (신규 라우트)
Step 2: engine/src/api/server.py에 라우터 등록 (2줄)
Step 3: engine/tests/unit/api/test_portfolio.py 작성 + pytest 통과 확인
Step 4: dashboard/src/types/index.ts 타입 추가
Step 5: dashboard/src/lib/api.ts API 함수 추가
Step 6: dashboard/src/components/PortfolioSummary.tsx 확장
Step 7: dashboard/src/__tests__/components/PortfolioSummary.test.tsx 테스트 확장
Step 8: npm run build 통과 확인
Step 9: (Step 2.5 통합검증) pytest 전체 통과 + 타입 에러 0
```

---

## 7. 리스크 및 트레이드오프

| 리스크 | 완화책 |
|--------|--------|
| Shadow `_balance_tracker._balances`에 직접 접근 (private 필드) | `VirtualBalanceTracker.summary()` 공개 메서드가 이미 존재 (`shadow.py:245-247`). 이를 활용하면 private 접근 회피 가능. 단, `summary()`는 str 반환이므로 float 변환 필요 |
| `exchange_status`가 main.py에서 미채워짐 | Paper/Live 모드에서는 별도 이슈. US-072는 Shadow 모드 잔고가 주 타겟. Paper 모드는 초기값 fallback으로 대응 |
| KRW 거래소 잔고가 KRW 단위일 수 있음 | VirtualBalanceTracker는 USDT 단위로만 추적 (`initial_balance_usdt`). KRW 변환 불필요 |
| `get_snapshot()`에 balance 정보 미포함 | `get_snapshot()` 확장보다 별도 엔드포인트가 관심사 분리에 적합. WS feed에 balance 추가는 선택 사항 (10s REST polling으로 충분) |

### 트레이드오프: 서버 합산 vs 클라이언트 합산

| 옵션 | 장점 | 단점 |
|------|------|------|
| **서버 합산 (선택)** | 단일 진실 소스, 다중 클라이언트 일관성, 복잡한 모드별 로직 서버에 캡슐화 | 새 엔드포인트 필요, 약간의 서버 부하 |
| 클라이언트 합산 (현재) | 기존 코드 변경 최소, 추가 API 불필요 | 모드별 분기 클라이언트 누출, Shadow balance 접근 불가 |

---

## 8. 아키텍처 노트

- **기존 패턴 준수**: `shadow.py`, `exchanges.py`, `attribution.py`와 동일한 구조 (APIRouter + EngineContext + require_auth)
- **ShadowMode.get_snapshot() 미수정**: balance는 portfolio 관심사이지 shadow stats 관심사가 아님. 관심사 분리 유지
- **WS feed에 balance 미추가**: 1s 간격 WS 브로드캐스트에 8개 거래소 잔고를 매초 보내는 것은 과도. 10s REST polling이 적절
- **`VirtualBalanceTracker.summary()` 활용**: private `_balances` 직접 접근 대신 공개 메서드 사용 권장
