from fastapi import (
    APIRouter,
    WebSocket,
    WebSocketDisconnect,
    Depends,
    HTTPException,
)

from pydantic import BaseModel, Field
from datetime import datetime, timezone
from math import radians, sin, cos, sqrt, atan2

from database import db
from routes.auth import get_current_user


router = APIRouter(
    prefix="/location",
    tags=["Live Location"]
)


# ============================================================
# MongoDB Collections
# ============================================================

user_locations_collection = db["user_locations"]

location_history_collection = db["user_location_history"]


# ============================================================
# Configuration
# ============================================================

# History me new location save karne ke liye minimum distance
# 50 meters
HISTORY_DISTANCE_METERS = 50.0


# ============================================================
# MongoDB Indexes
# ============================================================

try:
    user_locations_collection.create_index(
        "user_id",
        unique=True
    )
except Exception as e:
    print("user_locations index error:", e)




try:
    location_history_collection.create_index(
        [
            ("user_id", 1),
            ("recorded_at", 1)
        ]
    )
except Exception as e:
    print("history compound index error:", e)


# History 2 days ke baad automatically delete
try:
    location_history_collection.create_index(
        "recorded_at",
        expireAfterSeconds=172800
    )
except Exception as e:
    print("history TTL index error:", e)


# ============================================================
# Pydantic Model
# ============================================================

class LocationUpdate(BaseModel):

    latitude: float = Field(
        ...,
        ge=-90,
        le=90
    )

    longitude: float = Field(
        ...,
        ge=-180,
        le=180
    )

    accuracy: float | None = None

    speed: float | None = None

    heading: float | None = None


# ============================================================
# Get User ID
# ============================================================

def get_user_id_from_current_user(current_user):

    if not current_user:
        raise HTTPException(
            status_code=401,
            detail="User authentication failed"
        )

    if current_user.get("_id") is not None:
        return str(current_user["_id"])

    if current_user.get("user_id") is not None:
        return str(current_user["user_id"])

    if current_user.get("id") is not None:
        return str(current_user["id"])

    raise HTTPException(
        status_code=401,
        detail="User ID not found in authenticated user"
    )


# ============================================================
# Distance Calculation
# ============================================================

def calculate_distance_meters(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float
) -> float:

    """
    Calculate distance between two GPS coordinates
    using Haversine formula.

    Returns:
        Distance in meters
    """

    earth_radius = 6371000  # meters

    lat1_rad = radians(lat1)
    lat2_rad = radians(lat2)

    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)

    a = (
        sin(dlat / 2) ** 2
        +
        cos(lat1_rad)
        * cos(lat2_rad)
        * sin(dlon / 2) ** 2
    )

    c = 2 * atan2(
        sqrt(a),
        sqrt(1 - a)
    )

    return earth_radius * c


# ============================================================
# Save History If User Moved 50 Meters
# ============================================================

def save_history_if_needed(
    user_id: str,
    latitude: float,
    longitude: float,
    accuracy=None,
    speed=None,
    heading=None,
    recorded_at=None
):
    """
    History me location tabhi save karega jab:

    1. User ki first location ho
       OR
    2. Last history location se >= 50 meters movement ho

    Returns:

    {
        "saved": True/False,
        "distance": distance_from_last_history
    }
    """

    if recorded_at is None:
        recorded_at = datetime.now(timezone.utc)

    # Last history location
    last_history = location_history_collection.find_one(
        {
            "user_id": user_id
        },
        sort=[
            ("recorded_at", -1)
        ]
    )

    # ========================================================
    # First location
    # ========================================================

    if not last_history:

        location_history_collection.insert_one(
            {
                "user_id": user_id,
                "latitude": latitude,
                "longitude": longitude,
                "accuracy": accuracy,
                "speed": speed,
                "heading": heading,
                "recorded_at": recorded_at,
            }
        )

        return {
            "saved": True,
            "distance": None
        }

    # ========================================================
    # Calculate distance from LAST HISTORY POINT
    # ========================================================

    distance = calculate_distance_meters(
        last_history["latitude"],
        last_history["longitude"],
        latitude,
        longitude
    )

    # ========================================================
    # Save only after 50 meters
    # ========================================================

    if distance >= HISTORY_DISTANCE_METERS:

        location_history_collection.insert_one(
            {
                "user_id": user_id,
                "latitude": latitude,
                "longitude": longitude,
                "accuracy": accuracy,
                "speed": speed,
                "heading": heading,
                "recorded_at": recorded_at,
            }
        )

        return {
            "saved": True,
            "distance": distance
        }

    # ========================================================
    # Less than 50 meters
    # ========================================================

    return {
        "saved": False,
        "distance": distance
    }


# ============================================================
# WebSocket Connection Manager
# ============================================================

class ConnectionManager:

    def __init__(self):

        # Currently connected WebSockets
        self.connections: dict[str, list[WebSocket]] = {}

        # Users who were disconnected
        self.disconnected_users: set[str] = set()


    # ========================================================
    # CONNECT
    # ========================================================

    async def connect(
        self,
        user_id: str,
        websocket: WebSocket
    ):

        await websocket.accept()

        if user_id not in self.connections:

            self.connections[user_id] = []

        self.connections[user_id].append(
            websocket
        )

        # Remove from disconnected list
        self.disconnected_users.discard(
            user_id
        )

        # Update MongoDB
        user_locations_collection.update_one(

            {
                "user_id": user_id
            },

            {
                "$set": {
                    "connection_status": "connected",
                    "connected_at": datetime.now(timezone.utc),
                    "updated_at": datetime.now(timezone.utc)
                }
            },

            upsert=True
        )

        print(
            f"WebSocket connected: {user_id}"
        )


    # ========================================================
    # DISCONNECT
    # ========================================================

    def disconnect(
        self,
        user_id: str,
        websocket: WebSocket
    ):

        if user_id not in self.connections:
            return

        if websocket in self.connections[user_id]:

            self.connections[user_id].remove(
                websocket
            )

        # Agar user ki koi WebSocket connection nahi bachi
        if not self.connections[user_id]:

            del self.connections[user_id]

            # Add to disconnected
            self.disconnected_users.add(
                user_id
            )

            # Update MongoDB
            user_locations_collection.update_one(

                {
                    "user_id": user_id
                },

                {
                    "$set": {

                        "connection_status":
                            "disconnected",

                        "disconnected_at":
                            datetime.now(timezone.utc),

                        "updated_at":
                            datetime.now(timezone.utc)

                    }
                }
            )

        print(
            f"WebSocket disconnected: {user_id}"
        )


    # ========================================================
    # GET CONNECTED USERS
    # ========================================================

    def get_connected_users(self):

        return list(
            self.connections.keys()
        )


    # ========================================================
    # GET DISCONNECTED USERS
    # ========================================================

    def get_disconnected_users(self):

        return list(
            self.disconnected_users
        )


    # ========================================================
    # GET CONNECTION STATUS
    # ========================================================

    def get_connection_status(self):

        connected = self.get_connected_users()

        disconnected = self.get_disconnected_users()

        return {

            "connected_users": connected,

            "disconnected_users": disconnected,

            "connected_count":
                len(connected),

            "disconnected_count":
                len(disconnected)

        }


    # ========================================================
    # SEND TO USER
    # ========================================================

    async def send_to_user(
        self,
        user_id: str,
        data: dict
    ):

        if user_id not in self.connections:
            return

        dead_connections = []

        for websocket in list(
            self.connections[user_id]
        ):

            try:

                await websocket.send_json(
                    data
                )

            except Exception:

                dead_connections.append(
                    websocket
                )

        for websocket in dead_connections:

            self.disconnect(
                user_id,
                websocket
            )


    # ========================================================
    # BROADCAST
    # ========================================================

    async def broadcast(
        self,
        data: dict
    ):

        for user_id in list(
            self.connections.keys()
        ):

            await self.send_to_user(
                user_id,
                data
            )

# Global WebSocket manager
manager = ConnectionManager()


# ============================================================
# START TRACKING
# ============================================================

@router.post("/start")
async def start_tracking(
    current_user=Depends(get_current_user)
):

    user_id = get_user_id_from_current_user(
        current_user
    )

    now = datetime.now(timezone.utc)

    user_locations_collection.update_one(

        {
            "user_id": user_id
        },

        {
            "$set": {

                "user_id": user_id,

                "tracking": True,

                "started_at": now,

                "updated_at": now,

            }
        },

        upsert=True
    )

    return {

        "success": True,

        "message": "Location tracking started",

        "user_id": user_id

    }


# ============================================================
# STOP TRACKING
# ============================================================

@router.post("/stop")
async def stop_tracking(
    current_user=Depends(get_current_user)
):

    user_id = get_user_id_from_current_user(
        current_user
    )

    now = datetime.now(timezone.utc)

    result = user_locations_collection.update_one(

        {
            "user_id": user_id
        },

        {
            "$set": {

                "tracking": False,

                "stopped_at": now,

                "updated_at": now,

            }
        }
    )

    return {

        "success": True,

        "message": "Location tracking stopped",

        "user_id": user_id,

        "updated": result.modified_count > 0

    }


# ============================================================
# UPDATE LOCATION API
# ============================================================

@router.post("/update")
async def update_location(

    location: LocationUpdate,

    current_user=Depends(get_current_user)

):

    user_id = get_user_id_from_current_user(
        current_user
    )

    now = datetime.now(timezone.utc)

    # ========================================================
    # Check tracking status
    # ========================================================

    existing = user_locations_collection.find_one(
        {
            "user_id": user_id
        }
    )

    if existing and existing.get("tracking") is False:

        raise HTTPException(
            status_code=400,
            detail="Location tracking is not started"
        )

    # ========================================================
    # Latest location data
    # ========================================================

    location_data = {

        "user_id": user_id,

        "latitude": location.latitude,

        "longitude": location.longitude,

        "accuracy": location.accuracy,

        "speed": location.speed,

        "heading": location.heading,

        "tracking": True,

        "updated_at": now,

    }

    # ========================================================
    # Update current/live location
    # ========================================================

    user_locations_collection.update_one(

        {
            "user_id": user_id
        },

        {
            "$set": location_data
        },

        upsert=True
    )

    # ========================================================
    # Save history only after 50 meters
    # ========================================================

    history_result = save_history_if_needed(

        user_id=user_id,

        latitude=location.latitude,

        longitude=location.longitude,

        accuracy=location.accuracy,

        speed=location.speed,

        heading=location.heading,

        recorded_at=now

    )

    # ========================================================
    # Live WebSocket data
    # ========================================================

    live_data = {

        "type": "location_update",

        "user_id": user_id,

        "latitude": location.latitude,

        "longitude": location.longitude,

        "accuracy": location.accuracy,

        "speed": location.speed,

        "heading": location.heading,

        "updated_at": now.isoformat(),

        "history_saved": history_result["saved"],

        "distance_from_last_history": (
            history_result["distance"]
        ),

    }

    # ========================================================
    # Broadcast live location
    # ========================================================

    await manager.broadcast(
        live_data
    )

    return {

        "success": True,

        "message": "Location updated",

        "data": live_data

    }


# ============================================================
# GET MY CURRENT LOCATION
# ============================================================

@router.get("/me")
async def get_my_location(
    current_user=Depends(get_current_user)
):

    user_id = get_user_id_from_current_user(
        current_user
    )

    location = user_locations_collection.find_one(

        {
            "user_id": user_id
        },

        {
            "_id": 0
        }

    )

    return {

        "success": True,

        "data": location

    }


# ============================================================
# GET ALL USER LOCATIONS
# ============================================================

@router.get("/users")
async def get_all_user_locations(
    current_user=Depends(get_current_user)
):

    get_user_id_from_current_user(
        current_user
    )

    locations = []

    for location in user_locations_collection.find({}):

        location["_id"] = str(
            location["_id"]
        )

        locations.append(
            location
        )

    return {

        "success": True,

        "count": len(locations),

        "data": locations

    }


# ============================================================
# GET SPECIFIC USER LOCATION
# ============================================================

@router.get("/{user_id}")
async def get_user_location(

    user_id: str,

    current_user=Depends(get_current_user)

):

    get_user_id_from_current_user(
        current_user
    )

    location = user_locations_collection.find_one(

        {
            "user_id": user_id
        },

        {
            "_id": 0
        }

    )

    if not location:

        raise HTTPException(

            status_code=404,

            detail="Location not found"

        )

    return {

        "success": True,

        "data": location

    }


# ============================================================
# GET LOCATION HISTORY
# ============================================================

@router.get("/history/{user_id}")
async def get_location_history(

    user_id: str,

    current_user=Depends(get_current_user)

):

    get_user_id_from_current_user(
        current_user
    )

    history = list(

        location_history_collection.find(

            {
                "user_id": user_id
            },

            {
                "_id": 0
            }

        ).sort(

            "recorded_at",
            1

        )

    )

    return {

        "success": True,

        "count": len(history),

        "data": history

    }


@router.get("/connections")
async def get_connections(
    current_user=Depends(get_current_user)
):

    get_user_id_from_current_user(
        current_user
    )

    status = manager.get_connection_status()

    return {

        "success": True,

        "data": status

    }


# ============================================================
# WEBSOCKET LIVE LOCATION
# ============================================================

@router.websocket("/ws/{user_id}")
async def location_websocket(

    websocket: WebSocket,

    user_id: str

):

    await manager.connect(
        user_id,
        websocket
    )

    try:

        while True:

            # =================================================
            # Receive GPS data
            # =================================================

            data = await websocket.receive_json()

            latitude = data.get(
                "latitude"
            )

            longitude = data.get(
                "longitude"
            )

            # =================================================
            # Required fields
            # =================================================

            if latitude is None or longitude is None:

                await websocket.send_json({

                    "success": False,

                    "message":
                        "latitude and longitude are required"

                })

                continue

            # =================================================
            # Convert to float
            # =================================================

            try:

                latitude = float(
                    latitude
                )

                longitude = float(
                    longitude
                )

            except (
                TypeError,
                ValueError
            ):

                await websocket.send_json({

                    "success": False,

                    "message":
                        "latitude and longitude must be numbers"

                })

                continue

            # =================================================
            # Validate latitude
            # =================================================

            if not -90 <= latitude <= 90:

                await websocket.send_json({

                    "success": False,

                    "message":
                        "Invalid latitude"

                })

                continue

            # =================================================
            # Validate longitude
            # =================================================

            if not -180 <= longitude <= 180:

                await websocket.send_json({

                    "success": False,

                    "message":
                        "Invalid longitude"

                })

                continue

            # =================================================
            # Additional GPS data
            # =================================================

            now = datetime.now(timezone.utc)

            accuracy = data.get(
                "accuracy"
            )

            speed = data.get(
                "speed"
            )

            heading = data.get(
                "heading"
            )

            # =================================================
            # Update latest location
            # =================================================

            user_locations_collection.update_one(

                {
                    "user_id": user_id
                },

                {

                    "$set": {

                        "user_id": user_id,

                        "latitude": latitude,

                        "longitude": longitude,

                        "accuracy": accuracy,

                        "speed": speed,

                        "heading": heading,

                        "tracking": True,

                        "updated_at": now,

                    }

                },

                upsert=True

            )

            # =================================================
            # HISTORY
            #
            # IMPORTANT:
            # History only saves when distance from the
            # previous HISTORY point >= 50 meters.
            # =================================================

            history_result = save_history_if_needed(

                user_id=user_id,

                latitude=latitude,

                longitude=longitude,

                accuracy=accuracy,

                speed=speed,

                heading=heading,

                recorded_at=now

            )

            # =================================================
            # Live data
            # =================================================

            live_data = {

                "type":
                    "location_update",

                "user_id":
                    user_id,

                "latitude":
                    latitude,

                "longitude":
                    longitude,

                "accuracy":
                    accuracy,

                "speed":
                    speed,

                "heading":
                    heading,

                "updated_at":
                    now.isoformat(),

                "history_saved":
                    history_result["saved"],

                "distance_from_last_history":
                    history_result["distance"],

            }

            # =================================================
            # Broadcast to all connected clients
            # =================================================

            await manager.broadcast(
                live_data
            )

            # =================================================
            # Send response to sender
            # =================================================

            await websocket.send_json({

                "success": True,

                "message":
                    "Location received",

                "data":
                    live_data

            })

    # ========================================================
    # WebSocket disconnected
    # ========================================================

    except WebSocketDisconnect:

        manager.disconnect(
            user_id,
            websocket
        )

    # ========================================================
    # Other WebSocket errors
    # ========================================================

    except Exception as e:

        print(
            "WebSocket error:",
            e
        )

        manager.disconnect(
            user_id,
            websocket
        )


# ============================================================
# GET AVAILABLE HISTORY DATES
# ============================================================

@router.get("/history/{user_id}/dates")
async def get_history_dates(
    user_id: str,
    current_user=Depends(get_current_user)
):

    get_user_id_from_current_user(current_user)

    pipeline = [
        {
            "$match": {
                "user_id": user_id
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$recorded_at",
                        "timezone": "UTC"
                    }
                }
            }
        },
        {
            "$group": {
                "_id": "$date"
            }
        },
        {
            "$sort": {
                "_id": -1
            }
        }
    ]

    result = list(
        location_history_collection.aggregate(
            pipeline
        )
    )

    dates = [
        item["_id"]
        for item in result
    ]

    return {
        "success": True,
        "user_id": user_id,
        "count": len(dates),
        "dates": dates
    }