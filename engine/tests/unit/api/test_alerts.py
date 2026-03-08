"""Tests for GET /api/v1/alerts endpoint."""
import pytest
from fastapi.testclient import TestClient

from src.api.auth import create_token
from src.api.server import EngineContext, create_app


@pytest.fixture
def context():
    return EngineContext()


@pytest.fixture
def client(context):
    app = create_app(context)
    return TestClient(app)


@pytest.fixture
def auth_headers():
    return {"Authorization": f"Bearer {create_token('test')}"}


class TestAlertsBasicResponse:
    def test_alerts_returns_200(self, client, auth_headers):
        assert client.get("/api/v1/alerts", headers=auth_headers).status_code == 200

    def test_alerts_returns_list(self, client, auth_headers):
        assert isinstance(client.get("/api/v1/alerts", headers=auth_headers).json(), list)

    def test_alerts_empty_by_default(self, client, auth_headers):
        assert client.get("/api/v1/alerts", headers=auth_headers).json() == []

    def test_alerts_requires_auth(self, client):
        assert client.get("/api/v1/alerts").status_code == 401

    def test_alerts_returns_context_alert_history(self, client, context, auth_headers):
        context.alert_history = [
            {"id": "a1", "type": "kill_switch", "severity": "critical", "message": "halt", "timestamp": "2024-01-01T00:00:00"}
        ]
        data = client.get("/api/v1/alerts", headers=auth_headers).json()
        assert len(data) == 1
        assert data[0]["id"] == "a1"


class TestAlertsSeverityFilter:
    def test_severity_filter_returns_matching_alerts(self, client, context, auth_headers):
        context.alert_history = [
            {"id": "a1", "severity": "critical", "timestamp": "2024-01-01T00:00:00"},
            {"id": "a2", "severity": "warning", "timestamp": "2024-01-01T00:01:00"},
        ]
        data = client.get("/api/v1/alerts?severity=critical", headers=auth_headers).json()
        assert len(data) == 1
        assert data[0]["id"] == "a1"

    def test_severity_filter_excludes_non_matching_alerts(self, client, context, auth_headers):
        context.alert_history = [
            {"id": "a1", "severity": "critical", "timestamp": "2024-01-01T00:00:00"},
            {"id": "a2", "severity": "warning", "timestamp": "2024-01-01T00:01:00"},
            {"id": "a3", "severity": "info", "timestamp": "2024-01-01T00:02:00"},
        ]
        data = client.get("/api/v1/alerts?severity=warning", headers=auth_headers).json()
        assert all(a["severity"] == "warning" for a in data)

    def test_severity_filter_returns_empty_when_no_match(self, client, context, auth_headers):
        context.alert_history = [
            {"id": "a1", "severity": "critical", "timestamp": "2024-01-01T00:00:00"},
        ]
        data = client.get("/api/v1/alerts?severity=info", headers=auth_headers).json()
        assert data == []

    def test_no_severity_filter_returns_all_alerts(self, client, context, auth_headers):
        context.alert_history = [
            {"id": "a1", "severity": "critical", "timestamp": "2024-01-01T00:00:00"},
            {"id": "a2", "severity": "warning", "timestamp": "2024-01-01T00:01:00"},
            {"id": "a3", "severity": "info", "timestamp": "2024-01-01T00:02:00"},
        ]
        data = client.get("/api/v1/alerts", headers=auth_headers).json()
        assert len(data) == 3

    def test_severity_info_filter(self, client, context, auth_headers):
        context.alert_history = [
            {"id": "a1", "severity": "critical", "timestamp": "2024-01-01T00:00:00"},
            {"id": "a2", "severity": "info", "timestamp": "2024-01-01T00:01:00"},
        ]
        data = client.get("/api/v1/alerts?severity=info", headers=auth_headers).json()
        assert len(data) == 1
        assert data[0]["severity"] == "info"
