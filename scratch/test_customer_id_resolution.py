"""
Verification script for Customer ID resolution and 404 handling in Accounting:
1. calculate_customer_aging with Custom ID (e.g. 'CUST1002')
2. calculate_customer_aging with Mongo ObjectId hex (e.g. '6a8d78486d2fb47c4c4bddac')
3. calculate_customer_aging with Non-existent ID -> Raises ValueError (Customer not found)
4. FastAPI endpoints:
   - GET /accounting/customers/{customer_id}/aging
     * Custom ID -> 200 OK
     * Mongo ID -> 200 OK
     * Non-existent ID -> 404 Not Found (NOT zero balance)
   - GET /accounting/customers/{customer_id}/statement
     * Custom ID -> 200 OK
     * Non-existent ID -> 404 Not Found
   - POST /accounting/customers/{customer_id}/receipt
     * Non-existent ID -> 404 Not Found
"""

import os
import sys
sys.path.insert(0, os.path.abspath("."))
from bson import ObjectId
from fastapi.testclient import TestClient

from main import app
from routes.auth import get_current_user
from database import customers_collection, orders_collection
from services.accounting_service import (
    resolve_customer,
    calculate_customer_aging,
    get_customer_statement,
)

app.dependency_overrides[get_current_user] = lambda: {"user_id": "test_admin", "role": "admin"}
client = TestClient(app)

def run_tests():
    print("=" * 60)
    print("CUSTOMER RESOLUTION & 404 NOT FOUND TEST SUITE")
    print("=" * 60)

    # 1. Fetch an existing customer from DB
    cust_doc = customers_collection.find_one({"id": {"$regex": "^CUST"}})
    if not cust_doc:
        print("[!] No customer with custom ID found in DB. Creating a temporary customer.")
        res = customers_collection.insert_one({
            "id": "CUST9001",
            "name": "Test Customer Resolution",
            "mobile": "919999999999",
            "credit_days": 15,
            "credit_limit": 50000.0,
            "status": "active"
        })
        cust_doc = customers_collection.find_one({"_id": res.inserted_id})
    
    mongo_id_str = str(cust_doc["_id"])
    custom_id_str = cust_doc.get("id")
    print(f"Testing with existing customer: Mongo ID = {mongo_id_str}, Custom ID = {custom_id_str}")

    # Test 1: resolve_customer with custom_id
    res_oid, resolved_doc = resolve_customer(custom_id_str)
    assert res_oid == cust_doc["_id"], f"Expected {cust_doc['_id']}, got {res_oid}"
    assert resolved_doc["id"] == custom_id_str
    print(f"[PASS] 1. resolve_customer('{custom_id_str}') resolved correctly.")

    # Test 2: resolve_customer with mongo_id
    res_oid2, resolved_doc2 = resolve_customer(mongo_id_str)
    assert res_oid2 == cust_doc["_id"], f"Expected {cust_doc['_id']}, got {res_oid2}"
    assert resolved_doc2["id"] == custom_id_str
    print(f"[PASS] 2. resolve_customer('{mongo_id_str}') resolved correctly.")

    # Test 3: resolve_customer with non-existent ID
    try:
        resolve_customer("CUST_DOES_NOT_EXIST_9999")
        assert False, "Should have raised ValueError"
    except ValueError as ve:
        assert "not found" in str(ve).lower()
        print(f"[PASS] 3. resolve_customer with non-existent ID raised ValueError: {ve}")

    # Test 4: calculate_customer_aging direct calls
    aging_by_custom = calculate_customer_aging(custom_id_str)
    assert aging_by_custom["customer_id"] == mongo_id_str
    assert aging_by_custom["custom_id"] == custom_id_str
    print(f"[PASS] 4. calculate_customer_aging('{custom_id_str}') returned customer aging: total_outstanding={aging_by_custom['total_outstanding']}")

    aging_by_mongo = calculate_customer_aging(mongo_id_str)
    assert aging_by_mongo["customer_id"] == mongo_id_str
    assert aging_by_mongo["custom_id"] == custom_id_str
    print(f"[PASS] 5. calculate_customer_aging('{mongo_id_str}') returned customer aging: total_outstanding={aging_by_mongo['total_outstanding']}")

    # Test 5: calculate_customer_aging with non-existent customer must raise ValueError
    try:
        calculate_customer_aging("NONEXISTENT_CUST_8888")
        assert False, "Should have raised ValueError"
    except ValueError as ve:
        assert "not found" in str(ve).lower()
        print(f"[PASS] 6. calculate_customer_aging with non-existent customer raised ValueError: {ve}")

    # Test 6: API endpoint GET /accounting/customers/{customer_id}/aging
    # 6a. Using custom ID -> 200 OK
    resp = client.get(f"/accounting/customers/{custom_id_str}/aging")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()["data"]
    assert data["customer_id"] == mongo_id_str
    assert data["custom_id"] == custom_id_str
    print(f"[PASS] 7. GET /accounting/customers/{custom_id_str}/aging returned 200 OK")

    # 6b. Using Mongo ID -> 200 OK
    resp = client.get(f"/accounting/customers/{mongo_id_str}/aging")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()["data"]
    assert data["customer_id"] == mongo_id_str
    assert data["custom_id"] == custom_id_str
    print(f"[PASS] 8. GET /accounting/customers/{mongo_id_str}/aging returned 200 OK")

    # 6c. Using non-existent custom ID -> MUST RETURN 404 NOT FOUND (NOT ZERO BALANCE!)
    resp_404 = client.get("/accounting/customers/NONEXISTENT_CUST_9999/aging")
    assert resp_404.status_code == 404, f"Expected 404 Not Found, got {resp_404.status_code}: {resp_404.text}"
    print(f"[PASS] 9. GET /accounting/customers/NONEXISTENT_CUST_9999/aging returned 404 Not Found: {resp_404.json()}")

    # 6d. Using non-existent Mongo ID -> MUST RETURN 404 NOT FOUND
    resp_404_mongo = client.get("/accounting/customers/6a8d78486d2fb47c4c4b9999/aging")
    assert resp_404_mongo.status_code == 404, f"Expected 404 Not Found, got {resp_404_mongo.status_code}: {resp_404_mongo.text}"
    print(f"[PASS] 10. GET /accounting/customers/6a8d78486d2fb47c4c4b9999/aging returned 404 Not Found: {resp_404_mongo.json()}")

    # Test 7: API endpoint GET /accounting/customers/{customer_id}/statement
    # 7a. Using custom ID -> 200 OK
    resp_stmt = client.get(f"/accounting/customers/{custom_id_str}/statement")
    assert resp_stmt.status_code == 200, f"Expected 200, got {resp_stmt.status_code}: {resp_stmt.text}"
    stmt_data = resp_stmt.json()["data"]
    assert stmt_data["customer"]["id"] == mongo_id_str
    assert stmt_data["customer"]["custom_id"] == custom_id_str
    print(f"[PASS] 11. GET /accounting/customers/{custom_id_str}/statement returned 200 OK")

    # 7b. Using non-existent ID -> 404 Not Found
    resp_stmt_404 = client.get("/accounting/customers/NONEXISTENT_CUST_9999/statement")
    assert resp_stmt_404.status_code == 404, f"Expected 404 Not Found, got {resp_stmt_404.status_code}: {resp_stmt_404.text}"
    print(f"[PASS] 12. GET /accounting/customers/NONEXISTENT_CUST_9999/statement returned 404 Not Found: {resp_stmt_404.json()}")

    # Test 8: API endpoint POST /accounting/customers/{customer_id}/receipt
    # Non-existent customer -> 404 Not Found
    payload = {
        "payment_mode": "CASH",
        "amount": 1000.0,
        "notes": "Test non-existent customer receipt"
    }
    resp_rcpt_404 = client.post("/accounting/customers/NONEXISTENT_CUST_9999/receipt", json=payload)
    assert resp_rcpt_404.status_code == 404, f"Expected 404 Not Found, got {resp_rcpt_404.status_code}: {resp_rcpt_404.text}"
    print(f"[PASS] 13. POST /accounting/customers/NONEXISTENT_CUST_9999/receipt returned 404 Not Found: {resp_rcpt_404.json()}")

    # Test 9: Test specific customer CUST1002 / 6a8d78486d2fb47c4c4bddac
    resp_cust1002 = client.get("/accounting/customers/CUST1002/aging")
    assert resp_cust1002.status_code == 200
    d_custom = resp_cust1002.json()["data"]
    
    resp_cust1002_mongo = client.get("/accounting/customers/6a8d78486d2fb47c4c4bddac/aging")
    assert resp_cust1002_mongo.status_code == 200
    d_mongo = resp_cust1002_mongo.json()["data"]

    assert d_custom["customer_id"] == "6a8d78486d2fb47c4c4bddac"
    assert d_custom["custom_id"] == "CUST1002"
    assert d_custom["total_outstanding"] == d_mongo["total_outstanding"]
    assert d_custom["open_invoices_count"] == d_mongo["open_invoices_count"]
    print(f"[PASS] 14. CUST1002 and 6a8d78486d2fb47c4c4bddac returned identical aging data: outstanding={d_custom['total_outstanding']}, bills_count={d_custom['open_invoices_count']}")

    print("\nALL CUSTOMER RESOLUTION & 404 CHECKS PASSED PERFECTLY!")

if __name__ == "__main__":
    run_tests()
