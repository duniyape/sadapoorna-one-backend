from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from datetime import datetime, timezone
from bson import ObjectId

from database import (
    users_collection,
    masters_collection,
    user_access_collection
)

router = APIRouter()


# =========================================================
# MODEL
# =========================================================

class HierarchyAccess(BaseModel):
    manager_id: str
    subordinate_ids: list[str]


# =========================================================
# GET USER LEVEL
# =========================================================

def get_user_level(user):

    designation_id = user.get("designation")

    if not designation_id:
        return None

    if not ObjectId.is_valid(designation_id):
        return None

    designation = masters_collection.find_one({
        "_id": ObjectId(designation_id),
        "master_type": "Designation"
    })

    if not designation:
        return None

    try:
        return int(
            designation.get(
                "level",
                designation.get("grade", 0)
            )
        )
    except (TypeError, ValueError):
        return 0


# =========================================================
# SAVE HIERARCHY
# =========================================================

@router.post("/hierarchy")
def save_hierarchy(data: HierarchyAccess):

    # -----------------------------------
    # Validate manager
    # -----------------------------------

    if not ObjectId.is_valid(data.manager_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid manager_id"
        )

    manager = users_collection.find_one({
        "_id": ObjectId(data.manager_id)
    })

    if not manager:
        raise HTTPException(
            status_code=404,
            detail="Manager user not found"
        )

    manager_level = get_user_level(manager)

    if manager_level is None:
        raise HTTPException(
            status_code=400,
            detail="Manager designation level not found"
        )

    # -----------------------------------
    # Validate subordinate users
    # -----------------------------------

    valid_subordinates = []

    for subordinate_id in data.subordinate_ids:

        if not ObjectId.is_valid(subordinate_id):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid subordinate_id: {subordinate_id}"
            )

        subordinate = users_collection.find_one({
            "_id": ObjectId(subordinate_id)
        })

        if not subordinate:
            raise HTTPException(
                status_code=404,
                detail=f"User not found: {subordinate_id}"
            )

        subordinate_level = get_user_level(
            subordinate
        )

        if subordinate_level is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Designation level not found "
                    f"for user {subordinate_id}"
                )
            )

        # -----------------------------------
        # Subordinate must be lower level
        # -----------------------------------

        if subordinate_level >= manager_level:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"User {subordinate_id} is not "
                    f"lower than manager level"
                )
            )

        valid_subordinates.append(
            subordinate_id
        )

    # -----------------------------------
    # Remove duplicate IDs
    # -----------------------------------

    valid_subordinates = list(
        dict.fromkeys(valid_subordinates)
    )

    now = datetime.now(timezone.utc)

    # -----------------------------------
    # Save / Update
    # -----------------------------------

    existing = user_access_collection.find_one({
        "manager_id": data.manager_id
    })

    if existing:

        user_access_collection.update_one(
            {
                "manager_id": data.manager_id
            },
            {
                "$set": {
                    "subordinate_ids": valid_subordinates,
                    "updated_at": now
                }
            }
        )

        message = "Hierarchy updated successfully"

    else:

        user_access_collection.insert_one({
            "manager_id": data.manager_id,
            "subordinate_ids": valid_subordinates,
            "created_at": now,
            "updated_at": now
        })

        message = "Hierarchy created successfully"

    return {
        "status": True,
        "message": message,
        "manager_id": data.manager_id,
        "subordinate_ids": valid_subordinates
    }



@router.get("/tree/{user_id}")
def get_access_tree(user_id: str):

    if not ObjectId.is_valid(user_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid user_id"
        )

    user = users_collection.find_one({
        "_id": ObjectId(user_id)
    })

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )

    visited = set()

    def build_tree(current_user_id):

        # -----------------------------------
        # Prevent circular hierarchy
        # -----------------------------------

        if current_user_id in visited:
            return []

        visited.add(current_user_id)

        mapping = user_access_collection.find_one({
            "manager_id": current_user_id
        })

        if not mapping:
            return []

        result = []

        for subordinate_id in mapping.get(
            "subordinate_ids",
            []
        ):

            subordinate = users_collection.find_one({
                "_id": ObjectId(subordinate_id)
            })

            if not subordinate:
                continue

            children = build_tree(
                subordinate_id
            )

            result.append({

                "id": str(
                    subordinate["_id"]
                ),

                "name": subordinate.get(
                    "name"
                ),

                "email": subordinate.get(
                    "email"
                ),

                "mobile": subordinate.get(
                    "mobile"
                ),

                "employee_id": subordinate.get(
                    "employee_id"
                ),

                "designation": subordinate.get(
                    "designation"
                ),

                "branch": subordinate.get(
                    "branch"
                ),

                "children": children
            })

        return result

    tree = build_tree(user_id)

    return {
        "status": True,
        "user_id": user_id,
        "access": tree
    }