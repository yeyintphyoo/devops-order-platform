import logging
from contextlib import asynccontextmanager
from decimal import Decimal
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, status
from prometheus_fastapi_instrumentator import Instrumentator
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from .cache import (
    cache_products,
    get_cached_products,
    invalidate_products_cache,
)
from .database import SessionLocal, get_db
from .models import Order, OutboxEvent, Product
from .schemas import (
    OrderCreate,
    OrderResponse,
    ProductCreate,
    ProductResponse,
    StockUpdate,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Database schema must be managed by Alembic.
    # Run `python -m alembic upgrade head` before starting the API.

    # Insert sample products only when the table is empty.
    # Let PostgreSQL generate primary keys automatically.
    with SessionLocal() as db:
        if db.scalar(select(Product.id).limit(1)) is None:
            db.add_all(
                [
                    Product(
                        name="Laptop",
                        price=2500.00,
                        stock_quantity=0,
                    ),
                    Product(
                        name="Keyboard",
                        price=150.00,
                        stock_quantity=0,
                    ),
                    Product(
                        name="Mouse",
                        price=75.00,
                        stock_quantity=0,
                    ),
                ]
            )

            try:
                db.commit()
                logger.info("Sample products initialized")
            except SQLAlchemyError:
                db.rollback()
                logger.exception("Failed to initialize sample products")
                raise

    yield


app = FastAPI(
    title="DevOps Order Platform",
    description="A production-style DevOps learning project",
    version="0.2.0",
    lifespan=lifespan,
)

Instrumentator().instrument(app).expose(
    app,
    endpoint="/metrics",
    include_in_schema=False,
)


@app.get("/health/live")
def health_live():
    return {"status": "alive"}


@app.get("/health/ready")
def health_ready(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Database readiness check failed")
        raise HTTPException(
            status_code=503,
            detail="Database is unavailable",
        )

    return {"status": "ready"}


@app.get("/products")
def get_products(db: Session = Depends(get_db)):
    # Try Redis first; None means cache miss or Redis failure.
    cached_products = get_cached_products()
    if cached_products is not None:
        logger.debug("Returning products from Redis cache")
        return cached_products

    # Fall back to PostgreSQL on a cache miss or Redis failure.
    try:
        products = db.scalars(select(Product).order_by(Product.id)).all()
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to retrieve products")
        raise HTTPException(
            status_code=503,
            detail="Database operation failed",
        )

    product_list = [
        {
            "id": product.id,
            "name": product.name,
            "price": product.price,
            "stock_quantity": product.stock_quantity,
        }
        for product in products
    ]

    # A cache failure must not prevent a successful database response.
    cache_products(product_list)

    return product_list


@app.get("/products/{product_id}", response_model=ProductResponse)
def get_product(
    product_id: int,
    db: Session = Depends(get_db),
):
    try:
        product = db.get(Product, product_id)
    except SQLAlchemyError:
        db.rollback()
        logger.exception(
            "Failed to retrieve product id=%s",
            product_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Database operation failed",
        )

    if product is None:
        raise HTTPException(
            status_code=404,
            detail="Product not found",
        )

    return product


@app.post(
    "/products",
    response_model=ProductResponse,
    status_code=201,
)
def create_product(
    product_data: ProductCreate,
    db: Session = Depends(get_db),
):
    product_name = product_data.name.strip()

    if not product_name:
        raise HTTPException(
            status_code=422,
            detail="Product name cannot be empty",
        )

    product = Product(
        name=product_name,
        price=product_data.price,
        stock_quantity=product_data.stock_quantity,
    )

    try:
        db.add(product)
        db.commit()
        db.refresh(product)

    except IntegrityError:
        db.rollback()
        logger.exception("Product creation failed due to a data conflict")
        raise HTTPException(
            status_code=409,
            detail="Could not create product due to a data conflict",
        )

    except SQLAlchemyError:
        db.rollback()
        logger.exception("Database error while creating a product")
        raise HTTPException(
            status_code=503,
            detail="Database operation failed",
        )

    logger.info("Product created successfully id=%s", product.id)

    invalidate_products_cache()

    return product


@app.patch(
    "/products/{product_id}/stock",
    response_model=ProductResponse,
)
def update_product_stock(
    product_id: int,
    stock_data: StockUpdate,
    db: Session = Depends(get_db),
):
    try:
        product = db.get(Product, product_id)

        if product is None:
            raise HTTPException(
                status_code=404,
                detail="Product not found",
            )

        product.stock_quantity = stock_data.stock_quantity

        db.commit()
        db.refresh(product)

    except HTTPException:
        raise

    except SQLAlchemyError:
        db.rollback()
        logger.exception(
            "Database error while updating stock for product id=%s",
            product_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Database operation failed",
        )

    logger.info(
        "Product stock updated id=%s stock_quantity=%s",
        product.id,
        product.stock_quantity,
    )

    invalidate_products_cache()

    return product


@app.post(
    "/orders",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_order(
    order_data: OrderCreate,
    db: Session = Depends(get_db),
):
    try:
        # Lock the product row to prevent concurrent orders
        # from overselling the available stock.
        product = db.scalar(
            select(Product).where(Product.id == order_data.product_id).with_for_update()
        )

        if product is None:
            raise HTTPException(
                status_code=404,
                detail="Product not found",
            )

        # Verify available stock.
        if product.stock_quantity < order_data.quantity:
            raise HTTPException(
                status_code=409,
                detail="Insufficient stock",
            )

        # Calculate prices using the database product price.
        unit_price = Decimal(str(product.price)).quantize(Decimal("0.01"))
        total_price = (unit_price * order_data.quantity).quantize(Decimal("0.01"))

        # Deduct stock.
        product.stock_quantity -= order_data.quantity

        # Create the order.
        order = Order(
            product_id=product.id,
            quantity=order_data.quantity,
            unit_price=unit_price,
            total_price=total_price,
            status="pending",
        )

        db.add(order)
        db.flush()

        # Load the database-generated order ID and timestamp.
        db.refresh(order)

        # Prepare the order-created event.
        event_id = str(uuid4())

        event_payload = {
            "event_id": event_id,
            "event_type": "order.created",
            "order_id": order.id,
            "product_id": product.id,
            "product_name": product.name,
            "quantity": order.quantity,
            "unit_price": str(unit_price),
            "total_price": str(total_price),
            "status": order.status,
            "created_at": order.created_at.isoformat(),
        }

        # Store the event in the outbox.
        outbox_event = OutboxEvent(
            event_id=event_id,
            aggregate_type="order",
            aggregate_id=order.id,
            event_type="order.created",
            payload=event_payload,
        )

        db.add(outbox_event)

        # Commit stock deduction, order, and outbox event together.
        db.commit()

    except HTTPException:
        db.rollback()
        raise

    except IntegrityError:
        db.rollback()
        logger.exception("Order creation failed due to a data conflict")
        raise HTTPException(
            status_code=409,
            detail="Could not create order due to a data conflict",
        )

    except SQLAlchemyError:
        db.rollback()
        logger.exception("Database error while creating an order")
        raise HTTPException(
            status_code=503,
            detail="Database operation failed",
        )

    logger.info(
        "Order created id=%s product_id=%s quantity=%s event_id=%s",
        order.id,
        order.product_id,
        order.quantity,
        event_id,
    )

    # Stock has changed, so invalidate the product cache.
    invalidate_products_cache()

    return order


# TEMPORARY: Remove after completing the Grafana 5xx alert test.
@app.get("/_test/500", include_in_schema=False)
def test_http_500():
    raise RuntimeError("Intentional HTTP 500 for alert testing")
