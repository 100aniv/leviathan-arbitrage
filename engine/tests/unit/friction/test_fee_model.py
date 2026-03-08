"""Tests for FeeModel — maker/taker fee calculator + withdrawal fees."""
import pytest
from decimal import Decimal

from src.friction.fee_model import FeeConfig, FeeModel, FeeType, WITHDRAWAL_FEES_USD


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
        assert fee == Decimal("10")  # 0.10% spot (corrected from futures rate)

    def test_unknown_exchange_fallback(self):
        """Unknown exchange returns conservative 0.25% fallback instead of ValueError."""
        model = FeeModel()
        fee = model.taker_fee("unknown_exchange", Decimal("10000"))
        assert fee == Decimal("25")  # 0.25% of 10000

    def test_unknown_exchange_no_crash(self):
        model = FeeModel()
        rate = model.taker_rate("totally_fake_exchange")
        assert rate == Decimal("0.0025")  # 0.25% fallback

    def test_unknown_exchange_maker_rate_fallback(self):
        model = FeeModel()
        rate = model.maker_rate("totally_fake_exchange")
        assert rate == Decimal("0.0025")  # 0.25% fallback

    def test_fee_scales_with_notional(self):
        model = FeeModel()
        fee_small = model.taker_fee("binance", Decimal("1000"))
        fee_large = model.taker_fee("binance", Decimal("10000"))
        assert fee_large == fee_small * 10


class TestFeeModelTiers:
    def test_set_tier_changes_fees(self):
        model = FeeModel()
        fee_tier0 = model.maker_fee("binance", Decimal("10000"))
        model.set_tier("binance", 1)
        fee_tier1 = model.maker_fee("binance", Decimal("10000"))
        assert fee_tier1 < fee_tier0  # VIP1 maker < VIP0 maker

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
        assert rate == Decimal("0.0010")  # Spot 0.10%

    def test_bybit_taker_rate_tier0(self):
        model = FeeModel()
        rate = model.taker_rate("bybit")
        assert rate == Decimal("0.0010")  # Spot 0.10%


class TestFeeModelNewExchanges:
    """Tests for all exchange fee configs including Korean exchanges."""

    def test_bitget_taker_fee_tier0(self):
        model = FeeModel()
        fee = model.taker_fee("bitget", Decimal("10000"))
        assert fee == Decimal("10")  # 0.10%

    def test_bitget_maker_fee_tier0(self):
        model = FeeModel()
        fee = model.maker_fee("bitget", Decimal("10000"))
        assert fee == Decimal("10")  # 0.10%

    def test_bitget_tier1_lower_fees(self):
        model = FeeModel()
        model.set_tier("bitget", 1)
        fee = model.taker_fee("bitget", Decimal("10000"))
        assert fee == Decimal("9")  # 0.09% VIP1

    def test_upbit_taker_fee_tier0(self):
        model = FeeModel()
        fee = model.taker_fee("upbit", Decimal("10000"))
        assert fee == Decimal("13.9")  # 0.139% KRW taker

    def test_upbit_maker_fee_tier0(self):
        model = FeeModel()
        fee = model.maker_fee("upbit", Decimal("10000"))
        assert fee == Decimal("5")  # 0.05% KRW maker

    def test_bithumb_taker_fee_tier0(self):
        model = FeeModel()
        fee = model.taker_fee("bithumb", Decimal("10000"))
        assert fee == Decimal("25")  # 0.25% KRW market

    def test_bithumb_maker_fee_tier0(self):
        model = FeeModel()
        fee = model.maker_fee("bithumb", Decimal("10000"))
        assert fee == Decimal("25")  # 0.25%

    def test_coinone_api_rate(self):
        model = FeeModel()
        fee = model.taker_fee("coinone", Decimal("10000"))
        assert fee == Decimal("2")  # 0.02% API rate

    def test_binance_futures_taker(self):
        model = FeeModel()
        fee = model.taker_fee("binance_futures", Decimal("10000"))
        assert fee == Decimal("5")  # 0.05% USDT-M VIP0

    def test_binance_futures_maker(self):
        model = FeeModel()
        fee = model.maker_fee("binance_futures", Decimal("10000"))
        assert fee == Decimal("2")  # 0.02% USDT-M VIP0

    def test_okx_futures_taker(self):
        model = FeeModel()
        fee = model.taker_fee("okx_futures", Decimal("10000"))
        assert fee == Decimal("5")  # 0.05%

    def test_okx_futures_maker(self):
        model = FeeModel()
        fee = model.maker_fee("okx_futures", Decimal("10000"))
        assert fee == Decimal("2")  # 0.02%

    def test_bitget_futures_taker(self):
        model = FeeModel()
        fee = model.taker_fee("bitget_futures", Decimal("10000"))
        assert fee == Decimal("6")  # 0.06%

    def test_bitget_futures_maker(self):
        model = FeeModel()
        fee = model.maker_fee("bitget_futures", Decimal("10000"))
        assert fee == Decimal("2")  # 0.02%

    def test_all_exchanges_accessible(self):
        model = FeeModel()
        exchanges = [
            "binance", "okx", "bybit", "bitget",
            "upbit", "bithumb", "coinone",
            "binance_futures", "bybit_futures", "okx_futures", "bitget_futures",
        ]
        for ex in exchanges:
            rate = model.taker_rate(ex)
            assert rate > 0, f"{ex} taker rate should be positive"

    def test_krw_exchanges_higher_fees_than_global(self):
        model = FeeModel()
        upbit_rate = model.taker_rate("upbit")
        binance_rate = model.taker_rate("binance")
        assert upbit_rate > binance_rate, "KRW exchanges have higher fees"

    def test_bitget_maker_rate(self):
        model = FeeModel()
        assert model.maker_rate("bitget") == Decimal("0.0010")

    def test_upbit_taker_rate(self):
        model = FeeModel()
        assert model.taker_rate("upbit") == Decimal("0.00139")

    def test_bithumb_taker_rate(self):
        model = FeeModel()
        assert model.taker_rate("bithumb") == Decimal("0.0025")


class TestWithdrawalFees:
    """Tests for withdrawal fee lookup and network cost estimation."""

    def test_withdrawal_fee_binance_xrp(self):
        model = FeeModel()
        fee = model.withdrawal_fee("binance", "XRP")
        assert fee == Decimal("0.40")

    def test_withdrawal_fee_binance_btc(self):
        model = FeeModel()
        fee = model.withdrawal_fee("binance", "BTC")
        assert fee == Decimal("1.39")

    def test_withdrawal_fee_bybit_btc_expensive(self):
        model = FeeModel()
        fee = model.withdrawal_fee("bybit", "BTC")
        assert fee == Decimal("12.40")

    def test_withdrawal_fee_bitget_usdt_free(self):
        model = FeeModel()
        fee = model.withdrawal_fee("bitget", "USDT")
        assert fee == Decimal("0.00")

    def test_withdrawal_fee_unknown_coin_uses_default(self):
        model = FeeModel()
        fee = model.withdrawal_fee("binance", "UNKNOWN_COIN")
        assert fee == Decimal("0.40")  # DEFAULT for binance

    def test_withdrawal_fee_unknown_exchange(self):
        model = FeeModel()
        fee = model.withdrawal_fee("unknown_ex", "BTC")
        assert fee == Decimal("1.00")  # conservative default

    def test_withdrawal_fee_strips_prefix(self):
        model = FeeModel()
        fee = model.withdrawal_fee("paper_binance", "XRP")
        assert fee == Decimal("0.40")

    def test_network_cost_cross_exchange(self):
        model = FeeModel()
        cost = model.network_cost("binance", "bybit", "XRP")
        assert cost == Decimal("0.40")  # binance withdrawal XRP

    def test_network_cost_same_exchange_zero(self):
        model = FeeModel()
        cost = model.network_cost("binance", "binance", "XRP")
        assert cost == Decimal("0")

    def test_network_cost_futures_spot_internal_zero(self):
        model = FeeModel()
        cost = model.network_cost("binance", "binance_futures", "USDT")
        assert cost == Decimal("0")  # internal transfer

    def test_network_cost_okx_futures_spot_internal_zero(self):
        model = FeeModel()
        cost = model.network_cost("okx", "okx_futures", "USDT")
        assert cost == Decimal("0")

    def test_network_cost_bitget_futures_spot_internal_zero(self):
        model = FeeModel()
        cost = model.network_cost("bitget", "bitget_futures", "USDT")
        assert cost == Decimal("0")

    def test_network_cost_korean_to_global(self):
        model = FeeModel()
        cost = model.network_cost("upbit", "binance", "XRP")
        assert cost == Decimal("0.60")  # upbit XRP withdrawal

    def test_round_trip_fee_rate(self):
        model = FeeModel()
        rate = model.round_trip_fee_rate("binance", "bybit")
        assert rate == Decimal("0.0020")  # 0.10% + 0.10%

    def test_round_trip_fee_rate_korean(self):
        model = FeeModel()
        rate = model.round_trip_fee_rate("upbit", "binance")
        assert rate == Decimal("0.00239")  # 0.139% + 0.10%

    def test_withdrawal_fees_data_completeness(self):
        """Ensure all exchanges in DEFAULT_FEES have withdrawal fee entries."""
        from src.friction.fee_model import DEFAULT_FEES
        for exchange in DEFAULT_FEES:
            assert exchange in WITHDRAWAL_FEES_USD, (
                f"{exchange} missing from WITHDRAWAL_FEES_USD"
            )


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

    def test_custom_withdrawal_fees(self):
        custom_wf = {"my_ex": {"BTC": Decimal("5.00"), "DEFAULT": Decimal("2.00")}}
        model = FeeModel(custom_withdrawal_fees=custom_wf)
        assert model.withdrawal_fee("my_ex", "BTC") == Decimal("5.00")
        assert model.withdrawal_fee("my_ex", "ETH") == Decimal("2.00")
