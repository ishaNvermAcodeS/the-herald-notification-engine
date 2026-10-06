from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "The Herald" in data["app"]


def test_root():
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "Welcome" in data["message"]
