import os
import sys
from bson import ObjectId
from datetime import datetime, timezone

# Ensure path includes root
sys.path.insert(0, os.path.abspath("."))

from database import (
    stock_batches_collection,
    stock_batch_allocations_collection,
    sale_batch_consumptions_collection,
    products_collection,
    product_variants_collection,
    warehouses_collection,
    vehicles_collection,
    orders_collection,
    vendors_collection,
    customers_collection,
    users_collection,
)
from routes.Inventory import (
    get_Unallocated_inventory,
    get_warehouse_inventory,
    get_vehicle_inventory,
    get_stock_ledger,
    get_batch_timeline,
)

def run_tests():
    print("[INFO] Starting test suite for Stock Batches & Stock Ledger...")

    # 1. Test get_Unallocated_inventory
    print("[TEST 1] Testing get_Unallocated_inventory...")
    res_unalloc = get_Unallocated_inventory(page=1, limit=10)
    assert res_unalloc["success"] is True, "Failed success flag"
    assert "data" in res_unalloc, "Missing data in unallocated response"
    assert "pagination" in res_unalloc, "Missing pagination in unallocated response"
    print(f"  [PASS] Unallocated items found: {len(res_unalloc['data'])} (total: {res_unalloc['pagination']['total']})")
    if res_unalloc['data']:
        first = res_unalloc['data'][0]
        print(f"  Sample: Product={first.get('product_name')}, AvailQty={first.get('available_quantity')}, TotalVal={first.get('total_value')}, Batches={first.get('batch_count')}")

    # 2. Test get_warehouse_inventory
    print("\n[TEST 2] Testing get_warehouse_inventory...")
    res_wh = get_warehouse_inventory(page=1, limit=10)
    assert res_wh["success"] is True, "Failed success flag"
    assert "data" in res_wh, "Missing data in warehouse response"
    assert "pagination" in res_wh, "Missing pagination in warehouse response"
    print(f"  [PASS] Warehouse items found: {len(res_wh['data'])} (total: {res_wh['pagination']['total']})")
    if res_wh['data']:
        first = res_wh['data'][0]
        print(f"  Sample: Warehouse={first.get('warehouse_name')}, Product={first.get('product_name')}, AvailQty={first.get('available_quantity')}, TotalVal={first.get('total_value')}")

    # 3. Test get_vehicle_inventory
    print("\n[TEST 3] Testing get_vehicle_inventory...")
    res_veh = get_vehicle_inventory(page=1, limit=10)
    assert res_veh["success"] is True, "Failed success flag"
    assert "data" in res_veh, "Missing data in vehicle response"
    assert "pagination" in res_veh, "Missing pagination in vehicle response"
    print(f"  [PASS] Vehicle items found: {len(res_veh['data'])} (total: {res_veh['pagination']['total']})")

    # 4. Test get_stock_ledger
    print("\n[TEST 4] Testing get_stock_ledger (Stock Card)...")
    res_ledger = get_stock_ledger(page=1, limit=20)
    assert res_ledger["success"] is True, "Failed success flag in stock ledger"
    assert "data" in res_ledger, "Missing data in stock ledger"
    assert "pagination" in res_ledger, "Missing pagination in stock ledger"
    print(f"  [PASS] Stock ledger entries: {len(res_ledger['data'])} (total: {res_ledger['pagination']['total']})")
    if res_ledger['data']:
        first = res_ledger['data'][0]
        print(f"  Sample Entry: Date={first.get('date')}, Action={first.get('action_label')}, Doc#={first.get('document_no')}, From={first.get('from_location')}, To={first.get('to_location')}, In={first.get('inward_quantity')}, Out={first.get('outward_quantity')}, Balance={first.get('running_balance')}, DoneBy={first.get('done_by')}")

    # 5. Test get_batch_timeline if any batch exists
    print("\n[TEST 5] Testing get_batch_timeline...")
    sample_batch = stock_batches_collection.find_one({"status": "active"})
    if sample_batch:
        batch_no = sample_batch.get("batch_no")
        print(f"  Testing timeline for batch_no: {batch_no}...")
        res_timeline = get_batch_timeline(batch_no)
        assert res_timeline["success"] is True, "Failed timeline response"
        assert "journey" in res_timeline, "Missing journey in timeline"
        assert "current_locations" in res_timeline, "Missing current_locations in timeline"
        print(f"  [PASS] Timeline verified: Product={res_timeline.get('product_name')}, InitialQty={res_timeline.get('initial_quantity')}, Available={res_timeline.get('total_available_quantity')}, JourneySteps={len(res_timeline['journey'])}")
        for step in res_timeline['journey']:
            print(f"    - Step: {step.get('step')} | {step.get('title')} | Qty={step.get('quantity')}")
    else:
        print("  [SKIP] No active stock batch currently found in DB to test timeline.")

    print("\n[ALL TESTS PASSED SUCCESSFULLY!]")

if __name__ == "__main__":
    run_tests()
