import os
from pymongo import MongoClient

# Use the same connection string from your database.py
MONGO_URL = "mongodb+srv://igold:gold0011@igold.eazpfbp.mongodb.net/?retryWrites=true&w=majority&appName=igold"
client = MongoClient(MONGO_URL)

source_db = client["sadapoorna_local"]
dest_db = client["sadapoorna_production"]

def sync_databases():
    collections = source_db.list_collection_names()
    
    print(f"Found {len(collections)} collections in 'sadapoorna_local'.")
    print("Starting sync to 'sadapoorna_production'...")
    
    for coll_name in collections:
        print(f"\nProcessing collection: {coll_name}...")
        
        # 1. Drop the destination collection so we start fresh and avoid duplicates/conflicts
        dest_db[coll_name].drop()
        
        # 2. Fetch all documents from the source collection
        docs = list(source_db[coll_name].find())
        
        # 3. Insert documents into the destination collection if there are any
        if docs:
            dest_db[coll_name].insert_many(docs)
            print(f"Copied {len(docs)} documents.")
        else:
            print("Collection is empty. Created empty collection.")
            
    print("\nDatabase sync completed successfully!")

if __name__ == "__main__":
    sync_databases()
