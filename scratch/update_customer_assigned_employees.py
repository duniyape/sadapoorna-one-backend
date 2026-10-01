import os
import json
from pymongo import MongoClient
from bson import ObjectId

# MongoDB configuration
MONGO_URL = os.getenv(
    "MONGO_URL",
    "mongodb+srv://igold:gold0011@igold.eazpfbp.mongodb.net/?retryWrites=true&w=majority&appName=igold"
)
client = MongoClient(MONGO_URL)
db = client["sadapoorna_local"]
customers_collection = db["customers"]

# Mapping from Firebase User ID to MongoDB User ObjectId string
EMPLOYEE_MAPPING = {
    "9aI4jdZ2kdUwJdqx1F5kbgVR9N33": "6abe494fb22b7c665b9d97c5",
    "8lhkM4TnmubQb0GMKyXlhlJxFv52": "6abe4a07b22b7c665b9d97c6"
}

def update_assigned_employees(json_path):
    print("Reading Firebase data...")
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if isinstance(data, dict) and "customers" in data:
        data = data["customers"]

    if isinstance(data, dict):
        records = [{"_fb_key": k, **(v if isinstance(v, dict) else {})} for k, v in data.items()]
    else:
        records = data

    print(f"Total customers in Firebase data: {len(records)}")
    
    updates_performed = 0
    updates_not_found = 0

    for record in records:
        if not record or not isinstance(record, dict):
            continue

        created_by = record.get("createdBy")
        if created_by in EMPLOYEE_MAPPING:
            new_employee_id = EMPLOYEE_MAPPING[created_by]
            firebase_id = record.get("id") or record.get("_fb_key") or ""
            
            if not firebase_id:
                continue

            # Update the customer in MongoDB
            result = customers_collection.update_one(
                {"reference_id": str(firebase_id)},
                {"$set": {"assigned_employee_id": str(new_employee_id)}}
            )

            if result.matched_count > 0:
                updates_performed += 1
            else:
                updates_not_found += 1

    print(f"Updates performed successfully: {updates_performed}")
    print(f"Customers not found in MongoDB: {updates_not_found}")

if __name__ == "__main__":
    json_file_path = "scratch/firebase_database.json"
    if not os.path.exists(json_file_path):
        print(f"File not found: {json_file_path}")
    else:
        update_assigned_employees(json_file_path)
