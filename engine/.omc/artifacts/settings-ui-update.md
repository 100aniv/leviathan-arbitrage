# Settings UI Update — 2026-03-24

## 변경 파일

### 1. `/Users/100aniv/Development/arbitrage_OMC/dashboard/src/types/index.ts`
- `SettingsResponse` 인터페이스에 4개 필드 추가 (optional):
  - `max_position_usd?: number`
  - `capital_per_exchange_usd?: number`
  - `max_daily_loss_usd?: number`
  - `execution_mode?: "paper" | "shadow" | "live"`

### 2. `/Users/100aniv/Development/arbitrage_OMC/dashboard/src/lib/api.ts`
- `updateMode(mode: string)` 함수 추가:
  - `PATCH /api/v1/settings/mode` 호출
  - 반환 타입: `{ mode: string }`

### 3. `/Users/100aniv/Development/arbitrage_OMC/dashboard/src/app/settings/page.tsx`
- **실행 모드 섹션** (맨 위, 거래 파라미터 위):
  - 3-카드 그리드: 페이퍼 모드 / 섀도 모드 / 라이브 모드
  - 활성 모드 accent 하이라이트 + "● 활성" 배지
  - 라이브 카드에 "⚠ LiveGate 필요" warn 배지
  - `handleSelectMode()` → `updateMode()` PATCH 호출
- **자본 설정 섹션** (실행 모드 다음):
  - 거래소당 자본 ($) — 기본값 $70
  - 최대 포지션 ($) — 기본값 $5000
  - 최대 일일 손실 ($) — 기본값 $500
  - 저장 버튼 → `handleSaveCapital()` → `updateSettings()` PUT 호출
- **거래 파라미터 섹션** (기존 유지 + 확장):
  - MIN_EDGE_BPS 기존 유지
  - MAX_POSITION_USD 표시 필드 추가 (readOnly, "자본 설정에서 관리" 안내)
- 새 state: `maxPosition`, `capitalPerExchange`, `maxDailyLoss`, `capitalSaving`, `modeSaving`
- 새 상수: `MODE_LABELS`, `MODE_DESCRIPTIONS`
- useEffect에서 API 응답의 새 필드로 state 초기화

## 검증
- TypeScript: `npx tsc --noEmit` → 0 errors, 0 warnings
- API 에러 시 피드백 메시지 표시 (3초 후 자동 사라짐)
- 기존 기능 (전략 토글, 거래소 선택, Danger Zone) 변경 없음
