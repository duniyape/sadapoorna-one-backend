from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import datetime, timezone
from bson import ObjectId

from database import (
    vouchers_collection,
    ledgers_collection
)

from routes.auth import get_current_user


# =========================================================
# ROUTER
# =========================================================

router = APIRouter()


# =========================================================
# PYDANTIC SCHEMAS
# =========================================================

class VoucherEntry(BaseModel):
    """
    Each voucher entry belongs to one ledger.
    ledger_id is MongoDB ObjectId of ledger.
    ledger_name will be fetched automatically from database.
    """

    ledger_id: str

    narration: Optional[str] = None

    debit: float = Field(
        default=0,
        ge=0
    )

    credit: float = Field(
        default=0,
        ge=0
    )

    user_id: Optional[str] = None


class VoucherCreate(BaseModel):

    voucher_type: Literal[
        "Payment",
        "Receipt",
        "Journal",
        "Contra"
    ]

    voucher_mode: Optional[str] = None

    date: datetime

    narration: Optional[str] = None

    amount: Optional[float] = Field(
        default=None,
        ge=0
    )

    entries: List[VoucherEntry]


# =========================================================
# HELPER
# =========================================================

def serialize_doc(doc):

    if not doc:
        return None

    doc["_id"] = str(doc["_id"])

    return doc


def get_object_id(value: str):

    try:
        return ObjectId(value)

    except Exception:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid MongoDB ID: {value}"
        )


# =========================================================
# GET USER ID FROM TOKEN
# =========================================================

def get_token_user_id(current_user):

    """
    Supports common get_current_user return formats:

    {
        "_id": ObjectId(...)
    }

    or

    {
        "id": "..."
    }

    or

    {
        "user_id": "..."
    }

    or

    JWT payload:
    {
        "sub": "..."
    }
    """

    if current_user is None:
        raise HTTPException(
            status_code=401,
            detail="User authentication required"
        )

    # If get_current_user returns dict
    if isinstance(current_user, dict):

        user_id = (
            current_user.get("user_id")
            or current_user.get("_id")
            or current_user.get("id")
            or current_user.get("sub")
        )

    # If get_current_user returns string
    elif isinstance(current_user, str):

        user_id = current_user

    # If get_current_user returns object
    else:

        user_id = (
            getattr(current_user, "user_id", None)
            or getattr(current_user, "_id", None)
            or getattr(current_user, "id", None)
            or getattr(current_user, "sub", None)
        )

    if user_id is None:

        raise HTTPException(
            status_code=401,
            detail="User ID not found in authentication token"
        )

    return str(user_id)


# =========================================================
# GENERATE VOUCHER PREFIX
# =========================================================

def get_voucher_prefix(
    voucher_type: str,
    voucher_mode: Optional[str]
):

    voucher_type = voucher_type.strip().title()

    if voucher_mode:
        voucher_mode = voucher_mode.strip().title()

    # -----------------------------------------------------
    # RECEIPT
    # -----------------------------------------------------

    if voucher_type == "Receipt":

        if voucher_mode == "Cash":
            return "CRV"

        if voucher_mode == "Bank":
            return "BRV"

        return "RV"

    # -----------------------------------------------------
    # PAYMENT
    # -----------------------------------------------------

    if voucher_type == "Payment":

        if voucher_mode == "Cash":
            return "CPV"

        if voucher_mode == "Bank":
            return "BPV"

        return "PV"

    # -----------------------------------------------------
    # JOURNAL
    # -----------------------------------------------------

    if voucher_type == "Journal":
        return "JRV"

    # -----------------------------------------------------
    # CONTRA
    # -----------------------------------------------------

    if voucher_type == "Contra":
        return "CTV"

    return voucher_type.upper()


# =========================================================
# GENERATE VOUCHER NUMBER
# =========================================================

def generate_voucher_number(
    voucher_type: str,
    voucher_mode: Optional[str],
    voucher_date: datetime
):

    date_part = voucher_date.strftime(
        "%Y-%m-%d"
    )

    prefix = get_voucher_prefix(
        voucher_type,
        voucher_mode
    )

    # -----------------------------------------------------
    # Find last voucher
    #
    # Numbering is separate for:
    # voucher type + voucher mode + date
    # -----------------------------------------------------

    query = {
        "voucher_type": voucher_type,
        "date_key": date_part
    }

    # For Receipt/Payment, mode matters
    if voucher_type in [
        "Receipt",
        "Payment"
    ]:
        query["voucher_mode"] = voucher_mode

    last_voucher = vouchers_collection.find_one(
        query,
        sort=[
            ("txn", -1)
        ]
    )

    if last_voucher:

        try:
            txn = int(
                last_voucher.get(
                    "txn",
                    0
                )
            ) + 1

        except Exception:
            txn = 1

    else:

        txn = 1

    voucher_number = (
        f"{prefix}-{date_part}-{txn}"
    )

    return voucher_number, txn


# =========================================================
# CREATE VOUCHER
# =========================================================

@router.post("/")
def create_voucher(
    data: VoucherCreate,
    current_user=Depends(get_current_user)
):

    # =====================================================
    # USER FROM TOKEN
    # =====================================================

    created_by = get_token_user_id(
        current_user
    )

    # =====================================================
    # VALIDATE ENTRIES
    # =====================================================

    if not data.entries:

        raise HTTPException(
            status_code=400,
            detail="At least one voucher entry is required"
        )

    # =====================================================
    # CALCULATE DEBIT / CREDIT
    # =====================================================

    total_debit = 0.0
    total_credit = 0.0

    clean_entries = []

    for entry in data.entries:

        debit = float(
            entry.debit or 0
        )

        credit = float(
            entry.credit or 0
        )

        # -------------------------------------------------
        # Negative check
        # -------------------------------------------------

        if debit < 0:

            raise HTTPException(
                status_code=400,
                detail=(
                    f"Debit cannot be negative "
                    f"for ledger {entry.ledger_id}"
                )
            )

        if credit < 0:

            raise HTTPException(
                status_code=400,
                detail=(
                    f"Credit cannot be negative "
                    f"for ledger {entry.ledger_id}"
                )
            )

        # -------------------------------------------------
        # Both debit and credit
        # -------------------------------------------------

        if debit > 0 and credit > 0:

            raise HTTPException(
                status_code=400,
                detail=(
                    f"Ledger {entry.ledger_id} "
                    "cannot have both debit and credit"
                )
            )

        # -------------------------------------------------
        # Both zero
        # -------------------------------------------------

        if debit == 0 and credit == 0:

            raise HTTPException(
                status_code=400,
                detail=(
                    f"Ledger {entry.ledger_id} "
                    "must have debit or credit amount"
                )
            )

        # =================================================
        # GET LEDGER USING MONGODB _id
        # =================================================

        ledger_oid = get_object_id(
            entry.ledger_id
        )

        ledger = ledgers_collection.find_one({
            "_id": ledger_oid
        })

        if not ledger:

            raise HTTPException(
                status_code=404,
                detail=(
                    f"Ledger not found: "
                    f"{entry.ledger_id}"
                )
            )

        # =================================================
        # GET LEDGER NAME AUTOMATICALLY
        # =================================================

        ledger_name = ledger.get(
            "ledger_name"
        )

        if not ledger_name:

            raise HTTPException(
                status_code=400,
                detail=(
                    f"Ledger name not found for "
                    f"{entry.ledger_id}"
                )
            )

        # =================================================
        # TOTALS
        # =================================================

        total_debit += debit
        total_credit += credit

        # =================================================
        # SAVE CLEAN ENTRY
        # =================================================

        clean_entries.append({

            "ledger_id": entry.ledger_id,

            "ledger_name": ledger_name,

            "narration": entry.narration,

            "debit": debit,

            "credit": credit,

            "user_id": entry.user_id
        })

    # =====================================================
    # ROUND TOTALS
    # =====================================================

    total_debit = round(
        total_debit,
        2
    )

    total_credit = round(
        total_credit,
        2
    )

    # =====================================================
    # DEBIT MUST EQUAL CREDIT
    # =====================================================

    if total_debit != total_credit:

        raise HTTPException(
            status_code=400,
            detail={
                "message": (
                    "Debit and credit "
                    "must be equal"
                ),
                "total_debit": total_debit,
                "total_credit": total_credit,
                "difference": round(
                    total_debit - total_credit,
                    2
                )
            }
        )

    # =====================================================
    # VOUCHER AMOUNT
    # =====================================================

    if data.amount is not None:

        voucher_amount = round(
            float(data.amount),
            2
        )

        if voucher_amount != total_debit:

            raise HTTPException(
                status_code=400,
                detail={
                    "message": (
                        "Voucher amount does not "
                        "match debit/credit total"
                    ),
                    "voucher_amount": voucher_amount,
                    "entries_amount": total_debit
                }
            )

    else:

        voucher_amount = total_debit

    # =====================================================
    # VOUCHER DATE
    # =====================================================

    voucher_date = data.date

    date_key = voucher_date.strftime(
        "%Y-%m-%d"
    )

    # =====================================================
    # GENERATE NUMBER
    # =====================================================

    voucher_number, txn = (
        generate_voucher_number(
            voucher_type=data.voucher_type,
            voucher_mode=data.voucher_mode,
            voucher_date=voucher_date
        )
    )

    # =====================================================
    # CREATED AT
    # =====================================================

    created_at = datetime.now(
        timezone.utc
    )

    # =====================================================
    # VOUCHER DOCUMENT
    # =====================================================

    voucher = {

        "voucher_number": voucher_number,

        "voucher_type": data.voucher_type,

        "voucher_mode": data.voucher_mode,

        "txn": txn,

        "date": voucher_date,

        "date_key": date_key,

        "narration": data.narration,

        "amount": voucher_amount,

        "entries": clean_entries,

        # From authentication token
        "created_by": created_by,

        # Server generated
        "created_at": created_at
    }

    # =====================================================
    # INSERT
    # =====================================================

    result = vouchers_collection.insert_one(
        voucher
    )

    # =====================================================
    # RESPONSE
    # =====================================================

    return {

        "success": True,

        "message": (
            "Voucher created successfully"
        ),

        "voucher_id": str(
            result.inserted_id
        ),

        "voucher_number": voucher_number,

        "voucher_type": data.voucher_type,

        "voucher_mode": data.voucher_mode,

        "txn": txn,

        "amount": voucher_amount,

        "total_debit": total_debit,

        "total_credit": total_credit,

        "created_by": created_by,

        "created_at": created_at
    }


# =========================================================
# GET ALL VOUCHERS
# =========================================================

@router.get("/")
def get_vouchers(
    voucher_type: Optional[str] = None,
    voucher_mode: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None
):

    query = {}

    # -----------------------------------------------------
    # Voucher Type
    # -----------------------------------------------------

    if voucher_type:

        query["voucher_type"] = (
            voucher_type.strip().title()
        )

    # -----------------------------------------------------
    # Voucher Mode
    # -----------------------------------------------------

    if voucher_mode:

        query["voucher_mode"] = (
            voucher_mode.strip().title()
        )

    # -----------------------------------------------------
    # Date Filter
    # -----------------------------------------------------

    if from_date or to_date:

        date_query = {}

        if from_date:

            date_query["$gte"] = from_date

        if to_date:

            date_query["$lte"] = to_date

        query["date_key"] = date_query

    # -----------------------------------------------------
    # Find
    # -----------------------------------------------------

    cursor = vouchers_collection.find(
        query
    ).sort(
        [
            ("date", -1),
            ("txn", -1)
        ]
    )

    vouchers = []

    for voucher in cursor:

        vouchers.append(
            serialize_doc(voucher)
        )

    return {

        "success": True,

        "count": len(vouchers),

        "data": vouchers
    }


# =========================================================
# GET SINGLE VOUCHER
# =========================================================

@router.get("/{voucher_id}")
def get_voucher(
    voucher_id: str
):

    voucher_oid = get_object_id(
        voucher_id
    )

    voucher = vouchers_collection.find_one({
        "_id": voucher_oid
    })

    if not voucher:

        raise HTTPException(
            status_code=404,
            detail="Voucher not found"
        )

    return {

        "success": True,

        "data": serialize_doc(
            voucher
        )
    }


# =========================================================
# DELETE VOUCHER
# =========================================================

@router.delete("/{voucher_id}")
def delete_voucher(
    voucher_id: str,
    current_user=Depends(get_current_user)
):

    # Make sure user is authenticated
    created_by = get_token_user_id(
        current_user
    )

    voucher_oid = get_object_id(
        voucher_id
    )

    voucher = vouchers_collection.find_one({
        "_id": voucher_oid
    })

    if not voucher:

        raise HTTPException(
            status_code=404,
            detail="Voucher not found"
        )

    # -----------------------------------------------------
    # Delete
    # -----------------------------------------------------

    result = vouchers_collection.delete_one({
        "_id": voucher_oid
    })

    if result.deleted_count == 0:

        raise HTTPException(
            status_code=400,
            detail="Voucher could not be deleted"
        )

    return {

        "success": True,

        "message": (
            "Voucher deleted successfully"
        ),

        "voucher_id": voucher_id,

        "deleted_by": created_by
    }