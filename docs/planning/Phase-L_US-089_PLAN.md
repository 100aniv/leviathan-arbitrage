# US-089: CEX-DEX 스프레드 스캐너

## 변경 대상
1. `engine/src/strategies/cex_dex.py` — DEXCostCalculator 통합 + scan_spread 메서드
2. `engine/src/main.py` — _build_dex_adapter() 실제 구현

## 구현 상세

### 1. cex_dex.py — DEXCostCalculator 통합

#### 1a. __init__ 확장
```python
def __init__(self, ..., dex_cost_calculator=None):
    ...
    self._dex_cost = dex_cost_calculator  # US-089
```

#### 1b. on_signal 수정 — DEXCostCalculator 사용
기존: hardcoded `friction_cost_pct`로 비용 계산
변경: DEXCostCalculator 있으면 실제 LP fee + gas + MEV + bridge 비용 사용

```python
# US-089: DEXCostCalculator로 실제 비용 계산
if self._dex_cost is not None:
    dex_cost_result = self._dex_cost.calculate(
        notional_usd=notional,
        fee_tier=self._config.dex_fee_bps * 10,  # 30bps → 3000
        gas_cost_usd=gas_cost_usd,
    )
    total_cost_pct = dex_cost_result.total_cost_bps / Decimal("10000")
else:
    total_cost_pct = self._config.friction_cost_pct + gas_pct
```

#### 1c. scan_spread() 메서드 추가
```python
async def scan_spread(self, cex_mid: Decimal, symbol: str) -> dict:
    """CEX vs DEX 스프레드 스캔 (외부 호출용).

    Returns:
        dict with raw_spread_bps, net_spread_bps, gas_cost_usd, direction
    """
```

### 2. main.py — _build_dex_adapter() 구현

```python
def _build_dex_adapter(self):
    dex_rpc = os.getenv("DEX_RPC_URL", "")
    if not dex_rpc:
        return None
    from src.infra.dex.uniswap_v3 import UniswapV3Adapter
    pool = os.getenv("DEX_POOL_ADDRESS", "")
    if not pool:
        return None
    return UniswapV3Adapter(rpc_url=dex_rpc, pool_address=pool)
```

DEXCostCalculator도 생성하여 CexDexStrategy에 전달.

## QUANT 검증 포인트
- DEXCostCalculator는 LP fee + gas + MEV 실제 비용 → friction_cost_pct 대체
- 기존 동작 보존: dex_cost_calculator=None이면 기존 friction_cost_pct 사용
- net_edge 계산: |CEX_mid - DEX_spot| / CEX_mid - total_cost > min_edge
- scan_spread는 read-only → 부수효과 없음

## 테스트
- DEXCostCalculator 통합 시 비용 계산 정확도
- dex_cost_calculator=None → 기존 friction_cost_pct 사용 (하위호환)
- scan_spread 반환값 검증
- _build_dex_adapter DEX_RPC_URL 미설정 → None
- _build_dex_adapter DEX_RPC_URL 설정 → UniswapV3Adapter 반환
