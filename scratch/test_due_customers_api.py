import os
import sys
sys.path.insert(0, os.path.abspath("."))
from datetime import datetime, timezone, timedelta
from bson import ObjectId
from collections import defaultdict
from database import orders_collection, customers_collection

def test_due_customers_calculation():
    now = datetime.now(timezone.utc)
    
    order_query = {
        "type": "sale",
        "record_status": "active",
        "pending_amount": {"$gt": 0.01},
        "invoice_no": {"$nin": [None, ""]},
    }
    open_orders = list(orders_collection.find(order_query).sort("billed_at", 1))
    print(f"Total open billed sale orders: {len(open_orders)}")

    # Group orders by customer identifier
    customer_orders_map = defaultdict(list)
    unique_cust_keys = set()
    for ord_doc in open_orders:
        cid = ord_doc.get("customer_id")
        if cid:
            customer_orders_map[str(cid)].append(ord_doc)
            unique_cust_keys.add(str(cid))

    print(f"Unique customer identifiers in open orders: {len(unique_cust_keys)}")

    # Batch fetch customers
    oids = [ObjectId(k) for k in unique_cust_keys if ObjectId.is_valid(k)]
    custom_ids = [k for k in unique_cust_keys if not ObjectId.is_valid(k)]
    
    query_parts = []
    if oids:
        query_parts.append({"_id": {"$in": oids}})
    if custom_ids:
        query_parts.append({"id": {"$in": custom_ids}})

    customer_lookup = {}
    if query_parts:
        cust_docs = list(customers_collection.find({"$or": query_parts}))
        for c in cust_docs:
            customer_lookup[str(c["_id"])] = c
            if c.get("id"):
                customer_lookup[c["id"]] = c

    # Merge orders under canonical customer _id
    canonical_customer_orders = defaultdict(list)
    for cid_str, orders in customer_orders_map.items():
        cust = customer_lookup.get(cid_str)
        canonical_id = str(cust["_id"]) if cust else cid_str
        canonical_customer_orders[canonical_id].extend(orders)

    customer_results = []

    for can_id, orders in canonical_customer_orders.items():
        cust = customer_lookup.get(can_id)
        
        total_outstanding = 0.0
        total_overdue = 0.0
        max_dpd = 0
        overdue_weight_sum = 0.0

        buckets = {
            "current": 0.0,
            "days_1_30": 0.0,
            "days_31_60": 0.0,
            "days_61_90": 0.0,
            "days_90_plus": 0.0,
        }

        open_bills = []
        oldest_bill_date = None
        oldest_due_date = None

        for ord_doc in orders:
            bill_amount = round(float(ord_doc.get("bill_amount", ord_doc.get("grand_total", 0.0))), 2)
            paid_amount = round(float(ord_doc.get("paid_amount", 0.0)), 2)
            pending = round(float(ord_doc.get("pending_amount", bill_amount - paid_amount)), 2)

            if pending <= 0.01:
                continue

            total_outstanding += pending

            billed_at = ord_doc.get("billed_at") or ord_doc.get("created_at") or now
            if billed_at.tzinfo is None:
                billed_at = billed_at.replace(tzinfo=timezone.utc)

            if oldest_bill_date is None or billed_at < oldest_bill_date:
                oldest_bill_date = billed_at

            due_date = ord_doc.get("due_date")
            if not due_date:
                credit_days = int(ord_doc.get("credit_days") or (cust.get("credit_days") if cust else 7) or 7)
                due_date = billed_at + timedelta(days=credit_days)

            if due_date.tzinfo is None:
                due_date = due_date.replace(tzinfo=timezone.utc)

            if oldest_due_date is None or due_date < oldest_due_date:
                oldest_due_date = due_date

            diff_days = (now.date() - due_date.date()).days
            dpd = max(0, diff_days)

            if dpd > 0:
                total_overdue += pending
                overdue_weight_sum += (pending * dpd)
                if dpd > max_dpd:
                    max_dpd = dpd

            if dpd == 0:
                buckets["current"] += pending
            elif 1 <= dpd <= 30:
                buckets["days_1_30"] += pending
            elif 31 <= dpd <= 60:
                buckets["days_31_60"] += pending
            elif 61 <= dpd <= 90:
                buckets["days_61_90"] += pending
            else:
                buckets["days_90_plus"] += pending

            open_bills.append({
                "order_id": str(ord_doc["_id"]),
                "order_no": ord_doc.get("order_no"),
                "invoice_no": ord_doc.get("invoice_no"),
                "billed_at": billed_at,
                "due_date": due_date,
                "bill_amount": bill_amount,
                "paid_amount": paid_amount,
                "pending_amount": pending,
                "dpd": dpd,
                "is_overdue": dpd > 0,
            })

        if total_outstanding <= 0.01:
            continue

        weighted_dpd = round(overdue_weight_sum / total_overdue, 1) if total_overdue > 0 else 0.0

        customer_results.append({
            "customer_id": can_id,
            "custom_id": cust.get("id") if cust else None,
            "customer_name": (cust.get("name") if cust else None) or (cust.get("company_name") if cust else None) or f"Customer {can_id[:8]}",
            "company_name": cust.get("company_name") if cust else None,
            "phone": (cust.get("mobile") if cust else None) or (cust.get("phone") if cust else None),
            "branch_id": str(cust.get("branch_id")) if (cust and cust.get("branch_id")) else None,
            "assigned_employee_id": str(cust.get("assigned_employee_id")) if (cust and cust.get("assigned_employee_id")) else None,
            "credit_limit": float(cust.get("credit_limit", 0.0)) if cust else 0.0,
            "credit_days": int(cust.get("credit_days", 7)) if cust else 7,
            "total_outstanding": round(total_outstanding, 2),
            "total_overdue": round(total_overdue, 2),
            "max_dpd": max_dpd,
            "weighted_dpd": weighted_dpd,
            "open_invoices_count": len(open_bills),
            "oldest_bill_date": oldest_bill_date,
            "oldest_due_date": oldest_due_date,
            "aging_buckets": {k: round(v, 2) for k, v in buckets.items()},
            "open_bills": open_bills,
        })

    # Sort descending by total_outstanding
    customer_results.sort(key=lambda c: c["total_outstanding"], reverse=True)

    print(f"Computed aging for {len(customer_results)} customers with dues:")
    for c in customer_results[:5]:
        print(f"  [{c['custom_id'] or c['customer_id'][:8]}] {c['customer_name']:<25} Outstanding: Rs.{c['total_outstanding']:<10} Overdue: Rs.{c['total_overdue']:<10} Max DPD: {c['max_dpd']:<4} Buckets: {c['aging_buckets']}")

    total_receivables = round(sum(c["total_outstanding"] for c in customer_results), 2)
    total_overdue = round(sum(c["total_overdue"] for c in customer_results), 2)
    print(f"\nPortfolio Total Receivables: Rs.{total_receivables}, Total Overdue: Rs.{total_overdue}")

if __name__ == "__main__":
    test_due_customers_calculation()
