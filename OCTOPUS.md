OMC vs Claude Octopus: LEVIATHAN 프로젝트 심층 비교 분석
Context
LEVIATHAN 프로젝트는 Phase S15~S21 회귀 단계. TF SF 9H에서 CRITICAL 6건 + 수학 오류 3건 발견으로 중단됨. 핵심 문제: "클로드가 자기 프로젝트의 맹점을 못 본다" — GPT/Gemini에게 물어보면 누락된 개선점이 나오는데 클로드 단독으로는 발견 못함.
조사 범위: GitHub, Exa.ai, Threads, Reddit, Medium, ClaudePluginHub, YouTube, Facebook 그룹 전수 조사 (2026-03-19)

1. 팩트체크: 생태계 비교 (실제 수치)
항목	OMC (oh-my-claudecode)	Octopus	CCG-workflow (제3옵션)
Stars	10,240	1,455	3,926
Forks	716	120	265
Contributors	60명	3명 (nyldn, dependabot, claude)	6명
언어	TypeScript 55%	Shell 87%	Go 54%
릴리즈	201 (v4.8.2)	174 (v9.4.2)	1 (prerelease)
생성일	2026-01-09	2026-01-15	2026-01-04
마지막 push	2026-03-18	2026-03-17	2026-03-17
중요한 발견들
1. Octopus는 사실상 1인 프로젝트 — 기여자 3명 중 nyldn만 실제 개발자 (dependabot=봇, claude=AI). 2달 만에 174 릴리즈 = 하루 2.9개. 안정성 우려.
2. Shell 87% — Octopus의 핵심 로직이 쉘 스크립트. OMC(TypeScript)나 CCG(Go) 대비 유지보수성 열세.
3. Octopus와 OMC는 동일한 인증 메커니즘 사용:
    * 둘 다 Codex CLI (ChatGPT OAuth) + Gemini CLI (Google OAuth)를 호출
    * API 키 불필요, 구독만 있으면 됨
    * 즉, OMC의 ask-codex/ask-gemini//ccg도 OAuth 기반으로 동일하게 작동
    * Octopus만 OAuth라는 건 Gemini의 오해
4. 실사용 후기가 거의 없음 — Reddit r/ClaudeOctopus, Threads, Medium 전수 조사했으나 "Octopus를 실제 프로젝트에 사용해서 맹점을 발견했다"는 구체적 사례 0건. 홍보글만 존재.
5. 적대적 리뷰 효과는 검증됨 — gaurav-yadav/adversarial-ai-review (500+ PR, false positive 30-60% → 7%)가 증명. 하지만 이건 Octopus 고유가 아니라 멀티모델 교차검증의 일반적 효과.

2. 사용자 지적 분석: "CCG도 결국 클로드에서 수행하니 관점이 비슷하지 않나?"
정확한 지적이지만, 절반만 맞음:
클로드가 실행하는 부분 vs 외부 모델이 실행하는 부분
[OMC CCG 실행 흐름]
사용자 → Claude Code (오케스트레이터)
         ├→ omc ask codex "질문" → Codex CLI → GPT-5.3 모델이 독립 실행 → 결과 반환
         ├→ omc ask gemini "질문" → Gemini CLI → Gemini 2.5 Pro 모델이 독립 실행 → 결과 반환
         └→ Claude가 세 결과를 종합 판단

[Octopus 실행 흐름]
사용자 → Claude Code (오케스트레이터)
         ├→ Codex CLI → GPT-5.3 모델이 독립 실행 → 결과 반환
         ├→ Gemini CLI → Gemini 2.5 Pro 모델이 독립 실행 → 결과 반환
         └→ Claude가 75% 합의 판정
결론: 내부 메커니즘이 동일하다.
Codex와 Gemini는 둘 다 독립된 외부 모델이 분석한다. 클로드는 "최종 종합"만 담당. 따라서 CCG든 Octopus든 "다른 모델의 눈"은 동일하게 확보된다.
진짜 차이점: 구조적 강제 vs 선택적 사용
	OMC CCG	Octopus
합의 게이트	없음 (Claude가 자유 판단)	75% 강제
적대적 모드	없음 (각 모델 독립 응답)	모델끼리 교차 비판
워크플로우 통합	수동 (/ccg 한번 호출)	자동 (모든 단계에 내장)
결과 포맷	.omc/artifacts/ask/에 텍스트	구조화된 합의/비합의 리포트
핵심: Octopus의 진짜 가치는 "다른 모델을 쓴다"가 아니라 "합의 없으면 진행 불가"라는 구조적 강제에 있다.

3. Gemini 응답의 팩트체크
Gemini 주장	실제	판정
"OMC는 실행 가속 전용"	OMC에 7단계 품질 게이트 있음	과장
"인프라 95%, 전략 로직 60-70%, 검증 40%"	4,843 테스트, 86% 커버리지, TF 4-Round 통과	과소평가
"Octopus가 OAuth, OMC는 API키"	둘 다 동일한 CLI OAuth 사용	오류
"cex_dex.py 왜 작동 안하는지 Octopus가 찾을 것"	DEX_RPC_URL 미설정이 원인, 이미 문서화됨	불필요
"적대적 리뷰가 맹점 제거"	맞음 (adversarial review 7% FP 검증)	정확
"75% 합의 게이트가 날림 작업 차단"	개념적으로 맞지만 실사용 검증 사례 없음	미검증
4. 최종 추천: A/B 비교 후 결정
사용자의 직관이 맞습니다. 둘 다 시도해보고 비교하는 것이 가장 합리적.
추천 방안: 2-Track A/B 테스트
Track A: OMC CCG 구조적 통합 (비용 0, 즉시 가능)
변경: leviathan.md Stage C에 CCG 멀티모델 리뷰 단계 추가
Stage C-Step 2 (NEW): 멀티모델 독립 감사 (CCG)
1. /ccg "다음 변경사항의 로직 오류, 누락된 엣지케이스, 수학적 정확성을 검증하라: [변경 파일 목록]"
   - Codex: 구현 깊이 (패턴 분석, 아키텍처)
   - Gemini: 생태계 넓이 (보안, 대안, 베스트 프랙티스)
   - Claude: 종합 판단
2. 3모델 중 2개 이상이 이슈 지적 → MUST FIX (수동 75% 게이트)
3. 결과를 .omc/artifacts/ccg-review-{phase}.md에 저장
장점: 기존 투자 100% 보존, 추가 설치 없음, 즉시 적용 단점: 합의 게이트가 자동이 아닌 수동 판단, 적대적 모드 없음
Track B: Octopus 설치 + 감사 전용 (병행)
# 터미널에서 (Claude Code 밖)
claude plugin marketplace add https://github.com/nyldn/claude-octopus.git
claude plugin install octo@nyldn-plugins
# Claude Code 안에서
/octo:setup
용도 제한 (실행은 LEVIATHAN, 감사만 Octopus):
* /octo:debate "engine/src/strategies/ 7개 전략 로직 전수 감사"
* /octo:security "engine/src/"
* /octo:research "crypto arbitrage slippage model best practices 2026"
장점: 75% 합의 자동 강제, 적대적 리뷰, Double Diamond 구조 단점: 1인 개발 프로젝트(안정성?), Shell 기반, 도메인 특화 없음
A/B 비교 실행 계획
대상: Phase S15 (CRITICAL 버그 + ML 연결, ~17 US)
단계	Track A (CCG)	Track B (Octopus)
설정	leviathan.md에 Stage C-Step 2 추가	Octopus 설치 + /octo:setup
감사 대상	engine/src/strategies/ 전체	동일
실행 방법	/ccg "전략 감사"	/octo:debate "전략 감사"
측정 지표	발견 이슈 수, 심각도, 실제 유효율	동일
소요 시간	5분 (이미 설치됨)	30분 (설치+설정)
비교 후 결정 기준:
* Octopus가 CCG 대비 유의미하게 더 많은/중요한 이슈 발견 → Octopus 감사 모드 유지
* 차이 미미 → CCG만 사용 (복잡도 감소)
* 둘 다 기존 대비 큰 개선 없음 → 멀티모델 리뷰 자체가 이 프로젝트에 과적합

5. 변경 대상 파일
즉시 적용 (Track A):
* .claude/commands/leviathan.md — Stage C-Step 5 CCG 멀티모델 검증 추가
* .claude/CLAUDE.md — "Stage C에서 CCG 필수" 규칙 추가
선택 적용 (Track B):
* Octopus 플러그인 설치 (외부 — 레포 변경 없음)
* /octo:setup 실행

6. 검증 방법
1. Track A 즉시 테스트:
    * /ccg "engine/src/strategies/cross_exchange.py의 profit_factor 계산, LiveGate 차단 로직, ML 연결 상태를 검증하라"
    * Codex+Gemini가 기존에 못 찾던 이슈를 발견하는지 확인
2. Track B 설치 후 테스트:
    * /octo:debate "engine/src/strategies/ 7개 전략 로직 전수 감사"
    * 75% 합의 게이트가 실제로 작동하는지, 발견 이슈가 Track A 대비 차이 있는지
3. 비교 판정:
    * 발견 이슈 목록을 나란히 놓고 실제 코드에서 유효한 이슈인지 수동 확인
    * 유효 이슈 수 / 총 이슈 수 = 정밀도(precision) 비교

7. 내 솔직한 의견
Octopus 도입 자체는 찬성이지만, 과대 기대는 경계해야 합니다.
찬성 이유:
* /octo:* 네임스페이스 분리로 기존 OMC와 충돌 없음
* 설치해두고 감사용으로만 쓰면 리스크 제로
* 75% 합의 강제가 수동 CCG보다 더 엄격한 건 사실
경계할 점:
* 1인 개발 + Shell 87% + 2달 174릴리즈 = 아직 불안정할 수 있음
* 실사용 성공 사례가 검색되지 않음 (홍보글만 존재)
* Gemini의 분석이 Octopus를 과대평가 (인프라 95%/전략 60-70%/검증 40%는 근거 없는 숫자)
* 우리 프로젝트의 진짜 맹점(profit_factor 버그, LiveGate 미동작 등)은 멀티모델이 아니라 통합 테스트 부재가 원인
결론: Track A(CCG 통합)를 먼저 하고, Track B(Octopus)는 병행 설치해서 A/B 비교. 데이터로 판단.
