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
