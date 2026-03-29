# Phase F: Live 전환 준비 — 실행 계획

> **결정 드라이버**: 소액($34) 테스트, Binance 전용, 운영자 1인(사장님), 24시간 감시 불가
> **Winter 비판 반영**: 옵션 D(하이브리드) 채택, 포지션 방치 위험 대응, 통계적 유의성 확보
> **AI CLI 리서치 반영**: Gemini "재시작이 가장 안전", Codex SEC/MiFID 규제 기준

---

## US-F01: LiveGate 재설계

**결정: 옵션 D (하이브리드)** — Winter 제안 채택

```
최초 전환: Shadow 1시간 PASS → LiveGate PASS 상태 DB 저장
재시작 시: DB에서 PASS 상태 복원 → 즉시 Live (재평가 불필요)
런타임: 경량 모니터 (MDD > 10% 또는 일일 손실 > $15 → 자동 Shadow 복귀)
```

**수정 파일:**
- `engine/src/modes/live_gate.py:387-409`: enforce_or_fallback()에 DB PASS 상태 확인 추가
- `engine/src/modes/live_gate.py:61-110`: LiveGate에 pass_state_key 저장 로직
- `engine/.env`: LIVE_GATE_EVALUATION_DAYS=1 (소액 테스트용)

**AC:**
1. LiveGate PASS 후 DB에 `live_gate_passed=true` 저장
2. 엔진 재시작 시 DB에서 상태 복원 → 즉시 Live 가동
3. 런타임 MDD > 10% → 자동 Shadow 복귀 + 텔레그램 알림
4. 대시보드에서 LiveGate 상태 표시

---

## US-F02: 모드 전환

**결정: 옵션 A (재시작) + 포지션 안전 처리** — Gemini 권장 + Winter 위험 대응

```
대시보드 모드 전환 클릭 →
  1. 오픈 포지션 확인 → 있으면 "포지션 청산 후 전환" 경고
  2. 포지션 없음 확인 → API로 .env EXECUTION_MODE 변경
  3. 엔진 graceful restart (SIGTERM → cleanup → 재시작)
  4. 새 모드로 가동 확인 → 대시보드 반영
```

**수정 파일:**
- `engine/src/api/routes/settings.py:74-117`: 모드 전환 시 포지션 체크 + .env 변경 + 재시작 트리거
- `dashboard/src/app/settings/page.tsx`: 전환 UI + 확인 다이얼로그 + 진행 표시
- `engine/src/main.py`: graceful restart 시그널 핸들링

**AC:**
1. 대시보드 Settings에서 모드 버튼 클릭 → 실제 모드 전환
2. 포지션 보유 중 전환 시도 → 경고 다이얼로그
3. 전환 후 대시보드에 새 모드 표시
4. 텔레그램 모드 전환 알림

---

## US-F03: PnL 세션 관리

**결정: 세션별 분리 + 일일 리셋 + 모드별 구분**

```
PnL 저장 구조:
  shadow_session_pnl: 현재 Shadow 세션 PnL (재시작 시 0)
  shadow_cumulative_pnl: Shadow 누적 PnL (DB)
  live_session_pnl: 현재 Live 세션 PnL
  live_cumulative_pnl: Live 누적 PnL (DB)
  daily_pnl: 일일 PnL (00:00 UTC 리셋)
```

**수정 파일:**
- `engine/src/modes/shadow.py:338-359`: ShadowStats에 session_id + daily_pnl
- `engine/src/api/routes/trading.py:41-74`: PnL API에 session/cumulative/daily 구분
- `dashboard/src/components/PnLChart.tsx`: 세션/누적/일일 토글

**AC:**
1. 대시보드에서 "오늘 PnL" / "세션 PnL" / "누적 PnL" 선택 가능
2. 엔진 재시작 시 세션 PnL은 0, 누적은 DB에서 복원
3. 매일 00:00 UTC 일일 PnL 자동 리셋

---

## US-F04: 전략-거래소 매핑

**결정: trading.json에 매핑 + Settings UI 자동 연동**

```json
"strategy_exchange_requirements": {
  "cross_exchange": {"min_exchanges": 2, "types": ["spot"]},
  "spot_futures": {"required": ["*_futures"], "same_exchange": true},
  "futures_futures": {"min_exchanges": 2, "types": ["futures"]},
  "triangular": {"min_exchanges": 1, "same_exchange": true},
  "funding_rate": {"required": ["*_futures"]},
  "statistical_arb": {"min_exchanges": 1},
  "cex_dex": {"required": ["dex"]}
}
```

**수정 파일:**
- `engine/config/trading.json`: strategy_exchange_requirements 필드
- `engine/src/strategies/manager.py`: 활성화 시 거래소 호환성 체크
- `dashboard/src/app/settings/page.tsx`: 전략 카드에 필요 거래소 표시 + 비활성 사유

**AC:**
1. 거래소 선택 변경 → 불가능한 전략 자동 비활성
2. 비활성 전략에 사유 표시 ("Binance Futures 필요")
3. 전략 카드에 한글 역할 설명

---

## US-F05: 대시보드 UX 종합

**수정 포인트:**
1. 오버뷰: 최근 5건 거래 피드 + 시장 상태(BTC 가격)
2. 전략 카드: 한글 설명 ("거래소 간 가격 차이를 이용한 차익거래")
3. 모드 뱃지: SHADOW(보라)/PAPER(노랑)/LIVE(빨강) 명확 구분
4. Shadow vs Live 데이터 시각적 구분 (Shadow = 점선, Live = 실선)

**수정 파일:**
- `dashboard/src/app/page.tsx`: 최근 거래 피드 컴포넌트
- `dashboard/src/components/StrategyPanel.tsx`: 한글 설명 추가
- `dashboard/src/components/MissionControlStrip.tsx`: 모드 색상 강화

---

## 수행 순서

```
Stage B-Step 1 (TeamCreate):
  Yujin: US-F01 LiveGate (engine/)
  Gaeul: US-F02 모드 전환 (engine/)
  Rei: US-F03~F05 대시보드 (dashboard/)
  Wonyoung: 테스트 작성

Stage B-Step 2 (Shadow 10min):
  모든 수정 반영 후 Shadow 검증

Stage C:
  Assembly + AI CLI + 코드리뷰 + Go/No-Go
```
