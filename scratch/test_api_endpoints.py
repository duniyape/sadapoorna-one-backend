"""
API Endpoint verification for:
- POST /orders/receipt/v1/{order_id}
- GET /accounting/customers/{customer_id}/aging
- GET /accounting/customers/{customer_id}/statement
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
    users_collection,
)
from routes.auth import get_current_user


def run_api_tests():
    print("=================================================================")
    print("TESTING FASTAPI ENDPOINTS VIA TESTCLIENT")
    print("=================================================================")

    # Override auth dependency for tests
    mock_user = {
        "user_id": "test_admin_user",
        "_id": ObjectId("65e000000000000000000001"),
        "role": "admin",
    }
    app.dependency_overrides[get_current_user] = lambda: mock_user
    client = TestClient(app)

    # Clean up previous test runs for API Test Client
    vouchers_collection.delete_many({"narration": {"$regex": "INV-API-"}})
    orders_collection.delete_many({"order_no": {"$regex": "ORD-API-"}})
    customers_collection.delete_many({"name": "API Test Client"})
    ledgers_collection.delete_many({"ledger_name": {"$regex": "API Test Client"}})

    # 1. Create a customer
    now = datetime.now(timezone.utc)
    cust_res = customers_collection.insert_one({
        "name": "API Test Client",
        "phone": "919123456789",
        "credit_days": 10,
        "credit_limit": 50000.0,
        "created_at": now,
    })
    cust_id = str(cust_res.inserted_id)

    # 2. Create a billed order
    order_res = orders_collection.insert_one({
        "type": "sale",
        "order_no": "ORD-API-001",
        "invoice_no": "INV-API-001",
        "customer_id": ObjectId(cust_id),
        "subtotal": 5000.0,
        "total_gst": 900.0,
        "grand_total": 5900.0,
        "bill_amount": 5900.0,
        "paid_amount": 0.0,
        "pending_amount": 5900.0,
        "credit_days": 10,
        "due_date": now + timedelta(days=10),
        "status": "Delivered",
        "record_status": "active",
        "payment_status": "UNPAID",
        "billed_at": now,
        "created_at": now,
    })
    order_id = str(order_res.inserted_id)

    # Create initial sales voucher
    from services.accounting_service import create_sales_invoice_voucher
    order_doc = orders_collection.find_one({"_id": ObjectId(order_id)})
    create_sales_invoice_voucher(order_doc, "INV-API-001", "test_admin_user")

    # 3. Test POST /accounting/customers/{cust_id}/receipt - Pay Cash Rs.2,500
    res_cash = client.post(
        f"/accounting/customers/{cust_id}/receipt",
        json={
            "payment_mode": "CASH",
            "amount": 2500.0,
            "notes": "Doorstep cash collection",
        }
    )
    print("  Receipt 1 (Cash) Response Code:", res_cash.status_code)
    cash_data = res_cash.json()
    assert res_cash.status_code == 200, res_cash.text
    assert cash_data["success"] is True
    print("    Created Voucher:", cash_data["voucher"]["voucher_number"])
    print("    Remaining Customer Due:", cash_data["summary"]["remaining_customer_due"])
    assert cash_data["summary"]["remaining_customer_due"] == 3400.0

    # 4. Test POST /accounting/customers/{cust_id}/receipt - Pay UPI Rs.1,400
    res_upi = client.post(
        f"/accounting/customers/{cust_id}/receipt",
        json={
            "payment_mode": "UPI",
            "amount": 1400.0,
            "transaction_ref": "UPI-REF-998877",
            "notes": "QR payment",
        }
    )
    print("  Receipt 2 (UPI) Response Code:", res_upi.status_code)
    upi_data = res_upi.json()
    assert res_upi.status_code == 200, res_upi.text
    assert upi_data["success"] is True
    print("    Created Voucher:", upi_data["voucher"]["voucher_number"])
    print("    Remaining Customer Due:", upi_data["summary"]["remaining_customer_due"])
    assert upi_data["summary"]["remaining_customer_due"] == 2000.0

    # 5. Test GET /accounting/customers/{customer_id}/statement
    res_stmt = client.get(f"/accounting/customers/{cust_id}/statement")
    print("  Customer Statement Response Code:", res_stmt.status_code)
    assert res_stmt.status_code == 200, res_stmt.text
    stmt_data = res_stmt.json()["data"]
    print(f"    Total Billed: Rs.{stmt_data['total_debit']}, Total Received: Rs.{stmt_data['total_credit']}")
    print(f"    Closing Balance: Rs.{stmt_data['closing_balance']} {stmt_data['closing_balance_type']}")
    assert stmt_data["total_debit"] == 5900.0
    assert stmt_data["total_credit"] == 3900.0
    assert stmt_data["closing_balance"] == 2000.0
    assert len(stmt_data["transactions"]) == 3

    # 6. Test GET /accounting/customers/{customer_id}/aging
    res_aging = client.get(f"/accounting/customers/{cust_id}/aging")
    print("  Customer Aging Response Code:", res_aging.status_code)
    assert res_aging.status_code == 200, res_aging.text
    aging_data = res_aging.json()["data"]
    print(f"    Outstanding: Rs.{aging_data['total_outstanding']}, Max DPD: {aging_data['max_dpd']}")
    assert aging_data["total_outstanding"] == 2000.0

    # Cleanup
    orders_collection.delete_one({"_id": ObjectId(order_id)})
    customers_collection.delete_one({"_id": ObjectId(cust_id)})
    vouchers_collection.delete_many({"order_id": ObjectId(order_id)})

    print("\n>>> ALL API ENDPOINTS TESTED SUCCESSFULLY 100%! <<<")


if __name__ == "__main__":
    run_api_tests()
