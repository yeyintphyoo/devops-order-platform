from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)


def test_health_live():
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json()["status"] == "alive"


def test_health_ready():
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_get_products():
    response = client.get("/products")

    assert response.status_code == 200

    products = response.json()

    assert len(products) == 3
    assert products[0]["name"] == "Laptop"


def test_get_existing_product():
    response = client.get("/products/1")

    assert response.status_code == 200
    assert response.json()["name"] == "Laptop"


def test_get_nonexistent_product():
    response = client.get("/products/999")

    assert response.status_code == 404
