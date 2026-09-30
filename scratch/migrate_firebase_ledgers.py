import json
from datetime import datetime
from database import customers_collection, ledgers_collection, groups_collection

def migrate_ledgers():
    # 1. Get Sundry Debtors group
    debtors_group = groups_collection.find_one({"group_name": "Sundry Debtors"})
    if not debtors_group:
        print("Sundry Debtors group not found!")
        return

    group_id = debtors_group["_id"]
    group_name = debtors_group["group_name"]
    group_type = debtors_group.get("group_type", "ASSETS")

    # 2. Compute balances from firebase JSON
    with open('scratch/firebase_database.json', 'r', encoding='utf-8') as f:
        data = json.load(f)

    firebase_customers = data.get('customers', {})
    
    def safe_float(val):
        try: return float(val) if val else 0.0
        except ValueError: return 0.0

    ledger_balances = {}
    for key, c in firebase_customers.items():
        # key is usually the phone number (reference_id)
        if 'balance' in c:
            ledger_balances[key] = safe_float(c['balance'])

    # 3. Create Ledgers for Customers
    customers = list(customers_collection.find())
    print(f"Found {len(customers)} customers in MongoDB.")

    created_count = 0
    updated_count = 0
    for customer in customers:
        ref_id = customer.get("reference_id")
        if not ref_id:
            continue

        balance = ledger_balances.get(ref_id, 0.0)
        balance_type = "DEBIT" if balance > 0 else "CREDIT"
        abs_balance = abs(balance)

        existing = ledgers_collection.find_one({"customer_id": str(customer["_id"])})
        
        if existing:
            # Update existing regardless of whether it's 0 or not, to fix any false positives
            ledgers_collection.update_one(
                {"_id": existing["_id"]},
                {"$set": {
                    "opening_balance": abs_balance,
                    "opening_balance_type": balance_type,
                    "updated_at": datetime.utcnow()
                }}
            )
            updated_count += 1
        else:
            # Create new ONLY if balance > 0
            if balance == 0:
                continue
                
            ledger_doc = {
                "ledger_name": f"{customer['name']} ({customer['id']})",
                "customer_id": str(customer["_id"]),
                "group_id": group_id,
                "group_name": group_name,
                "group_type": group_type,
                "subgroup_id": None,
                "subgroup_name": None,
                "opening_balance": abs_balance,
                "opening_balance_type": balance_type,
                "description": "Migrated from Firebase",
                "status": "ACTIVE",
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow()
            }
            ledgers_collection.insert_one(ledger_doc)
            created_count += 1

    print(f"Migration completed! Created {created_count} new ledgers and updated {updated_count} existing ledgers.")

if __name__ == "__main__":
    migrate_ledgers()
