from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import datetime, timedelta, timezone
from bson import ObjectId
from zoneinfo import ZoneInfo
from routes.auth import (get_current_user)

from pymongo import ReturnDocument

from database import (
    orders_collection,
    products_collection,
    product_variants_collection,
    product_units_collection,
    packing_types_collection,
    users_collection,
    warehouses_collection,
    customers_collection,
    counters_collection,
    vendors_collection,
    vehicles_collection,
)

router = APIRouter()

IST = ZoneInfo("Asia/Kolkata")


# =========================================================
# CONSTANTS
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
]

ORDER_STATUSES = [
    "Pending",
    "Confirmed",
    "Ready to Pick Up",
    "Out for Delivery",
    "Completed",
    "Delivered",
    "Cancelled",
]

RECORD_STATUSES = [
    "active",
    "inactive",
]

GST_TYPES = [
    "including",
    "excluding",
]


# =========================================================
# INVOICE PREFIX
# =========================================================

ORDER_INVOICE_PREFIX = {
    "sale": "INV",
    "purchase": "PUR",
    "sale_return": "SRN",
    "purchase_return": "PRN",
    "Warehouse_IN": "WIN",
    "Warehouse_OUT": "WOUT",
    "Vehicle_IN": "VIN",
    "Vehicle_OUT": "VOUT",
}


# =========================================================
# TIME HELPERS
# =========================================================

def utc_now():
    """
    Return current UTC datetime as a naive datetime.

    MongoDB/PyMongo commonly stores BSON datetime values as UTC.
    """

    return datetime.now(timezone.utc).replace(
        tzinfo=None
    )


def convert_utc_to_ist(value):
    """
    Recursively convert datetime values from UTC to IST
    in dictionaries, lists and tuples.

    Naive datetime values are assumed to be UTC.
    """

    if isinstance(value, datetime):

        if value.tzinfo is None:
            value = value.replace(
                tzinfo=timezone.utc
            )

        return value.astimezone(IST)

    if isinstance(value, dict):

        return {
            key: convert_utc_to_ist(val)
            for key, val in value.items()
        }

    if isinstance(value, list):

        return [
            convert_utc_to_ist(item)
            for item in value
        ]

    if isinstance(value, tuple):

        return tuple(
            convert_utc_to_ist(item)
            for item in value
        )

    return value


# =========================================================
# DATE RANGE HELPER
# =========================================================

def get_utc_date_range(
    from_date: Optional[str],
    to_date: Optional[str],
):
    """
    Convert user-provided IST dates into UTC datetime range.

    Example:

        from_date = 2026-08-26
        to_date   = 2026-08-26

    Means:

        IST:
        2026-08-26 00:00:00
        to
        2026-08-27 00:00:00

    Converted to UTC:

        2026-08-25 18:30:00
        to
        2026-08-26 18:30:00

    MongoDB query uses:
        $gte start_utc
        $lt  end_utc
    """

    start_date = None
    end_date = None

    if from_date:

        try:
            start_date = datetime.strptime(
                from_date,
                "%Y-%m-%d"
            )

        except ValueError:

            raise HTTPException(
                status_code=400,
                detail=(
                    "Invalid from_date format. "
                    "Use YYYY-MM-DD"
                )
            )

    if to_date:

        try:
            end_date = datetime.strptime(
                to_date,
                "%Y-%m-%d"
            )

        except ValueError:

            raise HTTPException(
                status_code=400,
                detail=(
                    "Invalid to_date format. "
                    "Use YYYY-MM-DD"
                )
            )

    if (
        start_date
        and end_date
        and start_date > end_date
    ):

        raise HTTPException(
            status_code=400,
            detail=(
                "from_date must be before "
                "or equal to to_date"
            )
        )

    date_query = {}

    if start_date:

        start_ist = start_date.replace(
            tzinfo=IST
        )

        start_utc = start_ist.astimezone(
            timezone.utc
        ).replace(
            tzinfo=None
        )

        date_query["$gte"] = start_utc

    if end_date:

        end_ist = (
            end_date
            + timedelta(days=1)
        ).replace(
            tzinfo=IST
        )

        end_utc = end_ist.astimezone(
            timezone.utc
        ).replace(
            tzinfo=None
        )

        date_query["$lt"] = end_utc

    return date_query


# =========================================================
# OBJECT ID VALIDATION
# =========================================================

def validate_object_id(
    value: str,
    field_name: str
):
    """
    Validate string and convert to ObjectId.
    """

    if not value or not ObjectId.is_valid(value):

        raise HTTPException(
            status_code=400,
            detail=f"Invalid {field_name}"
        )

    return ObjectId(value)


# =========================================================
# SERIALIZER
# =========================================================

def serialize_value(value):
    """
    Recursively convert ObjectId values into strings.
    """

    if isinstance(value, ObjectId):

        return str(value)

    if isinstance(value, datetime):

        return value

    if isinstance(value, list):

        return [
            serialize_value(item)
            for item in value
        ]

    if isinstance(value, dict):

        return {
            key: serialize_value(val)
            for key, val in value.items()
        }

    return value


def serialize_order(order):
    """
    Serialize order document.
    """

    data = serialize_value(order)

    if "_id" in data:

        data["id"] = data["_id"]

        del data["_id"]

    return data


# =========================================================
# RESPONSE REFERENCE ENRICHMENT
# =========================================================

def enrich_orders_with_references(
    orders: List[dict]
):
    """
    Enrich orders ONLY for API response.

    IMPORTANT:
    This function NEVER updates orders_collection.

    MongoDB stores IDs.

    API response returns expanded objects:

        vendor
        customer
        vehicle

    And item references:

        product_name
        variant_name
        sku
        unit
        packaging_type
    """

    if not orders:

        return []

    # =====================================================
    # COLLECT REFERENCE IDS
    # =====================================================

    product_ids = set()
    variant_ids = set()

    vendor_ids = set()
    customer_ids = set()
    vehicle_ids = set()

    for order in orders:

        # -------------------------------------------------
        # VENDOR
        # -------------------------------------------------

        vendor_id = order.get(
            "vendor_id"
        )

        if isinstance(
            vendor_id,
            ObjectId
        ):

            vendor_ids.add(
                vendor_id
            )

        # -------------------------------------------------
        # CUSTOMER
        # -------------------------------------------------

        customer_id = order.get(
            "customer_id"
        )

        if isinstance(
            customer_id,
            ObjectId
        ):

            customer_ids.add(
                customer_id
            )

        # -------------------------------------------------
        # VEHICLE
        # -------------------------------------------------

        vehicle_id = order.get(
            "vehicle_id"
        )

        if isinstance(
            vehicle_id,
            ObjectId
        ):

            vehicle_ids.add(
                vehicle_id
            )

        # -------------------------------------------------
        # ITEMS
        # -------------------------------------------------

        for item in order.get(
            "items",
            []
        ):

            product_id = item.get(
                "product_id"
            )

            if isinstance(
                product_id,
                ObjectId
            ):

                product_ids.add(
                    product_id
                )

            variant_id = item.get(
                "variant_id"
            )

            if isinstance(
                variant_id,
                ObjectId
            ):

                variant_ids.add(
                    variant_id
                )

    # =====================================================
    # VENDOR LOOKUP
    #
    # IMPORTANT:
    # Outside the order loop.
    # =====================================================

    vendors_map = {}

    if vendor_ids:

        vendors = list(
            vendors_collection.find(
                {
                    "_id": {
                        "$in": list(
                            vendor_ids
                        )
                    }
                },
                {
                    "_id": 1,
                    "contact_person": 1,
                    "business_name": 1,
                    "mobile": 1,
                    "gst_number": 1,
                    "address": 1,
                }
            )
        )

        vendors_map = {
            vendor["_id"]: vendor
            for vendor in vendors
        }

    # =====================================================
    # CUSTOMER LOOKUP
    # =====================================================

    customers_map = {}

    if customer_ids:

        customers = list(
            customers_collection.find(
                {
                    "_id": {
                        "$in": list(
                            customer_ids
                        )
                    }
                },
                {
                    "_id": 1,
                    "name": 1,
                    "mobile": 1,
                    "billing_address": 1,
                    "shipping_address": 1,
                    "location": 1,
                    "gst_number": 1,
                }
            )
        )

        customers_map = {
            customer["_id"]: customer
            for customer in customers
        }

    # =====================================================
    # VEHICLE LOOKUP
    # =====================================================

    vehicles_map = {}

    if vehicle_ids:

        vehicles = list(
            vehicles_collection.find(
                {
                    "_id": {
                        "$in": list(
                            vehicle_ids
                        )
                    }
                },
                {
                    "_id": 1,
                    "vehicle_number": 1,
                    "model": 1,
                    "vehicle_type": 1,
                }
            )
        )

        vehicles_map = {
            vehicle["_id"]: vehicle
            for vehicle in vehicles
        }

    # =====================================================
    # PRODUCT LOOKUP
    # =====================================================

    products_map = {}

    if product_ids:

        products = list(
            products_collection.find(
                {
                    "_id": {
                        "$in": list(
                            product_ids
                        )
                    }
                },
                {
                    "_id": 1,
                    "name": 1,
                }
            )
        )

        products_map = {
            product["_id"]: product
            for product in products
        }

    # =====================================================
    # VARIANT LOOKUP
    # =====================================================

    variants_map = {}

    if variant_ids:

        variants = list(
            product_variants_collection.find(
                {
                    "_id": {
                        "$in": list(
                            variant_ids
                        )
                    }
                },
                {
                    "_id": 1,
                    "product_id": 1,
                    "name": 1,
                    "sku": 1,
                    "unit_id": 1,
                    "packaging_type_id": 1,
                }
            )
        )

        variants_map = {
            variant["_id"]: variant
            for variant in variants
        }

    # =====================================================
    # COLLECT UNIT / PACKAGING IDS
    # =====================================================

    unit_ids = set()
    packaging_type_ids = set()

    for variant in variants_map.values():

        unit_id = variant.get(
            "unit_id"
        )

        if isinstance(
            unit_id,
            ObjectId
        ):

            unit_ids.add(
                unit_id
            )

        packaging_type_id = variant.get(
            "packaging_type_id"
        )

        if isinstance(
            packaging_type_id,
            ObjectId
        ):

            packaging_type_ids.add(
                packaging_type_id
            )

    # =====================================================
    # UNIT LOOKUP
    # =====================================================

    units_map = {}

    if unit_ids:

        units = list(
            product_units_collection.find(
                {
                    "_id": {
                        "$in": list(
                            unit_ids
                        )
                    }
                },
                {
                    "_id": 1,
                    "name": 1,
                    "symbol": 1,
                    "short_name": 1,
                }
            )
        )

        units_map = {
            unit["_id"]: unit
            for unit in units
        }

    # =====================================================
    # PACKAGING LOOKUP
    # =====================================================

    packaging_map = {}

    if packaging_type_ids:

        packaging_types = list(
            packing_types_collection.find(
                {
                    "_id": {
                        "$in": list(
                            packaging_type_ids
                        )
                    }
                },
                {
                    "_id": 1,
                    "name": 1,
                }
            )
        )

        packaging_map = {
            packaging["_id"]: packaging
            for packaging in packaging_types
        }

    # =====================================================
    # BUILD RESPONSE
    # =====================================================

    response_orders = []

    for order in orders:

        response_order = dict(
            order
        )

        # =================================================
        # VENDOR
        # =================================================

        vendor = vendors_map.get(
            order.get("vendor_id")
        )

        response_order.pop(
            "vendor_id",
            None
        )

        if vendor:

            response_order["vendor"] = {

                "id": str(
                    vendor["_id"]
                ),

                "contact_person": vendor.get(
                    "contact_person"
                ),

                "business_name": vendor.get(
                    "business_name"
                ),

                "mobile": vendor.get(
                    "mobile"
                ),

                "address": vendor.get(
                    "address"
                ),

                "gst_number": vendor.get(
                    "gst_number"
                ),
            }

        else:

            response_order["vendor"] = None

        # =================================================
        # CUSTOMER
        # =================================================

        customer = customers_map.get(
            order.get("customer_id")
        )

        response_order.pop(
            "customer_id",
            None
        )

        if customer:

            response_order["customer"] = {

                "id": str(
                    customer["_id"]
                ),

                "name": customer.get(
                    "name"
                ),

                "mobile": customer.get(
                    "mobile"
                ),

                "billing_address": customer.get(
                    "billing_address"
                ),

                "shipping_address": customer.get(
                    "shipping_address"
                ),

                "gst_number": customer.get(
                    "gst_number"
                ),

                "location": customer.get(
                    "location"
                ),
            }

        else:

            response_order["customer"] = None

        # =================================================
        # VEHICLE
        # =================================================

        vehicle = vehicles_map.get(
            order.get("vehicle_id")
        )

        response_order.pop(
            "vehicle_id",
            None
        )

        if vehicle:

            response_order["vehicle"] = {

                "id": str(
                    vehicle["_id"]
                ),

                "vehicle_number": vehicle.get(
                    "vehicle_number"
                ),

                "model": vehicle.get(
                    "model"
                ),

                "vehicle_type": vehicle.get(
                    "vehicle_type"
                ),
            }

        else:

            response_order["vehicle"] = None

        # =================================================
        # ITEMS
        # =================================================

        response_items = []

        for item in order.get(
            "items",
            []
        ):

            product_id = item.get(
                "product_id"
            )

            variant_id = item.get(
                "variant_id"
            )

            product = products_map.get(
                product_id
            )

            variant = variants_map.get(
                variant_id
            )

            # ---------------------------------------------
            # PRODUCT
            # ---------------------------------------------

            product_name = (
                product.get("name")
                if product
                else None
            )

            # ---------------------------------------------
            # VARIANT
            # ---------------------------------------------

            variant_name = (
                variant.get("name")
                if variant
                else None
            )

            sku = (
                variant.get("sku")
                if variant
                else None
            )

            # ---------------------------------------------
            # UNIT
            # ---------------------------------------------

            unit_data = None

            if variant:

                unit_id = variant.get(
                    "unit_id"
                )

                unit = units_map.get(
                    unit_id
                )

                if unit:

                    unit_data = {

                        "id": str(
                            unit["_id"]
                        ),

                        "name": unit.get(
                            "name"
                        ),

                        "symbol": unit.get(
                            "symbol"
                        ),

                        "short_name": unit.get(
                            "short_name"
                        ),
                    }

            # ---------------------------------------------
            # PACKAGING
            # ---------------------------------------------

            packaging_data = None

            if variant:

                packaging_type_id = variant.get(
                    "packaging_type_id"
                )

                packaging = packaging_map.get(
                    packaging_type_id
                )

                if packaging:

                    packaging_data = {

                        "id": str(
                            packaging["_id"]
                        ),

                        "name": packaging.get(
                            "name"
                        ),
                    }

            # ---------------------------------------------
            # INVESTORS
            # ---------------------------------------------

            investors_response = []

            for investor in item.get(
                "investors",
                []
            ):

                investors_response.append({

                    "investor_id": serialize_value(
                        investor.get(
                            "investor_id"
                        )
                    ),

                    "quantity": investor.get(
                        "quantity",
                        0
                    ),
                })

            # ---------------------------------------------
            # ITEM RESPONSE
            # ---------------------------------------------

            response_item = {

                "product_id": (
                    str(product_id)
                    if isinstance(
                        product_id,
                        ObjectId
                    )
                    else product_id
                ),

                "variant_id": (
                    str(variant_id)
                    if isinstance(
                        variant_id,
                        ObjectId
                    )
                    else variant_id
                ),

                "product_name": product_name,

                "variant_name": variant_name,

                "sku": sku,

                "quantity": item.get(
                    "quantity",
                    0
                ),

                "rate": item.get(
                    "rate",
                    0
                ),

                "gst_percent": item.get(
                    "gst_percent",
                    0
                ),

                "gst_amount": item.get(
                    "gst_amount",
                    0
                ),

                "taxable_amount": item.get(
                    "taxable_amount",
                    0
                ),

                "total_amount": item.get(
                    "total_amount",
                    0
                ),

                "unit": unit_data,

                "packaging_type": packaging_data,

                "investors": investors_response,
            }

            response_items.append(
                response_item
            )

        response_order["items"] = response_items

        # =================================================
        # SERIALIZE
        # =================================================

        response_orders.append(
            serialize_order(
                response_order
            )
        )

    # =====================================================
    # UTC -> IST
    # =====================================================

    response_orders = convert_utc_to_ist(
        response_orders
    )

    return response_orders


# =========================================================
# INVESTOR ALLOCATION
# =========================================================

class InvestorAllocation(BaseModel):

    investor_id: str

    quantity: float = Field(
        gt=0
    )


# =========================================================
# ORDER ITEM
# =========================================================

class OrderItem(BaseModel):

    product_id: str

    variant_id: str

    quantity: float = Field(
        gt=0
    )

    rate: float = Field(
        ge=0
    )

    investors: List[
        InvestorAllocation
    ] = Field(
        default_factory=list
    )


# =========================================================
# CREATE ORDER MODEL
# =========================================================

class OrderCreate(BaseModel):

    type: Literal[
        "purchase",
        "sale",
        "purchase_return",
        "sale_return",
        "Warehouse_IN",
        "Warehouse_OUT",
        "Vehicle_IN",
        "Vehicle_OUT",
    ]

    invoice_no: Optional[str] = None

    invoice_date: Optional[
        datetime
    ] = None

    vendor_id: Optional[str] = None

    customer_id: Optional[str] = None

    warehouse_id: Optional[str] = None

    # NEW
    vehicle_id: Optional[str] = None

    gst_type: Literal[
        "including",
        "excluding",
    ] = "excluding"

    items: List[OrderItem] = Field(
        min_length=1
    )

    discount: float = Field(
        default=0,
        ge=0
    )

    other_charges: float = Field(
        default=0,
        ge=0
    )

    status: Literal[
        "Pending",
        "Confirmed",
        "Ready to Pick Up",
        "Out for Delivery",
        "Completed",
        "Delivered",
        "Cancelled",
    ] = "Pending"

    record_status: Literal[
        "active",
        "inactive",
    ] = "active"

    notes: Optional[str] = None


# =========================================================
# UPDATE ORDER MODEL
# =========================================================

class OrderUpdate(BaseModel):

    invoice_no: Optional[str] = None

    invoice_date: Optional[
        datetime
    ] = None

    vendor_id: Optional[str] = None

    customer_id: Optional[str] = None

    warehouse_id: Optional[str] = None

    # NEW
    vehicle_id: Optional[str] = None

    gst_type: Optional[
        Literal[
            "including",
            "excluding",
        ]
    ] = None

    items: Optional[
        List[OrderItem]
    ] = None

    discount: Optional[float] = Field(
        default=None,
        ge=0
    )

    other_charges: Optional[float] = Field(
        default=None,
        ge=0
    )

    status: Optional[
        Literal[
            "Pending",
            "Confirmed",
            "Ready to Pick Up",
            "Out for Delivery",
            "Completed",
            "Delivered",
            "Cancelled",
        ]
    ] = None

    record_status: Optional[
        Literal[
            "active",
            "inactive",
        ]
    ] = None

    notes: Optional[str] = None


# =========================================================
# STATUS UPDATE MODEL
# =========================================================

class OrderStatusUpdate(BaseModel):

    status: Literal[
        "Pending",
        "Confirmed",
        "Ready to Pick Up",
        "Out for Delivery",
        "Completed",
        "Delivered",
        "Cancelled",
    ]


# =========================================================
# RECORD STATUS UPDATE MODEL
# =========================================================

class RecordStatusUpdate(BaseModel):

    record_status: Literal[
        "active",
        "inactive",
    ]


# =========================================================
# VENDOR VALIDATION
# =========================================================

def validate_vendor(
    vendor_id: Optional[str]
):

    if not vendor_id:

        return None

    vendor_object_id = validate_object_id(
        vendor_id,
        "vendor_id"
    )

    vendor = vendors_collection.find_one(
        {
            "_id": vendor_object_id
        },
        {
            "_id": 1
        }
    )

    if not vendor:

        raise HTTPException(
            status_code=404,
            detail=(
                f"Vendor not found: "
                f"{vendor_id}"
            )
        )

    return vendor_object_id


# =========================================================
# CUSTOMER VALIDATION
# =========================================================

def validate_customer(
    customer_id: Optional[str]
):

    if not customer_id:

        return None

    customer_object_id = validate_object_id(
        customer_id,
        "customer_id"
    )

    customer = customers_collection.find_one(
        {
            "_id": customer_object_id
        },
        {
            "_id": 1
        }
    )

    if not customer:

        raise HTTPException(
            status_code=404,
            detail=(
                f"Customer not found: "
                f"{customer_id}"
            )
        )

    return customer_object_id


# =========================================================
# WAREHOUSE VALIDATION
# =========================================================

def validate_warehouse(
    warehouse_id: Optional[str]
):

    if not warehouse_id:

        return None

    warehouse_object_id = validate_object_id(
        warehouse_id,
        "warehouse_id"
    )

    warehouse = warehouses_collection.find_one(
        {
            "_id": warehouse_object_id
        },
        {
            "_id": 1
        }
    )

    if not warehouse:

        raise HTTPException(
            status_code=404,
            detail="Warehouse not found"
        )

    return warehouse_object_id


# =========================================================
# VEHICLE VALIDATION
# =========================================================

def validate_vehicle(
    vehicle_id: Optional[str]
):

    if not vehicle_id:

        return None

    vehicle_object_id = validate_object_id(
        vehicle_id,
        "vehicle_id"
    )

    vehicle = vehicles_collection.find_one(
        {
            "_id": vehicle_object_id
        },
        {
            "_id": 1
        }
    )

    if not vehicle:

        raise HTTPException(
            status_code=404,
            detail=(
                f"Vehicle not found: "
                f"{vehicle_id}"
            )
        )

    return vehicle_object_id


# =========================================================
# INVESTOR VALIDATION
# =========================================================

def validate_investor(
    investor_id: str
):

    investor_object_id = validate_object_id(
        investor_id,
        "investor_id"
    )

    investor = users_collection.find_one(
        {
            "_id": investor_object_id
        },
        {
            "_id": 1
        }
    )

    if not investor:

        raise HTTPException(
            status_code=404,
            detail=(
                f"Investor/user not found: "
                f"{investor_id}"
            )
        )

    return investor_object_id


# =========================================================
# BUILD ITEMS
# =========================================================

def build_items(
    items: List[OrderItem],
    gst_type: str
):

    processed_items = []

    subtotal = 0

    total_gst = 0

    # =====================================================
    # PROCESS EACH ITEM
    # =====================================================

    for item in items:

        # =================================================
        # PRODUCT
        # =================================================

        product_object_id = validate_object_id(
            item.product_id,
            "product_id"
        )

        product = products_collection.find_one(
            {
                "_id": product_object_id
            },
            {
                "_id": 1,
                "name": 1,
            }
        )

        if not product:

            raise HTTPException(
                status_code=404,
                detail=(
                    f"Product not found: "
                    f"{item.product_id}"
                )
            )

        # =================================================
        # VARIANT
        # =================================================

        variant_object_id = validate_object_id(
            item.variant_id,
            "variant_id"
        )

        variant = product_variants_collection.find_one(
            {
                "_id": variant_object_id,
                "product_id": product_object_id,
            },
            {
                "_id": 1,
                "product_id": 1,
                "gst_percent": 1,
                "status": 1,
            }
        )

        if not variant:

            raise HTTPException(
                status_code=404,
                detail=(
                    f"Product variant not found: "
                    f"{item.variant_id}"
                )
            )

        # =================================================
        # VARIANT STATUS
        # =================================================

        if variant.get(
            "status"
        ) == "inactive":

            raise HTTPException(
                status_code=400,
                detail=(
                    f"Product variant is inactive: "
                    f"{item.variant_id}"
                )
            )

        # =================================================
        # GST
        # =================================================

        gst_percent = float(
            variant.get(
                "gst_percent",
                0
            )
        )

        # =================================================
        # INVESTORS
        # =================================================

        investors = []

        investor_quantity = 0

        investor_ids = set()

        for allocation in item.investors:

            investor_object_id = validate_investor(
                allocation.investor_id
            )

            if investor_object_id in investor_ids:

                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Same investor cannot be "
                        "allocated multiple times "
                        f"for variant "
                        f"{item.variant_id}"
                    )
                )

            investor_ids.add(
                investor_object_id
            )

            investor_quantity += (
                allocation.quantity
            )

            investors.append({

                "investor_id":
                    investor_object_id,

                "quantity":
                    allocation.quantity,
            })

        # =================================================
        # INVESTOR QUANTITY CHECK
        # =================================================

        if investor_quantity > item.quantity:

            raise HTTPException(
                status_code=400,
                detail=(
                    "Total investor quantity "
                    "cannot be greater than "
                    f"item quantity for "
                    f"variant {item.variant_id}"
                )
            )

        # =================================================
        # LINE AMOUNT
        # =================================================

        line_amount = (
            item.quantity
            * item.rate
        )

        # =================================================
        # GST INCLUDING
        # =================================================

        if gst_type == "including":

            taxable_amount = (

                line_amount
                * 100
                / (
                    100
                    + gst_percent
                )

                if gst_percent > 0

                else line_amount
            )

            gst_amount = (
                line_amount
                - taxable_amount
            )

        # =================================================
        # GST EXCLUDING
        # =================================================

        else:

            taxable_amount = line_amount

            gst_amount = (
                taxable_amount
                * gst_percent
                / 100
            )

        # =================================================
        # TOTAL LINE
        # =================================================

        total_line_amount = (
            taxable_amount
            + gst_amount
        )

        subtotal += taxable_amount

        total_gst += gst_amount

        # =================================================
        # STORE DATA
        # =================================================

        processed_items.append({

            "product_id":
                product_object_id,

            "variant_id":
                variant_object_id,

            "quantity":
                item.quantity,

            "rate":
                item.rate,

            "gst_percent":
                gst_percent,

            "gst_amount":
                round(
                    gst_amount,
                    2
                ),

            "taxable_amount":
                round(
                    taxable_amount,
                    2
                ),

            "total_amount":
                round(
                    total_line_amount,
                    2
                ),

            "investors":
                investors,
        })

    return (
        processed_items,
        round(
            subtotal,
            2
        ),
        round(
            total_gst,
            2
        )
    )


# =========================================================
# GENERATE INVOICE NUMBER
# =========================================================

def generate_invoice_no(
    order_type: str
):

    prefix = ORDER_INVOICE_PREFIX.get(
        order_type
    )

    if not prefix:

        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported order type: "
                f"{order_type}"
            )
        )

    now = utc_now()

    year = now.strftime(
        "%Y"
    )

    month = now.strftime(
        "%m"
    )

    counter_id = (
        f"order_invoice:"
        f"{prefix}:"
        f"{year}:"
        f"{month}"
    )

    counter = counters_collection.find_one_and_update(

        {
            "_id": counter_id
        },

        {
            "$inc": {
                "seq": 1
            },

            "$set": {
                "prefix": prefix,
                "year": int(year),
                "month": int(month),
                "updated_at": now,
            }
        },

        upsert=True,

        return_document=ReturnDocument.AFTER
    )

    sequence = counter["seq"]

    return (
        f"{prefix}-"
        f"{year}-"
        f"{month}-"
        f"{sequence:04d}"
    )


# =========================================================
# CREATE ORDER
# POST /orders/v1
# =========================================================

@router.post("/v1")
def create_order(
    data: OrderCreate
):

    # =====================================================
    # INVOICE
    # =====================================================

    invoice_no = (
        data.invoice_no.strip()
        if data.invoice_no
        else None
    )

    if not invoice_no:

        invoice_no = generate_invoice_no(
            data.type
        )

    # =====================================================
    # DUPLICATE INVOICE CHECK
    # =====================================================

    existing_invoice = orders_collection.find_one(
        {
            "invoice_no": invoice_no,
            "record_status": "active",
        }
    )

    if existing_invoice:

        raise HTTPException(
            status_code=400,
            detail=(
                f"Invoice number "
                f"{invoice_no} already exists"
            )
        )

    # =====================================================
    # PARTY
    # =====================================================

    vendor_object_id = validate_vendor(
        data.vendor_id
    )

    customer_object_id = validate_customer(
        data.customer_id
    )

    # =====================================================
    # WAREHOUSE
    # =====================================================

    warehouse_object_id = validate_warehouse(
        data.warehouse_id
    )

    # =====================================================
    # VEHICLE
    # =====================================================

    vehicle_object_id = validate_vehicle(
        data.vehicle_id
    )

    # =====================================================
    # BUILD ITEMS
    # =====================================================

    (
        processed_items,
        subtotal,
        total_gst
    ) = build_items(
        data.items,
        data.gst_type
    )

    # =====================================================
    # TOTAL
    # =====================================================

    discount = data.discount

    other_charges = data.other_charges

    grand_total = (
        subtotal
        + total_gst
        + other_charges
        - discount
    )

    if grand_total < 0:

        raise HTTPException(
            status_code=400,
            detail=(
                "Grand total cannot be negative"
            )
        )

    # =====================================================
    # DATE
    # =====================================================

    now = utc_now()

    invoice_date = (
        data.invoice_date
        or now
    )

    # =====================================================
    # ORDER DOCUMENT
    # =====================================================

    order_data = {

        "type":
            data.type,

        "invoice_no":
            invoice_no,

        "invoice_date":
            invoice_date,

        "vendor_id":
            vendor_object_id,

        "customer_id":
            customer_object_id,

        "warehouse_id":
            warehouse_object_id,

        "vehicle_id":
            vehicle_object_id,

        "gst_type":
            data.gst_type,

        "items":
            processed_items,

        "subtotal":
            subtotal,

        "total_gst":
            round(
                total_gst,
                2
            ),

        "discount":
            round(
                discount,
                2
            ),

        "other_charges":
            round(
                other_charges,
                2
            ),

        "grand_total":
            round(
                grand_total,
                2
            ),

        "status":
            data.status,

        "record_status":
            data.record_status,

        "notes":
            data.notes,

        "created_at":
            now,

        "updated_at":
            now,
    }

    # =====================================================
    # INSERT
    # =====================================================

    result = orders_collection.insert_one(
        order_data
    )

    order_data["_id"] = (
        result.inserted_id
    )

    # =====================================================
    # RESPONSE ONLY
    # =====================================================

    enriched_order = (
        enrich_orders_with_references(
            [order_data]
        )[0]
    )

    return {

        "success":
            True,

        "message":
            "Order created successfully",

        "data":
            enriched_order,
    }


# =========================================================
# GET ORDERS
# GET /orders/v1
# =========================================================

@router.get("/v1")
def get_orders(

    page: int = Query(
        1,
        ge=1
    ),

    limit: int = Query(
        20,
        ge=1,
        le=100
    ),

    type: Optional[str] = None,

    status: Optional[str] = None,

    record_status: Optional[str] = "active",

    search: Optional[str] = None,

    warehouse_id: Optional[str] = None,

    vendor_id: Optional[str] = None,

    customer_id: Optional[str] = None,

    vehicle_id: Optional[str] = None,

    from_date: Optional[str] = Query(
        None,
        description=(
            "Start date in YYYY-MM-DD "
            "format, interpreted as IST"
        )
    ),

    to_date: Optional[str] = Query(
        None,
        description=(
            "End date in YYYY-MM-DD "
            "format, interpreted as IST"
        )
    )
):

    skip = (
        page - 1
    ) * limit

    query = {}

    # =====================================================
    # TYPE
    # =====================================================

    if type:

        if type not in ORDER_TYPES:

            raise HTTPException(
                status_code=400,
                detail="Invalid order type"
            )

        query["type"] = type

    # =====================================================
    # STATUS
    # =====================================================

    if status:

        if status not in ORDER_STATUSES:

            raise HTTPException(
                status_code=400,
                detail="Invalid order status"
            )

        query["status"] = status

    # =====================================================
    # RECORD STATUS
    # =====================================================

    if record_status:

        if record_status not in RECORD_STATUSES:

            raise HTTPException(
                status_code=400,
                detail="Invalid record_status"
            )

        query["record_status"] = record_status

    # =====================================================
    # SEARCH
    # =====================================================

    if search:

        query["$or"] = [

            {
                "invoice_no": {
                    "$regex": search,
                    "$options": "i",
                }
            },

            {
                "notes": {
                    "$regex": search,
                    "$options": "i",
                }
            },
        ]

    # =====================================================
    # WAREHOUSE
    # =====================================================

    if warehouse_id:

        query["warehouse_id"] = (
            validate_object_id(
                warehouse_id,
                "warehouse_id"
            )
        )

    # =====================================================
    # VENDOR
    # =====================================================

    if vendor_id:

        query["vendor_id"] = (
            validate_object_id(
                vendor_id,
                "vendor_id"
            )
        )

    # =====================================================
    # CUSTOMER
    # =====================================================

    if customer_id:

        query["customer_id"] = (
            validate_object_id(
                customer_id,
                "customer_id"
            )
        )

    # =====================================================
    # VEHICLE
    # =====================================================

    if vehicle_id:

        query["vehicle_id"] = (
            validate_object_id(
                vehicle_id,
                "vehicle_id"
            )
        )

    # =====================================================
    # DATE RANGE
    # =====================================================

    if from_date or to_date:

        date_query = get_utc_date_range(
            from_date,
            to_date
        )

        query["created_at"] = date_query

    # =====================================================
    # COUNT
    # =====================================================

    total = (
        orders_collection
        .count_documents(
            query
        )
    )

    # =====================================================
    # FETCH
    # =====================================================

    orders = list(

        orders_collection
        .find(query)
        .sort(
            "created_at",
            -1
        )
        .skip(skip)
        .limit(limit)
    )

    # =====================================================
    # ENRICH
    # =====================================================

    data = enrich_orders_with_references(
        orders
    )

    # =====================================================
    # RESPONSE
    # =====================================================

    return {

        "success":
            True,

        "data":
            data,

        "pagination": {

            "page":
                page,

            "limit":
                limit,

            "total":
                total,

            "total_pages":
                (
                    total
                    + limit
                    - 1
                ) // limit,
        }
    }


# =========================================================
# GET SINGLE ORDER
# GET /orders/v1/{order_id}
# =========================================================

@router.get(
    "/v1/{order_id}"
)
def get_order(
    order_id: str
):

    order_object_id = validate_object_id(
        order_id,
        "order_id"
    )

    order = orders_collection.find_one(
        {
            "_id": order_object_id
        }
    )

    if not order:

        raise HTTPException(
            status_code=404,
            detail="Order not found"
        )

    enriched_order = (
        enrich_orders_with_references(
            [order]
        )[0]
    )

    return {

        "success":
            True,

        "data":
            enriched_order,
    }


# =========================================================
# UPDATE ORDER
# POST /orders/update/v1/{order_id}
# =========================================================

@router.post(
    "/update/v1/{order_id}"
)
def update_order(

    order_id: str,

    data: OrderUpdate
):

    order_object_id = validate_object_id(
        order_id,
        "order_id"
    )

    # =====================================================
    # FIND ORDER
    # =====================================================

    existing_order = orders_collection.find_one(
        {
            "_id": order_object_id
        }
    )

    if not existing_order:

        raise HTTPException(
            status_code=404,
            detail="Order not found"
        )

    # =====================================================
    # FINAL STATUS
    # =====================================================

    if existing_order.get(
        "status"
    ) in [
        "Delivered",
        "Cancelled",
    ]:

        if data.record_status is not None:

            update_data = {

                "record_status":
                    data.record_status,

                "updated_at":
                    utc_now(),
            }

            orders_collection.update_one(

                {
                    "_id":
                        order_object_id
                },

                {
                    "$set":
                        update_data
                }
            )

            updated = (
                orders_collection.find_one(
                    {
                        "_id":
                            order_object_id
                    }
                )
            )

            enriched_order = (
                enrich_orders_with_references(
                    [updated]
                )[0]
            )

            return {

                "success":
                    True,

                "message":
                    "Order record status updated",

                "data":
                    enriched_order,
            }

        raise HTTPException(
            status_code=400,
            detail=(
                "Delivered or Cancelled "
                "orders cannot be edited"
            )
        )

    # =====================================================
    # UPDATE DATA
    # =====================================================

    update_data = {}

    # =====================================================
    # INVOICE
    # =====================================================

    if data.invoice_no is not None:

        invoice_no = (
            data.invoice_no.strip()
        )

        if not invoice_no:

            raise HTTPException(
                status_code=400,
                detail=(
                    "Invoice number "
                    "cannot be empty"
                )
            )

        duplicate = orders_collection.find_one(
            {

                "_id": {
                    "$ne":
                        order_object_id
                },

                "type":
                    existing_order.get(
                        "type"
                    ),

                "invoice_no":
                    invoice_no,

                "record_status":
                    "active",
            }
        )

        if duplicate:

            raise HTTPException(
                status_code=400,
                detail=(
                    "Invoice number "
                    "already exists"
                )
            )

        update_data[
            "invoice_no"
        ] = invoice_no

    # =====================================================
    # INVOICE DATE
    # =====================================================

    if data.invoice_date is not None:

        update_data[
            "invoice_date"
        ] = data.invoice_date

    # =====================================================
    # VENDOR
    # =====================================================

    if data.vendor_id is not None:

        update_data[
            "vendor_id"
        ] = validate_vendor(
            data.vendor_id
        )

    # =====================================================
    # CUSTOMER
    # =====================================================

    if data.customer_id is not None:

        update_data[
            "customer_id"
        ] = validate_customer(
            data.customer_id
        )

    # =====================================================
    # WAREHOUSE
    # =====================================================

    if data.warehouse_id is not None:

        update_data[
            "warehouse_id"
        ] = validate_warehouse(
            data.warehouse_id
        )

    # =====================================================
    # VEHICLE
    # =====================================================

    if data.vehicle_id is not None:

        update_data[
            "vehicle_id"
        ] = validate_vehicle(
            data.vehicle_id
        )

    # =====================================================
    # GST TYPE
    # =====================================================

    gst_type = (

        data.gst_type

        if data.gst_type is not None

        else existing_order.get(
            "gst_type",
            "excluding"
        )
    )

    if data.gst_type is not None:

        update_data[
            "gst_type"
        ] = data.gst_type

    # =====================================================
    # ITEMS
    # =====================================================

    if data.items is not None:

        (
            processed_items,
            subtotal,
            total_gst
        ) = build_items(

            data.items,

            gst_type
        )

        update_data[
            "items"
        ] = processed_items

        update_data[
            "subtotal"
        ] = subtotal

        update_data[
            "total_gst"
        ] = total_gst

    else:

        subtotal = existing_order.get(
            "subtotal",
            0
        )

        total_gst = existing_order.get(
            "total_gst",
            0
        )

    # =====================================================
    # DISCOUNT
    # =====================================================

    discount = (

        data.discount

        if data.discount is not None

        else existing_order.get(
            "discount",
            0
        )
    )

    # =====================================================
    # OTHER CHARGES
    # =====================================================

    other_charges = (

        data.other_charges

        if data.other_charges is not None

        else existing_order.get(
            "other_charges",
            0
        )
    )

    # =====================================================
    # GRAND TOTAL
    # =====================================================

    grand_total = (

        subtotal
        + total_gst
        + other_charges
        - discount
    )

    if grand_total < 0:

        raise HTTPException(
            status_code=400,
            detail=(
                "Grand total cannot be negative"
            )
        )

    update_data[
        "discount"
    ] = round(
        discount,
        2
    )

    update_data[
        "other_charges"
    ] = round(
        other_charges,
        2
    )

    update_data[
        "grand_total"
    ] = round(
        grand_total,
        2
    )

    # =====================================================
    # STATUS
    # =====================================================

    if data.status is not None:

        update_data[
            "status"
        ] = data.status

    # =====================================================
    # RECORD STATUS
    # =====================================================

    if data.record_status is not None:

        update_data[
            "record_status"
        ] = data.record_status

    # =====================================================
    # NOTES
    # =====================================================

    if data.notes is not None:

        update_data[
            "notes"
        ] = data.notes

    # =====================================================
    # UPDATED AT
    # =====================================================

    update_data[
        "updated_at"
    ] = utc_now()

    # =====================================================
    # UPDATE DATABASE
    # =====================================================

    orders_collection.update_one(

        {
            "_id":
                order_object_id
        },

        {
            "$set":
                update_data
        }
    )

    # =====================================================
    # GET UPDATED
    # =====================================================

    updated_order = (
        orders_collection.find_one(
            {
                "_id":
                    order_object_id
            }
        )
    )

    # =====================================================
    # ENRICH
    # =====================================================

    enriched_order = (
        enrich_orders_with_references(
            [updated_order]
        )[0]
    )

    return {

        "success":
            True,

        "message":
            "Order updated successfully",

        "data":
            enriched_order,
    }


# =========================================================
# UPDATE ORDER STATUS
# POST /orders/status/v1/{order_id}
# =========================================================

@router.post(
    "/status/v1/{order_id}"
)
def update_order_status(

    order_id: str,

    data: OrderStatusUpdate
):

    order_object_id = validate_object_id(
        order_id,
        "order_id"
    )

    # =====================================================
    # FIND
    # =====================================================

    order = orders_collection.find_one(
        {
            "_id":
                order_object_id
        }
    )

    if not order:

        raise HTTPException(
            status_code=404,
            detail="Order not found"
        )

    # =====================================================
    # CURRENT STATUS
    # =====================================================

    current_status = order.get(
        "status",
        "Pending"
    )

    # =====================================================
    # FINAL STATUS
    # =====================================================

    if current_status in [
        "Delivered",
        "Cancelled",
    ]:

        raise HTTPException(
            status_code=400,
            detail=(
                f"Order is already "
                f"{current_status}"
            )
        )

    # =====================================================
    # UPDATE
    # =====================================================

    orders_collection.update_one(

        {
            "_id":
                order_object_id
        },

        {
            "$set": {

                "status":
                    data.status,

                "updated_at":
                    utc_now(),
            }
        }
    )

    # =====================================================
    # GET UPDATED
    # =====================================================

    updated_order = (
        orders_collection.find_one(
            {
                "_id":
                    order_object_id
            }
        )
    )

    # =====================================================
    # ENRICH
    # =====================================================

    enriched_order = (
        enrich_orders_with_references(
            [updated_order]
        )[0]
    )

    return {

        "success":
            True,

        "message":
            "Order status updated successfully",

        "data":
            enriched_order,
    }


# =========================================================
# UPDATE RECORD STATUS
# POST /orders/record-status/v1/{order_id}
# =========================================================

@router.post(
    "/record-status/v1/{order_id}"
)
def update_record_status(

    order_id: str,

    data: RecordStatusUpdate
):

    order_object_id = validate_object_id(
        order_id,
        "order_id"
    )

    # =====================================================
    # FIND
    # =====================================================

    order = orders_collection.find_one(
        {
            "_id":
                order_object_id
        }
    )

    if not order:

        raise HTTPException(
            status_code=404,
            detail="Order not found"
        )

    # =====================================================
    # UPDATE
    # =====================================================

    orders_collection.update_one(

        {
            "_id":
                order_object_id
        },

        {
            "$set": {

                "record_status":
                    data.record_status,

                "updated_at":
                    utc_now(),
            }
        }
    )

    # =====================================================
    # GET UPDATED
    # =====================================================

    updated_order = (
        orders_collection.find_one(
            {
                "_id":
                    order_object_id
            }
        )
    )

    # =====================================================
    # ENRICH
    # =====================================================

    enriched_order = (
        enrich_orders_with_references(
            [updated_order]
        )[0]
    )

    return {

        "success":
            True,

        "message": (

            "Order activated successfully"

            if data.record_status == "active"

            else
            "Order inactivated successfully"
        ),

        "data":
            enriched_order,
    }
