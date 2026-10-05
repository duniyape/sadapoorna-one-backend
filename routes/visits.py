from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timezone, date, timedelta
from bson import ObjectId
from utils import convert_utc_to_ist

from database import (
    beats_collection,
    customers_collection,
    visit_logs_collection,
    users_collection,
    orders_collection
)
from routes.auth import get_current_user


router = APIRouter(
    prefix="/visits",
    tags=["Visit Logs"]
)


# =========================================================
# REQUEST MODELS
# =========================================================

class VisitRecord(BaseModel):
    customer_id: str = Field(..., min_length=1)
    outcome: str = Field(..., min_length=1) # e.g., "Order Taken", "Follow-up", "Not Interested"
    remark: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None


# =========================================================
# 1. RECORD A VISIT (MARK DONE)
# =========================================================

@router.post("/record")
def record_visit(
    data: VisitRecord,
    current_user=Depends(get_current_user)
):
    if not ObjectId.is_valid(data.customer_id):
        raise HTTPException(status_code=400, detail="Invalid customer_id")

    user_id = str(current_user.get("_id") or current_user.get("user_id") or current_user.get("id"))
    now = datetime.now(timezone.utc)

    visit_log = {
        "customer_id": ObjectId(data.customer_id),
        "employee_id": ObjectId(user_id),
        "outcome": data.outcome,
        "remark": data.remark,
        "location": {
            "lat": data.lat,
            "lng": data.lng
        } if data.lat and data.lng else None,
        "date": now.strftime("%Y-%m-%d"),
        "created_at": now
    }

    visit_logs_collection.insert_one(visit_log)

    return {
        "success": True,
        "message": "Visit marked as done successfully!"
    }


# =========================================================
# 2. GET TODAY'S BEAT / TASKS (EMPLOYEE VIEW)
# =========================================================

@router.get("/my-tasks-today")
def get_my_tasks_today(
    current_user=Depends(get_current_user)
):
    user_id = str(current_user.get("_id") or current_user.get("user_id") or current_user.get("id"))
    
    # 1. Find today's day name (e.g., "Monday")
    today_name = datetime.now(timezone.utc).strftime("%A")
    today_date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 2. Find beats assigned to this employee for today
    assigned_beats = list(beats_collection.find({
        "user_id": user_id,
        "day": today_name,
        "status": "ACTIVE"
    }))

    if not assigned_beats:
        return {
            "success": True,
            "message": f"No beats assigned to you for {today_name}.",
            "data": []
        }

    beat_ids = [b["_id"] for b in assigned_beats]

    # 3. Find customers in those beats
    customers = list(customers_collection.find({
        "beat_id": {"$in": beat_ids}
    }))

    # 4. Check if they were visited today
    visited_customer_ids = set()
    today_logs = list(visit_logs_collection.find({
        "employee_id": ObjectId(user_id),
        "date": today_date_str
    }))
    
    for log in today_logs:
        visited_customer_ids.add(str(log["customer_id"]))

    # Fetch unpaid orders for these customers to calculate DPD and Due
    customer_ids = [str(c["_id"]) for c in customers]
    customer_ids.extend([c["_id"] for c in customers]) # cover both str and objectid just in case
    
    unpaid_orders = list(orders_collection.find({
        "customer_id": {"$in": customer_ids},
        "payment_status": {"$ne": "PAID"}
    }))

    due_map = {}
    now = datetime.now(timezone.utc)
    for ord_doc in unpaid_orders:
        cid = str(ord_doc.get("customer_id"))
        if cid not in due_map:
            due_map[cid] = {"total_due": 0.0, "max_dpd": 0}
            
        bill_amount = round(float(ord_doc.get("bill_amount", ord_doc.get("grand_total", 0.0))), 2)
        paid_amount = round(float(ord_doc.get("paid_amount", 0.0)), 2)
        pending = round(float(ord_doc.get("pending_amount", bill_amount - paid_amount)), 2)
        
        if pending <= 0.01:
            continue
            
        due_map[cid]["total_due"] += pending
        
        billed_at = ord_doc.get("billed_at") or ord_doc.get("created_at") or now
        if billed_at.tzinfo is None:
            billed_at = billed_at.replace(tzinfo=timezone.utc)
            
        due_date = ord_doc.get("due_date")
        if not due_date:
            credit_days = int(ord_doc.get("credit_days") or 7)
            due_date = billed_at + timedelta(days=credit_days)
            
        if due_date.tzinfo is None:
            due_date = due_date.replace(tzinfo=timezone.utc)
            
        diff_days = (now.date() - due_date.date()).days
        dpd = max(0, diff_days)
        
        if dpd > due_map[cid]["max_dpd"]:
            due_map[cid]["max_dpd"] = dpd

    # 5. Format the response
    tasks = []
    for c in customers:
        cid = str(c["_id"])
        tasks.append({
            "customer_id": cid,
            "customer_name": c.get("name"),
            "company_name": c.get("company_name") or c.get("shop_name"),
            "phone": c.get("mobile") or c.get("phone"),
            "address": c.get("billing_address"),
            "location": c.get("location"),
            "beat_id": str(c.get("beat_id")) if c.get("beat_id") else None,
            "visited_today": cid in visited_customer_ids,
            "total_due": round(due_map.get(cid, {}).get("total_due", 0.0), 2),
            "max_dpd": due_map.get(cid, {}).get("max_dpd", 0)
        })

    # Sort so unvisited are on top
    tasks.sort(key=lambda x: x["visited_today"])

    return {
        "success": True,
        "day": today_name,
        "total_customers": len(tasks),
        "visited_count": len(visited_customer_ids),
        "data": tasks
    }


# =========================================================
# 3. GET VISIT LOGS (ADMIN / MANAGER VIEW)
# =========================================================

@router.get("/logs")
def get_visit_logs(
    employee_id: Optional[str] = None,
    customer_id: Optional[str] = None,
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    current_user=Depends(get_current_user)
):
    query = {}
    
    if employee_id and ObjectId.is_valid(employee_id):
        query["employee_id"] = ObjectId(employee_id)
        
    if customer_id and ObjectId.is_valid(customer_id):
        query["customer_id"] = ObjectId(customer_id)
        
    if start_date or end_date:
        query["date"] = {}
        if start_date:
            query["date"]["$gte"] = start_date
        if end_date:
            query["date"]["$lte"] = end_date
            
    logs = list(visit_logs_collection.find(query).sort("created_at", -1))
    
    results = []
    for log in logs:
        # Resolve Employee Name
        emp = users_collection.find_one({"_id": log["employee_id"]})
        emp_name = emp.get("name") or emp.get("full_name") if emp else "Unknown"
        
        # Resolve Customer Name
        cust = customers_collection.find_one({"_id": log["customer_id"]})
        cust_name = cust.get("company_name") or cust.get("name") if cust else "Unknown"
        
        results.append(convert_utc_to_ist({
            "id": str(log["_id"]),
            "customer_id": str(log["customer_id"]),
            "customer_name": cust_name,
            "employee_id": str(log["employee_id"]),
            "employee_name": emp_name,
            "outcome": log.get("outcome"),
            "remark": log.get("remark"),
            "location": log.get("location"),
            "date": log.get("date"),
            "created_at": log["created_at"].isoformat()
        }))
        
    return {
        "success": True,
        "count": len(results),
        "data": results
    }


# =========================================================
# 4. ADMIN: DASHBOARD (ALL STAFF)
# =========================================================

@router.get("/admin/dashboard")
def get_admin_dashboard(
    target_date: Optional[str] = Query(None, description="YYYY-MM-DD (defaults to today)"),
    current_user=Depends(get_current_user)
):
    # Determine the date to query
    if not target_date:
        target_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        target_dt = datetime.now(timezone.utc)
    else:
        target_dt = datetime.strptime(target_date, "%Y-%m-%d")
        
    day_name = target_dt.strftime("%A")

    # 1. Find all beats for that day
    day_beats = list(beats_collection.find({
        "day": day_name,
        "status": "ACTIVE"
    }))

    # Map beats by employee
    employee_map = {}
    for b in day_beats:
        emp_id = b.get("user_id")
        if not emp_id:
            continue
        if emp_id not in employee_map:
            employee_map[emp_id] = {"beat_ids": [], "total_customers": 0, "visited_customers": 0}
        employee_map[emp_id]["beat_ids"].append(str(b["_id"]))

    # 2. Get customer counts & visit counts per employee
    dashboard_stats = []
    
    for emp_id, data in employee_map.items():
        # Resolve Employee Name
        emp_doc = users_collection.find_one({"_id": ObjectId(emp_id)})
        emp_name = emp_doc.get("name") or emp_doc.get("full_name") if emp_doc else "Unknown"

        # Count total customers in these beats
        total_customers = customers_collection.count_documents({
            "beat_id": {"$in": data["beat_ids"]}
        })

        # Count how many unique customers were visited by this employee on this date
        visited_logs = list(visit_logs_collection.find({
            "employee_id": ObjectId(emp_id),
            "date": target_date
        }))
        
        # Use set to count unique customers in case of multiple check-ins
        unique_visited_customers = len(set([str(log["customer_id"]) for log in visited_logs]))

        percentage = round((unique_visited_customers / total_customers * 100), 2) if total_customers > 0 else 0

        dashboard_stats.append({
            "employee_id": emp_id,
            "employee_name": emp_name,
            "total_customers": total_customers,
            "visited_customers": unique_visited_customers,
            "pending_customers": max(0, total_customers - unique_visited_customers),
            "completion_percentage": percentage
        })

    # Sort by lowest completion first (to highlight those lagging behind)
    dashboard_stats.sort(key=lambda x: x["completion_percentage"])

    return {
        "success": True,
        "date": target_date,
        "day": day_name,
        "data": dashboard_stats
    }


# =========================================================
# 5. ADMIN: EMPLOYEE'S SPECIFIC DAY
# =========================================================

@router.get("/admin/employee-day")
def get_employee_day_view(
    employee_id: str,
    target_date: Optional[str] = Query(None, description="YYYY-MM-DD (defaults to today)"),
    current_user=Depends(get_current_user)
):
    if not ObjectId.is_valid(employee_id):
        raise HTTPException(status_code=400, detail="Invalid employee_id")

    if not target_date:
        target_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        target_dt = datetime.now(timezone.utc)
    else:
        target_dt = datetime.strptime(target_date, "%Y-%m-%d")
        
    day_name = target_dt.strftime("%A")

    assigned_beats = list(beats_collection.find({
        "user_id": employee_id,
        "day": day_name,
        "status": "ACTIVE"
    }))

    beat_ids = [b["_id"] for b in assigned_beats]

    customers = list(customers_collection.find({
        "beat_id": {"$in": beat_ids}
    }))

    visited_customer_ids = set()
    today_logs = list(visit_logs_collection.find({
        "employee_id": ObjectId(employee_id),
        "date": target_date
    }))
    
    # Map logs to customer for outcome details
    log_map = {}
    for log in today_logs:
        cid = str(log["customer_id"])
        visited_customer_ids.add(cid)
        log_map[cid] = log

    # Fetch unpaid orders for these customers to calculate DPD and Due
    customer_ids = [str(c["_id"]) for c in customers]
    customer_ids.extend([c["_id"] for c in customers]) # cover both str and objectid just in case
    
    unpaid_orders = list(orders_collection.find({
        "customer_id": {"$in": customer_ids},
        "payment_status": {"$ne": "PAID"}
    }))

    due_map = {}
    now = datetime.now(timezone.utc)
    for ord_doc in unpaid_orders:
        cid = str(ord_doc.get("customer_id"))
        if cid not in due_map:
            due_map[cid] = {"total_due": 0.0, "max_dpd": 0}
            
        bill_amount = round(float(ord_doc.get("bill_amount", ord_doc.get("grand_total", 0.0))), 2)
        paid_amount = round(float(ord_doc.get("paid_amount", 0.0)), 2)
        pending = round(float(ord_doc.get("pending_amount", bill_amount - paid_amount)), 2)
        
        if pending <= 0.01:
            continue
            
        due_map[cid]["total_due"] += pending
        
        billed_at = ord_doc.get("billed_at") or ord_doc.get("created_at") or now
        if billed_at.tzinfo is None:
            billed_at = billed_at.replace(tzinfo=timezone.utc)
            
        due_date = ord_doc.get("due_date")
        if not due_date:
            credit_days = int(ord_doc.get("credit_days") or 7)
            due_date = billed_at + timedelta(days=credit_days)
            
        if due_date.tzinfo is None:
            due_date = due_date.replace(tzinfo=timezone.utc)
            
        diff_days = (now.date() - due_date.date()).days
        dpd = max(0, diff_days)
        
        if dpd > due_map[cid]["max_dpd"]:
            due_map[cid]["max_dpd"] = dpd

    tasks = []
    for c in customers:
        cid = str(c["_id"])
        is_visited = cid in visited_customer_ids
        log_detail = log_map.get(cid)
        
        tasks.append({
            "customer_id": cid,
            "customer_name": c.get("name"),
            "company_name": c.get("company_name") or c.get("shop_name"),
            "phone": c.get("mobile") or c.get("phone"),
            "address": c.get("billing_address"),
            "location": c.get("location"),
            "beat_id": str(c.get("beat_id")) if c.get("beat_id") else None,
            "visited": is_visited,
            "total_due": round(due_map.get(cid, {}).get("total_due", 0.0), 2),
            "max_dpd": due_map.get(cid, {}).get("max_dpd", 0),
            "visit_details": {
                "outcome": log_detail.get("outcome") if log_detail else None,
                "remark": log_detail.get("remark") if log_detail else None,
                "time": log_detail["created_at"].isoformat() if log_detail else None
            } if is_visited else None
        })

    tasks.sort(key=lambda x: x["visited"])

    return {
        "success": True,
        "date": target_date,
        "day": day_name,
        "employee_id": employee_id,
        "total_customers": len(tasks),
        "visited_count": len(visited_customer_ids),
        "data": tasks
    }
