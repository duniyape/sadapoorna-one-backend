# routes/address.py

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from typing import Optional, List
from bson import ObjectId
from datetime import datetime

from database import db

router = APIRouter(
    prefix="/addresses",
    tags=["Addresses"]
)

address_collection = db["addresses"]


# =========================
# Pydantic Models
# =========================

class AddressCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    address: str = Field(..., min_length=1)
    latitude: float
    longitude: float


class AddressUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    address: Optional[str] = Field(None, min_length=1)
    latitude: Optional[float] = None
    longitude: Optional[float] = None


# =========================
# Helper
# =========================

def serialize_address(address):
    return {
        "id": str(address["_id"]),
        "name": address.get("name"),
        "address": address.get("address"),
        "latitude": address.get("latitude"),
        "longitude": address.get("longitude"),
        "created_at": address.get("created_at"),
        "updated_at": address.get("updated_at")
    }


def validate_object_id(address_id: str):
    if not ObjectId.is_valid(address_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid address ID"
        )

    return ObjectId(address_id)


# =========================
# CREATE
# POST /addresses
# =========================

@router.post(
    "",
    status_code=status.HTTP_201_CREATED
)
def create_address(data: AddressCreate):

    now = datetime.utcnow()

    address_data = {
        "name": data.name,
        "address": data.address,
        "latitude": data.latitude,
        "longitude": data.longitude,
        "created_at": now,
        "updated_at": now
    }

    result = address_collection.insert_one(address_data)

    created = address_collection.find_one({
        "_id": result.inserted_id
    })

    return {
        "success": True,
        "message": "Address created successfully",
        "data": serialize_address(created)
    }


# =========================
# GET SINGLE
# GET /addresses/{id}
# =========================

@router.get("/{address_id}")
def get_address(address_id: str):

    object_id = validate_object_id(address_id)

    address = address_collection.find_one({
        "_id": object_id
    })

    if not address:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Address not found"
        )

    return {
        "success": True,
        "data": serialize_address(address)
    }


# =========================
# UPDATE
# PUT /addresses/{id}
# =========================

@router.put("/{address_id}")
def update_address(
    address_id: str,
    data: AddressUpdate
):

    object_id = validate_object_id(address_id)

    existing = address_collection.find_one({
        "_id": object_id
    })

    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Address not found"
        )

    update_data = {}

    if data.name is not None:
        update_data["name"] = data.name

    if data.address is not None:
        update_data["address"] = data.address

    if data.latitude is not None:
        update_data["latitude"] = data.latitude

    if data.longitude is not None:
        update_data["longitude"] = data.longitude

    update_data["updated_at"] = datetime.utcnow()

    address_collection.update_one(
        {"_id": object_id},
        {"$set": update_data}
    )

    updated = address_collection.find_one({
        "_id": object_id
    })

    return {
        "success": True,
        "message": "Address updated successfully",
        "data": serialize_address(updated)
    }


# =========================
# DELETE
# DELETE /addresses/{id}
# =========================

@router.delete("/{address_id}")
def delete_address(address_id: str):

    object_id = validate_object_id(address_id)

    result = address_collection.delete_one({
        "_id": object_id
    })

    if result.deleted_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Address not found"
        )

    return {
        "success": True,
        "message": "Address deleted successfully"
    }


# =========================
# GET LIST
# GET /addresses
# =========================

@router.get("")
def get_addresses(
    skip: int = 0,
    limit: int = 20
):

    addresses = []

    cursor = (
        address_collection
        .find({})
        .sort("created_at", -1)
        .skip(skip)
        .limit(limit)
    )

    for address in cursor:
        addresses.append(
            serialize_address(address)
        )

    total = address_collection.count_documents({})

    return {
        "success": True,
        "total": total,
        "skip": skip,
        "limit": limit,
        "data": addresses
    }
