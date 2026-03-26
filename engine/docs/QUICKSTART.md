# LEVIATHAN Quick Start Guide

## Prerequisites
- Docker Desktop (4+ CPU, 4GB+ RAM)
- Python 3.12+
- Node.js 18+ (dashboard)

## 1. Clone & Setup
```bash
git clone <repo-url>
cd arbitrage_OMC
cp engine/.env.example engine/.env  # API 키 설정
```

## 2. Start Infrastructure
```bash
docker compose up -d timescaledb redis
```

## 3. Start Engine (Shadow Mode)
```bash
cd engine && python -m src.main
```
Engine이 10개 거래소에 연결하고 Shadow 모드로 시뮬레이션 거래를 시작합니다.

## 4. Start Dashboard
```bash
docker compose up -d dashboard
```
http://localhost:3000 접속 → admin / (설정한 비밀번호) 로그인

## 5. Telegram Bots
- TradeBot: 거래 알림 + Kill Switch + /status
- DevBot: 원격 개발 제어 + Watchdog
- InfraBot: 인프라 모니터링

## 6. Monitoring
- Dashboard: http://localhost:3000
- Grafana: http://localhost:3001 (admin/admin → 비밀번호 변경 필수)
- Prometheus: http://localhost:9090

## 7. Key Commands
```bash
# 텔레그램
/status     # 엔진 상태
/kill       # 긴급 거래 중단
/resume     # 거래 재개

# CLI
cd engine && python -m pytest tests/ -x --tb=short  # 테스트
cd engine && python -m src.workflow.cli check_all    # 정합성 검사
```
