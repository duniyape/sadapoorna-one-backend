from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Literal, Union, Any
from datetime import datetime, timezone
from bson import ObjectId
from database import groups_collection, subgroups_collection, ledgers_collection
from routes.auth import get_current_user

# =========================================================
# HELPERS
# =========================================================
router = APIRouter()


def serialize_doc(doc):
    if doc is None:
        return None

    if isinstance(doc, ObjectId):
        return str(doc)

    if isinstance(doc, dict):
        return {
            key: serialize_doc(value)
            for key, value in doc.items()
        }

    if isinstance(doc, list):
        return [
            serialize_doc(item)
            for item in doc
        ]

    return doc


def object_id(value: str):
    try:
        return ObjectId(value)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid ID"
        )


# =========================================================
# SCHEMAS
# =========================================================

class GroupCreate(BaseModel):
    group_name: str = Field(..., min_length=1)
    group_type: Literal[
        "LIABILITIES",
        "EXPENSES",
        "ASSETS",
        "INCOME"
    ]
    description: Optional[str] = None
    status: Literal["ACTIVE", "INACTIVE"] = "ACTIVE"


class GroupUpdate(BaseModel):
    group_name: Optional[str] = None
    group_type: Optional[
        Literal[
            "LIABILITIES",
            "EXPENSES",
            "ASSETS",
            "INCOME"
        ]
    ] = None
    description: Optional[str] = None
    status: Optional[Literal["ACTIVE", "INACTIVE"]] = None


class SubgroupCreate(BaseModel):
    subgroup_name: str = Field(..., min_length=1)
    group_id: str
    description: Optional[str] = None
    status: Literal["ACTIVE", "INACTIVE"] = "ACTIVE"


class SubgroupUpdate(BaseModel):
    subgroup_name: Optional[str] = None
    group_id: Optional[str] = None
    description: Optional[str] = None
    status: Optional[Literal["ACTIVE", "INACTIVE"]] = None


class LedgerCreate(BaseModel):
    ledger_name: str = Field(..., min_length=1)
    group_id: str
    subgroup_id: Optional[str] = None
    opening_balance: float = 0
    opening_balance_type: Literal["DEBIT", "CREDIT"] = "DEBIT"
    description: Optional[str] = None
    status: Literal["ACTIVE", "INACTIVE"] = "ACTIVE"


class LedgerUpdate(BaseModel):
    ledger_name: Optional[str] = None
    group_id: Optional[str] = None
    subgroup_id: Optional[str] = None
    opening_balance: Optional[float] = None
    opening_balance_type: Optional[
        Literal["DEBIT", "CREDIT"]
    ] = None
    description: Optional[str] = None
    status: Optional[Literal["ACTIVE", "INACTIVE"]] = None


# =========================================================
# GROUP APIs
# =========================================================

@router.post("/groups")
def create_group(data: GroupCreate):

    # Duplicate check
    existing = groups_collection.find_one({
        "group_name": data.group_name.strip()
    })

    if existing:
        raise HTTPException(
            status_code=400,
            detail="Group already exists"
        )

    now = datetime.now(timezone.utc)

    group = {
        "group_name": data.group_name.strip(),
        "group_type": data.group_type,
        "description": data.description,
        "status": data.status,
        "created_at": now,
        "updated_at": now
    }

    result = groups_collection.insert_one(group)

    return {
        "success": True,
        "message": "Group created successfully",
        "group_id": str(result.inserted_id)
    }


@router.get("/groups")
def get_groups(
    group_type: Optional[str] = None,
    status: Optional[str] = None
):

    query = {}

    if group_type:
        query["group_type"] = group_type.upper()

    if status:
        query["status"] = status.upper()

    cursor = groups_collection.find(query).sort(
        "group_name", 1
    )

    groups = []

    for group in cursor:
        groups.append(serialize_doc(group))

    return {
        "success": True,
        "count": len(groups),
        "data": groups
    }


@router.get("/groups/{group_id}")
def get_group(group_id: str):

    group = groups_collection.find_one({
        "_id": object_id(group_id)
    })

    if not group:
        raise HTTPException(
            status_code=404,
            detail="Group not found"
        )

    return {
        "success": True,
        "data": serialize_doc(group)
    }


@router.put("/groups/{group_id}")
def update_group(
    group_id: str,
    data: GroupUpdate
):

    gid = object_id(group_id)

    group = groups_collection.find_one({
        "_id": gid
    })

    if not group:
        raise HTTPException(
            status_code=404,
            detail="Group not found"
        )

    update_data = {
        key: value
        for key, value in data.model_dump().items()
        if value is not None
    }

    if "group_name" in update_data:
        duplicate = groups_collection.find_one({
            "group_name": update_data["group_name"].strip(),
            "_id": {"$ne": gid}
        })

        if duplicate:
            raise HTTPException(
                status_code=400,
                detail="Group already exists"
            )

        update_data["group_name"] = update_data[
            "group_name"
        ].strip()

    update_data["updated_at"] = datetime.now(timezone.utc)

    groups_collection.update_one(
        {"_id": gid},
        {"$set": update_data}
    )

    return {
        "success": True,
        "message": "Group updated successfully"
    }


@router.delete("/groups/{group_id}")
def delete_group(group_id: str):

    gid = object_id(group_id)

    group = groups_collection.find_one({
        "_id": gid
    })

    if not group:
        raise HTTPException(
            status_code=404,
            detail="Group not found"
        )

    # Don't delete if subgroups exist
    subgroup_count = subgroups_collection.count_documents({
        "group_id": gid
    })

    if subgroup_count > 0:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete group. Subgroups exist under this group."
        )

    # Don't delete if ledgers exist
    ledger_count = ledgers_collection.count_documents({
        "group_id": gid
    })

    if ledger_count > 0:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete group. Ledgers exist under this group."
        )

    groups_collection.delete_one({
        "_id": gid
    })

    return {
        "success": True,
        "message": "Group deleted successfully"
    }


# =========================================================
# SUBGROUP APIs
# =========================================================

@router.post("/subgroups")
def create_subgroup(data: SubgroupCreate):

    gid = object_id(data.group_id)

    # Check parent group
    group = groups_collection.find_one({
        "_id": gid
    })

    if not group:
        raise HTTPException(
            status_code=404,
            detail="Parent group not found"
        )

    # Duplicate subgroup inside same group
    existing = subgroups_collection.find_one({
        "group_id": gid,
        "subgroup_name": data.subgroup_name.strip()
    })

    if existing:
        raise HTTPException(
            status_code=400,
            detail="Subgroup already exists in this group"
        )

    now = datetime.now(timezone.utc)

    subgroup = {
        "subgroup_name": data.subgroup_name.strip(),
        "group_id": gid,
        "group_name": group["group_name"],
        "group_type": group["group_type"],
        "description": data.description,
        "status": data.status,
        "created_at": now,
        "updated_at": now
    }

    result = subgroups_collection.insert_one(subgroup)

    return {
        "success": True,
        "message": "Subgroup created successfully",
        "subgroup_id": str(result.inserted_id)
    }


@router.get("/subgroups")
def get_subgroups(
    group_id: Optional[str] = None,
    status: Optional[str] = None
):

    query = {}

    if group_id:
        query["group_id"] = object_id(group_id)

    if status:
        query["status"] = status.upper()

    cursor = subgroups_collection.find(query).sort(
        "subgroup_name", 1
    )

    subgroups = []

    for subgroup in cursor:
        subgroups.append(
            serialize_doc(subgroup)
        )

    return {
        "success": True,
        "count": len(subgroups),
        "data": subgroups
    }


@router.get("/subgroups/{subgroup_id}")
def get_subgroup(subgroup_id: str):

    subgroup = subgroups_collection.find_one({
        "_id": object_id(subgroup_id)
    })

    if not subgroup:
        raise HTTPException(
            status_code=404,
            detail="Subgroup not found"
        )

    return {
        "success": True,
        "data": serialize_doc(subgroup)
    }


@router.put("/subgroups/{subgroup_id}")
def update_subgroup(
    subgroup_id: str,
    data: SubgroupUpdate
):

    sid = object_id(subgroup_id)

    subgroup = subgroups_collection.find_one({
        "_id": sid
    })

    if not subgroup:
        raise HTTPException(
            status_code=404,
            detail="Subgroup not found"
        )

    update_data = {
        key: value
        for key, value in data.model_dump().items()
        if value is not None
    }

    # If group is changed
    if "group_id" in update_data:

        new_gid = object_id(
            update_data["group_id"]
        )

        group = groups_collection.find_one({
            "_id": new_gid
        })

        if not group:
            raise HTTPException(
                status_code=404,
                detail="New parent group not found"
            )

        update_data["group_id"] = new_gid
        update_data["group_name"] = group["group_name"]
        update_data["group_type"] = group["group_type"]

    else:
        new_gid = subgroup["group_id"]

    if "subgroup_name" in update_data:

        name = update_data["subgroup_name"].strip()

        duplicate = subgroups_collection.find_one({
            "group_id": new_gid,
            "subgroup_name": name,
            "_id": {"$ne": sid}
        })

        if duplicate:
            raise HTTPException(
                status_code=400,
                detail="Subgroup already exists in this group"
            )

        update_data["subgroup_name"] = name

    update_data["updated_at"] = datetime.now(timezone.utc)

    subgroups_collection.update_one(
        {"_id": sid},
        {"$set": update_data}
    )

    return {
        "success": True,
        "message": "Subgroup updated successfully"
    }


@router.delete("/subgroups/{subgroup_id}")
def delete_subgroup(subgroup_id: str):

    sid = object_id(subgroup_id)

    subgroup = subgroups_collection.find_one({
        "_id": sid
    })

    if not subgroup:
        raise HTTPException(
            status_code=404,
            detail="Subgroup not found"
        )

    ledger_count = ledgers_collection.count_documents({
        "subgroup_id": sid
    })

    if ledger_count > 0:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete subgroup. Ledgers exist under this subgroup."
        )

    subgroups_collection.delete_one({
        "_id": sid
    })

    return {
        "success": True,
        "message": "Subgroup deleted successfully"
    }


# =========================================================
# LEDGER APIs
# =========================================================

@router.post("/ledgers")
def create_ledger(data: LedgerCreate):

    gid = object_id(data.group_id)

    # Check group
    group = groups_collection.find_one({
        "_id": gid
    })

    if not group:
        raise HTTPException(
            status_code=404,
            detail="Group not found"
        )

    subgroup = None

    if data.subgroup_id:

        sid = object_id(data.subgroup_id)

        subgroup = subgroups_collection.find_one({
            "_id": sid,
            "group_id": gid
        })

        if not subgroup:
            raise HTTPException(
                status_code=404,
                detail="Subgroup not found under selected group"
            )

    # Duplicate ledger
    duplicate_query = {
        "group_id": gid,
        "ledger_name": data.ledger_name.strip()
    }

    if data.subgroup_id:
        duplicate_query["subgroup_id"] = object_id(
            data.subgroup_id
        )

    existing = ledgers_collection.find_one(
        duplicate_query
    )

    if existing:
        raise HTTPException(
            status_code=400,
            detail="Ledger already exists"
        )

    now = datetime.now(timezone.utc)

    ledger = {
        "ledger_name": data.ledger_name.strip(),

        "group_id": gid,
        "group_name": group["group_name"],
        "group_type": group["group_type"],

        "subgroup_id": (
            object_id(data.subgroup_id)
            if data.subgroup_id
            else None
        ),

        "subgroup_name": (
            subgroup["subgroup_name"]
            if subgroup
            else None
        ),

        "opening_balance": data.opening_balance,
        "opening_balance_type": data.opening_balance_type,

        "description": data.description,
        "status": data.status,

        "created_at": now,
        "updated_at": now
    }

    result = ledgers_collection.insert_one(
        ledger
    )

    return {
        "success": True,
        "message": "Ledger created successfully",
        "ledger_id": str(result.inserted_id)
    }


@router.get("/ledgers")
def get_ledgers(
    group_id: Optional[str] = None,
    group_name: Optional[str] = None,
    subgroup_id: Optional[str] = None,
    status: Optional[str] = None
):

    query = {}

    if group_id:
        query["group_id"] = object_id(group_id)

    if group_name:
        query["group_name"] = {
            "$regex": f"^{group_name.strip()}$",
            "$options": "i"
        }

    if subgroup_id:
        query["subgroup_id"] = object_id(
            subgroup_id
        )

    if status:
        query["status"] = status.upper()

    cursor = ledgers_collection.find(query).sort(
        "ledger_name", 1
    )

    ledgers = []

    for ledger in cursor:
        ledgers.append(
            serialize_doc(ledger)
        )

    return {
        "success": True,
        "count": len(ledgers),
        "data": ledgers
    }


# =========================================================
# ROUTE: GET BANK ACCOUNTS LEDGERS
# GET /accounting/ledgers/bank-accounts
# GET /accounting/bank-accounts
# =========================================================


@router.get("/ledgers/bank-accounts")
def get_bank_accounts_ledgers_endpoint(
    status: Optional[str] = Query(None, description="Filter by status, e.g. ACTIVE"),
    search: Optional[str] = Query(None, description="Search bank ledger name"),
):
    # Find Bank Accounts group
    bank_grp = groups_collection.find_one({
        "group_name": {"$regex": "^bank account", "$options": "i"}
    })

    query = {}
    if bank_grp:
        query["$or"] = [
            {"group_id": bank_grp["_id"]},
            {"group_name": {"$regex": "^bank account", "$options": "i"}}
        ]
    else:
        query["group_name"] = {"$regex": "^bank account", "$options": "i"}

    if status:
        query["status"] = status.strip().upper()

    if search:
        query["ledger_name"] = {"$regex": search.strip(), "$options": "i"}

    cursor = ledgers_collection.find(query).sort("ledger_name", 1)
    ledgers = [serialize_doc(ledger) for ledger in cursor]

    return {
        "success": True,
        "count": len(ledgers),
        "data": ledgers
    }


@router.get("/ledgers/{ledger_id}")
def get_ledger(ledger_id: str):

    ledger = ledgers_collection.find_one({
        "_id": object_id(ledger_id)
    })

    if not ledger:
        raise HTTPException(
            status_code=404,
            detail="Ledger not found"
        )

    return {
        "success": True,
        "data": serialize_doc(ledger)
    }


@router.put("/ledgers/{ledger_id}")
def update_ledger(
    ledger_id: str,
    data: LedgerUpdate
):

    lid = object_id(ledger_id)

    ledger = ledgers_collection.find_one({
        "_id": lid
    })

    if not ledger:
        raise HTTPException(
            status_code=404,
            detail="Ledger not found"
        )

    update_data = {
        key: value
        for key, value in data.model_dump().items()
        if value is not None
    }

    # -----------------------------------------
    # Group change
    # -----------------------------------------

    if "group_id" in update_data:

        gid = object_id(
            update_data["group_id"]
        )

        group = groups_collection.find_one({
            "_id": gid
        })

        if not group:
            raise HTTPException(
                status_code=404,
                detail="Group not found"
            )

        update_data["group_id"] = gid
        update_data["group_name"] = group["group_name"]
        update_data["group_type"] = group["group_type"]

    else:
        gid = ledger["group_id"]

    # -----------------------------------------
    # Subgroup change
    # -----------------------------------------

    if "subgroup_id" in update_data:

        if update_data["subgroup_id"]:

            sid = object_id(
                update_data["subgroup_id"]
            )

            subgroup = subgroups_collection.find_one({
                "_id": sid,
                "group_id": gid
            })

            if not subgroup:
                raise HTTPException(
                    status_code=404,
                    detail="Subgroup not found under selected group"
                )

            update_data["subgroup_id"] = sid
            update_data["subgroup_name"] = (
                subgroup["subgroup_name"]
            )

        else:
            update_data["subgroup_id"] = None
            update_data["subgroup_name"] = None

    # -----------------------------------------
    # Ledger name
    # -----------------------------------------

    if "ledger_name" in update_data:

        name = update_data["ledger_name"].strip()

        duplicate_query = {
            "ledger_name": name,
            "group_id": gid,
            "_id": {"$ne": lid}
        }

        if update_data.get("subgroup_id"):
            duplicate_query["subgroup_id"] = (
                update_data["subgroup_id"]
            )

        duplicate = ledgers_collection.find_one(
            duplicate_query
        )

        if duplicate:
            raise HTTPException(
                status_code=400,
                detail="Ledger already exists"
            )

        update_data["ledger_name"] = name

    update_data["updated_at"] = datetime.now(timezone.utc)

    ledgers_collection.update_one(
        {"_id": lid},
        {"$set": update_data}
    )

    return {
        "success": True,
        "message": "Ledger updated successfully"
    }


@router.delete("/ledgers/{ledger_id}")
def delete_ledger(ledger_id: str):

    lid = object_id(ledger_id)

    ledger = ledgers_collection.find_one({
        "_id": lid
    })

    if not ledger:
        raise HTTPException(
            status_code=404,
            detail="Ledger not found"
        )

    ledgers_collection.delete_one({
        "_id": lid
    })

    return {
        "success": True,
        "message": "Ledger deleted successfully"
    }


# =========================================================
# ROUTE: DUE CUSTOMERS LIST WITH AGING
# GET /accounting/customers/due
# GET /accounting/customers/aging
# =========================================================

@router.get("/customers/due")
def get_due_customers_list(
    search: Optional[str] = Query(None, description="Search by name, custom ID, mobile, or company"),
    branch_id: Optional[str] = Query(None, description="Filter by branch ID"),
    assigned_employee_id: Optional[str] = Query(None, description="Filter by sales agent/employee ID"),
    aging_bucket: Optional[str] = Query(None, description="Filter by bucket: current, days_1_30, days_31_60, days_61_90, days_90_plus, overdue"),
    is_overdue: Optional[bool] = Query(None, description="True for overdue only, False for not overdue"),
    min_due: Optional[float] = Query(None, description="Minimum outstanding balance"),
    max_due: Optional[float] = Query(None, description="Maximum outstanding balance"),
    sort_by: str = Query("total_outstanding", description="Sort by: total_outstanding, total_overdue, max_dpd, customer_name, oldest_due_date"),
    sort_order: str = Query("desc", description="Sort order: asc or desc"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    include_bills: bool = Query(False, description="Include detailed unpaid bills list for each customer"),
):
    from services.accounting_service import get_due_customers_aging_list
    try:
        res = get_due_customers_aging_list(
            search=search,
            branch_id=branch_id,
            assigned_employee_id=assigned_employee_id,
            aging_bucket=aging_bucket,
            is_overdue=is_overdue,
            min_due=min_due,
            max_due=max_due,
            sort_by=sort_by,
            sort_order=sort_order,
            page=page,
            limit=limit,
            include_bills=include_bills,
        )
        return {
            "success": True,
            **serialize_doc(res)
        }
    except Exception as ex:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch due customers list: {ex}"
        )


# =========================================================
# ROUTE: CUSTOMER DPD & DEBTORS AGING
# GET /accounting/customers/{customer_id}/aging
# =========================================================

@router.get("/customers/{customer_id}/aging")
def get_customer_aging_analysis(
    customer_id: str
):
    from services.accounting_service import calculate_customer_aging
    try:
        data = calculate_customer_aging(customer_id)
        return {
            "success": True,
            "data": serialize_doc(data)
        }
    except ValueError as ve:
        if "not found" in str(ve).lower():
            raise HTTPException(status_code=404, detail=str(ve))
        raise HTTPException(status_code=400, detail=str(ve))
    except HTTPException:
        raise
    except Exception as ex:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to calculate customer aging: {ex}"
        )


# =========================================================
# ROUTE: CUSTOMER STATEMENT (PARTY LEDGER / KHATA)
# GET /accounting/customers/{customer_id}/statement
# =========================================================

@router.get("/customers/{customer_id}/statement")
def get_customer_ledger_statement(
    customer_id: str,
    from_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    to_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
):
    parsed_from = None
    parsed_to = None

    if from_date:
        try:
            parsed_from = datetime.strptime(from_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid from_date format. Use YYYY-MM-DD")

    if to_date:
        try:
            parsed_to = datetime.strptime(to_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid to_date format. Use YYYY-MM-DD")

    from services.accounting_service import get_customer_statement
    try:
        statement = get_customer_statement(
            customer_id=customer_id,
            from_date=parsed_from,
            to_date=parsed_to,
        )
        return {
            "success": True,
            "data": serialize_doc(statement)
        }
    except ValueError as ve:
        if "not found" in str(ve).lower():
            raise HTTPException(status_code=404, detail=str(ve))
        raise HTTPException(status_code=400, detail=str(ve))
    except HTTPException:
        raise
    except Exception as ex:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate customer statement: {ex}"
        )


# =========================================================
# ROUTE: SINGLE ORDER RECEIPT VOUCHER
# POST /accounting/orders/{order_id}/receipt
# =========================================================

VALID_PAYMENT_MODES = {
    "CASH", "COD",
    "UPI", "BANK", "BANK_TRANSFER", "ONLINE", "NEFT", "RTGS", "IMPS",
    "CHEQUE", "DD",
    "FINANCE", "LOAN", "NBFC"
}

def parse_optional_datetime(val):
    if not val or not str(val).strip():
        return None
    if isinstance(val, datetime):
        return val
    s = str(val).strip()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(s, fmt)
            except Exception:
                pass
    return None


class OrderReceiptRequest(BaseModel):
    payment_mode: str
    amount: float = Field(..., gt=0)
    bank_account_name: Optional[str] = None
    transaction_ref: Optional[str] = None
    cheque_no: Optional[str] = None
    cheque_date: Optional[Union[datetime, str]] = None
    cheque_bank: Optional[str] = None
    financier_name: Optional[str] = None
    receipt_date: Optional[Union[datetime, str]] = None
    bank_clearance_date: Optional[Union[datetime, str]] = None
    notes: Optional[str] = None
    collected_by_id: Optional[str] = None

    @field_validator("payment_mode", mode="before")
    @classmethod
    def normalize_payment_mode(cls, v):
        if not v:
            raise ValueError("payment_mode is required")
        v_clean = str(v).strip().upper()
        if v_clean not in VALID_PAYMENT_MODES:
            raise ValueError(f"Invalid payment mode '{v}'. Must be one of: Cash, UPI, Bank Transfer, Cheque, Finance")
        return v_clean

    @field_validator("receipt_date", "bank_clearance_date", mode="before")
    @classmethod
    def normalize_dates(cls, v):
        return parse_optional_datetime(v)

    @field_validator("cheque_date", mode="before")
    @classmethod
    def normalize_cheque_date(cls, v):
        if not v or not str(v).strip():
            return None
        return str(v).strip()

    @field_validator("bank_account_name", "transaction_ref", "cheque_no", "cheque_bank", "financier_name", "notes", "collected_by_id", mode="before")
    @classmethod
    def empty_string_to_none(cls, v):
        if v is not None and isinstance(v, str) and not v.strip():
            return None
        return v


@router.post("/orders/{order_id}/receipt")
def record_single_order_receipt(
    order_id: str,
    data: OrderReceiptRequest,
    current_user=Depends(get_current_user),
):
    """
    Records a double-entry Receipt Voucher against a specific billed order.
    Debits Cash / Bank / Cheques / Finance Clearing, Credits Customer Ledger,
    and updates the order payment status to PAID or PARTIALLY_PAID.
    """
    user_id = "admin"
    if isinstance(current_user, dict):
        user_id = str(current_user.get("user_id") or current_user.get("_id") or current_user.get("id") or "admin")
    elif current_user:
        user_id = str(current_user)

    from services.accounting_service import record_order_receipt_voucher
    try:
        voucher_doc, updated_order = record_order_receipt_voucher(
            order_id=order_id,
            user_id=user_id,
            amount=data.amount,
            payment_mode=data.payment_mode,
            bank_account_name=data.bank_account_name,
            transaction_ref=data.transaction_ref,
            cheque_no=data.cheque_no,
            cheque_date=data.cheque_date,
            cheque_bank=data.cheque_bank,
            financier_name=data.financier_name,
            receipt_date=data.receipt_date,
            bank_clearance_date=data.bank_clearance_date,
            notes=data.notes,
            collected_by_id=data.collected_by_id,
        )
        return {
            "success": True,
            "message": f"Receipt voucher {voucher_doc.get('voucher_number')} created successfully for order",
            "voucher": serialize_doc(voucher_doc),
            "order": serialize_doc(updated_order),
        }
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as ex:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to record order receipt: {ex}"
        )


# =========================================================
# ROUTE: CUSTOMER-LEVEL FIFO RECEIPT (LUMP-SUM SETTLEMENT)
# POST /accounting/customers/{customer_id}/receipt
# =========================================================

class CustomerReceiptRequest(BaseModel):
    payment_mode: str
    amount: float = Field(..., gt=0)
    bank_account_name: Optional[str] = None
    transaction_ref: Optional[str] = None
    cheque_no: Optional[str] = None
    cheque_date: Optional[Union[datetime, str]] = None
    cheque_bank: Optional[str] = None
    financier_name: Optional[str] = None
    receipt_date: Optional[Union[datetime, str]] = None
    bank_clearance_date: Optional[Union[datetime, str]] = None
    notes: Optional[str] = None
    collected_by_id: Optional[str] = None

    @field_validator("payment_mode", mode="before")
    @classmethod
    def normalize_payment_mode(cls, v):
        if not v:
            raise ValueError("payment_mode is required")
        v_clean = str(v).strip().upper()
        if v_clean not in VALID_PAYMENT_MODES:
            raise ValueError(f"Invalid payment mode '{v}'. Must be one of: Cash, UPI, Bank Transfer, Cheque, Finance")
        return v_clean

    @field_validator("receipt_date", "bank_clearance_date", mode="before")
    @classmethod
    def normalize_dates(cls, v):
        return parse_optional_datetime(v)

    @field_validator("cheque_date", mode="before")
    @classmethod
    def normalize_cheque_date(cls, v):
        if not v or not str(v).strip():
            return None
        return str(v).strip()

    @field_validator("bank_account_name", "transaction_ref", "cheque_no", "cheque_bank", "financier_name", "notes", "collected_by_id", mode="before")
    @classmethod
    def empty_string_to_none(cls, v):
        if v is not None and isinstance(v, str) and not v.strip():
            return None
        return v


@router.post("/customers/{customer_id}/receipt")
def settle_customer_fifo_receipt(
    customer_id: str,
    data: CustomerReceiptRequest,
    current_user=Depends(get_current_user),
):
    """
    Records ONE single Receipt Voucher for a customer payment (Cash, UPI, Cheque, etc.),
    and automatically knocks off their open bills using FIFO (oldest bill first).
    Any excess payment is held as Advance credit on the customer's ledger.
    """
    user_id = "admin"
    if isinstance(current_user, dict):
        user_id = str(current_user.get("user_id") or current_user.get("_id") or current_user.get("id") or "admin")
    elif current_user:
        user_id = str(current_user)

    from services.accounting_service import record_customer_fifo_receipt_voucher
    try:
        voucher_doc, summary = record_customer_fifo_receipt_voucher(
            customer_id=customer_id,
            payment_mode=data.payment_mode,
            amount=data.amount,
            user_id=user_id,
            bank_account_name=data.bank_account_name,
            transaction_ref=data.transaction_ref,
            cheque_no=data.cheque_no,
            cheque_date=data.cheque_date,
            cheque_bank=data.cheque_bank,
            financier_name=data.financier_name,
            receipt_date=data.receipt_date,
            bank_clearance_date=data.bank_clearance_date,
            notes=data.notes,
            collected_by_id=data.collected_by_id,
        )
        return {
            "success": True,
            "message": f"FIFO Receipt voucher {voucher_doc.get('voucher_number')} created successfully",
            "voucher": serialize_doc(voucher_doc),
            "summary": serialize_doc(summary),
        }
    except ValueError as ve:
        if "not found" in str(ve).lower():
            raise HTTPException(status_code=404, detail=str(ve))
        raise HTTPException(status_code=400, detail=str(ve))
    except HTTPException:
        raise
    except Exception as ex:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to record customer receipt: {ex}"
        )


# =========================================================
# ROUTE: EMPLOYEE CASH CUSTODY, OVERVIEW & HANDOVERS
# =========================================================

class CashHandoverRequest(BaseModel):
    employee_id: str
    amount: float = Field(..., gt=0)
    handover_to: Literal["SAFE", "BANK"] = "SAFE"
    bank_account_name: Optional[str] = None
    transaction_ref: Optional[str] = None
    handover_date: Optional[datetime] = None
    notes: Optional[str] = None


@router.get("/employees/cash-balances")
def get_employees_cash_overview(
    current_user=Depends(get_current_user),
):
    """
    Returns live cash-in-hand custody breakdown for all associates/employees
    (delivery staff, field collectors, sales reps, counter cashiers).
    """
    from services.accounting_service import get_all_employees_cash_balances
    try:
        data = get_all_employees_cash_balances()
        return {
            "success": True,
            "count": len(data),
            "data": serialize_doc(data),
        }
    except Exception as ex:
        raise HTTPException(status_code=500, detail=f"Failed to fetch employee cash balances: {ex}")


@router.get("/employees/{employee_id}/cash-summary")
def get_single_employee_cash_summary(
    employee_id: str,
    current_user=Depends(get_current_user),
):
    """
    Returns single employee's cash custody status, current cash in hand,
    and recent collection & handover ledger transactions.
    """
    from services.accounting_service import get_employee_cash_balance
    try:
        data = get_employee_cash_balance(employee_id)
        return {
            "success": True,
            "data": serialize_doc(data),
        }
    except Exception as ex:
        raise HTTPException(status_code=500, detail=f"Failed to fetch employee cash summary: {ex}")


@router.post("/employees/cash-handover")
def record_employee_handover(
    data: CashHandoverRequest,
    current_user=Depends(get_current_user),
):
    """
    Records an associate's physical cash handover to the company Safe or direct Bank CDM deposit.
    Generates an automated Contra Voucher (CTV):
      Dr Cash-in-Hand (Main Safe) or Bank Account
      Cr Employee Cash Custody Account
    Strictly validates that amount does not exceed the employee's current cash in hand.
    """
    user_id = "admin"
    if isinstance(current_user, dict):
        user_id = str(current_user.get("user_id") or current_user.get("_id") or current_user.get("id") or "admin")
    elif current_user:
        user_id = str(current_user)

    from services.accounting_service import record_employee_cash_handover
    try:
        voucher_doc, summary = record_employee_cash_handover(
            employee_id=data.employee_id,
            amount=data.amount,
            handover_to=data.handover_to,
            handled_by_user_id=user_id,
            bank_account_name=data.bank_account_name,
            transaction_ref=data.transaction_ref,
            handover_date=data.handover_date,
            notes=data.notes,
        )
        return {
            "success": True,
            "message": f"Cash handover Contra voucher {voucher_doc.get('voucher_number')} recorded successfully",
            "voucher": serialize_doc(voucher_doc),
            "summary": serialize_doc(summary),
        }
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as ex:
        raise HTTPException(status_code=500, detail=f"Failed to record cash handover: {ex}")