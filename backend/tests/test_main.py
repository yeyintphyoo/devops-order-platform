import asyncio
import pytest

from backend.app import main
from backend.app import database

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.database import Base, get_db
from backend.app.main import app
from backend.app.models import Product
from sqlalchemy.exc import IntegrityError, SQLAlchemyError


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


def test_prometheus_metrics_endpoint():
    # Generate a request so the instrumentation records a metric.
    health_response = client.get("/health/live")
    assert health_response.status_code == 200

    # Retrieve the Prometheus metrics.
    response = client.get("/metrics")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "http_requests_total" in response.text
    assert "http_request_duration_highr_seconds" in response.text


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


def test_create_product_when_integrity_error_occurs():
    class FailingSession:
        rollback_called = False

        def add(self, product):
            raise IntegrityError(
                "INSERT INTO products",
                {},
                Exception("Simulated integrity conflict"),
            )

        def rollback(self):
            self.rollback_called = True

    failing_session = FailingSession()

    def override_failing_get_db():
        yield failing_session

    app.dependency_overrides[get_db] = override_failing_get_db

    try:
        response = client.post(
            "/products",
            json={
                "name": "Conflict Product",
                "price": 100.00,
                "stock_quantity": 5,
            },
        )
    finally:
        app.dependency_overrides[get_db] = override_get_db

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Could not create product due to a data conflict"
    }
    assert failing_session.rollback_called is True


def test_create_product_when_database_fails():
    class FailingSession:
        rollback_called = False

        def add(self, product):
            pass

        def commit(self):
            raise SQLAlchemyError("Simulated database failure")

        def rollback(self):
            self.rollback_called = True

    failing_session = FailingSession()

    def override_failing_get_db():
        yield failing_session

    app.dependency_overrides[get_db] = override_failing_get_db

    try:
        response = client.post(
            "/products",
            json={
                "name": "Database Failure Product",
                "price": 100.00,
                "stock_quantity": 5,
            },
        )
    finally:
        app.dependency_overrides[get_db] = override_get_db

    assert response.status_code == 503
    assert response.json() == {"detail": "Database operation failed"}
    assert failing_session.rollback_called is True


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


def test_create_product_rejects_zero_price():
    response = client.post(
        "/products",
        json={
            "name": "Invalid Product",
            "price": 0,
            "stock_quantity": 5,
        },
    )

    assert response.status_code == 422


def test_create_product_accepts_name_with_100_characters():
    name = "A" * 100

    response = client.post(
        "/products",
        json={
            "name": name,
            "price": 10,
            "stock_quantity": 0,
        },
    )

    assert response.status_code == 201
    assert response.json()["name"] == name


def test_create_product_trims_surrounding_whitespace():
    response = client.post(
        "/products",
        json={
            "name": "  Monitor  ",
            "price": 800,
            "stock_quantity": 5,
        },
    )

    assert response.status_code == 201
    assert response.json()["name"] == "Monitor"


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


def test_update_product_stock_to_zero():
    response = client.patch(
        "/products/3/stock",
        json={"stock_quantity": 0},
    )

    assert response.status_code == 200
    assert response.json()["stock_quantity"] == 0


def test_update_product_stock_rejects_negative_quantity():
    response = client.patch(
        "/products/3/stock",
        json={"stock_quantity": -1},
    )

    assert response.status_code == 422


def test_update_product_stock_when_database_fails():
    class FailingSession:
        def get(self, model, product_id):
            raise SQLAlchemyError("Simulated database failure")

        def rollback(self):
            pass

    def override_failing_get_db():
        yield FailingSession()

    app.dependency_overrides[get_db] = override_failing_get_db

    try:
        response = client.patch(
            "/products/3/stock",
            json={"stock_quantity": 25},
        )
    finally:
        app.dependency_overrides[get_db] = override_get_db

    assert response.status_code == 503
    assert response.json() == {"detail": "Database operation failed"}


def test_update_stock_for_nonexistent_product():
    response = client.patch(
        "/products/999/stock",
        json={"stock_quantity": 25},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Product not found"}


def test_get_products_when_database_is_empty():
    with TestingSessionLocal() as db:
        db.query(Product).delete()
        db.commit()

    response = client.get("/products")

    assert response.status_code == 200
    assert response.json() == []


def test_get_products_when_database_fails():
    class FailingSession:
        def scalars(self, statement):
            raise SQLAlchemyError("Simulated database failure")

        def rollback(self):
            pass

    def override_failing_get_db():
        yield FailingSession()

    app.dependency_overrides[get_db] = override_failing_get_db

    try:
        response = client.get("/products")
    finally:
        app.dependency_overrides[get_db] = override_get_db

    assert response.status_code == 503
    assert response.json() == {"detail": "Database operation failed"}


def test_get_product_when_database_fails():
    class FailingSession:
        def get(self, model, product_id):
            raise SQLAlchemyError("Simulated database failure")

        def rollback(self):
            pass

    def override_failing_get_db():
        yield FailingSession()

    app.dependency_overrides[get_db] = override_failing_get_db

    try:
        response = client.get("/products/1")
    finally:
        app.dependency_overrides[get_db] = override_get_db

    assert response.status_code == 503
    assert response.json() == {"detail": "Database operation failed"}


def test_lifespan_seeds_products_when_database_is_empty(monkeypatch):
    class FakeSession:
        def __init__(self):
            self.added_products = []
            self.committed = False
            self.rolled_back = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            pass

        def scalar(self, statement):
            return None

        def add_all(self, products):
            self.added_products.extend(products)

        def commit(self):
            self.committed = True

        def rollback(self):
            self.rolled_back = True

    fake_session = FakeSession()
    monkeypatch.setattr(main, "SessionLocal", lambda: fake_session)

    async def run_lifespan():
        async with main.lifespan(app):
            pass

    asyncio.run(run_lifespan())

    assert len(fake_session.added_products) == 3
    assert [p.name for p in fake_session.added_products] == [
        "Laptop",
        "Keyboard",
        "Mouse",
    ]
    assert fake_session.committed is True
    assert fake_session.rolled_back is False


def test_lifespan_does_not_seed_existing_products(monkeypatch):
    class FakeSession:
        def __init__(self):
            self.add_all_called = False
            self.committed = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            pass

        def scalar(self, statement):
            return 1

        def add_all(self, products):
            self.add_all_called = True

        def commit(self):
            self.committed = True

        def rollback(self):
            pass

    fake_session = FakeSession()
    monkeypatch.setattr(main, "SessionLocal", lambda: fake_session)

    async def run_lifespan():
        async with main.lifespan(app):
            pass

    asyncio.run(run_lifespan())

    assert fake_session.add_all_called is False
    assert fake_session.committed is False


def test_lifespan_rolls_back_when_seed_commit_fails(monkeypatch):
    class FakeSession:
        def __init__(self):
            self.rolled_back = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            pass

        def scalar(self, statement):
            return None

        def add_all(self, products):
            pass

        def commit(self):
            raise SQLAlchemyError("Simulated seed failure")

        def rollback(self):
            self.rolled_back = True

    fake_session = FakeSession()
    monkeypatch.setattr(main, "SessionLocal", lambda: fake_session)

    async def run_lifespan():
        async with main.lifespan(app):
            pass

    with pytest.raises(SQLAlchemyError, match="Simulated seed failure"):
        asyncio.run(run_lifespan())

    assert fake_session.rolled_back is True


def test_get_db_closes_session(monkeypatch):
    class FakeSession:
        closed = False

        def close(self):
            self.closed = True

    fake_session = FakeSession()
    monkeypatch.setattr(database, "SessionLocal", lambda: fake_session)

    db_generator = database.get_db()

    yielded_session = next(db_generator)
    assert yielded_session is fake_session
    assert fake_session.closed is False

    with pytest.raises(StopIteration):
        next(db_generator)

    assert fake_session.closed is True


def test_get_db_closes_session_after_exception(monkeypatch):
    class FakeSession:
        closed = False

        def close(self):
            self.closed = True

    fake_session = FakeSession()
    monkeypatch.setattr(database, "SessionLocal", lambda: fake_session)

    db_generator = database.get_db()

    yielded_session = next(db_generator)
    assert yielded_session is fake_session

    with pytest.raises(RuntimeError, match="Simulated request failure"):
        db_generator.throw(RuntimeError("Simulated request failure"))

    assert fake_session.closed is True
