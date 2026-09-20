import random
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from bson import ObjectId

from database import (
    customers_collection,
    otp_collection
)
from services.whatsapp_service import send_otp_template_whatsapp


router = APIRouter()


# =========================================================
# REQUEST MODELS
# =========================================================

class VerifyOTPRequest(BaseModel):
    otp: str


# =========================================================
# HELPER FUNCTIONS
# =========================================================

def generate_otp() -> str:
    """Generate a random 6-digit OTP."""
    return str(random.randint(100000, 999999))


def hash_otp(otp: str) -> str:
    """Hash the OTP using SHA-256."""
    return hashlib.sha256(otp.encode("utf-8")).hexdigest()


def normalize_indian_phone(phone: str) -> str:
    """
    Normalizes Indian mobile number to E.164 with 91 prefix (e.g., '919876543210').
    Validates that the 10-digit number starts with 6, 7, 8, or 9.
    """
    cleaned = str(phone or "").strip().replace(" ", "").replace("-", "")

    if cleaned.startswith("+91"):
        cleaned = cleaned[3:]
    elif cleaned.startswith("91") and len(cleaned) == 12:
        cleaned = cleaned[2:]
    elif cleaned.startswith("0") and len(cleaned) == 11:
        cleaned = cleaned[1:]

    if len(cleaned) != 10 or not cleaned.isdigit() or not cleaned.startswith(("6", "7", "8", "9")):
        raise HTTPException(
            status_code=400,
            detail="Invalid Indian mobile number. Must be a 10-digit number starting with 6, 7, 8, or 9."
        )

    return "91" + cleaned


def send_whatsapp_otp(phone: str, otp: str) -> Dict[str, Any]:
    """
    Sends WhatsApp OTP using the centralized WhatsApp service and approved 'custmer_otp' template.
    """
    return send_otp_template_whatsapp(
        recipient_mobile=phone,
        otp=otp,
        template_name="custmer_otp"
    )


# =========================================================
# ROUTE: SEND OTP FOR CUSTOMER PHONE VERIFICATION
# POST /whatsapp/send-otp/{customer_id}
# =========================================================

@router.post("/send-otp/{customer_id}")
def send_customer_otp(customer_id: str):
    """
    Sends WhatsApp OTP to verify an existing customer's phone number using custom customer ID (e.g. CUST1001) or ObjectId.
    """
    query: Dict[str, Any] = {"id": customer_id}
    if ObjectId.is_valid(customer_id):
        query = {"$or": [{"id": customer_id}, {"_id": ObjectId(customer_id)}]}

    customer = customers_collection.find_one(query)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    phone = customer.get("mobile")
    if not phone:
        raise HTTPException(status_code=400, detail="Customer mobile number not found")

    phone = normalize_indian_phone(phone)

    if customer.get("phone_verified", False):
        return {
            "status": True,
            "message": "Customer phone number is already verified",
            "data": {
                "phone_verified": True,
                "otp_sent": False
            }
        }

    # Invalidate previous unverified OTPs for this customer
    otp_collection.update_many(
        {
            "customer_id": customer["_id"],
            "verified": False,
            "invalidated": False
        },
        {"$set": {"invalidated": True}}
    )

    otp = generate_otp()
    now = datetime.now(timezone.utc)

    otp_collection.insert_one({
        "customer_id": customer["_id"],
        "customer_custom_id": customer.get("id"),
        "phone": phone,
        "otp_hash": hash_otp(otp),
        "purpose": "phone_verification",
        "verified": False,
        "invalidated": False,
        "attempts": 0,
        "created_at": now,
        "expires_at": now + timedelta(minutes=10)
    })

    try:
        send_whatsapp_otp(phone, otp)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send OTP via WhatsApp: {str(e)}"
        )

    return {
        "status": True,
        "message": "OTP sent successfully",
        "data": {
            "customer_id": customer.get("id"),
            "mobile": phone,
            "otp_sent": True,
            "expires_in": 600
        }
    }


# =========================================================
# ROUTE: VERIFY OTP FOR CUSTOMER PHONE VERIFICATION
# POST /whatsapp/verify-otp/{customer_id}
# =========================================================

@router.post("/verify-otp/{customer_id}")
def verify_customer_otp(customer_id: str, data: VerifyOTPRequest):
    """
    Verifies the WhatsApp OTP submitted for a customer's phone verification.
    """
    query: Dict[str, Any] = {"id": customer_id}
    if ObjectId.is_valid(customer_id):
        query = {"$or": [{"id": customer_id}, {"_id": ObjectId(customer_id)}]}

    customer = customers_collection.find_one(query)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    if customer.get("phone_verified", False):
        return {
            "status": True,
            "message": "Phone number already verified",
            "data": {
                "phone_verified": True
            }
        }

    otp_record = otp_collection.find_one(
        {
            "customer_id": customer["_id"],
            "verified": False,
            "invalidated": False
        },
        sort=[("created_at", -1)]
    )

    if not otp_record:
        raise HTTPException(
            status_code=400,
            detail="OTP not found or already used. Please request a new OTP."
        )

    now = datetime.now(timezone.utc)
    expires_at = otp_record.get("expires_at")
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at and now > expires_at:
        otp_collection.update_one(
            {"_id": otp_record["_id"]},
            {"$set": {"invalidated": True}}
        )
        raise HTTPException(
            status_code=400,
            detail="OTP expired. Please request a new OTP."
        )

    attempts = otp_record.get("attempts", 0)
    if attempts >= 5:
        otp_collection.update_one(
            {"_id": otp_record["_id"]},
            {"$set": {"invalidated": True}}
        )
        raise HTTPException(
            status_code=429,
            detail="Too many incorrect attempts. This OTP has been invalidated. Please request a new OTP."
        )

    submitted_hash = hash_otp(data.otp.strip())
    if submitted_hash != otp_record.get("otp_hash"):
        otp_collection.update_one(
            {"_id": otp_record["_id"]},
            {"$inc": {"attempts": 1}}
        )
        remaining = 4 - attempts
        raise HTTPException(
            status_code=400,
            detail=f"Invalid OTP. {remaining} attempt(s) remaining."
        )

    # Mark OTP as verified
    otp_collection.update_one(
        {"_id": otp_record["_id"]},
        {"$set": {"verified": True, "verified_at": now}}
    )

    # Update customer record
    customers_collection.update_one(
        {"_id": customer["_id"]},
        {
            "$set": {
                "phone_verified": True,
                "phone_verified_at": now,
                "updated_at": now
            }
        }
    )

    return {
        "status": True,
        "message": "Phone number verified successfully",
        "data": {
            "customer_id": customer.get("id"),
            "mobile": customer.get("mobile"),
            "phone_verified": True,
            "phone_verified_at": now
        }
    }


