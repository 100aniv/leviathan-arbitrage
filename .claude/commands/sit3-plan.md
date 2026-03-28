# SIT-3 Plan — 다음 작업 결정

> Auto-Chaining 2/4단계. `/project:sit3-audit` 완료 후 호출. 완료 후 `/project:sit3-execute` 호출.

## 수행 (순서 엄수)

1. **PENDING/FAIL 식별** — sit3-checklist.json에서 미완 시나리오 추출
2. **우선순위 결정**:
   - CRITICAL: 전략 로직 버그, 데이터 무결성, 보안
   - HIGH: 전략 튜닝, 리스크 관리, API 기능
   - MEDIUM: 대시보드 UI, 문서, 인프라
3. **수정 계획 작성**:
   - 구체적 파일 경로 + 라인 번호
   - 수정 내용 (before → after)
   - 예상 영향 범위
4. **전략 우선**: 7개 전략 관련 FAIL이 있으면 무조건 최우선
5. **/devils-advocate** — 플랜에 대한 반론:
   - "이 수정이 다른 걸 깨뜨리지 않는가?"
   - "더 근본적인 해결책이 있지 않은가?"
   - "이 순서가 최적인가?"

## 출력 형식

```
[PLAN] Fix Loop #N
- 대상: X건 FAIL/PENDING
- 수정 1: [파일:라인] [내용]
- 수정 2: [파일:라인] [내용]
- DA 반론: [요약] → 대응: [조치]
→ 다음: /project:sit3-execute
```

6. **Notion 기록** — 플랜을 Notion 페이지에 기록 (연동 시)

## 완료 조건 (DoD)
- [ ] PENDING/FAIL 시나리오 목록 작성
- [ ] 수정 계획 (파일:라인 수준)
- [ ] /devils-advocate 반론 + 대응
- [ ] Notion에 플랜 기록 (연동 시)
- [ ] "→ 다음: /project:sit3-execute" 출력

**DoD 미충족 시 다음 단계 진행 금지.**
