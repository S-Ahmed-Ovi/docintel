from typing import Optional
from pydantic import BaseModel, Field, field_validator


class LineItemIn(BaseModel):
    description: Optional[str] = Field(None, max_length=500)
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    line_total: Optional[float] = None


class CorrectionIn(BaseModel):
    """Partial update of an invoice's structured data."""
    invoice_id: Optional[int] = None          # defaults to first invoice of the document
    corrected_by: str = Field("api-user", max_length=100)
    invoice_number: Optional[str] = Field(None, max_length=100)
    invoice_date: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    supplier_name: Optional[str] = Field(None, max_length=255)
    customer_name: Optional[str] = Field(None, max_length=255)
    subtotal: Optional[float] = None
    tax: Optional[float] = None
    total: Optional[float] = None
    currency: Optional[str] = Field(None, min_length=3, max_length=3)
    line_items: Optional[list[LineItemIn]] = None

    @field_validator("currency")
    @classmethod
    def up(cls, v):
        return v.upper() if v else v
