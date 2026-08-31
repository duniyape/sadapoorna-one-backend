from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from datetime import datetime
from bson import ObjectId

from database import vehicles_collection


router = APIRouter()


# =========================================================
# VEHICLE MODEL
# =========================================================

class Vehicle(BaseModel):

    # -----------------------------------------------------
    # Basic Details
    # -----------------------------------------------------

    vehicle_number: str
    vehicle_type: str | None = None
    make: str | None = None
    model: str | None = None
    variant: str | None = None
    color: str | None = None

    # -----------------------------------------------------
    # Vehicle Identification
    # -----------------------------------------------------

    chassis_number: str | None = None
    engine_number: str | None = None
    registration_date: str | None = None

    # -----------------------------------------------------
    # Ownership
    # -----------------------------------------------------

    ownership_type: str | None = "company"
    owner_name: str | None = None

    # -----------------------------------------------------
    # Branch
    # -----------------------------------------------------

    branch: str | None = None

    # -----------------------------------------------------
    # Capacity
    # -----------------------------------------------------

    capacity: float | None = None
    capacity_unit: str | None = "kg"

    # -----------------------------------------------------
    # Fuel
    # -----------------------------------------------------

    fuel_type: str | None = None

    # -----------------------------------------------------
    # Documents
    # -----------------------------------------------------

    rc_document: str | None = None
    insurance_document: str | None = None
    pollution_document: str | None = None
    fitness_document: str | None = None

    # -----------------------------------------------------
    # Document Expiry
    # -----------------------------------------------------

    insurance_expiry: str | None = None
    pollution_expiry: str | None = None
    fitness_expiry: str | None = None
    permit_expiry: str | None = None

    # -----------------------------------------------------
    # Other
    # -----------------------------------------------------

    description: str | None = None

    status: str | None = "active"


# =========================================================
# UPDATE MODEL
# =========================================================

class VehicleUpdate(BaseModel):

    vehicle_number: str | None = None
    vehicle_type: str | None = None

    make: str | None = None
    model: str | None = None
    variant: str | None = None
    color: str | None = None

    chassis_number: str | None = None
    engine_number: str | None = None
    registration_date: str | None = None

    ownership_type: str | None = None
    owner_name: str | None = None

    branch: str | None = None

    capacity: float | None = None
    capacity_unit: str | None = None

    fuel_type: str | None = None

    rc_document: str | None = None
    insurance_document: str | None = None
    pollution_document: str | None = None
    fitness_document: str | None = None

    insurance_expiry: str | None = None
    pollution_expiry: str | None = None
    fitness_expiry: str | None = None
    permit_expiry: str | None = None

    description: str | None = None

    status: str | None = None


# =========================================================
# STATUS MODEL
# =========================================================

class VehicleStatusUpdate(BaseModel):

    status: str


# =========================================================
# CREATE VEHICLE
# =========================================================

@router.post("/create")
def create_vehicle(vehicle: Vehicle):

    # -----------------------------------------------------
    # Normalize vehicle number
    # -----------------------------------------------------

    vehicle_number = (
        vehicle.vehicle_number
        .strip()
        .upper()
    )

    # -----------------------------------------------------
    # Check duplicate vehicle number
    # -----------------------------------------------------

    existing = vehicles_collection.find_one({
        "vehicle_number": vehicle_number
    })

    if existing:

        raise HTTPException(
            status_code=400,
            detail="Vehicle number already exists"
        )

    # -----------------------------------------------------
    # Check chassis number
    # -----------------------------------------------------

    if vehicle.chassis_number:

        existing_chassis = vehicles_collection.find_one({
            "chassis_number": vehicle.chassis_number
        })

        if existing_chassis:

            raise HTTPException(
                status_code=400,
                detail="Chassis number already exists"
            )

    # -----------------------------------------------------
    # Check engine number
    # -----------------------------------------------------

    if vehicle.engine_number:

        existing_engine = vehicles_collection.find_one({
            "engine_number": vehicle.engine_number
        })

        if existing_engine:

            raise HTTPException(
                status_code=400,
                detail="Engine number already exists"
            )

    # -----------------------------------------------------
    # Prepare data
    # -----------------------------------------------------

    data = vehicle.model_dump()

    data["vehicle_number"] = vehicle_number

    data["created_at"] = datetime.utcnow()
    data["updated_at"] = datetime.utcnow()

    # -----------------------------------------------------
    # Insert
    # -----------------------------------------------------

    result = vehicles_collection.insert_one(data)

    return {
        "status": True,
        "message": "Vehicle Created Successfully",
        "vehicle_id": str(result.inserted_id),
        "vehicle_number": vehicle_number
    }


# =========================================================
# GET ALL VEHICLES
# =========================================================

@router.get("/get")
def get_vehicles(

    search: str | None = Query(None),

    status: str | None = Query(None),

    branch: str | None = Query(None),

    vehicle_type: str | None = Query(None)

):

    query = {}

    # -----------------------------------------------------
    # Search
    # -----------------------------------------------------

    if search:

        query["$or"] = [

            {
                "vehicle_number": {
                    "$regex": search,
                    "$options": "i"
                }
            },

            {
                "make": {
                    "$regex": search,
                    "$options": "i"
                }
            },

            {
                "model": {
                    "$regex": search,
                    "$options": "i"
                }
            },

            {
                "chassis_number": {
                    "$regex": search,
                    "$options": "i"
                }
            },

            {
                "engine_number": {
                    "$regex": search,
                    "$options": "i"
                }
            }

        ]

    # -----------------------------------------------------
    # Filters
    # -----------------------------------------------------

    if status:
        query["status"] = status

    if branch:
        query["branch"] = branch

    if vehicle_type:
        query["vehicle_type"] = vehicle_type

    # -----------------------------------------------------
    # Fetch
    # -----------------------------------------------------

    vehicles = list(
        vehicles_collection.find(query).sort(
            "created_at",
            -1
        )
    )

    data = []

    for vehicle in vehicles:

        data.append({

            "id": str(vehicle["_id"]),

            # Basic
            "vehicle_number": vehicle.get(
                "vehicle_number"
            ),

            "vehicle_type": vehicle.get(
                "vehicle_type"
            ),

            "make": vehicle.get("make"),

            "model": vehicle.get("model"),

            "variant": vehicle.get("variant"),

            "color": vehicle.get("color"),

            # Identification
            "chassis_number": vehicle.get(
                "chassis_number"
            ),

            "engine_number": vehicle.get(
                "engine_number"
            ),

            "registration_date": vehicle.get(
                "registration_date"
            ),

            # Ownership
            "ownership_type": vehicle.get(
                "ownership_type"
            ),

            "owner_name": vehicle.get(
                "owner_name"
            ),

            # Branch
            "branch": vehicle.get("branch"),

            # Capacity
            "capacity": vehicle.get(
                "capacity"
            ),

            "capacity_unit": vehicle.get(
                "capacity_unit"
            ),

            # Fuel
            "fuel_type": vehicle.get(
                "fuel_type"
            ),

            # Documents
            "rc_document": vehicle.get(
                "rc_document"
            ),

            "insurance_document": vehicle.get(
                "insurance_document"
            ),

            "pollution_document": vehicle.get(
                "pollution_document"
            ),

            "fitness_document": vehicle.get(
                "fitness_document"
            ),

            # Expiry
            "insurance_expiry": vehicle.get(
                "insurance_expiry"
            ),

            "pollution_expiry": vehicle.get(
                "pollution_expiry"
            ),

            "fitness_expiry": vehicle.get(
                "fitness_expiry"
            ),

            "permit_expiry": vehicle.get(
                "permit_expiry"
            ),

            # Other
            "description": vehicle.get(
                "description"
            ),

            "status": vehicle.get("status"),

            "created_at": vehicle.get(
                "created_at"
            ),

            "updated_at": vehicle.get(
                "updated_at"
            )

        })

    return {
        "status": True,
        "count": len(data),
        "data": data
    }


# =========================================================
# GET ONE VEHICLE
# =========================================================

@router.get("/get-one")
def get_vehicle(

    vehicle_id: str | None = Query(None),

    vehicle_number: str | None = Query(None),

    chassis_number: str | None = Query(None)

):

    # -----------------------------------------------------
    # Validation
    # -----------------------------------------------------

    if not any([
        vehicle_id,
        vehicle_number,
        chassis_number
    ]):

        raise HTTPException(
            status_code=400,
            detail=(
                "Please provide vehicle_id, "
                "vehicle_number or chassis_number"
            )
        )

    # -----------------------------------------------------
    # Build query
    # -----------------------------------------------------

    query = {}

    if vehicle_id:

        if not ObjectId.is_valid(vehicle_id):

            raise HTTPException(
                status_code=400,
                detail="Invalid vehicle_id"
            )

        query["_id"] = ObjectId(vehicle_id)

    elif vehicle_number:

        query["vehicle_number"] = (
            vehicle_number.strip().upper()
        )

    elif chassis_number:

        query["chassis_number"] = chassis_number

    # -----------------------------------------------------
    # Find
    # -----------------------------------------------------

    vehicle = vehicles_collection.find_one(query)

    if not vehicle:

        raise HTTPException(
            status_code=404,
            detail="Vehicle not found"
        )

    # -----------------------------------------------------
    # Response
    # -----------------------------------------------------

    data = {

        "id": str(vehicle["_id"]),

        "vehicle_number": vehicle.get(
            "vehicle_number"
        ),

        "vehicle_type": vehicle.get(
            "vehicle_type"
        ),

        "make": vehicle.get("make"),

        "model": vehicle.get("model"),

        "variant": vehicle.get("variant"),

        "color": vehicle.get("color"),

        "chassis_number": vehicle.get(
            "chassis_number"
        ),

        "engine_number": vehicle.get(
            "engine_number"
        ),

        "registration_date": vehicle.get(
            "registration_date"
        ),

        "ownership_type": vehicle.get(
            "ownership_type"
        ),

        "owner_name": vehicle.get(
            "owner_name"
        ),

        "branch": vehicle.get("branch"),

        "capacity": vehicle.get(
            "capacity"
        ),

        "capacity_unit": vehicle.get(
            "capacity_unit"
        ),

        "fuel_type": vehicle.get(
            "fuel_type"
        ),

        "rc_document": vehicle.get(
            "rc_document"
        ),

        "insurance_document": vehicle.get(
            "insurance_document"
        ),

        "pollution_document": vehicle.get(
            "pollution_document"
        ),

        "fitness_document": vehicle.get(
            "fitness_document"
        ),

        "insurance_expiry": vehicle.get(
            "insurance_expiry"
        ),

        "pollution_expiry": vehicle.get(
            "pollution_expiry"
        ),

        "fitness_expiry": vehicle.get(
            "fitness_expiry"
        ),

        "permit_expiry": vehicle.get(
            "permit_expiry"
        ),

        "description": vehicle.get(
            "description"
        ),

        "status": vehicle.get("status"),

        "created_at": vehicle.get(
            "created_at"
        ),

        "updated_at": vehicle.get(
            "updated_at"
        )

    }

    return {
        "status": True,
        "data": data
    }


# =========================================================
# UPDATE VEHICLE
# =========================================================

@router.post("/update/{vehicle_id}")
def update_vehicle(

    vehicle_id: str,

    vehicle: VehicleUpdate

):

    # -----------------------------------------------------
    # Validate ObjectId
    # -----------------------------------------------------

    if not ObjectId.is_valid(vehicle_id):

        raise HTTPException(
            status_code=400,
            detail="Invalid vehicle_id"
        )

    object_id = ObjectId(vehicle_id)

    # -----------------------------------------------------
    # Check vehicle
    # -----------------------------------------------------

    existing = vehicles_collection.find_one({
        "_id": object_id
    })

    if not existing:

        raise HTTPException(
            status_code=404,
            detail="Vehicle not found"
        )

    # -----------------------------------------------------
    # Prepare update
    # -----------------------------------------------------

    update_data = vehicle.model_dump(
        exclude_unset=True
    )

    # -----------------------------------------------------
    # Vehicle number
    # -----------------------------------------------------

    if update_data.get("vehicle_number"):

        update_data["vehicle_number"] = (
            update_data["vehicle_number"]
            .strip()
            .upper()
        )

        duplicate = vehicles_collection.find_one({

            "vehicle_number":
                update_data["vehicle_number"],

            "_id": {
                "$ne": object_id
            }

        })

        if duplicate:

            raise HTTPException(
                status_code=400,
                detail="Vehicle number already exists"
            )

    # -----------------------------------------------------
    # Chassis number
    # -----------------------------------------------------

    if update_data.get("chassis_number"):

        duplicate = vehicles_collection.find_one({

            "chassis_number":
                update_data["chassis_number"],

            "_id": {
                "$ne": object_id
            }

        })

        if duplicate:

            raise HTTPException(
                status_code=400,
                detail="Chassis number already exists"
            )

    # -----------------------------------------------------
    # Engine number
    # -----------------------------------------------------

    if update_data.get("engine_number"):

        duplicate = vehicles_collection.find_one({

            "engine_number":
                update_data["engine_number"],

            "_id": {
                "$ne": object_id
            }

        })

        if duplicate:

            raise HTTPException(
                status_code=400,
                detail="Engine number already exists"
            )

    # -----------------------------------------------------
    # Updated timestamp
    # -----------------------------------------------------

    update_data["updated_at"] = datetime.utcnow()

    # -----------------------------------------------------
    # Update MongoDB
    # -----------------------------------------------------

    vehicles_collection.update_one(

        {
            "_id": object_id
        },

        {
            "$set": update_data
        }

    )

    return {

        "status": True,

        "message": "Vehicle Updated Successfully",

        "vehicle_id": vehicle_id,

        "vehicle_number": update_data.get(
            "vehicle_number",
            existing.get("vehicle_number")
        )

    }


# =========================================================
# CHANGE VEHICLE STATUS
# POST
# =========================================================

@router.post("/status/{vehicle_id}")
def change_vehicle_status(

    vehicle_id: str,

    data: VehicleStatusUpdate

):

    # -----------------------------------------------------
    # Validate ObjectId
    # -----------------------------------------------------

    if not ObjectId.is_valid(vehicle_id):

        raise HTTPException(
            status_code=400,
            detail="Invalid vehicle_id"
        )

    # -----------------------------------------------------
    # Allowed status
    # -----------------------------------------------------

    allowed_status = [
        "active",
        "inactive",
        "maintenance"
    ]

    if data.status not in allowed_status:

        raise HTTPException(
            status_code=400,
            detail=(
                f"Status must be one of "
                f"{allowed_status}"
            )
        )

    # -----------------------------------------------------
    # Update
    # -----------------------------------------------------

    result = vehicles_collection.update_one(

        {
            "_id": ObjectId(vehicle_id)
        },

        {
            "$set": {

                "status": data.status,

                "updated_at": datetime.utcnow()

            }
        }

    )

    if result.matched_count == 0:

        raise HTTPException(
            status_code=404,
            detail="Vehicle not found"
        )

    return {

        "status": True,

        "message": "Vehicle Status Updated Successfully",

        "vehicle_id": vehicle_id,

        "status": data.status

    }