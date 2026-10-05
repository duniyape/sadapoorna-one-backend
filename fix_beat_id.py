from pymongo import MongoClient
from bson import ObjectId

MONGO_URL = "mongodb+srv://igold:gold0011@igold.eazpfbp.mongodb.net/?retryWrites=true&w=majority&appName=igold"
client = MongoClient(MONGO_URL)

# Update Local
db_local = client["sadapoorna_local"]
db_local["customers"].update_many({}, {"$set": {"beat_id": ObjectId("6a9a86b1b9d4a793e515858c")}})

# Update Production
db_prod = client["sadapoorna_production"]
db_prod["customers"].update_many({}, {"$set": {"beat_id": ObjectId("6a9a86b1b9d4a793e515858c")}})

print("Corrected beat_id across both databases!")
