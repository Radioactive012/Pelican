from starlette.testclient import TestClient

from server import app


def test_health_endpoint_exposes_pinned_versions():
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["mem0"] == "2.2.0"
    assert body["mcp"] == "2.2.0"
