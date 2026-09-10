import sys
import os
from datetime import datetime, timezone
from bson import ObjectId

# Ensure app root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import (
    stock_batches_collection,
    stock_batch_allocations_collection,
    sale_batch_consumptions_collection,
    orders_collection,
    warehouses_collection,
    vehicles_collection,
    products_collection,
    product_variants_collection,
    customers_collection,
    vendors_collection,
    users_collection,
)
from services.batch_service import (
    reverse_sale_consumptions,
    allocate_fifo_from_batches,
    record_sale_consumptions,
    generate_batch_no,
)
from routes.Inventory import get_stock_ledger, get_batch_timeline

def run_test():
    print("=== Starting End-to-End Test for Sale Return Stock Management ===")

    # 1. Fetch reference entities
    wh = warehouses_collection.find_one({"$or": [{"status": "active"}, {"record_status": "active"}, {}]})
    if not wh:
        print("FAIL: No warehouse found")
        return False
    wh_id = wh["_id"]
    print(f"Using Warehouse: {wh.get('name')} ({wh_id})")

    var = product_variants_collection.find_one({"status": "active"})
    if not var:
        print("FAIL: No active variant found")
        return False
    v_id = var["_id"]
    p_id = var["product_id"]
    prod = products_collection.find_one({"_id": p_id}) or {}
    print(f"Using Product: {prod.get('name')}, Variant: {var.get('name')} ({v_id})")

    customer = customers_collection.find_one({"$or": [{"status": "active"}, {"record_status": "active"}, {}]})
    cust_id = customer["_id"] if customer else ObjectId()

    user = users_collection.find_one()
    user_id = str(user["_id"]) if user else str(ObjectId())

    # 2. Create a dedicated test stock batch in warehouse
    test_batch_no = f"BAT-TEST-RETURN-{int(datetime.now().timestamp())}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    init_qty = 20.0
    purchase_rate = 150.0

    batch_doc = {
        "batch_no": test_batch_no,
        "product_id": p_id,
        "variant_id": v_id,
        "location_type": "warehouse",
        "warehouse_id": wh_id,
        "vehicle_id": None,
        "initial_quantity": init_qty,
        "available_quantity": init_qty,
        "reserved_quantity": 0.0,
        "purchase_rate": purchase_rate,
        "tax_rate": 5.0,
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }
    batch_res = stock_batches_collection.insert_one(batch_doc)
    batch_id = batch_res.inserted_id
    print(f"Created test stock batch: {test_batch_no} (ID: {batch_id}, Qty: {init_qty})")

    sale_order_id = None
    ret_order_id = None
    try:
        # 3. Simulate Sale Order & FIFO Consumption (delivering 10 units)
        sale_order_doc = {
            "type": "sale",
            "order_no": f"ORD-TEST-SALE-{int(datetime.now().timestamp())}",
            "invoice_no": f"INV-TEST-SALE-{int(datetime.now().timestamp())}",
            "customer_id": cust_id,
            "customer_name": customer.get("name") if customer else "Test Customer",
            "warehouse_id": wh_id,
            "status": "Delivered",
            "record_status": "active",
            "created_at": now,
            "updated_at": now,
        }
        so_res = orders_collection.insert_one(sale_order_doc)
        sale_order_id = so_res.inserted_id

        sale_item_id = ObjectId()
        sold_qty = 10.0
        sale_rate = 200.0

        # Direct FIFO allocation from this batch
        stock_batches_collection.update_one(
            {"_id": batch_id},
            {"$inc": {"available_quantity": -sold_qty}}
        )

        consumption_doc = {
            "sale_order_id": sale_order_id,
            "sale_item_id": sale_item_id,
            "stock_batch_id": batch_id,
            "batch_no": test_batch_no,
            "product_id": p_id,
            "variant_id": v_id,
            "warehouse_id": wh_id,
            "vehicle_id": None,
            "quantity": sold_qty,
            "purchase_rate": purchase_rate,
            "sale_rate": sale_rate,
            "returned_quantity": 0.0,
            "consumed_at": now,
        }
        sale_batch_consumptions_collection.insert_one(consumption_doc)
        print(f"Delivered {sold_qty} units in sale order {sale_order_doc['invoice_no']}")

        # Verify batch available quantity is now 10
        b_after_sale = stock_batches_collection.find_one({"_id": batch_id})
        assert b_after_sale["available_quantity"] == 10.0, f"Expected 10.0, got {b_after_sale['available_quantity']}"
        print(f"Batch available quantity after sale: {b_after_sale['available_quantity']} (OK)")

        # 4. Create Sale Return Order (returning 4 units)
        ret_qty_1 = 4.0
        ret_order_doc = {
            "type": "sale_return",
            "order_no": f"ORD-TEST-RET-{int(datetime.now().timestamp())}",
            "invoice_no": f"CN-TEST-RET-{int(datetime.now().timestamp())}",
            "ref_invoice_id": sale_order_id,
            "customer_id": cust_id,
            "customer_name": customer.get("name") if customer else "Test Customer",
            "warehouse_id": wh_id,
            "status": "Completed",
            "record_status": "active",
            "created_at": now,
            "updated_at": now,
        }
        ret_res = orders_collection.insert_one(ret_order_doc)
        ret_order_id = ret_res.inserted_id

        # Call reverse_sale_consumptions
        restored, cogs_rev = reverse_sale_consumptions(
            original_sale_order_id=sale_order_id,
            ref_item_id=sale_item_id,
            return_quantity=ret_qty_1,
            return_warehouse_id=wh_id,
            user_id=user_id,
            return_order_id=ret_order_id,
        )
        print(f"Reversed sale return: {restored}, COGS reversed: {cogs_rev}")

        # Verify batch quantity restored to 14.0
        b_after_ret1 = stock_batches_collection.find_one({"_id": batch_id})
        assert b_after_ret1["available_quantity"] == 14.0, f"Expected 14.0, got {b_after_ret1['available_quantity']}"
        print(f"Batch available quantity after return 1: {b_after_ret1['available_quantity']} (OK)")

        # Verify allocation doc was created
        alloc = stock_batch_allocations_collection.find_one({"transfer_order_id": ret_order_id})
        assert alloc is not None, "Allocation document for sale return not found!"
        assert alloc["allocation_type"] == "sale_return"
        assert alloc["quantity"] == 4.0
        assert alloc["from_location"]["type"] == "customer"
        assert alloc["to_location"]["type"] == "warehouse"
        assert alloc["to_location"]["id"] == wh_id
        assert alloc["batch_no"] == test_batch_no
        print(f"Verified stock_batch_allocations record: type={alloc['allocation_type']}, qty={alloc['quantity']}, batch={alloc['batch_no']} (OK)")

        # Verify sale consumption returned_quantity was incremented to 4.0
        cons_after_ret1 = sale_batch_consumptions_collection.find_one({"sale_order_id": sale_order_id})
        assert cons_after_ret1["returned_quantity"] == 4.0, f"Expected returned_quantity 4.0, got {cons_after_ret1['returned_quantity']}"
        print(f"Verified sale consumption returned_quantity: {cons_after_ret1['returned_quantity']} (OK)")

        # 5. Verify partial second return (return remaining 6 units)
        restored2, cogs_rev2 = reverse_sale_consumptions(
            original_sale_order_id=sale_order_id,
            ref_item_id=sale_item_id,
            return_quantity=6.0,
            return_warehouse_id=wh_id,
            user_id=user_id,
            return_order_id=ret_order_id,
        )
        b_after_ret2 = stock_batches_collection.find_one({"_id": batch_id})
        assert b_after_ret2["available_quantity"] == 20.0, f"Expected 20.0, got {b_after_ret2['available_quantity']}"
        print(f"Batch available quantity after return 2: {b_after_ret2['available_quantity']} (OK)")

        # 6. Verify over-return is blocked
        try:
            reverse_sale_consumptions(
                original_sale_order_id=sale_order_id,
                ref_item_id=sale_item_id,
                return_quantity=1.0,
                return_warehouse_id=wh_id,
                user_id=user_id,
                return_order_id=ret_order_id,
            )
            print("FAIL: Expected exception on over-return, but none raised!")
            return False
        except Exception as ex:
            print(f"Over-return correctly blocked with message: {ex.detail if hasattr(ex, 'detail') else ex} (OK)")

        # 7. Verify Inventory Ledger for this batch
        ledger_res = get_stock_ledger(
            product_id=str(p_id),
            variant_id=str(v_id),
            warehouse_id=str(wh_id),
            batch_no=test_batch_no,
        )
        assert ledger_res["success"] is True
        ledger_rows = ledger_res["data"]
        print(f"Retrieved {len(ledger_rows)} ledger rows for batch {test_batch_no}")

        # Check that sale return rows exist
        ret_rows = [r for r in ledger_rows if r["action"] == "SALE_RETURN"]
        assert len(ret_rows) >= 2, f"Expected at least 2 SALE_RETURN rows, found {len(ret_rows)}"
        for r in ret_rows:
            print(f"  Ledger Row: Date={r['date']}, Action={r['action_label']}, DocType={r['document_type']}, DocNo={r['document_no']}, Inward={r['inward_quantity']}, RunningBalance={r['running_balance']}")
            assert r["inward_quantity"] > 0
            assert r["outward_quantity"] == 0.0
            assert r["document_type"] == "Credit Note"
            assert r["from_location"] == (customer.get("name") if customer else "Customer")

        # 8. Verify Batch Timeline
        timeline_res = get_batch_timeline(batch_no=test_batch_no)
        assert timeline_res["success"] is True
        timeline_journey = timeline_res.get("journey") or timeline_res.get("data", {}).get("journey", [])
        print(f"Retrieved {len(timeline_journey)} timeline journey steps for batch {test_batch_no}:")
        return_steps = [s for s in timeline_journey if s["step"] == "SALE_RETURN"]
        assert len(return_steps) >= 2, f"Expected at least 2 SALE_RETURN timeline steps, found {len(return_steps)}"
        for s in return_steps:
            print(f"  Timeline Step: Step={s['step']}, Title={s['title']}, Qty={s['quantity']}, Rate={s['unit_rate']}")

        print("\n>>> ALL TESTS PASSED SUCCESSFULLY! 100% VERIFIED! <<<")
        return True

    finally:
        # Cleanup test documents so DB is untouched
        print("\nCleaning up test artifacts from database...")
        stock_batches_collection.delete_one({"_id": batch_id})
        if sale_order_id:
            orders_collection.delete_one({"_id": sale_order_id})
            sale_batch_consumptions_collection.delete_many({"sale_order_id": sale_order_id})
        if ret_order_id:
            orders_collection.delete_one({"_id": ret_order_id})
            stock_batch_allocations_collection.delete_many({"transfer_order_id": ret_order_id})
        print("Cleanup completed.")

if __name__ == "__main__":
    success = run_test()
    sys.exit(0 if success else 1)
