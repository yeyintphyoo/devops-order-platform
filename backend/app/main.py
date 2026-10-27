import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from .database import SessionLocal, get_db
from .models import Product
from .schemas import ProductCreate, ProductResponse, StockUpdate
from prometheus_fastapi_instrumentator import Instrumentator


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
    try:
        products = db.scalars(select(Product).order_by(Product.id)).all()
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to retrieve products")
        raise HTTPException(
            status_code=503,
            detail="Database operation failed",
        )

    return [
        {
            "id": product.id,
            "name": product.name,
            "price": product.price,
            "stock_quantity": product.stock_quantity,
        }
        for product in products
    ]


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

    return product


# TEMPORARY: Remove after completing the Grafana 5xx alert test.
@app.get("/_test/500", include_in_schema=False)
def test_http_500():
    raise RuntimeError("Intentional HTTP 500 for alert testing")
