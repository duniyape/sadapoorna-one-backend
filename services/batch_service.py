from datetime import datetime, timezone
from typing import Optional, List, Dict, Tuple, Any
from bson import ObjectId
from fastapi import HTTPException
from pymongo import ReturnDocument

from database import (
    stock_batches_collection,
    stock_batch_allocations_collection,
    sale_batch_consumptions_collection,
    counters_collection,
    orders_collection,
)

# =========================================================
# TIME HELPER
# =========================================================

def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# =========================================================
# BATCH NUMBER GENERATOR
# =========================================================

def generate_batch_no() -> str:
    """Generate a unique sequential batch number: BAT-YYYY-MM-00001."""
    now = utc_now()
    year = now.strftime("%Y")
    month = now.strftime("%m")
    counter_id = f"batch_no:{year}:{month}"

    counter = counters_collection.find_one_and_update(
        {"_id": counter_id},
        {
            "$inc": {"seq": 1},
            "$set": {
                "prefix": "BAT",
                "year": int(year),
                "month": int(month),
                "updated_at": now,
            },
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return f"BAT-{year}-{month}-{counter['seq']:05d}"


def generate_manifest_no() -> str:
    """Generate a unique sequential trip manifest number: MNF-YYYY-MM-00001."""
    now = utc_now()
    year = now.strftime("%Y")
    month = now.strftime("%m")
    counter_id = f"manifest_no:{year}:{month}"

    counter = counters_collection.find_one_and_update(
        {"_id": counter_id},
        {
            "$inc": {"seq": 1},
            "$set": {
                "prefix": "MNF",
                "year": int(year),
                "month": int(month),
                "updated_at": now,
            },
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return f"MNF-{year}-{month}-{counter['seq']:05d}"


# =========================================================
# 1. PURCHASE INWARD: CREATE STOCK BATCHES
# =========================================================

def create_stock_batches_from_purchase(
    purchase_order: Dict[str, Any],
    user_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Creates stock batches in `stock_batches` when a purchase order is completed.
    If a warehouse_id is provided, it assigns to that warehouse; otherwise
    it creates virtual unallocated stock (location_type='unallocated').
    Idempotent: skips already created batches for this purchase order.
    """
    purchase_order_id = purchase_order["_id"]
    warehouse_id = purchase_order.get("warehouse_id")
    location_type = "warehouse" if warehouse_id else "unallocated"

    # Check if batches were already generated for this purchase
    existing_batches = list(
        stock_batches_collection.find({"purchase_order_id": purchase_order_id})
    )
    if existing_batches:
        return existing_batches

    created_batches = []
    now = utc_now()

    for item in purchase_order.get("items", []):
        qty = float(item.get("quantity", 0))
        rate = float(item.get("rate", 0))
        product_id = item.get("product_id")
        variant_id = item.get("variant_id")
        item_id = item.get("item_id")

        if qty <= 0:
            continue

        batch_no = generate_batch_no()
        batch_doc = {
            "batch_no": batch_no,
            "product_id": product_id,
            "variant_id": variant_id,
            "location_type": location_type,
            "warehouse_id": warehouse_id if location_type == "warehouse" else None,
            "vehicle_id": None,
            "purchase_order_id": purchase_order_id,
            "purchase_item_id": item_id,
            "vendor_id": purchase_order.get("vendor_id"),
            "initial_quantity": qty,
            "available_quantity": qty,
            "reserved_quantity": 0.0,
            "purchase_rate": rate,
            "status": "active",
            "created_at": now,
            "updated_at": now,
            "created_by": ObjectId(user_id) if user_id and ObjectId.is_valid(str(user_id)) else None,
        }

        result = stock_batches_collection.insert_one(batch_doc)
        batch_doc["_id"] = result.inserted_id
        created_batches.append(batch_doc)

    return created_batches


# =========================================================
# 2. FIFO ALLOCATION ENGINE FOR SALES
# =========================================================

def allocate_fifo_from_batches(
    product_id: ObjectId,
    variant_id: ObjectId,
    warehouse_id: Optional[ObjectId] = None,
    vehicle_id: Optional[ObjectId] = None,
    source_type: Optional[str] = None,
    required_quantity: float = 0,
) -> Tuple[List[Dict[str, Any]], float]:
    """
    Atomically allocates stock from available active batches using FIFO (oldest first).
    Supports source_type='unallocated' (virtual stock), 'warehouse', or 'vehicle'.
    Returns (batch_consumptions_list, total_cogs).
    """
    required_quantity = float(required_quantity)
    if required_quantity <= 0:
        return [], 0.0

    # Build query for the location
    query: Dict[str, Any] = {
        "product_id": product_id,
        "variant_id": variant_id,
        "status": "active",
        "available_quantity": {"$gt": 0},
    }

    if source_type == "unallocated" or (not warehouse_id and not vehicle_id and source_type == "unallocated"):
        query["location_type"] = "unallocated"
    elif vehicle_id or source_type == "vehicle":
        query["location_type"] = "vehicle"
        query["vehicle_id"] = vehicle_id
    elif warehouse_id or source_type == "warehouse":
        query["location_type"] = "warehouse"
        query["warehouse_id"] = warehouse_id
    elif not warehouse_id and not vehicle_id:
        # If neither is specified, check unallocated stock by default
        query["location_type"] = "unallocated"
    else:
        raise HTTPException(
            status_code=400,
            detail="A warehouse_id, vehicle_id, or source_type='unallocated' must be specified for FIFO stock allocation."
        )

    # Query active batches sorted chronologically (FIFO)
    batches = list(
        stock_batches_collection.find(query).sort([("created_at", 1), ("_id", 1)])
    )

    total_available = sum(float(b.get("available_quantity", 0)) for b in batches)
    if total_available < required_quantity:
        # Check if historical purchases need syncing to batches
        if sync_existing_purchases_to_batches() > 0:
            batches = list(
                stock_batches_collection.find(query).sort([("created_at", 1), ("_id", 1)])
            )
            total_available = sum(float(b.get("available_quantity", 0)) for b in batches)

    if total_available < required_quantity:
        if query.get("location_type") == "unallocated":
            location_desc = "unallocated virtual purchase stock"
        elif vehicle_id:
            location_desc = f"vehicle {vehicle_id}"
        else:
            location_desc = f"warehouse {warehouse_id}"
        raise HTTPException(
            status_code=400,
            detail=(
                f"Insufficient stock for product variant {variant_id} at {location_desc}. "
                f"Required: {required_quantity}, Available: {total_available}"
            )
        )

    remaining_required = required_quantity
    consumptions = []
    total_cogs = 0.0
    now = utc_now()

    for batch in batches:
        if remaining_required <= 0:
            break

        batch_id = batch["_id"]
        avail = float(batch.get("available_quantity", 0))
        if avail <= 0:
            continue

        consume_qty = min(avail, remaining_required)
        purchase_rate = float(batch.get("purchase_rate", 0))
        cost = round(consume_qty * purchase_rate, 2)

        # Atomic deduction from stock_batch
        new_avail = round(avail - consume_qty, 6)
        new_status = "exhausted" if new_avail <= 0 else "active"

        update_result = stock_batches_collection.update_one(
            {
                "_id": batch_id,
                "available_quantity": {"$gte": consume_qty}
            },
            {
                "$inc": {"available_quantity": -consume_qty},
                "$set": {
                    "status": new_status,
                    "updated_at": now,
                }
            }
        )

        if update_result.modified_count == 0:
            # Concurrency conflict detected: re-run allocation recursively
            return allocate_fifo_from_batches(
                product_id=product_id,
                variant_id=variant_id,
                warehouse_id=warehouse_id,
                vehicle_id=vehicle_id,
                source_type=source_type,
                required_quantity=required_quantity,
            )

        consumptions.append({
            "stock_batch_id": batch_id,
            "batch_no": batch.get("batch_no"),
            "purchase_order_id": batch.get("purchase_order_id"),
            "purchase_item_id": batch.get("purchase_item_id"),
            "quantity": consume_qty,
            "purchase_rate": purchase_rate,
            "cost": cost,
        })

        total_cogs += cost
        remaining_required = round(remaining_required - consume_qty, 6)

    return consumptions, round(total_cogs, 2)


# =========================================================
# 3. RECORD SALES CONSUMPTION LOG
# =========================================================

def record_sale_consumptions(
    sale_order_id: ObjectId,
    sale_item_id: ObjectId,
    product_id: ObjectId,
    variant_id: ObjectId,
    warehouse_id: Optional[ObjectId],
    vehicle_id: Optional[ObjectId],
    consumptions: List[Dict[str, Any]],
    sale_rate: float,
) -> None:
    """
    Inserts immutable traceability logs into `sale_batch_consumptions`.
    """
    if not consumptions:
        return

    now = utc_now()
    docs = []
    for c in consumptions:
        qty = float(c.get("quantity", 0))
        purchase_rate = float(c.get("purchase_rate", 0))
        cogs = float(c.get("cost", round(qty * purchase_rate, 2)))
        revenue = round(qty * sale_rate, 2)
        profit = round(revenue - cogs, 2)

        docs.append({
            "sale_order_id": sale_order_id,
            "sale_item_id": sale_item_id,
            "stock_batch_id": c.get("stock_batch_id"),
            "batch_no": c.get("batch_no"),
            "purchase_order_id": c.get("purchase_order_id"),
            "purchase_item_id": c.get("purchase_item_id"),
            "product_id": product_id,
            "variant_id": variant_id,
            "warehouse_id": warehouse_id,
            "vehicle_id": vehicle_id,
            "quantity": qty,
            "purchase_rate": purchase_rate,
            "sale_rate": sale_rate,
            "cogs": cogs,
            "gross_profit": profit,
            "consumed_at": now,
        })

    if docs:
        sale_batch_consumptions_collection.insert_many(docs)


# =========================================================
# 4. SALES RETURN: RESTORE EXACT CONSUMED BATCHES
# =========================================================

def reverse_sale_consumptions(
    original_sale_order_id: ObjectId,
    ref_item_id: ObjectId,
    return_quantity: float,
    return_warehouse_id: Optional[ObjectId] = None,
    return_vehicle_id: Optional[ObjectId] = None,
    user_id: Optional[str] = None,
    return_order_id: Optional[ObjectId] = None,
) -> Tuple[List[Dict[str, Any]], float]:
    """
    Reverses batch consumptions for a sales return, restoring available quantities
    back into the designated warehouse/vehicle stock batches.
    Logs an immutable audit entry in stock_batch_allocations and returns
    (restored_batches, total_cogs_reversed).
    """
    return_quantity = float(return_quantity)
    if return_quantity <= 0:
        return [], 0.0

    consumptions = list(
        sale_batch_consumptions_collection.find({
            "sale_order_id": original_sale_order_id,
            "sale_item_id": ref_item_id,
        }).sort([("consumed_at", -1)])
    )

    if not consumptions:
        raise HTTPException(
            status_code=400,
            detail=f"No batch consumption records found for original sale item {ref_item_id}."
        )

    # Determine customer_id from return order or original order
    customer_id = None
    if return_order_id:
        ret_order = orders_collection.find_one({"_id": return_order_id})
        if ret_order:
            customer_id = ret_order.get("customer_id")
    if not customer_id and original_sale_order_id:
        orig_order = orders_collection.find_one({"_id": original_sale_order_id})
        if orig_order:
            customer_id = orig_order.get("customer_id")

    # Determine default destination if specified in return parameters
    target_dest_type = None
    target_dest_id = None
    if return_warehouse_id:
        target_dest_type = "warehouse"
        target_dest_id = return_warehouse_id
    elif return_vehicle_id:
        target_dest_type = "vehicle"
        target_dest_id = return_vehicle_id

    remaining_return = return_quantity
    restored_batches = []
    total_reversed_cogs = 0.0
    now = utc_now()

    for cons in consumptions:
        if remaining_return <= 0:
            break

        cons_qty = float(cons.get("quantity", 0))
        already_returned = float(cons.get("returned_quantity", 0))
        available_to_return = max(0.0, cons_qty - already_returned)
        if available_to_return <= 0:
            continue

        restore_qty = min(available_to_return, remaining_return)
        batch_id = cons.get("stock_batch_id")
        orig_batch = stock_batches_collection.find_one({"_id": batch_id}) if batch_id else None
        purchase_rate = float(cons.get("purchase_rate", 0))
        cost = round(restore_qty * purchase_rate, 2)

        # Resolve destination location for this batch
        dest_type = target_dest_type
        dest_id = target_dest_id
        if not dest_type:
            if cons.get("warehouse_id"):
                dest_type = "warehouse"
                dest_id = cons.get("warehouse_id")
            elif cons.get("vehicle_id"):
                dest_type = "vehicle"
                dest_id = cons.get("vehicle_id")
            elif orig_batch and orig_batch.get("warehouse_id"):
                dest_type = "warehouse"
                dest_id = orig_batch.get("warehouse_id")
            elif orig_batch and orig_batch.get("vehicle_id"):
                dest_type = "vehicle"
                dest_id = orig_batch.get("vehicle_id")
            else:
                dest_type = "warehouse"
                dest_id = None

        target_batch_id = batch_id

        # If returning to the same location as original batch, increment existing batch
        if orig_batch and orig_batch.get("location_type") == dest_type and (
            (dest_type == "warehouse" and orig_batch.get("warehouse_id") == dest_id) or
            (dest_type == "vehicle" and orig_batch.get("vehicle_id") == dest_id)
        ):
            stock_batches_collection.update_one(
                {"_id": batch_id},
                {
                    "$inc": {"available_quantity": restore_qty},
                    "$set": {
                        "status": "active",
                        "updated_at": now,
                    }
                }
            )
        else:
            # Stock is returned to a different warehouse or vehicle
            dest_filter: Dict[str, Any] = {
                "batch_no": cons.get("batch_no"),
                "product_id": cons.get("product_id"),
                "variant_id": cons.get("variant_id"),
                "location_type": dest_type,
                "status": "active",
            }
            if dest_type == "warehouse" and dest_id:
                dest_filter["warehouse_id"] = dest_id
            elif dest_type == "vehicle" and dest_id:
                dest_filter["vehicle_id"] = dest_id

            existing_dest_batch = stock_batches_collection.find_one(dest_filter)
            if existing_dest_batch:
                stock_batches_collection.update_one(
                    {"_id": existing_dest_batch["_id"]},
                    {
                        "$inc": {"available_quantity": restore_qty},
                        "$set": {
                            "status": "active",
                            "updated_at": now,
                        }
                    }
                )
                target_batch_id = existing_dest_batch["_id"]
            else:
                # Create a new active batch at the return destination
                new_batch_doc = {
                    "batch_no": cons.get("batch_no"),
                    "product_id": cons.get("product_id"),
                    "variant_id": cons.get("variant_id"),
                    "location_type": dest_type,
                    "warehouse_id": dest_id if dest_type == "warehouse" else None,
                    "vehicle_id": dest_id if dest_type == "vehicle" else None,
                    "purchase_order_id": orig_batch.get("purchase_order_id") if orig_batch else cons.get("purchase_order_id"),
                    "purchase_item_id": orig_batch.get("purchase_item_id") if orig_batch else cons.get("purchase_item_id"),
                    "parent_batch_id": batch_id,
                    "vendor_id": orig_batch.get("vendor_id") if orig_batch else None,
                    "initial_quantity": restore_qty,
                    "available_quantity": restore_qty,
                    "reserved_quantity": 0.0,
                    "purchase_rate": purchase_rate,
                    "tax_rate": orig_batch.get("tax_rate", 0.0) if orig_batch else 0.0,
                    "expiry_date": orig_batch.get("expiry_date") if orig_batch else None,
                    "mfg_date": orig_batch.get("mfg_date") if orig_batch else None,
                    "status": "active",
                    "created_at": now,
                    "updated_at": now,
                    "created_by": ObjectId(user_id) if user_id and ObjectId.is_valid(str(user_id)) else None,
                }
                ins_res = stock_batches_collection.insert_one(new_batch_doc)
                target_batch_id = ins_res.inserted_id

        # Record immutable audit entry in stock_batch_allocations
        allocation_doc = {
            "allocation_type": "sale_return",
            "transfer_order_id": return_order_id or original_sale_order_id,
            "source_batch_id": batch_id,
            "destination_batch_id": target_batch_id,
            "batch_no": cons.get("batch_no"),
            "product_id": cons.get("product_id"),
            "variant_id": cons.get("variant_id"),
            "from_location": {"type": "customer", "id": customer_id} if customer_id else {"type": "customer"},
            "to_location": {"type": dest_type, "id": dest_id} if dest_id else {"type": dest_type or "warehouse"},
            "quantity": restore_qty,
            "unit_cost": purchase_rate,
            "status": "completed",
            "allocated_by": ObjectId(user_id) if user_id and ObjectId.is_valid(str(user_id)) else None,
            "created_at": now,
        }
        stock_batch_allocations_collection.insert_one(allocation_doc)

        # Track returned quantity on the sale consumption document to guard against over-returns
        sale_batch_consumptions_collection.update_one(
            {"_id": cons["_id"]},
            {"$inc": {"returned_quantity": restore_qty}}
        )

        restored_batches.append({
            "stock_batch_id": target_batch_id,
            "original_batch_id": batch_id,
            "batch_no": cons.get("batch_no"),
            "purchase_order_id": cons.get("purchase_order_id"),
            "purchase_item_id": cons.get("purchase_item_id"),
            "quantity": restore_qty,
            "purchase_rate": purchase_rate,
            "cost": cost,
        })

        total_reversed_cogs += cost
        remaining_return = round(remaining_return - restore_qty, 6)

    if remaining_return > 0:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Return quantity exceeds available consumed quantity "
                f"from original sale (remaining unreturned: {return_quantity - remaining_return} / requested: {return_quantity})."
            )
        )

    return restored_batches, round(total_reversed_cogs, 2)


# =========================================================
# 5. PURCHASE RETURN: DEDUCT FROM PURCHASE BATCH
# =========================================================

def handle_purchase_return_batches(
    purchase_order_id: ObjectId,
    return_items: List[Dict[str, Any]],
    warehouse_id: Optional[ObjectId] = None,
    user_id: Optional[str] = None,
) -> None:
    """
    Deducts returned stock from active purchase batches when returning to vendor.
    """
    now = utc_now()
    for item in return_items:
        product_id = item.get("product_id")
        variant_id = item.get("variant_id")
        qty = float(item.get("quantity", 0))

        if qty <= 0:
            continue

        query = {
            "purchase_order_id": purchase_order_id,
            "product_id": product_id,
            "variant_id": variant_id,
            "status": "active",
        }
        if warehouse_id:
            query["warehouse_id"] = warehouse_id

        batch = stock_batches_collection.find_one(query)
        if not batch:
            raise HTTPException(
                status_code=400,
                detail=f"Matching stock batch not found for purchase return (variant: {variant_id})."
            )

        avail = float(batch.get("available_quantity", 0))
        if avail < qty:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot return {qty} units; only {avail} units remaining in batch "
                    f"{batch.get('batch_no')} (some may have been sold or transferred)."
                )
            )

        new_avail = round(avail - qty, 6)
        stock_batches_collection.update_one(
            {"_id": batch["_id"]},
            {
                "$inc": {"available_quantity": -qty},
                "$set": {
                    "status": "exhausted" if new_avail <= 0 else "active",
                    "updated_at": now,
                }
            }
        )


# =========================================================
# 6. STOCK TRANSFERS: UNALLOCATED -> WAREHOUSE -> VEHICLE
# =========================================================

def transfer_stock_batches(
    source_type: str,  # "unallocated" | "warehouse" | "vehicle"
    source_id: Optional[ObjectId],
    dest_type: str,    # "warehouse" | "vehicle"
    dest_id: ObjectId,
    items: List[Dict[str, Any]],
    transfer_order_id: Optional[ObjectId] = None,
    user_id: Optional[str] = None,
    allocation_type: Optional[str] = None,
    manifest_id: Optional[ObjectId] = None,
    manifest_no: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Executes an atomic transfer of stock batches from a source location to a destination location.
    Supports:
    - Unallocated purchase stock -> Warehouse (purchase_to_warehouse / Warehouse_IN)
    - Warehouse -> Vehicle (warehouse_to_vehicle)
    - Vehicle -> Warehouse (vehicle_to_warehouse)
    - Warehouse -> Warehouse (warehouse_to_warehouse)
    Preserves batch number, purchase cost, and lineage across locations.
    Records audit entries in `stock_batch_allocations`.
    """
    allocations = []
    now = utc_now()

    if isinstance(source_id, str) and ObjectId.is_valid(source_id):
        source_id = ObjectId(source_id)
    if isinstance(dest_id, str) and ObjectId.is_valid(dest_id):
        dest_id = ObjectId(dest_id)
    if isinstance(manifest_id, str) and ObjectId.is_valid(manifest_id):
        manifest_id = ObjectId(manifest_id)
    if isinstance(transfer_order_id, str) and ObjectId.is_valid(transfer_order_id):
        transfer_order_id = ObjectId(transfer_order_id)

    # Determine allocation type if not explicitly supplied
    if not allocation_type:
        if source_type == "unallocated":
            allocation_type = "purchase_to_warehouse"
        elif source_type == "warehouse" and dest_type == "vehicle":
            allocation_type = "warehouse_to_vehicle"
        elif source_type == "vehicle" and dest_type == "warehouse":
            allocation_type = "vehicle_to_warehouse"
        elif source_type == "warehouse" and dest_type == "warehouse":
            allocation_type = "warehouse_to_warehouse"
        else:
            allocation_type = "transfer"

    for item in items:
        product_id = item.get("product_id")
        variant_id = item.get("variant_id")
        if isinstance(product_id, str) and ObjectId.is_valid(product_id):
            product_id = ObjectId(product_id)
        if isinstance(variant_id, str) and ObjectId.is_valid(variant_id):
            variant_id = ObjectId(variant_id)
        req_qty = float(item.get("quantity", 0))

        if req_qty <= 0:
            continue

        # 1. Allocate FIFO from source
        src_warehouse_id = source_id if source_type == "warehouse" else None
        src_vehicle_id = source_id if source_type == "vehicle" else None

        consumed_batches, _ = allocate_fifo_from_batches(
            product_id=product_id,
            variant_id=variant_id,
            warehouse_id=src_warehouse_id,
            vehicle_id=src_vehicle_id,
            source_type=source_type,
            required_quantity=req_qty,
        )

        # 2. Replicate batches at destination with preserved purchase rate and lineage
        for cb in consumed_batches:
            c_qty = float(cb["quantity"])
            p_rate = float(cb["purchase_rate"])
            src_batch_id = cb["stock_batch_id"]
            batch_no = cb["batch_no"]
            p_order_id = cb["purchase_order_id"]
            p_item_id = cb["purchase_item_id"]

            dest_filter: Dict[str, Any] = {
                "batch_no": batch_no,
                "product_id": product_id,
                "variant_id": variant_id,
                "location_type": dest_type,
                "status": "active",
            }
            if dest_type == "warehouse" and dest_id:
                dest_filter["warehouse_id"] = dest_id
            elif dest_type == "vehicle" and dest_id:
                dest_filter["vehicle_id"] = dest_id

            existing_dest_batch = stock_batches_collection.find_one(dest_filter)
            if existing_dest_batch:
                stock_batches_collection.update_one(
                    {"_id": existing_dest_batch["_id"]},
                    {
                        "$inc": {
                            "initial_quantity": c_qty,
                            "available_quantity": c_qty,
                        },
                        "$set": {
                            "status": "active",
                            "updated_at": now,
                        }
                    }
                )
                dest_batch_id = existing_dest_batch["_id"]
            else:
                dest_batch_doc = {
                    "batch_no": batch_no,
                    "product_id": product_id,
                    "variant_id": variant_id,
                    "location_type": dest_type,
                    "warehouse_id": dest_id if dest_type == "warehouse" else None,
                    "vehicle_id": dest_id if dest_type == "vehicle" else None,
                    "purchase_order_id": p_order_id,
                    "purchase_item_id": p_item_id,
                    "parent_batch_id": src_batch_id,
                    "initial_quantity": c_qty,
                    "available_quantity": c_qty,
                    "reserved_quantity": 0.0,
                    "purchase_rate": p_rate,
                    "status": "active",
                    "created_at": now,
                    "updated_at": now,
                    "created_by": ObjectId(user_id) if user_id and ObjectId.is_valid(str(user_id)) else None,
                }
                dest_insert_result = stock_batches_collection.insert_one(dest_batch_doc)
                dest_batch_id = dest_insert_result.inserted_id

            # 3. Log transfer in stock_batch_allocations
            allocation_doc = {
                "allocation_type": allocation_type,
                "transfer_order_id": transfer_order_id,
                "manifest_id": manifest_id,
                "manifest_no": manifest_no,
                "source_batch_id": src_batch_id,
                "destination_batch_id": dest_batch_id,
                "batch_no": batch_no,
                "product_id": product_id,
                "variant_id": variant_id,
                "from_location": {"type": source_type, "id": source_id} if source_id else {"type": source_type},
                "to_location": {"type": dest_type, "id": dest_id},
                "quantity": c_qty,
                "unit_cost": p_rate,
                "status": "completed",
                "allocated_by": ObjectId(user_id) if user_id and ObjectId.is_valid(str(user_id)) else None,
                "created_at": now,
            }

            stock_batch_allocations_collection.insert_one(allocation_doc)
            allocations.append(allocation_doc)

    return allocations


# =========================================================
# 7. REAL-TIME BATCH INVENTORY QUERY
# =========================================================

def get_batch_inventory(
    warehouse_id: Optional[ObjectId] = None,
    vehicle_id: Optional[ObjectId] = None,
    product_id: Optional[ObjectId] = None,
    variant_id: Optional[ObjectId] = None,
    location_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Returns aggregated real-time stock levels derived directly from active `stock_batches`.
    Supports querying 'unallocated' virtual stock, 'warehouse', or 'vehicle'.
    """
    match: Dict[str, Any] = {
        "status": "active",
        "available_quantity": {"$gt": 0}
    }

    if location_type:
        match["location_type"] = location_type
        if warehouse_id:
            match["warehouse_id"] = warehouse_id
        if vehicle_id:
            match["vehicle_id"] = vehicle_id
    elif warehouse_id:
        match["location_type"] = "warehouse"
        match["warehouse_id"] = warehouse_id
    elif vehicle_id:
        match["location_type"] = "vehicle"
        match["vehicle_id"] = vehicle_id

    if product_id:
        match["product_id"] = product_id
    if variant_id:
        match["variant_id"] = variant_id

    pipeline = [
        {"$match": match},
        {
            "$group": {
                "_id": {
                    "product_id": "$product_id",
                    "variant_id": "$variant_id",
                    "location_type": "$location_type",
                    "warehouse_id": "$warehouse_id",
                    "vehicle_id": "$vehicle_id",
                },
                "total_available": {"$sum": "$available_quantity"},
                "batch_count": {"$sum": 1},
                "avg_purchase_rate": {"$avg": "$purchase_rate"},
            }
        }
    ]

    return list(stock_batches_collection.aggregate(pipeline))
    

# =========================================================
# 8. HISTORICAL PURCHASE BATCH SYNCHRONIZATION
# =========================================================

def sync_existing_purchases_to_batches() -> int:
    """
    Scans orders_collection for all completed purchase orders that do not
    yet have corresponding stock_batches documents and generates them.
    Returns the count of newly synced purchase orders.
    """
    completed_purchases = list(orders_collection.find({
        "type": "purchase",
        "status": "Completed",
        "record_status": "active",
    }))

    synced_count = 0
    for po in completed_purchases:
        po_id = po["_id"]
        has_batches = stock_batches_collection.find_one({"purchase_order_id": po_id})
        if not has_batches:
            created = create_stock_batches_from_purchase(po, user_id=str(po.get("created_by") or ""))
            if created:
                synced_count += 1
    return synced_count


# =========================================================
# 9. SELLABLE & UNBLOCKED STOCK ENGINE
# =========================================================

BLOCKING_SALE_STATUSES = [
    "Pending",
    "Ready to Pick-up",
    "Ready to Pick Up",
    "Out for Delivery",
]


def get_blocked_sale_orders_query(
    warehouse_id: Optional[ObjectId] = None,
    vehicle_id: Optional[ObjectId] = None,
    location_type: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Returns the MongoDB filter matching all active sale orders that block sellable inventory:
    1. Orders in pipeline statuses: Pending, Ready to Pick-up, Out for Delivery
    2. Orders marked Delivered that have NOT been billed yet (invoice_no is None or "")

    Location-awareness:
    - For warehouse inventory: only block orders that are still at the warehouse (vehicle_id is None).
      Orders dispatched or loaded on a vehicle (vehicle_id set) belong to the vehicle, not the warehouse!
    - For vehicle inventory: only block orders assigned to vehicles (vehicle_id is not None).
    """
    match_query: Dict[str, Any] = {
        "type": "sale",
        "record_status": "active",
        "$or": [
            {
                "status": {"$in": BLOCKING_SALE_STATUSES}
            },
            {
                "status": "Delivered",
                "invoice_no": {"$in": [None, ""]}
            }
        ]
    }

    loc_type = location_type or ("warehouse" if warehouse_id else "vehicle" if vehicle_id else None)

    if loc_type == "warehouse":
        if warehouse_id:
            match_query["warehouse_id"] = warehouse_id
        # Warehouse blocked stock must ONLY include orders still physically at the warehouse (NOT on a vehicle)
        match_query["vehicle_id"] = None
    elif loc_type == "vehicle":
        if vehicle_id:
            match_query["vehicle_id"] = vehicle_id
        else:
            # Vehicle blocked stock must only include orders assigned to a vehicle
            match_query["vehicle_id"] = {"$ne": None}

    return match_query


def get_bulk_blocked_quantities(
    warehouse_id: Optional[ObjectId] = None,
    vehicle_id: Optional[ObjectId] = None,
    location_type: Optional[str] = None,
    group_by_location: bool = False,
) -> Dict[Tuple[Any, ...], float]:
    """
    Computes committed/blocked quantities across all active open orders
    for a given warehouse or vehicle in a single fast indexed aggregation.
    - If group_by_location is True:
      returns (location_id, product_id, variant_id) -> blocked_quantity
    - If group_by_location is False:
      returns (product_id, variant_id) -> blocked_quantity
    """
    loc_type = location_type or ("warehouse" if warehouse_id else "vehicle" if vehicle_id else None)

    match_query = get_blocked_sale_orders_query(
        warehouse_id=warehouse_id,
        vehicle_id=vehicle_id,
        location_type=loc_type,
    )

    group_id: Dict[str, Any] = {
        "product_id": "$items.product_id",
        "variant_id": "$items.variant_id",
    }
    if group_by_location:
        if loc_type == "vehicle":
            group_id["vehicle_id"] = "$vehicle_id"
        else:
            group_id["warehouse_id"] = "$warehouse_id"

    pipeline = [
        {"$match": match_query},
        {"$unwind": "$items"},
        {
            "$group": {
                "_id": group_id,
                "blocked_quantity": {"$sum": "$items.quantity"},
            }
        },
    ]

    blocked_map: Dict[Tuple[Any, ...], float] = {}
    for row in orders_collection.aggregate(pipeline):
        pid = row["_id"].get("product_id")
        vid = row["_id"].get("variant_id")
        qty = float(row.get("blocked_quantity", 0))

        if not (pid and vid):
            continue

        if group_by_location:
            if loc_type == "vehicle":
                loc_id = row["_id"].get("vehicle_id") or vehicle_id
            else:
                loc_id = row["_id"].get("warehouse_id") or warehouse_id
            if loc_id:
                blocked_map[(loc_id, pid, vid)] = qty
        else:
            blocked_map[(pid, vid)] = blocked_map.get((pid, vid), 0.0) + qty

    return blocked_map


def get_sellable_stock_breakdown(
    product_id: ObjectId,
    variant_id: ObjectId,
    warehouse_id: Optional[ObjectId] = None,
    vehicle_id: Optional[ObjectId] = None,
) -> Dict[str, float]:
    """
    Calculates the exact real-time stock breakdown for a specific product variant:
    - physical_stock: On-shelf stock from active stock_batches
    - blocked_stock: Committed in Pending / Ready to Pick-up / Out for Delivery / unbilled Delivered sale orders
    - unblocked_stock: max(0, physical_stock - blocked_stock)
    """
    # 1. Physical stock from active batches
    batch_match: Dict[str, Any] = {
        "product_id": product_id,
        "variant_id": variant_id,
        "status": "active",
        "available_quantity": {"$gt": 0},
    }
    if warehouse_id:
        batch_match["location_type"] = "warehouse"
        batch_match["warehouse_id"] = warehouse_id
    elif vehicle_id:
        batch_match["location_type"] = "vehicle"
        batch_match["vehicle_id"] = vehicle_id

    phys_pipeline = [
        {"$match": batch_match},
        {"$group": {"_id": None, "total": {"$sum": "$available_quantity"}}},
    ]
    phys_res = list(stock_batches_collection.aggregate(phys_pipeline))
    physical_stock = float(phys_res[0]["total"]) if phys_res else 0.0

    # 2. Blocked stock in pipeline and unbilled Delivered sales
    order_match = get_blocked_sale_orders_query(
        warehouse_id=warehouse_id,
        vehicle_id=vehicle_id
    )

    blocked_pipeline = [
        {"$match": order_match},
        {"$unwind": "$items"},
        {
            "$match": {
                "items.product_id": product_id,
                "items.variant_id": variant_id,
            }
        },
        {"$group": {"_id": None, "total": {"$sum": "$items.quantity"}}},
    ]
    block_res = list(orders_collection.aggregate(blocked_pipeline))
    blocked_stock = float(block_res[0]["total"]) if block_res else 0.0

    unblocked_stock = max(0.0, round(physical_stock - blocked_stock, 6))

    return {
        "physical_stock": round(physical_stock, 6),
        "blocked_stock": round(blocked_stock, 6),
        "unblocked_stock": unblocked_stock,
    }


