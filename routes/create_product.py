from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from bson import ObjectId

from database import (
    products_collection,
    product_variants_collection,
    inventory_collection,
    product_units_collection,
    packing_types_collection
)

router = APIRouter()


# =========================================================
# HELPER
# =========================================================

def validate_object_id(value: str, field_name: str):
    if not ObjectId.is_valid(value):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {field_name}"
        )

    return ObjectId(value)


# =========================================================
# MODELS
# =========================================================

class ProductCreate(BaseModel):
    name: str

    category_id: Optional[str] = None
    subcategory_id: Optional[str] = None
    brand_id: Optional[str] = None

    description: Optional[str] = None

    # HSN Code
    hsn_code: Optional[str] = None

    # Base unit
    base_unit: str


class ProductVariantCreate(BaseModel):
    name: str

    # Packaging Type
    packaging_type_id: str

    # Quantity
    quantity: float = Field(gt=0)

    # Unit
    unit_id: str

    # SKU
    sku: str

    # Prices
    selling_price: float = Field(ge=0)
    purchase_price: float = Field(ge=0)

    # GST
    gst_percent: float = Field(
        ge=0,
        le=100
    )


# =========================================================
# CREATE PRODUCT
# POST /products/v1
# =========================================================

@router.post("/products/v1")
def create_product(data: ProductCreate):

    # -----------------------------------------
    # Duplicate product check
    # -----------------------------------------

    existing = products_collection.find_one({
        "name": {
            "$regex": f"^{data.name}$",
            "$options": "i"
        }
    })

    if existing:
        raise HTTPException(
            status_code=400,
            detail="Product already exists"
        )

    # -----------------------------------------
    # Create product
    # -----------------------------------------

    now = datetime.utcnow()

    product = {
        "name": data.name,

        "category_id": data.category_id,
        "subcategory_id": data.subcategory_id,
        "brand_id": data.brand_id,

        "description": data.description,

        "hsn_code": data.hsn_code,

        "base_unit": data.base_unit,

        "status": "active",

        "created_at": now,
        "updated_at": now
    }

    result = products_collection.insert_one(product)

    return {
        "success": True,
        "message": "Product created successfully",
        "product_id": str(result.inserted_id)
    }


# =========================================================
# CREATE PRODUCT VARIANT
# POST /products/{product_id}/variants
# =========================================================

@router.post("/products/{product_id}/variants")
def create_product_variant(
    product_id: str,
    data: ProductVariantCreate
):

    # -----------------------------------------
    # Validate product ID
    # -----------------------------------------

    product_object_id = validate_object_id(
        product_id,
        "product_id"
    )

    # -----------------------------------------
    # Check product
    # -----------------------------------------

    product = products_collection.find_one({
        "_id": product_object_id
    })

    if not product:
        raise HTTPException(
            status_code=404,
            detail="Product not found"
        )

    # -----------------------------------------
    # Validate packaging type ID
    # -----------------------------------------

    packaging_type_object_id = validate_object_id(
        data.packaging_type_id,
        "packaging_type_id"
    )

    packaging_type = packing_types_collection.find_one({
        "_id": packaging_type_object_id
    })

    if not packaging_type:
        raise HTTPException(
            status_code=404,
            detail="Packaging type not found"
        )

    # -----------------------------------------
    # Validate unit ID
    # -----------------------------------------

    unit_object_id = validate_object_id(
        data.unit_id,
        "unit_id"
    )

    unit = product_units_collection.find_one({
        "_id": unit_object_id
    })

    if not unit:
        raise HTTPException(
            status_code=404,
            detail="Unit not found"
        )

    # -----------------------------------------
    # Duplicate SKU check
    # -----------------------------------------

    existing = product_variants_collection.find_one({
        "sku": data.sku
    })

    if existing:
        raise HTTPException(
            status_code=400,
            detail="SKU already exists"
        )

    # -----------------------------------------
    # Create variant
    # -----------------------------------------

    now = datetime.utcnow()

    variant = {
        "product_id": product_object_id,

        "name": data.name,

        "packaging_type_id": packaging_type_object_id,

        "quantity": data.quantity,

        "unit_id": unit_object_id,

        "sku": data.sku,

        "selling_price": data.selling_price,

        "purchase_price": data.purchase_price,

        "gst_percent": data.gst_percent,

        "status": "active",

        "created_at": now,
        "updated_at": now
    }

    result = product_variants_collection.insert_one(
        variant
    )

    # -----------------------------------------
    # Initial inventory
    # -----------------------------------------

    inventory = {
        "product_id": product_object_id,

        "variant_id": result.inserted_id,

        "sku": data.sku,

        "stock_quantity": 0,

        "base_quantity": 0,

        "unit_id": unit_object_id,

        "created_at": now,
        "updated_at": now
    }

    inventory_collection.insert_one(inventory)

    return {
        "success": True,
        "message": "Product variant created successfully",
        "variant_id": str(result.inserted_id)
    }


# =========================================================
# GET PRODUCT LIST
# GET /products/v1
# =========================================================

@router.get("/products/v1")
def get_products(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),

    search: Optional[str] = None,

    category_id: Optional[str] = None,
    subcategory_id: Optional[str] = None,
    brand_id: Optional[str] = None,

    status: Optional[str] = "active"
):

    skip = (page - 1) * limit

    query = {}

    # -----------------------------------------
    # Status
    # -----------------------------------------

    if status:
        query["status"] = status

    # -----------------------------------------
    # Search
    # -----------------------------------------

    if search:
        query["name"] = {
            "$regex": search,
            "$options": "i"
        }

    # -----------------------------------------
    # Category
    # -----------------------------------------

    if category_id:
        query["category_id"] = category_id

    # -----------------------------------------
    # Subcategory
    # -----------------------------------------

    if subcategory_id:
        query["subcategory_id"] = subcategory_id

    # -----------------------------------------
    # Brand
    # -----------------------------------------

    if brand_id:
        query["brand_id"] = brand_id

    # -----------------------------------------
    # Count
    # -----------------------------------------

    total = products_collection.count_documents(query)

    # -----------------------------------------
    # Get products
    # -----------------------------------------

    products = list(
        products_collection
        .find(query)
        .sort("created_at", -1)
        .skip(skip)
        .limit(limit)
    )

    data = []

    for product in products:

        data.append({
            "id": str(product["_id"]),

            "name": product.get("name"),

            "category_id": product.get(
                "category_id"
            ),

            "subcategory_id": product.get(
                "subcategory_id"
            ),

            "brand_id": product.get(
                "brand_id"
            ),

            "description": product.get(
                "description"
            ),

            "hsn_code": product.get(
                "hsn_code"
            ),

            "base_unit": product.get(
                "base_unit"
            ),

            "status": product.get(
                "status"
            ),

            "created_at": product.get(
                "created_at"
            ),

            "updated_at": product.get(
                "updated_at"
            )
        })

    return {
        "success": True,

        "data": data,

        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "total_pages": (
                (total + limit - 1) // limit
            )
        }
    }


# =========================================================
# GET PRODUCT VARIANTS
# GET /products/{product_id}/variants
# =========================================================

@router.get("/products/{product_id}/variants")
def get_product_variants(
    product_id: str,

    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),

    search: Optional[str] = None,

    status: Optional[str] = "active"
):

    # -----------------------------------------
    # Validate product ID
    # -----------------------------------------

    product_object_id = validate_object_id(
        product_id,
        "product_id"
    )

    # -----------------------------------------
    # Check product
    # -----------------------------------------

    product = products_collection.find_one({
        "_id": product_object_id
    })

    if not product:
        raise HTTPException(
            status_code=404,
            detail="Product not found"
        )

    # -----------------------------------------
    # Pagination
    # -----------------------------------------

    skip = (page - 1) * limit

    # -----------------------------------------
    # Query
    # -----------------------------------------

    query = {
        "product_id": product_object_id
    }

    if status:
        query["status"] = status

    # -----------------------------------------
    # Search
    # -----------------------------------------

    if search:

        query["$or"] = [

            {
                "name": {
                    "$regex": search,
                    "$options": "i"
                }
            },

            {
                "sku": {
                    "$regex": search,
                    "$options": "i"
                }
            }
        ]

    # -----------------------------------------
    # Count
    # -----------------------------------------

    total = product_variants_collection.count_documents(
        query
    )

    # -----------------------------------------
    # Get variants
    # -----------------------------------------

    variants = list(
        product_variants_collection
        .find(query)
        .sort("created_at", -1)
        .skip(skip)
        .limit(limit)
    )

    data = []

    for variant in variants:

        # =====================================
        # INVENTORY
        # =====================================

        inventory = inventory_collection.find_one({
            "variant_id": variant["_id"]
        })

        # =====================================
        # UNIT
        # =====================================

        unit_data = None

        unit_id = variant.get("unit_id")

        if unit_id:

            unit = product_units_collection.find_one({
                "_id": unit_id
            })

            if unit:

                unit_data = {
                    "id": str(unit["_id"]),

                    "name": unit.get(
                        "name"
                    ),

                    "symbol": unit.get(
                        "symbol"
                    ),

                    "short_name": unit.get(
                        "short_name"
                    ),

                    "status": unit.get(
                        "status"
                    )
                }

        # =====================================
        # PACKAGING TYPE
        # =====================================

        packaging_data = None

        packaging_type_id = variant.get(
            "packaging_type_id"
        )

        if packaging_type_id:

            packaging = packing_types_collection.find_one({
                "_id": packaging_type_id
            })

            if packaging:

                packaging_data = {
                    "id": str(
                        packaging["_id"]
                    ),

                    "name": packaging.get(
                        "name"
                    ),

                    "status": packaging.get(
                        "status"
                    )
                }

        # =====================================
        # RESPONSE
        # =====================================

        data.append({

            "id": str(
                variant["_id"]
            ),

            "product_id": str(
                variant["product_id"]
            ),

            "name": variant.get(
                "name"
            ),

            "packaging_type": packaging_data,

            "quantity": variant.get(
                "quantity"
            ),

            "unit": unit_data,

            "sku": variant.get(
                "sku"
            ),

            "selling_price": variant.get(
                "selling_price"
            ),

            "purchase_price": variant.get(
                "purchase_price"
            ),

            "gst_percent": variant.get(
                "gst_percent",
                0
            ),

            "status": variant.get(
                "status"
            ),

            # =================================
            # INVENTORY
            # =================================

            "stock_quantity": (
                inventory.get(
                    "stock_quantity",
                    0
                )
                if inventory
                else 0
            ),

            "base_quantity": (
                inventory.get(
                    "base_quantity",
                    0
                )
                if inventory
                else 0
            ),

            "base_unit": product.get(
                "base_unit"
            ),

            "created_at": variant.get(
                "created_at"
            ),

            "updated_at": variant.get(
                "updated_at"
            )
        })

    # =========================================
    # RESPONSE
    # =========================================

    return {

        "success": True,

        "product": {

            "id": str(
                product["_id"]
            ),

            "name": product.get(
                "name"
            ),

            "base_unit": product.get(
                "base_unit"
            )
        },

        "data": data,

        "pagination": {

            "page": page,

            "limit": limit,

            "total": total,

            "total_pages": (
                (total + limit - 1)
                // limit
            )
        }
    }


# =========================================================
# UPDATE PRODUCT
# PUT /products/{product_id}
# =========================================================

@router.post("/products/{product_id}")
def update_product(
    product_id: str,
    data: ProductCreate
):

    product_object_id = validate_object_id(
        product_id,
        "product_id"
    )

    # -----------------------------------------
    # Check product
    # -----------------------------------------

    existing_product = products_collection.find_one({
        "_id": product_object_id
    })

    if not existing_product:

        raise HTTPException(
            status_code=404,
            detail="Product not found"
        )

    # -----------------------------------------
    # Duplicate name
    # -----------------------------------------

    duplicate = products_collection.find_one({

        "_id": {
            "$ne": product_object_id
        },

        "name": {
            "$regex": f"^{data.name}$",
            "$options": "i"
        }
    })

    if duplicate:

        raise HTTPException(
            status_code=400,
            detail="Product already exists"
        )

    # -----------------------------------------
    # Update
    # -----------------------------------------

    update_data = {

        "name": data.name,

        "category_id": data.category_id,

        "subcategory_id": data.subcategory_id,

        "brand_id": data.brand_id,

        "description": data.description,

        "hsn_code": data.hsn_code,

        "base_unit": data.base_unit,

        "updated_at": datetime.utcnow()
    }

    products_collection.update_one(

        {
            "_id": product_object_id
        },

        {
            "$set": update_data
        }
    )

    # -----------------------------------------
    # Update inventory base unit
    # -----------------------------------------

    inventory_collection.update_many(

        {
            "product_id": product_object_id
        },

        {
            "$set": {

                "base_unit": data.base_unit,

                "updated_at": datetime.utcnow()
            }
        }
    )

    return {

        "success": True,

        "message": "Product updated successfully",

        "product_id": product_id
    }


# =========================================================
# UPDATE PRODUCT VARIANT
# PUT /products/{product_id}/variants/{variant_id}
# =========================================================

@router.post(
    "/products/{product_id}/variants/{variant_id}"
)
def update_product_variant(

    product_id: str,

    variant_id: str,

    data: ProductVariantCreate
):

    # -----------------------------------------
    # Validate IDs
    # -----------------------------------------

    product_object_id = validate_object_id(
        product_id,
        "product_id"
    )

    variant_object_id = validate_object_id(
        variant_id,
        "variant_id"
    )

    # -----------------------------------------
    # Check product
    # -----------------------------------------

    product = products_collection.find_one({
        "_id": product_object_id
    })

    if not product:

        raise HTTPException(
            status_code=404,
            detail="Product not found"
        )

    # -----------------------------------------
    # Check variant
    # -----------------------------------------

    existing_variant = product_variants_collection.find_one({

        "_id": variant_object_id,

        "product_id": product_object_id
    })

    if not existing_variant:

        raise HTTPException(
            status_code=404,
            detail="Product variant not found"
        )

    # -----------------------------------------
    # Validate packaging
    # -----------------------------------------

    packaging_type_object_id = validate_object_id(
        data.packaging_type_id,
        "packaging_type_id"
    )

    packaging = packing_types_collection.find_one({

        "_id": packaging_type_object_id
    })

    if not packaging:

        raise HTTPException(
            status_code=404,
            detail="Packaging type not found"
        )

    # -----------------------------------------
    # Validate unit
    # -----------------------------------------

    unit_object_id = validate_object_id(
        data.unit_id,
        "unit_id"
    )

    unit = product_units_collection.find_one({

        "_id": unit_object_id
    })

    if not unit:

        raise HTTPException(
            status_code=404,
            detail="Unit not found"
        )

    # -----------------------------------------
    # Duplicate SKU
    # -----------------------------------------

    duplicate_sku = product_variants_collection.find_one({

        "_id": {
            "$ne": variant_object_id
        },

        "sku": data.sku
    })

    if duplicate_sku:

        raise HTTPException(
            status_code=400,
            detail="SKU already exists"
        )

    # -----------------------------------------
    # Update variant
    # -----------------------------------------

    update_data = {

        "name": data.name,

        "packaging_type_id":
            packaging_type_object_id,

        "quantity": data.quantity,

        "unit_id":
            unit_object_id,

        "sku": data.sku,

        "selling_price":
            data.selling_price,

        "purchase_price":
            data.purchase_price,

        "gst_percent":
            data.gst_percent,

        "updated_at":
            datetime.utcnow()
    }

    product_variants_collection.update_one(

        {
            "_id": variant_object_id,

            "product_id": product_object_id
        },

        {
            "$set": update_data
        }
    )

    # -----------------------------------------
    # Update inventory
    # -----------------------------------------

    inventory_collection.update_one(

        {
            "variant_id": variant_object_id
        },

        {
            "$set": {

                "sku": data.sku,

                "unit_id":
                    unit_object_id,

                "updated_at":
                    datetime.utcnow()
            }
        }
    )

    return {

        "success": True,

        "message":
            "Product variant updated successfully",

        "variant_id":
            variant_id
    }