"""LEVIATHAN One-Click CLI.
US-294: 간편 엔진 제어 CLI.
Usage: python -m src.cli.leviathan_cli [command]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="leviathan",
        description="LEVIATHAN Engine CLI — 원클릭 제어 도구",
    )
    sub = parser.add_subparsers(dest="command", help="사용 가능한 명령")

    sub.add_parser("status", help="엔진 상태 확인")
    sub.add_parser("start", help="인프라 + 엔진 시작")
    sub.add_parser("stop", help="엔진 중지")
    sub.add_parser("shadow", help="Shadow 모드 10분 실행")
    sub.add_parser("health", help="헬스 체크 (Redis/DB/API)")
    sub.add_parser("test", help="테스트 실행")
    sub.add_parser("logs", help="최근 로그 조회")
    sub.add_parser("env-check", help=".env 변수 검증")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    handlers = {
        "status": cmd_status,
        "start": cmd_start,
        "stop": cmd_stop,
        "shadow": cmd_shadow,
        "health": cmd_health,
        "test": cmd_test,
        "logs": cmd_logs,
        "env-check": cmd_env_check,
    }
    handler = handlers.get(args.command)
    if handler:
        handler()


def cmd_status() -> None:
    print("LEVIATHAN Engine Status")
    print("=" * 40)
    result = subprocess.run(
        ["docker", "compose", "ps", "--format", "table {{.Name}}\t{{.Status}}\t{{.Ports}}"],
        capture_output=True,
        text=True,
        cwd=_project_root(),
    )
    if result.returncode == 0:
        print(result.stdout)
    else:
        print("Docker 컨테이너 정보 없음")

    env_file = os.path.join(_engine_root(), ".env")
    if os.path.exists(env_file):
        print(".env 파일 존재")
    else:
        print(".env 파일 없음")


def cmd_start() -> None:
    print("LEVIATHAN 시작...")
    root = _project_root()
    print("1. 인프라 시작 (TimescaleDB + Redis)...")
    subprocess.run(["docker", "compose", "up", "-d", "timescaledb", "redis"], cwd=root)
    print("2. 엔진 시작...")
    os.chdir(_engine_root())
    os.execvp(sys.executable, [sys.executable, "-m", "src.main"])


def cmd_stop() -> None:
    print("LEVIATHAN 중지...")
    subprocess.run(["docker", "compose", "stop", "engine"], cwd=_project_root())
    print("완료")


def cmd_shadow() -> None:
    print("Shadow 모드 10분 실행...")
    os.chdir(_engine_root())
    env = os.environ.copy()
    env["DATA_MODE"] = "shadow"
    subprocess.run([sys.executable, "-m", "src.main"], timeout=600, env=env)


def cmd_health() -> None:
    sys.path.insert(0, _engine_root())
    from src.infra.startup_checker import StartupChecker  # noqa: PLC0415
    checker = StartupChecker()
    asyncio.run(checker.check_all())
    print(checker.format_checklist().replace("<b>", "").replace("</b>", ""))


def cmd_test() -> None:
    print("테스트 실행...")
    os.chdir(_engine_root())
    subprocess.run([sys.executable, "-m", "pytest", "tests/", "-x", "--tb=short"])


def cmd_logs() -> None:
    log_file = os.path.join(_engine_root(), "logs", "engine.log")
    if os.path.exists(log_file):
        subprocess.run(["tail", "-50", log_file])
    else:
        print("로그 파일 없음 (logs/engine.log)")


def cmd_env_check() -> None:
    print(".env 변수 검증")
    env_file = os.path.join(_engine_root(), ".env")
    required = ["DATABASE_URL", "REDIS_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]

    try:
        from dotenv import load_dotenv  # noqa: PLC0415
        load_dotenv(env_file)
    except ImportError:
        print("python-dotenv 미설치 — os.environ만 사용")

    for var in required:
        val = os.getenv(var, "")
        status = "OK" if val else "MISSING"
        display = val[:20] + "..." if len(val) > 20 else val or "(미설정)"
        print(f"  [{status}] {var} = {display}")


def _project_root() -> str:
    # engine/src/cli/leviathan_cli.py -> go up 4 levels
    return os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )


def _engine_root() -> str:
    # engine/src/cli/leviathan_cli.py -> go up 3 levels
    return os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )


if __name__ == "__main__":
    main()
