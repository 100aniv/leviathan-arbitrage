# Phase S19 PLAN: DataQualityManager 통합

**작성일**: 2026-03-21
**US**: US-286, US-287, US-288, US-289, US-290, US-290-a
**Entry Gate**: Karina PASS

---

## 1. 목표

StaleOrderbookDetector(기존)와 HealthChecker(기존)를 DataQualityManager 중앙 객체로 통합.
RiskGuardian과 LiveGate에 주입하여 데이터 품질이 리스크 판단과 게이트 통과에 직접 반영되도록 한다.

---

## 2. 의존성 그래프

```
US-286 (DataQualityManager 중앙객체)
  ├── US-287 (차등 Freshness)     ─┐
  ├── US-288 (Health Score 통합)  ─┤ 병렬
  ├── US-289 (Anomaly Detection)  ─┤
  └── US-290 (Bithumb stale 특화) ─┘
        └── US-290-a (Shadow 10min 통합 검증)
```

**구현 순서**: US-286 → (US-287/288/289/290 병렬) → US-290-a

---

## 3. US별 상세

### US-286: DataQualityManager 중앙객체

**신규 파일**: `engine/src/core/data_quality_manager.py`

**설계**:
- `DataQualityManager` 클래스: `StaleOrderbookDetector`와 `HealthChecker` dict를 내부 보유
- 단일 진입점 `check(exchange, symbol, book, all_books, seq, spread) -> DataQualityResult`
- `DataQualityResult(ok: bool, score: float, reasons: list[str])`
- `get_health_score(exchange) -> float` — HealthChecker 위임
- `get_or_create_health_checker(exchange_id) -> HealthChecker` — lazy init
- `register_exchange(exchange_id)` — 명시적 등록 지원

**변경 파일**:
- `engine/src/core/data_quality_manager.py` (신규)

**테스트**: `tests/unit/core/test_data_quality_manager.py`
- `DataQualityManager` 생성, `check()` OK/FAIL 반환, score 범위 [0,1]
- HealthChecker lazy init 확인

---

### US-287: 차등 Freshness 정책

**설계**:
- 거래소별 freshness threshold 매핑 (env 오버라이드 지원)
  - Korean exchanges (upbit/bithumb/coinone): `FRESHNESS_KOREAN_S` (기본 2.0s)
  - Futures exchanges (binance_futures/bybit): `FRESHNESS_FUTURES_S` (기본 0.5s)
  - Default: `FRESHNESS_DEFAULT_S` (기본 1.0s)
- `DataQualityManager.get_freshness_threshold(exchange_id) -> float`
- `check_freshness(exchange, last_update_ts) -> bool` — StaleDetector heartbeat 기반

**변경 파일**:
- `engine/src/core/data_quality_manager.py` (freshness 로직 추가)

**테스트**:
- Korean exchange에 2.0s, Binance에 0.5s threshold 확인
- ENV 오버라이드 반영 확인

---

### US-288: Health Score 통합

**설계**:
- `DataQualityManager`가 exchange별 `HealthChecker` 인스턴스 관리
- `record_api_latency(exchange, ms)`, `record_ws_disconnect(exchange)`, `record_heartbeat(exchange)` 위임 메서드 노출
- `aggregate_health_score(exchanges: list[str]) -> float` — 최솟값 기반 (취약한 거래소가 전체를 제한)
- RiskGuardian Check #5 (exchange health score) → DQM에서 조회하도록 교체

**변경 파일**:
- `engine/src/core/data_quality_manager.py` (health score 집계)
- `engine/src/risk/guardian.py` — Check #5를 DQM.get_health_score() 호출로 교체

**테스트**:
- 2개 거래소 중 1개 낮으면 aggregate 최솟값 반환
- guardian Check #5 DQM 주입 경로 확인

---

### US-289: Anomaly Detection

**설계**:
- `AnomalyDetector` (내부 클래스 또는 별도 모듈): z-score 기반 이상가격 탐지
  - `update(exchange, symbol, price)` — 롤링 mean/std 갱신 (window=100)
  - `is_anomaly(exchange, symbol, price, z_thresh=4.0) -> bool`
- `DataQualityManager.check()` 내부에서 anomaly 탐지 결과를 `DataQualityResult.reasons`에 추가
- 이상 탐지 시 score 0.0, ok=False 반환

**변경 파일**:
- `engine/src/core/data_quality_manager.py` (AnomalyDetector 내장)

**테스트**:
- 정상 가격 → anomaly=False
- mean+5*std 가격 → anomaly=True, ok=False 반환
- window 미달 시 warmup pass-through

---

### US-290: Bithumb stale 특화

**설계**:
- `StaleOrderbookDetector`의 `_deviation_pct`를 Bithumb에 대해 강화
  - Bithumb: `BITHUMB_DEVIATION_PCT` env (기본 0.05 = 5%, 일반 10%보다 엄격)
  - Bithumb: `BITHUMB_FRESHNESS_S` env (기본 1.0s, 일반 Korean 2.0s보다 엄격)
- `DataQualityManager.get_freshness_threshold()` 및 `get_or_create_health_checker()` 분기 추가
- 소형코인 (volume < threshold) 추가 필터: 가격 2x 이상 편차 → 즉시 blacklist (TTL 600s)

**변경 파일**:
- `engine/src/core/data_quality_manager.py` (Bithumb 분기)
- `engine/src/core/stale_detector.py` — `_deviation_pct` 파라미터화 이미 지원됨, DQM에서 per-exchange 인스턴스로 교체

**테스트**:
- Bithumb 심볼에 5% deviation 임계치 적용 확인
- 소형코인 2x 편차 → blacklist TTL 600s 확인
- 일반 거래소에는 10% 유지 확인

---

### US-290-a: Shadow 10min 통합 검증

**설계**: Shadow 10분 런 후 아래 항목 확인
1. DQM `check()` 호출 횟수 > 0 (dead code 아님 증명)
2. Bithumb blacklist 건수 로그 노출
3. RiskGuardian Check #5 DQM 경유 거부 건수 > 0 (또는 PASS)
4. LiveGate health_score criterion이 DQM 값 반영
5. MDD < 5%, PF > 1.0, crash 0건

**검증 명령**:
```bash
cd engine && timeout 600 python -m src.main 2>&1 | grep -E "data_quality|health_score|blacklist|dqm"
```

---

## 4. main.py Wiring 계획

```python
# engine/src/main.py — Engine._setup() 내 추가 위치 (Step 6: risk 초기화 직후)

from src.core.data_quality_manager import DataQualityManager

# 1. 생성 (stale_detector, health_checker dict 내부 초기화)
data_quality_manager = DataQualityManager()

# 2. RiskGuardian에 주입
guardian = RiskGuardian(
    ...existing params...,
    data_quality_manager=data_quality_manager,  # Check #5 교체
)

# 3. LiveGate에 주입
live_gate = LiveGate(
    ...existing params...,
    data_quality_manager=data_quality_manager,  # health score criterion
)

# 4. SignalGenerator/CollectorManager에서 DQM.record_heartbeat() 호출
# → CollectorManager._on_orderbook() 내 data_quality_manager.record_heartbeat(exchange) 추가
```

**변경 파일 요약**:
| 파일 | 변경 유형 |
|------|----------|
| `engine/src/core/data_quality_manager.py` | 신규 |
| `engine/src/risk/guardian.py` | Check #5 DQM 위임 |
| `engine/src/modes/live_gate.py` | health_score → DQM 조회 |
| `engine/src/main.py` | DQM 생성 + 주입 |
| `engine/src/collectors/collector_manager.py` | DQM.record_heartbeat() 호출 |

---

## 5. WIRING AC (Assembly Gate 필수)

각 US의 WIRING Acceptance Criteria:

| US | 생성 | 주입 | 호출 |
|----|------|------|------|
| US-286 | `DataQualityManager()` in main.py | guardian, live_gate 생성자 | `dqm.check()` in signal path |
| US-287 | freshness 매핑 dict | DQM 내부 | `get_freshness_threshold()` called |
| US-288 | HealthChecker dict lazy init | DQM 내부 | `record_heartbeat()` in collector |
| US-289 | AnomalyDetector rolling stats | DQM 내부 | `is_anomaly()` in `check()` |
| US-290 | Bithumb 분기 설정 | DQM 내부 | blacklist TTL 600s verified |

---

## 6. 리스크 항목

| 리스크 | 완화 |
|--------|------|
| HealthChecker per-exchange 메모리 누수 | maxlen deque 이미 구현됨. DQM cleanup_expired() 주기 호출 |
| Bithumb 과도한 blacklist → 거래 0건 | TTL 600s + US-290-a Shadow에서 trade count 확인 |
| DQM check() 레이턴시 증가 | 4-layer all sync, 추가 z-score O(1). 1ms 이내 예상 |
| guardian.py backward compat | health_checker 기존 파라미터 유지 + DQM optional injection (None 시 기존 로직 유지) |

---

## 7. 테스트 파일 목록

```
engine/tests/unit/core/test_data_quality_manager.py   (신규, ~80 lines)
engine/tests/unit/core/test_s19_freshness.py          (신규, ~40 lines)
engine/tests/unit/core/test_s19_anomaly.py            (신규, ~40 lines)
engine/tests/unit/core/test_s19_bithumb_stale.py      (신규, ~40 lines)
```

**기존 테스트 수정**:
- `tests/unit/strategies/test_futures_futures.py` — DQM mock 추가 불필요 (guardian mock 그대로)
- `tests/unit/core/test_ou_process.py` — 영향 없음

---

## 8. 완료 기준

- [ ] `DataQualityManager.check()` 단위테스트 PASS
- [ ] Bithumb 5% deviation threshold 단위테스트 PASS
- [ ] `guardian.py` Check #5 DQM 경유 확인 (단위테스트)
- [ ] Shadow 10분 런: DQM 호출 로그 확인, MDD < 5%, crash 0건
- [ ] `python -m pytest tests/ -x --tb=short` 전체 PASS (기존 4,991 + 신규 ~200)
