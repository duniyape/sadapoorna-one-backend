from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi.responses import StreamingResponse
from typing import Optional, List, Dict, Tuple, Any
from datetime import datetime, timezone, timedelta
from bson import ObjectId
from zoneinfo import ZoneInfo
import io
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
    stock_batches_collection,
    stock_batch_allocations_collection,
    sale_batch_consumptions_collection,
    delivery_manifests_collection,
)

from routes.auth import get_current_user
from schemas.order_schemas import (
    ORDER_TYPES,
    ORDER_STATUSES,
    RECORD_STATUSES,
    ORDER_PREFIX,
    ORDER_INVOICE_PREFIX,
    OrderCreate,
    OrderUpdate,
    OrderStatusUpdate,
    RecordStatusUpdate,
    ManualBillingRequest,
    BulkOrderDispatchRequest,
)

from services.batch_service import (
    create_stock_batches_from_purchase,
    allocate_fifo_from_batches,
    record_sale_consumptions,
    reverse_sale_consumptions,
    handle_purchase_return_batches,
    transfer_stock_batches,
    sync_existing_purchases_to_batches,
    get_sellable_stock_breakdown,
    generate_manifest_no,
)
from services.invoice_pdf_service import generate_invoice_pdf
from services.whatsapp_service import send_invoice_template_whatsapp

router = APIRouter()
IST = ZoneInfo("Asia/Kolkata")


# =========================================================
# TIME & SERIALIZATION HELPERS
# =========================================================

def utc_now() -> datetime:
    """Return current naive UTC datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def convert_utc_to_ist(value):
    """Recursively convert datetime values from UTC to IST in dicts and lists."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(IST)
    if isinstance(value, dict):
        return {k: convert_utc_to_ist(v) for k, v in value.items()}
    if isinstance(value, list):
        return [convert_utc_to_ist(item) for item in value]
    if isinstance(value, tuple):
        return tuple(convert_utc_to_ist(item) for item in value)
    return value


def get_utc_date_range(from_date: Optional[str], to_date: Optional[str]):
    """Convert IST date strings (YYYY-MM-DD) to UTC query range."""
    start_date, end_date = None, None
    if from_date:
        try:
            start_date = datetime.strptime(from_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid from_date format. Use YYYY-MM-DD")
    if to_date:
        try:
            end_date = datetime.strptime(to_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid to_date format. Use YYYY-MM-DD")

    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=400, detail="from_date must be before or equal to to_date")

    date_query = {}
    if start_date:
        start_utc = start_date.replace(tzinfo=IST).astimezone(timezone.utc).replace(tzinfo=None)
        date_query["$gte"] = start_utc
    if end_date:
        end_utc = (end_date.replace(tzinfo=IST) + timedelta(days=1)).astimezone(timezone.utc).replace(tzinfo=None)
        date_query["$lt"] = end_utc
    return date_query


def validate_object_id(value: str, field_name: str) -> ObjectId:
    """Validate string and convert to ObjectId."""
    if not value or not ObjectId.is_valid(value):
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}")
    return ObjectId(value)


def serialize_value(value):
    """Recursively convert ObjectId values into strings."""
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value
    if isinstance(value, list):
        return [serialize_value(item) for item in value]
    if isinstance(value, dict):
        return {k: serialize_value(v) for k, v in value.items()}
    return value


def serialize_order(order: dict) -> dict:
    """Serialize order document with id field."""
    data = serialize_value(order)
    if "_id" in data:
        data["id"] = data["_id"]
        del data["_id"]
    return data


def create_tracking_entry(status: str, user_id: Optional[str] = None, note: Optional[str] = None) -> dict:
    """Create embedded order-status tracking entry."""
    entry = {
        "status": status,
        "timestamp": utc_now(),
        "updated_by": ObjectId(str(user_id)) if user_id and ObjectId.is_valid(str(user_id)) else None,
        "note": note,
    }
    return entry


# =========================================================
# VALIDATORS
# =========================================================

def validate_vendor(vendor_id: Optional[str]) -> Optional[ObjectId]:
    if not vendor_id:
        return None
    obj_id = validate_object_id(vendor_id, "vendor_id")
    if not vendors_collection.find_one({"_id": obj_id}, {"_id": 1}):
        raise HTTPException(status_code=404, detail=f"Vendor not found: {vendor_id}")
    return obj_id


def validate_customer(customer_id: Optional[str]) -> Optional[ObjectId]:
    if not customer_id:
        return None
    cid_str = str(customer_id).strip()
    cust = None
    if ObjectId.is_valid(cid_str):
        cust = customers_collection.find_one({"_id": ObjectId(cid_str)}, {"_id": 1})
    if not cust:
        cust = customers_collection.find_one({"id": cid_str}, {"_id": 1})
    if not cust and cid_str.upper().startswith("CUST"):
        cust = customers_collection.find_one({"id": cid_str.upper()}, {"_id": 1})
    if not cust:
        raise HTTPException(status_code=404, detail=f"Customer not found: {customer_id}")
    return cust["_id"]


def validate_warehouse(warehouse_id: Optional[str]) -> Optional[ObjectId]:
    if not warehouse_id:
        return None
    obj_id = validate_object_id(warehouse_id, "warehouse_id")
    if not warehouses_collection.find_one({"_id": obj_id}, {"_id": 1}):
        raise HTTPException(status_code=404, detail=f"Warehouse not found: {warehouse_id}")
    return obj_id


def validate_vehicle(vehicle_id: Optional[str]) -> Optional[ObjectId]:
    if not vehicle_id:
        return None
    obj_id = validate_object_id(vehicle_id, "vehicle_id")
    if not vehicles_collection.find_one({"_id": obj_id}, {"_id": 1}):
        raise HTTPException(status_code=404, detail=f"Vehicle not found: {vehicle_id}")
    return obj_id


def validate_branch(branch_id: Optional[str]) -> Optional[ObjectId]:
    if not branch_id:
        return None
    obj_id = validate_object_id(branch_id, "branch_id")
    if not branches_collection.find_one({"_id": obj_id}, {"_id": 1}):
        raise HTTPException(status_code=404, detail="Branch not found")
    return obj_id


def validate_assigned_employee(employee_id: Optional[str]) -> Optional[ObjectId]:
    if not employee_id:
        return None
    obj_id = validate_object_id(employee_id, "assigned_employee_id")
    if not users_collection.find_one({"_id": obj_id}, {"_id": 1}):
        raise HTTPException(status_code=404, detail="Assigned employee not found")
    return obj_id


# =========================================================
# ITEM BUILDER & TAX CALCULATION
# =========================================================

def build_items(items, gst_type: str, order_type: Optional[str] = None):
    processed_items = []
    subtotal = 0.0
    total_gst = 0.0

    for item in items:
        product_obj_id = validate_object_id(str(item.product_id), "product_id")
        product = products_collection.find_one({"_id": product_obj_id}, {"_id": 1, "name": 1})
        if not product:
            raise HTTPException(status_code=404, detail=f"Product not found: {item.product_id}")

        variant_obj_id = validate_object_id(str(item.variant_id), "variant_id")
        variant = product_variants_collection.find_one({
            "_id": variant_obj_id,
            "product_id": product_obj_id
        })
        if not variant:
            raise HTTPException(status_code=404, detail=f"Product variant not found: {item.variant_id}")
        if variant.get("status") == "inactive":
            raise HTTPException(status_code=400, detail=f"Product variant is inactive: {item.variant_id}")

        quantity = float(item.quantity)
        rate = float(item.rate)
        if quantity <= 0:
            raise HTTPException(status_code=400, detail="Quantity must be greater than zero")
        if rate < 0:
            raise HTTPException(status_code=400, detail="Rate cannot be negative")

        gst_percent = float(variant.get("gst_percent", 0))
        line_amount = round(quantity * rate, 2)

        if gst_type == "including":
            if gst_percent > 0:
                taxable_amount = round(line_amount * 100 / (100 + gst_percent), 2)
                gst_amount = round(line_amount - taxable_amount, 2)
            else:
                taxable_amount = line_amount
                gst_amount = 0.0
        else:
            taxable_amount = line_amount
            gst_amount = round(taxable_amount * gst_percent / 100, 2)

        total_line_amount = round(taxable_amount + gst_amount, 2)
        subtotal += taxable_amount
        total_gst += gst_amount

        processed_item = {
            "item_id": ObjectId(),
            "product_id": product_obj_id,
            "variant_id": variant_obj_id,
            "quantity": quantity,
            "rate": rate,
            "gst_percent": gst_percent,
            "gst_amount": gst_amount,
            "taxable_amount": taxable_amount,
            "total_amount": total_line_amount,
            "investors": getattr(item, "investors", []),
            "batch_consumptions": [],
            "cogs": 0.0,
        }

        if order_type == "sale_return":
            if not getattr(item, "ref_item_id", None):
                raise HTTPException(status_code=400, detail="ref_item_id is required for sale return items")
            processed_item["ref_item_id"] = validate_object_id(str(item.ref_item_id), "ref_item_id")

        processed_items.append(processed_item)

    return processed_items, round(subtotal, 2), round(total_gst, 2)


# =========================================================
# HELPER: VALIDATE SALE RETURN REQUEST
# =========================================================

def validate_sale_return_request(
    ref_invoice_id: Optional[ObjectId],
    items: List[Dict[str, Any]],
    customer_id: Optional[ObjectId] = None,
    exclude_order_id: Optional[ObjectId] = None,
) -> Dict[str, Any]:
    """
    Validates sale return orders upfront during creation or item update:
    1. Checks ref_invoice_id is provided, exists, and belongs to a billed/delivered sale order.
    2. Validates customer_id matches original sale customer.
    3. Validates each ref_item_id belongs to the original sale order and matches product/variant.
    4. Calculates available returnable quantity taking into account:
       - Total sold quantity from original sale batch consumptions (or original order item).
       - Quantities already returned in previous billed returns.
       - Quantities requested in other active, unbilled (pending) return orders.
    5. Rejects if requested return quantity exceeds remaining returnable quantity.
    Returns the original sale order doc.
    """
    if not ref_invoice_id:
        raise HTTPException(
            status_code=400,
            detail="ref_invoice_id is required for sales return orders."
        )

    orig_order = orders_collection.find_one({"_id": ref_invoice_id})
    if not orig_order:
        raise HTTPException(
            status_code=404,
            detail="Referenced original sales invoice order not found."
        )

    if orig_order.get("type") != "sale":
        raise HTTPException(
            status_code=400,
            detail=f"Referenced order is '{orig_order.get('type')}'. Only sale orders can be returned."
        )

    # Check that original sale order has been billed / delivered
    orig_invoice = orig_order.get("invoice_no")
    orig_status = orig_order.get("status")
    if not orig_invoice and orig_status not in ["Delivered", "Completed"]:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Referenced sale order '{orig_order.get('order_no')}' is not billed yet. "
                f"Only billed / delivered orders can be returned."
            )
        )

    if customer_id and orig_order.get("customer_id") and customer_id != orig_order.get("customer_id"):
        raise HTTPException(
            status_code=400,
            detail="Customer on sales return must match the customer from the original sale order."
        )

    orig_items = {it.get("item_id"): it for it in orig_order.get("items", []) if it.get("item_id")}

    # Query any other active, unbilled (pending) return orders referencing this original sale
    pending_return_query: Dict[str, Any] = {
        "type": "sale_return",
        "ref_invoice_id": ref_invoice_id,
        "record_status": "active",
        "status": {"$nin": ["Cancelled", "Rejected"]},
        "$or": [{"invoice_no": None}, {"invoice_no": {"$exists": False}}, {"invoice_no": ""}],
    }
    if exclude_order_id:
        pending_return_query["_id"] = {"$ne": exclude_order_id}

    pending_returns = list(orders_collection.find(pending_return_query, {"items": 1}))
    pending_return_qty_map: Dict[ObjectId, float] = {}
    for pret in pending_returns:
        for pit in pret.get("items", []):
            p_ref_id = pit.get("ref_item_id")
            if p_ref_id:
                pending_return_qty_map[p_ref_id] = pending_return_qty_map.get(p_ref_id, 0.0) + float(pit.get("quantity", 0))

    # Also aggregate requested quantities in the current request per ref_item_id
    current_request_qty_map: Dict[ObjectId, float] = {}
    for it in items:
        r_id = it.get("ref_item_id")
        if r_id:
            current_request_qty_map[r_id] = current_request_qty_map.get(r_id, 0.0) + float(it.get("quantity", 0))

    for item in items:
        ref_item_id = item.get("ref_item_id")
        if not ref_item_id:
            raise HTTPException(
                status_code=400,
                detail="ref_item_id is required for each sale return item."
            )

        orig_item = orig_items.get(ref_item_id)
        if not orig_item:
            raise HTTPException(
                status_code=400,
                detail=f"Referenced item '{ref_item_id}' does not exist in original sale order '{orig_order.get('order_no')}'."
            )

        if item.get("product_id") != orig_item.get("product_id") or item.get("variant_id") != orig_item.get("variant_id"):
            raise HTTPException(
                status_code=400,
                detail="Product and variant must match the original sold item."
            )

        # Check consumption records for this sale item
        consumptions = list(sale_batch_consumptions_collection.find({
            "sale_order_id": ref_invoice_id,
            "sale_item_id": ref_item_id,
        }))

        if consumptions:
            total_sold_qty = sum(float(c.get("quantity", 0)) for c in consumptions)
            already_returned_qty = sum(float(c.get("returned_quantity", 0)) for c in consumptions)
        else:
            total_sold_qty = float(orig_item.get("quantity", 0))
            already_returned_qty = 0.0

        other_pending_qty = pending_return_qty_map.get(ref_item_id, 0.0)
        available_to_return = round(total_sold_qty - already_returned_qty - other_pending_qty, 4)
        req_qty = current_request_qty_map.get(ref_item_id, float(item.get("quantity", 0)))

        if req_qty > available_to_return:
            var = product_variants_collection.find_one({"_id": item.get("variant_id")}) or {}
            var_name = var.get("name") or str(item.get("variant_id"))
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Return quantity exceeds available returnable quantity for '{var_name}' from original sale. "
                    f"Sold: {total_sold_qty}, Already returned: {already_returned_qty}, "
                    f"Pending return requests: {other_pending_qty}, "
                    f"Available to return: {available_to_return}, Requested: {req_qty}."
                )
            )

    return orig_order



# =========================================================
# SERIAL NUMBER GENERATORS
# =========================================================

def generate_order_no() -> str:
    """Generate unique order number: ORD-YYYY-000001."""
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


def generate_invoice_no(order_type: str) -> str:
    """Generate unique monthly invoice number: INV-YYYY-MM-0001."""
    prefix = ORDER_INVOICE_PREFIX.get(order_type, "INV")
    now = utc_now()
    year = now.strftime("%Y")
    month = now.strftime("%m")
    counter_id = f"order_invoice:{prefix}:{year}:{month}"

    counter = counters_collection.find_one_and_update(
        {"_id": counter_id},
        {
            "$inc": {"seq": 1},
            "$set": {
                "prefix": prefix,
                "year": int(year),
                "month": int(month),
                "updated_at": now,
            },
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return f"{prefix}-{year}-{month}-{counter['seq']:04d}"


# =========================================================
# RESPONSE ENRICHMENT
# =========================================================

def enrich_orders_with_references(orders: List[dict]) -> List[dict]:
    """Enrich order documents with party, vehicle, user, product, variant details for response."""
    if not orders:
        return []

    product_ids, variant_ids = set(), set()
    vendor_ids, customer_ids, vehicle_ids = set(), set(), set()
    user_ids, branch_ids = set(), set()

    for order in orders:
        if isinstance(order.get("vendor_id"), ObjectId):
            vendor_ids.add(order["vendor_id"])
        if isinstance(order.get("customer_id"), ObjectId):
            customer_ids.add(order["customer_id"])
        if isinstance(order.get("vehicle_id"), ObjectId):
            vehicle_ids.add(order["vehicle_id"])
        if isinstance(order.get("assigned_employee_id"), ObjectId):
            user_ids.add(order["assigned_employee_id"])
        if isinstance(order.get("created_by"), ObjectId):
            user_ids.add(order["created_by"])
        if isinstance(order.get("branch_id"), ObjectId):
            branch_ids.add(order["branch_id"])

        for tracking_entry in order.get("tracking", []):
            if isinstance(tracking_entry.get("updated_by"), ObjectId):
                user_ids.add(tracking_entry["updated_by"])

        for item in order.get("items", []):
            if isinstance(item.get("product_id"), ObjectId):
                product_ids.add(item["product_id"])
            if isinstance(item.get("variant_id"), ObjectId):
                variant_ids.add(item["variant_id"])

    # Batch Lookups
    vendors_map = {v["_id"]: v for v in vendors_collection.find({"_id": {"$in": list(vendor_ids)}})} if vendor_ids else {}
    customers_map = {c["_id"]: c for c in customers_collection.find({"_id": {"$in": list(customer_ids)}})} if customer_ids else {}
    vehicles_map = {vh["_id"]: vh for vh in vehicles_collection.find({"_id": {"$in": list(vehicle_ids)}})} if vehicle_ids else {}
    users_map = {u["_id"]: u for u in users_collection.find({"_id": {"$in": list(user_ids)}})} if user_ids else {}
    branches_map = {b["_id"]: b for b in branches_collection.find({"_id": {"$in": list(branch_ids)}})} if branch_ids else {}
    products_map = {p["_id"]: p for p in products_collection.find({"_id": {"$in": list(product_ids)}})} if product_ids else {}
    variants_map = {vr["_id"]: vr for vr in product_variants_collection.find({"_id": {"$in": list(variant_ids)}})} if variant_ids else {}

    unit_ids = {v["unit_id"] for v in variants_map.values() if isinstance(v.get("unit_id"), ObjectId)}
    packaging_type_ids = {v["packaging_type_id"] for v in variants_map.values() if isinstance(v.get("packaging_type_id"), ObjectId)}

    units_map = {u["_id"]: u for u in product_units_collection.find({"_id": {"$in": list(unit_ids)}})} if unit_ids else {}
    packaging_map = {pk["_id"]: pk for pk in packing_types_collection.find({"_id": {"$in": list(packaging_type_ids)}})} if packaging_type_ids else {}

    response_orders = []
    for order in orders:
        resp = dict(order)

        # Vendor
        v = vendors_map.get(order.get("vendor_id"))
        resp.pop("vendor_id", None)
        resp["vendor"] = {
            "id": str(v["_id"]),
            "contact_person": v.get("contact_person"),
            "business_name": v.get("business_name"),
            "mobile": v.get("mobile"),
            "address": v.get("address"),
            "gst_number": v.get("gst_number"),
        } if v else None

        # Customer
        c = customers_map.get(order.get("customer_id"))
        resp.pop("customer_id", None)
        resp["customer"] = {
            "id": str(c["_id"]),
            "name": c.get("name"),
            "shop_name": c.get("company_name") or c.get("name"),
            "mobile": c.get("mobile"),
            "billing_address": c.get("billing_address"),
            "shipping_address": c.get("shipping_address"),
            "gst_number": c.get("gst_number"),
            "location": c.get("location"),
        } if c else None

        # Vehicle
        vh = vehicles_map.get(order.get("vehicle_id"))
        resp.pop("vehicle_id", None)
        resp["vehicle"] = {
            "id": str(vh["_id"]),
            "vehicle_number": vh.get("vehicle_number"),
            "model": vh.get("model"),
            "vehicle_type": vh.get("vehicle_type"),
        } if vh else None

        # Employee & Created By
        emp = users_map.get(order.get("assigned_employee_id"))
        resp.pop("assigned_employee_id", None)
        resp["assigned_employee_name"] = emp.get("name") or emp.get("full_name") if emp else None

        creator = users_map.get(order.get("created_by"))
        resp.pop("created_by", None)
        resp["created_by_name"] = creator.get("name") or creator.get("full_name") if creator else None

        # Branch
        br = branches_map.get(order.get("branch_id"))
        resp.pop("branch_id", None)
        resp["branch"] = {"id": str(br["_id"]), "name": br.get("name")} if br else None

        # Tracking
        tracking_resp = []
        for t in order.get("tracking", []):
            t_user = users_map.get(t.get("updated_by"))
            tracking_resp.append({
                "status": t.get("status"),
                "timestamp": t.get("timestamp"),
                "updated_by": str(t.get("updated_by")) if t.get("updated_by") else None,
                "updated_by_name": t_user.get("name") or t_user.get("full_name") if t_user else None,
                "note": t.get("note"),
            })
        resp["tracking"] = tracking_resp

        # Items
        items_resp = []
        for item in order.get("items", []):
            prod = products_map.get(item.get("product_id"))
            vr = variants_map.get(item.get("variant_id"))
            unit = units_map.get(vr.get("unit_id")) if vr else None
            pkg = packaging_map.get(vr.get("packaging_type_id")) if vr else None

            items_resp.append({
                "item_id": str(item.get("item_id")),
                "product_id": str(item.get("product_id")),
                "variant_id": str(item.get("variant_id")),
                "product_name": prod.get("name") if prod else None,
                "variant_name": vr.get("name") if vr else None,
                "sku": vr.get("sku") if vr else None,
                "quantity": item.get("quantity", 0),
                "rate": item.get("rate", 0),
                "gst_percent": item.get("gst_percent", 0),
                "gst_amount": item.get("gst_amount", 0),
                "taxable_amount": item.get("taxable_amount", 0),
                "total_amount": item.get("total_amount", 0),
                "cogs": item.get("cogs", 0),
                "batch_consumptions": item.get("batch_consumptions", []),
                "unit": {
                    "id": str(unit["_id"]),
                    "name": unit.get("name"),
                    "symbol": unit.get("symbol"),
                    "short_name": unit.get("short_name"),
                } if unit else None,
                "packaging_type": {
                    "id": str(pkg["_id"]),
                    "name": pkg.get("name"),
                } if pkg else None,
                "investors": item.get("investors", []),
            })
        resp["items"] = items_resp

        # Ref invoice for returns
        if order.get("type") in ["sale_return", "purchase_return"] and order.get("ref_invoice_id"):
            parent = orders_collection.find_one({"_id": order["ref_invoice_id"]}, {"invoice_no": 1})
            resp["ref_invoice_no"] = parent.get("invoice_no") if parent else None

        response_orders.append(serialize_order(resp))

    return convert_utc_to_ist(response_orders)


# =========================================================
# HELPER: CREATE DELIVERY MANIFEST DOCUMENT
# =========================================================

def _create_delivery_manifest_document(
    orders: List[Dict[str, Any]],
    warehouse_id: Optional[ObjectId],
    vehicle_id: Optional[ObjectId],
    user_id: Optional[str] = None,
    route_name: Optional[str] = None,
    notes: Optional[str] = None,
    status: str = "active",
    manifest_id: Optional[ObjectId] = None,
    manifest_no: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Creates and persists a Driver Loading Manifest (Trip Sheet) document
    in delivery_manifests_collection.
    """
    now = utc_now()
    if not manifest_id:
        manifest_id = ObjectId()
    if not manifest_no:
        manifest_no = generate_manifest_no()

    wh_doc = warehouses_collection.find_one({"_id": warehouse_id}) if warehouse_id else None
    veh_doc = vehicles_collection.find_one({"_id": vehicle_id}) if vehicle_id else None

    customer_stops = []
    trip_demand: Dict[Tuple[ObjectId, ObjectId], float] = {}
    obj_ids = []

    for o in orders:
        obj_ids.append(o["_id"])
        cid = o.get("customer_id")
        cust = None
        if cid:
            if isinstance(cid, str) and ObjectId.is_valid(cid):
                cid = ObjectId(cid)
            cust = customers_collection.find_one({"_id": cid})

        customer_stops.append({
            "order_id": str(o["_id"]),
            "order_no": o.get("order_no"),
            "customer_name": cust.get("name") if cust else "N/A",
            "customer_phone": cust.get("phone") or cust.get("mobile") if cust else None,
            "address": cust.get("address") or cust.get("shipping_address") or cust.get("billing_address") if cust else None,
            "payment_mode": o.get("payment_mode"),
            "grand_total": o.get("grand_total", 0.0),
            "item_count": len(o.get("items", [])),
        })

        for it in o.get("items", []):
            p_id = it.get("product_id")
            v_id = it.get("variant_id")
            if isinstance(p_id, str) and ObjectId.is_valid(p_id):
                p_id = ObjectId(p_id)
            if isinstance(v_id, str) and ObjectId.is_valid(v_id):
                v_id = ObjectId(v_id)
            qty = float(it.get("quantity", 0))
            if p_id and v_id and qty > 0:
                trip_demand[(p_id, v_id)] = trip_demand.get((p_id, v_id), 0.0) + qty

    consolidated_items = []
    for (p_id, v_id), total_qty in trip_demand.items():
        prod = products_collection.find_one({"_id": p_id})
        var = product_variants_collection.find_one({"_id": v_id})
        consolidated_items.append({
            "product_id": str(p_id),
            "product_name": prod.get("name") if prod else str(p_id),
            "variant_id": str(v_id),
            "variant_name": var.get("name") if var else str(v_id),
            "sku": var.get("sku") if var else "",
            "total_quantity": round(total_qty, 4),
        })

    total_trip_amount = round(sum(float(o.get("grand_total", 0.0) or 0.0) for o in orders), 2)

    manifest_doc = {
        "_id": manifest_id,
        "manifest_no": manifest_no,
        "status": status,
        "dispatch_date": now,
        "dispatched_by": ObjectId(user_id) if (user_id and ObjectId.is_valid(user_id)) else None,
        "warehouse": {
            "id": warehouse_id,
            "name": wh_doc.get("name") if wh_doc else None,
        } if warehouse_id else None,
        "vehicle": {
            "id": vehicle_id,
            "vehicle_number": veh_doc.get("vehicle_number") if veh_doc else None,
            "model": veh_doc.get("model") if veh_doc else None,
        } if vehicle_id else None,
        "route_name": route_name,
        "notes": notes,
        "order_ids": obj_ids,
        "total_orders": len(orders),
        "total_trip_amount": total_trip_amount,
        "consolidated_items": consolidated_items,
        "customer_stops": customer_stops,
        "created_at": now,
        "updated_at": now,
    }

    delivery_manifests_collection.insert_one(manifest_doc)
    return manifest_doc



# =========================================================
# ROUTE: CREATE ORDER
# POST /orders/v1
# =========================================================

@router.post("/v1")
def create_order(
    data: OrderCreate,
    current_user=Depends(get_current_user)
):
    order_no = generate_order_no()
    invoice_no = None

    # Purchase invoice validation
    if data.type == "purchase":
        if not data.invoice_no or not data.invoice_no.strip():
            raise HTTPException(status_code=400, detail="invoice_no is required for purchase orders")
        invoice_no = data.invoice_no.strip()
        duplicate = orders_collection.find_one({
            "type": "purchase",
            "invoice_no": invoice_no,
            "record_status": "active",
        })
        if duplicate:
            raise HTTPException(status_code=400, detail=f"Purchase invoice number {invoice_no} already exists")
    elif data.invoice_no:
        raise HTTPException(status_code=400, detail="invoice_no can only be provided for purchase orders")

    vendor_obj_id = validate_vendor(data.vendor_id)
    customer_obj_id = validate_customer(data.customer_id)
    warehouse_obj_id = validate_warehouse(data.warehouse_id)
    dest_warehouse_obj_id = validate_warehouse(data.destination_warehouse_id)
    if data.type in ["purchase_to_warehouse", "Warehouse_IN"]:
        if warehouse_obj_id and not dest_warehouse_obj_id:
            dest_warehouse_obj_id = warehouse_obj_id
        elif dest_warehouse_obj_id and not warehouse_obj_id:
            warehouse_obj_id = dest_warehouse_obj_id
    vehicle_obj_id = validate_vehicle(data.vehicle_id)
    branch_obj_id = validate_branch(data.branch_id)
    employee_obj_id = validate_assigned_employee(data.assigned_employee_id)
    created_by_obj_id = validate_object_id(str(current_user["user_id"]), "user_id")
    ref_invoice_obj_id = validate_object_id(data.ref_invoice_id, "ref_invoice_id") if data.ref_invoice_id else None

    processed_items, subtotal, total_gst = build_items(data.items, data.gst_type, data.type)
    grand_total = round(subtotal + total_gst + data.other_charges, 2)
    if grand_total < 0:
        raise HTTPException(status_code=400, detail="Grand total cannot be negative")

    # -----------------------------------------------------
    # PRE-CREATION STOCK VALIDATION
    # -----------------------------------------------------
    if data.type == "sale":
        if not warehouse_obj_id and not vehicle_obj_id:
            raise HTTPException(
                status_code=400,
                detail="Either warehouse_id or vehicle_id is required for sales orders."
            )
        for item in processed_items:
            p_id = item.get("product_id")
            v_id = item.get("variant_id")
            req_qty = float(item.get("quantity", 0))

            breakdown = get_sellable_stock_breakdown(
                product_id=p_id,
                variant_id=v_id,
                warehouse_id=warehouse_obj_id,
                vehicle_id=vehicle_obj_id,
            )
            unblocked_qty = breakdown["unblocked_stock"]
            if req_qty > unblocked_qty:
                var = product_variants_collection.find_one({"_id": v_id}) or {}
                var_name = var.get("name") or str(v_id)
                loc_name = "warehouse" if warehouse_obj_id else "vehicle"
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Insufficient sellable stock for '{var_name}' in selected {loc_name}. "
                        f"Requested: {req_qty}, Available to sell: {unblocked_qty} "
                        f"(Physical: {breakdown['physical_stock']}, Blocked in pending orders: {breakdown['blocked_stock']})."
                    )
                )

    elif data.type in ["warehouse_to_vehicle", "warehouse_to_warehouse"]:
        if not warehouse_obj_id:
            raise HTTPException(status_code=400, detail="Source warehouse_id is required for transfer.")
        for item in processed_items:
            p_id = item.get("product_id")
            v_id = item.get("variant_id")
            req_qty = float(item.get("quantity", 0))

            breakdown = get_sellable_stock_breakdown(
                product_id=p_id,
                variant_id=v_id,
                warehouse_id=warehouse_obj_id,
            )
            phys_qty = breakdown["physical_stock"]
            if req_qty > phys_qty:
                var = product_variants_collection.find_one({"_id": v_id}) or {}
                var_name = var.get("name") or str(v_id)
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Insufficient physical stock for transfer of '{var_name}' from warehouse. "
                        f"Requested: {req_qty}, Available in warehouse: {phys_qty}."
                    )
                )

    elif data.type == "vehicle_to_warehouse":
        if not vehicle_obj_id:
            raise HTTPException(status_code=400, detail="Source vehicle_id is required for transfer.")
        for item in processed_items:
            p_id = item.get("product_id")
            v_id = item.get("variant_id")
            req_qty = float(item.get("quantity", 0))

            breakdown = get_sellable_stock_breakdown(
                product_id=p_id,
                variant_id=v_id,
                vehicle_id=vehicle_obj_id,
            )
            phys_qty = breakdown["physical_stock"]
            if req_qty > phys_qty:
                var = product_variants_collection.find_one({"_id": v_id}) or {}
                var_name = var.get("name") or str(v_id)
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Insufficient stock on vehicle for transfer of '{var_name}'. "
                        f"Requested: {req_qty}, Available on vehicle: {phys_qty}."
                    )
                )

    elif data.type == "sale_return":
        orig_order_validated = validate_sale_return_request(
            ref_invoice_id=ref_invoice_obj_id,
            items=processed_items,
            customer_id=customer_obj_id,
        )
        if not customer_obj_id and orig_order_validated.get("customer_id"):
            customer_obj_id = orig_order_validated.get("customer_id")

    now = utc_now()
    order_doc = {
        "type": data.type,
        "order_no": order_no,
        "invoice_no": invoice_no,
        "vendor_id": vendor_obj_id,
        "customer_id": customer_obj_id,
        "warehouse_id": warehouse_obj_id,
        "destination_warehouse_id": dest_warehouse_obj_id,
        "vehicle_id": vehicle_obj_id,
        "ref_invoice_id": ref_invoice_obj_id,
        "payment_mode": data.payment_mode,
        "branch_id": branch_obj_id,
        "assigned_employee_id": employee_obj_id,
        "created_by": created_by_obj_id,
        "gst_type": data.gst_type,
        "items": processed_items,
        "total_cogs": 0.0,
        "gross_profit": 0.0,
        "subtotal": subtotal,
        "total_gst": total_gst,
        "other_charges": round(data.other_charges, 2),
        "discount": 0.0,
        "grand_total": grand_total,
        "status": data.status,
        "record_status": data.record_status,
        "tracking": [
            create_tracking_entry(
                status=data.status,
                user_id=str(current_user["user_id"]),
                note=f"Order created with status {data.status}",
            )
        ],
        "notes": data.notes,
        "created_at": now,
        "updated_at": now,
    }

    result = orders_collection.insert_one(order_doc)
    order_doc["_id"] = result.inserted_id

    try:
        # If purchase is created directly as Completed -> Create Stock Batches immediately
        if data.type == "purchase" and data.status == "Completed":
            create_stock_batches_from_purchase(order_doc, user_id=str(current_user["user_id"]))

        # If transfer is created directly as Completed -> Move batches immediately
        if data.status == "Completed" and data.type in [
            "purchase_to_warehouse",
            "Warehouse_IN",
            "warehouse_to_vehicle",
            "vehicle_to_warehouse",
            "warehouse_to_warehouse",
        ]:
            _handle_transfer_movement(order_doc, user_id=str(current_user["user_id"]))

        created_manifest_id = None
        # If sale order is created directly as Out for Delivery -> Create manifest & Transfer if vehicle present
        if data.type == "sale" and data.status == "Out for Delivery" and warehouse_obj_id:
            manifest_doc = _create_delivery_manifest_document(
                orders=[order_doc],
                warehouse_id=warehouse_obj_id,
                vehicle_id=vehicle_obj_id,
                user_id=str(current_user["user_id"]),
                notes=f"Order created directly as Out for Delivery ({order_doc.get('order_no', '')}).",
            )
            created_manifest_id = manifest_doc["_id"]
            manifest_no = manifest_doc["manifest_no"]
            orders_collection.update_one(
                {"_id": result.inserted_id},
                {"$set": {"manifest_id": created_manifest_id, "manifest_no": manifest_no}}
            )
            order_doc["manifest_id"] = created_manifest_id
            order_doc["manifest_no"] = manifest_no

            if vehicle_obj_id:
                for it in order_doc.get("items", []):
                    p_id = it.get("product_id")
                    v_id = it.get("variant_id")
                    req_qty = float(it.get("quantity", 0))
                    if req_qty <= 0 or not (p_id and v_id):
                        continue
                    breakdown = get_sellable_stock_breakdown(
                        product_id=p_id,
                        variant_id=v_id,
                        warehouse_id=warehouse_obj_id,
                    )
                    if req_qty > breakdown["physical_stock"]:
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                f"Cannot dispatch '{it.get('product_name') or 'item'}': "
                                f"Insufficient physical stock in warehouse. "
                                f"Requested: {req_qty}, Available in warehouse: {breakdown['physical_stock']}."
                            )
                        )

                transfer_stock_batches(
                    source_type="warehouse",
                    source_id=warehouse_obj_id,
                    dest_type="vehicle",
                    dest_id=vehicle_obj_id,
                    items=order_doc.get("items", []),
                    transfer_order_id=result.inserted_id,
                    user_id=str(current_user["user_id"]),
                    allocation_type="warehouse_to_vehicle",
                    manifest_id=created_manifest_id,
                    manifest_no=manifest_no,
                )
    except Exception as ex:
        # ROLLBACK: Delete the inserted order and any generated manifest
        orders_collection.delete_one({"_id": result.inserted_id})
        if 'created_manifest_id' in locals() and created_manifest_id:
            delivery_manifests_collection.delete_one({"_id": created_manifest_id})
        raise ex

    enriched_order = enrich_orders_with_references([order_doc])[0]
    return {
        "success": True,
        "message": "Order created successfully",
        "data": enriched_order,
    }


# =========================================================
# ROUTE: GET ORDERS
# GET /orders/v1
# =========================================================

@router.get("/v1")
def get_orders(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    type: Optional[str] = None,
    status: Optional[str] = None,
    record_status: Optional[str] = "active",
    search: Optional[str] = None,
    warehouse_id: Optional[str] = None,
    vendor_id: Optional[str] = None,
    customer_id: Optional[str] = None,
    vehicle_id: Optional[str] = None,
    from_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD in IST"),
    to_date: Optional[str] = Query(None, description="End date YYYY-MM-DD in IST"),
):
    skip = (page - 1) * limit
    query = {}

    if type:
        if type not in ORDER_TYPES:
            raise HTTPException(status_code=400, detail="Invalid order type")
        query["type"] = type

    if status:
        if status not in ORDER_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid order status")
        query["status"] = status

    if record_status:
        if record_status not in RECORD_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid record_status")
        query["record_status"] = record_status

    if search:
        query["$or"] = [
            {"order_no": {"$regex": search, "$options": "i"}},
            {"invoice_no": {"$regex": search, "$options": "i"}},
            {"notes": {"$regex": search, "$options": "i"}},
        ]

    if warehouse_id:
        query["warehouse_id"] = validate_object_id(warehouse_id, "warehouse_id")
    if vendor_id:
        query["vendor_id"] = validate_object_id(vendor_id, "vendor_id")
    if customer_id:
        query["customer_id"] = validate_object_id(customer_id, "customer_id")
    if vehicle_id:
        query["vehicle_id"] = validate_object_id(vehicle_id, "vehicle_id")

    if from_date or to_date:
        query["created_at"] = get_utc_date_range(from_date, to_date)

    total = orders_collection.count_documents(query)
    orders = list(orders_collection.find(query).sort("created_at", -1).skip(skip).limit(limit))
    data = enrich_orders_with_references(orders)

    return {
        "success": True,
        "data": data,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "total_pages": (total + limit - 1) // limit,
        }
    }


# =========================================================
# ROUTE: GET SINGLE ORDER
# GET /orders/v1/{order_id}
# =========================================================

@router.get("/v1/{order_id}")
def get_order(order_id: str):
    obj_id = validate_object_id(order_id, "order_id")
    order = orders_collection.find_one({"_id": obj_id})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    enriched = enrich_orders_with_references([order])[0]
    return {
        "success": True,
        "data": enriched,
    }


# =========================================================
# ROUTE: UPDATE ORDER
# POST /orders/update/v1/{order_id}
# =========================================================

@router.post("/update/v1/{order_id}")
def update_order(
    order_id: str,
    data: OrderUpdate,
    current_user=Depends(get_current_user)
):
    obj_id = validate_object_id(order_id, "order_id")
    existing_order = orders_collection.find_one({"_id": obj_id})
    if not existing_order:
        raise HTTPException(status_code=404, detail="Order not found")

    current_status = existing_order.get("status", "Pending")

    if current_status in ["Delivered", "Cancelled"]:
        if data.record_status is not None:
            orders_collection.update_one(
                {"_id": obj_id},
                {"$set": {"record_status": data.record_status, "updated_at": utc_now()}}
            )
            updated = orders_collection.find_one({"_id": obj_id})
            return {
                "success": True,
                "message": "Order record status updated",
                "data": enrich_orders_with_references([updated])[0],
            }
        raise HTTPException(status_code=400, detail="Delivered or Cancelled orders cannot be edited")

    # Downwards-only and physical constraints validation for Out for Delivery sale orders
    if current_status == "Out for Delivery" and existing_order.get("type") == "sale":
        if data.warehouse_id is not None:
            wh_val = validate_warehouse(data.warehouse_id)
            if wh_val != existing_order.get("warehouse_id"):
                raise HTTPException(
                    status_code=400,
                    detail="Cannot change source warehouse for an order that is already Out for Delivery."
                )

        if data.customer_id is not None:
            cust_val = validate_customer(data.customer_id)
            if cust_val != existing_order.get("customer_id"):
                raise HTTPException(
                    status_code=400,
                    detail="Cannot change customer for an order that is already Out for Delivery."
                )

        if data.items is not None:
            if len(data.items) == 0:
                raise HTTPException(
                    status_code=400,
                    detail="Order cannot have zero items. If all items are rejected at doorstep, update status to 'Cancelled' instead."
                )

            orig_items_map: Dict[Tuple[str, str], float] = {}
            for it in existing_order.get("items", []):
                p_str = str(it.get("product_id"))
                v_str = str(it.get("variant_id"))
                orig_items_map[(p_str, v_str)] = orig_items_map.get((p_str, v_str), 0.0) + float(it.get("quantity", 0))

            for new_it in data.items:
                p_key = (str(new_it.product_id), str(new_it.variant_id))
                orig_qty = orig_items_map.get(p_key)

                if orig_qty is None:
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot add new items while Out for Delivery. Goods are already loaded on the vehicle."
                    )

                req_qty = float(new_it.quantity)
                if req_qty > orig_qty:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Cannot increase quantity while Out for Delivery (original loaded: {orig_qty}, requested: {req_qty}). "
                            f"Goods on vehicle cannot be increased on the road."
                        )
                    )

    update_data = {}
    if data.vendor_id is not None:
        update_data["vendor_id"] = validate_vendor(data.vendor_id)
    if data.customer_id is not None:
        update_data["customer_id"] = validate_customer(data.customer_id)
    if data.warehouse_id is not None:
        update_data["warehouse_id"] = validate_warehouse(data.warehouse_id)
    if data.destination_warehouse_id is not None:
        update_data["destination_warehouse_id"] = validate_warehouse(data.destination_warehouse_id)
    if existing_order.get("type") in ["purchase_to_warehouse", "Warehouse_IN"]:
        if "warehouse_id" in update_data and "destination_warehouse_id" not in update_data:
            update_data["destination_warehouse_id"] = update_data["warehouse_id"]
        elif "destination_warehouse_id" in update_data and "warehouse_id" not in update_data:
            update_data["warehouse_id"] = update_data["destination_warehouse_id"]
    if data.vehicle_id is not None:
        update_data["vehicle_id"] = validate_vehicle(data.vehicle_id)
    if data.payment_mode is not None:
        update_data["payment_mode"] = data.payment_mode
    if data.branch_id is not None:
        update_data["branch_id"] = validate_branch(data.branch_id)
    if data.assigned_employee_id is not None:
        update_data["assigned_employee_id"] = validate_assigned_employee(data.assigned_employee_id)

    gst_type = data.gst_type if data.gst_type is not None else existing_order.get("gst_type", "excluding")
    if data.gst_type is not None:
        update_data["gst_type"] = data.gst_type

    if data.items is not None:
        processed_items, subtotal, total_gst = build_items(data.items, gst_type, existing_order.get("type"))
        if existing_order.get("type") == "sale_return":
            validate_sale_return_request(
                ref_invoice_id=update_data.get("ref_invoice_id") or existing_order.get("ref_invoice_id"),
                items=processed_items,
                customer_id=update_data.get("customer_id") or existing_order.get("customer_id"),
                exclude_order_id=obj_id,
            )
        update_data["items"] = processed_items
        update_data["subtotal"] = subtotal
        update_data["total_gst"] = total_gst
    else:
        subtotal = existing_order.get("subtotal", 0)
        total_gst = existing_order.get("total_gst", 0)

    other_charges = data.other_charges if data.other_charges is not None else existing_order.get("other_charges", 0)
    discount = existing_order.get("discount", 0)
    grand_total = round(subtotal + total_gst + other_charges - discount, 2)
    if grand_total < 0:
        raise HTTPException(status_code=400, detail="Grand total cannot be negative")

    update_data["other_charges"] = round(other_charges, 2)
    update_data["grand_total"] = grand_total

    if data.status is not None:
        update_data["status"] = data.status
    if data.record_status is not None:
        update_data["record_status"] = data.record_status
    if data.notes is not None:
        update_data["notes"] = data.notes

    update_data["updated_at"] = utc_now()
    status_changed = data.status is not None and data.status != current_status

    update_op = {"$set": update_data}
    if status_changed:
        update_op["$push"] = {
            "tracking": create_tracking_entry(
                status=data.status,
                user_id=str(current_user["user_id"]),
                note=f"Order status changed from {current_status} to {data.status}",
            )
        }

    update_filter = {"_id": obj_id}
    if status_changed:
        update_filter["status"] = current_status

    result = orders_collection.update_one(update_filter, update_op)
    if status_changed and result.modified_count == 0:
        raise HTTPException(
            status_code=409,
            detail="Order status was changed by another request. Please refresh."
        )

    updated_order = orders_collection.find_one({"_id": obj_id})

    # Trigger inventory batch operations if status changed to Completed
    if status_changed and data.status == "Completed":
        try:
            if updated_order.get("type") == "purchase":
                create_stock_batches_from_purchase(updated_order, user_id=str(current_user["user_id"]))
            elif updated_order.get("type") in [
                "purchase_to_warehouse",
                "Warehouse_IN",
                "warehouse_to_vehicle",
                "vehicle_to_warehouse",
                "warehouse_to_warehouse",
            ]:
                _handle_transfer_movement(updated_order, user_id=str(current_user["user_id"]))
        except Exception as ex:
            # Rollback status back to previous status
            orders_collection.update_one(
                {"_id": obj_id},
                {
                    "$set": {"status": current_status, "updated_at": utc_now()},
                    "$pop": {"tracking": 1}
                }
            )
            raise ex

    # Trigger stock batch movement for sale orders on dispatch or vehicle assignment
    if updated_order.get("type") == "sale":
        try:
            _handle_sale_order_dispatch_movement(
                order=existing_order,
                current_status=current_status,
                new_status=data.status or current_status,
                new_vehicle_id=update_data.get("vehicle_id"),
                user_id=str(current_user["user_id"]),
            )
            updated_order = orders_collection.find_one({"_id": obj_id})
        except Exception as ex:
            if status_changed:
                orders_collection.update_one(
                    {"_id": obj_id},
                    {
                        "$set": {"status": current_status, "updated_at": utc_now()},
                        "$pop": {"tracking": 1}
                    }
                )
            raise ex

    enriched_order = enrich_orders_with_references([updated_order])[0]
    return {
        "success": True,
        "message": "Order updated successfully",
        "data": enriched_order,
    }


# =========================================================
# ROUTE: MANUAL BILLING (FIFO & COGS ENGINE)
# POST /orders/billing/v1/{order_id}
# =========================================================

@router.post("/billing/v1/{order_id}")
def manual_bill_order(
    order_id: str,
    data: ManualBillingRequest,
    current_user=Depends(get_current_user)
):
    obj_id = validate_object_id(order_id, "order_id")
    order = orders_collection.find_one({"_id": obj_id})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    order_type = order.get("type")
    if order_type not in ["sale", "sale_return"]:
        raise HTTPException(status_code=400, detail="Manual billing is only allowed for sale and sale_return orders")

    if order.get("record_status", "active") != "active":
        raise HTTPException(status_code=400, detail="Inactive orders cannot be billed")

    if order.get("invoice_no"):
        raise HTTPException(status_code=400, detail=f"Order is already billed with invoice {order['invoice_no']}")

    subtotal = float(order.get("subtotal", 0))
    total_gst = float(order.get("total_gst", 0))
    other_charges = float(order.get("other_charges", 0))
    discount = float(data.discount_amount or 0)
    gross_total = round(subtotal + total_gst + other_charges, 2)

    if discount < 0:
        raise HTTPException(status_code=400, detail="Discount cannot be negative")
    if discount > gross_total:
        raise HTTPException(status_code=400, detail="Discount cannot exceed total bill amount")

    grand_total = round(gross_total - discount, 2)
    total_cogs = 0.0
    items = order.get("items", [])


    # -----------------------------------------------------
    # 1. SALE: FIFO BATCH ALLOCATION
    # -----------------------------------------------------
    if order_type == "sale":
        warehouse_id = order.get("warehouse_id")
        vehicle_id = order.get("vehicle_id")

        for item in items:
            p_id = item.get("product_id")
            v_id = item.get("variant_id")
            qty = float(item.get("quantity", 0))
            sale_rate = float(item.get("rate", 0))
            item_id = item.get("item_id")

            consumptions, item_cogs = allocate_fifo_from_batches(
                product_id=p_id,
                variant_id=v_id,
                warehouse_id=warehouse_id,
                vehicle_id=vehicle_id,
                required_quantity=qty,
            )

            item["batch_consumptions"] = consumptions
            item["cogs"] = round(item_cogs, 2)
            total_cogs += item_cogs

            # Record immutable audit trail
            record_sale_consumptions(
                sale_order_id=obj_id,
                sale_item_id=item_id,
                product_id=p_id,
                variant_id=v_id,
                warehouse_id=warehouse_id,
                vehicle_id=vehicle_id,
                consumptions=consumptions,
                sale_rate=sale_rate,
            )

    # -----------------------------------------------------
    # 2. SALE RETURN: REVERSE FIFO BATCH CONSUMPTION
    # -----------------------------------------------------

    
    elif order_type == "sale_return":
        ref_inv_id = order.get("ref_invoice_id")
        if not ref_inv_id:
            raise HTTPException(status_code=400, detail="ref_invoice_id is required for sales return")

        ret_wh_id = order.get("destination_warehouse_id") or order.get("warehouse_id")
        ret_veh_id = order.get("vehicle_id")

        for item in items:
            ref_item_id = item.get("ref_item_id")
            qty = float(item.get("quantity", 0))

            restored_batches, returned_cogs = reverse_sale_consumptions(
                original_sale_order_id=ref_inv_id,
                ref_item_id=ref_item_id,
                return_quantity=qty,
                return_warehouse_id=ret_wh_id,
                return_vehicle_id=ret_veh_id,
                user_id=str(current_user["user_id"]),
                return_order_id=obj_id,
            )

            item["batch_consumptions"] = restored_batches
            item["cogs"] = round(returned_cogs, 2)
            total_cogs += returned_cogs

    total_cogs = round(total_cogs, 2)
    net_sales = round(subtotal - discount, 2)
    gross_profit = round(net_sales - total_cogs, 2)

    invoice_no = generate_invoice_no(order_type)
    now = utc_now()

    status = "Completed" if order_type == "sale_return" else "Delivered"
    billing_tracking = create_tracking_entry(
        status="Billed",
        user_id=str(current_user["user_id"]),
        note=f"{order_type} billed with invoice {invoice_no}. Discount: ₹{discount:.2f}, COGS: ₹{total_cogs:.2f}",
    )
    credit_days = 15
    if order.get("customer_id"):
        cust_doc = customers_collection.find_one({"_id": order["customer_id"]})
        if cust_doc and cust_doc.get("credit_days"):
            try:
                credit_days = int(cust_doc["credit_days"])
            except Exception:
                credit_days = 15
    due_date = now + timedelta(days=credit_days)

    # Atomic Billing Update
    update_res = orders_collection.update_one(
        {
            "_id": obj_id,
            "type": order_type,
            "record_status": "active",
            "$or": [{"invoice_no": None}, {"invoice_no": {"$exists": False}}, {"invoice_no": ""}],
        },
        {
            "$set": {
                "invoice_no": invoice_no,
                "discount": discount,
                "grand_total": grand_total,
                "bill_amount": grand_total,
                "paid_amount": 0.0,
                "pending_amount": grand_total,
                "credit_days": credit_days,
                "due_date": due_date,
                "payment_status": "UNPAID",
                "items": items,
                "total_cogs": total_cogs,
                "gross_profit": gross_profit,
                "status": status,
                "billed_at": now,
                "billed_by": validate_object_id(str(current_user["user_id"]), "user_id"),
                "invoice_whatsapp_sent": False,
                "invoice_whatsapp_status": "pending",
                "updated_at": now,
            },
            "$push": {"tracking": billing_tracking}
        }
    )

    if update_res.modified_count == 0:
        raise HTTPException(status_code=409, detail="Order was already billed or modified concurrently.")

    updated_order = orders_collection.find_one({"_id": obj_id})
    enriched_order = enrich_orders_with_references([updated_order])[0]

    # Generate Sales Invoice / Return Voucher in accounting ledger
    if order_type == "sale":
        try:
            from services.accounting_service import create_sales_invoice_voucher
            create_sales_invoice_voucher(
                order=updated_order,
                invoice_no=invoice_no,
                user_id=str(current_user["user_id"]),
            )
        except Exception as voucher_ex:
            print(f"Warning: Could not create sales invoice voucher for {invoice_no}: {voucher_ex}")

    elif order_type == "sale_return":
        try:
            from services.accounting_service import create_sales_return_voucher
            create_sales_return_voucher(
                order=updated_order,
                invoice_no=invoice_no,
                user_id=str(current_user["user_id"]),
            )
        except Exception as voucher_ex:
            print(f"Warning: Could not create sales return voucher for {invoice_no}: {voucher_ex}")

    # Automated WhatsApp Notification
    whatsapp_sent = False
    whatsapp_err = None
    try:
        send_invoice_template_whatsapp(enriched_order)
        whatsapp_sent = True
        orders_collection.update_one(
            {"_id": obj_id},
            {
                "$set": {
                    "invoice_whatsapp_sent": True,
                    "invoice_whatsapp_status": "sent",
                    "invoice_whatsapp_sent_at": utc_now(),
                    "invoice_whatsapp_error": None,
                }
            }
        )
    except Exception as ex:
        whatsapp_err = str(ex)
        orders_collection.update_one(
            {"_id": obj_id},
            {
                "$set": {
                    "invoice_whatsapp_sent": False,
                    "invoice_whatsapp_status": "failed",
                    "invoice_whatsapp_error": whatsapp_err,
                }
            }
        )

    return {
        "success": True,
        "message": f"{'Sale' if order_type == 'sale' else 'Sales return'} billed successfully" + (" and sent via WhatsApp" if whatsapp_sent else ""),
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
        "whatsapp_error": whatsapp_err,
        "pdf_available": True,
    }





# =========================================================
# ROUTE: GET PDF INVOICE
# GET /orders/get-bill/v1/{order_id}/pdf
# =========================================================

@router.get(
    "/get-bill/v1/{order_id}/pdf",
    response_class=StreamingResponse,
    responses={200: {"content": {"application/pdf": {}}, "description": "Generated sale invoice PDF"}}
)
def get_bill_pdf(order_id: str):
    obj_id = validate_object_id(order_id, "order_id")
    order = orders_collection.find_one({"_id": obj_id})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    enriched = enrich_orders_with_references([order])[0]
    pdf_bytes = generate_invoice_pdf(enriched)
    invoice_no = order.get("invoice_no") or order.get("order_no") or "order"

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="Invoice_{invoice_no}.pdf"'}
    )


# =========================================================
# ROUTE: RESEND WHATSAPP INVOICE
# POST /orders/resend-bill/v1/{order_id}/whatsapp
# =========================================================

@router.post("/resend-bill/v1/{order_id}/whatsapp")
def resend_bill_whatsapp(
    order_id: str,
    data: Optional[ManualBillingRequest] = None,
    current_user=Depends(get_current_user)
):
    obj_id = validate_object_id(order_id, "order_id")
    order = orders_collection.find_one({"_id": obj_id})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.get("type") not in ["sale", "sale_return"]:
        raise HTTPException(status_code=400, detail="Invoices can only be resent for sale/sale_return orders")

    if not order.get("invoice_no"):
        raise HTTPException(status_code=400, detail="Order must be billed before sending invoice")

    enriched = enrich_orders_with_references([order])[0]
    send_invoice_template_whatsapp(enriched)

    now = utc_now()
    orders_collection.update_one(
        {"_id": obj_id},
        {
            "$set": {
                "invoice_whatsapp_sent": True,
                "invoice_whatsapp_status": "sent",
                "invoice_whatsapp_sent_at": now,
                "invoice_whatsapp_error": None,
                "updated_at": now,
            },
            "$push": {
                "tracking": create_tracking_entry(
                    status=order.get("status", "Delivered"),
                    user_id=str(current_user["user_id"]),
                    note=f"Invoice {order.get('invoice_no')} resent to customer via WhatsApp",
                )
            }
        }
    )

    return {
        "success": True,
        "message": f"Invoice {order.get('invoice_no')} sent successfully via WhatsApp",
        "order_id": order_id,
        "invoice_no": order.get("invoice_no"),
        "sent_at": now,
    }


# =========================================================
# ROUTE: UPDATE STATUS
# POST /orders/status/v1/{order_id}
# =========================================================

@router.post("/status/v1/{order_id}")
def update_order_status(
    order_id: str,
    data: OrderStatusUpdate,
    current_user=Depends(get_current_user)
):
    obj_id = validate_object_id(order_id, "order_id")
    order = orders_collection.find_one({"_id": obj_id})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    current_status = order.get("status", "Pending")
    if current_status == data.status:
        raise HTTPException(status_code=400, detail=f"Order is already {data.status}")
    if current_status in ["Delivered", "Cancelled"]:
        raise HTTPException(status_code=400, detail=f"Order is already {current_status} and cannot be modified")

    update_fields = {
        "status": data.status,
        "updated_at": utc_now(),
    }
    if data.vehicle_id:
        update_fields["vehicle_id"] = validate_vehicle(data.vehicle_id)
    if data.warehouse_id:
        update_fields["warehouse_id"] = validate_warehouse(data.warehouse_id)
    if data.destination_warehouse_id:
        update_fields["destination_warehouse_id"] = validate_warehouse(data.destination_warehouse_id)

    tracking_entry = create_tracking_entry(
        status=data.status,
        user_id=str(current_user["user_id"]),
        note=data.note or f"Order status changed from {current_status} to {data.status}",
    )

    result = orders_collection.update_one(
        {"_id": obj_id, "status": current_status},
        {
            "$set": update_fields,
            "$push": {"tracking": tracking_entry}
        }
    )

    if result.modified_count == 0:
        raise HTTPException(status_code=409, detail="Order status was changed concurrently. Please refresh.")

    updated_order = orders_collection.find_one({"_id": obj_id})

    # Trigger stock batch creation or transfer on Completed status
    if data.status == "Completed":
        try:
            if updated_order.get("type") == "purchase":
                create_stock_batches_from_purchase(updated_order, user_id=str(current_user["user_id"]))
            elif updated_order.get("type") in [
                "purchase_to_warehouse",
                "Warehouse_IN",
                "warehouse_to_vehicle",
                "vehicle_to_warehouse",
                "warehouse_to_warehouse",
            ]:
                _handle_transfer_movement(updated_order, user_id=str(current_user["user_id"]))
        except Exception as ex:
            # ROLLBACK: Revert status back to current_status
            orders_collection.update_one(
                {"_id": obj_id},
                {
                    "$set": {"status": current_status, "updated_at": utc_now()},
                    "$pop": {"tracking": 1}
                }
            )
            raise ex

    # Trigger stock batch movement for sale orders on dispatch (Out for Delivery) or reversal
    if updated_order.get("type") == "sale":
        try:
            _handle_sale_order_dispatch_movement(
                order=order,
                current_status=current_status,
                new_status=data.status,
                new_vehicle_id=update_fields.get("vehicle_id"),
                user_id=str(current_user["user_id"]),
            )
            updated_order = orders_collection.find_one({"_id": obj_id})
        except Exception as ex:
            # ROLLBACK: Revert status back to current_status
            orders_collection.update_one(
                {"_id": obj_id},
                {
                    "$set": {"status": current_status, "updated_at": utc_now()},
                    "$pop": {"tracking": 1}
                }
            )
            raise ex

    enriched = enrich_orders_with_references([updated_order])[0]
    return {
        "success": True,
        "message": "Order status updated successfully",
        "data": enriched,
    }


# =========================================================
# ROUTE: BULK ORDER DISPATCH (ALL-OR-NOTHING WITH TRIP MANIFEST)
# POST /orders/bulk-dispatch/v1
# =========================================================

@router.post("/bulk-dispatch/v1")
def bulk_dispatch_orders(
    data: BulkOrderDispatchRequest,
    current_user=Depends(get_current_user)
):
    """
    Dispatches multiple sale orders in a single atomic bulk operation:
    1. Validates all orders exist, are active, not delivered/cancelled, and share the same warehouse.
    2. Performs All-or-Nothing pre-flight physical stock check across aggregate trip demand.
    3. Executes atomic stock transfers per order, tagging each with manifest_id and manifest_no.
    4. Generates a unified Driver Loading Manifest (Trip Sheet) and saves it permanently.
    """
    if not data.order_ids:
        raise HTTPException(status_code=400, detail="order_ids list cannot be empty")

    user_id_str = str(current_user["user_id"])
    obj_ids = [validate_object_id(oid, "order_id") for oid in data.order_ids]

    orders = list(orders_collection.find({"_id": {"$in": obj_ids}}))
    if len(orders) != len(obj_ids):
        found_ids = {o["_id"] for o in orders}
        missing = [str(oid) for oid in obj_ids if oid not in found_ids]
        raise HTTPException(status_code=404, detail=f"Orders not found: {missing}")

    # Validate order types and statuses
    for o in orders:
        if o.get("type") != "sale":
            raise HTTPException(
                status_code=400,
                detail=f"Order '{o.get('order_no') or str(o['_id'])}' is type '{o.get('type')}'. Only sale orders can be dispatched for delivery."
            )
        if o.get("status") in ["Delivered", "Cancelled"]:
            raise HTTPException(
                status_code=400,
                detail=f"Order '{o.get('order_no') or str(o['_id'])}' is already {o.get('status')} and cannot be dispatched."
            )

    # Validate source warehouse consistency
    warehouse_ids = set()
    for o in orders:
        wid = o.get("warehouse_id")
        if not wid:
            raise HTTPException(
                status_code=400,
                detail=f"Order '{o.get('order_no') or str(o['_id'])}' has no warehouse_id assigned."
            )
        warehouse_ids.add(wid)

    if len(warehouse_ids) > 1:
        wh_names = []
        for wid in warehouse_ids:
            wh_doc = warehouses_collection.find_one({"_id": wid})
            wh_names.append(wh_doc.get("name") if wh_doc else str(wid))
        raise HTTPException(
            status_code=400,
            detail=f"All orders in a bulk dispatch must originate from the same warehouse. Found: {wh_names}"
        )

    src_warehouse_id = list(warehouse_ids)[0]
    wh_doc = warehouses_collection.find_one({"_id": src_warehouse_id})

    # Validate vehicle (if provided)
    target_vehicle_id = None
    veh_doc = None
    if data.vehicle_id:
        target_vehicle_id = validate_vehicle(data.vehicle_id)
        veh_doc = vehicles_collection.find_one({"_id": target_vehicle_id})

    now = utc_now()
    manifest_id = ObjectId()
    manifest_no = generate_manifest_no()

    # -------------------------------------------------------------
    # ALL-OR-NOTHING PRE-FLIGHT STOCK CHECK
    # -------------------------------------------------------------
    trip_demand: Dict[Tuple[ObjectId, ObjectId], float] = {}
    for o in orders:
        for it in o.get("items", []):
            p_id = it.get("product_id")
            v_id = it.get("variant_id")
            if isinstance(p_id, str) and ObjectId.is_valid(p_id):
                p_id = ObjectId(p_id)
            if isinstance(v_id, str) and ObjectId.is_valid(v_id):
                v_id = ObjectId(v_id)
            qty = float(it.get("quantity", 0))
            if p_id and v_id and qty > 0:
                trip_demand[(p_id, v_id)] = trip_demand.get((p_id, v_id), 0.0) + qty

    shortages = []
    for (p_id, v_id), total_req in trip_demand.items():
        breakdown = get_sellable_stock_breakdown(
            product_id=p_id,
            variant_id=v_id,
            warehouse_id=src_warehouse_id,
        )
        phys_stock = breakdown["physical_stock"]
        if total_req > phys_stock:
            prod = products_collection.find_one({"_id": p_id})
            var = product_variants_collection.find_one({"_id": v_id})
            shortages.append({
                "product_name": prod.get("name") if prod else str(p_id),
                "variant_name": var.get("name") if var else str(v_id),
                "sku": var.get("sku") if var else "",
                "requested_quantity": round(total_req, 4),
                "warehouse_physical_stock": round(phys_stock, 4),
                "shortfall": round(total_req - phys_stock, 4),
            })

    if shortages:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "INSUFFICIENT_WAREHOUSE_STOCK",
                "message": "Cannot dispatch bulk orders: Warehouse has insufficient physical stock for this trip. No stock was transferred.",
                "shortages": shortages,
            }
        )

    # -------------------------------------------------------------
    # EXECUTE PER-ORDER TRANSFERS & UPDATE ORDERS
    # -------------------------------------------------------------
    for o in orders:
        # Transfer order items to vehicle with manifest tags only if vehicle provided
        if target_vehicle_id:
            transfer_stock_batches(
                source_type="warehouse",
                source_id=src_warehouse_id,
                dest_type="vehicle",
                dest_id=target_vehicle_id,
                items=o.get("items", []),
                transfer_order_id=o["_id"],
                user_id=user_id_str,
                allocation_type="warehouse_to_vehicle",
                manifest_id=manifest_id,
                manifest_no=manifest_no,
            )

        veh_number_str = veh_doc.get("vehicle_number") if veh_doc else (str(target_vehicle_id) if target_vehicle_id else None)
        note_suffix = f" on vehicle {veh_number_str}" if veh_number_str else " without vehicle assignment (stock remains in warehouse)"
        tracking_entry = create_tracking_entry(
            status=data.status,
            user_id=user_id_str,
            note=data.note or f"Dispatched Out for Delivery under Manifest {manifest_no}{note_suffix}.",
        )

        update_set: Dict[str, Any] = {
            "status": data.status,
            "manifest_id": manifest_id,
            "manifest_no": manifest_no,
            "updated_at": now,
        }
        if target_vehicle_id:
            update_set["vehicle_id"] = target_vehicle_id

        orders_collection.update_one(
            {"_id": o["_id"]},
            {
                "$set": update_set,
                "$push": {"tracking": tracking_entry},
            }
        )

    # -------------------------------------------------------------
    # BUILD UNIFIED DRIVER LOADING MANIFEST
    # -------------------------------------------------------------
    manifest_doc = _create_delivery_manifest_document(
        orders=orders,
        warehouse_id=src_warehouse_id,
        vehicle_id=target_vehicle_id,
        user_id=user_id_str,
        route_name=data.route_name,
        notes=data.note,
        status="active",
        manifest_id=manifest_id,
        manifest_no=manifest_no,
    )

    serialized_manifest = convert_utc_to_ist(serialize_order(manifest_doc))
    msg = (
        f"Bulk dispatch completed successfully. Created Trip Manifest {manifest_no}."
        if target_vehicle_id
        else f"Updated {len(orders)} orders to '{data.status}' under Manifest {manifest_no}. Stock remains in warehouse (Option 2 - no vehicle assigned)."
    )
    return {
        "success": True,
        "message": msg,
        "manifest": serialized_manifest,
    }


# =========================================================
# ROUTE: GET DRIVER LOADING MANIFEST BY ID OR NUMBER
# GET /orders/manifest/v1/{manifest_id_or_no}
# =========================================================

@router.get("/manifest/v1/{manifest_id_or_no}")
def get_manifest(manifest_id_or_no: str):
    query: Dict[str, Any] = {}
    if ObjectId.is_valid(manifest_id_or_no):
        query = {"$or": [{"_id": ObjectId(manifest_id_or_no)}, {"manifest_no": manifest_id_or_no}]}
    else:
        query = {"manifest_no": manifest_id_or_no}

    doc = delivery_manifests_collection.find_one(query)
    if not doc:
        raise HTTPException(status_code=404, detail="Trip manifest not found")

    return {
        "success": True,
        "data": convert_utc_to_ist(serialize_order(doc)),
    }


# =========================================================
# ROUTE: LIST TRIP MANIFESTS
# GET /orders/manifests/v1
# =========================================================

@router.get("/manifests/v1")
def list_manifests(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    vehicle_id: Optional[str] = None,
    warehouse_id: Optional[str] = None,
):
    page_val = int(page.default if hasattr(page, "default") else page)
    limit_val = int(limit.default if hasattr(limit, "default") else limit)

    query: Dict[str, Any] = {}
    if vehicle_id:
        query["vehicle.id"] = validate_object_id(vehicle_id, "vehicle_id")
    if warehouse_id:
        query["warehouse.id"] = validate_object_id(warehouse_id, "warehouse_id")

    total = delivery_manifests_collection.count_documents(query)
    cursor = delivery_manifests_collection.find(query).sort([("created_at", -1)]).skip((page_val - 1) * limit_val).limit(limit_val)
    manifests = [convert_utc_to_ist(serialize_order(m)) for m in cursor]

    return {
        "success": True,
        "data": manifests,
        "pagination": {
            "page": page_val,
            "limit": limit_val,
            "total": total,
            "total_pages": (total + limit_val - 1) // limit_val if limit_val else 1,
        }
    }


# =========================================================
# ROUTE: UPDATE RECORD STATUS (SOFT DELETE)
# POST /orders/record-status/v1/{order_id}
# =========================================================

@router.post("/record-status/v1/{order_id}")
def update_record_status(order_id: str, data: RecordStatusUpdate):
    obj_id = validate_object_id(order_id, "order_id")
    order = orders_collection.find_one({"_id": obj_id})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    orders_collection.update_one(
        {"_id": obj_id},
        {"$set": {"record_status": data.record_status, "updated_at": utc_now()}}
    )
    updated = orders_collection.find_one({"_id": obj_id})
    return {
        "success": True,
        "message": f"Order {'activated' if data.record_status == 'active' else 'inactivated'} successfully",
        "data": enrich_orders_with_references([updated])[0],
    }


# =========================================================
# ROUTE: BATCH TRACEABILITY AUDIT
# GET /orders/v1/{order_id}/batch-traceability
# =========================================================

@router.get("/v1/{order_id}/batch-traceability")
def get_order_batch_traceability(order_id: str):
    """
    Returns full batch audit trail for the order:
    - For purchase: created batches in stock_batches.
    - For sale: consumptions in sale_batch_consumptions.
    - For transfer: allocations in stock_batch_allocations.
    """
    obj_id = validate_object_id(order_id, "order_id")
    order = orders_collection.find_one({"_id": obj_id})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    order_type = order.get("type")
    traceability_data: dict = {
        "order_id": order_id,
        "order_no": order.get("order_no"),
        "order_type": order_type,
        "status": order.get("status"),
        "batches_created": [],
        "batch_consumptions": [],
        "batch_allocations": [],
    }

    if order_type == "purchase":
        batches = list(stock_batches_collection.find({"purchase_order_id": obj_id}))
        traceability_data["batches_created"] = serialize_value(batches)
    elif order_type == "sale":
        consumptions = list(sale_batch_consumptions_collection.find({"sale_order_id": obj_id}))
        traceability_data["batch_consumptions"] = serialize_value(consumptions)
        allocations = list(stock_batch_allocations_collection.find({"transfer_order_id": obj_id}))
        traceability_data["batch_allocations"] = serialize_value(allocations)
    elif order_type == "sale_return":
        allocations = list(stock_batch_allocations_collection.find({"transfer_order_id": obj_id}))
        traceability_data["batch_allocations"] = serialize_value(allocations)
    else:
        allocations = list(stock_batch_allocations_collection.find({"transfer_order_id": obj_id}))
        traceability_data["batch_allocations"] = serialize_value(allocations)

    return {
        "success": True,
        "data": convert_utc_to_ist(traceability_data),
    }


# =========================================================
# ROUTE: AUDIT VEHICLE STOCK ALLOCATIONS & TRANSFERS
# GET /orders/vehicle-allocations/v1/{vehicle_id}
# =========================================================

@router.get("/vehicle-allocations/v1/{vehicle_id}")
def get_vehicle_stock_allocations(
    vehicle_id: str,
    manifest_id_or_no: Optional[str] = None,
    allocation_type: Optional[str] = None,
    product_id: Optional[str] = None,
    variant_id: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
):
    """
    Audits all stock batch allocations and transfers associated with a specific vehicle:
    - Inflow: warehouse_to_vehicle (loading / bulk dispatch)
    - Outflow: vehicle_to_warehouse (end-of-day return / cancellation return)
    - Reassignment: vehicle_to_vehicle
    Filterable by manifest_no, manifest_id, allocation_type, product_id, and variant_id.
    """
    veh_obj = validate_object_id(vehicle_id, "vehicle_id")
    page_val = int(page.default if hasattr(page, "default") else page)
    limit_val = int(limit.default if hasattr(limit, "default") else limit)

    query: Dict[str, Any] = {
        "$or": [
            {"from_location.id": veh_obj},
            {"to_location.id": veh_obj},
        ]
    }
    if manifest_id_or_no:
        if ObjectId.is_valid(manifest_id_or_no):
            query["$and"] = [{"$or": [{"manifest_id": ObjectId(manifest_id_or_no)}, {"manifest_no": manifest_id_or_no}]}]
        else:
            query["manifest_no"] = manifest_id_or_no

    if allocation_type:
        query["allocation_type"] = allocation_type

    if product_id:
        query["product_id"] = validate_object_id(product_id, "product_id")

    if variant_id:
        query["variant_id"] = validate_object_id(variant_id, "variant_id")

    total = stock_batch_allocations_collection.count_documents(query)
    cursor = stock_batch_allocations_collection.find(query).sort([("created_at", -1)]).skip((page_val - 1) * limit_val).limit(limit_val)
    raw_allocations = list(cursor)

    enriched = []
    for a in raw_allocations:
        item = serialize_value(a)
        if a.get("product_id"):
            prod = products_collection.find_one({"_id": a["product_id"]}, {"name": 1})
            item["product_name"] = prod.get("name") if prod else None
        if a.get("variant_id"):
            var = product_variants_collection.find_one({"_id": a["variant_id"]}, {"name": 1, "sku": 1})
            item["variant_name"] = var.get("name") if var else None
            item["sku"] = var.get("sku") if var else None
        if a.get("transfer_order_id"):
            ord_doc = orders_collection.find_one({"_id": a["transfer_order_id"]}, {"order_no": 1, "type": 1, "status": 1})
            if ord_doc:
                item["order_no"] = ord_doc.get("order_no")
                item["order_status"] = ord_doc.get("status")
        if a.get("allocated_by"):
            u = users_collection.find_one({"_id": a["allocated_by"]}, {"name": 1, "username": 1})
            item["allocated_by_name"] = (u.get("name") or u.get("username")) if u else None

        from_loc = a.get("from_location", {})
        to_loc = a.get("to_location", {})
        if to_loc.get("id") == veh_obj:
            item["direction"] = "INWARD"
            item["direction_label"] = "Loaded into Vehicle"
        elif from_loc.get("id") == veh_obj:
            item["direction"] = "OUTWARD"
            item["direction_label"] = "Unloaded from Vehicle"
        else:
            item["direction"] = "TRANSFER"
            item["direction_label"] = a.get("allocation_type")

        enriched.append(item)

    return {
        "success": True,
        "vehicle_id": str(veh_obj),
        "total": total,
        "page": page_val,
        "limit": limit_val,
        "allocations": convert_utc_to_ist(enriched),
    }


# =========================================================
# ROUTE: AUDIT WAREHOUSE STOCK ALLOCATIONS & TRANSFERS
# GET /orders/warehouse-allocations/v1/{warehouse_id}
# =========================================================

@router.get("/warehouse-allocations/v1/{warehouse_id}")
def get_warehouse_stock_allocations(
    warehouse_id: str,
    allocation_type: Optional[str] = None,
    product_id: Optional[str] = None,
    variant_id: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
):
    """
    Audits all stock batch allocations and physical movements for a warehouse:
    - Inflow: purchase_to_warehouse, vehicle_to_warehouse (unloading / return), warehouse_to_warehouse (received)
    - Outflow: warehouse_to_vehicle (loading / dispatch), warehouse_to_warehouse (sent)
    Filterable by allocation_type, product_id, and variant_id.
    """
    wh_obj = validate_object_id(warehouse_id, "warehouse_id")
    page_val = int(page.default if hasattr(page, "default") else page)
    limit_val = int(limit.default if hasattr(limit, "default") else limit)

    query: Dict[str, Any] = {
        "$or": [
            {"from_location.id": wh_obj},
            {"to_location.id": wh_obj},
        ]
    }
    if allocation_type:
        query["allocation_type"] = allocation_type

    if product_id:
        query["product_id"] = validate_object_id(product_id, "product_id")

    if variant_id:
        query["variant_id"] = validate_object_id(variant_id, "variant_id")

    total = stock_batch_allocations_collection.count_documents(query)
    cursor = stock_batch_allocations_collection.find(query).sort([("created_at", -1)]).skip((page_val - 1) * limit_val).limit(limit_val)
    raw_allocations = list(cursor)

    enriched = []
    for a in raw_allocations:
        item = serialize_value(a)
        if a.get("product_id"):
            prod = products_collection.find_one({"_id": a["product_id"]}, {"name": 1})
            item["product_name"] = prod.get("name") if prod else None
        if a.get("variant_id"):
            var = product_variants_collection.find_one({"_id": a["variant_id"]}, {"name": 1, "sku": 1})
            item["variant_name"] = var.get("name") if var else None
            item["sku"] = var.get("sku") if var else None
        if a.get("transfer_order_id"):
            ord_doc = orders_collection.find_one({"_id": a["transfer_order_id"]}, {"order_no": 1, "type": 1, "status": 1})
            if ord_doc:
                item["order_no"] = ord_doc.get("order_no")
                item["order_status"] = ord_doc.get("status")
        if a.get("allocated_by"):
            u = users_collection.find_one({"_id": a["allocated_by"]}, {"name": 1, "username": 1})
            item["allocated_by_name"] = (u.get("name") or u.get("username")) if u else None

        from_loc = a.get("from_location", {})
        to_loc = a.get("to_location", {})
        if to_loc.get("id") == wh_obj:
            item["direction"] = "INWARD"
            item["direction_label"] = "Received into Warehouse"
        elif from_loc.get("id") == wh_obj:
            item["direction"] = "OUTWARD"
            item["direction_label"] = "Dispatched from Warehouse"
        else:
            item["direction"] = "TRANSFER"
            item["direction_label"] = a.get("allocation_type")

        enriched.append(item)

    return {
        "success": True,
        "warehouse_id": str(wh_obj),
        "total": total,
        "page": page_val,
        "limit": limit_val,
        "allocations": convert_utc_to_ist(enriched),
    }


# =========================================================
# ROUTE: SYNC HISTORICAL PURCHASE BATCHES
# POST /orders/v1/sync-batches
# =========================================================

@router.post("/v1/sync-batches")
def sync_purchase_batches(current_user=Depends(get_current_user)):
    """
    Backfills stock_batches for completed purchase orders that do not yet
    have stock batch records in the database.
    """
    synced_count = sync_existing_purchases_to_batches()
    return {
        "success": True,
        "message": f"Successfully synced {synced_count} completed purchase orders to stock batches",
        "synced_count": synced_count,
    }



# =========================================================
# INTERNAL HELPER: TRANSFER MOVEMENTS
# =========================================================

def _handle_transfer_movement(order_doc: dict, user_id: Optional[str] = None):
    """
    Executes physical batch transfer for inter-warehouse and vehicle transfers.
    """
    order_type = order_doc.get("type")
    items = order_doc.get("items", [])
    order_id = order_doc.get("_id")

    if order_type in ["purchase_to_warehouse", "Warehouse_IN"]:
        dest_id = order_doc.get("destination_warehouse_id") or order_doc.get("warehouse_id")
        if not dest_id:
            raise HTTPException(
                status_code=400,
                detail="destination_warehouse_id or warehouse_id is required to allocate stock to a warehouse."
            )
        transfer_stock_batches(
            source_type="unallocated",
            source_id=None,
            dest_type="warehouse",
            dest_id=dest_id,
            items=items,
            transfer_order_id=order_id,
            user_id=user_id,
        )
    elif order_type == "warehouse_to_vehicle":
        src_id = order_doc.get("warehouse_id")
        dest_id = order_doc.get("vehicle_id")
        if src_id and dest_id:
            transfer_stock_batches(
                source_type="warehouse",
                source_id=src_id,
                dest_type="vehicle",
                dest_id=dest_id,
                items=items,
                transfer_order_id=order_id,
                user_id=user_id,
            )
    elif order_type == "vehicle_to_warehouse":
        src_id = order_doc.get("vehicle_id")
        dest_id = order_doc.get("warehouse_id")
        if src_id and dest_id:
            transfer_stock_batches(
                source_type="vehicle",
                source_id=src_id,
                dest_type="warehouse",
                dest_id=dest_id,
                items=items,
                transfer_order_id=order_id,
                user_id=user_id,
            )
    elif order_type == "warehouse_to_warehouse":
        src_id = order_doc.get("warehouse_id")
        dest_id = order_doc.get("destination_warehouse_id")
        if src_id and dest_id:
            transfer_stock_batches(
                source_type="warehouse",
                source_id=src_id,
                dest_type="warehouse",
                dest_id=dest_id,
                items=items,
                transfer_order_id=order_id,
                user_id=user_id,
            )


def _handle_sale_order_dispatch_movement(
    order: Dict[str, Any],
    current_status: str,
    new_status: str,
    new_vehicle_id: Optional[ObjectId],
    user_id: str,
) -> None:
    """
    Handles automatic stock batch transfers for sale orders:
    1. Dispatch to vehicle: when moving to 'Out for Delivery' with a vehicle assigned,
       or when assigning a vehicle to an order already in 'Out for Delivery'.
       (If vehicle_id is not provided, transfer is skipped per Option 2).
    2. Reversal from vehicle: when moving from 'Out for Delivery' (with vehicle)
       back to 'Ready to Pick Up', 'Pending', or 'Cancelled'.
    3. Reassignment between vehicles: when vehicle changes while 'Out for Delivery'.
    """
    if order.get("type") != "sale":
        return

    order_id = order["_id"]
    items = order.get("items", [])
    if not items:
        return

    orig_vehicle_id = order.get("vehicle_id")
    effective_vehicle_id = new_vehicle_id or orig_vehicle_id
    warehouse_id = order.get("warehouse_id")

    # A. Reassignment between vehicles while Out for Delivery
    if (
        current_status == "Out for Delivery"
        and (new_status == "Out for Delivery" or not new_status)
        and orig_vehicle_id
        and new_vehicle_id
        and orig_vehicle_id != new_vehicle_id
    ):
        transfer_stock_batches(
            source_type="vehicle",
            source_id=orig_vehicle_id,
            dest_type="vehicle",
            dest_id=new_vehicle_id,
            items=items,
            transfer_order_id=order_id,
            user_id=user_id,
            allocation_type="vehicle_to_vehicle",
        )
        return

    # B. Dispatch -> Transfer from warehouse to vehicle & Auto-generate Manifest if missing
    is_becoming_out_for_delivery = (new_status == "Out for Delivery" and current_status != "Out for Delivery")
    is_assigning_vehicle_while_out = (current_status == "Out for Delivery" and not orig_vehicle_id and new_vehicle_id)

    if (is_becoming_out_for_delivery or is_assigning_vehicle_while_out) and warehouse_id:
        manifest_id = order.get("manifest_id")
        manifest_no = order.get("manifest_no")
        created_manifest_id = None

        if not manifest_id:
            veh_suffix = f" on vehicle {effective_vehicle_id}" if effective_vehicle_id else " without vehicle assignment"
            manifest_doc = _create_delivery_manifest_document(
                orders=[order],
                warehouse_id=warehouse_id,
                vehicle_id=effective_vehicle_id,
                user_id=user_id,
                notes=f"Single order dispatch: {order.get('order_no', str(order_id))}{veh_suffix}",
            )
            manifest_id = manifest_doc["_id"]
            manifest_no = manifest_doc["manifest_no"]
            created_manifest_id = manifest_id
            orders_collection.update_one(
                {"_id": order_id},
                {"$set": {"manifest_id": manifest_id, "manifest_no": manifest_no}}
            )
        elif is_assigning_vehicle_while_out and effective_vehicle_id:
            veh_doc = vehicles_collection.find_one({"_id": effective_vehicle_id})
            delivery_manifests_collection.update_one(
                {"_id": manifest_id},
                {
                    "$set": {
                        "vehicle": {
                            "id": effective_vehicle_id,
                            "vehicle_number": veh_doc.get("vehicle_number") if veh_doc else None,
                            "model": veh_doc.get("model") if veh_doc else None,
                        },
                        "updated_at": utc_now(),
                    }
                }
            )

        if effective_vehicle_id:
            try:
                # 1. Validate physical stock in warehouse before transferring
                for it in items:
                    p_id = it.get("product_id")
                    v_id = it.get("variant_id")
                    req_qty = float(it.get("quantity", 0))
                    if req_qty <= 0 or not (p_id and v_id):
                        continue

                    breakdown = get_sellable_stock_breakdown(
                        product_id=p_id,
                        variant_id=v_id,
                        warehouse_id=warehouse_id,
                    )
                    if req_qty > breakdown["physical_stock"]:
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                f"Cannot dispatch '{it.get('product_name') or 'item'}': "
                                f"Insufficient physical stock in warehouse. "
                                f"Requested: {req_qty}, Available in warehouse: {breakdown['physical_stock']}."
                            )
                        )

                # 2. Transfer stock from warehouse to vehicle
                transfer_stock_batches(
                    source_type="warehouse",
                    source_id=warehouse_id,
                    dest_type="vehicle",
                    dest_id=effective_vehicle_id,
                    items=items,
                    transfer_order_id=order_id,
                    user_id=user_id,
                    allocation_type="warehouse_to_vehicle",
                    manifest_id=manifest_id,
                    manifest_no=manifest_no,
                )
            except Exception as ex:
                if created_manifest_id:
                    delivery_manifests_collection.delete_one({"_id": created_manifest_id})
                    orders_collection.update_one(
                        {"_id": order_id},
                        {"$unset": {"manifest_id": "", "manifest_no": ""}}
                    )
                raise ex

    # C. Reversal -> Transfer back from vehicle to warehouse (only when order is reverted to warehouse queue)
    # NOTE: When an order is Cancelled at the doorstep while Out for Delivery, goods physically remain on the vehicle.
    # The cancelled order unblocks the stock on the vehicle, making it immediately available for the driver to re-sell or unload at end-of-day.
    is_reverting_from_out = (
        current_status == "Out for Delivery"
        and new_status in ["Pending", "Ready to Pick Up", "Ready to Pick-up"]
        and orig_vehicle_id
        and warehouse_id
    )

    if is_reverting_from_out:
        transfer_stock_batches(
            source_type="vehicle",
            source_id=orig_vehicle_id,
            dest_type="warehouse",
            dest_id=warehouse_id,
            items=items,
            transfer_order_id=order_id,
            user_id=user_id,
            allocation_type="vehicle_to_warehouse",
        )
        orders_collection.update_one({"_id": order_id}, {"$set": {"vehicle_id": None}})
