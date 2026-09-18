import os
import sys
from datetime import datetime, timezone, timedelta
from bson import ObjectId

# Ensure workspace root is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient
from main import app
from database import (
    users_collection,
    customers_collection,
    orders_collection,
    vouchers_collection,
    ledgers_collection,
    groups_collection,
)
from routes.auth import get_current_user
from services.accounting_service import (
    ensure_system_ledgers,
    ensure_customer_ledger,
    ensure_employee_cash_ledger,
    get_employee_cash_balance,
    get_all_employees_cash_balances,
    record_employee_cash_handover,
)

def run_test():
    print("=" * 65)
    print("TESTING UNIVERSAL EMPLOYEE CASH CUSTODY, TRACKING & HANDOVERS")
    print("=" * 65)

    now = datetime.now(timezone.utc)
    mock_admin_id = str(ObjectId())

    # Mock authentication override
    app.dependency_overrides[get_current_user] = lambda: {
        "_id": ObjectId(mock_admin_id),
        "user_id": mock_admin_id,
        "name": "Head Office Cashier",
        "role": "admin",
    }
    client = TestClient(app)

    # 1. Setup Associate / Employee (e.g. Sales rep / delivery driver)
    emp_name = f"Ramesh Sharma {str(ObjectId())[-6:]}"
    emp_res = users_collection.insert_one({
        "name": emp_name,
        "phone": "9876500123",
        "role": "delivery_driver",
        "email": "ramesh.driver@sadapoorna.test",
        "status": "ACTIVE",
        "created_at": now,
    })
    emp_id = str(emp_res.inserted_id)
    print(f"\n[Step 1] Created Employee / Associate: {emp_name} (ID: {emp_id})")

    # Verify employee cash ledger is auto-provisioned
    emp_ledger_id, emp_ledger_name = ensure_employee_cash_ledger(emp_id)
    print(f"  Employee Cash Custody Ledger: {emp_ledger_name} (ID: {emp_ledger_id})")

    # 2. Setup Customer & Orders
    cust_res = customers_collection.insert_one({
        "name": "Gupta Kirana Store",
        "phone": "9811122233",
        "state": "Madhya Pradesh",
        "credit_days": 15,
        "created_at": now,
    })
    cust_id = str(cust_res.inserted_id)

    # Order 1 (Invoice 1: Rs. 6,000)
    ord_1 = orders_collection.insert_one({
        "order_no": "ORD-EMP-001",
        "invoice_no": "INV-EMP-001",
        "customer_id": ObjectId(cust_id),
        "type": "sale",
        "record_status": "active",
        "grand_total": 6000.0,
        "bill_amount": 6000.0,
        "paid_amount": 0.0,
        "pending_amount": 6000.0,
        "payment_status": "UNPAID",
        "billed_at": now,
        "created_at": now,
    })
    ord_1_id = str(ord_1.inserted_id)

    # Order 2 (Invoice 2: Rs. 4,000)
    ord_2 = orders_collection.insert_one({
        "order_no": "ORD-EMP-002",
        "invoice_no": "INV-EMP-002",
        "customer_id": ObjectId(cust_id),
        "type": "sale",
        "record_status": "active",
        "grand_total": 4000.0,
        "bill_amount": 4000.0,
        "paid_amount": 0.0,
        "pending_amount": 4000.0,
        "payment_status": "UNPAID",
        "billed_at": now,
        "created_at": now,
    })
    ord_2_id = str(ord_2.inserted_id)
    print(f"[Step 2] Billed 2 orders for Gupta Kirana Store. Total Due: Rs. 10,000")

    # Initial employee cash balance should be 0.0
    bal_initial = get_employee_cash_balance(emp_id)
    assert bal_initial["current_cash_in_hand"] == 0.0, f"Expected 0.0, got {bal_initial['current_cash_in_hand']}"
    print(f"  Ramesh initial cash in hand: Rs. {bal_initial['current_cash_in_hand']}")

    # 3. Ramesh collects Cash on Order 1: Rs. 4,000 CASH
    print(f"\n[Step 3] Ramesh collects Rs. 4,000 CASH for Order 1 at customer doorstep")
    resp_rcpt1 = client.post(
        f"/accounting/customers/{cust_id}/receipt",
        json={
            "payment_mode": "CASH",
            "amount": 4000.0,
            "collected_by_id": emp_id,
            "notes": "Doorstep cash collected by delivery driver Ramesh",
        }
    )
    assert resp_rcpt1.status_code == 200, f"Customer receipt failed: {resp_rcpt1.text}"
    v1 = resp_rcpt1.json()["voucher"]
    print(f"  Receipt Voucher: {v1['voucher_number']}, Amount: Rs. {v1['amount']}")
    print(f"  Voucher Entries:")
    for entry in v1["entries"]:
        print(f"    - {entry['ledger_name']}: Dr Rs.{entry['debit']} | Cr Rs.{entry['credit']}")

    # Verify debit went to Employee Cash - Ramesh Kumar Sharma
    assert any(e["ledger_id"] == emp_ledger_id and e["debit"] == 4000.0 for e in v1["entries"]), "Expected Ramesh cash ledger debited"

    bal_step3 = get_employee_cash_balance(emp_id)
    print(f"  Ramesh current cash in hand: Rs. {bal_step3['current_cash_in_hand']}")
    assert bal_step3["current_cash_in_hand"] == 4000.0

    # 4. Ramesh collects Customer-Level FIFO Receipt: Rs. 3,500 CASH
    print(f"\n[Step 4] Ramesh collects Rs. 3,500 CASH via Customer-Level FIFO Receipt")
    resp_rcpt2 = client.post(
        f"/accounting/customers/{cust_id}/receipt",
        json={
            "payment_mode": "CASH",
            "amount": 3500.0,
            "collected_by_id": emp_id,
            "notes": "Field collection lump-sum by associate Ramesh",
        }
    )
    assert resp_rcpt2.status_code == 200, f"FIFO receipt failed: {resp_rcpt2.text}"
    v2 = resp_rcpt2.json()["voucher"]
    print(f"  FIFO Receipt Voucher: {v2['voucher_number']}, Amount: Rs. {v2['amount']}")
    for entry in v2["entries"]:
        print(f"    - {entry['ledger_name']}: Dr Rs.{entry['debit']} | Cr Rs.{entry['credit']}")

    bal_step4 = get_employee_cash_balance(emp_id)
    print(f"  Ramesh current cash in hand: Rs. {bal_step4['current_cash_in_hand']}")
    assert bal_step4["current_cash_in_hand"] == 7500.0, f"Expected 7500.0, got {bal_step4['current_cash_in_hand']}"

    # 5. Check Admin Dashboard: All Employees Cash Balances
    print(f"\n[Step 5] Admin queries GET /accounting/employees/cash-balances")
    resp_balances = client.get("/accounting/employees/cash-balances")
    assert resp_balances.status_code == 200
    all_bals = resp_balances.json()["data"]
    ramesh_entry = next((e for e in all_bals if e["employee_id"] == emp_id), None)
    assert ramesh_entry is not None, "Ramesh must appear in all employee balances"
    print(f"  Dashboard lists {len(all_bals)} staff members holding cash.")
    print(f"  Found Ramesh: {ramesh_entry['employee_name']} - Cash in Hand: Rs. {ramesh_entry['current_cash_in_hand']}")
    assert ramesh_entry["current_cash_in_hand"] == 7500.0

    # 6. Check Single Employee Cash Summary
    print(f"\n[Step 6] Admin queries GET /accounting/employees/{emp_id}/cash-summary")
    resp_summary = client.get(f"/accounting/employees/{emp_id}/cash-summary")
    assert resp_summary.status_code == 200
    summary_data = resp_summary.json()["data"]
    print(f"  Total Cash Collected: Rs. {summary_data['total_cash_collected']}")
    print(f"  Total Cash Handed Over: Rs. {summary_data['total_cash_handed_over']}")
    print(f"  Current Cash in Hand: Rs. {summary_data['current_cash_in_hand']}")
    print(f"  Transaction Count: {len(summary_data['recent_transactions'])}")
    assert summary_data["current_cash_in_hand"] == 7500.0
    assert len(summary_data["recent_transactions"]) == 2

    # 7. Validation: Attempt to hand over MORE than cash in hand (Rs. 8,000 on Rs. 7,500)
    print(f"\n[Step 7] Validation Test: Attempting handover of Rs. 8,000 when holding Rs. 7,500")
    resp_over_handover = client.post(
        "/accounting/employees/cash-handover",
        json={
            "employee_id": emp_id,
            "amount": 8000.0,
            "handover_to": "SAFE",
            "notes": "Testing excess handover prevention",
        }
    )
    print(f"  Response Code: {resp_over_handover.status_code}")
    print(f"  Detail: {resp_over_handover.json().get('detail')}")
    assert resp_over_handover.status_code == 400
    assert "exceeds employee's current cash in hand" in resp_over_handover.json()["detail"]

    # 8. Handover Part 1: Ramesh hands over Rs. 2,500 cash to Head Office Safe
    print(f"\n[Step 8] Ramesh hands over Rs. 2,500 to Company Safe (Contra Voucher)")
    resp_ho1 = client.post(
        "/accounting/employees/cash-handover",
        json={
            "employee_id": emp_id,
            "amount": 2500.0,
            "handover_to": "SAFE",
            "notes": "Day-end cash handover to head office safe",
        }
    )
    assert resp_ho1.status_code == 200, f"Handover 1 failed: {resp_ho1.text}"
    ho1_voucher = resp_ho1.json()["voucher"]
    ho1_summary = resp_ho1.json()["summary"]
    print(f"  Contra Voucher: {ho1_voucher['voucher_number']}, Amount: Rs. {ho1_voucher['amount']}")
    print(f"  Contra Entries:")
    for entry in ho1_voucher["entries"]:
        print(f"    - {entry['ledger_name']}: Dr Rs.{entry['debit']} | Cr Rs.{entry['credit']}")
    print(f"  Remaining Cash in Hand: Rs. {ho1_summary['remaining_cash_in_hand']}")
    assert ho1_summary["remaining_cash_in_hand"] == 5000.0

    # 9. Handover Part 2: Ramesh deposits the remaining Rs. 5,000 into Bank CDM
    print(f"\n[Step 9] Ramesh deposits remaining Rs. 5,000 into Bank CDM (Contra Voucher)")
    resp_ho2 = client.post(
        "/accounting/employees/cash-handover",
        json={
            "employee_id": emp_id,
            "amount": 5000.0,
            "handover_to": "BANK",
            "transaction_ref": "CDM-SLIP-998877",
            "notes": "Direct bank deposit via Bank of Baroda CDM",
        }
    )
    assert resp_ho2.status_code == 200, f"Handover 2 failed: {resp_ho2.text}"
    ho2_voucher = resp_ho2.json()["voucher"]
    ho2_summary = resp_ho2.json()["summary"]
    print(f"  Contra Voucher: {ho2_voucher['voucher_number']}, Amount: Rs. {ho2_voucher['amount']}")
    for entry in ho2_voucher["entries"]:
        print(f"    - {entry['ledger_name']}: Dr Rs.{entry['debit']} | Cr Rs.{entry['credit']}")
    print(f"  Remaining Cash in Hand: Rs. {ho2_summary['remaining_cash_in_hand']}")
    assert ho2_summary["remaining_cash_in_hand"] == 0.0

    # 10. Final Verification: Check employee cash summary is completely cleared
    print(f"\n[Step 10] Final Audit: Verifying Ramesh's Cash Ledger is clean (Rs. 0.00)")
    final_summary = client.get(f"/accounting/employees/{emp_id}/cash-summary").json()["data"]
    print(f"  Total Cash Collected: Rs. {final_summary['total_cash_collected']}")
    print(f"  Total Cash Handed Over: Rs. {final_summary['total_cash_handed_over']}")
    print(f"  Current Cash in Hand: Rs. {final_summary['current_cash_in_hand']}")
    print(f"  Total Transactions in Ledger: {len(final_summary['recent_transactions'])}")
    assert final_summary["current_cash_in_hand"] == 0.0
    assert final_summary["total_cash_collected"] == 7500.0
    assert final_summary["total_cash_handed_over"] == 7500.0
    assert len(final_summary["recent_transactions"]) == 4

    print("\n" + ">" * 3 + " UNIVERSAL EMPLOYEE CASH CUSTODY TEST PASSED 100%! " + "<" * 3)

if __name__ == "__main__":
    run_test()
