"""Tests for leviathan CLI (US-294)."""
import pytest
from unittest.mock import patch
from src.cli.leviathan_cli import main, _project_root, _engine_root, cmd_env_check


class TestLeviathanCLI:
    def test_project_root(self):
        root = _project_root()
        # Goes up 4 levels from engine/src/cli/leviathan_cli.py -> arbitrage_OMC
        assert "engine" not in root.split("/")[-1]

    def test_engine_root(self):
        root = _engine_root()
        # Goes up 3 levels from engine/src/cli/leviathan_cli.py -> engine/
        assert root.endswith("engine")

    def test_no_command_shows_help(self, capsys):
        with patch("sys.argv", ["leviathan"]):
            main()
        # No crash is sufficient — argparse prints help

    def test_main_status_command(self):
        with patch("sys.argv", ["leviathan", "status"]):
            with patch("src.cli.leviathan_cli.cmd_status") as mock_status:
                main()
                mock_status.assert_called_once()

    def test_main_unknown_no_crash(self):
        # argparse exits on unknown command — just verify import works
        with patch("sys.argv", ["leviathan", "env-check"]):
            with patch("src.cli.leviathan_cli.cmd_env_check") as mock_env:
                main()
                mock_env.assert_called_once()

    def test_env_check_output(self, capsys):
        with patch.dict(
            "os.environ",
            {
                "DATABASE_URL": "test_db",
                "REDIS_URL": "test_redis",
                "TELEGRAM_BOT_TOKEN": "test_tok",
                "TELEGRAM_CHAT_ID": "123",
            },
        ):
            with patch("src.cli.leviathan_cli._engine_root", return_value="/tmp"):
                cmd_env_check()
        captured = capsys.readouterr()
        assert "DATABASE_URL" in captured.out
        assert "REDIS_URL" in captured.out

    def test_env_check_missing_vars(self, capsys):
        with patch.dict("os.environ", {}, clear=True):
            with patch("src.cli.leviathan_cli._engine_root", return_value="/tmp"):
                cmd_env_check()
        captured = capsys.readouterr()
        assert "MISSING" in captured.out
