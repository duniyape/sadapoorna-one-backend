from pymongo import MongoClient
from bson import ObjectId

MONGO_URL = "mongodb+srv://igold:gold0011@igold.eazpfbp.mongodb.net/?retryWrites=true&w=majority&appName=igold"
client = MongoClient(MONGO_URL)
db = client["sadapoorna_local"]
customers_collection = db["customers"]

default_beat_id = ObjectId("6a7afb1775c551fee4ea35a4")

# Update all customers to have beat_id as an ObjectId
result = customers_collection.update_many(
    {}, 
    {"$set": {"beat_id": default_beat_id}}
)

print(f"Updated {result.modified_count} customers.")
