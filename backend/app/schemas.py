from uuid import UUID
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field


class ProductCreate(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=100,
        description="Product name",
    )
    price: float = Field(
        gt=0,
        description="Product price must be greater than zero",
    )
    stock_quantity: int = Field(
        default=0,
        ge=0,
        description="Available stock; cannot be negative",
    )


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    price: float
    stock_quantity: int


class StockUpdate(BaseModel):
    stock_quantity: int = Field(
        ge=0,
        description="New stock quantity; cannot be negative",
    )


class OrderCreate(BaseModel):
    product_id: int = Field(
        gt=0,
        description="ID of the product to order",
    )

    quantity: int = Field(
        gt=0,
        description="Number of units to order",
    )


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    quantity: int
    unit_price: Decimal
    total_price: Decimal
    status: str
    created_at: datetime


class OrderCreatedEvent(BaseModel):
    event_id: UUID
    event_type: str = Field(pattern="^order\\.created$")

    order_id: int = Field(gt=0)
    product_id: int = Field(gt=0)

    product_name: str = Field(min_length=1, max_length=100)
    quantity: int = Field(gt=0)

    unit_price: Decimal = Field(ge=0)
    total_price: Decimal = Field(ge=0)

    status: str = Field(min_length=1, max_length=30)
    created_at: datetime
