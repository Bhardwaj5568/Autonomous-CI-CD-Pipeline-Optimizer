from fastapi.testclient import TestClient

from app.main import app


def test_post_endpoints_expose_request_body_in_openapi():
    expected_request_body_paths = {
        "/ingest/events",
        "/ingest/source-event",
        "/webhooks/github-actions",
        "/webhooks/gitlab-ci",
        "/webhooks/jenkins",
        "/feedback/run/{run_id}",
    }

    with TestClient(app) as client:
        spec = client.get("/openapi.json").json()

    for path in expected_request_body_paths:
        assert "requestBody" in spec["paths"][path]["post"], f"Missing requestBody in OpenAPI for {path}"


def test_score_run_post_has_no_request_body_in_openapi():
    with TestClient(app) as client:
        spec = client.get("/openapi.json").json()

    assert "requestBody" not in spec["paths"]["/score/run/{run_id}"]["post"]