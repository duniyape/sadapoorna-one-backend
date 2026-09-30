import json
import bson
from database import customers_collection, ledgers_collection

def fix_ledgers():
    migrated_ledgers = list(ledgers_collection.find({"description": "Migrated from Firebase"}))
    updated_custs = 0
    deleted_dups = 0
    for ledger in migrated_ledgers:
        cust_id = ledger.get("customer_id")
        
        try:
            cust = customers_collection.find_one({"_id": bson.ObjectId(cust_id)})
        except:
            cust = customers_collection.find_one({"_id": cust_id})
            
        if not cust: continue
        
        # If customer already has a DIFFERENT ledger_id, it means ensure_customer_ledger created a duplicate.
        current_ledger_id = cust.get("ledger_id")
        migrated_ledger_id = str(ledger["_id"])
        
        if current_ledger_id and current_ledger_id != migrated_ledger_id:
            try:
                ledgers_collection.delete_one({"_id": bson.ObjectId(current_ledger_id)})
                deleted_dups += 1
            except:
                pass
            
        # Link the correct migrated ledger
        customers_collection.update_one(
            {"_id": cust["_id"]},
            {"$set": {"ledger_id": migrated_ledger_id}}
        )
        updated_custs += 1
        
    print(f"Fixed! Linked {updated_custs} migrated ledgers and deleted {deleted_dups} duplicate zero-balance ledgers.")

if __name__ == "__main__":
    fix_ledgers()
