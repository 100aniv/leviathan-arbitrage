# TF Final — Live 전환 판정

> SF PASS 후 호출. PASS 시 Live 모드 전환.

## 수행

### 1. 최종 체크리스트
- [ ] check_all 9/9
- [ ] pytest 전체 PASS
- [ ] Shadow 1시간 무중단 + PnL > 0
- [ ] 킬스위치 동작 확인
- [ ] Telegram 알림 정상
- [ ] 대시보드 12페이지 에러 0건
- [ ] API 키 확인 (Binance/Upbit/Bithumb/Coinone)
- [ ] 포지션 한도 5만원 설정
- [ ] 일일 손실 한도 $50

### 2. Go/No-Go 판정
- 전부 PASS → **LIVE 전환**
- FAIL → Fix Loop

### 3. Live 전환 절차
```
1. EXECUTION_MODE=live (engine/.env)
2. 엔진 재시작
3. Telegram /status 명령으로 확인
4. 대시보드 모드 LIVE 표시 확인
5. 첫 거래 모니터링 (30분)
6. 안정 확인 후 방치
```

## 완료 조건
- [ ] Go/No-Go PASS
- [ ] EXECUTION_MODE=live 전환
- [ ] 첫 거래 확인
- [ ] Telegram 알림 수신 확인
