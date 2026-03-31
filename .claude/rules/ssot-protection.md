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

## 컨텍스트 memoize 주의

세션 중 SSOT.md 수정 내용은 **현재 세션의 다른 에이전트에게 반영되지 않습니다**.
수정 완료 후 세션을 재시작하거나 `/memory`를 호출하세요.
