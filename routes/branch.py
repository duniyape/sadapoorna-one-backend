from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime
from bson import ObjectId
from database import branches_collection

router = APIRouter()


class Branch(BaseModel):
    name: str
    address: str | None = None
    city: str | None = None
    state: str | None = None
    pincode: str | None = None
    country: str | None = None
    status: str = "active"


@router.post("/v1")
def create_branch(branch: Branch):

    # Check duplicate branch name
    existing = branches_collection.find_one({
        "name": branch.name
    })

    if existing:
        raise HTTPException(
            status_code=400,
            detail="Branch already exists"
        )

    # Get last branch
    last_branch = branches_collection.find_one(
        {},
        sort=[("branch_number", -1)]
    )

    if last_branch and last_branch.get("branch_number"):
        next_number = last_branch["branch_number"] + 1
    else:
        next_number = 1

    # Generate BR001, BR002, BR003...
    branch_code = f"BR{next_number:03d}"

    data = branch.model_dump()

    data["branch_code"] = branch_code
    data["branch_number"] = next_number
    data["created_at"] = datetime.utcnow()
    data["updated_at"] = datetime.utcnow()

    result = branches_collection.insert_one(data)

    return {
        "status": True,
        "message": "Branch Created Successfully",
        "branch_id": str(result.inserted_id),
        "branch_code": branch_code
    }


@router.get("/v1")
def get_branches():

    branches = list(
        branches_collection.find().sort("branch_number", 1)
    )

    data = []

    for branch in branches:
        data.append({
            "id": str(branch["_id"]),
            "branch_code": branch.get("branch_code"),
            "name": branch.get("name"),
            "address": branch.get("address"),
            "city": branch.get("city"),
            "state": branch.get("state"),
            "pincode": branch.get("pincode"),
            "country": branch.get("country"),
            "status": branch.get("status"),
            "created_at": branch.get("created_at"),
            "updated_at": branch.get("updated_at")
        })

    return {
        "status": True,
        "count": len(data),
        "data": data
    }


@router.get("/v1/{branch_id}")
def get_branch(branch_id: str):

    if not ObjectId.is_valid(branch_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid branch_id"
        )

    branch = branches_collection.find_one({
        "_id": ObjectId(branch_id)
    })

    if not branch:
        raise HTTPException(
            status_code=404,
            detail="Branch not found"
        )

    return {
        "status": True,
        "data": {
            "id": str(branch["_id"]),
            "branch_code": branch.get("branch_code"),
            "name": branch.get("name"),
            "address": branch.get("address"),
            "city": branch.get("city"),
            "state": branch.get("state"),
            "pincode": branch.get("pincode"),
            "country": branch.get("country"),
            "status": branch.get("status"),
            "created_at": branch.get("created_at"),
            "updated_at": branch.get("updated_at")
        }
    }

@router.post("/v1/{branch_id}")
def update_branch(branch_id: str, branch: Branch):

    # Validate ObjectId
    if not ObjectId.is_valid(branch_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid branch_id"
        )

    object_id = ObjectId(branch_id)

    # Check branch exists
    existing = branches_collection.find_one({
        "_id": object_id
    })

    if not existing:
        raise HTTPException(
            status_code=404,
            detail="Branch not found"
        )

    # Check duplicate branch name
    duplicate = branches_collection.find_one({
        "name": branch.name,
        "_id": {"$ne": object_id}
    })

    if duplicate:
        raise HTTPException(
            status_code=400,
            detail="Branch with this name already exists"
        )

    # Update data
    update_data = branch.model_dump()

    update_data["updated_at"] = datetime.utcnow()

    result = branches_collection.update_one(
        {"_id": object_id},
        {
            "$set": update_data
        }
    )

    return {
        "status": True,
        "message": "Branch Updated Successfully",
        "branch_id": branch_id,
        "branch_code": existing.get("branch_code")
    }