from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timezone
from bson import ObjectId

from database import (
    beats_collection,
    users_collection
)
from routes.auth import get_current_user


router = APIRouter(
    prefix="/beats",
    tags=["Beat Management"]
)


# =========================================================
# CONSTANTS
# =========================================================

VALID_DAYS = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday"
]

VALID_STATUS = [
    "ACTIVE",
    "INACTIVE"
]


# =========================================================
# REQUEST MODELS
# =========================================================

class BeatCreate(BaseModel):
    beat_name: str = Field(..., min_length=1)
    day: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1)
    status: str = "ACTIVE"


class BeatUpdate(BaseModel):
    beat_name: Optional[str] = None
    day: Optional[str] = None
    user_id: Optional[str] = None
    status: Optional[str] = None


# =========================================================
# HELPER
# =========================================================

def serialize_beat(beat):
    user_name = None

    user_id = beat.get("user_id")

    if user_id:
        user = users_collection.find_one({
            "_id": ObjectId(user_id)
        })

        if user:
            user_name = (
                user.get("name")
                or user.get("full_name")
                or user.get("username")
            )

    return {
        "id": str(beat["_id"]),
        "beat_name": beat.get("beat_name"),
        "day": beat.get("day"),
        "user_id": user_id,
        "user_name": user_name,
        "status": beat.get("status"),
        "created_at": (
            beat["created_at"].isoformat()
            if isinstance(beat.get("created_at"), datetime)
            else beat.get("created_at")
        ),
        "updated_at": (
            beat["updated_at"].isoformat()
            if isinstance(beat.get("updated_at"), datetime)
            else beat.get("updated_at")
        )
    }

# =========================================================
# CREATE BEAT
# =========================================================

@router.post("/create")
def create_beat(
    data: BeatCreate,
    current_user=Depends(get_current_user)
):
    try:

        beat_name = data.beat_name.strip()
        day = data.day.strip().capitalize()
        user_id = data.user_id.strip()
        status = data.status.strip().upper()

        # -------------------------
        # Validation
        # -------------------------

        if not beat_name:
            raise HTTPException(
                status_code=400,
                detail="Beat name is required"
            )

        if day not in VALID_DAYS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid day. Allowed days: {', '.join(VALID_DAYS)}"
            )

        if status not in VALID_STATUS:
            raise HTTPException(
                status_code=400,
                detail="Status must be ACTIVE or INACTIVE"
            )

        # -------------------------
        # Duplicate Check
        # -------------------------

        existing_beat = beats_collection.find_one({
            "beat_name": beat_name,
            "day": day,
            "user_id": user_id
        })

        if existing_beat:
            raise HTTPException(
                status_code=400,
                detail="Beat already exists for this user and day"
            )

        # -------------------------
        # Create Document
        # -------------------------

        now = datetime.now(timezone.utc)

        beat = {
            "beat_name": beat_name,
            "day": day,
            "user_id": user_id,
            "status": status,
            "created_at": now,
            "updated_at": now
        }

        result = beats_collection.insert_one(beat)

        beat["_id"] = result.inserted_id

        return {
            "success": True,
            "message": "Beat created successfully",
            "data": serialize_beat(beat)
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


# =========================================================
# GET ALL BEATS
# =========================================================

@router.get("/")
def get_beats(
    user_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    day: Optional[str] = Query(None),
    current_user=Depends(get_current_user)
):
    try:

        query = {}

        # -------------------------
        # User Filter
        # -------------------------

        if user_id:
            query["user_id"] = user_id.strip()

        # -------------------------
        # Status Filter
        # -------------------------

        if status:

            status = status.strip().upper()

            if status not in VALID_STATUS:
                raise HTTPException(
                    status_code=400,
                    detail="Status must be ACTIVE or INACTIVE"
                )

            query["status"] = status

        # -------------------------
        # Day Filter
        # -------------------------

        if day:

            day = day.strip().capitalize()

            if day not in VALID_DAYS:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid day. Allowed days: {', '.join(VALID_DAYS)}"
                )

            query["day"] = day

        # -------------------------
        # Fetch Data
        # -------------------------

        cursor = beats_collection.find(query).sort(
            "created_at",
            -1
        )

        beats = []

        # IMPORTANT:
        # PyMongo uses normal for loop
        # NOT async for

        for beat in cursor:
            beats.append(
                serialize_beat(beat)
            )

        return {
            "success": True,
            "count": len(beats),
            "data": beats
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


# =========================================================
# GET SINGLE BEAT
# =========================================================

@router.get("/{beat_id}")
def get_beat(
    beat_id: str,
    current_user=Depends(get_current_user)
):
    try:

        # -------------------------
        # Validate ObjectId
        # -------------------------

        if not ObjectId.is_valid(beat_id):
            raise HTTPException(
                status_code=400,
                detail="Invalid beat ID"
            )

        # -------------------------
        # Find Beat
        # -------------------------

        beat = beats_collection.find_one({
            "_id": ObjectId(beat_id)
        })

        if not beat:
            raise HTTPException(
                status_code=404,
                detail="Beat not found"
            )

        return {
            "success": True,
            "data": serialize_beat(beat)
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


# =========================================================
# UPDATE BEAT
# =========================================================

@router.put("/{beat_id}")
def update_beat(
    beat_id: str,
    data: BeatUpdate,
    current_user=Depends(get_current_user)
):
    try:

        # -------------------------
        # Validate ObjectId
        # -------------------------

        if not ObjectId.is_valid(beat_id):
            raise HTTPException(
                status_code=400,
                detail="Invalid beat ID"
            )

        object_id = ObjectId(beat_id)

        # -------------------------
        # Check Existing Beat
        # -------------------------

        existing_beat = beats_collection.find_one({
            "_id": object_id
        })

        if not existing_beat:
            raise HTTPException(
                status_code=404,
                detail="Beat not found"
            )

        update_data = {}

        # -------------------------
        # Beat Name
        # -------------------------

        if data.beat_name is not None:

            beat_name = data.beat_name.strip()

            if not beat_name:
                raise HTTPException(
                    status_code=400,
                    detail="Beat name cannot be empty"
                )

            update_data["beat_name"] = beat_name

        # -------------------------
        # Day
        # -------------------------

        if data.day is not None:

            day = data.day.strip().capitalize()

            if day not in VALID_DAYS:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid day. Allowed days: {', '.join(VALID_DAYS)}"
                )

            update_data["day"] = day

        # -------------------------
        # User ID
        # -------------------------

        if data.user_id is not None:

            user_id = data.user_id.strip()

            if not user_id:
                raise HTTPException(
                    status_code=400,
                    detail="User ID cannot be empty"
                )

            update_data["user_id"] = user_id

        # -------------------------
        # Status
        # -------------------------

        if data.status is not None:

            status = data.status.strip().upper()

            if status not in VALID_STATUS:
                raise HTTPException(
                    status_code=400,
                    detail="Status must be ACTIVE or INACTIVE"
                )

            update_data["status"] = status

        # -------------------------
        # Nothing To Update
        # -------------------------

        if not update_data:
            raise HTTPException(
                status_code=400,
                detail="No data provided for update"
            )

        # -------------------------
        # Updated Time
        # -------------------------

        update_data["updated_at"] = datetime.now(timezone.utc)

        # -------------------------
        # Update
        # -------------------------

        beats_collection.update_one(
            {
                "_id": object_id
            },
            {
                "$set": update_data
            }
        )

        # -------------------------
        # Get Updated Beat
        # -------------------------

        updated_beat = beats_collection.find_one({
            "_id": object_id
        })

        return {
            "success": True,
            "message": "Beat updated successfully",
            "data": serialize_beat(updated_beat)
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


# =========================================================
# DELETE BEAT
# =========================================================

@router.delete("/{beat_id}")
def delete_beat(
    beat_id: str,
    current_user=Depends(get_current_user)
):
    try:

        if not ObjectId.is_valid(beat_id):
            raise HTTPException(
                status_code=400,
                detail="Invalid beat ID"
            )

        result = beats_collection.delete_one({
            "_id": ObjectId(beat_id)
        })

        if result.deleted_count == 0:
            raise HTTPException(
                status_code=404,
                detail="Beat not found"
            )

        return {
            "success": True,
            "message": "Beat deleted successfully"
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )