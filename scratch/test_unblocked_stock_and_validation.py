import sys
import os
from datetime import datetime, timezone
from bson import ObjectId
from fastapi import HTTPException

# Ensure app root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import (
    stock_batches_collection,
    orders_collection,
    warehouses_collection,
    vehicles_collection,
    products_collection,
    product_variants_collection,
    customers_collection,
    users_collection,
)
from services.batch_service import (
    get_sellable_stock_breakdown,
    get_bulk_blocked_quantities,
)
from routes.Inventory import (
    get_unblocked_stock,
    get_warehouse_inventory,
    get_vehicle_inventory,
)
from routes.orders import create_order
from schemas.order_schemas import OrderCreate, OrderItem

def run_tests():
    print("=== Testing High-Performance Unblocked Stock & Order Validation ===")

    # 1. Fetch reference entities
    wh = warehouses_collection.find_one({"$or": [{"status": "active"}, {}]})
    if not wh:
        print("FAIL: No warehouse found")
        return False
    wh_id = wh["_id"]
    print(f"Using Warehouse: {wh.get('name')} ({wh_id})")

    var = product_variants_collection.find_one({"status": "active"})
    if not var:
        print("FAIL: No variant found")
        return False
    v_id = var["_id"]
    p_id = var["product_id"]
    prod = products_collection.find_one({"_id": p_id}) or {}
    print(f"Using Product: {prod.get('name')}, Variant: {var.get('name')} ({v_id})")

    cust = customers_collection.find_one({"$or": [{"status": "active"}, {}]})
    cust_id = cust["_id"] if cust else ObjectId()

    user = users_collection.find_one()
    current_user = {"user_id": str(user["_id"])} if user else {"user_id": str(ObjectId())}

    # 2. Test get_sellable_stock_breakdown
    print("\n--- Test 1: get_sellable_stock_breakdown ---")
    breakdown = get_sellable_stock_breakdown(
        product_id=p_id,
        variant_id=v_id,
        warehouse_id=wh_id,
    )
    print(f"Sellable Breakdown: Physical={breakdown['physical_stock']}, Blocked={breakdown['blocked_stock']}, Unblocked={breakdown['unblocked_stock']}")
    assert breakdown["unblocked_stock"] == max(0.0, round(breakdown["physical_stock"] - breakdown["blocked_stock"], 6))
    print("[PASS] Breakdown calculation matches physical - blocked.")

    # 3. Test get_warehouse_inventory enrichment
    print("\n--- Test 2: get_warehouse_inventory enrichment ---")
    wh_inv = get_warehouse_inventory(warehouse_id=str(wh_id), limit=10)
    assert wh_inv["success"] is True
    print(f"Retrieved {len(wh_inv['data'])} warehouse inventory rows.")
    if wh_inv["data"]:
        first = wh_inv["data"][0]
        assert "blocked_quantity" in first, "blocked_quantity missing from warehouse inventory row!"
        assert "unblocked_quantity" in first, "unblocked_quantity missing from warehouse inventory row!"
        print(f"Sample row: Variant={first.get('variant_name')}, Avail={first.get('available_quantity')}, Blocked={first.get('blocked_quantity')}, Unblocked={first.get('unblocked_quantity')}")
    print("[PASS] Warehouse inventory properly enriched with blocked and unblocked stock.")

    # 4. Test get_unblocked_stock endpoint
    print("\n--- Test 3: get_unblocked_stock endpoint ---")
    unblocked_res = get_unblocked_stock(warehouse_id=str(wh_id), limit=10)
    assert unblocked_res["success"] is True
    print(f"Retrieved {len(unblocked_res['data'])} unblocked stock rows.")
    for row in unblocked_res["data"][:3]:
        assert "available_quantity" in row
        assert "blocked_quantity" in row
        assert "unblocked_quantity" in row
        assert row["unblocked_quantity"] == max(0.0, round(row["available_quantity"] - row["blocked_quantity"], 6))
        print(f"Unblocked row: Variant={row.get('variant_name')}, Avail={row['available_quantity']}, Blocked={row['blocked_quantity']}, Unblocked={row['unblocked_quantity']}")
    print("[PASS] GET /inventory/unblocked works with fast batch-direct engine.")

    # 5. Test Pre-Creation Sales Order Validation
    print("\n--- Test 4: Pre-Creation Sales Order Validation ---")
    # Create a dedicated test batch with known quantity
    test_batch_no = f"BAT-VALIDATION-{int(datetime.now().timestamp())}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    test_batch_doc = {
        "batch_no": test_batch_no,
        "product_id": p_id,
        "variant_id": v_id,
        "location_type": "warehouse",
        "warehouse_id": wh_id,
        "vehicle_id": None,
        "initial_quantity": 10.0,
        "available_quantity": 10.0,
        "reserved_quantity": 0.0,
        "purchase_rate": 100.0,
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }
    b_res = stock_batches_collection.insert_one(test_batch_doc)
    b_id = b_res.inserted_id
    print(f"Inserted test batch with 10 units: {test_batch_no}")

    test_order_ids = []
    try:
        current_unblocked = get_sellable_stock_breakdown(product_id=p_id, variant_id=v_id, warehouse_id=wh_id)["unblocked_stock"]
        print(f"Current unblocked before test orders: {current_unblocked}")

        # A: Attempt to order current_unblocked + 999 units (must FAIL with HTTP 400)
        oversell_qty = current_unblocked + 999.0
        oversell_order = OrderCreate(
            type="sale",
            customer_id=str(cust_id),
            warehouse_id=str(wh_id),
            items=[
                OrderItem(
                    product_id=str(p_id),
                    variant_id=str(v_id),
                    quantity=oversell_qty,
                    rate=120.0,
                )
            ]
        )
        try:
            create_order(data=oversell_order, current_user=current_user)
            print("FAIL: Expected oversell order to be rejected, but it succeeded!")
            return False
        except HTTPException as ex:
            print(f"[PASS] Oversell order correctly rejected with HTTP {ex.status_code}: {ex.detail}")
            assert ex.status_code == 400
            assert "Insufficient sellable stock" in ex.detail

        # B: Place a valid order for 5 units (must SUCCEED)
        valid_order = OrderCreate(
            type="sale",
            customer_id=str(cust_id),
            warehouse_id=str(wh_id),
            items=[
                OrderItem(
                    product_id=str(p_id),
                    variant_id=str(v_id),
                    quantity=5.0,
                    rate=120.0,
                )
            ]
        )
        res_valid = create_order(data=valid_order, current_user=current_user)
        assert res_valid["success"] is True
        created_ord_id = ObjectId(res_valid["data"]["id"])
        test_order_ids.append(created_ord_id)
        print(f"[PASS] Valid order created successfully with ID: {created_ord_id}")

        # C: Verify that unblocked stock instantly decreased by 5 units
        after_unblocked = get_sellable_stock_breakdown(product_id=p_id, variant_id=v_id, warehouse_id=wh_id)["unblocked_stock"]
        print(f"Unblocked stock after creating 5-unit order: {after_unblocked} (expected: {current_unblocked - 5.0})")
        assert round(after_unblocked, 4) == round(current_unblocked - 5.0, 4)
        print("[PASS] Unblocked stock immediately decremented by the pending order!")

    finally:
        print("\nCleaning up test batch and orders...")
        stock_batches_collection.delete_one({"_id": b_id})
        for oid in test_order_ids:
            orders_collection.delete_one({"_id": oid})
        print("Cleanup completed.")

    print("\n>>> ALL TESTS PASSED SUCCESSFULLY! 100% VERIFIED! <<<")
    return True

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
