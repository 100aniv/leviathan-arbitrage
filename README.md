# LEVIATHAN — 글로벌 암호화폐 크로스 차익거래 엔진

**LEVIATHAN**은 Python 3.12 + Rust(PyO3)로 구축한 고빈도 자동거래 엔진입니다. 10개 거래소에서 실시간으로 스프레드를 감시하고 수익성 기회를 자동 실행합니다. 실 자본으로 거래하기 전에 Shadow 모드(가상 거래 + 전체 지표)에서 안정성을 검증한 후 Live 모드로 전환합니다.

## 5분 안에 시작하기

### 1. 저장소 복제
```bash
git clone https://github.com/your-org/leviathan.git
cd leviathan
```

### 2. 환경 설정
```bash
cd engine
cp .env.example .env
# .env 파일 열어서 다음 항목 채우기:
# - BINANCE_API_KEY / BINANCE_SECRET
# - OKX_API_KEY / OKX_SECRET / OKX_PASSPHRASE
# - (다른 거래소 API 키 필요 시 추가)
# - TELEGRAM_BOT_TOKEN (선택사항: 알림용)
cd ..
```

### 3. Docker 인프라 시작
```bash
# TimescaleDB + Redis 시작 (필수)
docker compose up -d timescaledb redis

# 상태 확인
docker compose ps
```

### 4. Python 환경 설정
```bash
cd engine
pip install -e .
# 또는 선택사항: pip install -e ".[legacy]" (ccxt 포함)
```

### 5. Shadow 모드 실행 (가상 거래 테스트)
```bash
cd engine
timeout 600 python -m src.main
# 10분 실행 후 자동 종료
# 결과: .omc/state/shadow-result-latest.json
```

실행 후 다음을 확인하세요:
- **Crash 여부**: 로그에 에러 없음
- **PnL > 0**: 수익성 확인
- **전략 신호**: 모든 활성 전략이 최소 1건 이상 거래

## 아키텍처 개요

### 5-Layer 스택

```
┌─────────────────────────────────────────┐
│        Dashboard (Next.js 14)           │  WebSocket + JWT 인증
│  (Overview / Strategies / Portfolio)    │
└──────────────┬──────────────────────────┘
               │
┌──────────────▼──────────────────────────┐
│    REST API + WebSocket Server (Uvicorn)   │  /api/v1/* + /ws
│           (FastAPI, JWT Auth)          │
└──────────────┬──────────────────────────┘
               │
┌──────────────▼──────────────────────────┐
│         Core Engine (Python + Rust)     │
│  • Signal Pipeline (PriceHub → Signal)  │  • Risk Guardian (Kill Switch)
│  • 7 Strategies (cross_exchange, ...)   │  • Circuit Breaker
│  • Atomic Execution (TradeRequest)      │  • Position Manager
└──────────────┬──────────────────────────┘
               │
┌──────────────▼──────────────────────────┐
│    Exchange Adapters + Data Collectors  │
│  • 10 Native WebSocket (Binance, OKX, …)   │
│  • Paper Executor (가상 거래)           │
│  • PriceHub (실시간 시세)               │
└──────────────┬──────────────────────────┘
               │
┌──────────────▼──────────────────────────┐
│   Infrastructure (Redis + TimescaleDB)  │
│  • Redis: 포지션 캐시 + Streams         │
│  • TimescaleDB: 거래 내역 + 메트릭      │
└─────────────────────────────────────────┘
```

### 실행 모드

| 모드 | 데이터 | 실행 | 용도 |
|------|--------|------|------|
| **Backtest** | 합성(GBM) | Paper | 히스토리 검증 |
| **Paper** | 실시간 WS | Paper(가상) | 파이프라인 기능 테스트 |
| **Shadow** | 실시간 WS | Paper(가상) | 수익성 + 안정성 검증 (10분~24시간) |
| **Live** | 실시간 WS | Live(실거래) | 실 자본 거래 (LiveGate 통과 후) |

## 디렉토리 구조

```
leviathan/
├── engine/                      # Python 엔진 (메인 코드)
│   ├── src/
│   │   ├── main.py             # 엔진 시작점 + 라이프사이클 관리
│   │   ├── api/                # FastAPI 서버 (REST + WS)
│   │   ├── collectors/         # 10개 거래소 WebSocket 수집기
│   │   ├── strategies/         # 7개 차익거래 전략
│   │   ├── execution/          # 주문 실행 + 자동화 프로토콜
│   │   ├── risk/               # Kill Switch, Circuit Breaker, 리스크 가디언
│   │   ├── friction/           # 수수료 모델 + 슬리피지 모델
│   │   ├── core/               # 공유 타입 + 설정
│   │   ├── infra/              # Redis, DB, Telegram 연동
│   │   ├── workflow/           # 체크포인트 + 일관성 검사 (순수 Python)
│   │   ├── ml/                 # ML 시그널 (HMM 레짐, XGBoost)
│   │   └── modes/              # Shadow 모드 메트릭
│   ├── tests/                  # 5,200+ 단위/통합 테스트
│   ├── .env.example            # 환경 변수 템플릿
│   └── pyproject.toml          # Python 의존성 + 빌드 설정
│
├── dashboard/                   # Next.js 14 모니터링 대시보드
│   ├── src/app/               # App Router 페이지
│   │   ├── (authenticated)/   # 로그인 필요 페이지
│   │   │   ├── page.tsx       # Overview (포트폴리오 + 리스크 + 이벤트)
│   │   │   ├── strategies/    # 전략별 성과
│   │   │   ├── portfolio/     # 자본 곡선 + 메트릭
│   │   │   ├── settings/      # 거래소 설정
│   │   │   └── system/        # 인프라 상태
│   │   └── login/             # 로그인 페이지
│   ├── src/components/        # 재사용 가능한 UI 컴포넌트
│   ├── src/types/             # TypeScript 타입 정의
│   └── next.config.ts         # Next.js 설정
│
├── docker/                      # Docker 빌드 파일
│   ├── engine.Dockerfile      # Python 엔진 이미지
│   ├── dashboard.Dockerfile   # Next.js 이미지
│   └── init.sql               # DB 초기화 스크립트
│
├── docker-compose.yml          # 15개 서비스 오케스트레이션
├── .env                        # Docker용 환경 변수 (루트)
├── .env.example                # 템플릿
├── SSOT.md                      # 단일 설계 문서 (상태 + 아키텍처)
└── .claude/                     # OMC 자동화 설정
    ├── CLAUDE.md              # 프로젝트 규칙
    ├── MEMORY.md              # 세션 메모리
    └── agents/                # 커스텀 에이전트
```

## 기술 스택

| 영역 | 기술 |
|------|------|
| **엔진** | Python 3.12+ (AsyncIO) + Rust (PyO3, hot-path 최적화) |
| **대시보드** | Next.js 14 (App Router) + TypeScript + Tailwind CSS |
| **API** | FastAPI + Uvicorn + PyJWT (JWT 인증) |
| **DB** | TimescaleDB 16 (PostgreSQL 기반, 시계열 최적화) |
| **캐시 / 메시징** | Redis 7 (Streams + 포지션 캐시) |
| **거래소** | 10개 네이티브 WebSocket 어댑터 (ccxt 미사용) |
| **테스트** | pytest + pytest-asyncio (5,200+ 테스트) |
| **배포** | Docker Compose (15 서비스) |
| **모니터링** | Prometheus + Grafana + Loki + Alertmanager |

## 테스트 실행

### 단위 테스트 (Docker 불필요)
```bash
cd engine
python -m pytest tests/ -x --tb=short
# -x: 첫 번째 실패 시 중단
# --tb=short: 간단한 트레이스백
```

### 통합 테스트 (Docker 필수)
```bash
# Docker 인프라 시작
docker compose up -d timescaledb redis

# 테스트 실행
cd engine
python -m pytest tests/ -m integration -x
```

### 테스트 커버리지
```bash
cd engine
python -m pytest tests/ --cov=src --cov-report=html
# htmlcov/index.html에서 결과 확인
```

## Docker 배포

### 전체 스택 시작
```bash
# 첫 실행: 이미지 빌드
docker compose up -d

# 재빌드 필요 시
docker compose up -d --build

# 상태 확인
docker compose ps
docker compose logs -f engine
```

### 개별 서비스 제어
```bash
# TimescaleDB + Redis만 시작 (로컬 개발용)
docker compose up -d timescaledb redis

# Engine 업데이트 후 재배포
docker compose build engine && docker compose up -d engine

# 대시보드 재배포
docker compose build dashboard && docker compose up -d dashboard
```

### 주요 포트

| 서비스 | 포트 | 용도 |
|--------|------|------|
| Engine API | 8000 | REST + WebSocket |
| Dashboard | 3000 | 모니터링 UI |
| Redis | 6379 | 포지션 캐시 |
| TimescaleDB | 5432 | 거래 데이터 |
| Prometheus | 9090 | 메트릭 |
| Grafana | 3001 | 대시보드 |

## 텔레그램 봇 설정

### 3개 봇 역할

1. **TradeBot** (거래 알림)
   - 환경변수: `TRADE_TELEGRAM_BOT_TOKEN`
   - 기능: 신호 발생 / 체결 알림 / Kill Switch / 포지션 관리 (20개 명령어)

2. **DevBot** (개발 제어)
   - 환경변수: `DEV_TELEGRAM_BOT_TOKEN`
   - 기능: 원격 재시작 / 전략 활성화 / Watchdog 수동 재개 (16개 명령어)

3. **InfraBot** (인프라 모니터링)
   - 환경변수: `INFRA_TELEGRAM_BOT_TOKEN`
   - 기능: CPU/메모리 / DB 상태 / Redis 상태 (7개 명령어)

### 토큰 설정
```bash
# engine/.env에 추가
TRADE_TELEGRAM_BOT_TOKEN=your_trade_bot_token
DEV_TELEGRAM_BOT_TOKEN=your_dev_bot_token
INFRA_TELEGRAM_BOT_TOKEN=your_infra_bot_token

# 토큰은 BotFather (@BotFather in Telegram)에서 발급
```

## 주요 문서

| 문서 | 내용 |
|------|------|
| [SSOT.md](SSOT.md) | 유일한 설계 문서 (프로젝트 상태, 아키텍처, 전략 상세) |
| [engine/README.md](engine/README.md) | 엔진 모듈 설명 + 전략/어댑터 추가 방법 |
| [dashboard/README.md](dashboard/README.md) | 대시보드 페이지 + API 연동 |
| [.claude/CLAUDE.md](.claude/CLAUDE.md) | 프로젝트 개발 규칙 + 팀 구조 + 워크플로우 |

## 라이선스

Proprietary — LEVIATHAN Project
