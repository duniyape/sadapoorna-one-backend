"""
Test Customer-Level FIFO Receipt Settlement Endpoint:
POST /accounting/customers/{customer_id}/receipt
"""
import sys
import os
from datetime import datetime, timezone, timedelta
from bson import ObjectId
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath("."))

from main import app
from database import (
    orders_collection,
    customers_collection,
    vouchers_collection,
    ledgers_collection,
)
from routes.auth import get_current_user
from services.accounting_service import (
    ensure_system_ledgers,
    ensure_customer_ledger,
    create_sales_invoice_voucher,
)


def run_customer_fifo_test():
    print("=================================================================")
    print("TESTING CUSTOMER-LEVEL FIFO RECEIPT SETTLEMENT")
    print("=================================================================")

    mock_user = {
        "user_id": "accounts_manager_1",
        "_id": ObjectId("65e000000000000000000001"),
        "role": "admin",
    }
    app.dependency_overrides[get_current_user] = lambda: mock_user
    client = TestClient(app)

    # Clean up test records
    customers_collection.delete_many({"name": "Gupta Enterprises"})
    ledgers_collection.delete_many({"ledger_name": {"$regex": "Gupta Enterprises"}})
    orders_collection.delete_many({"order_no": {"$regex": "ORD-FIFO-"}})
    vouchers_collection.delete_many({"narration": {"$regex": "INV-FIFO-"}})

    # 1. Create a customer
    now = datetime.now(timezone.utc)
    cust_res = customers_collection.insert_one({
        "name": "Gupta Enterprises",
        "phone": "919811223344",
        "credit_days": 15,
        "credit_limit": 50000.0,
        "created_at": now,
    })
    cust_id = str(cust_res.inserted_id)
    cust_lid, cust_lname = ensure_customer_ledger(cust_id, "Gupta Enterprises")

    # 2. Create 2 separate billed orders
    # Bill 1: Rs.6,000
    ord1_res = orders_collection.insert_one({
        "type": "sale",
        "order_no": "ORD-FIFO-001",
        "invoice_no": "INV-FIFO-001",
        "customer_id": ObjectId(cust_id),
        "subtotal": 5084.75,
        "total_gst": 915.25,
        "grand_total": 6000.0,
        "bill_amount": 6000.0,
        "paid_amount": 0.0,
        "pending_amount": 6000.0,
        "credit_days": 15,
        "due_date": now + timedelta(days=15),
        "status": "Delivered",
        "record_status": "active",
        "payment_status": "UNPAID",
        "billed_at": now - timedelta(days=5),
        "created_at": now - timedelta(days=5),
    })
    ord1_id = str(ord1_res.inserted_id)
    create_sales_invoice_voucher(orders_collection.find_one({"_id": ord1_res.inserted_id}), "INV-FIFO-001", "accounts_manager_1")

    # Bill 2: Rs.4,000
    ord2_res = orders_collection.insert_one({
        "type": "sale",
        "order_no": "ORD-FIFO-002",
        "invoice_no": "INV-FIFO-002",
        "customer_id": ObjectId(cust_id),
        "subtotal": 3389.83,
        "total_gst": 610.17,
        "grand_total": 4000.0,
        "bill_amount": 4000.0,
        "paid_amount": 0.0,
        "pending_amount": 4000.0,
        "credit_days": 15,
        "due_date": now + timedelta(days=15),
        "status": "Delivered",
        "record_status": "active",
        "payment_status": "UNPAID",
        "billed_at": now - timedelta(days=1),
        "created_at": now - timedelta(days=1),
    })
    ord2_id = str(ord2_res.inserted_id)
    create_sales_invoice_voucher(orders_collection.find_one({"_id": ord2_res.inserted_id}), "INV-FIFO-002", "accounts_manager_1")

    print("  Created 2 Invoices: INV-FIFO-001 (Rs.6,000) & INV-FIFO-002 (Rs.4,000). Total Due: Rs.10,000")

    # 3. Test: Attempting to Pay MORE than Total Due (Rs.12,000 on Rs.10,000 due)
    print("\n--- Test 1: Excess Payment Rejection (Rs.12,000 on Rs.10,000 Due) ---")
    res_over = client.post(
        f"/accounting/customers/{cust_id}/receipt",
        json={
            "payment_mode": "BANK_TRANSFER",
            "amount": 12000.0,
            "bank_account_name": "HDFC Current Account",
            "notes": "Attempting overpayment",
        }
    )
    print("  Response Code:", res_over.status_code)
    print("  Detail:", res_over.json().get("detail"))
    assert res_over.status_code == 400
    assert "Excess payment is not allowed" in res_over.json()["detail"]

    # 4. Customer makes valid partial lump-sum payment of Rs.8,000 via Bank Transfer
    print("\n--- Test 2: Valid Partial Lump-Sum Payment of Rs.8,000 ---")
    res = client.post(
        f"/accounting/customers/{cust_id}/receipt",
        json={
            "payment_mode": "BANK_TRANSFER",
            "amount": 8000.0,
            "bank_account_name": "HDFC Current Account",
            "transaction_ref": "NEFT-UTR-789012",
            "notes": "Lump sum payment received via NEFT",
        }
    )
    print("  Response Code:", res.status_code)
    assert res.status_code == 200, res.text
    res_data = res.json()
    summary = res_data["summary"]
    print(f"  Receipt Voucher Created: {summary['voucher_number']}")
    print(f"  Allocated to Bills: Rs.{summary['allocated_to_bills']}, Remaining Due: Rs.{summary['remaining_customer_due']}")
    print(f"  Bills Affected: {summary['bills_affected_count']} (Fully Paid: {summary['bills_fully_paid']}, Partial: {summary['bills_partially_paid']})")
    for alloc in summary["allocations"]:
        print(f"    - Bill {alloc['invoice_no']}: Allocated Rs.{alloc['allocated_amount']}, New Pending: Rs.{alloc['new_pending_amount']}, Status: {alloc['payment_status']}")

    assert summary["bills_fully_paid"] == 1
    assert summary["bills_partially_paid"] == 1
    assert summary["remaining_customer_due"] == 2000.0

    # Verify orders in DB
    ord1_updated = orders_collection.find_one({"_id": ObjectId(ord1_id)})
    ord2_updated = orders_collection.find_one({"_id": ObjectId(ord2_id)})
    assert ord1_updated["pending_amount"] == 0.0
    assert ord1_updated["payment_status"] == "PAID"
    assert ord2_updated["pending_amount"] == 2000.0
    assert ord2_updated["payment_status"] == "PARTIALLY_PAID"

    # 5. Check Customer Statement
    print("\n--- Test 3: Check Customer Statement ---")
    res_stmt = client.get(f"/accounting/customers/{cust_id}/statement")
    assert res_stmt.status_code == 200
    stmt_data = res_stmt.json()["data"]
    print(f"  Total Billed: Rs.{stmt_data['total_debit']}, Total Paid: Rs.{stmt_data['total_credit']}")
    print(f"  Closing Balance: Rs.{stmt_data['closing_balance']} {stmt_data['closing_balance_type']}")
    assert stmt_data["total_debit"] == 10000.0
    assert stmt_data["total_credit"] == 8000.0
    assert stmt_data["closing_balance"] == 2000.0
    assert stmt_data["closing_balance_type"] == "Dr"

    # 6. Attempt to pay Rs.3,000 when only Rs.2,000 is pending
    print("\n--- Test 4: Excess Payment Rejection (Rs.3,000 on Rs.2,000 Due) ---")
    res_over2 = client.post(
        f"/accounting/customers/{cust_id}/receipt",
        json={
            "payment_mode": "UPI",
            "amount": 3000.0,
            "transaction_ref": "UPI-ADV-112233",
        }
    )
    print("  Response Code:", res_over2.status_code)
    print("  Detail:", res_over2.json().get("detail"))
    assert res_over2.status_code == 400
    assert "Excess payment is not allowed" in res_over2.json()["detail"]

    # 7. Settle exact remaining Rs.2,000
    print("\n--- Test 5: Settle Exact Remaining Rs.2,000 ---")
    res_exact = client.post(
        f"/accounting/customers/{cust_id}/receipt",
        json={
            "payment_mode": "UPI",
            "amount": 2000.0,
            "transaction_ref": "UPI-SETTLE-445566",
        }
    )
    assert res_exact.status_code == 200
    summary_exact = res_exact.json()["summary"]
    print(f"  Allocated: Rs.{summary_exact['allocated_to_bills']}, Remaining Due: Rs.{summary_exact['remaining_customer_due']}")
    assert summary_exact["allocated_to_bills"] == 2000.0
    assert summary_exact["remaining_customer_due"] == 0.0

    # Verify Bill 2 is now fully PAID
    ord2_final = orders_collection.find_one({"_id": ObjectId(ord2_id)})
    assert ord2_final["pending_amount"] == 0.0
    assert ord2_final["payment_status"] == "PAID"

    # 8. Attempt payment when all bills are cleared
    print("\n--- Test 6: Payment Rejection when No Dues Pending ---")
    res_zero = client.post(
        f"/accounting/customers/{cust_id}/receipt",
        json={
            "payment_mode": "CASH",
            "amount": 500.0,
        }
    )
    print("  Response Code:", res_zero.status_code)
    print("  Detail:", res_zero.json().get("detail"))
    assert res_zero.status_code == 400
    assert "no outstanding dues to collect" in res_zero.json()["detail"]

    # Verify final Customer Statement shows exact Rs.0.00
    res_stmt_final = client.get(f"/accounting/customers/{cust_id}/statement")
    final_stmt = res_stmt_final.json()["data"]
    print(f"  Final Statement: Total Billed=Rs.{final_stmt['total_debit']}, Total Paid=Rs.{final_stmt['total_credit']}")
    print(f"  Final Balance: Rs.{final_stmt['closing_balance']} {final_stmt['closing_balance_type']}")
    assert final_stmt["total_debit"] == 10000.0
    assert final_stmt["total_credit"] == 10000.0
    assert final_stmt["closing_balance"] == 0.0

    # Clean up
    orders_collection.delete_many({"order_no": {"$regex": "ORD-FIFO-"}})
    customers_collection.delete_many({"name": "Gupta Enterprises"})
    vouchers_collection.delete_many({"narration": {"$regex": "Gupta Enterprises"}})
    ledgers_collection.delete_many({"ledger_name": {"$regex": "Gupta Enterprises"}})

    print("\n>>> CUSTOMER-LEVEL FIFO RECEIPT TEST PASSED 100%! <<<")


if __name__ == "__main__":
    run_customer_fifo_test()
