from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from datetime import datetime
from bson import ObjectId
from utils import convert_utc_to_ist

from database import (
    vehicles_collection,
    orders_collection,
    customers_collection,
    products_collection,
    product_variants_collection,
    active_routes_collection
)


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

    return convert_utc_to_ist({
        "status": True,
        "message": "Vehicle Created Successfully",
        "vehicle_id": str(result.inserted_id),
        "vehicle_number": vehicle_number
    })


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

    return convert_utc_to_ist({
        "status": True,
        "count": len(data),
        "data": data
    })


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

    return convert_utc_to_ist({
        "status": True,
        "data": data
    })


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

    return convert_utc_to_ist({

        "status": True,

        "message": "Vehicle Updated Successfully",

        "vehicle_id": vehicle_id,

        "vehicle_number": update_data.get(
            "vehicle_number",
            existing.get("vehicle_number")
        )

    })


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

    return convert_utc_to_ist({

        "status": True,

        "message": "Vehicle Status Updated Successfully",

        "vehicle_id": vehicle_id,

        "status": data.status
    })

import math

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    dLat = math.radians(lat2 - lat1)
    dLon = math.radians(lon2 - lon1)
    a = math.sin(dLat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dLon / 2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

# =========================================================
# ROUTE: START ACTIVE TRIP SESSION
# POST /vehicles/{vehicle_id}/start-trip
# =========================================================

@router.post("/{vehicle_id}/start-trip")
def start_vehicle_trip(vehicle_id: str):
    """
    Locks in all 'Out for Delivery' orders into a persistent Trip Session.
    Sorts them automatically using GPS (Nearest Neighbor).
    """
    if not ObjectId.is_valid(vehicle_id):
        raise HTTPException(status_code=400, detail="Invalid vehicle_id")
    v_oid = ObjectId(vehicle_id)

    veh = vehicles_collection.find_one({"_id": v_oid})
    if not veh:
        raise HTTPException(status_code=404, detail="Vehicle not found")

    # Check if a trip is already in progress
    existing_trip = active_routes_collection.find_one({"vehicle_id": v_oid, "status": "in_progress"})
    if existing_trip:
        raise HTTPException(status_code=400, detail="A trip is already in progress for this vehicle.")

    # Fetch all Out for Delivery orders assigned to this vehicle
    query = {
        "type": "sale",
        "vehicle_id": v_oid,
        "status": "Out for Delivery",
        "record_status": "active"
    }
    orders = list(orders_collection.find(query))

    if not orders:
        raise HTTPException(status_code=400, detail="No 'Out for Delivery' orders found for this vehicle to start a trip.")

    # Sort using Nearest Neighbor
    located_orders = []
    unlocated_orders = []
    for o in orders:
        loc = None
        cid = o.get("customer_id")
        if cid:
            if isinstance(cid, str) and ObjectId.is_valid(cid):
                cid = ObjectId(cid)
            cust = customers_collection.find_one({"_id": cid})
            if cust and cust.get("location") and cust["location"].get("lat") is not None:
                loc = (cust["location"]["lat"], cust["location"]["lng"])
        
        if loc:
            located_orders.append({"order": o, "loc": loc})
        else:
            unlocated_orders.append(o)

    optimized_orders = []
    if located_orders:
        current = located_orders.pop(0)
        optimized_orders.append(current["order"])
        while located_orders:
            nearest_idx = 0
            min_dist = float("inf")
            for i, target in enumerate(located_orders):
                dist = haversine(current["loc"][0], current["loc"][1], target["loc"][0], target["loc"][1])
                if dist < min_dist:
                    min_dist = dist
                    nearest_idx = i
            current = located_orders.pop(nearest_idx)
            optimized_orders.append(current["order"])

    final_sequence = optimized_orders + unlocated_orders

    # Build the manifest data to persist
    customer_stops = []
    trip_demand = {}

    for o in final_sequence:
        cid = o.get("customer_id")
        cust = None
        if cid:
            if isinstance(cid, str) and ObjectId.is_valid(cid):
                cid = ObjectId(cid)
            cust = customers_collection.find_one({"_id": cid})

        customer_stops.append({
            "order_id": str(o["_id"]),
            "order_no": o.get("order_no"),
            "customer_name": cust.get("name") if cust else "N/A",
            "customer_phone": cust.get("phone") or cust.get("mobile") if cust else None,
            "address": cust.get("address") or cust.get("shipping_address") or cust.get("billing_address") if cust else None,
            "payment_mode": o.get("payment_mode"),
            "grand_total": round(float(o.get("grand_total", 0.0)), 2),
            "item_count": len(o.get("items", [])),
            "manifest_no": o.get("manifest_no"),
        })

        for it in o.get("items", []):
            p_id = it.get("product_id")
            v_id = it.get("variant_id")
            if isinstance(p_id, str) and ObjectId.is_valid(p_id):
                p_id = ObjectId(p_id)
            if isinstance(v_id, str) and ObjectId.is_valid(v_id):
                v_id = ObjectId(v_id)
            qty = float(it.get("quantity", 0))
            if p_id and v_id and qty > 0:
                trip_demand[(p_id, v_id)] = trip_demand.get((p_id, v_id), 0.0) + qty

    consolidated_items = []
    for (p_id, v_id), total_qty in trip_demand.items():
        prod = products_collection.find_one({"_id": p_id})
        var = product_variants_collection.find_one({"_id": v_id})
        consolidated_items.append({
            "product_id": str(p_id),
            "product_name": prod.get("name") if prod else str(p_id),
            "variant_id": str(v_id),
            "variant_name": var.get("name") if var else str(v_id),
            "sku": var.get("sku") if var else "",
            "total_quantity": round(total_qty, 4),
        })

    total_trip_amount = round(sum(float(o.get("grand_total", 0.0) or 0.0) for o in final_sequence), 2)

    trip_doc = {
        "vehicle_id": v_oid,
        "status": "in_progress",
        "started_at": datetime.utcnow(),
        "customer_stops": customer_stops,
        "consolidated_items": consolidated_items,
        "total_trip_amount": total_trip_amount,
        "total_orders": len(final_sequence)
    }
    active_routes_collection.insert_one(trip_doc)

    return convert_utc_to_ist({
        "success": True,
        "message": "Trip successfully started and route sequence locked.",
        "trip_id": str(trip_doc["_id"])
    })


# =========================================================
# ROUTE: GET ACTIVE ROUTE
# GET /vehicles/{vehicle_id}/active-route
# =========================================================

@router.get("/{vehicle_id}/active-route")
def get_vehicle_active_route(vehicle_id: str):
    """
    Returns the locked Trip Session for the vehicle.
    Dynamically injects the current order status so delivered orders are marked accurately.
    """
    if not ObjectId.is_valid(vehicle_id):
        raise HTTPException(status_code=400, detail="Invalid vehicle_id")
    v_oid = ObjectId(vehicle_id)

    veh = vehicles_collection.find_one({"_id": v_oid})
    if not veh:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    veh["_id"] = str(veh["_id"])

    trip = active_routes_collection.find_one({"vehicle_id": v_oid, "status": "in_progress"})
    if not trip:
        return convert_utc_to_ist({
            "success": True,
            "message": "No active trip for this vehicle.",
            "data": {
                "vehicle": veh,
                "consolidated_items": [],
                "customer_stops": [],
                "total_trip_amount": 0.0,
                "total_orders": 0
            }
        })

    # Update real-time status of orders in the sequence
    for stop in trip.get("customer_stops", []):
        o_id = stop.get("order_id")
        if o_id and ObjectId.is_valid(o_id):
            o_doc = orders_collection.find_one({"_id": ObjectId(o_id)})
            stop["current_status"] = o_doc.get("status") if o_doc else "Unknown"

    trip["_id"] = str(trip["_id"])
    trip["vehicle_id"] = str(trip["vehicle_id"])
    trip["vehicle"] = veh

    return convert_utc_to_ist({
        "success": True,
        "data": trip
    })


# =========================================================
# ROUTE: REORDER ACTIVE ROUTE
# POST /vehicles/{vehicle_id}/reorder-route
# =========================================================

class RouteReorderRequest(BaseModel):
    order_ids: list[str]

@router.post("/{vehicle_id}/reorder-route")
def reorder_vehicle_active_route(vehicle_id: str, data: RouteReorderRequest):
    """
    Reorders the customer stops inside the active Trip Session.
    """
    if not ObjectId.is_valid(vehicle_id):
        raise HTTPException(status_code=400, detail="Invalid vehicle_id")
    v_oid = ObjectId(vehicle_id)

    trip = active_routes_collection.find_one({"vehicle_id": v_oid, "status": "in_progress"})
    if not trip:
        raise HTTPException(status_code=404, detail="No active trip found for this vehicle to reorder.")

    current_stops = trip.get("customer_stops", [])
    stops_map = {stop["order_id"]: stop for stop in current_stops}

    new_stops = []
    for oid in data.order_ids:
        if oid in stops_map:
            new_stops.append(stops_map[oid])

    if len(new_stops) != len(current_stops):
        raise HTTPException(status_code=400, detail="Order IDs provided do not perfectly match the active route stops.")

    active_routes_collection.update_one(
        {"_id": trip["_id"]},
        {"$set": {"customer_stops": new_stops}}
    )

    return convert_utc_to_ist({
        "success": True,
        "message": "Route sequence updated successfully."
    })


# =========================================================
# ROUTE: END ACTIVE TRIP
# POST /vehicles/{vehicle_id}/end-trip
# =========================================================

@router.post("/{vehicle_id}/end-trip")
def end_vehicle_trip(vehicle_id: str):
    """
    Marks the active trip session as completed.
    """
    if not ObjectId.is_valid(vehicle_id):
        raise HTTPException(status_code=400, detail="Invalid vehicle_id")
    v_oid = ObjectId(vehicle_id)

    result = active_routes_collection.update_one(
        {"vehicle_id": v_oid, "status": "in_progress"},
        {"$set": {"status": "completed", "ended_at": datetime.utcnow()}}
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="No active trip found to end.")

    return convert_utc_to_ist({
        "success": True,
        "message": "Trip successfully ended."
    })