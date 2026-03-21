# Phase S20 PLAN — 모니터링 전면 재설계 + 3-Bot 텔레그램 분리

## 목표
기존 단일 텔레그램 봇을 3봇(인프라/거래/개발)으로 분리하고, 인라인 키보드 상호작용, 차트 시각화, 일일 리포트, 시작 체크리스트, Prometheus 계측 완성, Grafana 대시보드, CLI 도구를 구현한다.

## US 목록 (18개)

### Batch 0: 기반 (BotBase + 3봇)
| US | 제목 | 파일 | 의존성 |
|----|------|------|--------|
| US-291-a | TelegramBotBase 인라인키보드+callback+사진 | `telegram_bot_base.py` ✅ | - |
| US-291-b | InfraTelegramBot 인프라봇 | `telegram_infra_bot.py` | US-291-a |
| US-291-c | TradeTelegramBot 거래봇 | `telegram_trade_bot.py` | US-291-a |
| US-291-d | DevTelegramBot 개발봇 | `telegram_dev_bot.py` | US-291-a |

### Batch 1: 인프라 + 설정
| US | 제목 | 파일 | 의존성 |
|----|------|------|--------|
| US-291 | Prometheus 계측 완성 | `metrics.py` 수정 | - |
| US-291-j | .env 3봇 토큰 매핑 | `.env` 수정 | - |
| US-295-a | MonitorDaemon main.py 통합 | `monitor_daemon.py`, `main.py` | US-291-b |

### Batch 2: 핵심 기능
| US | 제목 | 파일 | 의존성 |
|----|------|------|--------|
| US-291-e | 시작 체크리스트 | `startup_checker.py` | US-291-b, US-295-a |
| US-295 | 일일 요약 리포트 09:00 KST | `telegram_trade_bot.py` | US-291-c |
| US-291-f | 긴급 제어 인라인 키보드 | `telegram_trade_bot.py` | US-291-c |

### Batch 3: 상호작용 + 시각화
| US | 제목 | 파일 | 의존성 |
|----|------|------|--------|
| US-291-g | 조회 메뉴 인라인 키보드 | `telegram_trade_bot.py` | US-291-f |
| US-291-h | 설정 변경 (/settings) | `telegram_trade_bot.py` | US-291-g |
| US-291-i | 차트 시각화 (/chart) | `telegram_charts.py` | US-291-c |

### Batch 4: 대시보드 + CLI
| US | 제목 | 파일 | 의존성 |
|----|------|------|--------|
| US-292 | Grafana 대시보드 4개 | `infra/grafana/dashboards/` | US-291 |
| US-293 | Alertmanager 3봇 라우팅 | `alertmanager.yml`, `alerts.yml` | US-291 |
| US-294 | 원클릭 CLI | `cli/leviathan_cli.py` | - |

### Batch 5: 통합 와이어링 + 검증
| US | 제목 | 파일 | 의존성 |
|----|------|------|--------|
| US-291-k | main.py 3봇 와이어링 | `main.py` 수정 | Batch 0~1 전체 |
| US-296 | Shadow 10min 통합 검증 | Shadow 실행 | 전체 |

## 구현 순서
```
Batch 0: US-291-a(완료) → (US-291-b, US-291-c, US-291-d 병렬)
Batch 1: (US-291, US-291-j, US-295-a 병렬)
Batch 2: (US-291-e, US-295, US-291-f 병렬)
Batch 3: (US-291-g, US-291-h, US-291-i 병렬)
Batch 4: (US-292, US-293, US-294 병렬)
Batch 5: US-291-k → US-296
```

## 핵심 설계

### 3봇 토큰 매핑
| 기존 env | 신규 env | 봇 | Fallback |
|----------|----------|-----|----------|
| TELEGRAM_BOT_TOKEN | TRADE_TELEGRAM_BOT_TOKEN | 거래봇 | 기존 값 |
| TELEGRAM_CHAT_ID | TRADE_TELEGRAM_CHAT_ID | 거래봇 | 기존 값 |
| WORKFLOW_TELEGRAM_BOT_TOKEN | DEV_TELEGRAM_BOT_TOKEN | 개발봇 | 기존 값 |
| WORKFLOW_TELEGRAM_CHAT_ID | DEV_TELEGRAM_CHAT_ID | 개발봇 | 기존 값 |
| (신규) | INFRA_TELEGRAM_BOT_TOKEN | 인프라봇 | - |
| (신규) | INFRA_TELEGRAM_CHAT_ID | 인프라봇 | - |

### 하위호환
- 기존 `TelegramAlerter`, `TelegramCommandHandler`, `SmartTelegramAlerter` 유지
- `self._telegram` → `TradeTelegramBot._alerter` 위임 (기존 코드 영향 없음)
- `get_telegram_alerter()` 팩토리 유지

### 알림 레벨
- ALL: 모든 거래 체결 포함
- IMPORTANT (기본): 중요 이벤트만
- CRITICAL_ONLY: 긴급만

### matplotlib 의존성
- optional import (try/except)
- 미설치 시 텍스트 fallback

## 리스크
1. 하위호환 깨짐 → self._telegram 참조 80+ 곳 확인 필수
2. 3봇 토큰 미설정 → graceful skip
3. Shadow 10min 충분성 → crash=0, 3봇 응답 확인

## 완료 조건
1. pytest 전체 PASS
2. Shadow 10min: crash=0
3. 3봇 모두 명령 응답 확인 (봇 토큰 설정 시)
4. 시작 체크리스트 메시지 수신 확인
5. 인라인 키보드 동작 확인
