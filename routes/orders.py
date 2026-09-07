from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import datetime, timedelta, timezone
from bson import ObjectId
from zoneinfo import ZoneInfo
import io
import os
import requests
from num2words import num2words
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle
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
    branches_collection,
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
    "warehouse_to_vehicle",
    "vehicle_to_warehouse",
    
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


# =========================================================
# ORDER / INVOICE PREFIX
# =========================================================

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
    "warehouse_to_vehicle": "WTV",
    "vehicle_to_warehouse": "VTW"
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
# TRACKING HELPER
# =========================================================

def create_tracking_entry(
    status: str,
    user_id: Optional[str] = None,
    note: Optional[str] = None,
):
    """Create one embedded order-status tracking entry."""
    now = utc_now()
    entry = {
        "status": status,
        "timestamp": now,
        "updated_by": None,
        "note": note,
    }
    if user_id and ObjectId.is_valid(str(user_id)):
        entry["updated_by"] = ObjectId(str(user_id))
    return entry


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
    user_ids = set()
    branch_ids = set()

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
        # ASSIGNED EMPLOYEE
        # -------------------------------------------------

        assigned_employee_id = order.get(
            "assigned_employee_id"
        )

        if isinstance(
            assigned_employee_id,
            ObjectId
        ):
            user_ids.add(
                assigned_employee_id
            )

        # -------------------------------------------------
        # CREATED BY
        # -------------------------------------------------

        created_by = order.get(
            "created_by"
        )

        if isinstance(
            created_by,
            ObjectId
        ):
            user_ids.add(
                created_by
            )

        # -------------------------------------------------
        # TRACKING USERS
        # -------------------------------------------------
        for tracking_entry in order.get("tracking", []):
            tracking_user_id = tracking_entry.get("updated_by")
            if isinstance(tracking_user_id, ObjectId):
                user_ids.add(tracking_user_id)

        # -------------------------------------------------
        # BRANCH
        # -------------------------------------------------

        branch_id = order.get(
            "branch_id"
        )

        if isinstance(
            branch_id,
            ObjectId
        ):
            branch_ids.add(
                branch_id
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
                    "company_name": 1,
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
    # USER LOOKUP
    # =====================================================

    users_map = {}

    if user_ids:

        users = list(
            users_collection.find(
                {"_id": {"$in": list(user_ids)}},
                {
                    "_id": 1,
                    "name": 1,
                    "full_name": 1,
                    "username": 1,
                }
            )
        )

        users_map = {
            user["_id"]: user
            for user in users
        }

    # =====================================================
    # BRANCH LOOKUP
    # =====================================================

    branches_map = {}

    if branch_ids:

        branches = list(
            branches_collection.find(
                {"_id": {"$in": list(branch_ids)}},
                {"_id": 1, "name": 1}
            )
        )

        branches_map = {
            branch["_id"]: branch
            for branch in branches
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
                "shop_name": customer.get(
                    "company_name"
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

        # =================================================
        # ASSIGNED EMPLOYEE
        # =================================================

        assigned_employee = users_map.get(
            order.get("assigned_employee_id")
        )

        response_order.pop(
            "assigned_employee_id",
            None
        )

        if assigned_employee:
            response_order["assigned_employee_name"] = (
                assigned_employee.get("name")
                or assigned_employee.get("full_name")
                or assigned_employee.get("username")
            )
        else:
            response_order["assigned_employee_name"] = None

        # =================================================
        # CREATED BY
        # =================================================

        created_by_user = users_map.get(
            order.get("created_by")
        )

        response_order.pop(
            "created_by",
            None
        )

        if created_by_user:
            response_order["created_by_name"] = (
                created_by_user.get("name")
                or created_by_user.get("full_name")
                or created_by_user.get("username")
            )
        else:
            response_order["created_by_name"] = None

        # =================================================
        # BRANCH
        # =================================================

        branch = branches_map.get(
            order.get("branch_id")
        )

        response_order.pop(
            "branch_id",
            None
        )

        if branch:
            response_order["branch"] = {
                "id": str(branch["_id"]),
                "name": branch.get("name")
            }
        else:
            response_order["branch"] = None

        # =================================================
        # TRACKING
        # =================================================
        tracking_response = []

        for tracking_entry in order.get("tracking", []):
            tracking_user_id = tracking_entry.get("updated_by")
            tracking_user = users_map.get(tracking_user_id)
            tracking_response.append({
                "status": tracking_entry.get("status"),
                "timestamp": tracking_entry.get("timestamp"),
                "updated_by": (
                    str(tracking_user_id)
                    if isinstance(tracking_user_id, ObjectId)
                    else tracking_user_id
                ),
                "updated_by_name": (
                    (
                        tracking_user.get("name")
                        or tracking_user.get("full_name")
                        or tracking_user.get("username")
                    )
                    if tracking_user
                    else None
                ),
                "note": tracking_entry.get("note"),
            })

        response_order["tracking"] = tracking_response

        # =================================================
        # ITEMS
        # =================================================
        response_items = []

        for item in order.get(
            "items",
            []
        ):
            item_id = item.get(
                "item_id",''
            )
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
                "item_id": (
                    str(item_id)
                    if isinstance(
                        item_id,
                        ObjectId
                    )
                    else item_id
                ),

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

        if order.get("type") in ["sale_return", "purchase_return"]:
            ref_invoice_id = order.get("ref_invoice_id")

            ref_invoice_no = None

            if ref_invoice_id:
                try:
                    if not isinstance(ref_invoice_id, ObjectId):
                        ref_invoice_id = ObjectId(str(ref_invoice_id))

                    parent_order = orders_collection.find_one(
                        {"_id": ref_invoice_id},
                        {"invoice_no": 1}
                    )

                    if parent_order:
                        ref_invoice_no = parent_order.get("invoice_no")

                except Exception:
                    ref_invoice_no = None

            response_order["ref_invoice_no"] = ref_invoice_no

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

    ref_item_id: Optional[str]|None = None


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
        "warehouse_to_vehicle",
        "vehicle_to_warehouse",
    ]

    vendor_id: Optional[str] = None

    customer_id: Optional[str] = None

    warehouse_id: Optional[str] = None

    # NEW
    vehicle_id: Optional[str] = None

    # Required only for purchase orders.
    # For sales, invoice_no is generated during manual billing.
    invoice_no: Optional[str] = None

    ref_invoice_id: Optional[str] = None

    payment_mode: Optional[str] = None

    branch_id: Optional[str] = None

    assigned_employee_id: Optional[str] = None

    gst_type: Literal[
        "including",
        "excluding",
    ] = "excluding"

    items: List[OrderItem] = Field(
        min_length=1
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
        "Rejected",
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

    vendor_id: Optional[str] = None

    customer_id: Optional[str] = None

    warehouse_id: Optional[str] = None

    # NEW
    vehicle_id: Optional[str] = None

    payment_mode: Optional[str] = None

    branch_id: Optional[str] = None

    assigned_employee_id: Optional[str] = None

    gst_type: Optional[
        Literal[
            "including",
            "excluding",
        ]
    ] = None

    items: Optional[
        List[OrderItem]
    ] = None

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
            "Rejected",
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
        "Rejected",
    ]

    note: Optional[str] = None

    vehicle_id: Optional[str] = None


# =========================================================
# RECORD STATUS UPDATE MODEL
# =========================================================

class RecordStatusUpdate(BaseModel):

    record_status: Literal[
        "active",
        "inactive",
    ]


# =========================================================
# MANUAL BILLING MODEL
# =========================================================

class ManualBillingRequest(BaseModel):

    discount_amount: float = Field(
        default=0,
        ge=0
    )


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
# BRANCH VALIDATION
# =========================================================

def validate_branch(
    branch_id: Optional[str]
):
    if not branch_id:
        return None

    branch_object_id = validate_object_id(
        branch_id,
        "branch_id"
    )

    branch = branches_collection.find_one(
        {"_id": branch_object_id},
        {"_id": 1}
    )

    if not branch:
        raise HTTPException(
            status_code=404,
            detail="Branch not found"
        )

    return branch_object_id


# =========================================================
# ASSIGNED EMPLOYEE VALIDATION
# =========================================================

def validate_assigned_employee(
    employee_id: Optional[str]
):
    if not employee_id:
        return None

    employee_object_id = validate_object_id(
        employee_id,
        "assigned_employee_id"
    )

    employee = users_collection.find_one(
        {"_id": employee_object_id},
        {"_id": 1}
    )

    if not employee:
        raise HTTPException(
            status_code=404,
            detail="Assigned employee not found"
        )

    return employee_object_id


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


def build_items(items, gst_type, order_type=None):
    processed_items = []
    subtotal = 0
    total_gst = 0

    for item in items:
        try:
            product_object_id = ObjectId(str(item.product_id))
        except Exception:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid product_id: {item.product_id}"
            )

        product = products_collection.find_one(
            {"_id": product_object_id},
            {"_id": 1, "name": 1}
        )

        if not product:
            raise HTTPException(
                status_code=404,
                detail=f"Product not found: {item.product_id}"
            )

        try:
            variant_object_id = ObjectId(str(item.variant_id))
        except Exception:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid variant_id: {item.variant_id}"
            )

        variant = product_variants_collection.find_one({
            "_id": variant_object_id,
            "product_id": product_object_id
        })

        if not variant:
            raise HTTPException(
                status_code=404,
                detail=f"Product variant not found: {item.variant_id}"
            )

        if variant.get("status") == "inactive":
            raise HTTPException(
                status_code=400,
                detail=f"Product variant is inactive: {item.variant_id}"
            )

        quantity = float(item.quantity)
        rate = float(item.rate)

        if quantity <= 0:
            raise HTTPException(
                status_code=400,
                detail="Quantity must be greater than zero"
            )

        if rate < 0:
            raise HTTPException(
                status_code=400,
                detail="Rate cannot be negative"
            )

        gst_percent = float(variant.get("gst_percent", 0))
        line_amount = round(quantity * rate, 2)

        if gst_type == "including":
            if gst_percent > 0:
                taxable_amount = round(
                    line_amount * 100 / (100 + gst_percent), 2
                )
                gst_amount = round(
                    line_amount - taxable_amount, 2
                )
            else:
                taxable_amount = line_amount
                gst_amount = 0
        else:
            taxable_amount = line_amount
            gst_amount = round(
                taxable_amount * gst_percent / 100, 2
            )

        total_line_amount = round(
            taxable_amount + gst_amount, 2
        )

        subtotal += taxable_amount
        total_gst += gst_amount

        processed_item = {
            "item_id": ObjectId(),
            "product_id": product_object_id,
            "variant_id": variant_object_id,
            "quantity": quantity,
            "rate": rate,
            "gst_percent": gst_percent,
            "gst_amount": gst_amount,
            "taxable_amount": taxable_amount,
            "total_amount": total_line_amount,
            "investors": getattr(item, "investors", []),
            "batch_consumptions": [],
            "cogs": 0
        }

        if order_type == "sale_return":
            if not getattr(item, "ref_item_id", None):
                raise HTTPException(
                    status_code=400,
                    detail="ref_item_id is required for sale return items"
                )

            try:
                processed_item["ref_item_id"] = ObjectId(
                    str(item.ref_item_id)
                )
            except Exception:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid ref_item_id: {item.ref_item_id}"
                )

        processed_items.append(processed_item)

    return (
        processed_items,
        round(subtotal, 2),
        round(total_gst, 2)
    )

# =========================================================
# GENERATE ORDER NUMBER
# =========================================================

def generate_order_no():
    """Generate a unique order number: ORD-YYYY-000001."""
    now = utc_now()
    year = now.strftime("%Y")
    counter_id = f"order_no:{year}"
    counter = counters_collection.find_one_and_update(
        {"_id": counter_id},
        {
            "$inc": {"seq": 1},
            "$set": {
                "prefix": ORDER_PREFIX,
                "year": int(year),
                "updated_at": now,
            },
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return f"{ORDER_PREFIX}-{year}-{counter['seq']:06d}"


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
    data: OrderCreate,
    current_user=Depends(get_current_user)
):

    # =====================================================
    # ORDER NUMBER
    # =====================================================
    # Invoice number is intentionally NOT generated here.
    # It is generated later by the manual billing process.
    order_no = generate_order_no()

    # =====================================================
    # PURCHASE INVOICE NUMBER
    # =====================================================
    # Purchase invoice_no is the supplier/vendor invoice number
    # supplied by the client. It is never auto-generated here.
    invoice_no = None

    if data.type == "purchase":

        if not data.invoice_no or not data.invoice_no.strip():
            raise HTTPException(
                status_code=400,
                detail="invoice_no is required for purchase orders"
            )

        invoice_no = data.invoice_no.strip()

        duplicate = orders_collection.find_one({
            "type": "purchase",
            "invoice_no": invoice_no,
            "record_status": "active",
        })

        if duplicate:
            raise HTTPException(
                status_code=400,
                detail=f"Purchase invoice number {invoice_no} already exists"
            )

    elif data.invoice_no:
        raise HTTPException(
            status_code=400,
            detail="invoice_no can only be provided for purchase orders"
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
    # BRANCH
    # =====================================================

    branch_object_id = validate_branch(
        data.branch_id
    )

    # =====================================================
    # ASSIGNED EMPLOYEE
    # =====================================================

    assigned_employee_object_id = validate_assigned_employee(
        data.assigned_employee_id
    )

    # =====================================================
    # CREATED BY CURRENT USER
    # =====================================================

    created_by_object_id = validate_object_id(
        str(current_user["user_id"]),
        "user_id"
    )

    ref_invoice_id = None

    if data.ref_invoice_id:
        ref_invoice_id = validate_object_id(
            data.ref_invoice_id,
            "ref_invoice_id"
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
        data.gst_type,
        data.type
    )

    # =====================================================
    # ORDER TOTAL
    # =====================================================
    # Discount is supplied only during manual billing.
    other_charges = data.other_charges

    grand_total = (
        subtotal
        + total_gst
        + other_charges
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

    # =====================================================
    # ORDER DOCUMENT
    # =====================================================

    order_data = {

        "type":
            data.type,

        "order_no":
            order_no,

        "invoice_no":
            invoice_no,

        "vendor_id":
            vendor_object_id,

        "customer_id":
            customer_object_id,

        "warehouse_id":
            warehouse_object_id,

        "vehicle_id":
            vehicle_object_id,

        "ref_invoice_id": ref_invoice_id,

        "payment_mode":
            data.payment_mode,

        "branch_id":
            branch_object_id,

        "assigned_employee_id":
            assigned_employee_object_id,

        "created_by":
            created_by_object_id,

        "gst_type":
            data.gst_type,

        "items":
            processed_items,

        "total_cogs": 0,

        "gross_profit": 0,

        "subtotal":
            subtotal,

        "total_gst":
            round(
                total_gst,
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

        "tracking": [
            create_tracking_entry(
                status=data.status,
                user_id=str(current_user["user_id"]),
                note="Order created",
            )
        ],

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
                "order_no": {
                    "$regex": search,
                    "$options": "i",
                }
            },

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

    data: OrderUpdate,

    current_user=Depends(get_current_user)
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
    # invoice_no is not editable through the order API.
    # It is generated only by the manual billing process.

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
    # PAYMENT MODE
    # =====================================================

    if data.payment_mode is not None:

        update_data[
            "payment_mode"
        ] = data.payment_mode

    # =====================================================
    # BRANCH
    # =====================================================

    if data.branch_id is not None:

        update_data[
            "branch_id"
        ] = validate_branch(
            data.branch_id
        )

    # =====================================================
    # ASSIGNED EMPLOYEE
    # =====================================================

    if data.assigned_employee_id is not None:

        update_data[
            "assigned_employee_id"
        ] = validate_assigned_employee(
            data.assigned_employee_id
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
            gst_type,
            existing_order.get(
                "type"
            )

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
    # Discount is not editable through the order API.
    # It is supplied only by manual billing.

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
    )

    if grand_total < 0:

        raise HTTPException(
            status_code=400,
            detail=(
                "Grand total cannot be negative"
            )
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
    # STATUS TRACKING
    # =====================================================
    current_status = existing_order.get(
        "status",
        "Pending"
    )

    status_changed = (
        data.status is not None
        and data.status != current_status
    )

    update_operation = {
        "$set": update_data
    }

    if status_changed:
        tracking_entry = create_tracking_entry(
            status=data.status,
            user_id=str(current_user["user_id"]),
            note=(
                f"Order status changed from "
                f"{current_status} to {data.status}"
            ),
        )
        update_operation["$push"] = {
            "tracking": tracking_entry
        }

    # UPDATE DATABASE
    # =====================================================
    update_filter = {
        "_id": order_object_id
    }

    if status_changed:
        update_filter["status"] = current_status

    result = orders_collection.update_one(
        update_filter,
        update_operation
    )

    if status_changed and result.modified_count == 0:
        raise HTTPException(
            status_code=409,
            detail=(
                "Order status was changed by another request. "
                "Please refresh and try again."
            )
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
# BILLING PDF HELPERS
# =========================================================

def _pdf_money(value):
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _pdf_text(value, default=""):
    if value is None:
        return default
    if isinstance(value, dict):
        return ", ".join(
            str(v) for v in value.values()
            if v not in (None, "")
        )
    return str(value)


def _pdf_customer_address(customer):
    if not customer:
        return ""

    address = customer.get("billing_address") or customer.get("shipping_address")
    if isinstance(address, dict):
        parts = []
        for key in ("address", "line1", "line2", "street", "area", "city", "district", "state", "pincode", "postal_code"):
            value = address.get(key)
            if value not in (None, ""):
                parts.append(str(value))
        if parts:
            return ", ".join(parts)

    return _pdf_text(address or customer.get("location"), "")


def _pdf_customer_state(customer):
    if not customer:
        return "", ""

    address = customer.get("billing_address") or customer.get("shipping_address")
    location = customer.get("location")

    for source in (customer, address, location):
        if isinstance(source, dict):
            state = source.get("state") or source.get("state_name")
            code = source.get("state_code") or source.get("gst_state_code")
            if state or code:
                return str(state or ""), str(code or "")

    return "", ""


def _build_gst_rows(order):
    """Aggregate GST by rate for the invoice tax summary."""
    grouped = {}

    for item in order.get("items", []) or []:
        rate = _pdf_money(item.get("gst_percent"))
        taxable = _pdf_money(item.get("taxable_amount"))
        gst_amount = _pdf_money(item.get("gst_amount"))
        key = rate
        grouped.setdefault(key, {"taxable": 0.0, "gst": 0.0})
        grouped[key]["taxable"] += taxable
        grouped[key]["gst"] += gst_amount

    rows = []
    for rate in sorted(grouped):
        taxable = round(grouped[rate]["taxable"], 2)
        gst_amount = round(grouped[rate]["gst"], 2)
        rows.append({
            "rate": rate,
            "taxable": taxable,
            "gst": gst_amount,
        })
    return rows



# Define IST timezone helper
IST = timezone(timedelta(hours=5, minutes=30))

# Helper functions required by the invoice generator
def _pdf_customer_address(customer):
    addr = customer.get("billing_address") or customer.get("shipping_address") or {}
    if isinstance(addr, dict):
        parts = [
            addr.get("address"),
            addr.get("city"),
            addr.get("state"),
            addr.get("pincode")
        ]
        return ", ".join([str(p) for p in parts if p])
    return str(addr)

def _pdf_customer_state(customer):
    addr = customer.get("billing_address") or customer.get("shipping_address") or {}
    state = addr.get("state") if isinstance(addr, dict) else ""
    # Map state to code if needed, default to Madhya Pradesh (23) if matching
    code = "23" if state and "madhya" in state.lower() else ""
    return state, code

def _pdf_money(val):
    try:
        return round(float(val), 2)
    except (TypeError, ValueError):
        return 0.00

def _build_gst_rows(order):
    rows = []
    for item in order.get("items", []):
        taxable = float(item.get("taxable_amount", 0))
        rate = float(item.get("gst_percent", 0))
        gst_amt = float(item.get("gst_amount", 0))
        if taxable > 0:
            rows.append({"rate": rate, "taxable": taxable, "gst": gst_amt})
    return rows


def generate_invoice_pdf(order):
    """Generate the invoice PDF in memory and return PDF bytes."""

    customer = order.get("customer") or {}
    supplier_gst = os.getenv("SUPPLIER_GSTIN", "")
    supplier_state = os.getenv("SUPPLIER_STATE", "Madhya Pradesh")
    supplier_code = os.getenv("SUPPLIER_STATE_CODE", "23")

    customer_name = (
        customer.get("shop_name")
        or customer.get("owner")
        or customer.get("name")
        or "Walk-in Customer"
    )
    customer_address = _pdf_customer_address(customer)
    customer_gst = customer.get("gst_number") or customer.get("gstin") or ""
    customer_state, customer_state_code = _pdf_customer_state(customer)

    if not customer_state:
        customer_state = ""

    is_inter_state = bool(
        customer_state_code
        and supplier_code
        and str(customer_state_code) != str(supplier_code)
    )

    invoice_no = order.get("invoice_no") or "NA"
    invoice_date = order.get("billed_at") or order.get("created_at")
    if isinstance(invoice_date, datetime):
        invoice_date = invoice_date.astimezone(timezone.utc).astimezone(IST).strftime("%d-%m-%Y")
    elif isinstance(invoice_date, str) and invoice_date:
        try:
            dt = datetime.fromisoformat(invoice_date.replace("Z", "+00:00"))
            invoice_date = dt.astimezone(IST).strftime("%d-%m-%Y")
        except ValueError:
            invoice_date = invoice_date[:10]
    else:
        invoice_date = str(invoice_date or "")

    subtotal = _pdf_money(order.get("subtotal"))
    total_gst = _pdf_money(order.get("total_gst"))
    other_charges = _pdf_money(order.get("other_charges"))
    discount = _pdf_money(order.get("discount"))
    previous_balance = _pdf_money(
        order.get("previous_balance", order.get("previousBalance", 0))
    )
    gross_bill = round(subtotal + total_gst + other_charges, 2)
    bill_amount = _pdf_money(order.get("grand_total", gross_bill - discount))
    total_due = round(bill_amount + previous_balance, 2)
    words = num2words(int(round(total_due)), lang="en_IN").title()

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    margin = 40

    # -----------------------------------------------------
    # HEADER & SUPPLIER
    # -----------------------------------------------------
    c.setFont("Helvetica-Bold", 14)
    c.drawString(margin, height - 50, "SADAPOORNA TRADERS")
    c.setFont("Helvetica", 8)
    c.setFillGray(0.3)
    c.drawString(margin, height - 62, "LIG B-301, E-7, Arera Colony, Bhopal (MP) 462016")
    c.drawString(
        margin,
        height - 72,
        f"GSTIN: {supplier_gst} | State: {supplier_state} ({supplier_code})"
    )

    logo_path = os.getenv("INVOICE_LOGO_PATH", "logo.png")
    if os.path.exists(logo_path):
        c.drawImage(
            logo_path,
            width - margin - 100,
            height - 65,
            width=80,
            height=50,
            preserveAspectRatio=True,
            mask="auto",
        )

    c.drawRightString(width - margin, height - 62, "Mob: 9977233055, 7553524977")
    c.line(margin, height - 80, width - margin, height - 80)

    # -----------------------------------------------------
    # CUSTOMER / PLACE OF SUPPLY
    # -----------------------------------------------------
    y_meta = height - 105
    c.setFillGray(0)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(margin, y_meta, "BILL TO / PLACE OF SUPPLY:")
    c.setFont("Helvetica", 9)
    c.drawString(margin, y_meta - 12, customer_name)
    c.setFont("Helvetica", 8)
    c.drawString(margin, y_meta - 22, customer_address[:90])
    c.drawString(margin, y_meta - 32, f"GSTIN: {customer_gst}")
    c.drawString(margin, y_meta - 42, f"State: {customer_state}")

    c.drawRightString(width - margin, y_meta, f"Invoice No: #{invoice_no}")
    c.drawRightString(width - margin, y_meta - 12, f"Date: {invoice_date}")

    # -----------------------------------------------------
    # ITEMS TABLE
    # -----------------------------------------------------
    y_table = y_meta - 65
    table_rows = [["#", "Description", "HSN", "units", "Qty", "Rate", "Amount"]]

    for i, item in enumerate(order.get("items", []) or [], 1):
        product_name = item.get("product_name") or (item.get("product", {}).get("name") if isinstance(item.get("product"), dict) else None)
        variant_name = item.get("variant_name") or (item.get("variant", {}).get("name") if isinstance(item.get("variant"), dict) else None)
        description = product_name or variant_name or item.get("description") or "Item"
        if product_name and variant_name and variant_name != product_name:
            description = f"{product_name} - {variant_name}"

        # Safely extract unit whether it's stored as a dictionary or a string
        raw_unit = item.get("billingUnit") or item.get("unit") or item.get("unit_name") or ""
        if isinstance(raw_unit, dict):
            unit = raw_unit.get("symbol") or raw_unit.get("name") or ""
        else:
            unit = str(raw_unit)

        billing_qty = item.get("billingQty", item.get("quantity", 0))
        qty = item.get("quantity", 0)
        rate = _pdf_money(item.get("rate"))
        amount = _pdf_money(item.get("total_amount", item.get("amount", item.get("taxable_amount", 0))))
        hsn = item.get("hsn") or item.get("hsn_code") or "-"
        
        weight_type = item.get("weightagetype") or item.get("weightage_type") or unit or ""
        rate_unit = item.get("rateUnit") or unit or "unit"

        table_rows.append([
            i,
            str(description),
            str(hsn),
            f"{billing_qty} {unit}".strip(),
            f"{qty} {weight_type}".strip(),
            f"{rate:.2f}/- per {rate_unit}",
            f"{amount:.2f}",
        ])

    table_rows.append(["", "", "", "", "", "Total:", f"{gross_bill:.2f}"])

    table = Table(
        table_rows,
        colWidths=[20, 170, 60, 55, 60, 60, 90]
    )
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#333333")),
        ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.1, colors.lightgrey),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    tw, th = table.wrapOn(c, width, height)
    table.drawOn(c, margin, y_table - th)

    # -----------------------------------------------------
    # GST SUMMARY
    # -----------------------------------------------------
    y_gst = y_table - th - 25
    c.setFont("Helvetica-Bold", 8)
    c.drawString(margin, y_gst, "GST TAX SUMMARY")

    gst_rows = _build_gst_rows(order)
    gst_table_rows = []
    if is_inter_state:
        gst_table_rows.append(["Tax Rate", "Taxable Value", "IGST", "Total Tax"])
        for row in gst_rows:
            gst_table_rows.append([
                f"{row['rate']:.2f}%",
                f"{row['taxable']:.2f}",
                f"{row['gst']:.2f}",
                f"{row['gst']:.2f}",
            ])
        if not gst_rows:
            gst_table_rows.append(["0%", f"{subtotal:.2f}", "0.00", "0.00"])
        col_w = [100, 110, 100, 110]
    else:
        gst_table_rows.append(["Tax Rate", "Taxable Value", "CGST", "SGST", "Total Tax"])
        for row in gst_rows:
            half = round(row["gst"] / 2, 2)
            gst_table_rows.append([
                f"{row['rate']:.2f}%",
                f"{row['taxable']:.2f}",
                f"{half:.2f}",
                f"{half:.2f}",
                f"{row['gst']:.2f}",
            ])
        if not gst_rows:
            gst_table_rows.append(["0%", f"{subtotal:.2f}", "0.00", "0.00", "0.00"])
        col_w = [70, 95, 75, 75, 105]

    gst_table = Table(gst_table_rows, colWidths=col_w)
    gst_table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("GRID", (0, 0), (-1, -1), 0.1, colors.grey),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
    ]))
    gw, gh = gst_table.wrapOn(c, width, height)
    gst_table.drawOn(c, margin, y_gst - gh - 5)

    # -----------------------------------------------------
    # TOTALS & BALANCE
    # -----------------------------------------------------
    y_fin = y_gst - gh - 35
    c.setFont("Helvetica", 9)
    c.drawRightString(width - 140, y_fin, "Current Bill Amount:")
    c.drawRightString(width - margin, y_fin, f"₹{gross_bill:.2f}")

    offset = 0
    if discount > 0:
        offset += 15
        c.setFillGray(0.4)
        c.drawRightString(width - 140, y_fin - offset, "Discount (-):")
        c.drawRightString(width - margin, y_fin - offset, f"₹{discount:.2f}")
        c.setFillGray(0)

    offset += 15
    if other_charges > 0:
        c.drawRightString(width - 140, y_fin - offset, "Other Charges (+):")
        c.drawRightString(width - margin, y_fin - offset, f"₹{other_charges:.2f}")
        offset += 15

    c.drawRightString(width - 140, y_fin - offset, "Previous Balance:")
    c.drawRightString(width - margin, y_fin - offset, f"₹{previous_balance:.2f}")
    c.line(width - 160, y_fin - offset - 7, width - margin, y_fin - offset - 7)

    offset += 20
    c.setFont("Helvetica-Bold", 11)
    c.drawRightString(width - 140, y_fin - offset, "Total Balance Due:")
    c.drawRightString(width - margin, y_fin - offset, f"₹{total_due:.2f}")
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(margin, y_fin - offset, f"Amount in words: INR {words} Only")

    # -----------------------------------------------------
    # BANK DETAILS
    # -----------------------------------------------------
    y_bank = y_fin - offset - 45
    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin, y_bank, "Bank Details:")
    c.setFont("Helvetica", 8)
    c.drawString(
        margin,
        y_bank - 10,
        "Indian Overseas Bank | Arera Colony, Bhopal | A/C: 372802000000555 | IFSC: IOBA0003728"
    )

    # -----------------------------------------------------
    # TERMS & SIGNATURE
    # -----------------------------------------------------
    y_terms = y_bank - 40
    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin, y_terms, "Terms & Conditions:")
    c.setFont("Helvetica", 8)
    terms = [
        "1. Not responsible after goods despatched.",
        "2. Interest @24% P.A. after 7 days.",
        "3. Payment on demand.",
        "4. (L) is for Loose Packing.",
    ]
    for i, line in enumerate(terms):
        c.drawString(margin, y_terms - 10 - (i * 9), line)

    sign_path = os.getenv("INVOICE_SIGN_PATH", "sign.png")
    if os.path.exists(sign_path):
        c.drawImage(
            sign_path,
            width - margin - 80,
            margin + 40,
            width=80,
            height=50,
            preserveAspectRatio=True,
            mask="auto",
        )
    c.drawRightString(width - margin, margin + 35, "For SADAPOORNA TRADERS")
    c.setFont("Helvetica", 7)
    c.drawRightString(width - margin, margin + 25, "Authorized Signatory")

    c.line(margin, margin + 15, width - margin, margin + 15)
    c.setFont("Helvetica-Oblique", 6.5)
    c.setFillGray(0.4)
    c.drawCentredString(
        width / 2,
        margin + 5,
        "* This is a computer-generated Bill and managed by Duniyape Technologies"
    )

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.getvalue()

    # access_token = os.getenv("WHATSAPP_ACCESS_TOKEN")
    # phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    # api_version = os.getenv("WHATSAPP_API_VERSION", "v22.0")

access_token = os.getenv("WHATSAPP_ACCESS_TOKEN", 'EAA6rtuUkSgIBOw1ZBKc0daGfX8SSbt86QetCckUtCodtMy2ZA44d9e0nrEUhZAsxaroHpX1217ROdLpkDRD1RwKa0VWMzgy5eMfIBv4WN1CYhXnAfXx7psCzgZB2xJkEZABscWDYYsKRwBHXMnfBdT905ZCLklGOnXS8tCaqsDGpoK7s5XlkOxgh4udFz67qw5aQZDZD')
phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", '670517682822062')
api_version = os.getenv("WHATSAPP_API_VERSION", "v22.0")

def send_invoice_template_whatsapp(order):
    """Send the approved WhatsApp template notification when an invoice is generated or resent."""
    
    

    customer = order.get("customer") or {}
    raw_mobile = customer.get("mobile", "919131037870")
    
    # Clean phone number (ensure E.164 format with '91' prefix)
    clean_number = "".join(filter(str.isdigit, str(raw_mobile)))
    if not clean_number.startswith("91"):
        clean_number = f"91{clean_number}"

    # Extract dynamic parameters matching your JS implementation
    customer_name = str(customer.get("shop") or customer.get("owner") or customer.get("name") or "Unknown").strip()
    order_id = str(order.get("order_no") or order.get("id") or "")
    invoice_no = str(order.get("invoice_no") or "")
    grand_total = str(order.get("grand_total") or "0")

    payload = {
        "messaging_product": "whatsapp",
        "to": clean_number,
        "type": "template",
        "template": {
            "name": "bill_ready_reply",
            "language": {
                "code": "en"
            },
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": customer_name},                          # {{1}}
                        {"type": "text", "text": order_id},                                # {{2}}
                        {"type": "text", "text": invoice_no},                              # {{3}}
                        {"type": "text", "text": grand_total},                             # {{4}}
                        {"type": "text", "text": " "}                                      # {{5}}
                    ]
                },
                {
                    "type": "button",
                    "sub_type": "quick_reply",
                    "index": "0",
                    "parameters": [
                        {
                            "type": "payload",
                            "payload": f"download_invoice_{order.get('id')}"
                        }
                    ]
                }
            ]
        }
    }

    url = f"https://graph.googleapis.com/v22.0/{phone_number_id}/messages" if False else f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
    
    try:
        response = requests.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
        
    except requests.exceptions.HTTPError as e:
        error_details = e.response.json() if e.response else str(e)
        print(f"WhatsApp Template API Error: {error_details}")
        raise HTTPException(
            status_code=400,
            detail=f"WhatsApp template sending failed: {error_details}"
        )


def send_invoice_whatsapp(pdf_bytes, invoice_no, from_number):
    """Upload and send the generated PDF through WhatsApp Cloud API."""

   

    if not access_token or not phone_number_id:
        raise HTTPException(
            status_code=500,
            detail="WhatsApp configuration is missing on the backend"
        )

    upload_url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/media"
    upload_files = {
        "file": (f"Invoice_{invoice_no}.pdf", io.BytesIO(pdf_bytes), "application/pdf")
    }
    upload_data = {
        "messaging_product": "whatsapp",
        "type": "application/pdf",
    }

    upload_resp = requests.post(
        upload_url,
        headers={"Authorization": f"Bearer {access_token}"},
        files=upload_files,
        data=upload_data,
        timeout=30,
    )
    upload_resp.raise_for_status()
    media_id = upload_resp.json()["id"]

    send_url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
    send_resp = requests.post(
        send_url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json={
            "messaging_product": "whatsapp",
            "to": from_number,
            "type": "document",
            "document": {
                "id": media_id,
                "filename": f"Invoice_{invoice_no}.pdf",
            },
        },
        timeout=30,
    )
    send_resp.raise_for_status()
    return send_resp.json()


# =========================================================
# GET PURCHASE BATCHES FOR PRODUCT / VARIANT / WAREHOUSE
# =========================================================

def get_purchase_batches(
    product_id,
    variant_id,
    warehouse_id
):
    """
    Purchase order items are treated as stock batches.

    FIFO:
        oldest purchase first
    """

    pipeline = [
        {
            "$match": {
                "type": "purchase",
                "status": "Completed",
                "record_status": "active",
                "warehouse_id": warehouse_id
            }
        },
        {
            "$unwind": "$items"
        },
        {
            "$match": {
                "items.product_id": product_id,
                "items.variant_id": variant_id
            }
        },
        {
            "$sort": {
                "created_at": 1,
                "_id": 1
            }
        }
    ]

    return list(
        orders_collection.aggregate(pipeline)
    )


# =========================================================
# GET CONSUMED QUANTITY FROM BATCH
# =========================================================

def get_batch_consumed_quantity(
    purchase_order_id,
    purchase_item_id
):
    """
    Finds how much of a purchase item has already
    been consumed by sales.
    """

    pipeline = [
        {
            "$match": {
                "type": "sale",
                "record_status": "active",
                "status": "Delivered"
            }
        },
        {
            "$unwind": "$items"
        },
        {
            "$unwind": {
                "path": "$items.batch_consumptions",
                "preserveNullAndEmptyArrays": False
            }
        },
        {
            "$match": {
                "items.batch_consumptions.purchase_order_id":
                    purchase_order_id,

                "items.batch_consumptions.purchase_item_id":
                    purchase_item_id
            }
        },
        {
            "$group": {
                "_id": None,
                "total": {
                    "$sum":
                        "$items.batch_consumptions.quantity"
                }
            }
        }
    ]

    result = list(
        orders_collection.aggregate(pipeline)
    )

    if not result:
        return 0

    return float(result[0]["total"])


# =========================================================
# GET PURCHASE RETURN QUANTITY
# =========================================================

def get_purchase_return_quantity(
    purchase_order_id,
    purchase_item_id
):
    """
    Purchase returns reduce the available quantity
    of the original purchase batch.
    """

    pipeline = [
        {
            "$match": {
                "type": "purchase_return",
                "record_status": "active"
            }
        },
        {
            "$unwind": "$items"
        },
        {
            "$match": {
                "ref_invoice_id":
                    purchase_order_id,

                "items.product_id":
                    None
            }
        }
    ]

    # We don't use the above generic pipeline because
    # ref_invoice_id may be stored as the purchase _id
    # and item-level matching is handled below.

    pipeline = [
        {
            "$match": {
                "type": "purchase_return",
                "record_status": "active",
                "ref_invoice_id": purchase_order_id
            }
        },
        {
            "$unwind": "$items"
        },
        {
            "$match": {
                "items.ref_purchase_item_id":
                    purchase_item_id
            }
        },
        {
            "$group": {
                "_id": None,
                "total": {
                    "$sum": "$items.quantity"
                }
            }
        }
    ]

    result = list(
        orders_collection.aggregate(pipeline)
    )

    if not result:
        return 0

    return float(result[0]["total"])

# =========================================================
# ALLOCATE STOCK USING FIFO
# =========================================================

def allocate_fifo(
    product_id,
    variant_id,
    warehouse_id,
    required_quantity
):
    """
    Allocate required quantity from purchase orders
    using FIFO.

    Returns:
        batch_consumptions
        total_cogs
    """

    required_quantity = float(
        required_quantity
    )

    if required_quantity <= 0:
        return [], 0

    batches = get_purchase_batches(
        product_id=product_id,
        variant_id=variant_id,
        warehouse_id=warehouse_id
    )

    remaining_required = required_quantity

    consumptions = []

    total_cogs = 0

    for batch in batches:

        purchase_item = batch["items"]

        purchase_order_id = batch["_id"]

        purchase_item_id = (
            purchase_item["item_id"]
        )

        purchased_qty = float(
            purchase_item["quantity"]
        )

        consumed_qty = get_batch_consumed_quantity(
            purchase_order_id,
            purchase_item_id
        )

        returned_qty = get_purchase_return_quantity(
            purchase_order_id,
            purchase_item_id
        )

        available_qty = (
            purchased_qty
            - consumed_qty
            - returned_qty
        )

        if available_qty <= 0:
            continue

        consume_qty = min(
            available_qty,
            remaining_required
        )

        purchase_rate = float(
            purchase_item["rate"]
        )

        cost = (
            consume_qty
            * purchase_rate
        )

        consumptions.append({
            "purchase_order_id":
                purchase_order_id,

            "purchase_item_id":
                purchase_item_id,

            "quantity":
                consume_qty,

            "purchase_rate":
                purchase_rate,

            "cost":
                round(cost, 2)
        })

        total_cogs += cost

        remaining_required -= consume_qty

        if remaining_required <= 0:
            break

    if remaining_required > 0:

        available = (
            required_quantity
            - remaining_required
        )

        raise HTTPException(
            status_code=400,
            detail=(
                f"Insufficient stock for product "
                f"{product_id}, variant {variant_id}. "
                f"Required: {required_quantity}, "
                f"Available: {available}"
            )
        )

    return (
        consumptions,
        round(total_cogs, 2)
    )


# =========================================================
# MANUAL BILLING
# POST /orders/billing/v1/{order_id}
#
# Supports:
#   1. sale
#   2. sale_return
#
# SALE:
#   Purchase batches -> FIFO -> Sale
#
# SALES RETURN:
#   Sale batch_consumptions -> restore stock
#
# PDF is NOT returned from this API.
# =========================================================

@router.post(
    "/billing/v1/{order_id}"
)
def manual_bill_order(
    order_id: str,
    data: ManualBillingRequest,
    current_user=Depends(get_current_user)
):

    # =====================================================
    # VALIDATE ORDER ID
    # =====================================================

    try:
        order_object_id = ObjectId(order_id)

    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid order MongoDB ID"
        )

    # =====================================================
    # GET ORDER
    # =====================================================

    order = orders_collection.find_one({
        "_id": order_object_id
    })

    if not order:
        raise HTTPException(
            status_code=404,
            detail="Order not found"
        )

    order_type = order.get("type")

    # =====================================================
    # BILLING ONLY FOR SALE / SALES RETURN
    # =====================================================

    if order_type not in [
        "sale",
        "sale_return"
    ]:
        raise HTTPException(
            status_code=400,
            detail=(
                "Manual billing is allowed only for "
                "sale and sale_return orders"
            )
        )

    # =====================================================
    # CHECK RECORD STATUS
    # =====================================================

    if order.get(
        "record_status",
        "active"
    ) != "active":

        raise HTTPException(
            status_code=400,
            detail="Inactive orders cannot be billed"
        )

    # =====================================================
    # CHECK ALREADY BILLED
    # =====================================================

    if order.get("invoice_no"):

        raise HTTPException(
            status_code=400,
            detail=(
                f"Order {order_id} is already billed with "
                f"invoice {order['invoice_no']}"
            )
        )

    # =====================================================
    # SALES RETURN VALIDATION
    # =====================================================

    original_sale = None

    if order_type == "sale_return":

        ref_invoice_id = order.get("ref_invoice_id")

        if not ref_invoice_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    "ref_invoice_id is required "
                    "for sales return"
                )
            )

        # -------------------------------------------------
        # GET ORIGINAL SALE
        # -------------------------------------------------

        original_sale = orders_collection.find_one({
            "_id": ref_invoice_id,
            "type": "sale",
            "record_status": "active"
        })

        if not original_sale:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Original sale order referenced by "
                    "ref_invoice_id was not found"
                )
            )

        # -------------------------------------------------
        # ORIGINAL SALE MUST BE BILLED
        # -------------------------------------------------

        if not original_sale.get("invoice_no"):

            raise HTTPException(
                status_code=400,
                detail=(
                    "Original sale must be billed "
                    "before it can be returned"
                )
            )

    # =====================================================
    # CALCULATE BILL
    # =====================================================

    subtotal = _pdf_money(
        order.get("subtotal")
    )

    total_gst = _pdf_money(
        order.get("total_gst")
    )

    other_charges = _pdf_money(
        order.get("other_charges")
    )

    # =====================================================
    # DISCOUNT
    # =====================================================

    discount = _pdf_money(
        data.discount_amount
    )

    if discount < 0:

        raise HTTPException(
            status_code=400,
            detail="Discount cannot be negative"
        )

    # =====================================================
    # GROSS TOTAL
    # =====================================================

    gross_total = round(
        subtotal +
        total_gst +
        other_charges,
        2
    )

    # =====================================================
    # DISCOUNT VALIDATION
    # =====================================================

    if discount > gross_total:

        raise HTTPException(
            status_code=400,
            detail=(
                "Discount cannot be greater than "
                "the bill amount"
            )
        )

    # =====================================================
    # GRAND TOTAL
    # =====================================================

    grand_total = round(
        gross_total - discount,
        2
    )

    # =====================================================
    # COGS / FIFO
    # =====================================================

    total_cogs = 0

    # =====================================================
    # SALE
    #
    # Sale billing consumes purchase batches FIFO.
    # =====================================================

    if order_type == "sale":

        for item in order.get("items", []):

            product_id = item.get("product_id")
            variant_id = item.get("variant_id")

            quantity = float(
                item.get("quantity", 0)
            )

            if not product_id or not variant_id:

                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Invalid product/variant "
                        "in sale item"
                    )
                )

            if quantity <= 0:

                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Sale quantity must be "
                        "greater than zero"
                    )
                )

            # -------------------------------------------------
            # FIFO ALLOCATION
            # -------------------------------------------------

            batch_consumptions, item_cogs = allocate_fifo(
                product_id=product_id,
                variant_id=variant_id,
                warehouse_id=order.get(
                    "warehouse_id"
                ),
                required_quantity=quantity
            )

            # -------------------------------------------------
            # SAVE FIFO INFORMATION IN SALE ITEM
            # -------------------------------------------------

            item["batch_consumptions"] = (
                batch_consumptions
            )

            item["cogs"] = round(
                item_cogs,
                2
            )

            total_cogs += item_cogs

    # =====================================================
    # SALES RETURN
    #
    # Restore the exact batches consumed by original sale.
    # =====================================================

    elif order_type == "sale_return":

        for item in order.get("items", []):

            product_id = item.get("product_id")
            variant_id = item.get("variant_id")

            quantity = float(
                item.get("quantity", 0)
            )

            ref_item_id = item.get(
                "ref_item_id"
            )

            # -------------------------------------------------
            # VALIDATE RETURN ITEM
            # -------------------------------------------------

            if not product_id or not variant_id:

                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Invalid product/variant "
                        "in sales return item"
                    )
                )

            if quantity <= 0:

                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Sales return quantity must "
                        "be greater than zero"
                    )
                )

            if not ref_item_id:

                raise HTTPException(
                    status_code=400,
                    detail=(
                        "ref_item_id is required "
                        "for every sales return item"
                    )
                )

            # -------------------------------------------------
            # FIND ORIGINAL SALE ITEM
            # -------------------------------------------------

            original_sale_item = None

            for sale_item in original_sale.get(
                "items",
                []
            ):

                if str(
                    sale_item.get("item_id")
                ) == str(ref_item_id):

                    original_sale_item = sale_item
                    break

            if not original_sale_item:

                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Original sale item "
                        f"{ref_item_id} not found"
                    )
                )

            # -------------------------------------------------
            # MAKE SURE PRODUCT / VARIANT MATCH
            # -------------------------------------------------

            if str(
                original_sale_item.get(
                    "product_id"
                )
            ) != str(product_id):

                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Return product does not match "
                        "original sale item"
                    )
                )

            if str(
                original_sale_item.get(
                    "variant_id"
                )
            ) != str(variant_id):

                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Return variant does not match "
                        "original sale item"
                    )
                )

            original_quantity = float(
                original_sale_item.get(
                    "quantity",
                    0
                )
            )

            # -------------------------------------------------
            # CALCULATE PREVIOUSLY RETURNED QUANTITY
            # -------------------------------------------------

            previously_returned = 0

            previous_returns = orders_collection.find({
                "type": "sale_return",
                "ref_invoice_id": original_sale["_id"],
                "record_status": "active",
                "invoice_no": {
                    "$exists": True,
                    "$ne": None
                }
            })

            for previous_return in previous_returns:

                for return_item in previous_return.get(
                    "items",
                    []
                ):

                    if str(
                        return_item.get(
                            "ref_item_id"
                        )
                    ) == str(ref_item_id):

                        previously_returned += float(
                            return_item.get(
                                "quantity",
                                0
                            )
                        )

            # -------------------------------------------------
            # AVAILABLE FOR RETURN
            # -------------------------------------------------

            available_for_return = round(
                original_quantity -
                previously_returned,
                6
            )

            if quantity > available_for_return:

                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Return quantity {quantity} "
                        f"cannot exceed remaining "
                        f"returnable quantity "
                        f"{available_for_return}"
                    )
                )

            # =================================================
            # RESTORE ORIGINAL SALE BATCHES
            # =================================================

            original_batches = (
                original_sale_item.get(
                    "batch_consumptions",
                    []
                )
            )

            if not original_batches:

                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Original sale item does not "
                        "contain FIFO batch information"
                    )
                )

            # -------------------------------------------------
            # DETERMINE HOW MUCH OF EACH BATCH WAS ALREADY
            # RETURNED
            # -------------------------------------------------

            already_returned_by_batch = {}

            previous_returns = orders_collection.find({
                "type": "sale_return",
                "ref_invoice_id": original_sale["_id"],
                "record_status": "active",
                "invoice_no": {
                    "$exists": True,
                    "$ne": None
                }
            })

            for previous_return in previous_returns:

                for return_item in previous_return.get(
                    "items",
                    []
                ):

                    if str(
                        return_item.get(
                            "ref_item_id"
                        )
                    ) != str(ref_item_id):

                        continue

                    for batch in return_item.get(
                        "batch_consumptions",
                        []
                    ):

                        purchase_order_id = str(
                            batch.get(
                                "purchase_order_id"
                            )
                        )

                        purchase_item_id = str(
                            batch.get(
                                "purchase_item_id"
                            )
                        )

                        batch_key = (
                            purchase_order_id,
                            purchase_item_id
                        )

                        already_returned_by_batch[
                            batch_key
                        ] = (
                            already_returned_by_batch.get(
                                batch_key,
                                0
                            )
                            +
                            float(
                                batch.get(
                                    "quantity",
                                    0
                                )
                            )
                        )

            # -------------------------------------------------
            # ALLOCATE RETURN AGAINST ORIGINAL BATCHES
            # -------------------------------------------------

            remaining_return_quantity = quantity
            return_batch_consumptions = []
            item_cogs = 0

            for batch in original_batches:

                if remaining_return_quantity <= 0:
                    break

                purchase_order_id = batch.get(
                    "purchase_order_id"
                )

                purchase_item_id = batch.get(
                    "purchase_item_id"
                )

                purchase_rate = float(
                    batch.get(
                        "purchase_rate",
                        0
                    )
                )

                original_batch_quantity = float(
                    batch.get(
                        "quantity",
                        0
                    )
                )

                batch_key = (
                    str(purchase_order_id),
                    str(purchase_item_id)
                )

                already_returned = float(
                    already_returned_by_batch.get(
                        batch_key,
                        0
                    )
                )

                available_batch_quantity = round(
                    original_batch_quantity -
                    already_returned,
                    6
                )

                if available_batch_quantity <= 0:
                    continue

                restore_quantity = min(
                    available_batch_quantity,
                    remaining_return_quantity
                )

                restore_quantity = round(
                    restore_quantity,
                    6
                )

                batch_cost = round(
                    restore_quantity *
                    purchase_rate,
                    2
                )

                return_batch_consumptions.append({
                    "purchase_order_id": (
                        purchase_order_id
                    ),
                    "purchase_item_id": (
                        purchase_item_id
                    ),
                    "quantity": restore_quantity,
                    "purchase_rate": purchase_rate,
                    "cost": batch_cost
                })

                item_cogs += batch_cost

                remaining_return_quantity = round(
                    remaining_return_quantity -
                    restore_quantity,
                    6
                )

            # -------------------------------------------------
            # SAFETY CHECK
            # -------------------------------------------------

            if remaining_return_quantity > 0:

                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Unable to map the complete "
                        "return quantity to original "
                        "sale batches"
                    )
                )

            # -------------------------------------------------
            # SAVE RETURN BATCH INFORMATION
            # -------------------------------------------------

            item["batch_consumptions"] = (
                return_batch_consumptions
            )

            item["cogs"] = round(
                item_cogs,
                2
            )

            total_cogs += item_cogs

    # =====================================================
    # FINAL COGS
    # =====================================================

    total_cogs = round(
        total_cogs,
        2
    )

    # =====================================================
    # NET SALES
    # =====================================================

    net_sales = round(
        subtotal - discount,
        2
    )

    # =====================================================
    # GROSS PROFIT
    # =====================================================

    gross_profit = round(
        net_sales - total_cogs,
        2
    )

    # =====================================================
    # GENERATE INVOICE NUMBER
    #
    # Separate series:
    #
    # SALE:
    #   INV-2026-09-xxxx
    #
    # SALES RETURN:
    #   RET-2026-09-xxxx
    #
    # Assuming generate_invoice_no() supports the type.
    # =====================================================

    invoice_no = generate_invoice_no(
        order_type
    )

    now = utc_now()

    # =====================================================
    # BILLING TRACKING
    # =====================================================

    billing_tracking = create_tracking_entry(
        status=order.get(
            "status",
            "Pending"
        ),
        user_id=str(
            current_user["user_id"]
        ),
        note=(
            f"{order_type} billing completed. "
            f"Invoice {invoice_no}. "
            f"Discount applied: {discount:.2f}"
        ),
    )

    # =====================================================
    # ATOMIC BILLING UPDATE
    # =====================================================

    result = orders_collection.update_one(
        {
            "_id": order["_id"],
            "type": order_type,
            "record_status": "active",
            "$or": [
                {"invoice_no": None},
                {
                    "invoice_no": {
                        "$exists": False
                    }
                },
                {"invoice_no": ""},
            ],
        },
        {
            "$set": {
                "invoice_no": invoice_no,

                "discount": discount,

                "grand_total": grand_total,

                # -----------------------------------------
                # FIFO / COGS
                # -----------------------------------------

                "items": order["items"],

                "total_cogs": total_cogs,

                "gross_profit": gross_profit,

                # -----------------------------------------
                # BILLING
                # -----------------------------------------

                "billed_at": now,

                "billed_by": validate_object_id(
                    str(
                        current_user["user_id"]
                    ),
                    "user_id"
                ),

                # -----------------------------------------
                # WHATSAPP
                # -----------------------------------------

                "invoice_whatsapp_sent": False,

                "invoice_whatsapp_status": "pending",

                "updated_at": now,
            },

            "$push": {
                "tracking": billing_tracking
            }
        }
    )

    # =====================================================
    # HANDLE ATOMIC UPDATE FAILURE
    # =====================================================

    if result.modified_count == 0:

        latest = orders_collection.find_one({
            "_id": order["_id"]
        })

        if latest and latest.get(
            "invoice_no"
        ):

            raise HTTPException(
                status_code=409,
                detail=(
                    f"Order {order_id} was already "
                    f"billed with invoice "
                    f"{latest['invoice_no']}"
                )
            )

        raise HTTPException(
            status_code=409,
            detail=(
                "Order could not be billed because "
                "it was modified by another request"
            )
        )

    # =====================================================
    # GET ACTUAL BILLED ORDER
    # =====================================================

    updated_order = orders_collection.find_one({
        "_id": order["_id"]
    })

    if not updated_order:

        raise HTTPException(
            status_code=500,
            detail=(
                f"Invoice {invoice_no} was created, "
                "but billed order could not be retrieved"
            )
        )

    # =====================================================
    # ENRICH ORDER
    # =====================================================

    try:

        enriched_order = enrich_orders_with_references(
            [updated_order]
        )[0]

    except Exception as exc:

        orders_collection.update_one(
            {
                "_id": order["_id"]
            },
            {
                "$set": {
                    "invoice_whatsapp_status": "failed",
                    "invoice_whatsapp_error": (
                        f"Order enrichment failed: {exc}"
                    ),
                    "updated_at": utc_now(),
                }
            }
        )

        raise HTTPException(
            status_code=500,
            detail=(
                f"Invoice {invoice_no} was created, "
                f"but invoice preparation failed: {exc}"
            )
        )

    # =====================================================
    # AUTOMATIC WHATSAPP DELIVERY
    # =====================================================

    whatsapp_sent = False
    whatsapp_error = None

    customer = (
        enriched_order.get("customer")
        or {}
    )

    raw_mobile = customer.get(
        "mobile",
        "919131037870"
    )

    # -----------------------------------------------------
    # CLEAN PHONE NUMBER
    # -----------------------------------------------------

    clean_number = "".join(
        filter(
            str.isdigit,
            str(raw_mobile)
        )
    )

    if not clean_number.startswith("91"):

        clean_number = (
            f"91{clean_number}"
        )

    try:

        send_invoice_template_whatsapp(
            enriched_order
        )

        whatsapp_sent = True

    except Exception as exc:

        whatsapp_sent = False

        whatsapp_error = str(exc)

        print(
            f"WhatsApp invoice delivery failed "
            f"for {invoice_no}: {exc}"
        )

    # =====================================================
    # SAVE WHATSAPP RESULT
    # =====================================================

    if whatsapp_sent:

        whatsapp_tracking = create_tracking_entry(
            status=order.get(
                "status",
                "Pending"
            ),
            user_id=str(
                current_user["user_id"]
            ),
            note=(
                f"Invoice {invoice_no} "
                f"automatically sent to customer "
                f"via WhatsApp."
            ),
        )

        orders_collection.update_one(
            {
                "_id": order["_id"]
            },
            {
                "$set": {
                    "invoice_whatsapp_sent": True,
                    "invoice_whatsapp_status": "sent",
                    "invoice_whatsapp_sent_at": utc_now(),
                    "invoice_whatsapp_error": None,
                    "updated_at": utc_now(),
                },
                "$push": {
                    "tracking": whatsapp_tracking
                }
            }
        )

    else:

        whatsapp_tracking = create_tracking_entry(
            status=order.get(
                "status",
                "Pending"
            ),
            user_id=str(
                current_user["user_id"]
            ),
            note=(
                f"Invoice {invoice_no} created, "
                f"but automatic WhatsApp delivery failed. "
                f"Reason: {whatsapp_error}"
            ),
        )

        orders_collection.update_one(
            {
                "_id": order["_id"]
            },
            {
                "$set": {
                    "invoice_whatsapp_sent": False,
                    "invoice_whatsapp_status": "failed",
                    "invoice_whatsapp_error": whatsapp_error,
                    "updated_at": utc_now(),
                },
                "$push": {
                    "tracking": whatsapp_tracking
                }
            }
        )

    # =====================================================
    # FINAL BILLING RESPONSE
    # =====================================================

    if whatsapp_sent:

        message = (
            f"{'Sale' if order_type == 'sale' else 'Sales return'} "
            f"billed successfully and "
            f"invoice sent on WhatsApp"
        )

    else:

        message = (
            f"{'Sale' if order_type == 'sale' else 'Sales return'} "
            f"billed successfully, "
            f"but WhatsApp delivery failed"
        )

    return {
        "success": True,

        "message": message,

        "order_id": order_id,

        "order_type": order_type,

        "invoice_no": invoice_no,

        "subtotal": subtotal,

        "total_gst": total_gst,

        "other_charges": other_charges,

        "gross_total": gross_total,

        "discount": discount,

        "grand_total": grand_total,

        "total_cogs": total_cogs,

        "gross_profit": gross_profit,

        "billed_at": now,

        "whatsapp_sent": whatsapp_sent,

        "whatsapp_status": (
            "sent"
            if whatsapp_sent
            else "failed"
        ),

        "whatsapp_error": (
            whatsapp_error
            if not whatsapp_sent
            else None
        ),

        "pdf_available": True,
    }
# =========================================================
# GET /orders/get-bill/v1/{order_id}/pdf
#
# View / Download invoice from frontend
#
# This API does NOT send WhatsApp.
# =========================================================

@router.get(
    "/get-bill/v1/{order_id}/pdf",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {
                "application/pdf": {}
            },
            "description": "Generated sale invoice PDF",
        }
    },
)

def get_bill_pdf(
    order_id: str,
    # current_user=Depends(get_current_user)
):

    # -----------------------------------------------------
    # VALIDATE ORDER ID
    # -----------------------------------------------------

    try:

        order_object_id = ObjectId(
            order_id
        )

    except Exception:

        raise HTTPException(
            status_code=400,
            detail="Invalid order MongoDB ID"
        )

    # -----------------------------------------------------
    # GET ORDER
    # -----------------------------------------------------

    order = orders_collection.find_one({
        "_id": order_object_id
    })

    if not order:

        raise HTTPException(
            status_code=404,
            detail="Order not found"
        )

    # -----------------------------------------------------
    # ONLY SALE
    # -----------------------------------------------------

    if order.get("type") not in [
            "sale",
            "sale_return"
        ]:

        raise HTTPException(
            status_code=400,
            detail=(
                "Invoice PDF is available only "
                "for sale orders"
            )
        )

    # -----------------------------------------------------
    # MUST BE BILLED
    # -----------------------------------------------------

    invoice_no = order.get(
        "invoice_no"
    )

    if not invoice_no:

        raise HTTPException(
            status_code=400,
            detail=(
                "Invoice has not been generated yet. "
                "Please bill the order first."
            )
        )

    # -----------------------------------------------------
    # ENRICH ORDER
    # -----------------------------------------------------

    try:

        enriched_order = enrich_orders_with_references(
            [order]
        )[0]

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                f"Invoice {invoice_no} could not be "
                f"prepared: {exc}"
            )
        )

    # -----------------------------------------------------
    # GENERATE PDF
    # -----------------------------------------------------

    try:

        pdf_bytes = generate_invoice_pdf(
            enriched_order
        )

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                f"Invoice {invoice_no} PDF generation "
                f"failed: {exc}"
            )
        )

    # -----------------------------------------------------
    # PDF STREAM
    # -----------------------------------------------------

    pdf_stream = io.BytesIO(
        pdf_bytes
    )

    pdf_stream.seek(0)

    # -----------------------------------------------------
    # INLINE
    #
    # Browser will open PDF.
    # Frontend can also download it.
    # -----------------------------------------------------

    headers = {

        "Content-Disposition": (
            f'inline; '
            f'filename="Invoice_{invoice_no}.pdf"'
        ),

        "X-Order-ID": order_id,

        "X-Invoice-No": invoice_no,
    }

    return StreamingResponse(
        pdf_stream,

        media_type="application/pdf",

        headers=headers,
    )


# =========================================================
# RESEND BILL ON WHATSAPP
#
# POST /orders/resend-bill/v1/{order_id}/whatsapp
#
# Used when user manually wants to send/resend invoice.
#
# This does NOT create a new bill.
# This does NOT generate a new invoice number.
# =========================================================

@router.post(
    "/resend-bill/v1/{order_id}/whatsapp"
)
def resend_bill_whatsapp(
    order_id: str,
    data: ManualBillingRequest,
    # current_user=Depends(get_current_user)
   
):
    current_user={"user_id": "6a7afb1775c551fee4ea35a4"}
    # -----------------------------------------------------
    # VALIDATE ORDER ID
    # -----------------------------------------------------

    try:

        order_object_id = ObjectId(
            order_id
        )

    except Exception:

        raise HTTPException(
            status_code=400,
            detail="Invalid order MongoDB ID"
        )

    # -----------------------------------------------------
    # GET ORDER
    # -----------------------------------------------------

    order = orders_collection.find_one({
        "_id": order_object_id
    })

    if not order:

        raise HTTPException(
            status_code=404,
            detail="Order not found"
        )

    # -----------------------------------------------------
    # ONLY SALE
    # -----------------------------------------------------

    if order.get("type") not in [
            "sale",
            "sale_return"
        ]:

        raise HTTPException(
            status_code=400,
            detail=(
                "Invoice WhatsApp delivery is "
                "available only for sale orders"
            )
        )

    # -----------------------------------------------------
    # ACTIVE ORDER
    # -----------------------------------------------------

    if order.get(
        "record_status",
        "active"
    ) != "active":

        raise HTTPException(
            status_code=400,
            detail="Inactive orders cannot send invoice"
        )

    # -----------------------------------------------------
    # MUST BE BILLED
    # -----------------------------------------------------

    invoice_no = order.get(
        "invoice_no"
    )

    if not invoice_no:

        raise HTTPException(
            status_code=400,
            detail=(
                "Order is not billed yet. "
                "Please bill the order first."
            )
        )

  
    # =====================================================
    # ENRICH ORDER
    # =====================================================

    try:

        enriched_order = enrich_orders_with_references(
            [order]
        )[0]

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                f"Invoice {invoice_no} could not "
                f"be prepared: {exc}"
            )
        )



    # =====================================================
    # SEND WHATSAPP
    # =====================================================

    try:

        send_invoice_template_whatsapp(enriched_order)

    except Exception as exc:

        # -----------------------------------------------
        # Save failed resend attempt
        # -----------------------------------------------

        tracking_entry = create_tracking_entry(
            status=order.get(
                "status",
                "Pending"
            ),
            user_id=str(
                current_user["user_id"]
            ),
            note=(
                f"Manual WhatsApp resend failed "
                f"for invoice {invoice_no}. "
                f"Reason: {exc}"
            ),
        )

        orders_collection.update_one(
            {
                "_id": order["_id"]
            },
            {
                "$set": {

                    "invoice_whatsapp_status": "failed",

                    "invoice_whatsapp_error": str(
                        exc
                    ),

                    "updated_at": utc_now(),
                },

                "$push": {
                    "tracking": tracking_entry
                }
            }
        )

        raise HTTPException(
            status_code=500,
            detail=(
                f"Invoice {invoice_no} could not "
                f"be sent on WhatsApp: {exc}"
            )
        )

    # =====================================================
    # SAVE SUCCESSFUL RESEND
    # =====================================================

    now = utc_now()

    tracking_entry = create_tracking_entry(
        status=order.get(
            "status",
            "Pending"
        ),
        user_id=str(
            current_user["user_id"]
        ),
        note=(
            f"Invoice {invoice_no} manually "
            f"sent/resend via WhatsApp."
        ),
    )

    orders_collection.update_one(
        {
            "_id": order["_id"]
        },
        {
            "$set": {

                "invoice_whatsapp_sent": True,

                "invoice_whatsapp_status": "sent",

                "invoice_whatsapp_sent_at": now,

                "invoice_whatsapp_error": None,

                "updated_at": now,
            },

            "$push": {
                "tracking": tracking_entry
            }
        }
    )

    # =====================================================
    # RESPONSE
    # =====================================================

    return {

        "success": True,

        "message": (
            "Invoice sent successfully "
            "on WhatsApp"
        ),

        "order_id": order_id,

        "invoice_no": invoice_no,

        "whatsapp_sent": True,

        "whatsapp_status": "sent",

        "sent_at": now,
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

    data: OrderStatusUpdate,

    current_user=Depends(get_current_user)
):

    order_object_id = validate_object_id(
        order_id,
        "order_id"
    )

    current_user_id = validate_object_id(
        str(current_user["user_id"]),
        "user_id"
    )

    # =====================================================
    # OPTIONAL VEHICLE
    # =====================================================
    current_vehicle_id = (
        validate_vehicle(data.vehicle_id)
        if data.vehicle_id
        else None
    )

    # =====================================================
    # FIND ORDER
    # =====================================================
    order = orders_collection.find_one(
        {"_id": order_object_id}
    )

    if not order:
        raise HTTPException(
            status_code=404,
            detail="Order not found"
        )

    current_status = order.get(
        "status",
        "Pending"
    )

    if current_status == data.status:
        raise HTTPException(
            status_code=400,
            detail=f"Order is already {data.status}"
        )

    if current_status in [
        "Delivered",
        "Cancelled",
    ]:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Order is already {current_status} "
                "and cannot be changed"
            )
        )

    # =====================================================
    # TRACKING
    # =====================================================
    tracking_entry = create_tracking_entry(
        status=data.status,
        user_id=str(current_user_id),
        note=(
            data.note
            or f"Order status changed from "
               f"{current_status} to {data.status}"
        ),
    )

    update_data = {
        "status": data.status,
        "updated_at": utc_now(),
    }

    if current_vehicle_id is not None:
        update_data["vehicle_id"] = current_vehicle_id

    # =====================================================
    # ATOMIC UPDATE
    # =====================================================
    result = orders_collection.update_one(
        {
            "_id": order_object_id,
            "status": current_status,
        },
        {
            "$set": update_data,
            "$push": {
                "tracking": tracking_entry
            },
        }
    )

    if result.modified_count == 0:
        raise HTTPException(
            status_code=409,
            detail=(
                "Order status was changed by another request. "
                "Please refresh and try again."
            )
        )

    updated_order = orders_collection.find_one(
        {"_id": order_object_id}
    )

    enriched_order = enrich_orders_with_references(
        [updated_order]
    )[0]

    return {
        "success": True,
        "message": "Order status updated successfully",
        "data": enriched_order,
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


