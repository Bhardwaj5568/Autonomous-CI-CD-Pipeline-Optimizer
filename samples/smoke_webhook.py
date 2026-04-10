import json
import sys
import time

from fastapi.testclient import TestClient

sys.path.insert(0, r"f:/CI-Cd/autonomous-cicd-optimizer")
from app.main import app  # noqa: E402

with open(r"f:/CI-Cd/autonomous-cicd-optimizer/samples/source_event_gitlab.json", "r", encoding="utf-8") as f:
    payload = json.load(f)

with TestClient(app) as client:
    resp = client.post(
        "/webhooks/gitlab-ci",
        json=payload["payload"],
        headers={"X-Role": "operator"},
    )

    ok = False
    status = {}
    for _ in range(40):
        status = client.get("/queue/status", headers={"X-Role": "viewer"}).json()
        if status["processed"] >= 1:
            ok = True
            break
        time.sleep(0.05)

    print("webhook", resp.status_code, resp.json())
    print("queue", status)
    print("processed_ok", ok)
