import json
from datetime import datetime, timezone
import bson
from database import ledgers_collection, orders_collection, customers_collection

def migrate_orders():
    ledgers = list(ledgers_collection.find({"opening_balance": {"$gt": 0}, "opening_balance_type": "DEBIT"}))
    created = 0
    updated = 0
    now = datetime.now(timezone.utc)
    
    for ledger in ledgers:
        customer_id_str = str(ledger.get("customer_id"))
        
        try:
            cust = customers_collection.find_one({"_id": bson.ObjectId(customer_id_str)})
        except:
            cust = customers_collection.find_one({"_id": customer_id_str})
            
        if not cust: 
            continue
            
        exists = orders_collection.find_one({"customer_id": customer_id_str, "invoice_no": "OPENING-BAL"})
        if exists:
            orders_collection.update_one(
                {"_id": exists["_id"]},
                {"$set": {
                    "grand_total": ledger["opening_balance"],
                    "pending_amount": ledger["opening_balance"],
                }}
            )
            updated += 1
        else:
            order = {
                "type": "sale",
                "record_status": "active",
                "customer_id": customer_id_str,
                "invoice_no": "OPENING-BAL",
                "billed_at": now,
                "created_at": now,
                "due_date": now,
                "grand_total": ledger["opening_balance"],
                "paid_amount": 0,
                "pending_amount": ledger["opening_balance"],
                "credit_days": 0,
                "items": [{"name": "Opening Balance", "amount": ledger["opening_balance"]}],
                "branch_id": cust.get("branch_id"),
                "assigned_employee_id": cust.get("assigned_employee_id"),
            }
            orders_collection.insert_one(order)
            created += 1
            
    print(f"Created {created} and updated {updated} Opening Balance invoices.")

if __name__ == "__main__":
    migrate_orders()
