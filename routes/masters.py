from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime
from bson import ObjectId

from database import masters_collection

router = APIRouter()


# ==============================
# Master Model
# ==============================
class Master(BaseModel):
    master_type: str
    name: str
    level: int | None = None


# ==============================
# CREATE MASTER
# ==============================
@router.post("/v1")
def create_master(master: Master):

    existing = masters_collection.find_one({
        "master_type": master.master_type,
        "name": master.name
    })

    if existing:
        raise HTTPException(
            status_code=400,
            detail="Master already exists"
        )

    data = master.model_dump()
    data["created_at"] = datetime.utcnow()
    data["updated_at"] = datetime.utcnow()

    result = masters_collection.insert_one(data)

    return {
        "status": True,
        "message": "Master Created Successfully",
        "master_id": str(result.inserted_id)
    }


# ==============================
# UPDATE MASTER
# ==============================
@router.post("/v1/{master_id}")
def update_master(master_id: str, master: Master):

    # Validate ObjectId
    if not ObjectId.is_valid(master_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid master_id"
        )

    object_id = ObjectId(master_id)

    # Check master exists
    existing = masters_collection.find_one({
        "_id": object_id
    })

    if not existing:
        raise HTTPException(
            status_code=404,
            detail="Master not found"
        )

    # Check duplicate name
    duplicate = masters_collection.find_one({
        "master_type": master.master_type,
        "name": master.name,
        "_id": {"$ne": object_id}
    })

    if duplicate:
        raise HTTPException(
            status_code=400,
            detail="Master with this name already exists"
        )

    update_data = {
        "master_type": master.master_type,
        "name": master.name,
        "level": master.level,
        "updated_at": datetime.utcnow()
    }

    result = masters_collection.update_one(
        {"_id": object_id},
        {"$set": update_data}
    )

    if result.modified_count == 0:
        return {
            "status": True,
            "message": "No changes made"
        }

    return {
        "status": True,
        "message": "Master Updated Successfully",
        "master_id": master_id
    }


# ==============================
# GET ALL MASTERS
# ==============================
@router.get("/v1/{master_type}")
def get_masters_by_type(master_type: str):

    masters = list(
        masters_collection.find({
            "master_type": master_type
        }).sort("created_at", -1)
    )

    data = []

    for master in masters:
        data.append({
            "id": str(master["_id"]),
            "master_type": master.get("master_type"),
            "name": master.get("name"),
            "level": master.get("level"),
            "created_at": master.get("created_at"),
            "updated_at": master.get("updated_at")
        })

    return {
        "status": True,
        "count": len(data),
        "data": data
    }

