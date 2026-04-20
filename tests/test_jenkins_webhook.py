import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.queue_worker import queue_stats


def _sign_payload(secret: str, payload_bytes: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _valid_payload() -> dict:
    return {
        "run_id": "jk-run-security-test",
        "job_name": "example-job",
        "branch": "main",
        "commit_sha": "abc123",
        "build": {
            "number": 42,
            "result": "SUCCESS",
            "duration": 1000,
            "log_excerpt_hash": "sha256:jenkins-log-hash",
        },
        "stages": [
            {
                "id": "stage-1",
                "name": "unit-tests",
                "status": "SUCCESS",
                "duration_ms": 500,
            }
        ],
    }


def test_jenkins_webhook_rejects_invalid_signature(monkeypatch):
    monkeypatch.setattr(settings, "jenkins_webhook_secret", "test-secret")
    monkeypatch.setattr(settings, "app_api_key", "")

    payload_bytes = json.dumps(_valid_payload()).encode("utf-8")

    with TestClient(app) as client:
        response = client.post(
            "/webhooks/jenkins",
            data=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Role": "operator",
                "X-Jenkins-Signature": "sha256=bad-signature",
                "X-Jenkins-Delivery": "jenkins-delivery-invalid-signature",
            },
        )

    assert response.status_code == 401


def test_jenkins_webhook_accepts_valid_signature(monkeypatch):
    monkeypatch.setattr(settings, "jenkins_webhook_secret", "test-secret")
    monkeypatch.setattr(settings, "app_api_key", "")

    payload_bytes = json.dumps(_valid_payload()).encode("utf-8")
    signature = _sign_payload("test-secret", payload_bytes)

    with TestClient(app) as client:
        response = client.post(
            "/webhooks/jenkins",
            data=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Role": "operator",
                "X-Jenkins-Signature": signature,
                "X-Jenkins-Delivery": "jenkins-delivery-valid-signature",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["queued"] is True
    assert body["duplicate"] is False


def test_jenkins_webhook_skips_duplicate_delivery(monkeypatch):
    monkeypatch.setattr(settings, "jenkins_webhook_secret", "test-secret")
    monkeypatch.setattr(settings, "app_api_key", "")

    payload_bytes = json.dumps(_valid_payload()).encode("utf-8")
    signature = _sign_payload("test-secret", payload_bytes)
    before_duplicates = queue_stats.get("duplicate_deliveries", 0)

    headers = {
        "Content-Type": "application/json",
        "X-Role": "operator",
        "X-Jenkins-Signature": signature,
        "X-Jenkins-Delivery": "jenkins-delivery-duplicate-check",
    }

    with TestClient(app) as client:
        first = client.post("/webhooks/jenkins", data=payload_bytes, headers=headers)
        second = client.post("/webhooks/jenkins", data=payload_bytes, headers=headers)

    assert first.status_code == 200
    assert first.json()["queued"] is True

    assert second.status_code == 200
    second_body = second.json()
    assert second_body["queued"] is False
    assert second_body["duplicate"] is True
    assert queue_stats.get("duplicate_deliveries", 0) >= before_duplicates + 1


def test_jenkins_webhook_rejects_missing_run_identifier(monkeypatch):
    monkeypatch.setattr(settings, "jenkins_webhook_secret", "")
    monkeypatch.setattr(settings, "app_api_key", "")

    payload = {
        "job_name": "example-job",
        "build": {"result": "SUCCESS"},
        "stages": [],
    }

    with TestClient(app) as client:
        response = client.post(
            "/webhooks/jenkins",
            json=payload,
            headers={"X-Role": "operator", "X-Jenkins-Delivery": "jenkins-delivery-missing-run-id"},
        )

    assert response.status_code == 422