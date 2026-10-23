import pytest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.database import Base, get_db
from backend.app.main import app
from backend.app.models import Product
from sqlalchemy.exc import SQLAlchemyError


# Use a dedicated in-memory SQLite database for tests.
TEST_DATABASE_URL = "sqlite://"

test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

TestingSessionLocal = sessionmaker(
    bind=test_engine,
    autoflush=False,
    autocommit=False,
)


def override_get_db():
    db = TestingSessionLocal()

    try:
        yield db
    finally:
        db.close()


# Replace the application's database dependency with the test database.
app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def reset_test_database():
    # Start every test with a clean schema.
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    # Seed predictable test data.
    with TestingSessionLocal() as db:
        db.add_all(
            [
                Product(id=1, name="Laptop", price=2500.00),
                Product(id=2, name="Keyboard", price=150.00),
                Product(id=3, name="Mouse", price=75.00),
            ]
        )
        db.commit()

    yield


client = TestClient(app)


def test_health_live():
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_health_ready():
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_get_products():
    response = client.get("/products")

    assert response.status_code == 200

    products = response.json()

    assert len(products) == 3
    assert products[0]["name"] == "Laptop"
    assert products[1]["name"] == "Keyboard"
    assert products[2]["name"] == "Mouse"


def test_get_existing_product():
    response = client.get("/products/1")

    assert response.status_code == 200
    assert response.json() == {
        "id": 1,
        "name": "Laptop",
        "price": 2500.0,
        "stock_quantity": 0,
    }


def test_get_nonexistent_product():
    response = client.get("/products/999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Product not found"}


def test_health_ready_when_database_fails():
    class FailingSession:
        def execute(self, statement):
            raise SQLAlchemyError("Simulated database failure")

        def rollback(self):
            pass

    def override_failing_get_db():
        yield FailingSession()

    app.dependency_overrides[get_db] = override_failing_get_db

    try:
        response = client.get("/health/ready")
    finally:
        app.dependency_overrides[get_db] = override_get_db

    assert response.status_code == 503
    assert response.json() == {"detail": "Database is unavailable"}


def test_create_product():
    response = client.post(
        "/products",
        json={
            "name": "Monitor",
            "price": 800,
            "stock_quantity": 10,
        },
    )

    assert response.status_code == 201

    data = response.json()
    assert data["name"] == "Monitor"
    assert data["price"] == 800
    assert data["stock_quantity"] == 10
    assert "id" in data


def test_create_product_rejects_negative_price():
    response = client.post(
        "/products",
        json={
            "name": "Invalid Product",
            "price": -10,
            "stock_quantity": 5,
        },
    )

    assert response.status_code == 422


def test_create_product_rejects_negative_stock():
    response = client.post(
        "/products",
        json={
            "name": "Invalid Product",
            "price": 100,
            "stock_quantity": -5,
        },
    )

    assert response.status_code == 422


def test_create_product_rejects_blank_name():
    response = client.post(
        "/products",
        json={
            "name": "   ",
            "price": 100,
            "stock_quantity": 5,
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Product name cannot be empty"


def test_update_product_stock():
    response = client.patch(
        "/products/3/stock",
        json={"stock_quantity": 25},
    )

    assert response.status_code == 200

    data = response.json()
    assert data["id"] == 3
    assert data["name"] == "Mouse"
    assert data["stock_quantity"] == 25


def test_update_stock_for_nonexistent_product():
    response = client.patch(
        "/products/999/stock",
        json={"stock_quantity": 25},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Product not found"}
