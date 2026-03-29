# LEVIATHAN Stage B — 구현 + 검증 (Auto-Chain 2/3)

> Stage A 완료 후 호출. 완료 후 `/project:leviathan-stage-c` 호출.

## B-Step 1: 개발 (TeamCreate)

### 1. TeamCreate("leviathan-phase-X")
### 2. Teammate 스폰
- Yujin(executor): 백엔드 `engine/src/`
- Wonyoung(test-engineer): 테스트 `tests/`
- Rei(designer): 대시보드 (해당 시)
- Gaeul/Leeseo/Liz: 병렬 백엔드 (필요 시)

### 3. pytest PASS 확인
### 4. TeamDelete + 좀비 정리

## B-Step 2: Shadow 테스트 (Sub-agents)

### 1. Docker 확인 (timescaledb + redis)
### 2. Shadow 10min+ 무중단
- Minji(shadow-tester): 실행 + 13항목 복합지표
### 3. QA (병렬)
- Danielle(scientist): PnL/WR/DD 분석
- Hanni(qa-tester): CLI/API 검증
- Haerin(browser-verifier): 대시보드 (해당 시)

### Shadow 13항목 필수 PASS
1. crash=0, 2. >=10min, 3. PnL>0, 4. MDD<5%
5. PF>1.0, 6. 신호>=100/day, 7. KillSwitch OFF
8. CB CLOSED, 9. Exchange>=95%, 10. loss_capped=0
11. 활성전략 trade>=1, 12. 방어레이어 활성, 13. 결과파일

## B-Step 3: 실패 시
- Type W(Wiring) → L2 Stage A
- Type P(Parameter) → fix 3회
- Type B(Bug) → fix 3회

## 완료 조건
- [ ] pytest 0 failures
- [ ] Shadow 13항목 전부 PASS
- [ ] TeamDelete 완료, 좀비 0건
- [ ] checkpoint 저장
- [ ] "→ 다음: /project:leviathan-stage-c" 출력
