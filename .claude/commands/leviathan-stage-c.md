# LEVIATHAN Stage C — 리뷰 + 릴리스 (Auto-Chain 3/3)

> Stage B 완료 후 호출. 완료 후 다음 Phase `/project:leviathan-stage-a` 자동 호출.

## C-Step 1: Assembly Gate (코드리뷰 전 필수)
- Assembly Verifier(verifier/sonnet): init chain + signal flow + dead wiring + config audit
- FAIL → Stage B-Step 1 복귀

## C-Step 2: 멀티모델 독립 감사 (AI CLI 필수)
- codex + gemini + qwen 병렬 코드 리뷰
- quorum 2+ 지적 = MUST FIX → Stage B 복귀
- MUST FIX 0건 → C-Step 3

## C-Step 3: 코드리뷰 + 보안리뷰 (병렬)
- Jennie(code-reviewer/opus): 종합 리뷰 + Shadow 교차평가
- Jisoo(security-reviewer): JWT/API키/OWASP
- CRITICAL/HIGH → Stage B 복귀

## C-Step 4: 멀티모델 Go/No-Go 토론
- codex + gemini + qwen: "상용급인가?" 평가
- 과반수 Go → C-Step 5

## C-Step 5: Karina Phase 완료 리뷰
- 7항목 Go/No-Go (계획/리뷰/런타임/조립/AC/정합성/최종)
- PASS → C-Step 6
- FAIL → 유형별 복귀 (W→A, P→B-2, Bug→B-1)

## C-Step 6: SSOT + Git — Sakura
- prd.json passes:true (런타임 증거 확인)
- SSOT.md + CLAUDE.md 동기화
- git commit + push
- check_all + checkpoint

## 완료 조건
- [ ] Assembly Gate PASS
- [ ] 멀티모델 MUST FIX 0건
- [ ] 코드리뷰 CRITICAL/HIGH 0건
- [ ] Karina Go
- [ ] SSOT + git push 완료
- [ ] "→ 다음: /project:leviathan-stage-a (Phase Y)" 출력
