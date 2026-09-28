import os
from pymongo import MongoClient

def run_migration():
    MONGO_URL = os.getenv(
        "MONGO_URL",
        "mongodb+srv://igold:gold0011@igold.eazpfbp.mongodb.net/?retryWrites=true&w=majority&appName=igold"
    )
    
    client = MongoClient(MONGO_URL)
    db = client["sadapoorna_local"]
    
    product_variants_collection = db["product_variants"]
    
    print("Starting migration: replacing 'rate_unit_id' with 'rate_type' in product_variants...")
    
    result = product_variants_collection.update_many(
        {},
        {
            "$set": {"rate_type": "per_package"},
            "$unset": {"rate_unit_id": ""}
        }
    )
    
    print(f"Migration completed. Matched {result.matched_count} documents and modified {result.modified_count} documents.")

if __name__ == "__main__":
    run_migration()
