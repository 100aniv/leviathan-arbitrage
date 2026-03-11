# Phase J-EXT Wave 3 Batch 2: US-116 + US-120 아키텍처 플랜

**작성자**: Architect
**날짜**: 2026-03-12
**대상 US**: US-116 (TCA 모듈 + 실행 레이턴시 위젯), US-120 (인벤토리 리밸런싱 통합)

---

## Summary

US-116은 신규 `engine/src/analysis/tca.py` 모듈 + API 라우트 + 대시보드 위젯으로 구성된다.
US-120은 `engine/src/core/inventory_rebalancer.py`가 **이미 완성** 상태이나
`main.py` wiring과 Telegram 알람 연동이 누락 → 추가만 하면 된다.

---

## US-116: TCA 모듈 + 실행 레이턴시 위젯

### 현황 파악

| 항목 | 상태 |
|------|------|
| `engine/src/analysis/` | `attribution.py`, `signal_analyzer.py`, `walk_forward.py` 존재 — `tca.py` **없음** |
| `engine/src/core/latency_tracker.py` | 거래소 WS 레이턴시 EMA 추적 — **실행 레이턴시와 무관** |
| `engine/src/api/routes/` | `attribution.py`, `trading.py` 등 — TCA 라우트 **없음** |
| `engine/src/api/server.py` → `EngineContext` | `tca_analyzer` 필드 없음 (Wave 3 필드는 57-58행에 패턴 존재) |
| `dashboard/src/app/system/page.tsx` | Docker + Exchange 상태 표시 — TCA 위젯 없음 (291행 이후 삽입 위치) |

### 구현 계획

#### 1. `engine/src/analysis/tca.py` (신규)

```python
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

@dataclass
class ExecutionRecord:
    expected_price: float   # signal 생성 시점 mid-price
    fill_price: float       # 실제 체결가
    latency_ms: float       # signal → 체결 완료 ms
    filled_ratio: float     # 실제체결/요청 (0.0~1.0)
    timestamp: datetime
    strategy_id: str = ""

class PercentileTracker:
    """deque 기반 rolling window P50/P95/P99 계산 (numpy 불필요)."""
    def __init__(self, window_size: int = 1000) -> None:
        self._data: deque[float] = deque(maxlen=window_size)

    def add(self, value: float) -> None: ...
    def percentile(self, pct: float) -> float: ...  # sorted + index

class TCAAnalyzer:
    def __init__(self, window_size: int = 1000) -> None: ...

    def record_execution(
        self,
        expected_price: float,
        fill_price: float,
        latency_ms: float,
        filled_ratio: float,
        strategy_id: str = "",
    ) -> None:
        """체결 완료 시점에 호출. IS·latency·fill_rate 누적."""

    def get_summary(self) -> dict:
        """Returns: {is_p50_bps, is_p95_bps, latency_p50_ms,
                     latency_p95_ms, latency_p99_ms,
                     fill_rate_pct, sample_count}"""
```

**Implementation Shortfall 공식**:
```
IS = abs(fill_price - expected_price) / expected_price × 10_000  [bps]
```
- 부호 무관 단방향 표기 (양수 = 마찰 비용)
- BUY/SELL 방향 보정 불필요 — abs() 취함

#### 2. `engine/src/api/routes/tca.py` (신규)

```python
router = APIRouter()

@router.get("/summary")
async def get_tca_summary(request: Request):
    ctx = request.app.state.context
    if ctx.tca_analyzer is None:
        return {"sample_count": 0}
    return ctx.tca_analyzer.get_summary()
```

엔드포인트: `GET /api/v1/tca/summary`

#### 3. `engine/src/api/server.py` 변경 (2곳)

```python
# EngineContext 필드 추가 (57-58행 Wave 3 패턴 이후)
tca_analyzer: Any = None  # US-116

# create_app() 라우터 등록
from src.api.routes import tca as tca_router
app.include_router(tca_router.router, prefix="/api/v1/tca", tags=["tca"])
```

#### 4. `engine/src/main.py` 변경 (4곳)

**`__init__`** — 필드 추가 (99-103행 Wave 3 선언 블록 이후):
```python
self._tca_analyzer: Any = None  # US-116
```

**`_init_execution()`** — SlippageFeedbackLoop 초기화 패턴(705-711행) 이후:
```python
# US-116: TCAAnalyzer
try:
    from src.analysis.tca import TCAAnalyzer
    self._tca_analyzer = TCAAnalyzer(window_size=1000)
    logger.info("TCAAnalyzer initialized (window=1000)")
except Exception as exc:
    logger.warning("TCAAnalyzer init failed (non-fatal): %s", exc)
```

**`_on_execution_result()`** — US-115 피드 블록(761-772행) 이후:
```python
# US-116: Feed TCA data
if self._tca_analyzer is not None:
    import time
    try:
        for leg in execution_result.legs:
            if leg.trade is not None:
                # latency: signal submit → fill (근사값; TradeRequest에 _submit_ts 없으면 0)
                latency_ms = getattr(trade_request, '_submit_latency_ms', 0.0)
                self._tca_analyzer.record_execution(
                    expected_price=float(
                        getattr(trade_request, 'expected_price', None)
                        or leg.order.price or 0
                    ),
                    fill_price=float(leg.trade.price),
                    latency_ms=latency_ms,
                    filled_ratio=float(leg.fill_ratio(leg.order.amount)),
                    strategy_id=trade_request.strategy_id,
                )
    except Exception:
        pass  # Non-critical
```

**`_populate_context()`** — Wave 3 context 블록(824-827행) 이후:
```python
self.context.tca_analyzer = self._tca_analyzer
```

#### 5. `dashboard/src/components/TCAWidget.tsx` (신규)

```tsx
'use client';
import { useApi } from '@/hooks/useApi';
import { getTCASummary } from '@/lib/api';

export function TCAWidget() {
  const { data } = useApi('/tca/summary', getTCASummary, { refreshInterval: 5000 });
  // 3-카드 레이아웃 (System 탭 카드 패턴 동일):
  //   [1] Implementation Shortfall: IS P50 / P95 (bps)
  //   [2] Execution Latency: P50 / P95 / P99 (ms)
  //   [3] Fill Rate: % (N fills)
}
```

#### 6. `dashboard/src/app/system/page.tsx` 변경

Resource Usage 섹션(263-291행) 이후에 `<TCAWidget />` 삽입.

#### 7. `dashboard/src/lib/api.ts` + `src/types/index.ts` 변경

```ts
// api.ts
export async function getTCASummary(): Promise<TCASummary> {
  return apiFetch('/api/v1/tca/summary');
}

// types/index.ts
export interface TCASummary {
  is_p50_bps: number;
  is_p95_bps: number;
  latency_p50_ms: number;
  latency_p95_ms: number;
  latency_p99_ms: number;
  fill_rate_pct: number;
  sample_count: number;
}
```

### 검증 기준 — US-116
- [ ] `PercentileTracker` P50/P95/P99 단위 테스트 (정렬 인덱스 검증)
- [ ] `IS = 0 bps` (expected == fill) 엣지케이스 처리
- [ ] `GET /api/v1/tca/summary` → 200 + 7개 필드 존재
- [ ] System 탭 TCAWidget 렌더링 확인 (sample_count=0 빈 상태 포함)
- [ ] `engine/src/analysis/__init__.py`에 `TCAAnalyzer` export 추가

---

## US-120: 인벤토리 리밸런싱 통합 확인

### 현황 파악

| 항목 | 상태 |
|------|------|
| `engine/src/core/inventory_rebalancer.py:26-143` | **완성** — `InventoryRebalancer`, `check_and_suggest()`, `has_critical_imbalance()` |
| `engine/tests/unit/core/test_inventory_rebalancer.py` | 테스트 존재 |
| `engine/src/main.py` | `_rebalancer` 필드 없음, wiring 없음, background loop 없음 |
| Telegram 알람 | `TelegramAlerter` 존재(`engine/src/infra/telegram.py`) — rebalancer 연결 없음 |

### 설계 결정: alert_callback vs 루프 내 직접 호출

기존 `InventoryRebalancer`는 `alert_callback` 파라미터가 없음.
두 가지 방법:

| 방법 | 장점 | 단점 |
|------|------|------|
| **A: `_rebalancer_loop()` 내 직접 Telegram 호출** (채택) | rebalancer.py 수정 불필요, Wave 3 패턴 일치 | loop 로직이 main.py에 집중 |
| B: rebalancer.py에 `alert_callback` 추가 | 재사용성↑ | 기존 테스트 변경 필요, 인터페이스 확장 |

**채택: 방법 A** — `inventory_rebalancer.py` 수정 없이 `main.py`에서 처리.

### 구현 계획

#### 1. `engine/src/main.py` — `__init__` 필드 추가

```python
self._rebalancer: Any = None  # US-120
```

#### 2. `_init_execution()` — CorrelationMonitor 패턴(670-678행) 이후

```python
# US-120: InventoryRebalancer
try:
    from src.core.inventory_rebalancer import InventoryRebalancer
    from src.core.balance_tracker import BalanceTracker
    _balance_tracker = BalanceTracker()
    self._rebalancer = InventoryRebalancer(
        tracker=_balance_tracker,
        deviation_threshold=float(os.getenv("REBALANCER_DEVIATION_THRESHOLD", "0.30")),
        check_interval_s=float(os.getenv("REBALANCER_CHECK_INTERVAL_S", "14400")),
        min_transfer_usd=float(os.getenv("REBALANCER_MIN_TRANSFER_USD", "50")),
    )
    logger.info(
        "InventoryRebalancer initialized (threshold=%.0f%%, interval=%.0fh)",
        self._rebalancer.deviation_threshold * 100,
        self._rebalancer.check_interval_s / 3600,
    )
except Exception as exc:
    logger.warning("InventoryRebalancer init failed (non-fatal): %s", exc)
```

#### 3. `_start_background_tasks()` — tasks 리스트에 추가

```python
if self._rebalancer is not None:
    tasks.append(asyncio.create_task(
        self._rebalancer_loop(), name="rebalancer"
    ))
```

#### 4. `_rebalancer_loop()` 신규 메서드

```python
async def _rebalancer_loop(self) -> None:
    """US-120: 4h 주기 리밸런싱 체크 + Telegram 알람."""
    while self.state.running:
        try:
            await asyncio.sleep(self._rebalancer.check_interval_s)

            # Critical imbalance 먼저 체크 (2× threshold)
            if self._rebalancer.has_critical_imbalance() and self._telegram:
                await self._telegram.send_alert(
                    "🚨 인벤토리 심각한 불균형 감지! 즉시 확인 필요.",
                    level="CRITICAL",
                )

            suggestions = self._rebalancer.check_and_suggest()
            if suggestions and self._telegram:
                lines = [f"⚠️ 인벤토리 리밸런싱 필요 ({len(suggestions)}건)"]
                for s in suggestions:
                    lines.append(
                        f"  {s.from_exchange} → {s.to_exchange}: "
                        f"${s.amount_usd:.0f} ({s.reason})"
                    )
                await self._telegram.send_alert("\n".join(lines), level="WARNING")

        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("rebalancer_loop error: %s", exc)
```

#### 5. `_populate_context()` 추가 (선택적)

```python
# US-120 (optional API 노출용)
# self.context.rebalancer = self._rebalancer
```

### 검증 기준 — US-120
- [ ] 엔진 시작 로그에 `InventoryRebalancer initialized` 확인
- [ ] `has_critical_imbalance()` True 시 CRITICAL 레벨 Telegram 발송 단위 테스트
- [ ] `check_and_suggest()` suggestion 발생 시 WARNING 레벨 메시지 형식 검증
- [ ] `REBALANCER_DEVIATION_THRESHOLD=0.10` 환경변수 override 확인
- [ ] `asyncio.CancelledError` 로 루프 graceful 종료 확인
- [ ] `BalanceTracker` import 성공 여부 (없으면 non-fatal 로그로 skip)

---

## 파일 변경 목록

### 신규 파일
| 파일 | 크기 추정 |
|------|-----------|
| `engine/src/analysis/tca.py` | ~120 LOC |
| `engine/src/api/routes/tca.py` | ~35 LOC |
| `dashboard/src/components/TCAWidget.tsx` | ~80 LOC |

### 수정 파일
| 파일 | 변경 내용 |
|------|-----------|
| `engine/src/main.py` | `__init__` 2개 필드; `_init_execution()` 2개 init 블록; `_on_execution_result()` TCA feed; `_start_background_tasks()` rebalancer task; `_rebalancer_loop()` 신규; `_populate_context()` tca_analyzer |
| `engine/src/api/server.py` | `EngineContext.tca_analyzer`; `create_app()` TCA 라우터 등록 |
| `engine/src/analysis/__init__.py` | `TCAAnalyzer` export |
| `dashboard/src/app/system/page.tsx` | `TCAWidget` import + 섹션 삽입 |
| `dashboard/src/lib/api.ts` | `getTCASummary()` |
| `dashboard/src/types/index.ts` | `TCASummary` 인터페이스 |

---

## Trade-offs

| 항목 | 채택 | 대안 |
|------|------|------|
| TCA latency 측정 | `_on_execution_result` 콜백 근사값 | Executor 내부 직접 계측 (더 정밀, 구조 변경 필요) |
| PercentileTracker | `sorted() + index` (numpy 불필요) | `numpy.percentile` (빠름, 의존성 추가) |
| US-120 Telegram | `_rebalancer_loop()` 직접 호출 | `inventory_rebalancer.py`에 `alert_callback` 추가 |
| US-120 wiring 위치 | `_init_execution()` (Wave 3 패턴 일치) | `_init_risk()` (의미적으로 더 적합) |

---

## References

- `engine/src/core/inventory_rebalancer.py:26-143` — 완성 구현 확인
- `engine/src/analysis/attribution.py:42-107` — PerformanceAttribution 패턴 참조
- `engine/src/core/latency_tracker.py:19-69` — deque 기반 tracker 패턴
- `engine/src/main.py:99-103` — Wave 3 모듈 필드 선언 패턴
- `engine/src/main.py:705-712` — SlippageFeedbackLoop wiring 참조 (US-116 동일)
- `engine/src/main.py:670-678` — CorrelationMonitor wiring 참조 (US-120 동일)
- `engine/src/api/server.py:54-58` — EngineContext Wave 3 필드 패턴
- `dashboard/src/app/system/page.tsx:263-291` — Resource Usage 섹션 (TCAWidget 삽입 위치)
