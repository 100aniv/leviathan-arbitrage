"""LEVIATHAN Telegram Chart Generator.
US-291-i: matplotlib 기반 차트 생성 (PNG bytes).
matplotlib 미설치 시 None 반환 (graceful degradation).
"""
from __future__ import annotations

try:
    import matplotlib
    matplotlib.use("Agg")  # Non-interactive backend
    import matplotlib.pyplot as plt
    _MPL_AVAILABLE = True
except ImportError:
    _MPL_AVAILABLE = False


async def generate_chart(chart_type: str, data: dict | None) -> bytes | None:
    """Generate chart PNG bytes. Returns None if matplotlib unavailable or no data."""
    if not _MPL_AVAILABLE or not data:
        return None

    generators = {
        "pnl": _generate_pnl_chart,
        "strategy": _generate_strategy_chart,
        "mdd": _generate_mdd_chart,
    }
    gen = generators.get(chart_type)
    if gen is None:
        return None

    import asyncio
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, gen, data)


def _generate_pnl_chart(data: dict) -> bytes | None:
    """PnL 곡선 차트."""
    pnl_history = data.get("pnl_history", [])
    if not pnl_history:
        return None

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(range(len(pnl_history)), pnl_history, color="#2ecc71", linewidth=2)
    ax.fill_between(range(len(pnl_history)), pnl_history, alpha=0.3, color="#2ecc71")
    ax.set_title("PnL 추이", fontsize=14, fontweight="bold")
    ax.set_xlabel("거래 수")
    ax.set_ylabel("PnL (USD)")
    ax.grid(True, alpha=0.3)
    ax.axhline(y=0, color="gray", linestyle="--", alpha=0.5)

    return _fig_to_bytes(fig)


def _generate_strategy_chart(data: dict) -> bytes | None:
    """전략별 PnL 파이차트."""
    by_strategy = data.get("by_strategy", [])
    if not by_strategy:
        return None

    labels = [s.get("strategy_id", "?") for s in by_strategy]
    values = [abs(s.get("pnl", 0.0)) for s in by_strategy]
    colors_map = ["#2ecc71" if s.get("pnl", 0) >= 0 else "#e74c3c" for s in by_strategy]

    if sum(values) == 0:
        return None

    fig, ax = plt.subplots(figsize=(8, 8))
    ax.pie(values, labels=labels, colors=colors_map, autopct="%1.1f%%", startangle=90)
    ax.set_title("전략별 PnL 분포", fontsize=14, fontweight="bold")

    return _fig_to_bytes(fig)


def _generate_mdd_chart(data: dict) -> bytes | None:
    """MDD 추이 차트."""
    mdd_history = data.get("mdd_history", [])
    if not mdd_history:
        return None

    fig, ax = plt.subplots(figsize=(10, 5))
    mdd_pct = [v * 100 for v in mdd_history]
    ax.fill_between(range(len(mdd_pct)), mdd_pct, color="#e74c3c", alpha=0.4)
    ax.plot(range(len(mdd_pct)), mdd_pct, color="#e74c3c", linewidth=2)
    ax.set_title("Maximum Drawdown 추이", fontsize=14, fontweight="bold")
    ax.set_xlabel("시간")
    ax.set_ylabel("MDD (%)")
    ax.grid(True, alpha=0.3)
    ax.axhline(y=5, color="orange", linestyle="--", alpha=0.7, label="경고선 5%")
    ax.legend()
    ax.invert_yaxis()  # MDD is negative concept

    return _fig_to_bytes(fig)


def _fig_to_bytes(fig) -> bytes:
    """Convert matplotlib figure to PNG bytes."""
    import io
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()
