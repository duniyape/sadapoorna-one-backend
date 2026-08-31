from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, EmailStr
from typing import Optional
from datetime import datetime
from bson import ObjectId

from database import vendors_collection

router = APIRouter()


# =========================================
# HELPERS
# =========================================

def serialize_vendor(vendor):
    vendor["id"] = str(vendor["_id"])
    del vendor["_id"]
    return vendor


# =========================================
# MODELS
# =========================================

class VendorCreate(BaseModel):
    business_name: str = Field(..., min_length=1)
    contact_person: Optional[str] = None
    mobile: Optional[str] = None
    email: Optional[EmailStr] = None

    gst_number: Optional[str] = None
    pan_number: Optional[str] = None

    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None

    bank_name: Optional[str] = None
    account_number: Optional[str] = None
    ifsc_code: Optional[str] = None

    payment_terms: Optional[str] = None
    status: str = "active"


class VendorUpdate(BaseModel):
    business_name: Optional[str] = None
    contact_person: Optional[str] = None
    mobile: Optional[str] = None
    email: Optional[EmailStr] = None

    gst_number: Optional[str] = None
    pan_number: Optional[str] = None

    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None

    bank_name: Optional[str] = None
    account_number: Optional[str] = None
    ifsc_code: Optional[str] = None

    payment_terms: Optional[str] = None
    status: Optional[str] = None


# =========================================
# CREATE VENDOR
# POST /vendors/v1
# =========================================

@router.post("/v1")
def create_vendor(vendor: VendorCreate):

    # Generate vendor code
    last_vendor = vendors_collection.find_one(
        {},
        sort=[("created_at", -1)]
    )

    if last_vendor and last_vendor.get("vendor_code"):
        try:
            last_number = int(
                last_vendor["vendor_code"].replace("VEN-", "")
            )
            vendor_number = last_number + 1
        except:
            vendor_number = 1
    else:
        vendor_number = 1

    vendor_code = f"VEN-{vendor_number:04d}"

    # Check duplicate GST
    if vendor.gst_number:
        existing = vendors_collection.find_one({
            "gst_number": vendor.gst_number
        })

        if existing:
            raise HTTPException(
                status_code=400,
                detail="Vendor with this GST number already exists"
            )

    # Check duplicate mobile
    if vendor.mobile:
        existing = vendors_collection.find_one({
            "mobile": vendor.mobile
        })

        if existing:
            raise HTTPException(
                status_code=400,
                detail="Vendor with this mobile number already exists"
            )

    now = datetime.utcnow()

    vendor_data = {
        "vendor_code": vendor_code,
        "business_name": vendor.business_name,
        "contact_person": vendor.contact_person,
        "mobile": vendor.mobile,
        "email": vendor.email,

        "gst_number": vendor.gst_number,
        "pan_number": vendor.pan_number,

        "address": vendor.address,
        "city": vendor.city,
        "state": vendor.state,
        "pincode": vendor.pincode,

        "bank_name": vendor.bank_name,
        "account_number": vendor.account_number,
        "ifsc_code": vendor.ifsc_code,

        "payment_terms": vendor.payment_terms,
        "status": vendor.status,

        "created_at": now,
        "updated_at": now
    }

    result = vendors_collection.insert_one(vendor_data)

    vendor_data["_id"] = result.inserted_id

    return {
        "success": True,
        "message": "Vendor created successfully",
        "data": serialize_vendor(vendor_data)
    }


# =========================================
# GET VENDOR LIST
# GET /vendors/v1
# =========================================

@router.get("/v1")
def get_vendors(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    status: Optional[str] = None
):

    skip = (page - 1) * limit

    query = {}

    if status:
        query["status"] = status

    if search:
        query["$or"] = [
            {
                "business_name": {
                    "$regex": search,
                    "$options": "i"
                }
            },
            {
                "vendor_code": {
                    "$regex": search,
                    "$options": "i"
                }
            },
            {
                "contact_person": {
                    "$regex": search,
                    "$options": "i"
                }
            },
            {
                "mobile": {
                    "$regex": search,
                    "$options": "i"
                }
            },
            {
                "gst_number": {
                    "$regex": search,
                    "$options": "i"
                }
            }
        ]

    total = vendors_collection.count_documents(query)

    vendors = list(
        vendors_collection
        .find(query)
        .sort("created_at", -1)
        .skip(skip)
        .limit(limit)
    )

    data = [
        serialize_vendor(vendor)
        for vendor in vendors
    ]

    return {
        "success": True,
        "page": page,
        "limit": limit,
        "total": total,
        "total_pages": (total + limit - 1) // limit,
        "data": data
    }


# =========================================
# GET SINGLE VENDOR
# GET /vendors/v1/{vendor_id}
# =========================================

@router.get("/v1/{vendor_id}")
def get_vendor(vendor_id: str):

    if not ObjectId.is_valid(vendor_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid vendor ID"
        )

    vendor = vendors_collection.find_one({
        "_id": ObjectId(vendor_id)
    })

    if not vendor:
        raise HTTPException(
            status_code=404,
            detail="Vendor not found"
        )

    return {
        "success": True,
        "data": serialize_vendor(vendor)
    }


# =========================================
# UPDATE VENDOR
# PUT /vendors/v1/{vendor_id}
# =========================================

@router.post("/update/v1/{vendor_id}")
def update_vendor(
    vendor_id: str,
    vendor: VendorUpdate
):

    if not ObjectId.is_valid(vendor_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid vendor ID"
        )

    existing = vendors_collection.find_one({
        "_id": ObjectId(vendor_id)
    })

    if not existing:
        raise HTTPException(
            status_code=404,
            detail="Vendor not found"
        )

    update_data = vendor.model_dump(
        exclude_unset=True,
        exclude_none=True
    )

    # GST duplicate check
    if "gst_number" in update_data:

        duplicate = vendors_collection.find_one({
            "gst_number": update_data["gst_number"],
            "_id": {
                "$ne": ObjectId(vendor_id)
            }
        })

        if duplicate:
            raise HTTPException(
                status_code=400,
                detail="Another vendor already has this GST number"
            )

    # Mobile duplicate check
    if "mobile" in update_data:

        duplicate = vendors_collection.find_one({
            "mobile": update_data["mobile"],
            "_id": {
                "$ne": ObjectId(vendor_id)
            }
        })

        if duplicate:
            raise HTTPException(
                status_code=400,
                detail="Another vendor already has this mobile number"
            )

    update_data["updated_at"] = datetime.utcnow()

    vendors_collection.update_one(
        {
            "_id": ObjectId(vendor_id)
        },
        {
            "$set": update_data
        }
    )

    updated_vendor = vendors_collection.find_one({
        "_id": ObjectId(vendor_id)
    })

    return {
        "success": True,
        "message": "Vendor updated successfully",
        "data": serialize_vendor(updated_vendor)
    }


# =========================================
# DELETE VENDOR
# DELETE /vendors/v1/{vendor_id}
# =========================================

@router.post("/delete/v1/{vendor_id}")
def delete_vendor(vendor_id: str):

    if not ObjectId.is_valid(vendor_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid vendor ID"
        )

    vendor = vendors_collection.find_one({
        "_id": ObjectId(vendor_id)
    })

    if not vendor:
        raise HTTPException(
            status_code=404,
            detail="Vendor not found"
        )

    vendors_collection.delete_one({
        "_id": ObjectId(vendor_id)
    })

    return {
        "success": True,
        "message": "Vendor deleted successfully"
    }