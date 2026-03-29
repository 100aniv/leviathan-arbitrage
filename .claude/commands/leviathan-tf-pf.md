# TF Pre-Final (PF) — Auto-Chain Wrapper

> SF PASS 후 호출. 완료 후 반드시 `/project:leviathan-tf-final` 호출.

## 실행

**반드시 `leviathan-tf.md`의 "TF Pre-Final (PF)" 섹션 전체를 Read한 후 수행.**

```
Read(".claude/commands/leviathan-tf.md") → PF 섹션 (PF-1~7 + 회귀) 정독 → 순서대로 수행
```

### 요약 (상세는 leviathan-tf.md 참조)
1. [PF-1] Baseline 확보 — git tag, 전체 테스트
2. [PF-2] Settings 통합 — 설정 산재 정리
3. [PF-3] Init Chain 모듈화 — main.py 분리
4. [PF-4] Loop Manager 추출 — 이벤트 루프 정리
5. [PF-5] 타입 강화 — strict typing
6. [PF-6] 멀티모델 리팩토링 감사 — codex/gemini/qwen
7. [PF-7] 재검증 — Shadow 1H + 13항목

### TeamCreate 필수
- `TeamCreate("tf-pf")` — PF 전용 팀 (leviathan-tf.md PF 팀 구성 참조)

### 완료 조건
- [ ] PF-1~7 전부 완료
- [ ] 멀티모델 리팩토링 MUST FIX 0건
- [ ] Shadow 1H 13항목 PASS
- [ ] 산출물: `docs/checklists/tf-pre-final_YYYYMMDD.md`
- [ ] TeamDelete + 좀비 0건
- [ ] "→ 다음: /project:leviathan-tf-final" 출력

**FAIL → git rollback → PF 재시도 (최대 2회) → 2회 실패 시 Final 직행**
