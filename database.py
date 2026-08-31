from pymongo import MongoClient

MONGO_URL = "mongodb+srv://igold:gold0011@igold.eazpfbp.mongodb.net/?retryWrites=true&w=majority&appName=igold"

client = MongoClient(MONGO_URL)

db = client["sadapoorna_local"]

users_collection = db["users"]
masters_collection = db["masters"]
branches_collection = db["branches"]
access_collection = db["access"]
user_access_collection = db["user_access"]
customers_collection = db["customers"]
otp_collection = db["otp_verifications"]
product_categories_collection = db["product_categories"]
product_sub_categories_collection = db["product_sub_categories"]
product_brands_collection = db["product_brands"]
product_units_collection = db["product_units"]
products_collection, product_variants_collection, inventory_collection = db["products"], db["product_variants"], db["inventory"]
packing_types_collection = db["packing_types"]
warehouses_collection = db["warehouses"]
vehicles_collection = db["vehicles"]
vendors_collection = db["vendors"]
orders_collection = db["orders"]
counters_collection = db["counter"]
