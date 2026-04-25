# Bug: KRW Exchange (coinone/upbit/bithumb) USDT pair 단위 Mix

**Date**: 2026-04-22
**Status**: DRAFT (별도 Day 작업, real-time monitor 발견)
**Priority**: HIGH (33K toxicity reject의 root cause)

---

## 발견

v3 engine 41분 elapsed monitoring 중 발견:

```
exchange=coinone symbol=MTL/USDT reason=imbalance imbalance=0.7132
  bid=16997.77 ask=2845.46

exchange=coinone symbol=KNC/USDT reason=imbalance imbalance=0.9935
  bid=30108.73 ask=98.44

exchange=bitget symbol=ALGO/USDT reason=imbalance imbalance=0.7698
  bid=777967.92 ask=101174.58
```

**증상**: bid_depth >> ask_depth (또는 vice versa) — thin liquidity asymmetry.

## 진단 정정 (2026-04-22 후속)

**원래 진단**: KRW base × USDT base 가격 mix
**정정**: toxicity_filter의 `bid=`/`ask=`는 **depth (base currency units)**, 가격 아님.
즉 KRW data normalization bug 아닌 **시장의 thin liquidity** (소형코인 매수/매도 사이드 depth 큰 차이).

KRW × USDT pair 차단 fix (`705be52`)는 안전하지만 이 진단의 직접 fix 아님.
v4 engine에서 KRW exchange toxicity reject 거의 0이지만, 전체 toxicity는 binance_futures/bitget_futures 같은 USDT-only 거래소의 thin liquidity 거부로 여전히 높음.

**진짜 root cause** (재진단):
- 소형코인 (KITE, KAT, MOVE 등) thin orderbook
- futures 거래소도 spot보다 thin liquidity 흔함
- signal_generator가 thin pair 사전 filter 미흡 → toxicity layer가 99% 거부

## Root Cause 후보

1. **coinone**: KRW 마켓이지만 cross_exchange_spot 전략이 USDT pair signal 생성. KRW→USDT 환율 변환 누락 → bid (KRW base)와 ask (USDT base) mix.
2. **bitget**: WS data 자체 stale (orderbook snapshot 미적용 + 증분만) — small alts에서 비정상 cross.
3. **collectors/coinone.py**: pair mapping (USDT → KRW) 시 가격 단위 변환 안 됨.

## 영향

- 33K + cycle별 증가 = 시간당 ~50K toxicity rejects
- spot_futures + cross_exchange_spot signal의 ~30%가 KRW exchange 관련 → 거의 전부 reject
- fill rate 정체 (5분 측정 5건 → 41분 측정 7건)
- toxicity filter 정상 동작 (false data 차단), 그러나 signal 효율성 낮음

## 임시 차단

KRW exchanges (coinone, upbit, bithumb)에서 cross_exchange_spot/spot_futures USDT pair 신호 생성 차단:

```python
# engine/src/strategies/base.py 또는 universe_matrix.py
KRW_EXCHANGES = {"coinone", "upbit", "bithumb"}
if exchange_id in KRW_EXCHANGES and symbol.endswith("/USDT"):
    # cross_exchange_spot은 KRW 거래소에서 USDT pair signal 생성 금지
    # KRW pair만 의미 있음 (KRW arb 전략용)
    return None  # signal reject
```

## 근본 fix (별도 Day)

1. `engine/src/collectors/coinone.py`: WS message 받을 때 pair_format 자동 변환 (USDT → KRW)
2. `engine/src/core/krw_rate_provider.py`: KRW/USDT 환율 사용해서 가격 정규화
3. `engine/src/risk/toxicity_filter.py`: cross-currency pair 감지 시 더 명확한 reject reason emit

## Acceptance Criteria

- AC-1: coinone/upbit/bithumb USDT pair 신호에서 KRW base 가격 leak 없음
- AC-2: toxicity reject 33K → < 5K (10x 감소)
- AC-3: fill rate (1h 측정) 7 → 30+ (4x 증가)
- AC-4: regression 5053 pass 유지

## 견적

3-5일. KRW exchange WS adapter + 환율 변환 + universe_matrix 필터 + 통합 검증.

## 시작 조건

paper canary 24h 통과 후. 현재 system이 false data 정확 차단 중이므로 자본 위험 없음. 별도 Day plan으로 진행.

---

## 참조

- 발견 trace: `/tmp/leviathan_v3.log` (41분 monitor 중)
- 메트릭: `signal_rejected_by_toxicity` 33,966건 (41분)
- 거래소 분포: bitget 14K, bitget_futures 9K, coinone 7K, bithumb 6K, binance_futures 6K
- 메모리: `feedback_engine_pipeline_design.md` (단일 배관 + 모드 스위칭)
