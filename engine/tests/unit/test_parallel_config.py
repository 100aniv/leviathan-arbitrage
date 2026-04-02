"""Unit tests for US-373: engine.json parallel section — 검증 완료 4조합 병렬 운영 설정."""
import json
import pathlib

import pytest

CONFIG_PATH = pathlib.Path(__file__).parents[2] / "config" / "engine.json"


@pytest.fixture(scope="module")
def engine_cfg():
    with CONFIG_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def parallel(engine_cfg):
    return engine_cfg["parallel"]


@pytest.fixture(scope="module")
def combinations(parallel):
    return parallel["combinations"]


# 1. parallel 섹션 존재
def test_parallel_section_exists(engine_cfg):
    assert "parallel" in engine_cfg, "engine.json에 'parallel' 섹션이 없습니다"


# 2. parallel.enabled = True
def test_parallel_enabled(parallel):
    assert parallel["enabled"] is True


# 3. combinations 길이 = 4
def test_combinations_count(combinations):
    assert len(combinations) == 4


# 4. 4조합 ID: P-01, P-05, P-12, P-08
def test_combination_ids(combinations):
    ids = {c["id"] for c in combinations}
    assert ids == {"P-01", "P-05", "P-12", "P-08"}


# 5. 전략: funding_rate×2, cross_exchange×1, triangular×1
def test_combination_strategies(combinations):
    strategies = [c["strategy"] for c in combinations]
    assert strategies.count("funding_rate") == 2
    assert strategies.count("cross_exchange") == 1
    assert strategies.count("triangular") == 1


# 6. P-01 exchange = "binance"
def test_p01_exchange(combinations):
    p01 = next(c for c in combinations if c["id"] == "P-01")
    assert p01["exchange"] == "binance"


# 7. P-08 exchange = "coinone"
def test_p08_exchange(combinations):
    p08 = next(c for c in combinations if c["id"] == "P-08")
    assert p08["exchange"] == "coinone"


# 8. P-12 exchanges에 "binance"와 "bitget" 포함
def test_p12_exchanges(combinations):
    p12 = next(c for c in combinations if c["id"] == "P-12")
    assert "binance" in p12["exchanges"]
    assert "bitget" in p12["exchanges"]


# 9. total_capital_usd = 30 (7.5×4)
def test_total_capital(parallel):
    assert parallel["risk"]["total_capital_usd"] == 30


# 10. max_mdd_pct = 5.0
def test_max_mdd_pct(parallel):
    assert parallel["risk"]["max_mdd_pct"] == 5.0
