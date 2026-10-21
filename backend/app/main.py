from fastapi import FastAPI, HTTPException

app = FastAPI(
    title="DevOps Order Platform",
    description="A production-style DevOps learning project",
    version="0.1.0",
)

PRODUCTS = {
    1: {
        "id": 1,
        "name": "Laptop",
        "price": 2500.00,
    },
    2: {
        "id": 2,
        "name": "Keyboard",
        "price": 150.00,
    },
    3: {
        "id": 3,
        "name": "Mouse",
        "price": 75.00,
    },
}


@app.get("/health/live")
def health_live():
    return {"status": "alive"}


@app.get("/health/ready")
def health_ready():
    return {"status": "ready"}


@app.get("/products")
def get_products():
    return list(PRODUCTS.values())


@app.get("/products/{product_id}")
def get_product(product_id: int):
    product = PRODUCTS.get(product_id)

    if product is None:
        raise HTTPException(
            status_code=404,
            detail="Product not found",
        )

    return product
