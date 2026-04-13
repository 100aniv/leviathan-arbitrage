# Phase K PLAN REVIEW GATE — Consensus Result
> 생성: 2026-04-02 | Codex + Gemini + Qwen (독립) + Claude (4번째 관점)

## Quorum 합의 MUST FIX (2+ 모델 동의)

### MUST FIX 1: Synthetic Orderbook ±0.05% 스프레드 < 수수료 (3/3 동의)
- **근거**: 고정 ±0.05% 대칭 스프레드 = 합성 mid±0.05%. triangular 수수료 최저는 Coinone 0.02%×3=0.06%. ±0.05% 스프레드에서는 triangular 수익 기회 거의 없음 → backtest trades=0 예상
- **심각도**: HIGH~MEDIUM (Codex MEDIUM, Gemini HIGH, Qwen HIGH)
- **수정 방향**: PLAN.md에 "triangular 전략 backtest = architecture validation only (신호 발생 없을 수 있음)" 명시. 실질 검증은 Paper(실시간 WS) 단계로 이관.

### MUST FIX 2: iMessage AppleScript macOS 종속성 (3/3 동의)
- **근거**: AppleScript 기반 iMessage는 Docker/Linux 환경에서 작동 불가. Telegram DevBot에 이미 `/approve` 플로우 존재.
- **심각도**: MEDIUM~HIGH (Codex MEDIUM, Gemini HIGH, Qwen MEDIUM)
- **수정 방향**: US-364에서 AppleScript 제거. Telegram DevBot `/approve K-L` 명령을 기본 승인 채널로 확정. iMessage는 알림 전용(결과 공유).

### MUST FIX 3: Live 완료 기준 너무 약함 — 롤백/부분체결 미정의 (3/3 동의)
- **근거**: "record_execution mode='live' 1건 = PASS"는 부분체결, 네트워크 실패, 포지션 미결 상황을 커버하지 않음. 첫 Live 실거래 시 자본 손실 위험.
- **심각도**: CRITICAL~HIGH (Codex HIGH, Gemini CRITICAL, Qwen HIGH)
- **수정 방향**: US-056 AC에 "주문 ID 조회 성공", "filled_qty > 0", "잔고 반영 확인" 추가. rollback_cost 측정.

### MUST FIX 4: US-332 Sharpe≥2.0 for 24H 모호 (2/3 동의: Gemini CRITICAL, Qwen CRITICAL)
- **근거**: 24H 데이터로 Sharpe 통계적 유의성 부족. sqrt(8760) 연간화 기준인지 명시 안 됨. Sharpe 달성 불가 시 Phase K 영구 블로킹.
- **수정 방향**: US-332에 sqrt(8760) 기준 명시. 보조 기준(crash=0, API rate limit 준수)을 PRIMARY AC로, Sharpe≥2.0은 SECONDARY(달성 시 보너스)로 순위 조정.

### MUST FIX 5: Tier4 어댑터 5개 Scope Creep (2/3 동의: Gemini HIGH, Qwen HIGH)
- **근거**: Phase K에서 5개 어댑터 동시 추가 = 버그 추적 불가. API 키 없는 상태에서 WIRING AC 3번째 조건(런타임 호출 증거) 달성 불가.
- **수정 방향**: US-360 완료 기준 = "mock 단위테스트 통과 + _NATIVE_ADAPTER_MAP 등록 확인". Runtime 호출 증거 조건 제외(API 키 없으므로). WIRING AC 3: "Shadow 실행 중 adapter 로딩 로그 1건" (실제 거래 아님).

---

## 단일 모델 이슈 (MUST FIX 아님, 참고 사항)

| 이슈 | 발견 모델 | 심각도 | 조치 |
|------|---------|--------|------|
| Approval gate 위치 (Paper → Live로 이동) | Codex | CRITICAL | US-056 에서 LiveMode.start() 진입 시 Gate 확인 |
| LiveGate 6 체크 vs 계획 10 체크 | Qwen | CRITICAL | US-055 AC에서 "Preflight 10항목" → "LiveGate 6+추가 4 구현" 명시 |
| US-358 실패 경로 record_execution 누락 | Qwen | CRITICAL | US-358 AC에 "rollback 경로에서는 mode='live_failed' 기록 또는 미기록 정책 명시" 추가 |
| Shadow 24H vs Preflight 72H 충돌 | Codex | HIGH | US-332 = 24H, Preflight 72H는 별도 요건 — US-332가 72H bypass 조건 명시 필요 |
| capital USD/KRW 혼용 환율 처리 | Qwen | HIGH | US-334 AC에 "KRW 잔고 = USD 한도 계산에서 분리 또는 환율 소스 명시" 추가 |

---

## 결론
- MUST FIX 5건 → PLAN.md 수정 후 Stage B 진입
- 재검증 1회 허용
