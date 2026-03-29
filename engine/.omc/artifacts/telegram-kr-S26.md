# 텔레그램 한글화 변경 기록 (S26)

날짜: 2026-03-24

## shadow.py (`engine/src/modes/shadow.py`)

모든 5곳 이미 한글화 완료 — 변경 불필요.

| 라인 | 상태 | 내용 |
|------|------|------|
| 650 | 이미 한글 | "섀도 모드 시작. 실데이터 + 페이퍼 실행 활성화." |
| 1977 | 이미 한글 | f"KRW 환율 {elapsed:.0f}초 지연 — KRW 거래소 소프트 차단됨" |
| 1996 | 이미 한글 | f"긴급: KRW 환율 {elapsed:.0f}초 지연 (10분 이상) — 킬 스위치 작동" |
| 2011 | 이미 한글 | "KRW 환율 복구 — 소프트 차단 해제" |
| 2140 | 이미 한글 | 일일 리포트 send_alert (lines 조립 자체가 한글) |

## main.py (`engine/src/main.py`)

5곳 변경, 5곳 이미 한글.

| 라인 | 상태 | 변경 전 | 변경 후 |
|------|------|---------|---------|
| 1279 | 이미 한글 | "⚠️ 포지션 불일치 {n}건: {summary}" | — |
| 1400 | **변경** | `"Position tracking persistently failing ({n}x) — risk data unreliable"` | `"⚠️ 포지션 추적 지속 실패 ({n}회) — 리스크 데이터 불신뢰"` |
| 1549 | 이미 한글 | "🚨 인벤토리 심각한 불균형 감지! 즉시 확인 필요." | — |
| 1565 | 이미 한글 | "⚠️ 인벤토리 리밸런싱 필요 ({n}건)" | — |
| 1967 | **변경** | `"Real data collection started\nExchanges: ...\nSymbols: ..."` | `"📡 실 데이터 수집 시작\n거래소: ...\n심볼: ..."` |
| 2091 | **변경** | `"LIVE Mode started\nExchanges: ...\nSymbols: ..."` | `"🚀 라이브 모드 시작\n거래소: ...\n심볼: ..."` |
| 2463 | **변경** | `"Shadow Mode active\nExchanges: ...\nSymbols: ...\nLiveGate: enabled/disabled"` | `"🌑 섀도 모드 활성화\n거래소: ...\n심볼: ...\n라이브게이트: 활성/비활성"` |
| 2729 | 이미 한글 | "⚠️ 시작 시 미정리 포지션 {n}건 발견..." | — |
| 2807 | **변경** | `msg = "Reconciliation mismatch: " + ...` (send_alert에 삽입) | `msg = "잔고 불일치: " + ...` |
