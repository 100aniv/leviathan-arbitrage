---
globs: ["engine/src/strategies/*.py", "engine/src/core/signal.py", "engine/src/modes/*.py"]
---

# 전략/시그널 파일 수정 시 강제 규칙

이 규칙은 `engine/src/strategies/`, `engine/src/core/signal.py`, `engine/src/modes/` 파일 편집 시 자동 적용됩니다.

## 필수 확인 (수정 전)

1. **이중 슬리피지 절대 금지**
   - `SignalGenerator`의 `CEXOrderbookSlippage`가 유일한 슬리피지 소스
   - `PaperExecutor`에 `PowerLawSlippage` 적용 = 즉시 차단
   - `k=0.0`이므로 PowerLaw는 비활성 상태 유지

2. **WIRING AC 3개 필수** (새 컴포넌트 추가 시)
   - `생성`: `__init__()` 또는 인스턴스 생성 코드
   - `주입`: 의존성 주입 경로 (`Engine.__init__` → 컴포넌트)
   - `호출`: 실제 런타임 호출 경로 (dead code 방지)

3. **passes:true 거짓 양성 금지**
   - 코드 존재 ≠ 완료
   - 런타임 호출 증거(로그/메트릭) 없으면 `passes:false` 유지

## 수정 후 필수

```bash
cd engine && python -m pytest tests/ -x --tb=short
```

테스트 실패 시 수정 완료로 간주하지 말 것.

## KRW 거래소 특이사항
- `upbit`, `bithumb`, `coinone` → KRW 페어 자동 매핑
- `auto-symbols` 설정: `min_exchanges=3` 필수 (7로 설정 시 symbol 0개)
- Bithumb stale data: ±50% 가드 + 2단계 REST 검증 코드 제거 금지
