import re

# Read routes/Inventory.py
with open("routes/Inventory.py", "r", encoding="utf-8") as f:
    content = f.read()

# 1. Replace get_warehouse_inventory
# Find from WAREHOUSE_INVENTORY_TYPES = [ up to MAIN_INVENTORY_TYPES = [
pattern_wh = re.compile(r'WAREHOUSE_INVENTORY_TYPES = \[.*?MAIN_INVENTORY_TYPES = \[', re.DOTALL)

warehouse_replacement = '''WAREHOUSE_INVENTORY_TYPES = [
    "Warehouse_IN",
    "purchase_to_warehouse",
    "Warehouse_OUT",
    "warehouse_to_vehicle",
    "vehicle_to_warehouse",
]


# =========================================================
# GET WAREHOUSE INVENTORY (DIRECT FROM STOCK BATCHES)
# =========================================================

@router.get("/warehouse-inventory", tags=["Inventory"])
def get_warehouse_inventory(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    warehouse_id: Optional[str] = None,
    product_id: Optional[str] = None,
    variant_id: Optional[str] = None,
    search: Optional[str] = None,
):
    skip = (page - 1) * limit

    match_filter: dict = {
        "location_type": "warehouse",
        "status": "active",
        "available_quantity": {"$gt": 0},
    }
    if warehouse_id:
        match_filter["warehouse_id"] = validate_object_id(warehouse_id, "warehouse_id")
    if product_id:
        match_filter["product_id"] = validate_object_id(product_id, "product_id")
    if variant_id:
        match_filter["variant_id"] = validate_object_id(variant_id, "variant_id")

    pipeline = [
        {"$match": match_filter},
        {
            "$group": {
                "_id": {
                    "warehouse_id": "$warehouse_id",
                    "product_id": "$product_id",
                    "variant_id": "$variant_id",
                },
                "warehouse_in_quantity": {"$sum": "$initial_quantity"},
                "available_quantity": {"$sum": "$available_quantity"},
                "total_value": {
                    "$sum": {"$multiply": ["$available_quantity", "$purchase_rate"]}
                },
                "batch_count": {"$sum": 1},
            }
        },
    ]

    aggregated = list(stock_batches_collection.aggregate(pipeline))
    if not aggregated:
        return {
            "success": True,
            "data": [],
            "pagination": {
                "page": page,
                "limit": limit,
                "total": 0,
                "total_pages": 0,
            },
        }

    meta = hydrate_inventory_metadata(aggregated)
    products_map = meta["products"]
    variants_map = meta["variants"]
    units_map = meta["units"]
    pkg_map = meta["packages"]
    warehouses_map = meta["warehouses"]

    data = []
    for doc in aggregated:
        wid = doc["_id"].get("warehouse_id")
        pid = doc["_id"].get("product_id")
        vid = doc["_id"].get("variant_id")

        warehouse = warehouses_map.get(wid, {})
        prod = products_map.get(pid, {})
        var = variants_map.get(vid, {})

        unit_str = units_map.get(var.get("unit_id"), "")
        pkg_str = pkg_map.get(var.get("packaging_type_id"), "")

        in_qty = doc.get("warehouse_in_quantity", 0)
        avail_qty = doc.get("available_quantity", 0)
        out_qty = max(0.0, round(in_qty - avail_qty, 6))

        data.append({
            "warehouse_id": str(wid) if wid else None,
            "warehouse_name": warehouse.get("name"),
            "product_id": str(pid) if pid else None,
            "product_name": prod.get("name"),
            "variant_id": str(vid) if vid else None,
            "variant_name": var.get("name"),
            "variant_qty": var.get("quantity"),
            "sku": var.get("sku"),
            "unit": unit_str,
            "package": pkg_str,
            "warehouse_in_quantity": in_qty,
            "warehouse_out_quantity": out_qty,
            "warehouse_to_vehicle_quantity": 0,
            "vehicle_to_warehouse_quantity": 0,
            "available_quantity": avail_qty,
            "total_value": round(doc.get("total_value", 0.0), 2),
            "batch_count": doc.get("batch_count", 0),
        })

    # Search filter
    if search:
        search_lower = search.strip().lower()
        data = [
            item for item in data
            if (
                search_lower in str(item.get("warehouse_name") or "").lower()
                or search_lower in str(item.get("product_name") or "").lower()
                or search_lower in str(item.get("variant_name") or "").lower()
                or search_lower in str(item.get("sku") or "").lower()
            )
        ]

    total = len(data)
    total_pages = (total + limit - 1) // limit
    paginated_data = data[skip : skip + limit]

    return {
        "success": True,
        "data": paginated_data,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "total_pages": total_pages,
        },
    }

# =========================================================
# MAIN INVENTORY TYPES
# =========================================================

MAIN_INVENTORY_TYPES = ['''

assert pattern_wh.search(content) is not None, "Could not match warehouse inventory pattern"
content = pattern_wh.sub(warehouse_replacement, content, count=1)

# 2. Replace get_vehicle_inventory
# Find from VEHICLE_INVENTORY_TYPES = [ up to @router.get(\n    "/batch-stock"
pattern_veh = re.compile(r'VEHICLE_INVENTORY_TYPES = \[.*?@router\.get\(\s*"/batch-stock"', re.DOTALL)

vehicle_replacement = '''VEHICLE_INVENTORY_TYPES = [
    "warehouse_to_vehicle",
    "sale",
    "sale_return"
]


# =========================================================
# GET VEHICLE INVENTORY (DIRECT FROM STOCK BATCHES)
# =========================================================

@router.get("/vehicle-inventory", tags=["Inventory"])
def get_vehicle_inventory(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    vehicle_id: Optional[str] = None,
    product_id: Optional[str] = None,
    variant_id: Optional[str] = None,
    search: Optional[str] = None,
):
    skip = (page - 1) * limit

    match_filter: dict = {
        "location_type": "vehicle",
        "status": "active",
        "available_quantity": {"$gt": 0},
    }
    if vehicle_id:
        match_filter["vehicle_id"] = validate_object_id(vehicle_id, "vehicle_id")
    if product_id:
        match_filter["product_id"] = validate_object_id(product_id, "product_id")
    if variant_id:
        match_filter["variant_id"] = validate_object_id(variant_id, "variant_id")

    pipeline = [
        {"$match": match_filter},
        {
            "$group": {
                "_id": {
                    "vehicle_id": "$vehicle_id",
                    "product_id": "$product_id",
                    "variant_id": "$variant_id",
                },
                "warehouse_to_vehicle_quantity": {"$sum": "$initial_quantity"},
                "available_quantity": {"$sum": "$available_quantity"},
                "total_value": {
                    "$sum": {"$multiply": ["$available_quantity", "$purchase_rate"]}
                },
                "batch_count": {"$sum": 1},
            }
        },
    ]

    aggregated = list(stock_batches_collection.aggregate(pipeline))
    if not aggregated:
        return {
            "success": True,
            "data": [],
            "pagination": {
                "page": page,
                "limit": limit,
                "total": 0,
                "total_pages": 0,
            },
        }

    meta = hydrate_inventory_metadata(aggregated)
    products_map = meta["products"]
    variants_map = meta["variants"]
    units_map = meta["units"]
    pkg_map = meta["packages"]
    vehicles_map = meta["vehicles"]

    data = []
    for doc in aggregated:
        veh_id = doc["_id"].get("vehicle_id")
        pid = doc["_id"].get("product_id")
        vid = doc["_id"].get("variant_id")

        vehicle = vehicles_map.get(veh_id, {})
        prod = products_map.get(pid, {})
        var = variants_map.get(vid, {})

        unit_str = units_map.get(var.get("unit_id"), "")
        pkg_str = pkg_map.get(var.get("packaging_type_id"), "")

        in_qty = doc.get("warehouse_to_vehicle_quantity", 0)
        avail_qty = doc.get("available_quantity", 0)
        sale_qty = max(0.0, round(in_qty - avail_qty, 6))

        vehicle_data = {
            "id": str(veh_id) if veh_id else None,
            "vehicle_number": vehicle.get("vehicle_number"),
            "model": vehicle.get("model"),
            "vehicle_type": vehicle.get("vehicle_type"),
        }

        data.append({
            "vehicle": vehicle_data,
            "product_id": str(pid) if pid else None,
            "product_name": prod.get("name"),
            "variant_id": str(vid) if vid else None,
            "variant_name": var.get("name"),
            "variant_qty": var.get("quantity"),
            "sku": var.get("sku"),
            "unit": unit_str,
            "package": pkg_str,
            "warehouse_to_vehicle_quantity": in_qty,
            "sale_quantity": sale_qty,
            "sale_return_quantity": 0,
            "vehicle_to_warehouse_quantity": 0,
            "available_quantity": avail_qty,
            "total_value": round(doc.get("total_value", 0.0), 2),
            "batch_count": doc.get("batch_count", 0),
        })

    # Search filter
    if search:
        search_lower = search.strip().lower()
        data = [
            item for item in data
            if (
                search_lower in str(item.get("vehicle", {}).get("vehicle_number") or "").lower()
                or search_lower in str(item.get("vehicle", {}).get("model") or "").lower()
                or search_lower in str(item.get("vehicle", {}).get("vehicle_type") or "").lower()
                or search_lower in str(item.get("product_name") or "").lower()
                or search_lower in str(item.get("variant_name") or "").lower()
                or search_lower in str(item.get("sku") or "").lower()
            )
        ]

    # Sort
    data.sort(
        key=lambda x: (
            x.get("vehicle", {}).get("vehicle_number") or "",
            x.get("product_name") or "",
            x.get("variant_name") or "",
        )
    )

    total = len(data)
    total_pages = (total + limit - 1) // limit
    paginated_data = data[skip : skip + limit]

    return {
        "success": True,
        "data": paginated_data,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "total_pages": total_pages,
        },
    }

@router.get(
    "/batch-stock"'''

assert pattern_veh.search(content) is not None, "Could not match vehicle inventory pattern"
content = pattern_veh.sub(vehicle_replacement, content, count=1)

# 3. Add Stock Ledger & Batch Timeline endpoints at the end of routes/Inventory.py
ledger_and_timeline_code = '''

# =========================================================
# STOCK LEDGER (RUNNING BALANCE & AUDIT STATEMENT)
# =========================================================

@router.get("/inventory/ledger", tags=["Inventory"])
def get_stock_ledger(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    product_id: Optional[str] = None,
    variant_id: Optional[str] = None,
    warehouse_id: Optional[str] = None,
    vehicle_id: Optional[str] = None,
    batch_no: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    search: Optional[str] = None,
):
    """
    Returns a unified chronological stock statement / ledger with running balance,
    document reference numbers, source & destination locations, rates, values, and staff names.
    Like Tally / SAP / Zoho Inventory Stock Card.
    """
    skip = (page - 1) * limit
    prod_obj = validate_object_id(product_id, "product_id") if product_id else None
    var_obj = validate_object_id(variant_id, "variant_id") if variant_id else None
    wh_obj = validate_object_id(warehouse_id, "warehouse_id") if warehouse_id else None
    veh_obj = validate_object_id(vehicle_id, "vehicle_id") if vehicle_id else None

    # Date bounds
    dt_from = None
    dt_to = None
    if from_date:
        try:
            dt_from = datetime.fromisoformat(from_date.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            pass
    if to_date:
        try:
            dt_to = datetime.fromisoformat(to_date.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            pass

    ledger_events = []

    # 1. Purchase Inwards (from initial stock_batches)
    batch_query: dict = {"purchase_order_id": {"$ne": None}, "parent_batch_id": None}
    if prod_obj:
        batch_query["product_id"] = prod_obj
    if var_obj:
        batch_query["variant_id"] = var_obj
    if batch_no:
        batch_query["batch_no"] = batch_no.strip()
    if dt_from or dt_to:
        df: dict = {}
        if dt_from:
            df["$gte"] = dt_from
        if dt_to:
            df["$lte"] = dt_to
        batch_query["created_at"] = df

    # If warehouse_id filter is specified, only include purchase batches directly into that warehouse
    if wh_obj:
        batch_query["warehouse_id"] = wh_obj
    elif veh_obj:
        # Initial purchase batches are never directly on vehicles
        batch_query["warehouse_id"] = ObjectId()  # dummy to skip

    for b in stock_batches_collection.find(batch_query):
        qty = float(b.get("initial_quantity", 0))
        rate = float(b.get("purchase_rate", 0))
        created_at = b.get("created_at") or datetime.min
        loc_type = b.get("location_type", "unallocated")

        ledger_events.append({
            "timestamp": created_at,
            "action": "PURCHASE_INWARD",
            "action_label": "Purchase Inward",
            "document_type": "Purchase Order",
            "order_id": b.get("purchase_order_id"),
            "batch_no": b.get("batch_no"),
            "product_id": b.get("product_id"),
            "variant_id": b.get("variant_id"),
            "from_location": {"type": "vendor", "id": b.get("vendor_id")},
            "to_location": {"type": loc_type, "id": b.get("warehouse_id")},
            "inward_quantity": qty,
            "outward_quantity": 0.0,
            "unit_cost": rate,
            "total_value": round(qty * rate, 2),
            "user_id": b.get("created_by"),
        })

    # 2. Stock Allocations (Transfers between unallocated, warehouses, vehicles)
    alloc_query: dict = {}
    if prod_obj:
        alloc_query["product_id"] = prod_obj
    if var_obj:
        alloc_query["variant_id"] = var_obj
    if dt_from or dt_to:
        df = {}
        if dt_from:
            df["$gte"] = dt_from
        if dt_to:
            df["$lte"] = dt_to
        alloc_query["created_at"] = df

    # Location conditions for allocations
    if wh_obj:
        alloc_query["$or"] = [
            {"from_location.id": wh_obj},
            {"to_location.id": wh_obj},
        ]
    elif veh_obj:
        alloc_query["$or"] = [
            {"from_location.id": veh_obj},
            {"to_location.id": veh_obj},
        ]

    for a in stock_batch_allocations_collection.find(alloc_query):
        qty = float(a.get("quantity", 0))
        rate = float(a.get("unit_cost", 0))
        created_at = a.get("created_at") or datetime.min
        from_loc = a.get("from_location", {})
        to_loc = a.get("to_location", {})
        alloc_type = a.get("allocation_type", "transfer")

        is_inward = True
        is_outward = False
        if wh_obj:
            is_inward = (to_loc.get("id") == wh_obj)
            is_outward = (from_loc.get("id") == wh_obj)
        elif veh_obj:
            is_inward = (to_loc.get("id") == veh_obj)
            is_outward = (from_loc.get("id") == veh_obj)
        else:
            is_inward = (alloc_type == "purchase_to_warehouse")
            is_outward = False

        in_q = qty if is_inward else 0.0
        out_q = qty if is_outward else 0.0

        action_label = "Warehouse Inward"
        if alloc_type == "warehouse_to_vehicle":
            action_label = "Vehicle Load Out" if is_outward else "Vehicle Stock Received"
        elif alloc_type == "vehicle_to_warehouse":
            action_label = "Vehicle Return Out" if is_outward else "Warehouse Stock Received"
        elif alloc_type == "purchase_to_warehouse":
            action_label = "Purchase to Warehouse"

        ledger_events.append({
            "timestamp": created_at,
            "action": alloc_type.upper(),
            "action_label": action_label,
            "document_type": "Stock Transfer Order",
            "order_id": a.get("transfer_order_id"),
            "batch_no": None,
            "source_batch_id": a.get("source_batch_id"),
            "product_id": a.get("product_id"),
            "variant_id": a.get("variant_id"),
            "from_location": from_loc,
            "to_location": to_loc,
            "inward_quantity": in_q,
            "outward_quantity": out_q,
            "unit_cost": rate,
            "total_value": round(qty * rate, 2),
            "user_id": a.get("allocated_by"),
        })

    # 3. Sales Consumptions (FIFO deductions on delivered sales)
    cons_query: dict = {}
    if prod_obj:
        cons_query["product_id"] = prod_obj
    if var_obj:
        cons_query["variant_id"] = var_obj
    if wh_obj:
        cons_query["warehouse_id"] = wh_obj
    if veh_obj:
        cons_query["vehicle_id"] = veh_obj
    if batch_no:
        cons_query["batch_no"] = batch_no.strip()
    if dt_from or dt_to:
        df = {}
        if dt_from:
            df["$gte"] = dt_from
        if dt_to:
            df["$lte"] = dt_to
        cons_query["consumed_at"] = df

    for c in sale_batch_consumptions_collection.find(cons_query):
        qty = float(c.get("quantity", 0))
        p_rate = float(c.get("purchase_rate", 0))
        s_rate = float(c.get("sale_rate", 0))
        consumed_at = c.get("consumed_at") or datetime.min

        src_type = "vehicle" if c.get("vehicle_id") else "warehouse"
        src_id = c.get("vehicle_id") or c.get("warehouse_id")

        ledger_events.append({
            "timestamp": consumed_at,
            "action": "CUSTOMER_SALE",
            "action_label": "Customer Sale (Delivered)",
            "document_type": "Sale Invoice",
            "order_id": c.get("sale_order_id"),
            "batch_no": c.get("batch_no"),
            "product_id": c.get("product_id"),
            "variant_id": c.get("variant_id"),
            "from_location": {"type": src_type, "id": src_id},
            "to_location": {"type": "customer"},
            "inward_quantity": 0.0,
            "outward_quantity": qty,
            "unit_cost": p_rate,
            "sale_rate": s_rate,
            "total_value": round(qty * p_rate, 2),
            "user_id": None,
        })

    # Sort all events chronologically (oldest first to calculate accurate running balance)
    ledger_events.sort(key=lambda x: x["timestamp"])

    # Calculate Running Balance
    running_balance = 0.0
    for ev in ledger_events:
        running_balance = round(running_balance + ev["inward_quantity"] - ev["outward_quantity"], 4)
        ev["running_balance"] = running_balance

    # Reverse to display latest first
    ledger_events.reverse()

    # Bulk hydrate metadata for all events
    prod_ids = list({e["product_id"] for e in ledger_events if e.get("product_id")})
    var_ids = list({e["variant_id"] for e in ledger_events if e.get("variant_id")})
    order_ids = list({e["order_id"] for e in ledger_events if e.get("order_id")})
    user_ids = list({e["user_id"] for e in ledger_events if e.get("user_id")})
    batch_ids = list({e["source_batch_id"] for e in ledger_events if e.get("source_batch_id")})
    wh_ids = list({
        e[loc]["id"] for e in ledger_events for loc in ("from_location", "to_location")
        if e.get(loc) and e[loc].get("type") == "warehouse" and e[loc].get("id")
    })
    veh_ids = list({
        e[loc]["id"] for e in ledger_events for loc in ("from_location", "to_location")
        if e.get(loc) and e[loc].get("type") == "vehicle" and e[loc].get("id")
    })
    vendor_ids = list({
        e[loc]["id"] for e in ledger_events for loc in ("from_location", "to_location")
        if e.get(loc) and e[loc].get("type") == "vendor" and e[loc].get("id")
    })

    prods_map = {p["_id"]: p for p in products_collection.find({"_id": {"$in": prod_ids}})} if prod_ids else {}
    vars_map = {v["_id"]: v for v in product_variants_collection.find({"_id": {"$in": var_ids}})} if var_ids else {}
    orders_map = {o["_id"]: o for o in orders_collection.find({"_id": {"$in": order_ids}})} if order_ids else {}
    users_map = {u["_id"]: u for u in users_collection.find({"_id": {"$in": user_ids}})} if user_ids else {}
    batches_map = {b["_id"]: b for b in stock_batches_collection.find({"_id": {"$in": batch_ids}})} if batch_ids else {}
    wh_map = {w["_id"]: w for w in warehouses_collection.find({"_id": {"$in": wh_ids}})} if wh_ids else {}
    veh_map = {v["_id"]: v for v in vehicles_collection.find({"_id": {"$in": veh_ids}})} if veh_ids else {}
    vendor_map = {vn["_id"]: vn for vn in vendors_collection.find({"_id": {"$in": vendor_ids}})} if vendor_ids else {}

    # Format output rows
    formatted = []
    for ev in ledger_events:
        p = prods_map.get(ev.get("product_id"), {})
        v = vars_map.get(ev.get("variant_id"), {})
        ord_doc = orders_map.get(ev.get("order_id"), {})
        u = users_map.get(ev.get("user_id"), {})

        # Resolve batch_no if missing
        b_no = ev.get("batch_no")
        if not b_no and ev.get("source_batch_id"):
            b_doc = batches_map.get(ev.get("source_batch_id"))
            if b_doc:
                b_no = b_doc.get("batch_no")

        # Resolve location names
        from_loc_name = "System"
        from_loc = ev.get("from_location", {})
        if from_loc.get("type") == "vendor":
            from_loc_name = vendor_map.get(from_loc.get("id"), {}).get("name", "Vendor")
        elif from_loc.get("type") == "warehouse":
            from_loc_name = wh_map.get(from_loc.get("id"), {}).get("name", "Warehouse")
        elif from_loc.get("type") == "vehicle":
            from_loc_name = veh_map.get(from_loc.get("id"), {}).get("vehicle_number", "Vehicle")
        elif from_loc.get("type") == "unallocated":
            from_loc_name = "Unallocated Stock"

        to_loc_name = "System"
        to_loc = ev.get("to_location", {})
        if to_loc.get("type") == "customer":
            to_loc_name = ord_doc.get("customer_name") or "Customer"
        elif to_loc.get("type") == "warehouse":
            to_loc_name = wh_map.get(to_loc.get("id"), {}).get("name", "Warehouse")
        elif to_loc.get("type") == "vehicle":
            to_loc_name = veh_map.get(to_loc.get("id"), {}).get("vehicle_number", "Vehicle")
        elif to_loc.get("type") == "unallocated":
            to_loc_name = "Unallocated Stock"

        doc_no = ord_doc.get("order_no") or ord_doc.get("invoice_no") or (str(ev.get("order_id")) if ev.get("order_id") else "-")
        user_name = u.get("name") or u.get("username") or ord_doc.get("created_by_name") or "System"

        ts = ev["timestamp"]
        dt_str = ts.strftime("%Y-%m-%d %H:%M:%S") if isinstance(ts, datetime) and ts != datetime.min else str(ts)

        row = {
            "date": dt_str,
            "action": ev["action"],
            "action_label": ev["action_label"],
            "document_type": ev["document_type"],
            "document_no": doc_no,
            "order_id": str(ev["order_id"]) if ev.get("order_id") else None,
            "batch_no": b_no,
            "product_id": str(ev["product_id"]) if ev.get("product_id") else None,
            "product_name": p.get("name"),
            "variant_id": str(ev["variant_id"]) if ev.get("variant_id") else None,
            "variant_name": v.get("name"),
            "sku": v.get("sku"),
            "from_location": from_loc_name,
            "to_location": to_loc_name,
            "inward_quantity": ev["inward_quantity"],
            "outward_quantity": ev["outward_quantity"],
            "unit_cost": ev.get("unit_cost", 0.0),
            "total_value": ev.get("total_value", 0.0),
            "running_balance": ev["running_balance"],
            "done_by": user_name,
        }

        # Optional search filter
        if search:
            sl = search.strip().lower()
            if not (
                sl in str(row["product_name"] or "").lower()
                or sl in str(row["variant_name"] or "").lower()
                or sl in str(row["sku"] or "").lower()
                or sl in str(row["batch_no"] or "").lower()
                or sl in str(row["document_no"] or "").lower()
                or sl in str(row["from_location"] or "").lower()
                or sl in str(row["to_location"] or "").lower()
            ):
                continue

        formatted.append(row)

    total = len(formatted)
    total_pages = (total + limit - 1) // limit
    paginated_data = formatted[skip : skip + limit]

    return {
        "success": True,
        "data": paginated_data,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "total_pages": total_pages,
        },
    }


# =========================================================
# BATCH TIMELINE & LIFECYCLE AUDIT
# =========================================================

@router.get("/inventory/batch-timeline/{batch_no}", tags=["Inventory"])
def get_batch_timeline(batch_no: str):
    """
    Returns the complete 360-degree journey of a stock batch:
    - Purchase inward & initial cost
    - Lineage / parent batch
    - All movements (warehouse to vehicle, warehouse to warehouse)
    - All sales consumptions (customer invoice, quantity, rate, margin)
    - Current remaining stock and locations
    """
    clean_batch_no = batch_no.strip()
    batches = list(stock_batches_collection.find({"batch_no": clean_batch_no}))
    if not batches:
        raise HTTPException(status_code=404, detail=f"Stock batch '{clean_batch_no}' not found")

    root_batch = next((b for b in batches if not b.get("parent_batch_id")), batches[0])
    prod = products_collection.find_one({"_id": root_batch.get("product_id")}) or {}
    var = product_variants_collection.find_one({"_id": root_batch.get("variant_id")}) or {}
    po = orders_collection.find_one({"_id": root_batch.get("purchase_order_id")}) if root_batch.get("purchase_order_id") else {}
    vendor = vendors_collection.find_one({"_id": root_batch.get("vendor_id")}) if root_batch.get("vendor_id") else {}

    batch_ids = [b["_id"] for b in batches]

    # Current Stock Distribution
    current_locations = []
    wh_ids = [b["warehouse_id"] for b in batches if b.get("warehouse_id")]
    veh_ids = [b["vehicle_id"] for b in batches if b.get("vehicle_id")]
    wh_map = {w["_id"]: w for w in warehouses_collection.find({"_id": {"$in": wh_ids}})} if wh_ids else {}
    veh_map = {v["_id"]: v for v in vehicles_collection.find({"_id": {"$in": veh_ids}})} if veh_ids else {}

    total_available = 0.0
    for b in batches:
        avail = float(b.get("available_quantity", 0))
        total_available += avail
        loc_name = "Unallocated"
        if b.get("location_type") == "warehouse" and b.get("warehouse_id"):
            loc_name = wh_map.get(b.get("warehouse_id"), {}).get("name", "Warehouse")
        elif b.get("location_type") == "vehicle" and b.get("vehicle_id"):
            loc_name = veh_map.get(b.get("vehicle_id"), {}).get("vehicle_number", "Vehicle")

        current_locations.append({
            "batch_id": str(b["_id"]),
            "location_type": b.get("location_type"),
            "location_name": loc_name,
            "initial_quantity": b.get("initial_quantity", 0),
            "available_quantity": avail,
            "status": b.get("status"),
        })

    # Allocations
    allocations = list(stock_batch_allocations_collection.find({
        "$or": [
            {"source_batch_id": {"$in": batch_ids}},
            {"destination_batch_id": {"$in": batch_ids}},
        ]
    }).sort([("created_at", 1)]))

    # Consumptions
    consumptions = list(sale_batch_consumptions_collection.find({
        "batch_no": clean_batch_no
    }).sort([("consumed_at", 1)]))

    # Build chronological journey
    journey = []

    # 1. Purchase Inward
    journey.append({
        "timestamp": root_batch.get("created_at"),
        "step": "PURCHASE_INWARD",
        "title": "Purchased from Vendor",
        "order_no": po.get("order_no") or po.get("invoice_no"),
        "party_name": vendor.get("name"),
        "quantity": root_batch.get("initial_quantity"),
        "unit_rate": root_batch.get("purchase_rate"),
        "notes": f"Batch initialized with {root_batch.get('initial_quantity')} units",
    })

    # 2. Transfers
    for a in allocations:
        from_loc = a.get("from_location", {}).get("type", "Source")
        to_loc = a.get("to_location", {}).get("type", "Destination")
        journey.append({
            "timestamp": a.get("created_at"),
            "step": "STOCK_TRANSFER",
            "title": f"Transferred from {from_loc} to {to_loc}",
            "order_id": str(a.get("transfer_order_id")) if a.get("transfer_order_id") else None,
            "quantity": a.get("quantity"),
            "unit_rate": a.get("unit_cost"),
            "from_location": a.get("from_location"),
            "to_location": a.get("to_location"),
        })

    # 3. Consumptions
    for c in consumptions:
        so = orders_collection.find_one({"_id": c.get("sale_order_id")}) or {}
        journey.append({
            "timestamp": c.get("consumed_at"),
            "step": "SALE_DELIVERY",
            "title": f"Delivered to Customer ({so.get('customer_name') or 'Customer'})",
            "order_no": so.get("order_no") or so.get("invoice_no"),
            "party_name": so.get("customer_name"),
            "quantity": c.get("quantity"),
            "purchase_rate": c.get("purchase_rate"),
            "sale_rate": c.get("sale_rate"),
            "cogs": c.get("cogs"),
            "gross_profit": c.get("gross_profit"),
        })

    journey.sort(key=lambda x: x.get("timestamp") or datetime.min)

    return {
        "success": True,
        "batch_no": clean_batch_no,
        "product_name": prod.get("name"),
        "variant_name": var.get("name"),
        "sku": var.get("sku"),
        "initial_quantity": root_batch.get("initial_quantity"),
        "total_available_quantity": total_available,
        "purchase_rate": root_batch.get("purchase_rate"),
        "vendor_name": vendor.get("name"),
        "current_locations": current_locations,
        "journey": journey,
    }
'''

content += ledger_and_timeline_code

with open("routes/Inventory.py", "w", encoding="utf-8") as f:
    f.write(content)

print("[OK] Successfully updated routes/Inventory.py")
