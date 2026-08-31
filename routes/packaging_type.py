from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from bson import ObjectId

from database import packing_types_collection

router = APIRouter()


# =========================================
# MODEL
# =========================================

class PackingTypeCreate(BaseModel):
    name: str
    description: Optional[str] = None


# =========================================
# CREATE PACKING TYPE
# POST /packing-types/v1
# =========================================

@router.post("/create/v1")
def create_packing_type(data: PackingTypeCreate):

    name = data.name.strip()

    if not name:
        raise HTTPException(
            status_code=400,
            detail="Packing type name is required"
        )

    # Duplicate check
    existing = packing_types_collection.find_one({
        "name": {
            "$regex": f"^{name}$",
            "$options": "i"
        }
    })

    if existing:
        raise HTTPException(
            status_code=400,
            detail="Packing type already exists"
        )

    packing_type = {
        "name": name,
        "description": data.description,
        "status": "active",
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }

    result = packing_types_collection.insert_one(packing_type)

    return {
        "success": True,
        "message": "Packing type created successfully",
        "packing_type_id": str(result.inserted_id)
    }


# =========================================
# GET PACKING TYPES
# GET /packing-types/v1
# =========================================

@router.get("/get/v1")
def get_packing_types(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    status: Optional[str] = "active"
):

    skip = (page - 1) * limit

    query = {}

    # Status filter
    if status:
        query["status"] = status

    # Search
    if search:
        query["name"] = {
            "$regex": search,
            "$options": "i"
        }

    total = packing_types_collection.count_documents(query)

    packing_types = list(
        packing_types_collection
        .find(query)
        .sort("created_at", -1)
        .skip(skip)
        .limit(limit)
    )

    data = []

    for item in packing_types:

        data.append({
            "id": str(item["_id"]),
            "name": item.get("name"),
            "description": item.get("description"),
            "status": item.get("status"),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at")
        })

    return {
        "success": True,
        "data": data,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "total_pages": (total + limit - 1) // limit
        }
    }


# =========================================
# DELETE PACKING TYPE
# DELETE /packing-types/v1/{packing_type_id}
# =========================================

@router.post("/delete/v1/{packing_type_id}")
def delete_packing_type(packing_type_id: str):

    if not ObjectId.is_valid(packing_type_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid packing_type_id"
        )

    packing_type_object_id = ObjectId(packing_type_id)

    existing = packing_types_collection.find_one({
        "_id": packing_type_object_id
    })

    if not existing:
        raise HTTPException(
            status_code=404,
            detail="Packing type not found"
        )

    # Soft delete
    packing_types_collection.update_one(
        {
            "_id": packing_type_object_id
        },
        {
            "$set": {
                "status": "deleted",
                "updated_at": datetime.utcnow()
            }
        }
    )

    return {
        "success": True,
        "message": "Packing type deleted successfully",
        "packing_type_id": packing_type_id
    }


# =========================================
# UPDATE PACKING TYPE
# PUT /packing-types/v1/{packing_type_id}
# =========================================

@router.post("/update/v1/{packing_type_id}")
def update_packing_type(
    packing_type_id: str,
    data: PackingTypeCreate
):

    # Validate ObjectId
    if not ObjectId.is_valid(packing_type_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid packing_type_id"
        )

    packing_type_object_id = ObjectId(packing_type_id)

    # Check existing packing type
    existing = packing_types_collection.find_one({
        "_id": packing_type_object_id
    })

    if not existing:
        raise HTTPException(
            status_code=404,
            detail="Packing type not found"
        )

    name = data.name.strip()

    if not name:
        raise HTTPException(
            status_code=400,
            detail="Packing type name is required"
        )

    # Check duplicate name
    duplicate = packing_types_collection.find_one({
        "_id": {
            "$ne": packing_type_object_id
        },
        "name": {
            "$regex": f"^{name}$",
            "$options": "i"
        },
        "status": {
            "$ne": "deleted"
        }
    })

    if duplicate:
        raise HTTPException(
            status_code=400,
            detail="Packing type already exists"
        )

    # Update
    update_data = {
        "name": name,
        "description": data.description,
        "updated_at": datetime.utcnow()
    }

    packing_types_collection.update_one(
        {
            "_id": packing_type_object_id
        },
        {
            "$set": update_data
        }
    )

    return {
        "success": True,
        "message": "Packing type updated successfully",
        "packing_type_id": packing_type_id
    }