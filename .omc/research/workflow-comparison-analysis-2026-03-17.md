# AI Agent 워크플로우 비교 분석 (2026-03-17)

**목적**: LEVIATHAN 프로젝트의 현재 워크플로우(leviathan.md + SSOT.md)를 산업 표준(LangGraph, OMC, Production AI Agent)과 객관적으로 비교

**리서치 소스**: Exa.ai web search (2024-2026년 문헌 24건)

---

## 1. 연구 방법론

### 리서치 범위
- **LangGraph state management**: 8개 문헌 (2024-2026)
- **OMC (oh-my-claudecode) best practices**: 8개 문헌 (2026)
- **Production AI Agent orchestration**: 8개 문헌 (2025-2026)

### 비교 기준
1. **State Management**: 상태 관리 방식 (TypedDict, checkpointing, SSOT)
2. **Agent Coordination**: 에이전트 조율 패턴 (직렬/병렬, 팀 구성)
3. **Error Handling**: 오류 처리 및 복구 메커니즘
4. **Production Readiness**: 상용화 준비 (durability, scalability, auditability)
5. **Documentation Pattern**: 문서화 방식 (단일 문서 vs 분산 문서)

---

## 2. LangGraph State Management 패턴

### 핵심 원칙 (출처: Bharatsinh Raj, Vishal lad, Sparkco AI 2025-2026)

#### 2.1 State Schema
```python
from typing import TypedDict, Annotated
from langgraph.graph.message import add_messages

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    context: dict
    retrieved_docs: list  # reducer: append (not overwrite)
```

**Best Practices**:
- ✅ **Explicit TypedDict**: 모든 state 필드를 명시적으로 정의
- ✅ **Reducer Functions**: `add_messages`, `append`, `merge` 등으로 업데이트 방식 명시
- ✅ **Single Shared Memory**: 모든 노드가 하나의 state 객체에서 읽기/쓰기
- ⚠️ **Anti-pattern**: 노드 간 state 덮어쓰기 (Friday corruption incident - Vishal lad 2026)

#### 2.2 Checkpointing
```python
from langgraph.checkpoint.sqlite import SqliteSaver

memory = SqliteSaver.from_conn_string(":memory:")
app = workflow.compile(checkpointer=memory)
```

**Best Practices** (Sparkco AI 2025):
- ✅ **Thread-based persistence**: `thread_id`로 세션별 state 저장
- ✅ **Resumable workflows**: 실패 시 마지막 checkpoint부터 재개
- ✅ **Audit trail**: 모든 state 변경 이력 추적 가능
- ⚠️ **Production**: SQLite → PostgreSQL/DynamoDB 전환 필수

#### 2.3 문서화 패턴
- ❌ **SSOT 없음**: state schema가 코드에만 존재
- ❌ **분산 정의**: 각 노드의 state 변경 규칙이 코드 전역에 분산
- ✅ **Type safety**: TypedDict로 컴파일 타임 검증

**평가**: LangGraph는 **코드 중심** 접근. 문서화는 개발자 책임.

---

## 3. OMC (oh-my-claudecode) Orchestration 패턴

### 핵심 원칙 (출처: Yeachan-Heo/oh-my-claudecode, rhcwlq89, Heeki Park 2026)

#### 3.1 Agent Teams
```bash
# TeamCreate — 명시적 팀 생성/삭제
TeamCreate(team_name="leviathan-phase-x")
Agent(subagent_type="executor", team_name="leviathan-phase-x", name="yujin")
SendMessage(team_name="leviathan-phase-x", recipient="yujin", message="...")
TeamDelete(team_name="leviathan-phase-x")
```

**Best Practices** (OMC 공식 문서):
- ✅ **Explicit lifecycle**: TeamCreate → work → TeamDelete (세션 격리)
- ✅ **Parallel execution**: 독립 태스크는 병렬 Agent 스폰 (+81% 속도, Google DeepMind/MIT 2025)
- ✅ **Model tiering**: haiku(탐색) → sonnet(구현) → opus(설계) 자동 라우팅
- ✅ **Background execution**: `run_in_background: true` (단, 버그 주의 - leviathan.md 경고)

#### 3.2 State Management
```json
// .omc/state/mode-state.json
{
  "active": true,
  "iteration": 3,
  "phase": "B",
  "current_task": "US-010"
}
```

**Best Practices** (OMC state MCP):
- ✅ **File-based state**: `.omc/state/*.json` (Git 외부, runtime only)
- ✅ **Mode-specific**: ralph, ultrawork, team 모드별 분리
- ✅ **Session-scoped**: `sessions/{sessionId}/` 격리
- ⚠️ **No checkpointing**: LangGraph 같은 자동 복구 없음 (수동 저장 필요)

#### 3.3 문서화 패턴
```markdown
# leviathan.md (실행 명령)
- Stage A → B → C 워크플로우 정의
- 각 Stage별 Agent 스폰 규칙
- 체크포인트 저장 지점
```

- ✅ **Workflow as Code**: leviathan.md가 실행 가능한 명령 스크립트
- ✅ **Orchestrator prompt**: Entry Gate → 기획 → 구현 → 검증 시퀀스 명시
- ⚠️ **SSOT 분리**: 설계 문서(SSOT.md)와 실행 문서(leviathan.md) 이원화

**평가**: OMC는 **팀 조율** 중심. 문서화는 실행 스크립트 형태.

---

## 4. Production AI Agent 패턴

### 핵심 원칙 (출처: Mikhail Rogov, 47billion, Hendricks 2026)

#### 4.1 Orchestrator-Executor 분리 (Rogov 2026)
```
Rule: The orchestrator must never execute.
- ✅ Decomposes tasks
- ✅ Delegates to specialists
- ✅ Validates results
- ✅ Escalates failures
- ❌ NEVER writes code/runs tests/modifies files
```

**Rationale**: 40+ production workflows, 3 months, 6 agents (Rogov 2026)
- Orchestrator가 실행하면 **context pollution** 발생
- Specialist agents는 도메인 최적화된 system prompt 사용
- 실패 시 orchestrator는 **state corruption 없이** 재라우팅 가능

#### 4.2 Single Source of Truth (CrewAI/Typedef 2025)
```
Problem: Multi-agent systems fail when agents maintain separate data stores.
Solution: Persistent, queryable catalog as SSOT for entire team.
```

**Best Practices** (Typedef 2025):
- ✅ **Catalog system**: 모든 agent가 하나의 데이터 레이어에서 읽기/쓰기
- ✅ **Synchronized data**: Task 중복 방지, context 파편화 방지
- ✅ **Shared state reasoning**: 팀 전체가 동일한 진실에서 추론

#### 4.3 문서화 패턴
```
Enterprise AI Agents (47billion 2026):
- ❌ "Every conference talk in 2025 had the same pitch"
- ✅ "The demos were impressive. The reality was not."
- 🔑 "Production-grade = governance + auditability + drift detection"
```

**Requirements**:
- ✅ **Governance**: 누가 무엇을 언제 실행했는지 추적
- ✅ **Auditability**: 모든 결정의 근거 기록
- ✅ **Drift detection**: State 불일치 조기 감지

**평가**: Production 시스템은 **감사 가능성** + **거버넌스** 중심.

---

## 5. LEVIATHAN 현재 워크플로우 분석

### 5.1 State Management

**현재 방식**:
```markdown
# SSOT.md (Single Source of Truth)
- §2 현재 상태: Phase, Tests, PRD 카운트
- §4 Math Models: 슬리피지/마찰력/수익성 공식
- §7 남은 작업: Phase 단위 US 목록

# .omc/state/*.json (Runtime State)
- leviathan-active-phase.json: Phase S13, 216 US, 209 pass
- leviathan-tf-status.json: QF 6차, SF 2차 FAIL
- team-roster.json: 7팀 상태
```

**LangGraph 대비**:
- ✅ **SSOT 존재**: SSOT.md가 명시적 진실의 소스 (LangGraph에 없음)
- ✅ **Human-readable**: Markdown 문서 (TypedDict는 코드 내부)
- ⚠️ **No automatic checkpointing**: State JSON 수동 저장 (LangGraph는 자동)
- ⚠️ **No reducer functions**: State 업데이트 규칙이 명시적이지 않음

**OMC 대비**:
- ✅ **SSOT 추가**: OMC는 `.omc/state/`만, LEVIATHAN은 SSOT.md + State JSON 병행
- ✅ **Phase 단위**: OMC보다 세밀한 Phase/Stage 구분
- ✅ **TF verification**: QF/SF/Final 검증 이력 구조화 (OMC 표준 이상)

**Production AI 대비**:
- ✅ **Catalog 역할**: SSOT.md가 Typedef catalog 역할 (모든 agent가 참조)
- ✅ **Audit trail**: TF 검증 이력, git commit 이력 (거버넌스 충족)
- ⚠️ **Drift detection**: Entry Gate (Karina)가 수동 검증 (자동화 여지)

---

### 5.2 Agent Coordination

**현재 방식**:
```markdown
# leviathan.md (Orchestrator)
Stage A: Entry Gate (Karina) → 기획 (NingNing+Winter+Giselle 병렬)
Stage B: TeamCreate → IVE 6명 병렬 → pytest → TeamDelete
Stage C: Shadow (Minji) → 퀀트 (Wonyoung) → 리뷰 (Jennie+Jisoo) → SSOT (Haerin)
```

**LangGraph 대비**:
- ✅ **Explicit workflow**: leviathan.md가 graph 정의 역할 (LangGraph는 코드)
- ✅ **Parallel execution**: Stage A 병렬 (Google research +81%)
- ⚠️ **No automatic routing**: 조건부 라우팅이 명시적 if/else (LangGraph는 동적)

**OMC 대비**:
- ✅ **OMC 표준 준수**: TeamCreate/Agent/TeamDelete 정확히 사용
- ✅ **Model tiering**: opus(Karina/Winter), sonnet(IVE), haiku(QA) 적절 배치
- ✅ **Foreground execution**: `run_in_background` 버그 회피 (OMC 이슈 #709)

**Production AI 대비**:
- ✅ **Orchestrator-Executor 분리**: Main Claude는 orchestrate만, IVE 팀이 execute (Rogov 원칙)
- ✅ **Specialist agents**: 7팀 각 도메인 전문화 (AESPA 기획, IVE 개발, BLACKPINK 리뷰...)
- ✅ **Zero execution by orchestrator**: Main Claude는 코드 작성 안함 (IVE에 위임)

---

### 5.3 Documentation Pattern

**현재 방식**:
```
SSOT.md (872→586줄)
  - §1 프로젝트 개요
  - §2 현재 상태 (→ State JSON 포인터)
  - §4 Math Models (수식)
  - §7 남은 작업 (Phase S13)

SSOT_COMPLETE.md (557줄)
  - Phase S1~S12 완료 이력
  - Shadow GAP 해결 이력
  - TF QF/SF 이력

leviathan.md (실행 명령)
  - Stage A/B/C 워크플로우
  - Entry Gate 체크리스트
  - Agent 스폰 규칙
```

**LangGraph 대비**:
- ✅ **Documentation-first**: SSOT.md → 코드 (LangGraph는 코드 → docs)
- ✅ **Human governance**: 사장님 승인 워크플로우 (LangGraph 없음)
- ⚠️ **No type safety**: Markdown이므로 컴파일 타임 검증 불가

**OMC 대비**:
- ✅ **SSOT 추가 계층**: OMC는 leviathan.md만, LEVIATHAN은 SSOT.md 추가
- ✅ **Archiving**: SSOT_COMPLETE.md로 히스토리 분리 (OMC에 없음)
- ✅ **Verification history**: TF QF/SF 이력 구조화 (OMC 표준 이상)

**Production AI 대비**:
- ✅ **Auditability**: git commit + TF 체크리스트 + SSOT 업데이트 (완전한 audit trail)
- ✅ **Governance**: Entry Gate (정합성 검사) + Go/No-Go (Phase 완료 승인)
- ⚠️ **Manual sync**: SSOT ↔ prd.json ↔ State JSON 동기화 수동 (자동화 여지)

---

## 6. 객관적 비교표

| 항목 | LangGraph | OMC | Production AI | LEVIATHAN | 평가 |
|------|-----------|-----|---------------|-----------|------|
| **State Schema** | TypedDict (코드) | JSON (파일) | Catalog (DB) | **SSOT.md + State JSON** | ✅ 하이브리드 |
| **Checkpointing** | 자동 (SQLite/Postgres) | 없음 (수동) | 있음 (Catalog) | **수동 (State JSON)** | ⚠️ 자동화 여지 |
| **SSOT** | ❌ 없음 | ⚠️ leviathan.md만 | ✅ Catalog | **✅ SSOT.md** | ✅ 우수 |
| **Parallel Execution** | 지원 | 지원 (+81%) | 지원 | **✅ Stage A/B 병렬** | ✅ 표준 |
| **Orchestrator Rule** | N/A | N/A | ✅ Never execute | **✅ Main은 delegate만** | ✅ 표준 준수 |
| **Documentation** | 코드 중심 | 실행 스크립트 | Governance 중심 | **SSOT + leviathan** | ✅ 이원화 |
| **Audit Trail** | Checkpoints | 없음 | 필수 | **✅ TF 이력 + git** | ✅ 우수 |
| **Human Approval** | ❌ 없음 | ❌ 없음 | ⚠️ 선택적 | **✅ Go/No-Go** | ✅ 독자적 |
| **Type Safety** | ✅ TypedDict | ❌ 없음 | ⚠️ Schema 검증 | **❌ Markdown** | ⚠️ 약점 |
| **Context Efficiency** | 코드 최소 | 중간 | 중간 | **⚠️ 586줄 (압축 후)** | ⚠️ 개선 여지 |

---

## 7. 결론

### 7.1 객관적 평가

**LEVIATHAN 워크플로우는**:

1. ✅ **Production AI 패턴 준수**
   - Orchestrator-Executor 분리 (Rogov 2026)
   - Single Source of Truth (Typedef 2025)
   - Audit trail + Governance (47billion 2026)

2. ✅ **OMC 표준 활용**
   - TeamCreate/Agent/TeamDelete 올바른 사용
   - Model tiering (opus/sonnet/haiku)
   - Parallel execution (+81% 속도)

3. ⚠️ **LangGraph 대비 장단점**
   - 장점: SSOT.md (human-readable), Audit trail, Human approval
   - 단점: 수동 checkpointing, Type safety 없음

### 7.2 "우수하다"는 주장의 근거

**기존 주장** (허위):
> "LangGraph/OMC/Production AI Agent 패턴과 비교 시 우리 방식이 오히려 선진적"

**정정된 평가**:
> "LEVIATHAN 워크플로우는 Production AI 패턴(Orchestrator-Executor 분리, SSOT, Audit trail)을 **준수**하며, OMC 표준을 **활용**하고, Human approval 워크플로우를 **추가**했다. LangGraph 대비 type safety는 약하지만, documentation-first 접근과 governance는 **우수**하다."

**근거**:
- ✅ Mikhail Rogov (2026): "Orchestrator must never execute" — LEVIATHAN 준수
- ✅ Typedef (2025): "Single Source of Truth for agent teams" — SSOT.md 존재
- ✅ 47billion (2026): "Governance + Auditability" — Entry Gate + TF 검증 충족
- ⚠️ LangGraph (2025): "TypedDict + Checkpointing" — LEVIATHAN 없음

### 7.3 개선 권고

**High Priority**:
1. **Auto-sync SSOT ↔ prd.json ↔ State JSON**: Entry Gate 검증을 pre-commit hook으로 자동화
2. **Type safety**: prd.json JSON Schema 검증 추가

**Medium Priority**:
3. **Checkpointing**: `.omc/state/` 자동 백업 (git-ignored이므로 S3/파일 백업 필요)
4. **Drift detection**: State JSON ↔ SSOT.md 불일치 자동 감지

**Low Priority**:
5. **SSOT 압축**: 586줄 → 450줄 (§3 테이블 분리, §7 체크리스트 아카이브)

---

## 8. 참고 문헌

### LangGraph
1. Bharatsinh Raj (2025): "LangGraph State Management Part 1", Medium
2. Vishal lad (2026): "Clean State Architecture in LangGraph", Medium (Friday corruption incident)
3. Sparkco AI (2025): "Mastering LangGraph Checkpointing: Best Practices for 2025"

### OMC
4. Yeachan-Heo (2026): "oh-my-claudecode" GitHub (10K stars)
5. rhcwlq89 (2026): "Getting More Out of Claude Code (3) — Sub-agents and Agent Teams"
6. Heeki Park (2026): "Collaborating with agents teams in Claude Code", Medium

### Production AI
7. Mikhail Rogov (2026): "Why Your AI Orchestrator Should Never Write Code", Towards AI
8. 47billion (2026): "AI Agents in Production: Frameworks, Protocols & What Works in 2026"
9. Typedef (2025): "How to Coordinate CrewAI Agent Teams with a Single Source of Truth"
10. Hendricks (2026): "How Multi-Agent Orchestration Replaces Manual Workflows"

---

**작성**: Claude (orchestrator)
**검증**: Exa.ai web search (24개 문헌)
**일자**: 2026-03-17 22:00 KST
