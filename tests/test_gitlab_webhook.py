from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.queue_worker import queue_stats


def _valid_payload() -> dict:
    return {
        "project_id": "12345",
        "ref": "main",
        "sha": "abc123def456",
        "pipeline": {
            "id": 9876,
            "status": "success",
            "duration": 12.5,
        },
        "jobs": [
            {
                "id": "job-1",
                "name": "unit-test",
                "stage": "test",
                "status": "success",
                "duration_ms": 3500,
            }
        ],
    }


def test_gitlab_webhook_rejects_invalid_token(monkeypatch):
    monkeypatch.setattr(settings, "gitlab_webhook_secret", "test-token")
    monkeypatch.setattr(settings, "app_api_key", "")

    with TestClient(app) as client:
        response = client.post(
            "/webhooks/gitlab-ci",
            json=_valid_payload(),
            headers={
                "X-Role": "operator",
                "X-Gitlab-Token": "bad-token",
                "X-Gitlab-Event-UUID": "gitlab-delivery-invalid-token",
            },
        )

    assert response.status_code == 401


def test_gitlab_webhook_accepts_valid_token(monkeypatch):
    monkeypatch.setattr(settings, "gitlab_webhook_secret", "test-token")
    monkeypatch.setattr(settings, "app_api_key", "")

    with TestClient(app) as client:
        response = client.post(
            "/webhooks/gitlab-ci",
            json=_valid_payload(),
            headers={
                "X-Role": "operator",
                "X-Gitlab-Token": "test-token",
                "X-Gitlab-Event-UUID": "gitlab-delivery-valid-token",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["queued"] is True
    assert body["duplicate"] is False


def test_gitlab_webhook_skips_duplicate_delivery(monkeypatch):
    monkeypatch.setattr(settings, "gitlab_webhook_secret", "test-token")
    monkeypatch.setattr(settings, "app_api_key", "")
    before_duplicates = queue_stats.get("duplicate_deliveries", 0)

    headers = {
        "X-Role": "operator",
        "X-Gitlab-Token": "test-token",
        "X-Gitlab-Event-UUID": "gitlab-delivery-duplicate-check",
    }

    with TestClient(app) as client:
        first = client.post("/webhooks/gitlab-ci", json=_valid_payload(), headers=headers)
        second = client.post("/webhooks/gitlab-ci", json=_valid_payload(), headers=headers)

    assert first.status_code == 200
    assert first.json()["queued"] is True

    assert second.status_code == 200
    second_body = second.json()
    assert second_body["queued"] is False
    assert second_body["duplicate"] is True
    assert queue_stats.get("duplicate_deliveries", 0) >= before_duplicates + 1


def test_gitlab_webhook_rejects_missing_pipeline_identifier(monkeypatch):
    monkeypatch.setattr(settings, "gitlab_webhook_secret", "")
    monkeypatch.setattr(settings, "app_api_key", "")

    payload = {
        "project_id": "12345",
        "jobs": [],
    }

    with TestClient(app) as client:
        response = client.post(
            "/webhooks/gitlab-ci",
            json=payload,
            headers={"X-Role": "operator", "X-Gitlab-Event-UUID": "gitlab-delivery-missing-pipeline-id"},
        )

    assert response.status_code == 422