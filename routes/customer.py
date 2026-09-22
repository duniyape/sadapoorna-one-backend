from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from typing import Optional, List, Tuple, Dict, Any
from datetime import datetime, timezone, timedelta
from bson import ObjectId
from utils import convert_utc_to_ist
import jwt
import random
import hashlib
from database import (
    customers_collection,
    branches_collection,
    users_collection,
    otp_collection
)
from services.whatsapp_service import (send_otp_template_whatsapp)


router = APIRouter()


# =========================================================
# CUSTOMER LOGIN & OTP MODELS
# =========================================================

class CustomerLoginSendOTPRequest(BaseModel):
    mobile: str


class CustomerLoginVerifyOTPRequest(BaseModel):
    mobile: str
    otp: str


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
    
    beat_id: Optional[str] = None

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

        "beat_id": (
            customer.beat_id.strip()
            if customer.beat_id
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

        send_otp_template_whatsapp(phone, otp, "custmer_otp")
        otp_sent = True

    except Exception as e:
        print("OTP sending failed:", str(e))

    # =====================================================
    # RESPONSE
    # =====================================================

    return convert_utc_to_ist({

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

            "beat_id": (
                customer.beat_id
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
    })


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

            "beat_id": customer.get(
                "beat_id"
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

    return convert_utc_to_ist({

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

    })

# =========================================================
# ROUTE: DUE CUSTOMERS LIST WITH AGING
# GET /customer/due
# GET /customer/aging
# =========================================================

@router.get("/due")
@router.get("/aging")
def get_customer_due_aging_list(
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
    from routes.accounting import serialize_doc
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
        return convert_utc_to_ist({
            "status": True,
            "success": True,
            **serialize_doc(res)
        })
    except Exception as ex:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch due customers list: {ex}"
        )

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
    if not customer and ObjectId.is_valid(customer_id):
        customer = customers_collection.find_one({
            "_id": ObjectId(customer_id)
        })
    if not customer and customer_id.upper().startswith("CUST"):
        customer = customers_collection.find_one({
            "id": customer_id.upper()
        })

    if not customer:

        raise HTTPException(
            status_code=404,
            detail="Customer not found"
        )

    # =====================================================
    # RESPONSE
    # =====================================================

    return convert_utc_to_ist({

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

            "beat_id": (
                customer.get(
                    "beat_id"
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
    })

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
    if not existing_customer and ObjectId.is_valid(customer_id):
        existing_customer = customers_collection.find_one({
            "_id": ObjectId(customer_id)
        })
    if not existing_customer and customer_id.upper().startswith("CUST"):
        existing_customer = customers_collection.find_one({
            "id": customer_id.upper()
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

        "beat_id": (
            customer.beat_id.strip()
            if customer.beat_id
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

    return convert_utc_to_ist({
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

            "beat_id": (
                updated_customer.get(
                    "beat_id"
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
    })


def get_phone_search_variants(phone: str) -> Tuple[str, List[str]]:
    """
    Normalizes Indian mobile number and returns:
    - E.164 formatted number with 91 prefix (e.g., '919876543210')
    - List of query variants to match MongoDB representations ('9876543210', '919876543210', '+919876543210')
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

    e164_number = f"91{cleaned}"
    variants = [
        cleaned,
        e164_number,
        f"+91{cleaned}"
    ]
    return e164_number, variants


def create_customer_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(days=30)
    to_encode["exp"] = expire
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_customer(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> Dict[str, Any]:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        customer_id = payload.get("customer_id") or payload.get("user_id")
        if not customer_id:
            raise HTTPException(status_code=401, detail="Invalid token payload")

        query = {}
        if ObjectId.is_valid(customer_id):
            query = {"_id": ObjectId(customer_id)}
        else:
            query = {"id": customer_id}

        customer = customers_collection.find_one(query)
        if not customer:
            raise HTTPException(status_code=404, detail="Customer account not found")
        if customer.get("status") == "inactive":
            raise HTTPException(status_code=403, detail="Customer account is inactive")
        return customer
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


# =========================================================
# HELPER: SEND OTP FOR CUSTOMER LOGIN
# (Exposed via POST /auth/customer/send-otp)
# =========================================================

def customer_login_send_otp(data: CustomerLoginSendOTPRequest):
    e164_phone, phone_variants = get_phone_search_variants(data.mobile)

    customer = customers_collection.find_one({
        "$or": [
            {"mobile": {"$in": phone_variants}},
            {"alternate_mobile": {"$in": phone_variants}}
        ]
    })

    if not customer:
        raise HTTPException(
            status_code=404,
            detail="Customer not registered with this mobile number. Please contact administrator or register."
        )

    if customer.get("status") == "inactive":
        raise HTTPException(
            status_code=403,
            detail="Customer account is inactive. Please contact administrator."
        )

    # Invalidate existing unverified OTPs for this customer
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
        "phone": e164_phone,
        "otp_hash": hash_otp(otp),
        "purpose": "customer_login",
        "verified": False,
        "invalidated": False,
        "attempts": 0,
        "created_at": now,
        "expires_at": now + timedelta(minutes=10)
    })

    # Dispatch WhatsApp Cloud API template
    try:
        send_otp_template_whatsapp(
            recipient_mobile=e164_phone,
            otp=otp,
            template_name="custmer_otp"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send OTP via WhatsApp: {str(e)}"
        )

    return convert_utc_to_ist({
        "status": True,
        "message": "OTP sent successfully to your WhatsApp number",
        "data": {
            "mobile": e164_phone,
            "customer_id": customer.get("id"),
            "customer_name": customer.get("name"),
            "otp_sent": True,
            "expires_in": 600
        }
    })


# =========================================================
# HELPER: VERIFY OTP & LOGIN
# (Exposed via POST /auth/customer/verify-otp)
# =========================================================

def customer_login_verify_otp(data: CustomerLoginVerifyOTPRequest):
    e164_phone, phone_variants = get_phone_search_variants(data.mobile)

    customer = customers_collection.find_one({
        "$or": [
            {"mobile": {"$in": phone_variants}},
            {"alternate_mobile": {"$in": phone_variants}}
        ]
    })

    if not customer:
        raise HTTPException(
            status_code=404,
            detail="Customer not found with this mobile number."
        )

    if customer.get("status") == "inactive":
        raise HTTPException(
            status_code=403,
            detail="Customer account is inactive. Please contact administrator."
        )

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

    # Mark OTP verified
    otp_collection.update_one(
        {"_id": otp_record["_id"]},
        {"$set": {"verified": True, "verified_at": now}}
    )

    # Invalidate any other open OTP records for this customer
    otp_collection.update_many(
        {
            "customer_id": customer["_id"],
            "_id": {"$ne": otp_record["_id"]},
            "verified": False
        },
        {"$set": {"invalidated": True}}
    )

    # Update customer record
    customers_collection.update_one(
        {"_id": customer["_id"]},
        {
            "$set": {
                "phone_verified": True,
                "phone_verified_at": now,
                "last_login_at": now,
                "updated_at": now
            }
        }
    )

    token_payload = {
        "user_id": str(customer["_id"]),
        "customer_id": str(customer["_id"]),
        "custom_id": customer.get("id"),
        "role": "customer",
        "name": customer.get("name"),
        "mobile": customer.get("mobile"),
        "customer_type": customer.get("customer_type"),
        "branch_id": customer.get("branch_id"),
    }
    access_token = create_customer_access_token(token_payload)

    return convert_utc_to_ist({
        "status": True,
        "message": "Login successful",
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": 30 * 24 * 3600,
        "customer": {
            "id": str(customer["_id"]),
            "custom_id": customer.get("id"),
            "customer_type": customer.get("customer_type"),
            "name": customer.get("name"),
            "company_name": customer.get("company_name"),
            "mobile": customer.get("mobile"),
            "alternate_mobile": customer.get("alternate_mobile"),
            "email": customer.get("email"),
            "billing_address": customer.get("billing_address"),
            "shipping_address": customer.get("shipping_address"),
            "gst_number": customer.get("gst_number"),
            "branch_id": customer.get("branch_id"),
            "beat_id": customer.get("beat_id"),
            "phone_verified": True,
            "status": customer.get("status")
        }
    })


# =========================================================
# ROUTE: GET AUTHENTICATED CUSTOMER PROFILE
# GET /customer/profile
# =========================================================

@router.get("/profile")
def get_authenticated_customer_profile(
    current_customer: Dict[str, Any] = Depends(get_current_customer)
):
    return convert_utc_to_ist({
        "status": True,
        "message": "Customer profile retrieved successfully",
        "data": {
            "id": str(current_customer["_id"]),
            "custom_id": current_customer.get("id"),
            "customer_type": current_customer.get("customer_type"),
            "name": current_customer.get("name"),
            "company_name": current_customer.get("company_name"),
            "mobile": current_customer.get("mobile"),
            "alternate_mobile": current_customer.get("alternate_mobile"),
            "email": current_customer.get("email"),
            "billing_address": current_customer.get("billing_address"),
            "shipping_address": current_customer.get("shipping_address"),
            "gst_number": current_customer.get("gst_number"),
            "branch_id": current_customer.get("branch_id"),
            "beat_id": current_customer.get("beat_id"),
            "phone_verified": current_customer.get("phone_verified", False),
            "phone_verified_at": current_customer.get("phone_verified_at"),
            "status": current_customer.get("status"),
            "last_login_at": current_customer.get("last_login_at"),
            "created_at": current_customer.get("created_at"),
        }
    })





