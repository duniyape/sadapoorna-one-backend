import os

route_code = '''
class CustomerBulkTransferRequest(BaseModel):
    customer_ids: List[str]
    new_employee_id: str

@router.post("/bulk-transfer")
def bulk_transfer_customers(
    request: CustomerBulkTransferRequest,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    if not ObjectId.is_valid(request.new_employee_id):
        raise HTTPException(status_code=400, detail="Invalid employee ID format")
        
    employee = users_collection.find_one({"_id": ObjectId(request.new_employee_id)})
    if not employee:
        raise HTTPException(status_code=404, detail="Assigned employee not found")
        
    valid_customer_ids = []
    for cid in request.customer_ids:
        if ObjectId.is_valid(cid):
            valid_customer_ids.append(ObjectId(cid))
            
    if not valid_customer_ids:
        raise HTTPException(status_code=400, detail="No valid customer IDs provided")
        
    result = customers_collection.update_many(
        {"_id": {"$in": valid_customer_ids}},
        {"$set": {
            "assigned_employee_id": str(employee["_id"]),
            "updated_at": datetime.now(timezone.utc)
        }}
    )
    
    return convert_utc_to_ist({
        "status": True,
        "message": f"Successfully transferred {result.modified_count} customers to {employee.get('name', 'selected employee')}.",
        "matched_count": result.matched_count,
        "modified_count": result.modified_count
    })
'''

with open(r'c:\Users\sadapoorna\OneDrive\Desktop\workspace\sadapoorna\sadapoorna-one-backend\routes\customer.py', 'a', encoding='utf-8') as f:
    f.write(route_code)
