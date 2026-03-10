# US-065: Shadow→Dashboard 데이터 브리지

> **Phase**: H (대시보드 통합 완성) | **작성일**: 2026-03-11

---

## 1. 목표

ShadowMode 메트릭을 REST API + WebSocket 피드로 노출하여
대시보드 Overview에서 Shadow PnL/trades/WR 실시간 표시.

### Acceptance Criteria
1. ShadowMode 메트릭이 API 엔드포인트로 노출
2. 대시보드 Overview에서 Shadow PnL/trades/WR 실시간 표시
3. WebSocket 피드에 shadow_stats 이벤트 추가
4. Chrome 브라우저에서 데이터 렌더링 확인

---

## 2. 구현 계획

### Step 1: ShadowMode.get_snapshot() 공개 메서드 추가
**파일**: `engine/src/modes/shadow.py`
- `_stats`에 접근하는 thread-safe 공개 메서드
- `_send_summary()` 패턴 재사용 (line 1611-1644)
- 반환: dict (active, uptime_seconds, signals_detected, trades_executed, trades_won, trades_lost, win_rate, total_pnl, peak_pnl, max_drawdown, by_strategy[])

### Step 2: EngineContext에 shadow_mode 필드 추가
**파일**: `engine/src/api/server.py`
- EngineContext 데이터클래스에 `shadow_mode: Any = None` 추가

**파일**: `engine/src/main.py`
- `_shadow_mode_loop()`에서 ShadowMode 생성 후 `self.context.shadow_mode = self._shadow_mode` 설정

### Step 3: REST API 라우트 생성
**파일**: `engine/src/api/routes/shadow.py` (새 파일)
- `GET /api/v1/shadow/stats` — `require_auth` 의존성, EngineContext에서 shadow_mode 접근
- Shadow 비활성 시 `{"active": false, "message": "Shadow mode not running"}` 반환

**파일**: `engine/src/api/server.py`
- shadow_router 마운트 (`app.include_router`)

### Step 4: WebSocket 피드 보강
**파일**: `engine/src/main.py`
- `_dashboard_feed_loop()` (line 1339-1398)의 state_update payload에 `shadow_stats` 추가
- `self._shadow_mode.get_snapshot()` 호출 (None 시 null)

### Step 5: Dashboard TypeScript 타입 + 훅
**파일**: `dashboard/src/types/index.ts`
- `ShadowStats`, `ShadowStrategyBreakdown` 인터페이스 추가
- `StateUpdateData`에 `shadow_stats?: ShadowStats | null` 추가

**파일**: `dashboard/src/hooks/useEngineWs.ts`
- `state_update` 핸들러에서 `shadow_stats` 추출

**파일**: `dashboard/src/lib/api.ts`
- `getShadowStats()` REST 클라이언트 추가

### Step 6: Dashboard ShadowPanel UI
**파일**: `dashboard/src/components/ShadowPanel.tsx` (새 파일)
- Shadow active 상태 + uptime
- PnL, Win Rate, Max Drawdown KPI 카드
- 전략별 성과 테이블

**파일**: `dashboard/src/app/page.tsx`
- Overview에 ShadowPanel 조건부 렌더링 (`shadow_stats?.active`)

---

## 3. 파일 변경 요약

| 파일 | 변경 유형 |
|------|----------|
| engine/src/modes/shadow.py | 수정: get_snapshot() 추가 |
| engine/src/api/server.py | 수정: EngineContext + router mount |
| engine/src/api/routes/shadow.py | 새 파일: REST 라우트 |
| engine/src/main.py | 수정: context 연결 + WS 피드 보강 |
| dashboard/src/types/index.ts | 수정: ShadowStats 타입 추가 |
| dashboard/src/hooks/useEngineWs.ts | 수정: shadow_stats 추출 |
| dashboard/src/lib/api.ts | 수정: getShadowStats 추가 |
| dashboard/src/components/ShadowPanel.tsx | 새 파일: UI 컴포넌트 |
| dashboard/src/app/page.tsx | 수정: ShadowPanel 렌더링 |
