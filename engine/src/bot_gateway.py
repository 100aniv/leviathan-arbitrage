"""LEVIATHAN Bot Gateway — DevBot + InfraBot + MonitorDaemon 독립 실행.

Phase S20-C: 엔진과 무관하게 항상 실행되는 텔레그램 봇 게이트웨이.
- DevBot: 원격 개발 제어 (pytest, git, docker, /shadow)
- InfraBot: 인프라 모니터링 + 엔진 제어 (health, docker, resources, /engine)
- MonitorDaemon: 5분 주기 인프라 헬스체크 (InfraBot 알림 연동)

Usage:
    python -m src.bot_gateway
"""
from __future__ import annotations

import asyncio
import os
import signal
import sys

from dotenv import load_dotenv
import structlog

logger = structlog.get_logger(__name__)


async def main() -> None:
    """Start DevBot + InfraBot in standalone mode."""
    load_dotenv()

    bots: list = []

    # DevBot
    try:
        from src.infra.telegram_dev_bot import DevTelegramBot
        dev_bot = DevTelegramBot()
        if dev_bot.enabled:
            bots.append(dev_bot)
            logger.info("bot_gateway_init", bot="DevBot", status="enabled")
        else:
            logger.info("bot_gateway_init", bot="DevBot", status="disabled")
    except Exception as e:
        logger.warning("bot_gateway_init_failed", bot="DevBot", error=str(e))

    # InfraBot
    try:
        from src.infra.telegram_infra_bot import InfraTelegramBot
        infra_bot = InfraTelegramBot()
        if infra_bot.enabled:
            bots.append(infra_bot)
            logger.info("bot_gateway_init", bot="InfraBot", status="enabled")
        else:
            logger.info("bot_gateway_init", bot="InfraBot", status="disabled")
    except Exception as e:
        logger.warning("bot_gateway_init_failed", bot="InfraBot", error=str(e))

    if not bots:
        logger.error("bot_gateway_no_bots", msg="No bots enabled — exiting")
        sys.exit(1)

    # MonitorDaemon integration with InfraBot
    infra_bot = next((b for b in bots if b.bot_name == "LEVIATHAN-INFRA"), None)
    monitor_daemon = None
    if infra_bot is not None:
        try:
            from src.infra.monitor_daemon import MonitorDaemon
            from src.core.config_loader import get_config as _gc
            interval = int(_gc("monitoring.monitor_interval_sec", default=300))
            monitor_daemon = MonitorDaemon(infra_bot=infra_bot, interval_sec=interval)
            infra_bot.set_monitor_daemon(monitor_daemon)
            logger.info("bot_gateway_monitor_daemon", status="initialized", interval=interval)
        except Exception as e:
            logger.warning("bot_gateway_monitor_daemon_failed", error=str(e))

    # Graceful shutdown
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    # Start poll loops
    tasks = []
    for bot in bots:
        tasks.append(asyncio.create_task(bot.poll_loop(), name=bot.bot_name))

    # Start MonitorDaemon loop
    if monitor_daemon is not None:
        tasks.append(asyncio.create_task(monitor_daemon.run(), name="monitor_daemon"))
        logger.info("bot_gateway_monitor_daemon", status="started")

    # Startup announcement
    for bot in bots:
        try:
            await bot.send_message(f"🟢 {bot.bot_name} 온라인 (독립 모드)")
        except Exception:
            pass

    # StartupChecker for InfraBot
    if infra_bot is not None:
        try:
            from src.infra.startup_checker import StartupChecker
            checker = StartupChecker()
            infra_bot.set_startup_checker(checker)
            await checker.check_all()
            await infra_bot.send_message(checker.format_checklist())
            logger.info("bot_gateway_startup_check", all_passed=checker.all_passed)
        except Exception as exc:
            logger.warning("bot_gateway_startup_check_failed", error=str(exc))

    logger.info("bot_gateway_running", bots=[b.bot_name for b in bots])
    await stop_event.wait()

    # Shutdown
    logger.info("bot_gateway_stopping")
    for bot in bots:
        bot.stop()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    for bot in bots:
        try:
            await bot.send_message(f"🔴 {bot.bot_name} 오프라인")
        except Exception:
            pass
        await bot.close()
    logger.info("bot_gateway_stopped")


if __name__ == "__main__":
    asyncio.run(main())
