from pymongo import MongoClient
import pprint

client = MongoClient("mongodb+srv://igold:gold0011@igold.eazpfbp.mongodb.net/?retryWrites=true&w=majority&appName=igold")
db = client["sadapoorna_local"]

print("--- Beats assigned to Monday ---")
beats = list(db["beats"].find({"day": "Monday"}))
for b in beats:
    print(f"Beat ID: {b['_id']}, User: {b.get('user_id')}, Status: {b.get('status')}")

print("\n--- Example Customers with beat_id ---")
custs = list(db["customers"].find({"beat_id": {"$exists": True}}).limit(3))
for c in custs:
    print(f"Customer Name: {c.get('name')}, Beat ID: {c.get('beat_id')} (Type: {type(c.get('beat_id'))})")
