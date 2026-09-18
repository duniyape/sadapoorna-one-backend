"""
End-to-End Verification Test for ERP Sales Order Vouchers,
Single-Mode Receipt Settlement, Customer DPD & Debtors Aging,
and Customer Statement Generation.
"""
import sys
import os
from datetime import datetime, timezone, timedelta
from bson import ObjectId

# Add current dir to path
sys.path.insert(0, os.path.abspath("."))

from database import (
    orders_collection,
    customers_collection,
    vouchers_collection,
    ledgers_collection,
    users_collection,
)
from services.accounting_service import (
    ensure_system_ledgers,
    ensure_customer_ledger,
    create_sales_invoice_voucher,
    record_order_receipt_voucher,
    calculate_customer_aging,
    get_customer_statement,
)


def run_tests():
    print("=================================================================")
    print("STEP 1: Verify System Ledgers & Customer Ledger Provisioning")
    print("=================================================================")
    # Clean up any leftover test data
    vouchers_collection.delete_many({"narration": {"$regex": "INV-TEST-"}})
    ledgers_collection.delete_many({"ledger_name": {"$regex": "Test Wholesale Distributor"}})
    customers_collection.delete_many({"name": {"$regex": "Test Wholesale Distributor"}})

    sys_ledgers = ensure_system_ledgers()
    for key, (lid, lname) in sys_ledgers.items():
        print(f"  System Ledger [{key}]: {lname} (ID: {lid})")
    assert "sales" in sys_ledgers and "cash" in sys_ledgers and "bank" in sys_ledgers

    # Create a test customer
    now = datetime.now(timezone.utc)
    cust_res = customers_collection.insert_one({
        "name": "Test Wholesale Distributor",
        "phone": "919988776655",
        "credit_days": 15,
        "credit_limit": 100000.0,
        "created_at": now,
    })
    cust_id = str(cust_res.inserted_id)

    cust_lid, cust_lname = ensure_customer_ledger(cust_id, "Test Wholesale Distributor")
    print(f"  Customer Ledger Provisioned: {cust_lname} (ID: {cust_lid})")
    assert cust_lid is not None

    print("\n=================================================================")
    print("STEP 2: Sales Order Billing & Sales Invoice Voucher Generation")
    print("=================================================================")
    # Create mock billed order
    order_doc = {
        "type": "sale",
        "order_no": "ORD-TEST-001",
        "invoice_no": "INV-TEST-001",
        "customer_id": ObjectId(cust_id),
        "subtotal": 10000.0,
        "discount": 0.0,
        "other_charges": 0.0,
        "total_gst": 1800.0,
        "gst_type": "including",
        "grand_total": 11800.0,
        "bill_amount": 11800.0,
        "paid_amount": 0.0,
        "pending_amount": 11800.0,
        "credit_days": 15,
        "due_date": now + timedelta(days=15),
        "status": "Delivered",
        "record_status": "active",
        "payment_status": "UNPAID",
        "billed_at": now,
        "created_at": now,
    }
    ord_res = orders_collection.insert_one(order_doc)
    order_id = str(ord_res.inserted_id)
    order_doc["_id"] = ord_res.inserted_id

    sales_voucher = create_sales_invoice_voucher(
        order=order_doc,
        invoice_no="INV-TEST-001",
        user_id="test_user_admin",
    )
    print(f"  Sales Voucher Created: {sales_voucher['voucher_number']}")
    print(f"  Voucher Type: {sales_voucher['voucher_type']}, Amount: Rs.{sales_voucher['amount']}")
    total_deb = sum(e["debit"] for e in sales_voucher["entries"])
    total_cred = sum(e["credit"] for e in sales_voucher["entries"])
    print(f"  Double-Entry Balance: Total Debit=Rs.{total_deb}, Total Credit=Rs.{total_cred}")
    assert round(total_deb, 2) == round(total_cred, 2) == 11800.0

    print("\n=================================================================")
    print("STEP 3: Multi-Mode Settlement using Single-Mode Receipt Vouchers")
    print("=================================================================")
    # Voucher A: Pay Rs.4,000 Cash
    v1, updated_ord_1 = record_order_receipt_voucher(
        order_id=order_id,
        payment_mode="CASH",
        amount=4000.0,
        user_id="delivery_agent_1",
    )
    print(f"  Receipt Voucher 1 (Cash): {v1['voucher_number']}")
    print(f"    Amount: Rs.{v1['amount']}, Order Paid: Rs.{updated_ord_1['paid_amount']}, Pending: Rs.{updated_ord_1['pending_amount']}, Status: {updated_ord_1['payment_status']}")
    assert updated_ord_1["paid_amount"] == 4000.0
    assert updated_ord_1["pending_amount"] == 7800.0
    assert updated_ord_1["payment_status"] == "PARTIALLY_PAID"

    # Voucher B: Pay Rs.3,000 UPI
    v2, updated_ord_2 = record_order_receipt_voucher(
        order_id=order_id,
        payment_mode="UPI",
        amount=3000.0,
        transaction_ref="UPI/UTR/99881122",
        user_id="delivery_agent_1",
    )
    print(f"  Receipt Voucher 2 (UPI): {v2['voucher_number']}")
    print(f"    Amount: Rs.{v2['amount']}, Order Paid: Rs.{updated_ord_2['paid_amount']}, Pending: Rs.{updated_ord_2['pending_amount']}, Status: {updated_ord_2['payment_status']}")
    assert updated_ord_2["paid_amount"] == 7000.0
    assert updated_ord_2["pending_amount"] == 4800.0
    assert updated_ord_2["payment_status"] == "PARTIALLY_PAID"

    print("\n=================================================================")
    print("STEP 4: Customer Statement (Party Ledger / Running Balance)")
    print("=================================================================")
    stmt = get_customer_statement(customer_id=cust_id)
    print(f"  Customer: {stmt['customer']['name']}")
    print(f"  Opening Balance: Rs.{stmt['opening_balance']} {stmt['opening_balance_type']}")
    print(f"  Transactions ({len(stmt['transactions'])}):")
    for t in stmt["transactions"]:
        print(f"    - [{t['voucher_type']}] {t['voucher_number']} | Dr: Rs.{t['debit']:>8.2f} | Cr: Rs.{t['credit']:>8.2f} | Bal: Rs.{t['running_balance']:>8.2f} {t['balance_type']}")
    print(f"  Total Billed (Debit): Rs.{stmt['total_debit']}")
    print(f"  Total Received (Credit): Rs.{stmt['total_credit']}")
    print(f"  Closing Balance: Rs.{stmt['closing_balance']} {stmt['closing_balance_type']}")
    assert stmt["total_debit"] == 11800.0
    assert stmt["total_credit"] == 7000.0
    assert stmt["closing_balance"] == 4800.0
    assert stmt["closing_balance_type"] == "Dr"

    print("\n=================================================================")
    print("STEP 5: Customer DPD (Days Past Due) & Debtors Aging")
    print("=================================================================")
    aging_normal = calculate_customer_aging(customer_id=cust_id)
    print(f"  Outstanding: Rs.{aging_normal['total_outstanding']}, Overdue: Rs.{aging_normal['total_overdue']}")
    print(f"  Max DPD: {aging_normal['max_dpd']} days (Due date is in the future)")
    print(f"  Aging Buckets: {aging_normal['aging_buckets']}")
    assert aging_normal["max_dpd"] == 0
    assert aging_normal["aging_buckets"]["current"] == 4800.0

    # Simulate Overdue Bill (Due date was 20 days ago)
    past_due_date = now - timedelta(days=20)
    orders_collection.update_one({"_id": ObjectId(order_id)}, {"$set": {"due_date": past_due_date}})
    aging_overdue = calculate_customer_aging(customer_id=cust_id)
    print("\n  After backdating due date by 20 days:")
    print(f"    Outstanding: Rs.{aging_overdue['total_outstanding']}, Overdue: Rs.{aging_overdue['total_overdue']}")
    print(f"    Max DPD: {aging_overdue['max_dpd']} days")
    print(f"    Aging Buckets: {aging_overdue['aging_buckets']}")
    assert aging_overdue["max_dpd"] == 20
    assert aging_overdue["total_overdue"] == 4800.0
    assert aging_overdue["aging_buckets"]["days_1_30"] == 4800.0

    # Cleanup test documents
    orders_collection.delete_one({"_id": ObjectId(order_id)})
    customers_collection.delete_one({"_id": ObjectId(cust_id)})
    vouchers_collection.delete_many({"order_id": ObjectId(order_id)})
    ledgers_collection.delete_one({"_id": ObjectId(cust_lid)})

    print("\n>>> ALL TESTS PASSED SUCCESSFULLY 100%! <<<")


if __name__ == "__main__":
    run_tests()
