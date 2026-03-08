# US-038: Settings 페이지 + Logout 기능

## Acceptance Criteria
1. MIN_EDGE_BPS, 전략 활성/비활성, 거래소 선택 UI
2. .env 직접 수정 없이 설정 변경 가능
3. cookie 삭제 + redirect 로그아웃 동작
4. npm run build 성공

## 아키텍처

### Backend (engine/src/api/)
- `routes/settings.py` (NEW): GET/PUT /api/v1/settings
  - GET: 현재 설정 반환 (min_edge_bps, active strategies, exchanges)
  - PUT: 런타임 설정 변경 (EngineContext에 저장, .env 수정 없음)
- `server.py`: EngineContext에 `settings: dict` 필드 추가 + settings router mount

### Frontend (dashboard/src/)
- `app/settings/page.tsx` (NEW): Settings 페이지
  - MIN_EDGE_BPS 슬라이더/입력
  - 전략별 활성/비활성 토글
  - 거래소 체크박스
  - Logout 버튼
- `components/Sidebar.tsx`: Settings 네비게이션 추가
- `lib/api.ts`: getSettings(), updateSettings(), logout() 추가
- `types/index.ts`: SettingsResponse 타입 추가

## 파일 변경 목록
| 파일 | 변경 유형 | 담당 |
|------|----------|------|
| engine/src/api/routes/settings.py | NEW | Jennie |
| engine/src/api/server.py | EDIT | Jennie |
| dashboard/src/app/settings/page.tsx | NEW | Rosé |
| dashboard/src/components/Sidebar.tsx | EDIT | Rosé |
| dashboard/src/lib/api.ts | EDIT | Rosé |
| dashboard/src/types/index.ts | EDIT | Rosé |
