from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime, timezone, timedelta
from bson import ObjectId
import jwt
import random
import hashlib
from database import (
    customers_collection,
    branches_collection,
    users_collection,
    otp_collection
)
from routes.whatsapp import (send_whatsapp_otp)


router = APIRouter()

def generate_otp():
    return str(random.randint(100000, 999999))

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


# =========================================================
# JWT CONFIGURATION
# =========================================================

SECRET_KEY = "sadapoorna_secret_key_2026"

ALGORITHM = "HS256"


security = HTTPBearer()


# =========================================================
# JWT AUTHENTICATION
# =========================================================

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(
        security
    )
):

    token = credentials.credentials

    try:

        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM]
        )

        user_id = payload.get(
            "user_id"
        )

        if not user_id:

            raise HTTPException(
                status_code=401,
                detail="Invalid token"
            )

        # -----------------------------------
        # Validate ObjectId
        # -----------------------------------

        if not ObjectId.is_valid(user_id):

            raise HTTPException(
                status_code=401,
                detail="Invalid user ID in token"
            )

        return payload

    except jwt.ExpiredSignatureError:

        raise HTTPException(
            status_code=401,
            detail="Token has expired"
        )

    except jwt.InvalidTokenError:

        raise HTTPException(
            status_code=401,
            detail="Invalid token"
        )


# =========================================================
# ADDRESS MODEL
# =========================================================

class Address(BaseModel):

    address: Optional[str] = None

    city: Optional[str] = None

    state: Optional[str] = None

    pincode: Optional[str] = None

class Location(BaseModel):
    lat: float
    lng: float

# =========================================================
# CUSTOMER CREATE / UPDATE MODEL
# =========================================================

class CustomerCreate(BaseModel):

    # -----------------------------------
    # Basic Details
    # -----------------------------------

    customer_type: str

    name: str

    email: Optional[EmailStr] = None

    mobile: str

    alternate_mobile: Optional[str] = None

    # -----------------------------------
    # Address
    # -----------------------------------

    billing_address: Address

    shipping_address: Address

    sameAsBilling: bool = False

    # -----------------------------------
    # Business Details
    # -----------------------------------

    company_name: Optional[str] = None

    business_type: Optional[str] = None

    gst_number: Optional[str] = None

    # -----------------------------------
    # Assignment
    # -----------------------------------

    branch_id: str

    assigned_employee_id: str
    location: Optional[Location] = None


# =========================================================
# CUSTOMER ID GENERATOR
# =========================================================

def generate_customer_id():

    last_customer = customers_collection.find_one(
        {},
        sort=[
            ("_id", -1)
        ]
    )

    if not last_customer:

        number = 1001

    else:

        last_id = last_customer.get(
            "id",
            "CUST1000"
        )

        try:

            number = (
                int(
                    last_id.replace(
                        "CUST",
                        ""
                    )
                )
                + 1
            )

        except:

            number = 1001

    return f"CUST{number}"


# =========================================================
# CREATE CUSTOMER
# =========================================================

@router.post("/create")
def create_customer(
    customer: CustomerCreate,
    current_user: dict = Depends(
        get_current_user
    )
):

    # =====================================================
    # VALIDATE BRANCH
    # =====================================================

    if not ObjectId.is_valid(
        customer.branch_id
    ):

        raise HTTPException(
            status_code=400,
            detail="Invalid branch_id"
        )

    branch = branches_collection.find_one({
        "_id": ObjectId(
            customer.branch_id
        )
    })

    if not branch:

        raise HTTPException(
            status_code=404,
            detail="Branch not found"
        )

    # =====================================================
    # VALIDATE EMPLOYEE
    # =====================================================

    if not ObjectId.is_valid(
        customer.assigned_employee_id
    ):

        raise HTTPException(
            status_code=400,
            detail="Invalid assigned_employee_id"
        )

    employee = users_collection.find_one({
        "_id": ObjectId(
            customer.assigned_employee_id
        )
    })

    if not employee:

        raise HTTPException(
            status_code=404,
            detail="Assigned employee not found"
        )

    # =====================================================
    # BUSINESS VALIDATION
    # =====================================================

    if customer.customer_type.lower() == "business":

        if not customer.company_name:

            raise HTTPException(
                status_code=400,
                detail=(
                    "company_name is required "
                    "for business customer"
                )
            )

    # =====================================================
    # CHECK DUPLICATE MOBILE
    # =====================================================

    mobile = normalize_indian_phone(customer.mobile)
    existing_mobile = customers_collection.find_one({
        "mobile": mobile
    })

    if existing_mobile:

        raise HTTPException(
            status_code=409,
            detail=(
                "Customer with this mobile "
                "number already exists"
            )
        )

    # =====================================================
    # BILLING ADDRESS
    # =====================================================

    billing_address = (
        customer.billing_address.model_dump()
    )

    # =====================================================
    # SHIPPING ADDRESS
    # =====================================================

    if customer.sameAsBilling:

        shipping_address = (
            billing_address.copy()
        )

    else:

        shipping_address = (
            customer.shipping_address.model_dump()
        )

    location = (
    customer.location.model_dump()
    if customer.location
    else None
    )

    # =====================================================
    # CUSTOMER ID
    # =====================================================

    customer_id = generate_customer_id()

    # =====================================================
    # CURRENT TIME
    # =====================================================

    now = datetime.now(
        timezone.utc
    )

    # =====================================================
    # JWT USER ID
    # =====================================================

    created_by = current_user.get(
        "user_id"
    )

    if not created_by:

        raise HTTPException(
            status_code=401,
            detail="User ID not found in token"
        )

    # =====================================================
    # CUSTOMER DATA
    # =====================================================

    customer_data = {

        # -----------------------------------
        # Customer ID
        # -----------------------------------

        "id": customer_id,

        # -----------------------------------
        # Basic Details
        # -----------------------------------

        "customer_type": (
            customer.customer_type.strip()
        ),

        "name": (
            customer.name.strip()
        ),

        "email": (
            str(customer.email)
            if customer.email
            else None
        ),

        "mobile": (
            mobile
        ),

        "alternate_mobile": (
            customer.alternate_mobile.strip()
            if customer.alternate_mobile
            else None
        ),

        # -----------------------------------
        # Business Details
        # -----------------------------------

        "company_name": (
            customer.company_name.strip()
            if customer.company_name
            else None
        ),

        "business_type": (
            customer.business_type.strip()
            if customer.business_type
            else None
        ),

        "gst_number": (
            customer.gst_number.strip()
            if customer.gst_number
            else None
        ),

        # -----------------------------------
        # Address
        # -----------------------------------

        "billing_address": (
            billing_address
        ),

        "shipping_address": (
            shipping_address
        ),

        "sameAsBilling": (
            customer.sameAsBilling
        ),

        # -----------------------------------
        # Assignment
        # -----------------------------------

        "branch_id": (
            customer.branch_id
        ),

        "assigned_employee_id": (
            customer.assigned_employee_id
        ),
        "location": location,

        # -----------------------------------
        # Audit
        # -----------------------------------

        "created_by": created_by,

        "created_at": now,

        "updated_by": None,

        "updated_at": now,

        # -----------------------------------
        # Status
        # -----------------------------------

        "status": "active",
        "phone_verified": False,
        "phone_verified_at": None,
    }

    # =====================================================
    # INSERT
    # =====================================================

    result = customers_collection.insert_one(
        customer_data
    )

    otp_sent = False

    try:
        phone = normalize_indian_phone(customer.mobile.strip())
        otp = generate_otp()
        now_otp = datetime.now(timezone.utc)

        otp_collection.insert_one({
            "customer_id": result.inserted_id,
            "customer_custom_id": customer_id,
            "phone": phone,
            "otp_hash": hash_otp(otp),
            "verified": False,
            "invalidated": False,
            "attempts": 0,
            "created_at": now_otp,
            "expires_at": now_otp + timedelta(minutes=10)
        })

        send_whatsapp_otp(phone, otp)
        otp_sent = True

    except Exception as e:
        print("OTP sending failed:", str(e))

    # =====================================================
    # RESPONSE
    # =====================================================

    return {

        "status": True,

        "message": (
            "Customer created successfully"
        ),

        "data": {

            "mongo_id": str(
                result.inserted_id
            ),

            "id": customer_id,

            "customer_type": (
                customer.customer_type
            ),

            "name": customer.name,

            "email": (
                str(customer.email)
                if customer.email
                else None
            ),

            "mobile": mobile,

            "alternate_mobile": (
                customer.alternate_mobile
            ),

            "company_name": (
                customer.company_name
            ),

            "business_type": (
                customer.business_type
            ),

            "gst_number": (
                customer.gst_number
            ),

            "billing_address": (
                billing_address
            ),

            "shipping_address": (
                shipping_address
            ),

            "sameAsBilling": (
                customer.sameAsBilling
            ),

            "branch_id": (
                customer.branch_id
            ),

            "assigned_employee_id": (
                customer.assigned_employee_id
            ),

            "created_by": created_by,

            "created_at": now,

            "status": "active",
            "phone_verified": False,
            "otp_sent": otp_sent
        }
    }


# =========================================================
# GET CUSTOMER LIST
# =========================================================
@router.get("/list")
def get_customers(

    # =====================================================
    # PAGINATION
    # =====================================================

    page: int = Query(
        1,
        ge=1
    ),

    limit: int = Query(
        20,
        ge=1,
        le=100
    ),

    # =====================================================
    # SEARCH
    # =====================================================

    search: Optional[str] = None,

    # =====================================================
    # FILTERS
    # =====================================================

    branch_id: Optional[str] = None,

    assigned_employee_id: Optional[str] = None,

    customer_type: Optional[str] = None,

    status: Optional[str] = None,

    # =====================================================
    # CURRENT USER
    # =====================================================

    current_user: dict = Depends(
        get_current_user
    )
):

    # =====================================================
    # QUERY
    # =====================================================

    query = {}

    # =====================================================
    # SEARCH
    # =====================================================

    if search:

        search = search.strip()

        if search:

            query["$or"] = [

                {
                    "name": {
                        "$regex": search,
                        "$options": "i"
                    }
                },

                {
                    "company_name": {
                        "$regex": search,
                        "$options": "i"
                    }
                },

                {
                    "mobile": {
                        "$regex": search,
                        "$options": "i"
                    }
                },

                {
                    "email": {
                        "$regex": search,
                        "$options": "i"
                    }
                },

                {
                    "id": {
                        "$regex": search,
                        "$options": "i"
                    }
                }

            ]

    # =====================================================
    # BRANCH FILTER
    # =====================================================

    if branch_id:

        query["branch_id"] = branch_id.strip()

    # =====================================================
    # EMPLOYEE FILTER
    # Supports:
    #
    # ?assigned_employee_id=EMP001
    #
    # ?assigned_employee_id=EMP001,EMP002,EMP003
    # =====================================================

    if assigned_employee_id:

        employee_ids = [

            emp_id.strip()

            for emp_id
            in assigned_employee_id.split(",")

            if emp_id.strip()

        ]

        if len(employee_ids) == 1:

            query["assigned_employee_id"] = employee_ids[0]

        elif len(employee_ids) > 1:

            query["assigned_employee_id"] = {
                "$in": employee_ids
            }

    # =====================================================
    # CUSTOMER TYPE
    # =====================================================

    if customer_type:

        query["customer_type"] = customer_type.strip()

    # =====================================================
    # STATUS
    # =====================================================

    if status:

        query["status"] = status.strip()

    # =====================================================
    # PAGINATION
    # =====================================================

    skip = (
        (page - 1) * limit
    )

    # =====================================================
    # TOTAL
    # =====================================================

    total = (
        customers_collection
        .count_documents(query)
    )

    # =====================================================
    # GET CUSTOMERS
    # =====================================================

    customers = list(

        customers_collection
        .find(query)
        .sort(
            "created_at",
            -1
        )
        .skip(skip)
        .limit(limit)

    )

    # =====================================================
    # FORMAT DATA
    # =====================================================

    data = []

    for customer in customers:

        data.append({

            "mongo_id": str(
                customer["_id"]
            ),

            "id": customer.get(
                "id"
            ),

            "customer_type": customer.get(
                "customer_type"
            ),

            "name": customer.get(
                "name"
            ),

            "email": customer.get(
                "email"
            ),

            "mobile": customer.get(
                "mobile"
            ),

            "alternate_mobile": customer.get(
                "alternate_mobile"
            ),

            "company_name": customer.get(
                "company_name"
            ),

            "business_type": customer.get(
                "business_type"
            ),

            "gst_number": customer.get(
                "gst_number"
            ),

            "billing_address": customer.get(
                "billing_address"
            ),

            "shipping_address": customer.get(
                "shipping_address"
            ),

            "sameAsBilling": customer.get(
                "sameAsBilling"
            ),

            "branch_id": customer.get(
                "branch_id"
            ),

            "assigned_employee_id": customer.get(
                "assigned_employee_id"
            ),

            "created_by": customer.get(
                "created_by"
            ),
            "location": customer.get(
                "location"
            ),

            "created_at": customer.get(
                "created_at"
            ),

            "updated_by": customer.get(
                "updated_by"
            ),

            "updated_at": customer.get(
                "updated_at"
            ),

            "status": customer.get(
                "status"
            ),
            "phone_verified": customer.get(
                "phone_verified"
            )

        })

    # =====================================================
    # TOTAL PAGES
    # =====================================================

    total_pages = (

        (total + limit - 1)
        // limit

    )

    # =====================================================
    # RESPONSE
    # =====================================================

    return {

        "status": True,

        "data": data,

        "pagination": {

            "page": page,

            "limit": limit,

            "total": total,

            "total_pages": total_pages,

            "has_next": (
                page < total_pages
            ),

            "has_previous": (
                page > 1
            )

        }

    }

# # =========================================================
# # GET SINGLE CUSTOMER
# # =========================================================

@router.get("/{customer_id}")
def get_customer(

    customer_id: str,

    current_user: dict = Depends(
        get_current_user
    )
):

    # =====================================================
    # FIND CUSTOMER
    # =====================================================

    customer = customers_collection.find_one({
        "id": customer_id
    })

    if not customer:

        raise HTTPException(
            status_code=404,
            detail="Customer not found"
        )

    # =====================================================
    # RESPONSE
    # =====================================================

    return {

        "status": True,

        "data": {

            "mongo_id": str(
                customer["_id"]
            ),

            "id": customer.get(
                "id"
            ),

            # -----------------------------------
            # Basic
            # -----------------------------------

            "customer_type": (
                customer.get(
                    "customer_type"
                )
            ),

            "name": (
                customer.get(
                    "name"
                )
            ),

            "email": (
                customer.get(
                    "email"
                )
            ),

            "mobile": (
                customer.get(
                    "mobile"
                )
            ),

            "alternate_mobile": (
                customer.get(
                    "alternate_mobile"
                )
            ),

            # -----------------------------------
            # Business
            # -----------------------------------

            "company_name": (
                customer.get(
                    "company_name"
                )
            ),

            "business_type": (
                customer.get(
                    "business_type"
                )
            ),

            "gst_number": (
                customer.get(
                    "gst_number"
                )
            ),

            # -----------------------------------
            # Address
            # -----------------------------------

            "billing_address": (
                customer.get(
                    "billing_address"
                )
            ),

            "shipping_address": (
                customer.get(
                    "shipping_address"
                )
            ),

            "sameAsBilling": (
                customer.get(
                    "sameAsBilling"
                )
            ),

            # -----------------------------------
            # Assignment
            # -----------------------------------

            "branch_id": (
                customer.get(
                    "branch_id"
                )
            ),

            "assigned_employee_id": (
                customer.get(
                    "assigned_employee_id"
                )
            ),

            # -----------------------------------
            # Audit
            # -----------------------------------

            "created_by": (
                customer.get(
                    "created_by"
                )
            ),

            "created_at": (
                customer.get(
                    "created_at"
                )
            ),

            "updated_by": (
                customer.get(
                    "updated_by"
                )
            ),

            "updated_at": (
                customer.get(
                    "updated_at"
                )
            ),
            "location": customer.get(
                            "location"
                        ),
            "phone_verified": customer.get(
                        "phone_verified"
                    ),

            # -----------------------------------
            # Status
            # -----------------------------------

            "status": (
                customer.get(
                    "status"
                )
            )
        }
    }

# =========================================================
# UPDATE CUSTOMER
# =========================================================

@router.post("/update/{customer_id}")
def update_customer(
    customer_id: str,
    customer: CustomerCreate,
    current_user: dict = Depends(get_current_user)
):
    # =====================================================
    # FIND CUSTOMER
    # =====================================================

    existing_customer = customers_collection.find_one({
        "id": customer_id
    })

    if not existing_customer:
        raise HTTPException(
            status_code=404,
            detail="Customer not found"
        )

    # =====================================================
    # VALIDATE MOBILE
    # =====================================================

    new_mobile = normalize_indian_phone(
        customer.mobile
    )

    old_mobile = normalize_indian_phone(
        existing_customer.get("mobile", "")
    )

    # =====================================================
    # CHECK IF MOBILE CHANGED
    # =====================================================

    mobile_changed = old_mobile != new_mobile

    # =====================================================
    # VALIDATE BRANCH
    # =====================================================

    if not ObjectId.is_valid(customer.branch_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid branch_id"
        )

    branch = branches_collection.find_one({
        "_id": ObjectId(customer.branch_id)
    })

    if not branch:
        raise HTTPException(
            status_code=404,
            detail="Branch not found"
        )

    # =====================================================
    # VALIDATE EMPLOYEE
    # =====================================================

    if not ObjectId.is_valid(
        customer.assigned_employee_id
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid assigned_employee_id"
        )

    employee = users_collection.find_one({
        "_id": ObjectId(
            customer.assigned_employee_id
        )
    })

    if not employee:
        raise HTTPException(
            status_code=404,
            detail="Assigned employee not found"
        )

    # =====================================================
    # BUSINESS VALIDATION
    # =====================================================

    if customer.customer_type.lower() == "business":
        if not customer.company_name:
            raise HTTPException(
                status_code=400,
                detail=(
                    "company_name is required "
                    "for business customer"
                )
            )

    # =====================================================
    # CHECK DUPLICATE MOBILE
    # =====================================================

    existing_mobile = customers_collection.find_one({
        "mobile": new_mobile,
        "id": {
            "$ne": customer_id
        }
    })

    if existing_mobile:
        raise HTTPException(
            status_code=409,
            detail=(
                "Customer with this mobile "
                "number already exists"
            )
        )

    # =====================================================
    # BILLING ADDRESS
    # =====================================================

    billing_address = (
        customer.billing_address.model_dump()
    )

    # =====================================================
    # SHIPPING ADDRESS
    # =====================================================

    if customer.sameAsBilling:
        shipping_address = billing_address.copy()
    else:
        shipping_address = (
            customer.shipping_address.model_dump()
        )

    # =====================================================
    # UPDATED TIME
    # =====================================================

    now = datetime.now(timezone.utc)

    # =====================================================
    # UPDATED BY
    # =====================================================

    updated_by = current_user.get("user_id")

    if not updated_by:
        raise HTTPException(
            status_code=401,
            detail="User ID not found in token"
        )

    # =====================================================
    # UPDATE DATA
    # =====================================================

    update_data = {
        # -----------------------------------
        # Basic
        # -----------------------------------

        "customer_type": (
            customer.customer_type.strip()
        ),

        "name": (
            customer.name.strip()
        ),

        "email": (
            str(customer.email)
            if customer.email
            else None
        ),

        "mobile": new_mobile,

        "alternate_mobile": (
            customer.alternate_mobile.strip()
            if customer.alternate_mobile
            else None
        ),

        # -----------------------------------
        # Business
        # -----------------------------------

        "company_name": (
            customer.company_name.strip()
            if customer.company_name
            else None
        ),

        "business_type": (
            customer.business_type.strip()
            if customer.business_type
            else None
        ),

        "gst_number": (
            customer.gst_number.strip()
            if customer.gst_number
            else None
        ),

        # -----------------------------------
        # Address
        # -----------------------------------

        "billing_address": billing_address,

        "shipping_address": shipping_address,

        "sameAsBilling": customer.sameAsBilling,

        # -----------------------------------
        # Assignment
        # -----------------------------------

        "branch_id": customer.branch_id,

        "assigned_employee_id": (
            customer.assigned_employee_id
        ),

        # -----------------------------------
        # Audit
        # -----------------------------------

        "updated_by": updated_by,

        "updated_at": now
    }

    # =====================================================
    # MOBILE CHANGED
    # =====================================================

    if mobile_changed:

        update_data["phone_verified"] = False

        update_data["phone_verified_at"] = None

        # -----------------------------------------------
        # Invalidate old OTPs
        # -----------------------------------------------

        otp_collection.update_many(
            {
                "customer_id": existing_customer["_id"],
                "verified": False,
                "invalidated": False
            },
            {
                "$set": {
                    "invalidated": True
                }
            }
        )

    # =====================================================
    # UPDATE CUSTOMER
    # =====================================================

    customers_collection.update_one(
        {
            "id": customer_id
        },
        {
            "$set": update_data
        }
    )

    # =====================================================
    # GET UPDATED CUSTOMER
    # =====================================================

    updated_customer = customers_collection.find_one({
        "id": customer_id
    })

    # =====================================================
    # RESPONSE
    # =====================================================

    return {
        "status": True,

        "message": (
            "Customer updated successfully"
        ),

        "data": {
            "mongo_id": str(
                updated_customer["_id"]
            ),

            "id": updated_customer.get("id"),

            "customer_type": (
                updated_customer.get(
                    "customer_type"
                )
            ),

            "name": (
                updated_customer.get("name")
            ),

            "email": (
                updated_customer.get("email")
            ),

            "mobile": (
                updated_customer.get("mobile")
            ),

            "alternate_mobile": (
                updated_customer.get(
                    "alternate_mobile"
                )
            ),

            "company_name": (
                updated_customer.get(
                    "company_name"
                )
            ),

            "business_type": (
                updated_customer.get(
                    "business_type"
                )
            ),

            "gst_number": (
                updated_customer.get(
                    "gst_number"
                )
            ),

            "billing_address": (
                updated_customer.get(
                    "billing_address"
                )
            ),

            "shipping_address": (
                updated_customer.get(
                    "shipping_address"
                )
            ),

            "sameAsBilling": (
                updated_customer.get(
                    "sameAsBilling"
                )
            ),

            "branch_id": (
                updated_customer.get(
                    "branch_id"
                )
            ),

            "assigned_employee_id": (
                updated_customer.get(
                    "assigned_employee_id"
                )
            ),

            "created_by": (
                updated_customer.get(
                    "created_by"
                )
            ),

            "created_at": (
                updated_customer.get(
                    "created_at"
                )
            ),

            "updated_by": (
                updated_customer.get(
                    "updated_by"
                )
            ),

            "updated_at": (
                updated_customer.get(
                    "updated_at"
                )
            ),

            "phone_verified": (
                updated_customer.get(
                    "phone_verified",
                    False
                )
            ),

            "phone_verified_at": (
                updated_customer.get(
                    "phone_verified_at"
                )
            ),

            "status": (
                updated_customer.get(
                    "status"
                )
            )
        }
    }




