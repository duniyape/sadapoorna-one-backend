from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime, timezone
from bson import ObjectId

from database import access_collection, masters_collection

router = APIRouter()


class FrontendIconAccess(BaseModel):
    icon: str
    buttons: list[str]


class AccessGrant(BaseModel):
    designation_id: str
    frontend_icons: list[FrontendIconAccess]


# =========================================================
# GRANT / UPDATE ACCESS
# =========================================================

@router.post("/grant")
def grant_access(access: AccessGrant):

    # -----------------------------------
    # Validate designation
    # -----------------------------------

    if not ObjectId.is_valid(access.designation_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid designation_id"
        )

    # -----------------------------------
    # Check designation
    # -----------------------------------

    designation = masters_collection.find_one({
        "_id": ObjectId(access.designation_id),
        "master_type": "Designation"
    })

    if not designation:
        raise HTTPException(
            status_code=404,
            detail="Designation not found"
        )

    # -----------------------------------
    # Prepare frontend icons
    # -----------------------------------

    frontend_icons = [
        {
            "icon": item.icon,
            "buttons": item.buttons
        }
        for item in access.frontend_icons
    ]

    now = datetime.now(timezone.utc)

    # -----------------------------------
    # Check existing access
    # -----------------------------------

    existing = access_collection.find_one({
        "designation_id": access.designation_id
    })

    if existing:

        access_collection.update_one(
            {
                "designation_id": access.designation_id
            },
            {
                "$set": {
                    "frontend_icons": frontend_icons,
                    "updated_at": now
                }
            }
        )

        message = "Access updated successfully"

    else:

        access_collection.insert_one({
            "designation_id": access.designation_id,
            "frontend_icons": frontend_icons,
            "created_at": now,
            "updated_at": now
        })

        message = "Access granted successfully"

    return {
        "status": True,
        "message": message
    }

# =========================================================
# GET ACCESS
# =========================================================

@router.get("/{designation_id}")
def get_access(designation_id: str):

    if not ObjectId.is_valid(designation_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid designation_id"
        )

    access = access_collection.find_one({
        "designation_id": designation_id
    })

    return {
        "status": True,
        "designation_id": designation_id,
        "frontend_icons": (
            access.get("frontend_icons", [])
            if access
            else []
        )
    }