from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, EmailStr
from datetime import datetime
from bson import ObjectId

from database import warehouses_collection


router = APIRouter()


# =========================================================
# WAREHOUSE MODEL
# =========================================================

class Warehouse(BaseModel):

    # Basic
    name: str
    code: str | None = None

    # Branch
    branch: str | None = None

    # Warehouse Details
    warehouse_type: str | None = "general"
    capacity: float | None = None
    capacity_unit: str | None = "kg"

    # Address
    address: str | None = None
    city: str | None = None
    state: str | None = None
    pincode: str | None = None

    # Contact
    contact_person: str | None = None
    mobile: str | None = None
    email: EmailStr | None = None

    # Other
    description: str | None = None
    status: str | None = "active"


# =========================================================
# UPDATE MODEL
# =========================================================

class WarehouseUpdate(BaseModel):

    name: str | None = None
    code: str | None = None

    branch: str | None = None

    warehouse_type: str | None = None
    capacity: float | None = None
    capacity_unit: str | None = None

    address: str | None = None
    city: str | None = None
    state: str | None = None
    pincode: str | None = None

    contact_person: str | None = None
    mobile: str | None = None
    email: EmailStr | None = None

    description: str | None = None
    status: str | None = None


# =========================================================
# CREATE WAREHOUSE
# =========================================================

@router.post("/create")
def create_warehouse(warehouse: Warehouse):

    # -----------------------------------------
    # Check duplicate warehouse name
    # -----------------------------------------

    existing_name = warehouses_collection.find_one({
        "name": warehouse.name
    })

    if existing_name:
        raise HTTPException(
            status_code=400,
            detail="Warehouse name already exists"
        )

    # -----------------------------------------
    # Generate warehouse code
    # -----------------------------------------

    if warehouse.code:

        existing_code = warehouses_collection.find_one({
            "code": warehouse.code.upper()
        })

        if existing_code:
            raise HTTPException(
                status_code=400,
                detail="Warehouse code already exists"
            )

        warehouse_code = warehouse.code.upper()

    else:

        # Generate WH001, WH002, WH003...

        last_warehouse = warehouses_collection.find_one(
            {
                "warehouse_number": {
                    "$exists": True
                }
            },
            sort=[
                ("warehouse_number", -1)
            ]
        )

        if last_warehouse:
            next_number = (
                last_warehouse.get("warehouse_number", 0) + 1
            )
        else:
            next_number = 1

        warehouse_code = f"WH{next_number:03d}"

    # -----------------------------------------
    # Prepare data
    # -----------------------------------------

    data = warehouse.model_dump()

    data["code"] = warehouse_code

    # If generated manually
    if "next_number" in locals():
        data["warehouse_number"] = next_number

    data["created_at"] = datetime.utcnow()
    data["updated_at"] = datetime.utcnow()

    # -----------------------------------------
    # Insert
    # -----------------------------------------

    result = warehouses_collection.insert_one(data)

    return {
        "status": True,
        "message": "Warehouse Created Successfully",
        "warehouse_id": str(result.inserted_id),
        "warehouse_code": warehouse_code
    }


# =========================================================
# GET ALL WAREHOUSES
# =========================================================

@router.get("/get")
def get_warehouses(

    search: str | None = Query(None),
    status: str | None = Query(None),
    branch: str | None = Query(None)

):

    query = {}

    # -----------------------------------------
    # Search
    # -----------------------------------------

    if search:

        query["$or"] = [
            {
                "name": {
                    "$regex": search,
                    "$options": "i"
                }
            },
            {
                "code": {
                    "$regex": search,
                    "$options": "i"
                }
            },
            {
                "city": {
                    "$regex": search,
                    "$options": "i"
                }
            },
            {
                "contact_person": {
                    "$regex": search,
                    "$options": "i"
                }
            }
        ]

    # -----------------------------------------
    # Status
    # -----------------------------------------

    if status:
        query["status"] = status

    # -----------------------------------------
    # Branch
    # -----------------------------------------

    if branch:
        query["branch"] = branch

    # -----------------------------------------
    # Fetch
    # -----------------------------------------

    warehouses = list(
        warehouses_collection.find(query).sort(
            "created_at",
            -1
        )
    )

    data = []

    for warehouse in warehouses:

        data.append({

            "id": str(warehouse["_id"]),

            # Basic
            "name": warehouse.get("name"),
            "code": warehouse.get("code"),

            # Branch
            "branch": warehouse.get("branch"),

            # Details
            "warehouse_type": warehouse.get(
                "warehouse_type"
            ),

            "capacity": warehouse.get(
                "capacity"
            ),

            "capacity_unit": warehouse.get(
                "capacity_unit"
            ),

            # Address
            "address": warehouse.get("address"),
            "city": warehouse.get("city"),
            "state": warehouse.get("state"),
            "pincode": warehouse.get("pincode"),

            # Contact
            "contact_person": warehouse.get(
                "contact_person"
            ),

            "mobile": warehouse.get("mobile"),
            "email": warehouse.get("email"),

            # Other
            "description": warehouse.get(
                "description"
            ),

            "status": warehouse.get("status"),

            # Dates
            "created_at": warehouse.get(
                "created_at"
            ),

            "updated_at": warehouse.get(
                "updated_at"
            )
        })

    return {

        "status": True,

        "count": len(data),

        "data": data
    }


# =========================================================
# GET ONE WAREHOUSE
# =========================================================

@router.get("/get-one")
def get_warehouse(

    warehouse_id: str | None = Query(None),

    code: str | None = Query(None)

):

    # -----------------------------------------
    # Validation
    # -----------------------------------------

    if not warehouse_id and not code:

        raise HTTPException(
            status_code=400,
            detail="Please provide warehouse_id or code"
        )

    # -----------------------------------------
    # Build query
    # -----------------------------------------

    query = {}

    if warehouse_id:

        if not ObjectId.is_valid(warehouse_id):

            raise HTTPException(
                status_code=400,
                detail="Invalid warehouse_id"
            )

        query["_id"] = ObjectId(warehouse_id)

    elif code:

        query["code"] = code.upper()

    # -----------------------------------------
    # Find
    # -----------------------------------------

    warehouse = warehouses_collection.find_one(
        query
    )

    if not warehouse:

        raise HTTPException(
            status_code=404,
            detail="Warehouse not found"
        )

    # -----------------------------------------
    # Response
    # -----------------------------------------

    data = {

        "id": str(warehouse["_id"]),

        "name": warehouse.get("name"),

        "code": warehouse.get("code"),

        "branch": warehouse.get("branch"),

        "warehouse_type": warehouse.get(
            "warehouse_type"
        ),

        "capacity": warehouse.get(
            "capacity"
        ),

        "capacity_unit": warehouse.get(
            "capacity_unit"
        ),

        "address": warehouse.get("address"),

        "city": warehouse.get("city"),

        "state": warehouse.get("state"),

        "pincode": warehouse.get("pincode"),

        "contact_person": warehouse.get(
            "contact_person"
        ),

        "mobile": warehouse.get("mobile"),

        "email": warehouse.get("email"),

        "description": warehouse.get(
            "description"
        ),

        "status": warehouse.get("status"),

        "created_at": warehouse.get(
            "created_at"
        ),

        "updated_at": warehouse.get(
            "updated_at"
        )
    }

    return {

        "status": True,

        "data": data

    }


# =========================================================
# UPDATE WAREHOUSE
# =========================================================

@router.post("/update/{warehouse_id}")
def update_warehouse(

    warehouse_id: str,

    warehouse: WarehouseUpdate

):

    # -----------------------------------------
    # Validate ID
    # -----------------------------------------

    if not ObjectId.is_valid(warehouse_id):

        raise HTTPException(
            status_code=400,
            detail="Invalid warehouse_id"
        )

    object_id = ObjectId(warehouse_id)

    # -----------------------------------------
    # Check warehouse
    # -----------------------------------------

    existing = warehouses_collection.find_one({

        "_id": object_id

    })

    if not existing:

        raise HTTPException(
            status_code=404,
            detail="Warehouse not found"
        )

    # -----------------------------------------
    # Prepare update
    # -----------------------------------------

    update_data = warehouse.model_dump(
        exclude_unset=True
    )

    # -----------------------------------------
    # Name duplicate check
    # -----------------------------------------

    if update_data.get("name"):

        duplicate_name = warehouses_collection.find_one({

            "name": update_data["name"],

            "_id": {
                "$ne": object_id
            }

        })

        if duplicate_name:

            raise HTTPException(
                status_code=400,
                detail="Warehouse name already exists"
            )

    # -----------------------------------------
    # Code duplicate check
    # -----------------------------------------

    if update_data.get("code"):

        update_data["code"] = (
            update_data["code"].upper()
        )

        duplicate_code = warehouses_collection.find_one({

            "code": update_data["code"],

            "_id": {
                "$ne": object_id
            }

        })

        if duplicate_code:

            raise HTTPException(
                status_code=400,
                detail="Warehouse code already exists"
            )

    # -----------------------------------------
    # Updated time
    # -----------------------------------------

    update_data["updated_at"] = datetime.utcnow()

    # -----------------------------------------
    # Update
    # -----------------------------------------

    warehouses_collection.update_one(

        {
            "_id": object_id
        },

        {
            "$set": update_data
        }

    )

    return {

        "status": True,

        "message": "Warehouse Updated Successfully",

        "warehouse_id": warehouse_id,

        "warehouse_code": existing.get("code")

    }

# =========================================================
# CHANGE STATUS
# =========================================================

@router.post("/status/{warehouse_id}")
def change_warehouse_status(

    warehouse_id: str,

    status: str = Query(...)

):

    # -----------------------------------------
    # Validate ID
    # -----------------------------------------

    if not ObjectId.is_valid(warehouse_id):

        raise HTTPException(
            status_code=400,
            detail="Invalid warehouse_id"
        )

    # -----------------------------------------
    # Validate status
    # -----------------------------------------

    allowed_status = [
        "active",
        "inactive"
    ]

    if status not in allowed_status:

        raise HTTPException(
            status_code=400,
            detail=f"Status must be one of {allowed_status}"
        )

    # -----------------------------------------
    # Update
    # -----------------------------------------

    result = warehouses_collection.update_one(

        {
            "_id": ObjectId(warehouse_id)
        },

        {
            "$set": {

                "status": status,

                "updated_at": datetime.utcnow()

            }
        }

    )

    if result.matched_count == 0:

        raise HTTPException(
            status_code=404,
            detail="Warehouse not found"
        )

    return {

        "status": True,

        "message": "Warehouse Status Updated Successfully",

        "warehouse_id": warehouse_id,

        "status": status

    }