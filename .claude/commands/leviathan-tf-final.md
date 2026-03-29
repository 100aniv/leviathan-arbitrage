# TF Final (F) — Auto-Chain Wrapper

> PF PASS 후 호출. PASS 시 **LIVE 전환**.

## 실행

**반드시 `leviathan-tf.md`의 "TF Final (F)" 섹션 전체를 Read한 후 수행.**

```
Read(".claude/commands/leviathan-tf.md") → Final 섹션 정독 → 순서대로 수행
```

### 요약 (상세는 leviathan-tf.md 참조)
1. Operations Readiness 검증
2. DR(Disaster Recovery) 훈련
3. Canary 7일 (또는 압축 일정 시 1시간)
4. 운영 매뉴얼 작성
5. 최종 Go/No-Go — Nayeon 판정

### TeamCreate 필수
- `TeamCreate("tf-final")` — TWICE 전원

### 추가 기준 (SF 기준 + Final 추가)
- Sharpe >= 2.5
- Profit Factor > 1.2
- 리콘실리에이션 오차 < 1%

### LIVE 전환 절차 (PASS 시)
1. `EXECUTION_MODE=live` (engine/.env)
2. 포지션 한도 확인 (거래소당 5만원)
3. 일일 손실 한도 $50
4. 엔진 재시작
5. Telegram /status 확인
6. 대시보드 LIVE 모드 확인
7. 첫 거래 모니터링 (30분)

### 완료 조건
- [ ] Final 추가 기준 전부 PASS
- [ ] DR 훈련 완료
- [ ] 운영 매뉴얼 존재
- [ ] Nayeon Go/No-Go = PASS
- [ ] EXECUTION_MODE=live 전환 완료
- [ ] 첫 거래 확인

**FAIL → 항목별 수정 → 코드 변경 시 SF부터, 구조 변경 시 PF부터**
