from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from bson import ObjectId

from database import product_units_collection

router = APIRouter()


class ProductUnit(BaseModel):
    name: str
    symbol: str


# =========================================
# CREATE PRODUCT UNIT
# POST /v1
# =========================================

@router.post("/v1")
def create_product_unit(unit: ProductUnit):

    # Check duplicate name
    existing = product_units_collection.find_one({
        "name": unit.name
    })

    if existing:
        raise HTTPException(
            status_code=400,
            detail="Product unit already exists"
        )

    # Check duplicate symbol
    existing_symbol = product_units_collection.find_one({
        "symbol": unit.symbol
    })

    if existing_symbol:
        raise HTTPException(
            status_code=400,
            detail="Product unit symbol already exists"
        )

    data = {
        "name": unit.name,
        "symbol": unit.symbol
    }

    result = product_units_collection.insert_one(data)

    return {
        "status": True,
        "message": "Product Unit Created Successfully",
        "unit_id": str(result.inserted_id)
    }


# =========================================
# UPDATE PRODUCT UNIT
# POST /update/{unit_id}
# =========================================

@router.post("/update/{unit_id}")
def update_product_unit(
    unit_id: str,
    unit: ProductUnit
):

    if not ObjectId.is_valid(unit_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid unit_id"
        )

    object_id = ObjectId(unit_id)

    # Check unit exists
    existing = product_units_collection.find_one({
        "_id": object_id
    })

    if not existing:
        raise HTTPException(
            status_code=404,
            detail="Product unit not found"
        )

    # Check duplicate name
    duplicate_name = product_units_collection.find_one({
        "name": unit.name,
        "_id": {
            "$ne": object_id
        }
    })

    if duplicate_name:
        raise HTTPException(
            status_code=400,
            detail="Product unit with this name already exists"
        )

    # Check duplicate symbol
    duplicate_symbol = product_units_collection.find_one({
        "symbol": unit.symbol,
        "_id": {
            "$ne": object_id
        }
    })

    if duplicate_symbol:
        raise HTTPException(
            status_code=400,
            detail="Product unit symbol already exists"
        )

    # Update
    product_units_collection.update_one(
        {
            "_id": object_id
        },
        {
            "$set": {
                "name": unit.name,
                "symbol": unit.symbol
            }
        }
    )

    return {
        "status": True,
        "message": "Product Unit Updated Successfully",
        "unit_id": unit_id
    }


# =========================================
# DELETE PRODUCT UNIT
# POST /delete/{unit_id}
# =========================================

@router.post("/delete/{unit_id}")
def delete_product_unit(unit_id: str):

    if not ObjectId.is_valid(unit_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid unit_id"
        )

    object_id = ObjectId(unit_id)

    existing = product_units_collection.find_one({
        "_id": object_id
    })

    if not existing:
        raise HTTPException(
            status_code=404,
            detail="Product unit not found"
        )

    result = product_units_collection.delete_one({
        "_id": object_id
    })

    if result.deleted_count == 0:
        raise HTTPException(
            status_code=500,
            detail="Failed to delete product unit"
        )

    return {
        "status": True,
        "message": "Product Unit Deleted Successfully",
        "unit_id": unit_id
    }


# =========================================
# GET PRODUCT UNIT LIST
# GET /v1
# =========================================

@router.get("/v1")
def get_product_units():

    units = list(
        product_units_collection.find().sort("name", 1)
    )

    data = []

    for unit in units:
        data.append({
            "id": str(unit["_id"]),
            "name": unit.get("name"),
            "symbol": unit.get("symbol")
        })

    return {
        "status": True,
        "count": len(data),
        "data": data
    }