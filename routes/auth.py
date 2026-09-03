from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from pydantic import BaseModel
from datetime import datetime, timedelta, timezone
from pwdlib import PasswordHash
from bson import ObjectId
import jwt

from database import (
    users_collection,
    masters_collection,
    branches_collection,
    access_collection,
    user_access_collection
)


router = APIRouter()

password_hash = PasswordHash.recommended()

security = HTTPBearer()


# =========================================================
# JWT CONFIGURATION
# =========================================================

SECRET_KEY = "sadapoorna_secret_key_2026"

ALGORITHM = "HS256"

ACCESS_TOKEN_EXPIRE_MINUTES = 600


# =========================================================
# LOGIN MODEL
# =========================================================

class LoginRequest(BaseModel):
    login: str
    password: str


# =========================================================
# CREATE JWT TOKEN
# =========================================================

def create_access_token(data: dict):

    to_encode = data.copy()

    expire = datetime.now(timezone.utc) + timedelta(
        minutes=ACCESS_TOKEN_EXPIRE_MINUTES
    )

    to_encode["exp"] = expire

    token = jwt.encode(
        to_encode,
        SECRET_KEY,
        algorithm=ALGORITHM
    )

    return token



def get_user_related_data(user):

    department = None
    designation = None
    branch = None
    access = None

    # -----------------------------------
    # Department
    # -----------------------------------

    if user.get("department") and ObjectId.is_valid(
        user["department"]
    ):
        department = masters_collection.find_one({
            "_id": ObjectId(user["department"]),
            "master_type": "Department"
        })

    # -----------------------------------
    # Designation
    # -----------------------------------

    if user.get("designation") and ObjectId.is_valid(
        user["designation"]
    ):
        designation = masters_collection.find_one({
            "_id": ObjectId(user["designation"]),
            "master_type": "Designation"
        })

        # Access based on designation
        access = access_collection.find_one({
            "designation_id": user["designation"]
        })

    # -----------------------------------
    # Branch
    # -----------------------------------

    if user.get("branch") and ObjectId.is_valid(
        user["branch"]
    ):
        branch = branches_collection.find_one({
            "_id": ObjectId(user["branch"])
        })

    # -----------------------------------
    # Return
    # -----------------------------------

    return {

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

        "branch": {
            "id": user.get("branch"),
            "name": branch.get("name")
            if branch else None,
            "branch_code": branch.get("branch_code")
            if branch else None
        },

        "access": {
            "id": str(access["_id"])
            if access else None,

            "designation_id": (
                access.get("designation_id")
                if access
                else user.get("designation")
            ),

            "frontend_icons": (
                access.get("frontend_icons", [])
                if access
                else []
            )
        }
    }


def get_access_tree(user_id):
    user = users_collection.find_one({
        "_id": ObjectId(user_id)
    })

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )

    visited = set()

    def build_tree(current_user_id):

        # -----------------------------------
        # Prevent circular hierarchy
        # -----------------------------------

        if current_user_id in visited:
            return []

        visited.add(current_user_id)

        mapping = user_access_collection.find_one({
            "manager_id": current_user_id
        })

        if not mapping:
            return []

        result = []

        for subordinate_id in mapping.get(
            "subordinate_ids",
            []
        ):

            subordinate = users_collection.find_one({
                "_id": ObjectId(subordinate_id)
            })

            if not subordinate:
                continue

            children = build_tree(
                subordinate_id
            )

            result.append({

                "id": str(
                    subordinate["_id"]
                ),

                "name": subordinate.get(
                    "name"
                ),

                "email": subordinate.get(
                    "email"
                ),

                "mobile": subordinate.get(
                    "mobile"
                ),

                "employee_id": subordinate.get(
                    "employee_id"
                ),

                "designation": subordinate.get(
                    "designation"
                ),

                "branch": subordinate.get(
                    "branch"
                ),

                "children": children
            })

        return result

    tree = build_tree(user_id)

    return {
        "status": True,
        "user_id": user_id,
        "access": tree
    }


# =========================================================
# LOGIN
# =========================================================

@router.post("/login")
def login_user(login_data: LoginRequest):

    login = login_data.login.strip()

    # -----------------------------------
    # Find user
    # -----------------------------------

    user = users_collection.find_one({
        "$or": [
            {
                "email": login
            },
            {
                "mobile": login
            },
            {
                "employee_id": login.upper()
            }
        ]
    })

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Invalid login credentials"
        )

    # -----------------------------------
    # Check status
    # -----------------------------------

    if user.get("status") != "active":
        raise HTTPException(
            status_code=403,
            detail="User account is inactive"
        )

    # -----------------------------------
    # Verify password
    # -----------------------------------

    stored_password = user.get("password")

    if not stored_password:
        raise HTTPException(
            status_code=401,
            detail="Password not configured"
        )

    try:
        password_valid = password_hash.verify(
            login_data.password,
            stored_password
        )
    except Exception:
        password_valid = False

    if not password_valid:
        raise HTTPException(
            status_code=401,
            detail="Invalid login credentials"
        )

    # -----------------------------------
    # Related Data
    # -----------------------------------

    related = get_user_related_data(user)

    # -----------------------------------
    # Create JWT
    # -----------------------------------

    token = create_access_token({

        "user_id": str(user["_id"]),

        "employee_id": user.get(
            "employee_id"
        ),

        "role": user.get(
            "role",
            "staff"
        )
    })

    user_access_tree = get_access_tree(str(user["_id"]))

    # -----------------------------------
    # Response
    # -----------------------------------

    return {
        "status": True,
        "message": "Login Successfully",

        "access_token": token,
        "token_type": "bearer",

        "expires_in":
            ACCESS_TOKEN_EXPIRE_MINUTES * 60,

        "user": {

            "id": str(user["_id"]),

            "name": user.get("name"),

            "email": user.get("email"),

            "mobile": user.get("mobile"),

            "access": related["access"],

            "profile_photo": user.get(
                "profile_photo"
            ),

            "employee_id": user.get(
                "employee_id"
            ),

            "department": related[
                "department"
            ],

            "designation": related[
                "designation"
            ],

            "access_tree": user_access_tree,
            

            "branch": related[
                "branch"
            ],

            "role": user.get("role"),

            "status": user.get("status")
        }
    }

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

        # Validate ObjectId
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
# GET CURRENT USER PROFILE
# =========================================================

@router.get("/profile")
def get_profile(
    current_user: dict = Depends(get_current_user)
):

    user_id = current_user.get("user_id")

    if not ObjectId.is_valid(user_id):
        raise HTTPException(
            status_code=401,
            detail="Invalid user ID"
        )

    user = users_collection.find_one({
        "_id": ObjectId(user_id)
    })

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )

    # -----------------------------------
    # Related Data
    # -----------------------------------

    related = get_user_related_data(user)

    user_access_tree = get_access_tree(str(user["_id"]))

    # -----------------------------------
    # Response
    # -----------------------------------

    return {

        "status": True,

        "data": {

            "id": str(user["_id"]),

            "name": user.get(
                "name"
            ),

            "email": user.get(
                "email"
            ),

            "mobile": user.get(
                "mobile"
            ),

            "profile_photo": user.get(
                "profile_photo"
            ),

            "access": related["access"],

            "employee_id": user.get(
                "employee_id"
            ),

            "department": related[
                "department"
            ],

            "designation": related[
                "designation"
            ],

            "access_tree": user_access_tree,

            "branch": related[
                "branch"
            ],

            "role": user.get(
                "role"
            ),

            "status": user.get(
                "status"
            ),

            "created_at": user.get(
                "created_at"
            ),

            "updated_at": user.get(
                "updated_at"
            )
        }
    }