from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from bson import ObjectId

from database import (
    orders_collection,
    products_collection,
    product_variants_collection,
    product_units_collection,
    packing_types_collection,
    warehouses_collection
)

router = APIRouter()


# =========================================================
# CONSTANTS
# =========================================================

INVENTORY_ORDER_TYPES = [
    "purchase",
    "Warehouse_IN"
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
# INVENTORY API
# =========================================================
#
# GET /inventory/v1
#
# Calculates:
#
# Confirmed + active + purchase = ADD
# Confirmed + active + IN       = SUBTRACT
#
# available =
# purchase_quantity - in_quantity
#
# Grouped by:
#
# product_id + variant_id
#
# Warehouse is NOT used for grouping.
#
# =========================================================

@router.get("/get_unallocated_inventory", tags=["Inventory"])
def get_Unallocated_inventory(

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

    # =====================================================
    # PAGINATION
    # =====================================================

    skip = (
        page - 1
    ) * limit

    # =====================================================
    # OPTIONAL PRODUCT FILTER
    # =====================================================

    match_filter = {

        "status":
            INVENTORY_STATUS,

        "record_status":
            INVENTORY_RECORD_STATUS,

        "type": {
            "$in":
                INVENTORY_ORDER_TYPES
        }
    }

    # -----------------------------------------------------
    # PRODUCT FILTER
    # -----------------------------------------------------

    if product_id:

        match_filter[
            "items.product_id"
        ] = validate_object_id(
            product_id,
            "product_id"
        )

    # -----------------------------------------------------
    # VARIANT FILTER
    # -----------------------------------------------------

    if variant_id:

        match_filter[
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
        # ONLY VALID INVENTORY ORDERS
        # -------------------------------------------------

        {
            "$match":
                match_filter
        },

        # -------------------------------------------------
        # ONE DOCUMENT PER ITEM
        # -------------------------------------------------

        {
            "$unwind":
                "$items"
        },

        # -------------------------------------------------
        # IMPORTANT
        #
        # The product/variant filter needs to be applied
        # again after unwind so only matching items are
        # aggregated.
        # -------------------------------------------------

        {
            "$match": {

                **{

                    "items.product_id":
                        match_filter.get(
                            "items.product_id",
                            {
                                "$exists":
                                    True
                            }
                        )
                },

                **({

                    "items.variant_id":
                        match_filter[
                            "items.variant_id"
                        ]

                } if "items.variant_id"
                in match_filter else {})
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
                # PURCHASE QUANTITY
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
                # IN QUANTITY
                # -----------------------------------------

                "in_quantity": {

                    "$sum": {

                        "$cond": [

                            {
                                "$eq": [
                                    "$type",
                                    "Warehouse_IN"
                                ]
                            },

                            "$items.quantity",

                            0
                        ]
                    }
                }
            }
        },

        # -------------------------------------------------
        # AVAILABLE QUANTITY
        # -------------------------------------------------

        {
            "$addFields": {

                "available_quantity": {

                    "$subtract": [

                        "$purchase_quantity",

                        "$in_quantity"
                    ]
                }
            }
        },

        # -------------------------------------------------
        # REMOVE NEGATIVE STOCK IF REQUIRED
        #
        # We keep the actual calculation.
        # Therefore negative stock can be visible if
        # IN quantity exceeds purchase quantity.
        # -------------------------------------------------

        {
            "$sort": {

                "_id.product_id": 1,

                "_id.variant_id": 1
            }
        },

        # -------------------------------------------------
        # PAGINATION
        # -------------------------------------------------

        {
            "$facet": {

                "data": [

                    {
                        "$skip":
                            skip
                    },

                    {
                        "$limit":
                            limit
                    }
                ],

                "total": [

                    {
                        "$count":
                            "count"
                    }
                ]
            }
        }
    ]

    # =====================================================
    # RUN AGGREGATION
    # =====================================================

    result = list(
        orders_collection.aggregate(
            pipeline
        )
    )

    # =====================================================
    # EMPTY RESULT
    # =====================================================

    if not result:

        return {

            "success":
                True,

            "data":
                [],

            "pagination": {

                "page":
                    page,

                "limit":
                    limit,

                "total":
                    0,

                "total_pages":
                    0
            }
        }

    aggregation_result = result[0]

    inventory_rows = (
        aggregation_result.get(
            "data",
            []
        )
    )

    total = 0

    total_data = (
        aggregation_result.get(
            "total",
            []
        )
    )

    if total_data:

        total = total_data[0].get(
            "count",
            0
        )

    # =====================================================
    # SEARCH
    #
    # Search requires product/variant master data, so it
    # is applied after aggregation.
    #
    # If search is provided, we fetch all aggregated
    # records first instead of using the pagination facet.
    # =====================================================

    if search:

        search_pipeline = [

            {
                "$match":
                    match_filter
            },

            {
                "$unwind":
                    "$items"
            },

            {
                "$group": {

                    "_id": {

                        "product_id":
                            "$items.product_id",

                        "variant_id":
                            "$items.variant_id"
                    },

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

                    "in_quantity": {

                        "$sum": {

                            "$cond": [

                                {
                                    "$eq": [
                                        "$type",
                                        "IN"
                                    ]
                                },

                                "$items.quantity",

                                0
                            ]
                        }
                    }
                }
            },

            {
                "$addFields": {

                    "available_quantity": {

                        "$subtract": [

                            "$purchase_quantity",

                            "$in_quantity"
                        ]
                    }
                }
            }
        ]

        inventory_rows = list(
            orders_collection.aggregate(
                search_pipeline
            )
        )

    # =====================================================
    # COLLECT IDS
    # =====================================================

    product_ids = set()

    variant_ids = set()

    for row in inventory_rows:

        product_id = (
            row["_id"]
            .get("product_id")
        )

        variant_id = (
            row["_id"]
            .get("variant_id")
        )

        if product_id:

            product_ids.add(
                product_id
            )

        if variant_id:

            variant_ids.add(
                variant_id
            )

    # =====================================================
    # FETCH PRODUCTS
    # =====================================================

    products = list(

        products_collection.find({

            "_id": {
                "$in":
                    list(product_ids)
            }

        })
    )

    product_map = {

        product["_id"]:
            product

        for product in products
    }

    # =====================================================
    # FETCH VARIANTS
    # =====================================================

    variants = list(

        product_variants_collection.find({

            "_id": {
                "$in":
                    list(variant_ids)
            }

        })
    )

    variant_map = {

        variant["_id"]:
            variant

        for variant in variants
    }

    # =====================================================
    # COLLECT UNIT IDS
    # =====================================================

    unit_ids = set()

    packaging_ids = set()

    for variant in variants:

        unit_id = variant.get(
            "unit_id"
        )

        packaging_id = variant.get(
            "packaging_type_id"
        )

        if unit_id:

            unit_ids.add(
                unit_id
            )

        if packaging_id:

            packaging_ids.add(
                packaging_id
            )

    # =====================================================
    # FETCH UNITS
    # =====================================================

    units = list(

        product_units_collection.find({

            "_id": {
                "$in":
                    list(unit_ids)
            }

        })
    )

    unit_map = {

        unit["_id"]:
            unit

        for unit in units
    }

    # =====================================================
    # FETCH PACKAGING TYPES
    # =====================================================

    packaging_types = list(

        packing_types_collection.find({

            "_id": {
                "$in":
                    list(packaging_ids)
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

        product_id = (
            row["_id"]
            .get("product_id")
        )

        variant_id = (
            row["_id"]
            .get("variant_id")
        )

        product = (
            product_map.get(
                product_id
            )
            or {}
        )

        variant = (
            variant_map.get(
                variant_id
            )
            or {}
        )

        # -------------------------------------------------
        # UNIT
        # -------------------------------------------------

        unit = ""

        unit_id = variant.get(
            "unit_id"
        )

        if unit_id:

            unit_data = (
                unit_map.get(
                    unit_id
                )
                or {}
            )

            unit = (
                unit_data.get(
                    "symbol"
                )
                or ""
            )

        # -------------------------------------------------
        # PACKAGE
        # -------------------------------------------------

        package = ""

        packaging_type_id = (
            variant.get(
                "packaging_type_id"
            )
        )

        if packaging_type_id:

            package_data = (
                packaging_map.get(
                    packaging_type_id
                )
                or {}
            )

            package = (
                package_data.get(
                    "name"
                )
                or ""
            )

        # -------------------------------------------------
        # RESPONSE
        # -------------------------------------------------

        data.append({

            "product_id":
                str(product_id)
                if product_id
                else None,

            "product_name":
                product.get(
                    "name"
                ),

            "variant_id":
                str(variant_id)
                if variant_id
                else None,

            "variant_name":
                variant.get(
                    "name"
                ),
            "variant_qty":
                variant.get(
                    "quantity"
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

            "in_quantity":
                row.get(
                    "in_quantity",
                    0
                ),

            "available_quantity":
                row.get(
                    "available_quantity",
                    0
                )
        })

    # =====================================================
    # SEARCH FILTER
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

        total = len(data)

        start = skip

        end = (
            start
            + limit
        )

        data = data[
            start:end
        ]

    # =====================================================
    # PAGINATION
    # =====================================================

    total_pages = (

        (
            total
            + limit
            - 1
        )
        // limit
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
                total_pages
        }
    }


# =========================================================
# WAREHOUSE INVENTORY
# =========================================================
#
# Calculation:
#
# Confirmed + active + Warehouse_IN  → ADD
# Confirmed + active + Warehouse_OUT → SUBTRACT
#
# Warehouse inventory =
# Warehouse_IN - Warehouse_OUT
#
# Grouped by:
# warehouse_id + product_id + variant_id
#
# =========================================================


WAREHOUSE_INVENTORY_TYPES = [
    "Warehouse_IN",
    "Warehouse_OUT",
    "Vehicle_IN",
]


# =========================================================
# GET WAREHOUSE INVENTORY
#
# GET /inventory/warehouse-inventory/v1
# =========================================================

@router.get("/warehouse-inventory", tags=["Inventory"])
def get_warehouse_inventory(

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

    product_id: Optional[str] = None,

    variant_id: Optional[str] = None,

    search: Optional[str] = None
):

    skip = (
        page - 1
    ) * limit

    # =====================================================
    # BASE FILTER
    # =====================================================

    query = {

        "type": {
            "$in":
                WAREHOUSE_INVENTORY_TYPES
        },

        "status":
            "Completed",

        "record_status":
            "active",

        "warehouse_id": {
            "$exists":
                True,

            "$ne":
                None
        }
    }

    # =====================================================
    # WAREHOUSE FILTER
    # =====================================================

    if warehouse_id:

        query[
            "warehouse_id"
        ] = validate_object_id(
            warehouse_id,
            "warehouse_id"
        )

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
        # ONLY:
        #
        # Confirmed
        # active
        # Warehouse_IN / Warehouse_OUT
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
        # APPLY ITEM FILTERS
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
        # GROUP
        #
        # warehouse + product + variant
        # -------------------------------------------------

        {
            "$group": {

                "_id": {

                    "warehouse_id":
                        "$warehouse_id",

                    "product_id":
                        "$items.product_id",

                    "variant_id":
                        "$items.variant_id"
                },

                # -----------------------------------------
                # WAREHOUSE IN
                # -----------------------------------------

                "warehouse_in_quantity": {

                    "$sum": {

                        "$cond": [

                            {
                                "$eq": [
                                    "$type",
                                    "Warehouse_IN"
                                ]
                            },

                            "$items.quantity",

                            0
                        ]
                    }
                },

                # -----------------------------------------
                # WAREHOUSE OUT
                # -----------------------------------------

                "warehouse_out_quantity": {
                    "$sum": {
                        "$cond": [
                            {
                                "$in": [
                                    "$type",
                                    [
                                        "Warehouse_OUT",
                                        "Vehicle_IN"
                                    ]
                                ]
                            },
                            "$items.quantity",
                            0
                        ]
                    }
                }
            }
        },

        # -------------------------------------------------
        # AVAILABLE QUANTITY
        # -------------------------------------------------

        {
            "$addFields": {

                "available_quantity": {

                    "$subtract": [

                        "$warehouse_in_quantity",

                        "$warehouse_out_quantity"
                    ]
                }
            }
        },

        # -------------------------------------------------
        # SORT
        # -------------------------------------------------

        {
            "$sort": {

                "_id.warehouse_id": 1,

                "_id.product_id": 1,

                "_id.variant_id": 1
            }
        }
    ]

    # =====================================================
    # EXECUTE AGGREGATION
    # =====================================================

    inventory_rows = list(
        orders_collection.aggregate(
            pipeline
        )
    )

    # =====================================================
    # SEARCH
    #
    # Search is done after resolving master data.
    # =====================================================

    # =====================================================
    # COLLECT IDS
    # =====================================================

    warehouse_ids = set()

    product_ids = set()

    variant_ids = set()

    for row in inventory_rows:

        row_id = row.get(
            "_id",
            {}
        )

        if row_id.get(
            "warehouse_id"
        ):

            warehouse_ids.add(
                row_id[
                    "warehouse_id"
                ]
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
    # WAREHOUSES
    # =====================================================

    warehouse_map = {}

    if warehouse_ids:

        warehouses = list(
            warehouses_collection.find({

                "_id": {
                    "$in":
                        list(
                            warehouse_ids
                        )
                }
            })
        )

        warehouse_map = {

            warehouse["_id"]:
                warehouse

            for warehouse in warehouses
        }

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
    # PACKAGING
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

        warehouse_id_value = (
            row_id.get(
                "warehouse_id"
            )
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

        warehouse = warehouse_map.get(
            warehouse_id_value,
            {}
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

            "warehouse_id":
                str(
                    warehouse_id_value
                )
                if warehouse_id_value
                else None,

            "warehouse_name":
                warehouse.get(
                    "name"
                ),

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

            "variant_qty":
                        variant.get(
                            "quantity"
                        ),

            "sku":
                variant.get(
                    "sku"
                ),

            "unit":
                unit,

            "package":
                package,

            "warehouse_in_quantity":
                row.get(
                    "warehouse_in_quantity",
                    0
                ),

            "warehouse_out_quantity":
                row.get(
                    "warehouse_out_quantity",
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
                        "warehouse_name"
                    )
                    or ""
                ).lower()

                or

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
# MAIN INVENTORY TYPES
# =========================================================
#
# Purchase        -> ADD
# Sale            -> SUBTRACT
# Purchase Return -> SUBTRACT
# Sale Return     -> ADD
#
# Only:
# status = Confirmed
# record_status = active
#
# Grouped by:
# product_id + variant_id
#
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

    skip = (
        page - 1
    ) * limit

    # =====================================================
    # BASE QUERY
    # =====================================================

    query = {
    "record_status": "active",
    "type": {
        "$in": MAIN_INVENTORY_TYPES
    },
    "$or": [
        {
            "type": "sale",
            "status": "Delivered"
        },
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

UNBLOCKED_INVENTORY_TYPES = [
    "sale",
    "sale_return",
    "Warehouse_IN",
]

BLOCKING_SALE_STATUSES = [
    "Pending",
    # "Confirmed",
    "Ready to Pick-up",
    "Out for Delivery",
]


# =========================================================
# GET UNBLOCKED STOCK
#
# GET /orders/inventory/v1/unblocked
#
# Formula:
#
# Available Stock
# = Sale Return Completed
# + Warehouse_IN Completed
# - Sale Delivered
#
# Blocked Stock
# = Pending Sale
# + Ready to Pick-up Sale
# + Out for Delivery Sale
#
# Unblocked Stock
# = Available Stock - Blocked Stock
# =========================================================


@router.get("/inventory/unblocked")
def get_unblocked_stock(
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

    skip = (page - 1) * limit


    # =====================================================
    # PRODUCT FILTER
    # =====================================================

    item_filter = {}

    if product_id:

        item_filter["items.product_id"] = validate_object_id(
            product_id,
            "product_id"
        )

    if variant_id:

        item_filter["items.variant_id"] = validate_object_id(
            variant_id,
            "variant_id"
        )


    # =====================================================
    # MATCH INVENTORY TRANSACTIONS
    #
    # ONLY:
    # sale
    # sale_return
    # Warehouse_IN
    # =====================================================

    inventory_query = {

        "record_status": "active",

        "type": {
            "$in": UNBLOCKED_INVENTORY_TYPES
        },

        "$or": [

            # ---------------------------------------------
            # SALE DELIVERED
            # ---------------------------------------------

            {
                "type": "sale",

                "status": "Delivered"
            },

            # ---------------------------------------------
            # SALE RETURN COMPLETED
            # ---------------------------------------------

            {
                "type": "sale_return",

                "status": "Completed"
            },

            # ---------------------------------------------
            # WAREHOUSE IN COMPLETED
            # ---------------------------------------------

            {
                "type": "Warehouse_IN",

                "status": "Completed"
            }
        ]
    }


    # =====================================================
    # MATCH BLOCKED SALES
    #
    # ONLY sale orders with:
    #
    # Pending
    # Ready to Pick-up
    # Out for Delivery
    #
    # Confirmed is NOT included.
    # =====================================================

    blocked_query = {

        "record_status": "active",

        "type": "sale",

        "status": {
            "$in": BLOCKING_SALE_STATUSES
        }
    }


    # =====================================================
    # AGGREGATION
    # =====================================================

    pipeline = [

        # =================================================
        # GET BOTH AVAILABLE + BLOCKED STOCK
        # =================================================

        {
            "$facet": {

                # =========================================
                # AVAILABLE STOCK
                # =========================================

                "available": [

                    {
                        "$match": inventory_query
                    },

                    {
                        "$unwind": "$items"
                    },

                    {
                        "$match": item_filter
                    },

                    {
                        "$group": {

                            "_id": {

                                "product_id":
                                    "$items.product_id",

                                "variant_id":
                                    "$items.variant_id"
                            },


                            # ---------------------------------
                            # SALE QUANTITY
                            # Delivered sales subtract stock
                            # ---------------------------------

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


                            # ---------------------------------
                            # SALE RETURN QUANTITY
                            # Completed returns add stock
                            # ---------------------------------

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
                            },


                            # ---------------------------------
                            # WAREHOUSE IN QUANTITY
                            # Completed Warehouse_IN adds stock
                            # ---------------------------------

                            "warehouse_in_quantity": {

                                "$sum": {

                                    "$cond": [

                                        {
                                            "$eq": [
                                                "$type",
                                                "Warehouse_IN"
                                            ]
                                        },

                                        "$items.quantity",

                                        0
                                    ]
                                }
                            }
                        }
                    },


                    # =====================================
                    # CALCULATE AVAILABLE STOCK
                    # =====================================

                    {
                        "$addFields": {

                            "available_quantity": {

                                "$add": [

                                    "$sale_return_quantity",

                                    "$warehouse_in_quantity",

                                    {
                                        "$multiply": [

                                            "$sale_quantity",

                                            -1
                                        ]
                                    }
                                ]
                            }
                        }
                    }
                ],


                # =========================================
                # BLOCKED STOCK
                # =========================================

                "blocked": [

                    {
                        "$match": blocked_query
                    },

                    {
                        "$unwind": "$items"
                    },

                    {
                        "$match": item_filter
                    },

                    {
                        "$group": {

                            "_id": {

                                "product_id":
                                    "$items.product_id",

                                "variant_id":
                                    "$items.variant_id"
                            },

                            "blocked_quantity": {

                                "$sum":
                                    "$items.quantity"
                            }
                        }
                    }
                ]
            }
        },


        # =================================================
        # PROJECT
        # =================================================

        {
            "$project": {

                "available": 1,

                "blocked": 1
            }
        }
    ]


    # =====================================================
    # EXECUTE AGGREGATION
    # =====================================================

    result = list(
        orders_collection.aggregate(
            pipeline
        )
    )


    # =====================================================
    # EMPTY RESULT
    # =====================================================

    if not result:

        return {

            "success": True,

            "data": [],

            "pagination": {

                "page": page,

                "limit": limit,

                "total": 0,

                "total_pages": 0
            }
        }


    result = result[0]


    # =====================================================
    # CREATE AVAILABLE MAP
    # =====================================================

    available_map = {

        (
            row["_id"]["product_id"],
            row["_id"]["variant_id"]
        ): row

        for row in result.get(
            "available",
            []
        )
    }


    # =====================================================
    # CREATE BLOCKED MAP
    # =====================================================

    blocked_map = {

        (
            row["_id"]["product_id"],
            row["_id"]["variant_id"]
        ): row.get(
            "blocked_quantity",
            0
        )

        for row in result.get(
            "blocked",
            []
        )
    }


    # =====================================================
    # COMBINE PRODUCT + VARIANT KEYS
    # =====================================================

    keys = (
        set(available_map.keys())
        |
        set(blocked_map.keys())
    )


    rows = []


    # =====================================================
    # CALCULATE UNBLOCKED STOCK
    # =====================================================

    for key in keys:

        product_id_value = key[0]

        variant_id_value = key[1]


        available_row = available_map.get(
            key,
            {}
        )


        available_quantity = available_row.get(
            "available_quantity",
            0
        )


        blocked_quantity = blocked_map.get(
            key,
            0
        )


        # =================================================
        # UNBLOCKED STOCK
        # =================================================

        unblocked_quantity = (
            available_quantity
            -
            blocked_quantity
        )


        # Never expose negative sellable stock

        unblocked_quantity = max(
            unblocked_quantity,
            0
        )


        rows.append({

            "product_id":

                str(product_id_value)

                if product_id_value

                else None,


            "variant_id":

                str(variant_id_value)

                if variant_id_value

                else None,


            "available_quantity":

                available_quantity,


            "blocked_quantity":

                blocked_quantity,


            "unblocked_quantity":

                unblocked_quantity
        })


    # =====================================================
    # COLLECT PRODUCT IDS
    # =====================================================

    product_ids = {

        row["product_id"]

        for row in rows

        if row["product_id"]
    }


    # =====================================================
    # COLLECT VARIANT IDS
    # =====================================================

    variant_ids = {

        row["variant_id"]

        for row in rows

        if row["variant_id"]
    }


    # =====================================================
    # CONVERT TO OBJECT IDS
    # =====================================================

    product_object_ids = [

        ObjectId(pid)

        for pid in product_ids
    ]


    variant_object_ids = [

        ObjectId(vid)

        for vid in variant_ids
    ]


    # =====================================================
    # PRODUCT MAP
    # =====================================================

    product_map = {}


    if product_object_ids:

        products = list(
            products_collection.find({

                "_id": {
                    "$in": product_object_ids
                }
            })
        )


        product_map = {

            str(product["_id"]):
                product

            for product in products
        }


    # =====================================================
    # VARIANT MAP
    # =====================================================

    variant_map = {}


    if variant_object_ids:

        variants = list(
            product_variants_collection.find({

                "_id": {
                    "$in": variant_object_ids
                }
            })
        )


        variant_map = {

            str(variant["_id"]):
                variant

            for variant in variants
        }


    # =====================================================
    # BUILD RESPONSE
    # =====================================================

    data = []


    for row in rows:

        product = product_map.get(
            row["product_id"],
            {}
        )


        variant = variant_map.get(
            row["variant_id"],
            {}
        )


        data.append({

            "product_id":
                row["product_id"],


            "product_name":
                product.get(
                    "name"
                ),


            "variant_id":
                row["variant_id"],


            "variant_name":
                variant.get(
                    "name"
                ),


            "sku":
                variant.get(
                    "sku"
                ),


            "available_quantity":
                row[
                    "available_quantity"
                ],


            "blocked_quantity":
                row[
                    "blocked_quantity"
                ],


            "unblocked_quantity":
                row[
                    "unblocked_quantity"
                ]
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
    # SORT
    # =====================================================

    data.sort(

        key=lambda x: (

            x.get(
                "product_name"
            )
            or "",


            x.get(
                "variant_name"
            )
            or ""
        )
    )


    # =====================================================
    # PAGINATION
    # =====================================================

    total = len(data)


    total_pages = (

        (total + limit - 1)

        //
        limit
    )


    data = data[
        skip:
        skip + limit
    ]


    # =====================================================
    # RESPONSE
    # =====================================================

    return {

        "success": True,

        "data": data,

        "pagination": {

            "page": page,

            "limit": limit,

            "total": total,

            "total_pages":
                total_pages
        }
    }

# =========================================================
# VEHICLE INVENTORY
# =========================================================
#
# Calculation:
#
# Vehicle_IN  -> ADD
# Vehicle_OUT -> SUBTRACT
#
# Only:
# status = Completed
# record_status = active
#
# Grouped by:
# vehicle_id + product_id + variant_id
#
# Vehicle Inventory =
# Vehicle_IN - Vehicle_OUT
#
# =========================================================


VEHICLE_INVENTORY_TYPES = [
    "Vehicle_IN",
    "Vehicle_OUT"
]


# =========================================================
# GET VEHICLE INVENTORY
#
# GET /inventory/vehicle-inventory
# =========================================================


@router.get(
    "/vehicle-inventory",
    tags=["Inventory"]
)
def get_vehicle_inventory(

    page: int = Query(
        1,
        ge=1
    ),

    limit: int = Query(
        20,
        ge=1,
        le=100
    ),

    vehicle_id: Optional[str] = None,

    product_id: Optional[str] = None,

    variant_id: Optional[str] = None,

    search: Optional[str] = None
):

    skip = (
        page - 1
    ) * limit


    # =====================================================
    # BASE QUERY
    # =====================================================

    query = {

        "type": {
            "$in":
                VEHICLE_INVENTORY_TYPES
        },

        "status":
            "Completed",

        "record_status":
            "active",

        "vehicle_id": {
            "$exists":
                True,

            "$ne":
                None
        }
    }


    # =====================================================
    # VEHICLE FILTER
    # =====================================================

    if vehicle_id:

        query[
            "vehicle_id"
        ] = validate_object_id(
            vehicle_id,
            "vehicle_id"
        )


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
        # FILTER VEHICLE TRANSACTIONS
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
        # APPLY PRODUCT / VARIANT FILTER
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
        # GROUP
        #
        # vehicle + product + variant
        # -------------------------------------------------

        {
            "$group": {

                "_id": {

                    "vehicle_id":
                        "$vehicle_id",

                    "product_id":
                        "$items.product_id",

                    "variant_id":
                        "$items.variant_id"
                },


                # -----------------------------------------
                # VEHICLE IN
                # -----------------------------------------

                "vehicle_in_quantity": {

                    "$sum": {

                        "$cond": [

                            {
                                "$eq": [
                                    "$type",
                                    "Vehicle_IN"
                                ]
                            },

                            "$items.quantity",

                            0
                        ]
                    }
                },


                # -----------------------------------------
                # VEHICLE OUT
                # -----------------------------------------

                "vehicle_out_quantity": {

                    "$sum": {

                        "$cond": [

                            {
                                "$eq": [
                                    "$type",
                                    "Vehicle_OUT"
                                ]
                            },

                            "$items.quantity",

                            0
                        ]
                    }
                }
            }
        },


        # -------------------------------------------------
        # CALCULATE AVAILABLE VEHICLE INVENTORY
        # -------------------------------------------------

        {
            "$addFields": {

                "available_quantity": {

                    "$subtract": [

                        "$vehicle_in_quantity",

                        "$vehicle_out_quantity"
                    ]
                }
            }
        },


        # -------------------------------------------------
        # SORT
        # -------------------------------------------------

        {
            "$sort": {

                "_id.vehicle_id": 1,

                "_id.product_id": 1,

                "_id.variant_id": 1
            }
        }
    ]


    # =====================================================
    # EXECUTE AGGREGATION
    # =====================================================

    inventory_rows = list(
        orders_collection.aggregate(
            pipeline
        )
    )


    # =====================================================
    # COLLECT IDS
    # =====================================================

    vehicle_ids = set()

    product_ids = set()

    variant_ids = set()


    for row in inventory_rows:

        row_id = row.get(
            "_id",
            {}
        )


        if row_id.get(
            "vehicle_id"
        ):

            vehicle_ids.add(
                row_id[
                    "vehicle_id"
                ]
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
    # VEHICLE MAP
    # =====================================================

    vehicle_map = {}


    if vehicle_ids:

        vehicles = list(
            vehicles_collection.find({

                "_id": {
                    "$in":
                        list(
                            vehicle_ids
                        )
                }
            })
        )


        vehicle_map = {

            vehicle["_id"]:
                vehicle

            for vehicle in vehicles
        }


    # =====================================================
    # PRODUCT MAP
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
    # VARIANT MAP
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
    # UNIT MAP
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
    # PACKAGING MAP
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


        vehicle_id_value = row_id.get(
            "vehicle_id"
        )

        product_id_value = row_id.get(
            "product_id"
        )

        variant_id_value = row_id.get(
            "variant_id"
        )


        vehicle = vehicle_map.get(
            vehicle_id_value,
            {}
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

        packaging_type_id = variant.get(
            "packaging_type_id"
        )


        if packaging_type_id:

            package_data = packaging_map.get(
                packaging_type_id,
                {}
            )


            package = (
                package_data.get(
                    "name"
                )
                or ""
            )


        # =================================================
        # VEHICLE DETAILS
        # =================================================

        vehicle_data = {

            "id":
                str(
                    vehicle_id_value
                )
                if vehicle_id_value
                else None,

            "vehicle_number":
                vehicle.get(
                    "vehicle_number"
                ),

            "model":
                vehicle.get(
                    "model"
                ),

            "vehicle_type":
                vehicle.get(
                    "vehicle_type"
                )
        }


        # =================================================
        # RESPONSE
        # =================================================

        data.append({

            "vehicle":
                vehicle_data,


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


            "variant_qty":
                variant.get(
                    "quantity"
                ),


            "sku":
                variant.get(
                    "sku"
                ),


            "unit":
                unit,


            "package":
                package,


            "vehicle_in_quantity":
                row.get(
                    "vehicle_in_quantity",
                    0
                ),


            "vehicle_out_quantity":
                row.get(
                    "vehicle_out_quantity",
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
                        "vehicle",
                        {}
                    ).get(
                        "vehicle_number"
                    )
                    or ""
                ).lower()


                or


                search_lower
                in str(
                    item.get(
                        "vehicle",
                        {}
                    ).get(
                        "model"
                    )
                    or ""
                ).lower()


                or


                search_lower
                in str(
                    item.get(
                        "vehicle",
                        {}
                    ).get(
                        "vehicle_type"
                    )
                    or ""
                ).lower()


                or


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
    # SORT
    # =====================================================

    data.sort(

        key=lambda x: (

            x.get(
                "vehicle",
                {}
            ).get(
                "vehicle_number"
            )
            or "",


            x.get(
                "product_name"
            )
            or "",


            x.get(
                "variant_name"
            )
            or ""
        )
    )


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
        //
        limit
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