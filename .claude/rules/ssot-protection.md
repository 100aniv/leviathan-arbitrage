---
globs: ["SSOT.md", ".omc/prd.json", "SSOT_COMPLETE.md"]
---

# SSOT 보호 규칙

이 규칙은 `SSOT.md`, `.omc/prd.json`, `SSOT_COMPLETE.md` 파일 접근 시 자동 적용됩니다.

## SSOT.md 수정 원칙

- **직접 Edit 금지**: `ssot-keeper` 에이전트를 통해서만 수정
- Phase 완료 시 동기화 CLI 사용:
  ```bash
  cd engine && python -m src.workflow.cli sync \
    --phase X --tests Y --prd-pass Z --prd-total W
  ```
- 수동 수정 시 반드시 `check_all` 9/9 OK 확인 후 커밋

## PRD (.omc/prd.json) 수정 원칙

- `passes:true` 선언 = 런타임 호출 증거 필수
- 증거 없이 `passes:true` 변경 = 거짓 양성 → 나중에 TF에서 반드시 발각됨
- dead code (정의는 있으나 호출 없음) = `passes:false` 유지

## math-models.md 동기화 의무

`SSOT.md §4` 수정 시 `.claude/rules/math-models.md`도 반드시 함께 업데이트:
- §4.1 슬리피지 수식 변경 → math-models.md §4.1 업데이트
- §4.2 수수료 테이블 변경 → math-models.md §4.2 업데이트
- 기타 §4 하위 섹션 동일

## 컨텍스트 memoize 주의

세션 중 SSOT.md 수정 내용은 **현재 세션의 다른 에이전트에게 반영되지 않습니다**.
수정 완료 후 세션을 재시작하거나 `/memory`를 호출하세요.

## Path-B v2 14-Doc Sync Rule (2026-04-20)

Every Day N commit MUST include updates to 4+ of the following 14 canonical docs:

1. `SSOT.md` — via ssot-keeper agent only
2. `REFACTOR_PLAN.md` — engine refactor progress
3. `MODULE_DESIGN.md` — module interface contracts
4. `OPERATOR_RUNBOOK.md` — operational procedures (requires 2 reviewer approval for changes)
5. `README.md` — root project readme
6. `engine/README.md` — engine-specific readme
7. `dashboard/README.md` — dashboard readme
8. `dashboard/docs/DESIGN.md` — dashboard design spec
9. `docs/archive/PHOENIX_PLAN.md` — FROZEN 2026-04-20 (historical record, do not edit)
10. `.env.example` — feature flag documentation
11. `docker-compose.yml` — infra service definitions
12. `infra/grafana/dashboards/*.json` — Grafana dashboard specs
13. `.claude/CLAUDE.md` — project agent rules
14. `.claude/rules/math-models.md` — math model SSOT mirror

### Enforcement
- Operator runbook changes (`OPERATOR_RUNBOOK.md`) require 2 reviewer approval before merge
- SSOT.md changes go through ssot-keeper agent (direct Edit forbidden)
- `docs/archive/PHOENIX_PLAN.md` is FROZEN — prepend freeze header if not present, never edit body
- Day N commit message must reference which docs were updated (e.g. `docs: .env.example, docker-compose.yml, CLAUDE.md, math-models.md`)
