# K-BT 역사 데이터 API 한계 문서

> 작성일: 2026-04-04 | US-387 조사 결과

## 요약

| 거래소 | 역사 데이터 가능 | 최대 과거 | 원인 |
|--------|----------------|---------|------|
| Bithumb | ⚠️ 제한적 | ~209일 (2025-09-08~) | 1h API 고정 5000건, from 파라미터 무시 |
| Gate.io | ⚠️ 제한적 | ~416일 (2025-02-12~) | 10000 포인트 제한 |
| Coinone | ✅ 완전 | 2014-08-22~ | URL 순서 오류 수정 후 정상 (KRW/BTC) |

---

## Bithumb

**공식 API**: `GET /public/candlestick/{order_currency}_{payment_currency}/{interval}`

**한계**:
- `1h` interval: 최신 **5,000캔들 고정** (~209일) — 페이지네이션 없음
- `from` 파라미터: 문서에 없음, 실측으로 **완전히 무시됨** 확인
- `24h` interval: 2013-12-26까지 가능 (4391건 일괄 반환)

**K-BT 영향**:
- K-BT-07: 원래 2024-01-10~2024-06-30 → **대체: 2025-09-08~2026-03-31**
- K-BT-11: 원래 2025-01-01~2025-03-31 → **대체: 2025-09-08~2026-03-31**

---

## Gate.io

**공식 API**: `GET /api/v4/spot/candlesticks`

**한계**:
- 최대 `from`: 현재 기준 **9998시간(~416일) 전**까지 (실측 바이너리서치 확인)
- 에러: `"Candlestick too long ago. Maximum 10000 points ago are allowed"`
- 최대 `limit`: 1000 (10000 지정 시 INVALID_PARAM_VALUE)
- `SOL/BTC` 페어: Gate.io에 없음 (HTTP 400)

**K-BT 영향**:
- K-BT-09: 원래 2024-04-01~2024-09-30 → **대체: 2025-07-01~2025-12-31**
- SOL/BTC 심볼 제외

---

## Coinone (수정 완료)

**버그 원인**: URL 경로 순서 오류
- 잘못된 URL: `/public/v2/chart/{target}/{quote}` → `BTC/KRW` → error 109
- 올바른 URL: `/public/v2/chart/{quote}/{target}` → `KRW/BTC` → 정상

**수정 내용** (`download_historical.py`):
1. URL: `chart/{symbol}/KRW` → `chart/KRW/{symbol}`
2. 파라미터: `period` → `interval`
3. 페이지네이션: `from` 순방향 → `timestamp` 역방향 (is_last 종료)
4. PAGE_SIZE: 200 → 500

**결과**: 2014-08-22부터 전체 역사 데이터 접근 가능 ✅

---

## K-BT 케이스 최종 데이터 현황

| K-BT | 거래소 | 기간 | DB rows | 상태 |
|------|--------|------|---------|------|
| K-BT-01 | binance + binance_futures | 2024-01-10~2024-09-30 | 65K+ / 15K+ | ✅ |
| K-BT-02 | bybit + bybit_futures | 2024-01-10~2024-09-30 | 64K+ / 15K+ | ✅ |
| K-BT-03 | okx + okx_futures | 2024-01-10~2024-09-30 | 65K+ / 15K+ | ✅ |
| K-BT-04 | bitget + bitget_futures | 2024-01-10~2024-03-31 | 8.6K / 6K | ✅ |
| K-BT-05 | coinone | 2024-01-10~2024-06-30 | 12,709 | ✅ (URL fix) |
| K-BT-06 | upbit | 2024-01-10~2024-06-30 | 16,479 | ✅ |
| K-BT-07 | bithumb | ~~2024-01-10~06-30~~ → **2025-09-08~2026-03-31** | 927K | ⚠️ 기간 조정 |
| K-BT-08 | mexc | 2024-04-01~2024-09-30 | 25,415 | ✅ (PAGE_SIZE fix) |
| K-BT-09 | gateio | ~~2024-04-01~09-30~~ → **2025-07-01~2025-12-31** | 17,628 | ⚠️ 기간 조정 |
| K-BT-10 | binance + upbit | 2025-01-01~2025-03-31 | 6.4K each | ✅ |
| K-BT-11 | binance + bithumb | ~~2025-01-01~03-31~~ → **2025-09-08~2026-03-31** | 1.45M / 927K | ⚠️ 기간 조정 |
| K-BT-12 | binance + coinone | 2025-01-01~2025-03-31 | 6.4K each | ✅ (URL fix) |
| K-BT-13 | binance + bybit | 2024-01-10~2024-03-31 | ✅ | ✅ |
| K-BT-14 | binance + okx | 2024-01-10~2024-03-31 | ✅ | ✅ |
| K-BT-15 | binance + bitget | 2024-01-10~2024-03-31 | ✅ | ✅ |
| K-BT-16 | binance_futures + bitget_futures | 2024-01-10~2024-03-31 | ✅ | ✅ |
| K-BT-17 | binance_futures + bybit_futures | 2024-01-10~2024-03-31 | ✅ | ✅ |
| K-BT-18 | binance_futures + okx_futures | 2024-01-10~2024-03-31 | ✅ | ✅ |
