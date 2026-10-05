from pymongo import MongoClient

client = MongoClient("mongodb+srv://igold:gold0011@igold.eazpfbp.mongodb.net/?retryWrites=true&w=majority&appName=igold")
db = client["sadapoorna_local"]

print("--- All Beats ---")
beats = list(db["beats"].find({}))
for b in beats:
    print(f"Beat Name: {b.get('beat_name')}, Day: {b.get('day')}, ID: {b['_id']}, User: {b.get('user_id')}")
