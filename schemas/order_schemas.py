from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Literal
from datetime import datetime

# =========================================================
# CONSTANTS & LISTS
# =========================================================

ORDER_TYPES = [
    "purchase",
    "sale",
    "purchase_return",
    "sale_return",
    "Warehouse_IN",
    "Warehouse_OUT",
    "Vehicle_IN",
    "Vehicle_OUT",
    "purchase_to_warehouse",
    "warehouse_to_vehicle",
    "vehicle_to_warehouse",
    "warehouse_to_warehouse",
]

ORDER_STATUSES = [
    "Pending",
    "Confirmed",
    "Ready to Pick Up",
    "Out for Delivery",
    "Completed",
    "Delivered",
    "Cancelled",
    "Rejected",
]

RECORD_STATUSES = [
    "active",
    "inactive",
]

GST_TYPES = [
    "including",
    "excluding",
]

ORDER_PREFIX = "ORD"

ORDER_INVOICE_PREFIX = {
    "sale": "INV",
    "purchase": "PUR",
    "sale_return": "SRN",
    "purchase_return": "PRN",
    "Warehouse_IN": "WIN",
    "Warehouse_OUT": "WOUT",
    "Vehicle_IN": "VIN",
    "Vehicle_OUT": "VOUT",
    "purchase_to_warehouse": "PTW",
    "warehouse_to_vehicle": "WTV",
    "vehicle_to_warehouse": "VTW",
    "warehouse_to_warehouse": "WTW",
}


# =========================================================
# SCHEMAS
# =========================================================

class InvestorAllocation(BaseModel):
    investor_id: str
    quantity: float = Field(gt=0)


class OrderItem(BaseModel):
    product_id: str
    variant_id: str
    quantity: float = Field(gt=0)
    rate: float = Field(ge=0)
    investors: List[InvestorAllocation] = Field(default_factory=list)
    ref_item_id: Optional[str] = None


class OrderCreate(BaseModel):
    type: str
    vendor_id: Optional[str] = None
    customer_id: Optional[str] = None
    warehouse_id: Optional[str] = None
    destination_warehouse_id: Optional[str] = None
    vehicle_id: Optional[str] = None
    invoice_no: Optional[str] = None
    ref_invoice_id: Optional[str] = None
    payment_mode: Optional[str] = None
    branch_id: Optional[str] = None
    assigned_employee_id: Optional[str] = None
    gst_type: str = "excluding"
    items: List[OrderItem] = Field(min_length=1)
    other_charges: float = Field(default=0, ge=0)
    status: str = "Pending"
    record_status: str = "active"
    notes: Optional[str] = None

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        val = str(v).strip()
        if val not in ORDER_TYPES:
            raise ValueError(f"Invalid order type '{v}'. Allowed types are: {ORDER_TYPES}")
        return val

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        val = str(v).strip()
        if val not in ORDER_STATUSES:
            raise ValueError(f"Invalid order status '{v}'. Allowed statuses are: {ORDER_STATUSES}")
        return val

    @field_validator("gst_type")
    @classmethod
    def validate_gst_type(cls, v: str) -> str:
        val = str(v).strip().lower()
        if val not in GST_TYPES:
            raise ValueError(f"Invalid gst_type '{v}'. Allowed are: {GST_TYPES}")
        return val

    @field_validator("record_status")
    @classmethod
    def validate_record_status(cls, v: str) -> str:
        val = str(v).strip().lower()
        if val not in RECORD_STATUSES:
            raise ValueError(f"Invalid record_status '{v}'. Allowed are: {RECORD_STATUSES}")
        return val


class OrderUpdate(BaseModel):
    vendor_id: Optional[str] = None
    customer_id: Optional[str] = None
    warehouse_id: Optional[str] = None
    destination_warehouse_id: Optional[str] = None
    vehicle_id: Optional[str] = None
    payment_mode: Optional[str] = None
    branch_id: Optional[str] = None
    assigned_employee_id: Optional[str] = None
    gst_type: Optional[str] = None
    items: Optional[List[OrderItem]] = None
    other_charges: Optional[float] = Field(default=None, ge=0)
    status: Optional[str] = None
    record_status: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            val = str(v).strip()
            if val not in ORDER_STATUSES:
                raise ValueError(f"Invalid order status '{v}'. Allowed statuses are: {ORDER_STATUSES}")
            return val
        return v

    @field_validator("gst_type")
    @classmethod
    def validate_gst_type(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            val = str(v).strip().lower()
            if val not in GST_TYPES:
                raise ValueError(f"Invalid gst_type '{v}'. Allowed are: {GST_TYPES}")
            return val
        return v

    @field_validator("record_status")
    @classmethod
    def validate_record_status(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            val = str(v).strip().lower()
            if val not in RECORD_STATUSES:
                raise ValueError(f"Invalid record_status '{v}'. Allowed are: {RECORD_STATUSES}")
            return val
        return v


class OrderStatusUpdate(BaseModel):
    status: str
    note: Optional[str] = None
    vehicle_id: Optional[str] = None
    warehouse_id: Optional[str] = None
    destination_warehouse_id: Optional[str] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        val = str(v).strip()
        if val not in ORDER_STATUSES:
            raise ValueError(f"Invalid order status '{v}'. Allowed statuses are: {ORDER_STATUSES}")
        return val


class RecordStatusUpdate(BaseModel):
    record_status: str

    @field_validator("record_status")
    @classmethod
    def validate_record_status(cls, v: str) -> str:
        val = str(v).strip().lower()
        if val not in RECORD_STATUSES:
            raise ValueError(f"Invalid record_status '{v}'. Allowed are: {RECORD_STATUSES}")
        return val


class ManualBillingRequest(BaseModel):
    discount_amount: float = Field(default=0, ge=0)


class StockTransferItem(BaseModel):
    product_id: str
    variant_id: str
    quantity: float = Field(gt=0)


class StockTransferRequest(BaseModel):
    transfer_type: str
    from_warehouse_id: Optional[str] = None
    to_warehouse_id: Optional[str] = None
    from_vehicle_id: Optional[str] = None
    to_vehicle_id: Optional[str] = None
    items: List[StockTransferItem] = Field(min_length=1)
    notes: Optional[str] = None

    @field_validator("transfer_type")
    @classmethod
    def validate_transfer_type(cls, v: str) -> str:
        val = str(v).strip()
        allowed = [
            "purchase_to_warehouse",
            "warehouse_to_warehouse",
            "warehouse_to_vehicle",
            "vehicle_to_warehouse",
            "Warehouse_IN",
            "Warehouse_OUT",
            "Vehicle_IN",
            "Vehicle_OUT",
        ]
        if val not in allowed:
            raise ValueError(f"Invalid transfer_type '{v}'. Allowed are: {allowed}")
        return val


class OrderReceiptRequest(BaseModel):
    payment_mode: Literal["CASH", "UPI", "BANK_TRANSFER", "CHEQUE", "FINANCE"]
    amount: float = Field(..., gt=0)
    bank_account_name: Optional[str] = None
    transaction_ref: Optional[str] = None
    cheque_no: Optional[str] = None
    cheque_date: Optional[str] = None
    cheque_bank: Optional[str] = None
    financier_name: Optional[str] = None
    receipt_date: Optional[datetime] = None
    bank_clearance_date: Optional[datetime] = None
    notes: Optional[str] = None
    collected_by_id: Optional[str] = None


class BulkOrderDispatchRequest(BaseModel):
    order_ids: List[str] = Field(..., min_length=1)
    vehicle_id: Optional[str] = None
    status: str = "Out for Delivery"
    note: Optional[str] = None
    route_name: Optional[str] = None

