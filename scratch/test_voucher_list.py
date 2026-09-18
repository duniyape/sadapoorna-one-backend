import os
import sys
from bson import ObjectId

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient
from main import app
from routes.auth import get_current_user

def test_vouchers():
    mock_admin_id = str(ObjectId())
    app.dependency_overrides[get_current_user] = lambda: {
        "_id": ObjectId(mock_admin_id),
        "user_id": mock_admin_id,
        "name": "Admin Tester",
        "role": "admin",
    }
    client = TestClient(app)

    # Test GET /accounting/vouchers (or /vouchers)
    # Let's check routes in app
    print("Testing GET /accounting/vouchers (without slash)...")
    resp1 = client.get("/accounting/vouchers")
    print(f"Status: {resp1.status_code}")
    assert resp1.status_code == 200, f"Error: {resp1.text}"

    print("Testing GET /accounting/vouchers/ (with slash)...")
    resp2 = client.get("/accounting/vouchers/")
    print(f"Status: {resp2.status_code}")
    assert resp2.status_code == 200, f"Error: {resp2.text}"

    data = resp1.json()
    print(f"Found {data.get('count', len(data.get('data', [])))} vouchers.")
    if data.get("data"):
        first = data["data"][0]
        print(f"Sample voucher: {first.get('voucher_number')} ({first.get('voucher_type')})")
        print(f"  _id: {first.get('_id')}")
        print(f"  customer_id: {first.get('customer_id')}")
        print(f"  order_id: {first.get('order_id')}")
        print(f"  employee_id: {first.get('employee_id')}")
        print(f"  affected_order_ids: {first.get('affected_order_ids')}")

    print("\n>>> GET /vouchers/ SUCCESSFUL! ObjectIds serialized cleanly! <<<")

if __name__ == "__main__":
    test_vouchers()
