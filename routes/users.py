from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, EmailStr
from datetime import datetime
from pwdlib import PasswordHash
from bson import ObjectId
from database import users_collection

router = APIRouter()

password_hash = PasswordHash.recommended()


class User(BaseModel):

    # Basic Details
    name: str
    email: EmailStr
    mobile: str
    password: str
    profile_photo: str | None = None

    # Personal Details
    gender: str | None = None
    date_of_birth: str | None = None

    # Address
    address: str | None = None
    city: str | None = None
    state: str | None = None
    pincode: str | None = None

    # Job Details
    department: str | None = None
    designation: str | None = None
    joining_date: str | None = None
    employment_type: str | None = None
    branch: str | None = None

    # Salary
    salary: float | None = None
    salary_type: str | None = None

    # Bank Details
    bank_name: str | None = None
    account_number: str | None = None
    ifsc_code: str | None = None

    # Documents
    aadhaar_document: str | None = None
    pan_document: str | None = None
    driving_license_document: str | None = None

    aadhaar_number: str | None = None
    pan_number: str | None = None
    driving_license_number: str | None = None

    # Emergency Contact
    emergency_contact: str | None = None

    # Access
    role: str | None = "staff"
    status: str | None = "active"


@router.post("/create")
def create_user(user: User):

    # Check duplicate email
    existing = users_collection.find_one({
        "email": str(user.email)
    })

    if existing:
        raise HTTPException(
            status_code=400,
            detail="Email already exists"
        )

    # Generate Employee ID
    first_letter = user.name.strip()[0].upper()

    last_employee = users_collection.find_one(
        {
            "employee_id": {
                "$regex": f"^{first_letter}[0-9]+$"
            }
        },
        sort=[
            ("employee_number", -1)
        ]
    )

    if last_employee and last_employee.get("employee_number"):
        next_number = last_employee["employee_number"] + 1
    else:
        next_number = 1

    employee_id = f"{first_letter}{next_number:03d}"

    # Prepare data
    data = user.model_dump()

    # Hash password
    data["password"] = password_hash.hash(user.password)

    data["employee_id"] = employee_id
    data["employee_number"] = next_number
    data["created_at"] = datetime.utcnow()
    data["updated_at"] = datetime.utcnow()

    # Insert
    result = users_collection.insert_one(data)

    return {
        "status": True,
        "message": "User Created Successfully",
        "user_id": str(result.inserted_id),
        "employee_id": employee_id
    }


@router.get("/get")
def get_users():

    users = list(
        users_collection.aggregate([

            # =====================================
            # Department Lookup
            # =====================================

            {
                "$lookup": {
                    "from": "masters",
                    "let": {
                        "department_id": "$department"
                    },
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {
                                            "$eq": [
                                                "$master_type",
                                                "Department"
                                            ]
                                        },
                                        {
                                            "$eq": [
                                                "$_id",
                                                {
                                                    "$convert": {
                                                        "input": "$$department_id",
                                                        "to": "objectId",
                                                        "onError": None,
                                                        "onNull": None
                                                    }
                                                }
                                            ]
                                        }
                                    ]
                                }
                            }
                        }
                    ],
                    "as": "department_data"
                }
            },

            # =====================================
            # Designation Lookup
            # =====================================

            {
                "$lookup": {
                    "from": "masters",
                    "let": {
                        "designation_id": "$designation"
                    },
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {
                                            "$eq": [
                                                "$master_type",
                                                "Designation"
                                            ]
                                        },
                                        {
                                            "$eq": [
                                                "$_id",
                                                {
                                                    "$convert": {
                                                        "input": "$$designation_id",
                                                        "to": "objectId",
                                                        "onError": None,
                                                        "onNull": None
                                                    }
                                                }
                                            ]
                                        }
                                    ]
                                }
                            }
                        }
                    ],
                    "as": "designation_data"
                }
            },

            # =====================================
            # Branch Lookup
            # =====================================

            {
                "$lookup": {
                    "from": "branches",
                    "let": {
                        "branch_id": "$branch"
                    },
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$eq": [
                                        "$_id",
                                        {
                                            "$convert": {
                                                "input": "$$branch_id",
                                                "to": "objectId",
                                                "onError": None,
                                                "onNull": None
                                            }
                                        }
                                    ]
                                }
                            }
                        }
                    ],
                    "as": "branch_data"
                }
            },

            # =====================================
            # Sort
            # =====================================

            {
                "$sort": {
                    "created_at": -1
                }
            }
        ])
    )

    data = []

    for user in users:

        department = (
            user["department_data"][0]
            if user.get("department_data")
            else None
        )

        designation = (
            user["designation_data"][0]
            if user.get("designation_data")
            else None
        )

        branch = (
            user["branch_data"][0]
            if user.get("branch_data")
            else None
        )

        data.append({

            "id": str(user["_id"]),

            # =================================
            # Basic Details
            # =================================

            "name": user.get("name"),
            "email": user.get("email"),
            "mobile": user.get("mobile"),
            "profile_photo": user.get("profile_photo"),

            # =================================
            # Personal Details
            # =================================

            "gender": user.get("gender"),
            "date_of_birth": user.get("date_of_birth"),

            # =================================
            # Address
            # =================================

            "address": user.get("address"),
            "city": user.get("city"),
            "state": user.get("state"),
            "pincode": user.get("pincode"),

            # =================================
            # Job Details
            # =================================

            "employee_id": user.get("employee_id"),

            "department": {
                "id": user.get("department"),
                "name": department.get("name")
                if department else None
            },

            "designation": {
                "id": user.get("designation"),
                "name": designation.get("name")
                if designation else None
            },

            "joining_date": user.get("joining_date"),
            "employment_type": user.get("employment_type"),

            "branch": {
                "id": user.get("branch"),
                "name": branch.get("name")
                if branch else None,
                "branch_code": branch.get("branch_code")
                if branch else None
            },

            # =================================
            # Salary
            # =================================

            "salary": user.get("salary"),
            "salary_type": user.get("salary_type"),

            # =================================
            # Bank
            # =================================

            "bank_name": user.get("bank_name"),
            "account_number": user.get("account_number"),
            "ifsc_code": user.get("ifsc_code"),

            # =================================
            # Documents
            # =================================

            "aadhaar_document": user.get(
                "aadhaar_document"
            ),
            "pan_document": user.get(
                "pan_document"
            ),
            "driving_license_document": user.get(
                "driving_license_document"
            ),

            "aadhaar_number": user.get(
                "aadhaar_number"
            ),
            "pan_number": user.get(
                "pan_number"
            ),
            "driving_license_number": user.get(
                "driving_license_number"
            ),

            # =================================
            # Emergency
            # =================================

            "emergency_contact": user.get(
                "emergency_contact"
            ),

            # =================================
            # Access
            # =================================

            "role": user.get("role"),
            "status": user.get("status"),

            # =================================
            # Dates
            # =================================

            "created_at": user.get("created_at"),
            "updated_at": user.get("updated_at")
        })

    return {
        "status": True,
        "count": len(data),
        "data": data
    }


@router.get("/get-one")
def get_user(
    email: str | None = Query(None),
    mobile: str | None = Query(None),
    employee_id: str | None = Query(None),
    user_id: str | None = Query(None)
):

    # -----------------------------------
    # At least one parameter required
    # -----------------------------------

    if not any([email, mobile, employee_id, user_id]):
        raise HTTPException(
            status_code=400,
            detail="Please provide email, mobile, employee_id or user_id"
        )

    # -----------------------------------
    # Build MongoDB Query
    # -----------------------------------

    query = {}

    if email:
        query["email"] = email

    elif mobile:
        query["mobile"] = mobile

    elif employee_id:
        query["employee_id"] = employee_id

    elif user_id:

        if not ObjectId.is_valid(user_id):
            raise HTTPException(
                status_code=400,
                detail="Invalid MongoDB user_id"
            )

        query["_id"] = ObjectId(user_id)

    # -----------------------------------
    # Get User + Department
    # + Designation + Branch
    # -----------------------------------

    users = list(
        users_collection.aggregate([

            # Match User
            {
                "$match": query
            },

            # =================================
            # Department
            # =================================

            {
                "$lookup": {
                    "from": "masters",
                    "let": {
                        "department_id": "$department"
                    },
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {
                                            "$eq": [
                                                "$master_type",
                                                "Department"
                                            ]
                                        },
                                        {
                                            "$eq": [
                                                "$_id",
                                                {
                                                    "$convert": {
                                                        "input": "$$department_id",
                                                        "to": "objectId",
                                                        "onError": None,
                                                        "onNull": None
                                                    }
                                                }
                                            ]
                                        }
                                    ]
                                }
                            }
                        }
                    ],
                    "as": "department_data"
                }
            },

            # =================================
            # Designation
            # =================================

            {
                "$lookup": {
                    "from": "masters",
                    "let": {
                        "designation_id": "$designation"
                    },
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {
                                            "$eq": [
                                                "$master_type",
                                                "Designation"
                                            ]
                                        },
                                        {
                                            "$eq": [
                                                "$_id",
                                                {
                                                    "$convert": {
                                                        "input": "$$designation_id",
                                                        "to": "objectId",
                                                        "onError": None,
                                                        "onNull": None
                                                    }
                                                }
                                            ]
                                        }
                                    ]
                                }
                            }
                        }
                    ],
                    "as": "designation_data"
                }
            },

            # =================================
            # Branch
            # =================================

            {
                "$lookup": {
                    "from": "branches",
                    "let": {
                        "branch_id": "$branch"
                    },
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$eq": [
                                        "$_id",
                                        {
                                            "$convert": {
                                                "input": "$$branch_id",
                                                "to": "objectId",
                                                "onError": None,
                                                "onNull": None
                                            }
                                        }
                                    ]
                                }
                            }
                        }
                    ],
                    "as": "branch_data"
                }
            }

        ])
    )

    # -----------------------------------
    # User Not Found
    # -----------------------------------

    if not users:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )

    user = users[0]

    # -----------------------------------
    # Related Data
    # -----------------------------------

    department = (
        user["department_data"][0]
        if user.get("department_data")
        else None
    )

    designation = (
        user["designation_data"][0]
        if user.get("designation_data")
        else None
    )

    branch = (
        user["branch_data"][0]
        if user.get("branch_data")
        else None
    )

    # -----------------------------------
    # Response
    # -----------------------------------

    data = {

        "id": str(user["_id"]),

        # Basic Details
        "name": user.get("name"),
        "email": user.get("email"),
        "mobile": user.get("mobile"),
        "profile_photo": user.get("profile_photo"),

        # Personal Details
        "gender": user.get("gender"),
        "date_of_birth": user.get("date_of_birth"),

        # Address
        "address": user.get("address"),
        "city": user.get("city"),
        "state": user.get("state"),
        "pincode": user.get("pincode"),

        # Job Details
        "employee_id": user.get("employee_id"),

        "department": {
            "id": user.get("department"),
            "name": department.get("name")
            if department else None
        },

        "designation": {
            "id": user.get("designation"),
            "name": designation.get("name")
            if designation else None
        },

        "joining_date": user.get("joining_date"),
        "employment_type": user.get("employment_type"),

        "branch": {
            "id": user.get("branch"),
            "name": branch.get("name")
            if branch else None,
            "branch_code": branch.get("branch_code")
            if branch else None
        },

        # Salary
        "salary": user.get("salary"),
        "salary_type": user.get("salary_type"),

        # Bank Details
        "bank_name": user.get("bank_name"),
        "account_number": user.get("account_number"),
        "ifsc_code": user.get("ifsc_code"),

        # Documents
        "aadhaar_document": user.get(
            "aadhaar_document"
        ),
        "pan_document": user.get(
            "pan_document"
        ),
        "driving_license_document": user.get(
            "driving_license_document"
        ),

        "aadhaar_number": user.get(
            "aadhaar_number"
        ),
        "pan_number": user.get(
            "pan_number"
        ),
        "driving_license_number": user.get(
            "driving_license_number"
        ),

        # Emergency
        "emergency_contact": user.get(
            "emergency_contact"
        ),

        # Access
        "role": user.get("role"),
        "status": user.get("status"),

        # Dates
        "created_at": user.get("created_at"),
        "updated_at": user.get("updated_at")
    }

    return {
        "status": True,
        "data": data
    }



class UserUpdate(BaseModel):
    name: str
    email: EmailStr
    mobile: str

    password: str | None = None

    profile_photo: str | None = None

    gender: str | None = None
    date_of_birth: str | None = None

    address: str | None = None
    city: str | None = None
    state: str | None = None
    pincode: str | None = None

    department: str | None = None
    designation: str | None = None
    joining_date: str | None = None
    employment_type: str | None = None
    branch: str | None = None

    salary: float | None = None
    salary_type: str | None = None

    bank_name: str | None = None
    account_number: str | None = None
    ifsc_code: str | None = None

    aadhaar_document: str | None = None
    pan_document: str | None = None
    driving_license_document: str | None = None

    aadhaar_number: str | None = None
    pan_number: str | None = None
    driving_license_number: str | None = None

    emergency_contact: str | None = None

    role: str | None = "staff"
    status: str | None = "active"

@router.post("/update/{user_id}")
def update_user(user_id: str, user: UserUpdate):

    # -----------------------------------
    # Validate MongoDB ObjectId
    # -----------------------------------

    if not ObjectId.is_valid(user_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid user_id"
        )

    object_id = ObjectId(user_id)

    # -----------------------------------
    # Check User Exists
    # -----------------------------------

    existing_user = users_collection.find_one({
        "_id": object_id
    })

    if not existing_user:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )

    # -----------------------------------
    # Check Duplicate Email
    # -----------------------------------

    duplicate_email = users_collection.find_one({
        "email": str(user.email),
        "_id": {
            "$ne": object_id
        }
    })

    if duplicate_email:
        raise HTTPException(
            status_code=400,
            detail="Email already exists"
        )

    # -----------------------------------
    # Prepare Update Data
    # -----------------------------------

    update_data = user.model_dump(
        exclude_unset=True
    )

    # -----------------------------------
    # Password
    #
    # Empty password = old password
    # New password = hash new password
    # -----------------------------------

    if user.password and user.password.strip():

        update_data["password"] = password_hash.hash(
            user.password
        )

    else:

        # Keep old password
        update_data["password"] = existing_user.get(
            "password"
        )

    # -----------------------------------
    # Keep Employee ID
    # -----------------------------------

    update_data["employee_id"] = existing_user.get(
        "employee_id"
    )

    update_data["employee_number"] = existing_user.get(
        "employee_number"
    )

    # -----------------------------------
    # Updated At
    # -----------------------------------

    update_data["updated_at"] = datetime.utcnow()

    # -----------------------------------
    # Update MongoDB
    # -----------------------------------

    result = users_collection.update_one(
        {
            "_id": object_id
        },
        {
            "$set": update_data
        }
    )

    # -----------------------------------
    # Response
    # -----------------------------------

    return {
        "status": True,
        "message": "User Updated Successfully",
        "user_id": user_id,
        "employee_id": existing_user.get(
            "employee_id"
        )
    }