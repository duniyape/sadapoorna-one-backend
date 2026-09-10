from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime, timezone
from bson import ObjectId
from database import groups_collection, subgroups_collection, ledgers_collection

# =========================================================
# HELPERS
# =========================================================
router = APIRouter()


def serialize_doc(doc):
    if doc is None:
        return None

    if isinstance(doc, ObjectId):
        return str(doc)

    if isinstance(doc, dict):
        return {
            key: serialize_doc(value)
            for key, value in doc.items()
        }

    if isinstance(doc, list):
        return [
            serialize_doc(item)
            for item in doc
        ]

    return doc


def object_id(value: str):
    try:
        return ObjectId(value)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid ID"
        )


# =========================================================
# SCHEMAS
# =========================================================

class GroupCreate(BaseModel):
    group_name: str = Field(..., min_length=1)
    group_type: Literal[
        "LIABILITIES",
        "EXPENSES",
        "ASSETS",
        "INCOME"
    ]
    description: Optional[str] = None
    status: Literal["ACTIVE", "INACTIVE"] = "ACTIVE"


class GroupUpdate(BaseModel):
    group_name: Optional[str] = None
    group_type: Optional[
        Literal[
            "LIABILITIES",
            "EXPENSES",
            "ASSETS",
            "INCOME"
        ]
    ] = None
    description: Optional[str] = None
    status: Optional[Literal["ACTIVE", "INACTIVE"]] = None


class SubgroupCreate(BaseModel):
    subgroup_name: str = Field(..., min_length=1)
    group_id: str
    description: Optional[str] = None
    status: Literal["ACTIVE", "INACTIVE"] = "ACTIVE"


class SubgroupUpdate(BaseModel):
    subgroup_name: Optional[str] = None
    group_id: Optional[str] = None
    description: Optional[str] = None
    status: Optional[Literal["ACTIVE", "INACTIVE"]] = None


class LedgerCreate(BaseModel):
    ledger_name: str = Field(..., min_length=1)
    group_id: str
    subgroup_id: Optional[str] = None
    opening_balance: float = 0
    opening_balance_type: Literal["DEBIT", "CREDIT"] = "DEBIT"
    description: Optional[str] = None
    status: Literal["ACTIVE", "INACTIVE"] = "ACTIVE"


class LedgerUpdate(BaseModel):
    ledger_name: Optional[str] = None
    group_id: Optional[str] = None
    subgroup_id: Optional[str] = None
    opening_balance: Optional[float] = None
    opening_balance_type: Optional[
        Literal["DEBIT", "CREDIT"]
    ] = None
    description: Optional[str] = None
    status: Optional[Literal["ACTIVE", "INACTIVE"]] = None


# =========================================================
# GROUP APIs
# =========================================================

@router.post("/groups")
def create_group(data: GroupCreate):

    # Duplicate check
    existing = groups_collection.find_one({
        "group_name": data.group_name.strip()
    })

    if existing:
        raise HTTPException(
            status_code=400,
            detail="Group already exists"
        )

    now = datetime.now(timezone.utc)

    group = {
        "group_name": data.group_name.strip(),
        "group_type": data.group_type,
        "description": data.description,
        "status": data.status,
        "created_at": now,
        "updated_at": now
    }

    result = groups_collection.insert_one(group)

    return {
        "success": True,
        "message": "Group created successfully",
        "group_id": str(result.inserted_id)
    }


@router.get("/groups")
def get_groups(
    group_type: Optional[str] = None,
    status: Optional[str] = None
):

    query = {}

    if group_type:
        query["group_type"] = group_type.upper()

    if status:
        query["status"] = status.upper()

    cursor = groups_collection.find(query).sort(
        "group_name", 1
    )

    groups = []

    for group in cursor:
        groups.append(serialize_doc(group))

    return {
        "success": True,
        "count": len(groups),
        "data": groups
    }


@router.get("/groups/{group_id}")
def get_group(group_id: str):

    group = groups_collection.find_one({
        "_id": object_id(group_id)
    })

    if not group:
        raise HTTPException(
            status_code=404,
            detail="Group not found"
        )

    return {
        "success": True,
        "data": serialize_doc(group)
    }


@router.put("/groups/{group_id}")
def update_group(
    group_id: str,
    data: GroupUpdate
):

    gid = object_id(group_id)

    group = groups_collection.find_one({
        "_id": gid
    })

    if not group:
        raise HTTPException(
            status_code=404,
            detail="Group not found"
        )

    update_data = {
        key: value
        for key, value in data.model_dump().items()
        if value is not None
    }

    if "group_name" in update_data:
        duplicate = groups_collection.find_one({
            "group_name": update_data["group_name"].strip(),
            "_id": {"$ne": gid}
        })

        if duplicate:
            raise HTTPException(
                status_code=400,
                detail="Group already exists"
            )

        update_data["group_name"] = update_data[
            "group_name"
        ].strip()

    update_data["updated_at"] = datetime.now(timezone.utc)

    groups_collection.update_one(
        {"_id": gid},
        {"$set": update_data}
    )

    return {
        "success": True,
        "message": "Group updated successfully"
    }


@router.delete("/groups/{group_id}")
def delete_group(group_id: str):

    gid = object_id(group_id)

    group = groups_collection.find_one({
        "_id": gid
    })

    if not group:
        raise HTTPException(
            status_code=404,
            detail="Group not found"
        )

    # Don't delete if subgroups exist
    subgroup_count = subgroups_collection.count_documents({
        "group_id": gid
    })

    if subgroup_count > 0:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete group. Subgroups exist under this group."
        )

    # Don't delete if ledgers exist
    ledger_count = ledgers_collection.count_documents({
        "group_id": gid
    })

    if ledger_count > 0:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete group. Ledgers exist under this group."
        )

    groups_collection.delete_one({
        "_id": gid
    })

    return {
        "success": True,
        "message": "Group deleted successfully"
    }


# =========================================================
# SUBGROUP APIs
# =========================================================

@router.post("/subgroups")
def create_subgroup(data: SubgroupCreate):

    gid = object_id(data.group_id)

    # Check parent group
    group = groups_collection.find_one({
        "_id": gid
    })

    if not group:
        raise HTTPException(
            status_code=404,
            detail="Parent group not found"
        )

    # Duplicate subgroup inside same group
    existing = subgroups_collection.find_one({
        "group_id": gid,
        "subgroup_name": data.subgroup_name.strip()
    })

    if existing:
        raise HTTPException(
            status_code=400,
            detail="Subgroup already exists in this group"
        )

    now = datetime.now(timezone.utc)

    subgroup = {
        "subgroup_name": data.subgroup_name.strip(),
        "group_id": gid,
        "group_name": group["group_name"],
        "group_type": group["group_type"],
        "description": data.description,
        "status": data.status,
        "created_at": now,
        "updated_at": now
    }

    result = subgroups_collection.insert_one(subgroup)

    return {
        "success": True,
        "message": "Subgroup created successfully",
        "subgroup_id": str(result.inserted_id)
    }


@router.get("/subgroups")
def get_subgroups(
    group_id: Optional[str] = None,
    status: Optional[str] = None
):

    query = {}

    if group_id:
        query["group_id"] = object_id(group_id)

    if status:
        query["status"] = status.upper()

    cursor = subgroups_collection.find(query).sort(
        "subgroup_name", 1
    )

    subgroups = []

    for subgroup in cursor:
        subgroups.append(
            serialize_doc(subgroup)
        )

    return {
        "success": True,
        "count": len(subgroups),
        "data": subgroups
    }


@router.get("/subgroups/{subgroup_id}")
def get_subgroup(subgroup_id: str):

    subgroup = subgroups_collection.find_one({
        "_id": object_id(subgroup_id)
    })

    if not subgroup:
        raise HTTPException(
            status_code=404,
            detail="Subgroup not found"
        )

    return {
        "success": True,
        "data": serialize_doc(subgroup)
    }


@router.put("/subgroups/{subgroup_id}")
def update_subgroup(
    subgroup_id: str,
    data: SubgroupUpdate
):

    sid = object_id(subgroup_id)

    subgroup = subgroups_collection.find_one({
        "_id": sid
    })

    if not subgroup:
        raise HTTPException(
            status_code=404,
            detail="Subgroup not found"
        )

    update_data = {
        key: value
        for key, value in data.model_dump().items()
        if value is not None
    }

    # If group is changed
    if "group_id" in update_data:

        new_gid = object_id(
            update_data["group_id"]
        )

        group = groups_collection.find_one({
            "_id": new_gid
        })

        if not group:
            raise HTTPException(
                status_code=404,
                detail="New parent group not found"
            )

        update_data["group_id"] = new_gid
        update_data["group_name"] = group["group_name"]
        update_data["group_type"] = group["group_type"]

    else:
        new_gid = subgroup["group_id"]

    if "subgroup_name" in update_data:

        name = update_data["subgroup_name"].strip()

        duplicate = subgroups_collection.find_one({
            "group_id": new_gid,
            "subgroup_name": name,
            "_id": {"$ne": sid}
        })

        if duplicate:
            raise HTTPException(
                status_code=400,
                detail="Subgroup already exists in this group"
            )

        update_data["subgroup_name"] = name

    update_data["updated_at"] = datetime.now(timezone.utc)

    subgroups_collection.update_one(
        {"_id": sid},
        {"$set": update_data}
    )

    return {
        "success": True,
        "message": "Subgroup updated successfully"
    }


@router.delete("/subgroups/{subgroup_id}")
def delete_subgroup(subgroup_id: str):

    sid = object_id(subgroup_id)

    subgroup = subgroups_collection.find_one({
        "_id": sid
    })

    if not subgroup:
        raise HTTPException(
            status_code=404,
            detail="Subgroup not found"
        )

    ledger_count = ledgers_collection.count_documents({
        "subgroup_id": sid
    })

    if ledger_count > 0:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete subgroup. Ledgers exist under this subgroup."
        )

    subgroups_collection.delete_one({
        "_id": sid
    })

    return {
        "success": True,
        "message": "Subgroup deleted successfully"
    }


# =========================================================
# LEDGER APIs
# =========================================================

@router.post("/ledgers")
def create_ledger(data: LedgerCreate):

    gid = object_id(data.group_id)

    # Check group
    group = groups_collection.find_one({
        "_id": gid
    })

    if not group:
        raise HTTPException(
            status_code=404,
            detail="Group not found"
        )

    subgroup = None

    if data.subgroup_id:

        sid = object_id(data.subgroup_id)

        subgroup = subgroups_collection.find_one({
            "_id": sid,
            "group_id": gid
        })

        if not subgroup:
            raise HTTPException(
                status_code=404,
                detail="Subgroup not found under selected group"
            )

    # Duplicate ledger
    duplicate_query = {
        "group_id": gid,
        "ledger_name": data.ledger_name.strip()
    }

    if data.subgroup_id:
        duplicate_query["subgroup_id"] = object_id(
            data.subgroup_id
        )

    existing = ledgers_collection.find_one(
        duplicate_query
    )

    if existing:
        raise HTTPException(
            status_code=400,
            detail="Ledger already exists"
        )

    now = datetime.now(timezone.utc)

    ledger = {
        "ledger_name": data.ledger_name.strip(),

        "group_id": gid,
        "group_name": group["group_name"],
        "group_type": group["group_type"],

        "subgroup_id": (
            object_id(data.subgroup_id)
            if data.subgroup_id
            else None
        ),

        "subgroup_name": (
            subgroup["subgroup_name"]
            if subgroup
            else None
        ),

        "opening_balance": data.opening_balance,
        "opening_balance_type": data.opening_balance_type,

        "description": data.description,
        "status": data.status,

        "created_at": now,
        "updated_at": now
    }

    result = ledgers_collection.insert_one(
        ledger
    )

    return {
        "success": True,
        "message": "Ledger created successfully",
        "ledger_id": str(result.inserted_id)
    }


@router.get("/ledgers")
def get_ledgers(
    group_id: Optional[str] = None,
    subgroup_id: Optional[str] = None,
    status: Optional[str] = None
):

    query = {}

    if group_id:
        query["group_id"] = object_id(group_id)

    if subgroup_id:
        query["subgroup_id"] = object_id(
            subgroup_id
        )

    if status:
        query["status"] = status.upper()

    cursor = ledgers_collection.find(query).sort(
        "ledger_name", 1
    )

    ledgers = []

    for ledger in cursor:
        ledgers.append(
            serialize_doc(ledger)
        )

    return {
        "success": True,
        "count": len(ledgers),
        "data": ledgers
    }


@router.get("/ledgers/{ledger_id}")
def get_ledger(ledger_id: str):

    ledger = ledgers_collection.find_one({
        "_id": object_id(ledger_id)
    })

    if not ledger:
        raise HTTPException(
            status_code=404,
            detail="Ledger not found"
        )

    return {
        "success": True,
        "data": serialize_doc(ledger)
    }


@router.put("/ledgers/{ledger_id}")
def update_ledger(
    ledger_id: str,
    data: LedgerUpdate
):

    lid = object_id(ledger_id)

    ledger = ledgers_collection.find_one({
        "_id": lid
    })

    if not ledger:
        raise HTTPException(
            status_code=404,
            detail="Ledger not found"
        )

    update_data = {
        key: value
        for key, value in data.model_dump().items()
        if value is not None
    }

    # -----------------------------------------
    # Group change
    # -----------------------------------------

    if "group_id" in update_data:

        gid = object_id(
            update_data["group_id"]
        )

        group = groups_collection.find_one({
            "_id": gid
        })

        if not group:
            raise HTTPException(
                status_code=404,
                detail="Group not found"
            )

        update_data["group_id"] = gid
        update_data["group_name"] = group["group_name"]
        update_data["group_type"] = group["group_type"]

    else:
        gid = ledger["group_id"]

    # -----------------------------------------
    # Subgroup change
    # -----------------------------------------

    if "subgroup_id" in update_data:

        if update_data["subgroup_id"]:

            sid = object_id(
                update_data["subgroup_id"]
            )

            subgroup = subgroups_collection.find_one({
                "_id": sid,
                "group_id": gid
            })

            if not subgroup:
                raise HTTPException(
                    status_code=404,
                    detail="Subgroup not found under selected group"
                )

            update_data["subgroup_id"] = sid
            update_data["subgroup_name"] = (
                subgroup["subgroup_name"]
            )

        else:
            update_data["subgroup_id"] = None
            update_data["subgroup_name"] = None

    # -----------------------------------------
    # Ledger name
    # -----------------------------------------

    if "ledger_name" in update_data:

        name = update_data["ledger_name"].strip()

        duplicate_query = {
            "ledger_name": name,
            "group_id": gid,
            "_id": {"$ne": lid}
        }

        if update_data.get("subgroup_id"):
            duplicate_query["subgroup_id"] = (
                update_data["subgroup_id"]
            )

        duplicate = ledgers_collection.find_one(
            duplicate_query
        )

        if duplicate:
            raise HTTPException(
                status_code=400,
                detail="Ledger already exists"
            )

        update_data["ledger_name"] = name

    update_data["updated_at"] = datetime.now(timezone.utc)

    ledgers_collection.update_one(
        {"_id": lid},
        {"$set": update_data}
    )

    return {
        "success": True,
        "message": "Ledger updated successfully"
    }


@router.delete("/ledgers/{ledger_id}")
def delete_ledger(ledger_id: str):

    lid = object_id(ledger_id)

    ledger = ledgers_collection.find_one({
        "_id": lid
    })

    if not ledger:
        raise HTTPException(
            status_code=404,
            detail="Ledger not found"
        )

    ledgers_collection.delete_one({
        "_id": lid
    })

    return {
        "success": True,
        "message": "Ledger deleted successfully"
    }