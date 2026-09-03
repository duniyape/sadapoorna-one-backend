from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone
from database import (
    customers_collection,
    otp_collection
)
from routes.auth import (get_current_user)

import requests
import os
import random
import hashlib


router = APIRouter()


ACCESS_TOKEN = "EAA6rtuUkSgIBOw1ZBKc0daGfX8SSbt86QetCckUtCodtMy2ZA44d9e0nrEUhZAsxaroHpX1217ROdLpkDRD1RwKa0VWMzgy5eMfIBv4WN1CYhXnAfXx7psCzgZB2xJkEZABscWDYYsKRwBHXMnfBdT905ZCLklGOnXS8tCaqsDGpoK7s5XlkOxgh4udFz67qw5aQZDZD"
PHONE_NUMBER_ID = "670517682822062"
WHATSAPP_URL = f"https://graph.facebook.com/v23.0/{PHONE_NUMBER_ID}/messages"


# --------------------------------------------------
# Request Models
# --------------------------------------------------
class VerifyOTPRequest(BaseModel):
    otp: str


# --------------------------------------------------
# Generate OTP
# --------------------------------------------------

def generate_otp():
    return str(random.randint(100000, 999999))


# --------------------------------------------------
# Hash OTP
# --------------------------------------------------
def hash_otp(otp: str):
    return hashlib.sha256(otp.encode("utf-8")).hexdigest()

def normalize_indian_phone(phone: str):
    phone = phone.strip().replace(" ", "").replace("-", "")

    if phone.startswith("+91"):
        phone = phone[3:]
    elif phone.startswith("91") and len(phone) == 12:
        phone = phone[2:]
    elif phone.startswith("0") and len(phone) == 11:
        phone = phone[1:]

    if len(phone) != 10 or not phone.isdigit():
        raise HTTPException(
            status_code=400,
            detail="Invalid Indian mobile number"
        )

    if not phone.startswith(("6", "7", "8", "9")):
        raise HTTPException(
            status_code=400,
            detail="Invalid Indian mobile number"
        )

    return "91" + phone

def send_whatsapp_otp(phone: str, otp: str):
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": {
            "name": "custmer_otp",
            "language": {
                "code": "en_US"
            },
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {
                            "type": "text",
                            "text": otp
                        }
                    ]
                },
                {
                    "type": "button",
                    "sub_type": "url",
                    "index": "0",
                    "parameters": [
                        {
                            "type": "text",
                            "text": otp
                        }
                    ]
                }
            ]
        }
    }
    response = requests.post(
        WHATSAPP_URL,
        headers=headers,
        json=payload,
        timeout=15
    )
    result = response.json()
    if response.status_code not in [200, 201]:
        raise Exception(result)
    return result

@router.post("/send-otp/{customer_id}")
def send_customer_otp(
    customer_id: str,
    # current_user: dict = Depends(get_current_user)
):
    customer = customers_collection.find_one({"id": customer_id})
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

    otp_collection.update_many(
        {
            "customer_id": customer["_id"],
            "verified": False,
            "invalidated": False
        },
        {
            "$set": {
                "invalidated": True
            }
        }
    )

    otp = generate_otp()
    now = datetime.now(timezone.utc)

    otp_collection.insert_one({
        "customer_id": customer["_id"],
        "customer_custom_id": customer["id"],
        "phone": phone,
        "otp_hash": hash_otp(otp),
        "verified": False,
        "invalidated": False,
        "attempts": 0,
        "created_at": now,
        "expires_at": now + timedelta(minutes=10)
    })

    try:
        send_whatsapp_otp(phone, otp)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send OTP: {str(e)}"
        )

    return {
        "status": True,
        "message": "OTP sent successfully",
        "data": {
            "customer_id": customer["id"],
            "mobile": phone,
            "otp_sent": True,
            "expires_in": 600
        }
    }

@router.post("/verify-otp/{customer_id}")
def verify_customer_otp(
    customer_id: str,
    data: VerifyOTPRequest,
    # current_user: dict = Depends(get_current_user)
):
    customer = customers_collection.find_one({"id": customer_id})
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
            detail="OTP not found. Please request a new OTP."
        )

    now = datetime.now(timezone.utc)

    expires_at = otp_record["expires_at"]

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if now > expires_at:
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
            detail="Too many incorrect attempts. Please request a new OTP."
        )

    submitted_hash = hash_otp(data.otp.strip())

    if submitted_hash != otp_record["otp_hash"]:
        otp_collection.update_one(
            {"_id": otp_record["_id"]},
            {"$inc": {"attempts": 1}}
        )
        raise HTTPException(
            status_code=400,
            detail="Invalid OTP"
        )

    otp_collection.update_one(
        {"_id": otp_record["_id"]},
        {
            "$set": {
                "verified": True,
                "verified_at": now
            }
        }
    )

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
            "customer_id": customer["id"],
            "mobile": customer["mobile"],
            "phone_verified": True,
            "phone_verified_at": now
        }
    }

