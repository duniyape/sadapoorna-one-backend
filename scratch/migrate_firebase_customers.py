import os
import json
import argparse
from datetime import datetime, timezone
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
branches_collection = db["branches"]
users_collection = db["users"]

def get_default_ids():
    branch = branches_collection.find_one()
    branch_id = str(branch["_id"]) if branch else None

    user = users_collection.find_one()
    user_id = str(user["_id"]) if user else None

    return branch_id, user_id

def parse_date(date_str):
    if not date_str:
        return datetime.now(timezone.utc)
    try:
        # Example: 2025-08-19T15:38:26.765Z
        return datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return datetime.now(timezone.utc)

def normalize_indian_phone(phone: str):
    if not phone:
        return ""
    phone = phone.strip().replace(" ", "").replace("-", "")
    if phone.startswith("+91"): phone = phone[3:]
    elif phone.startswith("91") and len(phone) == 12: phone = phone[2:]
    elif phone.startswith("0") and len(phone) == 11: phone = phone[1:]
    if len(phone) == 10 and phone.isdigit():
        return "91" + phone
    return phone

def delete_previous_customers():
    print("Deleting previous customers...")
    res_cust = customers_collection.delete_many({})
    print(f"Deleted {res_cust.deleted_count} customers.")

def migrate_customers(json_path):
    delete_previous_customers()

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # If the JSON contains the entire DB export, extract the "customers" node
    if isinstance(data, dict) and "customers" in data:
        data = data["customers"]

    # Firebase RTDB exports are often a dict with ID as key
    if isinstance(data, dict):
        records = [{"_fb_key": k, **(v if isinstance(v, dict) else {})} for k, v in data.items()]
    else:
        records = data

    branch_id, user_id = get_default_ids()
    if not branch_id:
        print("Warning: No branch found in DB, using a dummy branch_id")
        branch_id = str(ObjectId())
    if not user_id:
        print("Warning: No user found in DB, using a dummy assigned_employee_id")
        user_id = str(ObjectId())

    inserted = 0
    now = datetime.now(timezone.utc)

    # Prepare custom ID counter
    last_customer = customers_collection.find_one({}, sort=[("_id", -1)])
    start_num = 0
    if last_customer and "id" in last_customer:
        try:
            start_num = int(last_customer["id"].replace("CUST", ""))
        except:
            pass

    for record in records:
        if not record or not isinstance(record, dict):
            continue

        name = record.get("owner")
        shop = record.get("shop")
        if not name and not shop:
            continue

        c_type = record.get("type", "Customer")
        if not name:
            name = shop if shop else "Unknown"

        # Fallback for business type/company
        business_type = c_type
        company_name = shop if shop else name
        
        gstin = record.get("gstin", "")
        if gstin.lower() in ["na", "none", "null", ""]:
            gstin = None

        mobile = normalize_indian_phone(record.get("contact", ""))
        
        address_str = record.get("address", "")
        state_str = record.get("state", "")
        
        billing_address = {
            "address": address_str,
            "city": None,
            "state": state_str,
            "pincode": None
        }

        lat = None
        lng = None
        loc_data = record.get("location")
        if loc_data and isinstance(loc_data, dict):
            lat = loc_data.get("lat")
            lng = loc_data.get("lng")
        
        location = None
        if lat is not None and lng is not None:
            location = {"lat": float(lat), "lng": float(lng)}

        created_at = parse_date(record.get("createdAt"))
        
        # Increment counter for new custom ID
        start_num += 1
        new_customer_id = f"CUST{start_num:04d}"

        # Original Firebase ID
        firebase_id = record.get("id") or record.get("_fb_key") or ""
        
        # Insert Customer
        customer_data = {
            "id": new_customer_id,
            "reference_id": firebase_id,
            
            "customer_type": "Business" if shop else "Individual",
            "name": name,
            "email": None,
            "mobile": mobile,
            "alternate_mobile": None,
            
            "company_name": company_name,
            "business_type": business_type,
            "gst_number": gstin,
            "beat_id": record.get("beatID", None),
            
            "billing_address": billing_address,
            "shipping_address": billing_address,
            "sameAsBilling": True,
            
            "branch_id": branch_id,
            "assigned_employee_id": user_id,
            "location": location,
            
            "created_by": user_id,
            "created_at": created_at,
            "updated_by": None,
            "updated_at": created_at,
            
            "status": "active",
            "phone_verified": False,
            "phone_verified_at": None,
        }

        cust_res = customers_collection.insert_one(customer_data)
        inserted += 1

    print(f"Migration complete. Inserted {inserted} customers.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate Firebase Customers to MongoDB")
    parser.add_argument("json_file", help="Path to the Firebase JSON export file")
    args = parser.parse_args()
    
    if not os.path.exists(args.json_file):
        print(f"File not found: {args.json_file}")
    else:
        migrate_customers(args.json_file)
