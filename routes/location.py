from fastapi import (
    APIRouter,
    WebSocket,
    WebSocketDisconnect,
    Depends,
    HTTPException,
)
from pydantic import BaseModel, Field
from datetime import datetime, timezone
from database import db
from routes.auth import get_current_user

router = APIRouter(prefix="/location", tags=["Live Location"])

user_locations_collection = db["user_locations"]
location_history_collection = db["user_location_history"]

# Indexes
try:
    user_locations_collection.create_index("user_id", unique=True)
except Exception as e:
    print("user_locations index error:", e)

try:
    location_history_collection.create_index([("user_id", 1), ("recorded_at", 1)])
except Exception as e:
    print("history compound index error:", e)

try:
    location_history_collection.create_index("recorded_at", expireAfterSeconds=2592000)  # 30 days
except Exception as e:
    print("history TTL index error:", e)


class LocationUpdate(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    accuracy: float | None = None
    speed: float | None = None
    heading: float | None = None


def get_user_id_from_current_user(current_user):
    if not current_user:
        raise HTTPException(status_code=401, detail="User authentication failed")

    if current_user.get("_id") is not None:
        return str(current_user["_id"])
    if current_user.get("user_id") is not None:
        return str(current_user["user_id"])
    if current_user.get("id") is not None:
        return str(current_user["id"])

    raise HTTPException(status_code=401, detail="User ID not found in authenticated user")


class ConnectionManager:
    def __init__(self):
        self.connections: dict[str, list[WebSocket]] = {}

    async def connect(self, user_id: str, websocket: WebSocket):
        await websocket.accept()
        if user_id not in self.connections:
            self.connections[user_id] = []
        self.connections[user_id].append(websocket)
        print(f"WebSocket connected: {user_id}")

    def disconnect(self, user_id: str, websocket: WebSocket):
        if user_id not in self.connections:
            return
        if websocket in self.connections[user_id]:
            self.connections[user_id].remove(websocket)
        if not self.connections[user_id]:
            del self.connections[user_id]
        print(f"WebSocket disconnected: {user_id}")

    async def send_to_user(self, user_id: str, data: dict):
        if user_id not in self.connections:
            return
        dead = []
        for ws in list(self.connections[user_id]):
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(user_id, ws)

    async def broadcast(self, data: dict):
        for user_id in list(self.connections.keys()):
            await self.send_to_user(user_id, data)


manager = ConnectionManager()


@router.post("/start")
async def start_tracking(current_user=Depends(get_current_user)):
    user_id = get_user_id_from_current_user(current_user)
    now = datetime.now(timezone.utc)

    user_locations_collection.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "user_id": user_id,
                "tracking": True,
                "started_at": now,
                "updated_at": now,
            }
        },
        upsert=True,
    )

    return {"success": True, "message": "Location tracking started", "user_id": user_id}


@router.post("/stop")
async def stop_tracking(current_user=Depends(get_current_user)):
    user_id = get_user_id_from_current_user(current_user)
    now = datetime.now(timezone.utc)

    result = user_locations_collection.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "tracking": False,
                "stopped_at": now,
                "updated_at": now,
            }
        },
    )

    return {
        "success": True,
        "message": "Location tracking stopped",
        "user_id": user_id,
        "updated": result.modified_count > 0,
    }


@router.post("/update")
async def update_location(
    location: LocationUpdate,
    current_user=Depends(get_current_user),
):
    user_id = get_user_id_from_current_user(current_user)
    now = datetime.now(timezone.utc)

    existing = user_locations_collection.find_one({"user_id": user_id})
    if existing and existing.get("tracking") is False:
        raise HTTPException(status_code=400, detail="Location tracking is not started")

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

    user_locations_collection.update_one(
        {"user_id": user_id},
        {"$set": location_data},
        upsert=True,
    )

    location_history_collection.insert_one(
        {
            "user_id": user_id,
            "latitude": location.latitude,
            "longitude": location.longitude,
            "accuracy": location.accuracy,
            "speed": location.speed,
            "heading": location.heading,
            "recorded_at": now,
        }
    )

    live_data = {
        "type": "location_update",
        "user_id": user_id,
        "latitude": location.latitude,
        "longitude": location.longitude,
        "accuracy": location.accuracy,
        "speed": location.speed,
        "heading": location.heading,
        "updated_at": now.isoformat(),
    }

    await manager.broadcast(live_data)

    return {"success": True, "message": "Location updated", "data": live_data}


@router.get("/me")
async def get_my_location(current_user=Depends(get_current_user)):
    user_id = get_user_id_from_current_user(current_user)
    location = user_locations_collection.find_one({"user_id": user_id}, {"_id": 0})
    return {"success": True, "data": location}


@router.get("/users")
async def get_all_user_locations(current_user=Depends(get_current_user)):
    get_user_id_from_current_user(current_user)

    locations = []
    for location in user_locations_collection.find({}):
        location["_id"] = str(location["_id"])
        locations.append(location)

    return {"success": True, "count": len(locations), "data": locations}


@router.get("/{user_id}")
async def get_user_location(user_id: str, current_user=Depends(get_current_user)):
    get_user_id_from_current_user(current_user)

    location = user_locations_collection.find_one({"user_id": user_id}, {"_id": 0})
    if not location:
        raise HTTPException(status_code=404, detail="Location not found")

    return {"success": True, "data": location}


@router.get("/history/{user_id}")
async def get_location_history(user_id: str, current_user=Depends(get_current_user)):
    get_user_id_from_current_user(current_user)

    history = list(
        location_history_collection.find({"user_id": user_id}, {"_id": 0}).sort(
            "recorded_at", 1
        )
    )

    return {"success": True, "count": len(history), "data": history}


@router.websocket("/ws/{user_id}")
async def location_websocket(websocket: WebSocket, user_id: str):
    await manager.connect(user_id, websocket)

    try:
        while True:
            data = await websocket.receive_json()

            latitude = data.get("latitude")
            longitude = data.get("longitude")

            if latitude is None or longitude is None:
                await websocket.send_json(
                    {"success": False, "message": "latitude and longitude are required"}
                )
                continue

            try:
                latitude = float(latitude)
                longitude = float(longitude)
            except (TypeError, ValueError):
                await websocket.send_json(
                    {"success": False, "message": "latitude and longitude must be numbers"}
                )
                continue

            if not -90 <= latitude <= 90:
                await websocket.send_json({"success": False, "message": "Invalid latitude"})
                continue

            if not -180 <= longitude <= 180:
                await websocket.send_json({"success": False, "message": "Invalid longitude"})
                continue

            now = datetime.now(timezone.utc)
            accuracy = data.get("accuracy")
            speed = data.get("speed")
            heading = data.get("heading")

            user_locations_collection.update_one(
                {"user_id": user_id},
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
                upsert=True,
            )

            location_history_collection.insert_one(
                {
                    "user_id": user_id,
                    "latitude": latitude,
                    "longitude": longitude,
                    "accuracy": accuracy,
                    "speed": speed,
                    "heading": heading,
                    "recorded_at": now,
                }
            )

            live_data = {
                "type": "location_update",
                "user_id": user_id,
                "latitude": latitude,
                "longitude": longitude,
                "accuracy": accuracy,
                "speed": speed,
                "heading": heading,
                "updated_at": now.isoformat(),
            }

            await manager.broadcast(live_data)

            await websocket.send_json(
                {"success": True, "message": "Location received", "data": live_data}
            )

    except WebSocketDisconnect:
        manager.disconnect(user_id, websocket)
    except Exception as e:
        print("WebSocket error:", e)
        manager.disconnect(user_id, websocket)