# US-065 Handoff: Shadow→Dashboard 데이터 브리지

> **Plan**: `docs/planning/US-065_PLAN.md` | **Phase**: H | **갱신**: 2026-03-11

---

## Backend (Jennie 담당: engine/src/** 만 수정)

### 1. shadow.py — get_snapshot() 공개 메서드
- `_send_summary()` (line 1611-1644) 패턴 재사용
- 반환 dict: active, uptime_seconds, signals_detected, trades_executed, trades_won, trades_lost, win_rate, total_pnl, peak_pnl, max_drawdown, trades_rejected, trades_partial_fill, trades_rate_limited, by_strategy[]
- by_strategy 각 항목: strategy_id, trades, wins, losses, win_rate, pnl

### 2. server.py — EngineContext 확장
- `shadow_mode: Any = None` 필드 추가 (line ~49)

### 3. routes/shadow.py — 새 REST 라우트
- `GET /api/v1/shadow/stats` (require_auth)
- trading.py 패턴 따르기 (prefix="/api/v1", Depends(require_auth))
- shadow_mode is None → {"active": false}

### 4. server.py — 라우터 마운트
- `from src.api.routes.shadow import router as shadow_router`
- `app.include_router(shadow_router)`

### 5. main.py — context 연결 + WS 피드
- `_shadow_mode_loop()` line ~1065: `self.context.shadow_mode = self._shadow_mode`
- `_dashboard_feed_loop()` line ~1390: data dict에 `"shadow_stats": shadow_stats` 추가
  ```python
  shadow_stats = None
  if self._shadow_mode and hasattr(self._shadow_mode, 'get_snapshot'):
      try:
          shadow_stats = self._shadow_mode.get_snapshot()
      except Exception:
          pass
  ```

## Frontend (Rosé 담당: dashboard/src/** 만 수정)

### 1. types/index.ts — 타입 추가
```typescript
export interface ShadowStrategyBreakdown {
  strategy_id: string; trades: number; wins: number;
  losses: number; win_rate: number; pnl: number;
}
export interface ShadowStats {
  active: boolean; uptime_seconds: number;
  signals_detected: number; trades_executed: number;
  trades_won: number; trades_lost: number; win_rate: number;
  total_pnl: number; peak_pnl: number; max_drawdown: number;
  trades_rejected: number; trades_partial_fill: number;
  trades_rate_limited: number; by_strategy: ShadowStrategyBreakdown[];
}
```
- `StateUpdateData`에 `shadow_stats?: ShadowStats | null` 추가

### 2. hooks/useEngineWs.ts — shadow_stats 추출
- state_update 핸들러에서 `msg.data.shadow_stats` 포함

### 3. lib/api.ts — REST 클라이언트
```typescript
export const getShadowStats = () => request<ShadowStats>("/api/v1/shadow/stats");
```

### 4. components/ShadowPanel.tsx — 새 UI 컴포넌트
- Shadow active badge + uptime
- PnL / WR / MDD 카드 3개
- 전략별 breakdown 테이블
- 조건부 렌더링: active일 때만

### 5. app/page.tsx — Overview에 ShadowPanel 추가
- `shadow_stats?.active` 조건부 렌더링

---

## 핵심 참고
- JWT 인증 미들웨어 적용 필수
- WS 피드는 1초 간격 state_update에 piggyback (별도 이벤트 불필요)
- shadow_mode가 None일 때 graceful 처리 필수

## 근본 원인 (이전 분석)
Shadow 모드의 거래 실행 경로가 FastAPI의 EngineContext에 데이터를 주입하지 않음.
- ShadowMode._execute_shadow_trade_request() → PnL 계산 ✓ → EngineContext ✗
- _dashboard_feed_loop() → EngineContext 읽기 → WebSocket broadcast → trade_history: []
