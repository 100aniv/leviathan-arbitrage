"""Tests for FeeModel — maker/taker fee calculator."""
import pytest
from decimal import Decimal

from src.friction.fee_model import FeeConfig, FeeModel, FeeType


class TestFeeModelDefault:
    def test_taker_fee_binance_tier0(self):
        model = FeeModel()
        fee = model.taker_fee("binance", Decimal("10000"))
        assert fee == Decimal("10")  # 0.10% of 10000

    def test_maker_fee_binance_tier0(self):
        model = FeeModel()
        fee = model.maker_fee("binance", Decimal("10000"))
        assert fee == Decimal("10")  # 0.10% of 10000

    def test_taker_fee_okx_tier0(self):
        model = FeeModel()
        fee = model.taker_fee("okx", Decimal("10000"))
        assert fee == Decimal("10")  # 0.10% of 10000

    def test_maker_fee_okx_tier0(self):
        model = FeeModel()
        fee = model.maker_fee("okx", Decimal("10000"))
        assert fee == Decimal("8")  # 0.08% of 10000

    def test_taker_fee_bybit_tier0(self):
        model = FeeModel()
        fee = model.taker_fee("bybit", Decimal("10000"))
        assert fee == Decimal("6")  # 0.06% of 10000

    def test_unknown_exchange_raises(self):
        model = FeeModel()
        with pytest.raises(ValueError, match="Unknown exchange"):
            model.taker_fee("unknown_exchange", Decimal("1000"))

    def test_fee_scales_with_notional(self):
        model = FeeModel()
        fee_small = model.taker_fee("binance", Decimal("1000"))
        fee_large = model.taker_fee("binance", Decimal("10000"))
        assert fee_large == fee_small * 10


class TestFeeModelTiers:
    def test_set_tier_changes_fees(self):
        model = FeeModel()
        fee_tier0 = model.taker_fee("binance", Decimal("10000"))
        model.set_tier("binance", 1)
        fee_tier1 = model.taker_fee("binance", Decimal("10000"))
        assert fee_tier1 < fee_tier0

    def test_get_tier_default_is_zero(self):
        model = FeeModel()
        assert model.get_tier("binance") == 0
        assert model.get_tier("okx") == 0

    def test_set_then_get_tier(self):
        model = FeeModel()
        model.set_tier("binance", 2)
        assert model.get_tier("binance") == 2

    def test_fee_enum_maker(self):
        model = FeeModel()
        fee = model.fee("binance", Decimal("1000"), FeeType.MAKER)
        assert fee == model.maker_fee("binance", Decimal("1000"))

    def test_fee_enum_taker(self):
        model = FeeModel()
        fee = model.fee("binance", Decimal("1000"), FeeType.TAKER)
        assert fee == model.taker_fee("binance", Decimal("1000"))

    def test_different_exchanges_independent_tiers(self):
        model = FeeModel()
        model.set_tier("binance", 2)
        # okx should still be tier 0
        assert model.get_tier("okx") == 0
        assert model.get_tier("binance") == 2


class TestFeeModelRates:
    def test_maker_rate_binance(self):
        model = FeeModel()
        rate = model.maker_rate("binance")
        assert rate == Decimal("0.0010")

    def test_taker_rate_binance(self):
        model = FeeModel()
        rate = model.taker_rate("binance")
        assert rate == Decimal("0.0010")

    def test_maker_rate_okx(self):
        model = FeeModel()
        rate = model.maker_rate("okx")
        assert rate == Decimal("0.0008")

    def test_taker_rate_okx(self):
        model = FeeModel()
        rate = model.taker_rate("okx")
        assert rate == Decimal("0.0010")

    def test_bybit_maker_rate_tier0(self):
        model = FeeModel()
        rate = model.maker_rate("bybit")
        assert rate == Decimal("0.0001")

    def test_bybit_taker_rate_tier0(self):
        model = FeeModel()
        rate = model.taker_rate("bybit")
        assert rate == Decimal("0.0006")


class TestFeeModelCustom:
    def test_custom_fee_config(self):
        custom = {
            "test_exchange": [
                FeeConfig("test_exchange", 0, Decimal("0.0005"), Decimal("0.0008")),
            ]
        }
        model = FeeModel(custom_fees=custom)
        assert model.maker_rate("test_exchange") == Decimal("0.0005")
        assert model.taker_rate("test_exchange") == Decimal("0.0008")
