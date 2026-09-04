from fastapi import FastAPI
from routes.auth import router
from routes.users import router as users
from routes.masters import router as masters
from routes.branch import router as branches
from routes.access import router as access
from routes.data_access_hierarchy import router as data_access_hierarchy
from routes.customer import router as customer
from routes.whatsapp import router as whatsapp
from routes.product_unit import router as product_units
from routes.category import router as category
from routes.create_product import router as create_product_router
from routes.packaging_type import router as packaging_type_router
from routes.warehouses import router as warehouses
from routes.vehicles import router as vehicles
from routes.vendor import router as vendors
from routes.orders import router as orders
from routes.Inventory import router as inventory
from routes.whatsapp_webhook import router as whatsapp_router
from routes.beat import router as beat_router


app = FastAPI()

app.include_router(router, prefix="/auth", tags=["Auth"])
app.include_router(users, prefix="/users", tags=["Users"])
app.include_router(masters, prefix="/masters", tags=["Masters"])
app.include_router(branches, prefix="/branches", tags=["Branches"])
app.include_router(access, prefix="/access", tags=["Access"])
app.include_router(data_access_hierarchy, prefix="/data-access-hierarchy", tags=["Data Access Hierarchy"])
app.include_router(customer, prefix="/customer", tags=["Customer"])
app.include_router(whatsapp,prefix="/whatsapp",tags=["WhatsApp"])
app.include_router(product_units, prefix="/product-units", tags=["Product Units"])
app.include_router(category, prefix="/attributes", tags=["Attributes"])
app.include_router(create_product_router, prefix="/products", tags=["Products"])
app.include_router(packaging_type_router, prefix="/packing-types", tags=["Packing Types"])
app.include_router(warehouses, prefix="/warehouses", tags=["Warehouses"])
app.include_router(vehicles, prefix="/vehicles", tags=["Vehicles"])
app.include_router(vendors, prefix="/vendors", tags=["Vendors"])
app.include_router(orders, prefix="/orders", tags=["Orders"])
app.include_router(inventory, prefix="/inventory", tags=["Inventory"])
app.include_router(whatsapp_router, prefix="/whatsapp-webhook", tags=["WhatsApp Webhook"])
app.include_router(beat_router, prefix="/beats", tags=["Beat Management"])


@app.get("/")
def home():
    return {
        "message": "FastAPI Working done"
    }


# uvicorn main:app --host 0.0.0.0 --port 8000 --reload