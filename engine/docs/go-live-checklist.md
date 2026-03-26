# Shadow → Live 전환 체크리스트

> SIT-3 72H PASS 후, TF Final 통과 후 Live 전환 시 사용

## Pre-Live 필수 확인

### 1. SIT-3 완료 확인
- [ ] 411개 시나리오 전부 GREEN
- [ ] 72H Shadow 연속 무중단
- [ ] AI CLI 최종 검증 0 MUST FIX
- [ ] check_all 9/9 OK

### 2. TF Final 완료 확인
- [ ] TF QF 12차 PASS
- [ ] TF SF PASS (Sharpe >= 2.5, MDD < 5%)
- [ ] TF PF PASS
- [ ] TF Final PASS (Canary 7일)

### 3. 거래소 API 키 확인
- [ ] Binance: 실제 API 키 설정 + IP 화이트리스트
- [ ] Upbit: API 키 + 출금 제한
- [ ] Bithumb: API 키 + 보안 설정
- [ ] 기타 사용 거래소 전부

### 4. 자본 설정
- [ ] capital_per_exchange_usd 운영 금액 설정
- [ ] max_position_usd 최대 포지션 제한
- [ ] max_daily_loss_usd 일일 최대 손실 제한

### 5. 보안 최종 확인
- [ ] JWT_SECRET 64바이트 랜덤값 (개발용 아님)
- [ ] ALLOWED_IPS 운영 서버 IP만
- [ ] Docker 포트 127.0.0.1 바인딩 또는 방화벽
- [ ] 텔레그램 chat_id 설정 확인

### 6. 모니터링 확인
- [ ] Grafana 대시보드 로드
- [ ] Prometheus 메트릭 수집
- [ ] 텔레그램 3봇 응답

### 7. Live 전환
```bash
# 1. Settings에서 모드 변경
PATCH /api/v1/settings/mode {"mode": "live"}

# 2. LiveGate 자동 확인 (6-check 통과 시에만 전환)
# Sharpe >= 2.5, MDD < 5%, Signals/day >= 100
# KillSwitch clear, CB CLOSED, Exchange Health >= 95%

# 3. 모니터링 시작
# 텔레그램 /status 확인
# Grafana 실시간 PnL 확인
```

### 8. 롤백 절차
```bash
# 문제 발생 시 즉시
텔레그램: /kill       # 또는
API: PATCH /api/v1/settings/mode {"mode": "shadow"}
```
