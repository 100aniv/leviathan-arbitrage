"""US-138/139: Infrastructure config file validity tests.

Tests cover:
- test_alertmanager_yml_is_valid_yaml: alertmanager.yml parses without error
- test_alertmanager_yml_has_telegram_receiver: receiver named 'telegram' exists
- test_alertmanager_yml_uses_workflow_token: uses WORKFLOW_TELEGRAM_BOT_TOKEN (not TELEGRAM_BOT_TOKEN)
- test_grafana_datasource_yml_is_valid_yaml: datasource.yml parses without error
- test_grafana_datasource_yml_has_prometheus: Prometheus datasource is present
- test_grafana_datasource_yml_has_timescaledb: TimescaleDB datasource is present
- test_grafana_datasource_yml_has_loki: Loki datasource is present
- test_docker_compose_config_is_valid: docker compose config --quiet exits 0
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# File paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ALERTMANAGER_YML = PROJECT_ROOT / "infra" / "prometheus" / "alertmanager.yml"
GRAFANA_DATASOURCE_YML = (
    PROJECT_ROOT / "infra" / "grafana" / "provisioning" / "datasources" / "datasource.yml"
)
DOCKER_COMPOSE_YML = PROJECT_ROOT / "docker-compose.yml"


# ---------------------------------------------------------------------------
# Alertmanager tests
# ---------------------------------------------------------------------------


def test_alertmanager_yml_is_valid_yaml():
    """alertmanager.yml must be parseable YAML without syntax errors."""
    assert ALERTMANAGER_YML.exists(), f"alertmanager.yml not found: {ALERTMANAGER_YML}"
    content = ALERTMANAGER_YML.read_text(encoding="utf-8")
    # Replace env var placeholders before parsing so yaml.safe_load does not error
    content_for_parse = content.replace(
        "${WORKFLOW_TELEGRAM_BOT_TOKEN}", "PLACEHOLDER_TOKEN"
    ).replace("${WORKFLOW_TELEGRAM_CHAT_ID}", "123456789")
    parsed = yaml.safe_load(content_for_parse)
    assert parsed is not None
    assert isinstance(parsed, dict)


def test_alertmanager_yml_has_telegram_receiver():
    """alertmanager.yml must define telegram receivers (Phase S20: 3-bot split)."""
    assert ALERTMANAGER_YML.exists()
    content = ALERTMANAGER_YML.read_text(encoding="utf-8")
    parsed = yaml.safe_load(
        content.replace("${WORKFLOW_TELEGRAM_BOT_TOKEN}", "tok")
        .replace("${WORKFLOW_TELEGRAM_CHAT_ID}", "123")
        .replace("${INFRA_TELEGRAM_BOT_TOKEN}", "tok")
        .replace("${INFRA_TELEGRAM_CHAT_ID}", "123")
        .replace("${TRADE_TELEGRAM_BOT_TOKEN}", "tok")
        .replace("${TRADE_TELEGRAM_CHAT_ID}", "123")
        .replace("${DEV_TELEGRAM_BOT_TOKEN}", "tok")
        .replace("${DEV_TELEGRAM_CHAT_ID}", "123")
    )
    receiver_names = [r["name"] for r in parsed.get("receivers", [])]
    # Phase S20: 3-bot receivers (telegram-infra, telegram-trade, telegram-dev)
    assert any("telegram" in name for name in receiver_names), (
        f"Expected telegram receiver(s) in alertmanager.yml, got: {receiver_names}"
    )


def test_alertmanager_yml_uses_workflow_token():
    """alertmanager.yml must use per-bot tokens (not bare TELEGRAM_BOT_TOKEN)."""
    assert ALERTMANAGER_YML.exists()
    content = ALERTMANAGER_YML.read_text(encoding="utf-8")
    # Phase S20: Should use INFRA/TRADE/DEV tokens
    has_bot_tokens = (
        "INFRA_TELEGRAM_BOT_TOKEN" in content
        or "TRADE_TELEGRAM_BOT_TOKEN" in content
        or "DEV_TELEGRAM_BOT_TOKEN" in content
        or "WORKFLOW_TELEGRAM_BOT_TOKEN" in content
    )
    assert has_bot_tokens, (
        "alertmanager.yml should use per-bot tokens (INFRA/TRADE/DEV_TELEGRAM_BOT_TOKEN)"
    )
    # Must NOT use the bare trading-alert token directly
    assert "bot_token: '${TELEGRAM_BOT_TOKEN}'" not in content, (
        "alertmanager.yml must not use bare TELEGRAM_BOT_TOKEN (use per-bot variant)"
    )


# ---------------------------------------------------------------------------
# Grafana datasource tests
# ---------------------------------------------------------------------------


def test_grafana_datasource_yml_is_valid_yaml():
    """datasource.yml must be parseable YAML without syntax errors."""
    assert GRAFANA_DATASOURCE_YML.exists(), (
        f"datasource.yml not found: {GRAFANA_DATASOURCE_YML}"
    )
    parsed = yaml.safe_load(GRAFANA_DATASOURCE_YML.read_text(encoding="utf-8"))
    assert parsed is not None
    assert isinstance(parsed, dict)


def _datasource_names() -> list[str]:
    parsed = yaml.safe_load(GRAFANA_DATASOURCE_YML.read_text(encoding="utf-8"))
    return [ds.get("name", "") for ds in parsed.get("datasources", [])]


def test_grafana_datasource_yml_has_prometheus():
    """Prometheus datasource must be configured in datasource.yml."""
    assert GRAFANA_DATASOURCE_YML.exists()
    names = _datasource_names()
    assert "Prometheus" in names, f"Prometheus datasource missing. Found: {names}"


def test_grafana_datasource_yml_has_timescaledb():
    """TimescaleDB datasource must be configured in datasource.yml."""
    assert GRAFANA_DATASOURCE_YML.exists()
    names = _datasource_names()
    assert "TimescaleDB" in names, f"TimescaleDB datasource missing. Found: {names}"


def test_grafana_datasource_yml_has_loki():
    """Loki datasource must be configured in datasource.yml."""
    assert GRAFANA_DATASOURCE_YML.exists()
    names = _datasource_names()
    assert "Loki" in names, f"Loki datasource missing. Found: {names}"


# ---------------------------------------------------------------------------
# docker-compose tests
# ---------------------------------------------------------------------------


def test_docker_compose_config_is_valid():
    """docker compose config --quiet must exit 0 (US-139: compose file validity)."""
    result = subprocess.run(
        ["docker", "compose", "config", "--quiet"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"docker compose config failed (returncode={result.returncode}):\n"
        f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )
