import os
from pymongo import MongoClient

MONGO_URL = os.getenv(
    "MONGO_URL",
    "mongodb+srv://igold:gold0011@igold.eazpfbp.mongodb.net/?retryWrites=true&w=majority&appName=igold"
)

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
whatsapp_chats_collection = db["whatsapp_chats"]
whatsapp_messages_collection = db["whatsapp_messages"]
beats_collection = db["beats"]

groups_collection = db["accounting_groups"]
subgroups_collection = db["accounting_subgroups"]
ledgers_collection = db["accounting_ledgers"]
vouchers_collection = db["accounting_vouchers"]

# =========================================================
# STOCK BATCH & FIFO COLLECTIONS
# =========================================================
stock_batches_collection = db["stock_batches"]
stock_batch_allocations_collection = db["stock_batch_allocations"]
sale_batch_consumptions_collection = db["sale_batch_consumptions"]

# =========================================================
# WHATSAPP INDEXES
# =========================================================

# One chat per WhatsApp phone number
whatsapp_chats_collection.create_index(
    [("phone", 1)],
    unique=True
)

# Latest chats first
whatsapp_chats_collection.create_index(
    [("last_message_at", -1)]
)

# Fast message loading for a chat
whatsapp_messages_collection.create_index(
    [
        ("chat_id", 1),
        ("timestamp", 1)
    ]
)

# WhatsApp message ID should be unique
whatsapp_messages_collection.create_index(
    [("message_id", 1)],
    unique=True,
    sparse=True
)

# =========================================================
# STOCK BATCHES & FIFO INDEXES
# =========================================================

# Fast FIFO warehouse lookup (location + product + variant + status + created_at)
stock_batches_collection.create_index(
    [
        ("warehouse_id", 1),
        ("product_id", 1),
        ("variant_id", 1),
        ("status", 1),
        ("created_at", 1),
    ]
)

# Fast FIFO vehicle lookup
stock_batches_collection.create_index(
    [
        ("vehicle_id", 1),
        ("product_id", 1),
        ("variant_id", 1),
        ("status", 1),
        ("created_at", 1),
    ]
)

# Batch number lookup
stock_batches_collection.create_index(
    [("batch_no", 1)]
)

# Fast location + status + available_quantity lookup
stock_batches_collection.create_index(
    [
        ("location_type", 1),
        ("status", 1),
        ("available_quantity", 1),
        ("product_id", 1),
        ("variant_id", 1),
    ]
)

# Purchase order batches lookup
stock_batches_collection.create_index(
    [("purchase_order_id", 1)]
)

# Allocations / Transfer lookup
stock_batch_allocations_collection.create_index(
    [("transfer_order_id", 1)]
)
stock_batch_allocations_collection.create_index(
    [("source_batch_id", 1)]
)
stock_batch_allocations_collection.create_index(
    [("destination_batch_id", 1)]
)
stock_batch_allocations_collection.create_index(
    [("product_id", 1), ("variant_id", 1)]
)
stock_batch_allocations_collection.create_index(
    [("created_at", -1)]
)

# Sales Batch Consumptions lookup
sale_batch_consumptions_collection.create_index(
    [("sale_order_id", 1), ("sale_item_id", 1)]
)
sale_batch_consumptions_collection.create_index(
    [("stock_batch_id", 1)]
)
sale_batch_consumptions_collection.create_index(
    [("product_id", 1), ("variant_id", 1)]
)
sale_batch_consumptions_collection.create_index(
    [("consumed_at", -1)]
)

# Orders collection indexes
orders_collection.create_index(
    [("type", 1), ("status", 1), ("record_status", 1), ("warehouse_id", 1)]
)
orders_collection.create_index(
    [("order_no", 1)]
)
orders_collection.create_index(
    [("invoice_no", 1)],
    sparse=True
)
orders_collection.create_index(
    [("created_at", -1)]
)