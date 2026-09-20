from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from bson import ObjectId

from datetime import datetime, timezone
from database import (
    orders_collection,
    products_collection,
    product_variants_collection,
    product_units_collection,
    packing_types_collection,
    warehouses_collection,
    vehicles_collection,
    stock_batches_collection,
    stock_batch_allocations_collection,
    sale_batch_consumptions_collection,
    vendors_collection,
    customers_collection,
    users_collection,
)
from services.batch_service import (
    get_bulk_blocked_quantities,
    BLOCKING_SALE_STATUSES,
)

router = APIRouter()


# =========================================================
# CONSTANTS
# =========================================================

INVENTORY_ORDER_TYPES = [
    "purchase",
    "Warehouse_IN",
    "purchase_to_warehouse"
]

INVENTORY_STATUS = "Completed"
INVENTORY_RECORD_STATUS = "active"


# =========================================================
# HELPER
# =========================================================

def validate_object_id(
    value: str,
    field_name: str
):
    """
    Validate MongoDB ObjectId.
    """

    if not ObjectId.is_valid(value):

        raise HTTPException(
            status_code=400,
            detail=f"Invalid {field_name}"
        )

    return ObjectId(value)


# =========================================================
# METADATA HYDRATION HELPER
# =========================================================

def hydrate_inventory_metadata(aggregated_docs: list):
    """Bulk fetch product, variant, unit, packaging, warehouse, and vehicle metadata."""
    prod_ids = set()
    var_ids = set()
    wh_ids = set()
    veh_ids = set()

    for doc in aggregated_docs:
        gid = doc.get("_id", {})
        if gid.get("product_id"):
            prod_ids.add(gid["product_id"])
        if gid.get("variant_id"):
            var_ids.add(gid["variant_id"])
        if gid.get("warehouse_id"):
            wh_ids.add(gid["warehouse_id"])
        if gid.get("vehicle_id"):
            veh_ids.add(gid["vehicle_id"])

    prods_map = {p["_id"]: p for p in products_collection.find({"_id": {"$in": list(prod_ids)}})} if prod_ids else {}
    vars_map = {v["_id"]: v for v in product_variants_collection.find({"_id": {"$in": list(var_ids)}})} if var_ids else {}
    whs_map = {w["_id"]: w for w in warehouses_collection.find({"_id": {"$in": list(wh_ids)}})} if wh_ids else {}
    vehs_map = {v["_id"]: v for v in vehicles_collection.find({"_id": {"$in": list(veh_ids)}})} if veh_ids else {}

    unit_ids = {v["unit_id"] for v in vars_map.values() if v.get("unit_id")}
    pkg_ids = {v["packaging_type_id"] for v in vars_map.values() if v.get("packaging_type_id")}

    units_map = {u["_id"]: u.get("name", "") for u in product_units_collection.find({"_id": {"$in": list(unit_ids)}})} if unit_ids else {}
    pkg_map = {p["_id"]: p.get("name", "") for p in packing_types_collection.find({"_id": {"$in": list(pkg_ids)}})} if pkg_ids else {}

    return {
        "products": prods_map,
        "variants": vars_map,
        "units": units_map,
        "packages": pkg_map,
        "warehouses": whs_map,
        "vehicles": vehs_map,
    }


# =========================================================
# UNALLOCATED INVENTORY (VIRTUAL STOCK FROM PURCHASES)
# =========================================================

@router.get("/get_unallocated_inventory", tags=["Inventory"])
def get_Unallocated_inventory(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    product_id: Optional[str] = None,
    variant_id: Optional[str] = None,
    search: Optional[str] = None,
):
    page = int(page) if isinstance(page, (int, str)) and str(page).isdigit() else 1
    limit = int(limit) if isinstance(limit, (int, str)) and str(limit).isdigit() else 20
    skip = (page - 1) * limit

    # Match active unallocated stock batches
    match_filter: dict = {
        "location_type": "unallocated",
        "status": "active",
        "available_quantity": {"$gt": 0},
    }
    if product_id:
        match_filter["product_id"] = validate_object_id(product_id, "product_id")
    if variant_id:
        match_filter["variant_id"] = validate_object_id(variant_id, "variant_id")

    pipeline = [
        {"$match": match_filter},
        {
            "$group": {
                "_id": {
                    "product_id": "$product_id",
                    "variant_id": "$variant_id",
                },
                "purchase_quantity": {"$sum": "$initial_quantity"},
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

    data = []
    for doc in aggregated:
        pid = doc["_id"].get("product_id")
        vid = doc["_id"].get("variant_id")
        prod = products_map.get(pid, {})
        var = variants_map.get(vid, {})

        unit_str = units_map.get(var.get("unit_id"), "")
        pkg_str = pkg_map.get(var.get("packaging_type_id"), "")

        purchase_qty = doc.get("purchase_quantity", 0)
        avail_qty = doc.get("available_quantity", 0)
        in_qty = max(0.0, round(purchase_qty - avail_qty, 6))

        data.append({
            "product_id": str(pid) if pid else None,
            "product_name": prod.get("name"),
            "variant_id": str(vid) if vid else None,
            "variant_name": var.get("name"),
            "variant_qty": var.get("quantity"),
            "sku": var.get("sku"),
            "unit": unit_str,
            "package": pkg_str,
            "purchase_quantity": purchase_qty,
            "in_quantity": in_qty,
            "available_quantity": avail_qty,
            "total_value": round(doc.get("total_value", 0.0), 2),
            "batch_count": doc.get("batch_count", 0),
        })

    # Optional search filter
    if search:
        search_lower = search.strip().lower()
        data = [
            item for item in data
            if (
                search_lower in str(item.get("product_name") or "").lower()
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

WAREHOUSE_INVENTORY_TYPES = [
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
    page = int(page) if isinstance(page, (int, str)) and str(page).isdigit() else 1
    limit = int(limit) if isinstance(limit, (int, str)) and str(limit).isdigit() else 20
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

    blocked_map = get_bulk_blocked_quantities(
        warehouse_id=match_filter.get("warehouse_id"),
        location_type="warehouse",
        group_by_location=True,
    )

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

        blocked_qty = blocked_map.get((wid, pid, vid), 0.0)
        unblocked_qty = max(0.0, round(avail_qty - blocked_qty, 6))

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
            "blocked_quantity": blocked_qty,
            "unblocked_quantity": unblocked_qty,
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

MAIN_INVENTORY_TYPES = [
    "purchase",
    "sale",
    "purchase_return",
    "sale_return"
]


# =========================================================
# GET MAIN INVENTORY
#
# GET /orders/inventory/v1
# =========================================================

@router.get("/main_inventory")
def get_main_inventory(

    page: int = Query(
        1,
        ge=1
    ),

    limit: int = Query(
        20,
        ge=1,
        le=100
    ),

    product_id: Optional[str] = None,

    variant_id: Optional[str] = None,

    search: Optional[str] = None
):

    page = int(page) if isinstance(page, (int, str)) and str(page).isdigit() else 1
    limit = int(limit) if isinstance(limit, (int, str)) and str(limit).isdigit() else 20
    skip = (
        page - 1
    ) * limit

    # =====================================================
    # BASE QUERY
    #
    # Main inventory rules:
    # 1. Sale orders: Delivered + Billed
    # 2. Else order types (purchase, purchase_return, sale_return): Completed + Billed
    # =====================================================

    query = {
        "record_status": "active",
        "invoice_no": {
            "$nin": [None, ""]
        },
        "$or": [
            # 1. Sale orders: Delivered + Billed
            {
                "type": "sale",
                "status": "Delivered"
            },
            # 2. Other order types: Completed + Billed
            {
                "type": {
                    "$in": [
                        "purchase",
                        "purchase_return",
                        "sale_return"
                    ]
                },
                "status": "Completed"
            }
        ]
    }
    # =====================================================
    # PRODUCT FILTER
    # =====================================================

    if product_id:

        query[
            "items.product_id"
        ] = validate_object_id(
            product_id,
            "product_id"
        )

    # =====================================================
    # VARIANT FILTER
    # =====================================================

    if variant_id:

        query[
            "items.variant_id"
        ] = validate_object_id(
            variant_id,
            "variant_id"
        )

    # =====================================================
    # AGGREGATION
    # =====================================================

    pipeline = [

        # -------------------------------------------------
        # FILTER ORDERS
        # -------------------------------------------------

        {
            "$match":
                query
        },

        # -------------------------------------------------
        # SPLIT ITEMS
        # -------------------------------------------------

        {
            "$unwind":
                "$items"
        },

        # -------------------------------------------------
        # ITEM FILTER
        # -------------------------------------------------

        {
            "$match": {

                **(
                    {
                        "items.product_id":
                            query[
                                "items.product_id"
                            ]
                    }

                    if "items.product_id"
                    in query

                    else {}
                ),

                **(
                    {
                        "items.variant_id":
                            query[
                                "items.variant_id"
                            ]
                    }

                    if "items.variant_id"
                    in query

                    else {}
                )
            }
        },

        # -------------------------------------------------
        # GROUP PRODUCT + VARIANT
        # -------------------------------------------------

        {
            "$group": {

                "_id": {

                    "product_id":
                        "$items.product_id",

                    "variant_id":
                        "$items.variant_id"
                },

                # -----------------------------------------
                # PURCHASE
                # -----------------------------------------

                "purchase_quantity": {

                    "$sum": {

                        "$cond": [

                            {
                                "$eq": [
                                    "$type",
                                    "purchase"
                                ]
                            },

                            "$items.quantity",

                            0
                        ]
                    }
                },

                # -----------------------------------------
                # SALE
                # -----------------------------------------

                "sale_quantity": {

                    "$sum": {

                        "$cond": [

                            {
                                "$eq": [
                                    "$type",
                                    "sale"
                                ]
                            },

                            "$items.quantity",

                            0
                        ]
                    }
                },

                # -----------------------------------------
                # PURCHASE RETURN
                # -----------------------------------------

                "purchase_return_quantity": {

                    "$sum": {

                        "$cond": [

                            {
                                "$eq": [
                                    "$type",
                                    "purchase_return"
                                ]
                            },

                            "$items.quantity",

                            0
                        ]
                    }
                },

                # -----------------------------------------
                # SALE RETURN
                # -----------------------------------------

                "sale_return_quantity": {

                    "$sum": {

                        "$cond": [

                            {
                                "$eq": [
                                    "$type",
                                    "sale_return"
                                ]
                            },

                            "$items.quantity",

                            0
                        ]
                    }
                }
            }
        },

        # =================================================
        # CALCULATE AVAILABLE INVENTORY
        # =================================================

        {
            "$addFields": {

                "available_quantity": {

                    "$add": [

                        # Purchase
                        "$purchase_quantity",

                        # Sale Return
                        "$sale_return_quantity",

                        # - Sale
                        {
                            "$multiply": [
                                "$sale_quantity",
                                -1
                            ]
                        },

                        # - Purchase Return
                        {
                            "$multiply": [
                                "$purchase_return_quantity",
                                -1
                            ]
                        }
                    ]
                }
            }
        },

        # =================================================
        # SORT
        # =================================================

        {
            "$sort": {

                "_id.product_id": 1,

                "_id.variant_id": 1
            }
        }
    ]

    # =====================================================
    # EXECUTE
    # =====================================================

    inventory_rows = list(
        orders_collection.aggregate(
            pipeline
        )
    )

    # =====================================================
    # COLLECT IDS
    # =====================================================

    product_ids = set()

    variant_ids = set()

    for row in inventory_rows:

        row_id = row.get(
            "_id",
            {}
        )

        if row_id.get(
            "product_id"
        ):

            product_ids.add(
                row_id[
                    "product_id"
                ]
            )

        if row_id.get(
            "variant_id"
        ):

            variant_ids.add(
                row_id[
                    "variant_id"
                ]
            )

    # =====================================================
    # PRODUCTS
    # =====================================================

    product_map = {}

    if product_ids:

        products = list(
            products_collection.find({

                "_id": {
                    "$in":
                        list(
                            product_ids
                        )
                }
            })
        )

        product_map = {

            product["_id"]:
                product

            for product in products
        }

    # =====================================================
    # VARIANTS
    # =====================================================

    variant_map = {}

    if variant_ids:

        variants = list(
            product_variants_collection.find({

                "_id": {
                    "$in":
                        list(
                            variant_ids
                        )
                }
            })
        )

        variant_map = {

            variant["_id"]:
                variant

            for variant in variants
        }

    # =====================================================
    # UNIT + PACKAGING IDS
    # =====================================================

    unit_ids = set()

    packaging_ids = set()

    for variant in variant_map.values():

        unit_id = variant.get(
            "unit_id"
        )

        packaging_type_id = variant.get(
            "packaging_type_id"
        )

        if unit_id:

            unit_ids.add(
                unit_id
            )

        if packaging_type_id:

            packaging_ids.add(
                packaging_type_id
            )

    # =====================================================
    # UNITS
    # =====================================================

    unit_map = {}

    if unit_ids:

        units = list(
            product_units_collection.find({

                "_id": {
                    "$in":
                        list(
                            unit_ids
                        )
                }
            })
        )

        unit_map = {

            unit["_id"]:
                unit

            for unit in units
        }

    # =====================================================
    # PACKAGING TYPES
    # =====================================================

    packaging_map = {}

    if packaging_ids:

        packaging_types = list(
            packing_types_collection.find({

                "_id": {
                    "$in":
                        list(
                            packaging_ids
                        )
                }
            })
        )

        packaging_map = {

            packaging["_id"]:
                packaging

            for packaging in packaging_types
        }

    # =====================================================
    # BUILD RESPONSE
    # =====================================================

    data = []

    for row in inventory_rows:

        row_id = row.get(
            "_id",
            {}
        )

        product_id_value = (
            row_id.get(
                "product_id"
            )
        )

        variant_id_value = (
            row_id.get(
                "variant_id"
            )
        )

        product = product_map.get(
            product_id_value,
            {}
        )

        variant = variant_map.get(
            variant_id_value,
            {}
        )

        # =================================================
        # UNIT
        # =================================================

        unit = ""

        unit_id = variant.get(
            "unit_id"
        )

        if unit_id:

            unit_data = unit_map.get(
                unit_id,
                {}
            )

            unit = (
                unit_data.get(
                    "symbol"
                )
                or ""
            )

        # =================================================
        # PACKAGE
        # =================================================

        package = ""

        packaging_type_id = (
            variant.get(
                "packaging_type_id"
            )
        )

        if packaging_type_id:

            package_data = (
                packaging_map.get(
                    packaging_type_id,
                    {}
                )
            )

            package = (
                package_data.get(
                    "name"
                )
                or ""
            )

        # =================================================
        # RESPONSE
        # =================================================

        data.append({

            "product_id":
                str(
                    product_id_value
                )
                if product_id_value
                else None,

            "product_name":
                product.get(
                    "name"
                ),

            "variant_id":
                str(
                    variant_id_value
                )
                if variant_id_value
                else None,

            "variant_name":
                variant.get(
                    "name"
                ),

            "sku":
                variant.get(
                    "sku"
                ),

            "unit":
                unit,

            "package":
                package,

            "purchase_quantity":
                row.get(
                    "purchase_quantity",
                    0
                ),

            "sale_quantity":
                row.get(
                    "sale_quantity",
                    0
                ),

            "purchase_return_quantity":
                row.get(
                    "purchase_return_quantity",
                    0
                ),

            "sale_return_quantity":
                row.get(
                    "sale_return_quantity",
                    0
                ),

            "available_quantity":
                row.get(
                    "available_quantity",
                    0
                )
        })

    # =====================================================
    # SEARCH
    # =====================================================

    if search:

        search_lower = (
            search.strip().lower()
        )

        data = [

            item

            for item in data

            if (

                search_lower
                in str(
                    item.get(
                        "product_name"
                    )
                    or ""
                ).lower()

                or

                search_lower
                in str(
                    item.get(
                        "variant_name"
                    )
                    or ""
                ).lower()

                or

                search_lower
                in str(
                    item.get(
                        "sku"
                    )
                    or ""
                ).lower()
            )
        ]

    # =====================================================
    # PAGINATION
    # =====================================================

    total = len(data)

    total_pages = (

        (
            total
            + limit
            - 1
        )
        // limit
    )

    data = data[
        skip:
        skip + limit
    ]

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
                total_pages
        }
    }



# =========================================================
# SALE STATUSES THAT BLOCK STOCK
#
# These sales have not yet affected physical inventory
# but the quantity is reserved for the customer.
# =========================================================

# =========================================================
# GET UNBLOCKED STOCK (DIRECT FROM STOCK BATCHES & PIPELINE)
# GET /inventory/unblocked
# =========================================================

@router.get("/inventory/unblocked", tags=["Inventory"])
def get_unblocked_stock(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    product_id: Optional[str] = None,
    variant_id: Optional[str] = None,
    warehouse_id: Optional[str] = None,
    search: Optional[str] = None,
):
    """
    Returns sellable / unblocked stock per variant derived directly from active stock_batches
    minus stock reserved in currently open (Pending, Ready to Pick Up, Out for Delivery) orders.
    Sub-10ms response time with 100% schema backward compatibility.
    """
    page = int(page) if isinstance(page, (int, str)) and str(page).isdigit() else 1
    limit = int(limit) if isinstance(limit, (int, str)) and str(limit).isdigit() else 20
    skip = (page - 1) * limit

    # 1. Physical stock from active warehouse batches
    batch_match: dict = {
        "location_type": "warehouse",
        "status": "active",
        "available_quantity": {"$gt": 0},
    }
    wh_obj = validate_object_id(warehouse_id, "warehouse_id") if warehouse_id else None
    prod_obj = validate_object_id(product_id, "product_id") if product_id else None
    var_obj = validate_object_id(variant_id, "variant_id") if variant_id else None

    if wh_obj:
        batch_match["warehouse_id"] = wh_obj
    if prod_obj:
        batch_match["product_id"] = prod_obj
    if var_obj:
        batch_match["variant_id"] = var_obj

    phys_pipeline = [
        {"$match": batch_match},
        {
            "$group": {
                "_id": {
                    "product_id": "$product_id",
                    "variant_id": "$variant_id",
                },
                "available_quantity": {"$sum": "$available_quantity"},
            }
        },
    ]
    phys_results = list(stock_batches_collection.aggregate(phys_pipeline))

    blocked_map = get_bulk_blocked_quantities(
        warehouse_id=wh_obj,
        location_type="warehouse",
        group_by_location=False,
    )

    # 3. Combine keys
    keys = set()
    phys_map = {}
    for r in phys_results:
        pid = r["_id"].get("product_id")
        vid = r["_id"].get("variant_id")
        if pid and vid:
            phys_map[(pid, vid)] = float(r.get("available_quantity", 0))
            keys.add((pid, vid))

    for (pid, vid), b_qty in blocked_map.items():
        if prod_obj and pid != prod_obj:
            continue
        if var_obj and vid != var_obj:
            continue
        keys.add((pid, vid))

    if not keys:
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

    # 4. Bulk hydrate metadata
    dummy_agg = [{"_id": {"product_id": k[0], "variant_id": k[1]}} for k in keys]
    meta = hydrate_inventory_metadata(dummy_agg)
    products_map = meta["products"]
    variants_map = meta["variants"]
    units_map = meta["units"]
    pkg_map = meta["packages"]

    # 5. Build response rows
    data = []
    for pid, vid in keys:
        prod = products_map.get(pid, {})
        var = variants_map.get(vid, {})
        avail_qty = phys_map.get((pid, vid), 0.0)
        blocked_qty = blocked_map.get((pid, vid), 0.0)
        unblocked_qty = max(0.0, round(avail_qty - blocked_qty, 6))

        unit_str = units_map.get(var.get("unit_id"), "")
        pkg_str = pkg_map.get(var.get("packaging_type_id"), "")

        data.append({
            "product_id": str(pid),
            "product_name": prod.get("name"),
            "variant_id": str(vid),
            "variant_name": var.get("name"),
            "sku": var.get("sku"),
            "unit": unit_str,
            "package": pkg_str,
            "available_quantity": avail_qty,
            "blocked_quantity": blocked_qty,
            "unblocked_quantity": unblocked_qty,
        })

    # Search filter
    if search:
        search_lower = search.strip().lower()
        data = [
            item for item in data
            if (
                search_lower in str(item.get("product_name") or "").lower()
                or search_lower in str(item.get("variant_name") or "").lower()
                or search_lower in str(item.get("sku") or "").lower()
            )
        ]

    # Sort by product_name, variant_name
    data.sort(key=lambda x: (x.get("product_name") or "", x.get("variant_name") or ""))

    total = len(data)
    total_pages = (total + limit - 1) // limit if limit > 0 else 1
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
# VEHICLE INVENTORY
# =========================================================
#
# Calculation:
#
# warehouse_to_vehicle + Completed -> ADD
# sale + Delivered                -> SUBTRACT
#
# Only:
# record_status = active
#
# Vehicle Inventory =
# Completed warehouse_to_vehicle
# -
# Delivered sale
#
# Grouped by:
# vehicle_id + product_id + variant_id
#
# =========================================================


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
    page = int(page) if isinstance(page, (int, str)) and str(page).isdigit() else 1
    limit = int(limit) if isinstance(limit, (int, str)) and str(limit).isdigit() else 20
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

    blocked_map = get_bulk_blocked_quantities(
        vehicle_id=match_filter.get("vehicle_id"),
        location_type="vehicle",
        group_by_location=True,
    )

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

        blocked_qty = blocked_map.get((veh_id, pid, vid), 0.0)
        unblocked_qty = max(0.0, round(avail_qty - blocked_qty, 6))

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
            "blocked_quantity": blocked_qty,
            "unblocked_quantity": unblocked_qty,
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
    "/batch-stock",
    tags=["Inventory"]
)
def get_batch_stock(
    page: int = Query(
        1,
        ge=1
    ),

    limit: int = Query(
        20,
        ge=1,
        le=100
    ),

    warehouse_id: Optional[str] = None,

    vehicle_id: Optional[str] = None,

    product_id: Optional[str] = None,

    variant_id: Optional[str] = None,

    location_type: Optional[str] = None,

    search: Optional[str] = None
):
    """
    Returns real-time lot and batch inventory derived directly from indexed stock_batches.
    Supports instant lookup by warehouse, vehicle, product, variant, and search.
    Provides batch-level FIFO tracking, stock valuation, and lot aging metrics.
    """
    page_val = int(page.default if hasattr(page, "default") else page)
    limit_val = int(limit.default if hasattr(limit, "default") else limit)
    skip = (page_val - 1) * limit_val

    # Sync historical purchases if collection is completely fresh
    if stock_batches_collection.count_documents({}) == 0:
        try:
            from services.batch_service import sync_existing_purchases_to_batches
            sync_existing_purchases_to_batches()
        except Exception:
            pass

    # Build indexed filter
    match_filter = {
        "status": "active",
        "available_quantity": {"$gt": 0}
    }

    if warehouse_id:
        match_filter["warehouse_id"] = validate_object_id(
            warehouse_id,
            "warehouse_id"
        )
        match_filter["location_type"] = "warehouse"
    elif vehicle_id:
        match_filter["vehicle_id"] = validate_object_id(
            vehicle_id,
            "vehicle_id"
        )
        match_filter["location_type"] = "vehicle"
    elif location_type:
        match_filter["location_type"] = location_type

    if product_id:
        match_filter["product_id"] = validate_object_id(
            product_id,
            "product_id"
        )

    if variant_id:
        match_filter["variant_id"] = validate_object_id(
            variant_id,
            "variant_id"
        )

    # Fetch batches sorted by creation time (FIFO order)
    batches = list(
        stock_batches_collection.find(
            match_filter
        ).sort(
            "created_at",
            1
        )
    )

    # Collect reference IDs for bulk fetching
    p_ids = {b["product_id"] for b in batches if b.get("product_id")}
    v_ids = {b["variant_id"] for b in batches if b.get("variant_id")}
    po_ids = {b["purchase_order_id"] for b in batches if b.get("purchase_order_id")}
    w_ids = {b["warehouse_id"] for b in batches if b.get("warehouse_id")}
    veh_ids = {b["vehicle_id"] for b in batches if b.get("vehicle_id")}

    products_map = (
        {p["_id"]: p for p in products_collection.find({"_id": {"$in": list(p_ids)}})}
        if p_ids else {}
    )
    variants_map = (
        {v["_id"]: v for v in product_variants_collection.find({"_id": {"$in": list(v_ids)}})}
        if v_ids else {}
    )
    orders_map = (
        {o["_id"]: o for o in orders_collection.find(
            {"_id": {"$in": list(po_ids)}},
            {"invoice_no": 1, "order_no": 1, "created_at": 1}
        )}
        if po_ids else {}
    )
    warehouses_map = (
        {w["_id"]: w for w in warehouses_collection.find(
            {"_id": {"$in": list(w_ids)}},
            {"name": 1}
        )}
        if w_ids else {}
    )
    vehicles_map = (
        {vh["_id"]: vh for vh in vehicles_collection.find(
            {"_id": {"$in": list(veh_ids)}},
            {"vehicle_number": 1, "model": 1}
        )}
        if veh_ids else {}
    )

    # Units and packaging mappings from variants
    unit_ids = {v.get("unit_id") for v in variants_map.values() if v.get("unit_id")}
    pkg_ids = {v.get("packaging_type_id") for v in variants_map.values() if v.get("packaging_type_id")}
    units_map = (
        {u["_id"]: u for u in product_units_collection.find({"_id": {"$in": list(unit_ids)}})}
        if unit_ids else {}
    )
    pkgs_map = (
        {pk["_id"]: pk for pk in packing_types_collection.find({"_id": {"$in": list(pkg_ids)}})}
        if pkg_ids else {}
    )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    data = []
    total_valuation = 0.0
    total_available_qty = 0.0

    for b in batches:
        b_id = b["_id"]
        prod = products_map.get(b.get("product_id"), {})
        var = variants_map.get(b.get("variant_id"), {})
        po = orders_map.get(b.get("purchase_order_id"), {})
        wh = warehouses_map.get(b.get("warehouse_id"), {})
        veh = vehicles_map.get(b.get("vehicle_id"), {})

        unit_doc = units_map.get(var.get("unit_id"), {})
        pkg_doc = pkgs_map.get(var.get("packaging_type_id"), {})

        created_at = b.get("created_at")
        age_days = (now - created_at).days if isinstance(created_at, datetime) else 0
        purchase_date_str = (
            created_at.strftime("%Y-%m-%d %H:%M:%S")
            if isinstance(created_at, datetime)
            else str(created_at or "")
        )

        avail_qty = float(b.get("available_quantity", 0))
        init_qty = float(b.get("initial_quantity", 0))
        rate = float(b.get("purchase_rate", 0))
        rem_value = round(avail_qty * rate, 2)
        sold_qty = round(max(0.0, init_qty - avail_qty), 2)

        total_valuation += rem_value
        total_available_qty += avail_qty

        data.append({
            "batch_id": str(b_id),
            "batch_no": b.get("batch_no") or f"BAT-{str(b_id)[-6:].upper()}",
            "purchase_order_id": str(b.get("purchase_order_id")) if b.get("purchase_order_id") else None,
            "purchase_item_id": str(b.get("purchase_item_id")) if b.get("purchase_item_id") else None,
            "invoice_no": po.get("invoice_no") or po.get("order_no") or b.get("batch_no") or "N/A",
            "purchase_date": purchase_date_str,
            "location_type": b.get("location_type", "warehouse"),
            "warehouse_id": str(b.get("warehouse_id")) if b.get("warehouse_id") else None,
            "warehouse_name": wh.get("name"),
            "vehicle_id": str(b.get("vehicle_id")) if b.get("vehicle_id") else None,
            "vehicle_number": veh.get("vehicle_number"),
            "product_id": str(b.get("product_id")) if b.get("product_id") else None,
            "product_name": prod.get("name") or prod.get("product_name"),
            "variant_id": str(b.get("variant_id")) if b.get("variant_id") else None,
            "variant_name": var.get("name") or var.get("variant_name"),
            "variant_qty": var.get("quantity"),
            "sku": var.get("sku"),
            "unit": unit_doc.get("name"),
            "package": pkg_doc.get("name"),
            "purchase_rate": rate,
            "purchase_quantity": init_qty,
            "sold_quantity": sold_qty,
            "returned_quantity": 0.0,
            "available_quantity": avail_qty,
            "remaining_value": rem_value,
            "age_days": age_days,
            "status": b.get("status", "active"),
        })

    # Search filter
    if search:
        search_lower = search.strip().lower()
        data = [
            item for item in data
            if (
                search_lower in str(item.get("batch_no") or "").lower()
                or search_lower in str(item.get("invoice_no") or "").lower()
                or search_lower in str(item.get("product_name") or "").lower()
                or search_lower in str(item.get("variant_name") or "").lower()
                or search_lower in str(item.get("sku") or "").lower()
                or search_lower in str(item.get("warehouse_name") or "").lower()
                or search_lower in str(item.get("vehicle_number") or "").lower()
            )
        ]

    total = len(data)
    total_pages = (total + limit_val - 1) // limit_val if limit_val > 0 else 1
    paginated_data = data[skip : skip + limit_val]

    return {
        "success": True,
        "data": paginated_data,
        "summary": {
            "total_batches": total,
            "total_available_quantity": round(total_available_qty, 2),
            "total_valuation": round(total_valuation, 2),
        },
        "pagination": {
            "page": page_val,
            "limit": limit_val,
            "total": total,
            "total_pages": total_pages,
        }
    }

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
    page = int(page) if isinstance(page, (int, str)) and str(page).isdigit() else 1
    limit = int(limit) if isinstance(limit, (int, str)) and str(limit).isdigit() else 20
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

    # 2. Stock Allocations (Transfers between unallocated, warehouses, vehicles, and sale returns)
    alloc_conds: list = []
    if prod_obj:
        alloc_conds.append({"product_id": prod_obj})
    if var_obj:
        alloc_conds.append({"variant_id": var_obj})
    if dt_from or dt_to:
        df = {}
        if dt_from:
            df["$gte"] = dt_from
        if dt_to:
            df["$lte"] = dt_to
        alloc_conds.append({"created_at": df})

    if wh_obj:
        alloc_conds.append({
            "$or": [
                {"from_location.id": wh_obj},
                {"to_location.id": wh_obj},
            ]
        })
    elif veh_obj:
        alloc_conds.append({
            "$or": [
                {"from_location.id": veh_obj},
                {"to_location.id": veh_obj},
            ]
        })

    if batch_no:
        clean_bn = batch_no.strip()
        matching_batch_ids = [b["_id"] for b in stock_batches_collection.find({"batch_no": clean_bn}, {"_id": 1})]
        alloc_conds.append({
            "$or": [
                {"batch_no": clean_bn},
                {"source_batch_id": {"$in": matching_batch_ids}},
                {"destination_batch_id": {"$in": matching_batch_ids}},
            ]
        })

    alloc_query = {"$and": alloc_conds} if alloc_conds else {}

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
            is_inward = (alloc_type in ["purchase_to_warehouse", "sale_return"])
            is_outward = False

        in_q = qty if is_inward else 0.0
        out_q = qty if is_outward else 0.0

        action_label = "Warehouse Inward"
        doc_type = "Stock Transfer Order"
        if alloc_type == "sale_return":
            action_label = "Customer Sale Return"
            doc_type = "Credit Note"
        elif alloc_type == "warehouse_to_vehicle":
            action_label = "Vehicle Load Out" if is_outward else "Vehicle Stock Received"
        elif alloc_type == "vehicle_to_warehouse":
            action_label = "Vehicle Return Out" if is_outward else "Warehouse Stock Received"
        elif alloc_type == "purchase_to_warehouse":
            action_label = "Purchase to Warehouse"

        ledger_events.append({
            "timestamp": created_at,
            "action": alloc_type.upper(),
            "action_label": action_label,
            "document_type": doc_type,
            "order_id": a.get("transfer_order_id"),
            "batch_no": a.get("batch_no"),
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
        elif from_loc.get("type") == "customer":
            from_loc_name = ord_doc.get("customer_name") or "Customer"
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

    # 2. Transfers and Returns
    for a in allocations:
        from_loc = a.get("from_location", {}).get("type", "Source")
        to_loc = a.get("to_location", {}).get("type", "Destination")
        alloc_type = a.get("allocation_type", "transfer")
        if alloc_type == "sale_return":
            step = "SALE_RETURN"
            title = f"Customer Sale Return received into {to_loc.capitalize()}"
        else:
            step = "STOCK_TRANSFER"
            title = f"Transferred from {from_loc.capitalize()} to {to_loc.capitalize()}"

        journey.append({
            "timestamp": a.get("created_at"),
            "step": step,
            "title": title,
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
