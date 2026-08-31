from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from bson import ObjectId

from database import product_categories_collection, product_sub_categories_collection, product_brands_collection

router = APIRouter()


class ProductCategory(BaseModel):
    name: str


# =========================================
# CREATE CATEGORY
# POST /category/v1
# =========================================

@router.post("/category/v1")
def create_category(category: ProductCategory):

    existing = product_categories_collection.find_one({
        "name": category.name
    })

    if existing:
        raise HTTPException(
            status_code=400,
            detail="Category already exists"
        )

    data = {
        "name": category.name
    }

    result = product_categories_collection.insert_one(data)

    return {
        "status": True,
        "message": "Category Created Successfully",
        "category_id": str(result.inserted_id)
    }


# =========================================
# UPDATE CATEGORY
# POST /category/update/{category_id}
# =========================================

@router.post("/category/update/{category_id}")
def update_category(
    category_id: str,
    category: ProductCategory
):

    if not ObjectId.is_valid(category_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid category_id"
        )

    object_id = ObjectId(category_id)

    existing = product_categories_collection.find_one({
        "_id": object_id
    })

    if not existing:
        raise HTTPException(
            status_code=404,
            detail="Category not found"
        )

    duplicate = product_categories_collection.find_one({
        "name": category.name,
        "_id": {"$ne": object_id}
    })

    if duplicate:
        raise HTTPException(
            status_code=400,
            detail="Category already exists"
        )

    product_categories_collection.update_one(
        {"_id": object_id},
        {
            "$set": {
                "name": category.name
            }
        }
    )

    return {
        "status": True,
        "message": "Category Updated Successfully",
        "category_id": category_id
    }


# =========================================
# DELETE CATEGORY
# POST /category/delete/{category_id}
# =========================================

@router.post("/category/delete/{category_id}")
def delete_category(category_id: str):

    if not ObjectId.is_valid(category_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid category_id"
        )

    object_id = ObjectId(category_id)

    existing = product_categories_collection.find_one({
        "_id": object_id
    })

    if not existing:
        raise HTTPException(
            status_code=404,
            detail="Category not found"
        )

    result = product_categories_collection.delete_one({
        "_id": object_id
    })

    if result.deleted_count == 0:
        raise HTTPException(
            status_code=500,
            detail="Failed to delete category"
        )

    return {
        "status": True,
        "message": "Category Deleted Successfully",
        "category_id": category_id
    }


# =========================================
# GET CATEGORY LIST
# GET /category/v1
# =========================================

@router.get("/category/v1")
def get_categories():

    categories = list(
        product_categories_collection.find().sort("name", 1)
    )

    data = []

    for category in categories:
        data.append({
            "id": str(category["_id"]),
            "name": category.get("name")
        })

    return {
        "status": True,
        "count": len(data),
        "data": data
    }


# =========================================
# SUB-CATEGORY
# =========================================

class ProductSubCategory(BaseModel):
    name: str
    category_id: str


# =========================================
# CREATE SUB-CATEGORY
# POST /sub-category/v1
# =========================================

@router.post("/sub-category/v1")
def create_sub_category(sub_category: ProductSubCategory):

    if not ObjectId.is_valid(sub_category.category_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid category_id"
        )

    category_id = ObjectId(sub_category.category_id)

    category = product_categories_collection.find_one({
        "_id": category_id
    })

    if not category:
        raise HTTPException(
            status_code=404,
            detail="Category not found"
        )

    existing = product_sub_categories_collection.find_one({
        "name": sub_category.name,
        "category_id": category_id
    })

    if existing:
        raise HTTPException(
            status_code=400,
            detail="Sub-category already exists in this category"
        )

    data = {
        "name": sub_category.name,
        "category_id": category_id
    }

    result = product_sub_categories_collection.insert_one(data)

    return {
        "status": True,
        "message": "Sub-category Created Successfully",
        "sub_category_id": str(result.inserted_id)
    }


# =========================================
# UPDATE SUB-CATEGORY
# POST /sub-category/update/{sub_category_id}
# =========================================

@router.post("/sub-category/update/{sub_category_id}")
def update_sub_category(
    sub_category_id: str,
    sub_category: ProductSubCategory
):

    if not ObjectId.is_valid(sub_category_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid sub_category_id"
        )

    if not ObjectId.is_valid(sub_category.category_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid category_id"
        )

    sub_object_id = ObjectId(sub_category_id)
    category_object_id = ObjectId(sub_category.category_id)

    existing = product_sub_categories_collection.find_one({
        "_id": sub_object_id
    })

    if not existing:
        raise HTTPException(
            status_code=404,
            detail="Sub-category not found"
        )

    category = product_categories_collection.find_one({
        "_id": category_object_id
    })

    if not category:
        raise HTTPException(
            status_code=404,
            detail="Category not found"
        )

    duplicate = product_sub_categories_collection.find_one({
        "name": sub_category.name,
        "category_id": category_object_id,
        "_id": {
            "$ne": sub_object_id
        }
    })

    if duplicate:
        raise HTTPException(
            status_code=400,
            detail="Sub-category already exists in this category"
        )

    product_sub_categories_collection.update_one(
        {
            "_id": sub_object_id
        },
        {
            "$set": {
                "name": sub_category.name,
                "category_id": category_object_id
            }
        }
    )

    return {
        "status": True,
        "message": "Sub-category Updated Successfully",
        "sub_category_id": sub_category_id
    }


# =========================================
# DELETE SUB-CATEGORY
# POST /sub-category/delete/{sub_category_id}
# =========================================

@router.post("/sub-category/delete/{sub_category_id}")
def delete_sub_category(sub_category_id: str):

    if not ObjectId.is_valid(sub_category_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid sub_category_id"
        )

    object_id = ObjectId(sub_category_id)

    existing = product_sub_categories_collection.find_one({
        "_id": object_id
    })

    if not existing:
        raise HTTPException(
            status_code=404,
            detail="Sub-category not found"
        )

    result = product_sub_categories_collection.delete_one({
        "_id": object_id
    })

    if result.deleted_count == 0:
        raise HTTPException(
            status_code=500,
            detail="Failed to delete sub-category"
        )

    return {
        "status": True,
        "message": "Sub-category Deleted Successfully",
        "sub_category_id": sub_category_id
    }


# =========================================
# GET SUB-CATEGORY LIST
# GET /sub-category/v1
# =========================================

@router.get("/sub-category/v1")
def get_sub_categories(
    category_id: str | None = None
):

    query = {}

    if category_id:

        if not ObjectId.is_valid(category_id):
            raise HTTPException(
                status_code=400,
                detail="Invalid category_id"
            )

        query["category_id"] = ObjectId(category_id)

    sub_categories = list(
        product_sub_categories_collection
        .find(query)
        .sort("name", 1)
    )

    data = []

    for sub_category in sub_categories:

        data.append({
            "id": str(sub_category["_id"]),
            "name": sub_category.get("name"),
            "category_id": str(
                sub_category["category_id"]
            )
        })

    return {
        "status": True,
        "count": len(data),
        "data": data
    }


# =========================================
# BRAND
# =========================================

class ProductBrand(BaseModel):
    name: str


# =========================================
# CREATE BRAND
# POST /brand/v1
# =========================================

@router.post("/brand/v1")
def create_brand(brand: ProductBrand):

    existing = product_brands_collection.find_one({
        "name": brand.name
    })

    if existing:
        raise HTTPException(
            status_code=400,
            detail="Brand already exists"
        )

    data = {
        "name": brand.name
    }

    result = product_brands_collection.insert_one(data)

    return {
        "status": True,
        "message": "Brand Created Successfully",
        "brand_id": str(result.inserted_id)
    }


# =========================================
# UPDATE BRAND
# POST /brand/update/{brand_id}
# =========================================

@router.post("/brand/update/{brand_id}")
def update_brand(
    brand_id: str,
    brand: ProductBrand
):

    if not ObjectId.is_valid(brand_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid brand_id"
        )

    object_id = ObjectId(brand_id)

    existing = product_brands_collection.find_one({
        "_id": object_id
    })

    if not existing:
        raise HTTPException(
            status_code=404,
            detail="Brand not found"
        )

    duplicate = product_brands_collection.find_one({
        "name": brand.name,
        "_id": {
            "$ne": object_id
        }
    })

    if duplicate:
        raise HTTPException(
            status_code=400,
            detail="Brand already exists"
        )

    product_brands_collection.update_one(
        {
            "_id": object_id
        },
        {
            "$set": {
                "name": brand.name
            }
        }
    )

    return {
        "status": True,
        "message": "Brand Updated Successfully",
        "brand_id": brand_id
    }


# =========================================
# DELETE BRAND
# POST /brand/delete/{brand_id}
# =========================================

@router.post("/brand/delete/{brand_id}")
def delete_brand(brand_id: str):

    if not ObjectId.is_valid(brand_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid brand_id"
        )

    object_id = ObjectId(brand_id)

    existing = product_brands_collection.find_one({
        "_id": object_id
    })

    if not existing:
        raise HTTPException(
            status_code=404,
            detail="Brand not found"
        )

    result = product_brands_collection.delete_one({
        "_id": object_id
    })

    if result.deleted_count == 0:
        raise HTTPException(
            status_code=500,
            detail="Failed to delete brand"
        )

    return {
        "status": True,
        "message": "Brand Deleted Successfully",
        "brand_id": brand_id
    }


# =========================================
# GET BRAND LIST
# GET /brand/v1
# =========================================

@router.get("/brand/v1")
def get_brands():

    brands = list(
        product_brands_collection.find().sort("name", 1)
    )

    data = []

    for brand in brands:

        data.append({
            "id": str(brand["_id"]),
            "name": brand.get("name")
        })

    return {
        "status": True,
        "count": len(data),
        "data": data
    }