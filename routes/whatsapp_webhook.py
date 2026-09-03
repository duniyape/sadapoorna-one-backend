from fastapi import (
    APIRouter,
    Request,
    Query,
    HTTPException,
    UploadFile,
    File,
    Form
)

from fastapi.responses import PlainTextResponse, StreamingResponse

from pydantic import BaseModel

from datetime import datetime, timezone

from bson import ObjectId

from database import (
    customers_collection,
    whatsapp_chats_collection,
    whatsapp_messages_collection
)

import requests
import os
import hashlib
import hmac
import io
import logging


router = APIRouter(
    prefix="/whatsapp",
    tags=["WhatsApp"]
)

logger = logging.getLogger(__name__)


# =========================================================
# CONFIG
# =========================================================

ACCESS_TOKEN = "EAAOOnx48PYMBSQbuqU4W5KiPRXPcqOSZBMSireK9JWcVRwdrZCz12BsvicEwWPrdJLymGalBeof6ZAIRetjWnTYtBoQ3k5ckAuK5O5RnHd7V7Ud4hOJikxPmvu0GCC8ZBq3FJ6JykZB1sKvg3bwFoivGKc6cWZCR81YPEoXLCOcZBdWcJnDA4yPZCvu2A3ZAP6ZAsy5cImZCumL3d3WKUYnLhf5yokbZB6pwt3Wv2UVglZBEUv536GBt1CE9b0MUZBaVWcwiftfCwQuGXrZA0dA6ABCVIJoGEsvh2NZCDJzDH1rcZBgZDZD"

PHONE_NUMBER_ID = "593779133824503"

VERIFY_TOKEN = "sadapoorna_one"

APP_SECRET = "1c4401e6371c76ce63f7b6b6eef2fe7c"

GRAPH_VERSION = "v26.0"

WHATSAPP_URL = (
    f"https://graph.facebook.com/"
    f"{GRAPH_VERSION}/"
    f"{PHONE_NUMBER_ID}/messages"
)

GRAPH_URL = (
    f"https://graph.facebook.com/"
    f"{GRAPH_VERSION}"
)


# =========================================================
# HELPERS
# =========================================================

def utc_now():
    return datetime.now(timezone.utc)


def normalize_phone(phone: str):

    phone = (
        str(phone)
        .strip()
        .replace("+", "")
        .replace(" ", "")
        .replace("-", "")
    )

    if phone.startswith("0"):
        phone = phone[1:]

    if len(phone) == 10:
        phone = "91" + phone

    return phone


def object_id(value):

    try:
        return ObjectId(value)

    except Exception:
        return None


# =========================================================
# CUSTOMER FIND
# =========================================================

def find_customer_by_phone(phone: str):

    phone = normalize_phone(phone)

    last10 = phone[-10:]

    customer = customers_collection.find_one({
        "$or": [
            {"mobile": phone},
            {"mobile": f"+{phone}"},
            {"mobile": last10},
            {"mobile": f"+91{last10}"},
            {"phone": phone},
            {"phone": f"+{phone}"},
            {"phone": last10}
        ]
    })

    return customer


# =========================================================
# GET / CREATE CHAT
# =========================================================

def get_or_create_chat(
    phone: str,
    customer=None,
    customer_name=None
):

    phone = normalize_phone(phone)

    chat = whatsapp_chats_collection.find_one({
        "phone": phone
    })

    now = utc_now()

    if chat:
        return chat

    customer_id = None

    if customer:
        customer_id = customer.get("id")

        if not customer_name:
            customer_name = (
                customer.get("name")
                or customer.get("customer_name")
                or customer.get("full_name")
            )

    chat_data = {
        "phone": phone,
        "customer_id": customer_id,
        "customer_name": customer_name,
        "last_message": "",
        "last_message_type": None,
        "last_message_at": None,
        "unread_count": 0,
        "created_at": now,
        "updated_at": now
    }

    result = whatsapp_chats_collection.insert_one(
        chat_data
    )

    chat_data["_id"] = result.inserted_id

    return chat_data


# =========================================================
# UPDATE CHAT
# =========================================================

def update_chat(
    chat_id,
    message,
    message_type,
    incoming=False
):

    now = utc_now()

    update = {
        "$set": {
            "last_message": message or "",
            "last_message_type": message_type,
            "last_message_at": now,
            "updated_at": now
        }
    }

    if incoming:

        update["$inc"] = {
            "unread_count": 1
        }

    whatsapp_chats_collection.update_one(
        {
            "_id": chat_id
        },
        update
    )


# =========================================================
# SAVE MESSAGE
# =========================================================

def save_message(
    chat,
    phone,
    message_id,
    direction,
    message_type,
    text=None,
    media=None,
    status="received",
    timestamp=None
):

    now = utc_now()

    message = {
        "chat_id": chat["_id"],
        "customer_id": chat.get("customer_id"),
        "phone": phone,

        "message_id": message_id,

        "direction": direction,

        "type": message_type,

        "text": text,

        "media": media or {},

        "status": status,

        "timestamp": timestamp or now,

        "created_at": now,
        "updated_at": now
    }

    result = whatsapp_messages_collection.insert_one(
        message
    )

    message["_id"] = result.inserted_id

    return message


# =========================================================
# WHATSAPP WEBHOOK VERIFY
# =========================================================

@router.get(
    "/webhook",
    response_class=PlainTextResponse
)
async def whatsapp_webhook_verify(

    hub_mode: str = Query(
        None,
        alias="hub.mode"
    ),

    hub_verify_token: str = Query(
        None,
        alias="hub.verify_token"
    ),

    hub_challenge: str = Query(
        None,
        alias="hub.challenge"
    )
):

    if hub_mode != "subscribe":

        raise HTTPException(
            status_code=400,
            detail="Invalid hub.mode"
        )

    if hub_verify_token != VERIFY_TOKEN:

        raise HTTPException(
            status_code=403,
            detail="Invalid verify token"
        )

    return hub_challenge


# =========================================================
# WEBHOOK SIGNATURE VALIDATION
# =========================================================

async def validate_webhook_signature(
    request: Request,
    body: bytes
):

    if not APP_SECRET:
        return True

    signature = request.headers.get(
        "X-Hub-Signature-256"
    )

    if not signature:
        return False

    expected = hmac.new(
        APP_SECRET.encode(),
        body,
        hashlib.sha256
    ).hexdigest()

    expected_signature = (
        "sha256=" + expected
    )

    return hmac.compare_digest(
        signature,
        expected_signature
    )


# =========================================================
# RECEIVE WEBHOOK
# =========================================================

@router.post("/webhook")
async def whatsapp_webhook(
    request: Request
):

    body = await request.body()

    valid = await validate_webhook_signature(
        request,
        body
    )

    if not valid:

        raise HTTPException(
            status_code=403,
            detail="Invalid webhook signature"
        )

    try:

        data = await request.json()

    except Exception:

        return {
            "status": True
        }

    if data.get("object") != "whatsapp_business_account":

        return {
            "status": True
        }

    for entry in data.get("entry", []):

        for change in entry.get(
            "changes",
            []
        ):

            value = change.get(
                "value",
                {}
            )

            # =================================================
            # INCOMING MESSAGES
            # =================================================

            messages = value.get(
                "messages",
                []
            )

            contacts = value.get(
                "contacts",
                []
            )

            for message in messages:

                await process_incoming_message(
                    message,
                    contacts
                )

            # =================================================
            # MESSAGE STATUS
            # =================================================

            statuses = value.get(
                "statuses",
                []
            )

            for status in statuses:

                process_message_status(
                    status
                )

    return {
        "status": True
    }


# =========================================================
# PROCESS INCOMING MESSAGE
# =========================================================

async def process_incoming_message(
    message: dict,
    contacts: list
):

    phone = normalize_phone(
        message.get("from")
    )

    message_id = message.get(
        "id"
    )

    message_type = message.get(
        "type"
    )

    timestamp = message.get(
        "timestamp"
    )

    customer = find_customer_by_phone(
        phone
    )

    customer_name = None

    if contacts:

        customer_name = (
            contacts[0]
            .get("profile", {})
            .get("name")
        )

    chat = get_or_create_chat(
        phone=phone,
        customer=customer,
        customer_name=customer_name
    )

    # =====================================================
    # TEXT
    # =====================================================

    if message_type == "text":

        text = (
            message
            .get("text", {})
            .get("body", "")
        )

        save_message(
            chat=chat,
            phone=phone,
            message_id=message_id,
            direction="incoming",
            message_type="text",
            text=text,
            status="received"
        )

        update_chat(
            chat["_id"],
            text,
            "text",
            incoming=True
        )

    # =====================================================
    # IMAGE
    # =====================================================

    elif message_type == "image":

        image = message.get(
            "image",
            {}
        )

        media_id = image.get(
            "id"
        )

        mime_type = image.get(
            "mime_type"
        )

        caption = image.get(
            "caption"
        )

        media = {
            "media_id": media_id,
            "mime_type": mime_type,
            "filename": None,
            "caption": caption
        }

        save_message(
            chat=chat,
            phone=phone,
            message_id=message_id,
            direction="incoming",
            message_type="image",
            text=caption,
            media=media
        )

        update_chat(
            chat["_id"],
            caption or "Photo",
            "image",
            incoming=True
        )

    # =====================================================
    # DOCUMENT
    # =====================================================

    elif message_type == "document":

        document = message.get(
            "document",
            {}
        )

        media_id = document.get(
            "id"
        )

        mime_type = document.get(
            "mime_type"
        )

        filename = document.get(
            "filename"
        )

        caption = document.get(
            "caption"
        )

        media = {
            "media_id": media_id,
            "mime_type": mime_type,
            "filename": filename,
            "caption": caption
        }

        save_message(
            chat=chat,
            phone=phone,
            message_id=message_id,
            direction="incoming",
            message_type="document",
            text=caption,
            media=media
        )

        update_chat(
            chat["_id"],
            filename or "Document",
            "document",
            incoming=True
        )

    # =====================================================
    # VIDEO
    # =====================================================

    elif message_type == "video":

        video = message.get(
            "video",
            {}
        )

        media = {
            "media_id": video.get("id"),
            "mime_type": video.get("mime_type"),
            "filename": None,
            "caption": video.get("caption")
        }

        save_message(
            chat=chat,
            phone=phone,
            message_id=message_id,
            direction="incoming",
            message_type="video",
            text=video.get("caption"),
            media=media
        )

        update_chat(
            chat["_id"],
            video.get("caption") or "Video",
            "video",
            incoming=True
        )

    # =====================================================
    # AUDIO
    # =====================================================

    elif message_type == "audio":

        audio = message.get(
            "audio",
            {}
        )

        media = {
            "media_id": audio.get("id"),
            "mime_type": audio.get("mime_type"),
            "filename": None,
            "caption": None
        }

        save_message(
            chat=chat,
            phone=phone,
            message_id=message_id,
            direction="incoming",
            message_type="audio",
            media=media
        )

        update_chat(
            chat["_id"],
            "Audio",
            "audio",
            incoming=True
        )

    # =====================================================
    # STICKER
    # =====================================================

    elif message_type == "sticker":

        sticker = message.get(
            "sticker",
            {}
        )

        media = {
            "media_id": sticker.get("id"),
            "mime_type": sticker.get("mime_type")
        }

        save_message(
            chat=chat,
            phone=phone,
            message_id=message_id,
            direction="incoming",
            message_type="sticker",
            media=media
        )

        update_chat(
            chat["_id"],
            "Sticker",
            "sticker",
            incoming=True
        )

    # =====================================================
    # LOCATION
    # =====================================================

    elif message_type == "location":

        location = message.get(
            "location",
            {}
        )

        media = {
            "latitude": location.get(
                "latitude"
            ),
            "longitude": location.get(
                "longitude"
            ),
            "name": location.get(
                "name"
            ),
            "address": location.get(
                "address"
            )
        }

        save_message(
            chat=chat,
            phone=phone,
            message_id=message_id,
            direction="incoming",
            message_type="location",
            media=media
        )

        update_chat(
            chat["_id"],
            "Location",
            "location",
            incoming=True
        )


# =========================================================
# MESSAGE STATUS
# =========================================================

def process_message_status(
    status: dict
):

    message_id = status.get(
        "id"
    )

    status_value = status.get(
        "status"
    )

    if not message_id:
        return

    update_data = {
        "status": status_value,
        "updated_at": utc_now()
    }

    if status_value == "failed":

        update_data["error"] = (
            status.get("errors")
        )

    whatsapp_messages_collection.update_one(
        {
            "message_id": message_id
        },
        {
            "$set": update_data
        }
    )


# =========================================================
# GET CHATS
# =========================================================

@router.get("/chats")
def get_chats(
    page: int = 1,
    limit: int = 30,
    search: str = None
):

    page = max(
        page,
        1
    )

    limit = min(
        max(limit, 1),
        100
    )

    query = {}

    if search:

        query["$or"] = [
            {
                "customer_name": {
                    "$regex": search,
                    "$options": "i"
                }
            },
            {
                "phone": {
                    "$regex": search,
                    "$options": "i"
                }
            }
        ]

    skip = (
        page - 1
    ) * limit

    total = whatsapp_chats_collection.count_documents(
        query
    )

    chats = list(
        whatsapp_chats_collection
        .find(query)
        .sort(
            "last_message_at",
            -1
        )
        .skip(skip)
        .limit(limit)
    )

    result = []

    for chat in chats:

        result.append({
            "id": str(
                chat["_id"]
            ),
            "customer_id": chat.get(
                "customer_id"
            ),
            "customer_name": chat.get(
                "customer_name"
            ),
            "phone": chat.get(
                "phone"
            ),
            "last_message": chat.get(
                "last_message"
            ),
            "last_message_type": chat.get(
                "last_message_type"
            ),
            "last_message_at": chat.get(
                "last_message_at"
            ),
            "unread_count": chat.get(
                "unread_count",
                0
            )
        })

    return {
        "status": True,
        "data": result,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "pages": (
                total + limit - 1
            ) // limit
        }
    }


# =========================================================
# GET CHAT MESSAGES
# =========================================================

@router.get("/chats/{chat_id}/messages")
def get_chat_messages(
    chat_id: str,
    page: int = 1,
    limit: int = 50
):

    chat_object_id = object_id(
        chat_id
    )

    if not chat_object_id:

        raise HTTPException(
            status_code=400,
            detail="Invalid chat id"
        )

    page = max(
        page,
        1
    )

    limit = min(
        max(limit, 1),
        100
    )

    skip = (
        page - 1
    ) * limit

    query = {
        "chat_id": chat_object_id
    }

    total = (
        whatsapp_messages_collection
        .count_documents(query)
    )

    messages = list(
        whatsapp_messages_collection
        .find(query)
        .sort(
            "timestamp",
            1
        )
        .skip(skip)
        .limit(limit)
    )

    result = []

    for message in messages:

        result.append({
            "id": str(
                message["_id"]
            ),

            "message_id": message.get(
                "message_id"
            ),

            "direction": message.get(
                "direction"
            ),

            "type": message.get(
                "type"
            ),

            "text": message.get(
                "text"
            ),

            "media": message.get(
                "media",
                {}
            ),

            "status": message.get(
                "status"
            ),

            "timestamp": message.get(
                "timestamp"
            )
        })

    # Mark chat read
    whatsapp_chats_collection.update_one(
        {
            "_id": chat_object_id
        },
        {
            "$set": {
                "unread_count": 0
            }
        }
    )

    return {
        "status": True,
        "data": result,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total
        }
    }


# =========================================================
# SEND TEXT MESSAGE
# =========================================================

class SendTextRequest(BaseModel):

    phone: str

    text: str


@router.post("/send-text")
def send_text_message(
    data: SendTextRequest
):

    phone = normalize_phone(
        data.phone
    )

    if not data.text.strip():

        raise HTTPException(
            status_code=400,
            detail="Message text is required"
        )

    payload = {

        "messaging_product": "whatsapp",

        "to": phone,

        "type": "text",

        "text": {
            "preview_url": False,
            "body": data.text
        }
    }

    response = requests.post(
        WHATSAPP_URL,
        headers={
            "Authorization":
                f"Bearer {ACCESS_TOKEN}",

            "Content-Type":
                "application/json"
        },
        json=payload,
        timeout=30
    )

    result = response.json()

    if response.status_code >= 400:

        raise HTTPException(
            status_code=400,
            detail=result
        )

    messages = result.get(
        "messages",
        []
    )

    whatsapp_message_id = None

    if messages:

        whatsapp_message_id = (
            messages[0].get("id")
        )

    customer = find_customer_by_phone(
        phone
    )

    chat = get_or_create_chat(
        phone,
        customer
    )

    save_message(
        chat=chat,
        phone=phone,
        message_id=whatsapp_message_id,
        direction="outgoing",
        message_type="text",
        text=data.text,
        status="sent"
    )

    update_chat(
        chat["_id"],
        data.text,
        "text",
        incoming=False
    )

    return {
        "status": True,
        "message": "Message sent successfully",
        "data": {
            "chat_id": str(
                chat["_id"]
            ),
            "message_id":
                whatsapp_message_id,
            "phone": phone,
            "text": data.text,
            "status": "sent"
        }
    }


# =========================================================
# SEND MEDIA
# =========================================================

@router.post("/send-media")
def send_media_message(

    phone: str = Form(...),

    media_type: str = Form(...),

    caption: str = Form(None),

    file: UploadFile = File(...)
):

    phone = normalize_phone(
        phone
    )

    allowed_types = [
        "image",
        "video",
        "audio",
        "document"
    ]

    if media_type not in allowed_types:

        raise HTTPException(
            status_code=400,
            detail=(
                "media_type must be "
                "image, video, audio or document"
            )
        )

    # =====================================================
    # READ FILE
    # =====================================================

    file_data = file.file.read()

    if not file_data:

        raise HTTPException(
            status_code=400,
            detail="Empty file"
        )

    mime_type = (
        file.content_type
        or "application/octet-stream"
    )

    # =====================================================
    # UPLOAD MEDIA TO WHATSAPP
    # =====================================================

    upload_url = (
        f"{GRAPH_URL}/"
        f"{PHONE_NUMBER_ID}/media"
    )

    upload_response = requests.post(

        upload_url,

        headers={
            "Authorization":
                f"Bearer {ACCESS_TOKEN}"
        },

        files={
            "file": (
                file.filename,
                file_data,
                mime_type
            )
        },

        data={
            "messaging_product":
                "whatsapp"
        },

        timeout=60
    )

    upload_result = (
        upload_response.json()
    )

    if upload_response.status_code >= 400:

        raise HTTPException(
            status_code=400,
            detail=upload_result
        )

    media_id = upload_result.get(
        "id"
    )

    # =====================================================
    # CREATE MESSAGE PAYLOAD
    # =====================================================

    media_payload = {
        "id": media_id
    }

    if caption and media_type in [
        "image",
        "video",
        "document"
    ]:

        media_payload["caption"] = caption

    if media_type == "document":

        media_payload["filename"] = (
            file.filename
        )

    payload = {

        "messaging_product":
            "whatsapp",

        "to":
            phone,

        "type":
            media_type,

        media_type:
            media_payload
    }

    send_response = requests.post(

        WHATSAPP_URL,

        headers={
            "Authorization":
                f"Bearer {ACCESS_TOKEN}",

            "Content-Type":
                "application/json"
        },

        json=payload,

        timeout=30
    )

    send_result = (
        send_response.json()
    )

    if send_response.status_code >= 400:

        raise HTTPException(
            status_code=400,
            detail=send_result
        )

    whatsapp_message_id = None

    messages = send_result.get(
        "messages",
        []
    )

    if messages:

        whatsapp_message_id = (
            messages[0].get("id")
        )

    # =====================================================
    # SAVE
    # =====================================================

    customer = find_customer_by_phone(
        phone
    )

    chat = get_or_create_chat(
        phone,
        customer
    )

    media = {

        "media_id":
            media_id,

        "mime_type":
            mime_type,

        "filename":
            file.filename,

        "caption":
            caption
    }

    save_message(

        chat=chat,

        phone=phone,

        message_id=
            whatsapp_message_id,

        direction="outgoing",

        message_type=
            media_type,

        text=caption,

        media=media,

        status="sent"
    )

    last_message = (
        caption
        or file.filename
        or media_type
    )

    update_chat(

        chat["_id"],

        last_message,

        media_type,

        incoming=False
    )

    return {

        "status": True,

        "message":
            "Media sent successfully",

        "data": {

            "chat_id":
                str(chat["_id"]),

            "message_id":
                whatsapp_message_id,

            "media_id":
                media_id,

            "filename":
                file.filename,

            "mime_type":
                mime_type,

            "type":
                media_type,

            "status":
                "sent"
        }
    }


# =========================================================
# GET MEDIA URL
# =========================================================

@router.get("/media/{media_id}")
def get_media(
    media_id: str
):

    media_url_response = requests.get(

        f"{GRAPH_URL}/{media_id}",

        headers={
            "Authorization":
                f"Bearer {ACCESS_TOKEN}"
        },

        timeout=30
    )

    result = (
        media_url_response.json()
    )

    if media_url_response.status_code >= 400:

        raise HTTPException(
            status_code=400,
            detail=result
        )

    return {
        "status": True,
        "data": result
    }


# =========================================================
# DOWNLOAD MEDIA
# =========================================================

@router.get(
    "/media/{media_id}/download"
)
def download_media(
    media_id: str
):

    media_response = requests.get(

        f"{GRAPH_URL}/{media_id}",

        headers={
            "Authorization":
                f"Bearer {ACCESS_TOKEN}"
        },

        timeout=30
    )

    media_info = (
        media_response.json()
    )

    if media_response.status_code >= 400:

        raise HTTPException(
            status_code=400,
            detail=media_info
        )

    download_url = media_info.get(
        "url"
    )

    mime_type = media_info.get(
        "mime_type",
        "application/octet-stream"
    )

    if not download_url:

        raise HTTPException(
            status_code=404,
            detail="Media URL not found"
        )

    file_response = requests.get(

        download_url,

        headers={
            "Authorization":
                f"Bearer {ACCESS_TOKEN}"
        },

        timeout=60
    )

    if file_response.status_code >= 400:

        raise HTTPException(
            status_code=400,
            detail="Failed to download media"
        )

    return StreamingResponse(

        io.BytesIO(
            file_response.content
        ),

        media_type=mime_type,

        headers={
            "Content-Disposition":
                "inline"
        }
    )


# =========================================================
# MARK CHAT READ
# =========================================================

@router.post(
    "/chats/{chat_id}/read"
)
def mark_chat_read(
    chat_id: str
):

    chat_object_id = object_id(
        chat_id
    )

    if not chat_object_id:

        raise HTTPException(
            status_code=400,
            detail="Invalid chat id"
        )

    whatsapp_chats_collection.update_one(

        {
            "_id":
                chat_object_id
        },

        {
            "$set": {
                "unread_count": 0,
                "updated_at":
                    utc_now()
            }
        }
    )

    return {
        "status": True,
        "message": "Chat marked as read"
    }


# =========================================================
# DELETE CHAT
# =========================================================

@router.delete(
    "/chats/{chat_id}"
)
def delete_chat(
    chat_id: str
):

    chat_object_id = object_id(
        chat_id
    )

    if not chat_object_id:

        raise HTTPException(
            status_code=400,
            detail="Invalid chat id"
        )

    whatsapp_chats_collection.delete_one(
        {
            "_id":
                chat_object_id
        }
    )

    whatsapp_messages_collection.delete_many(
        {
            "chat_id":
                chat_object_id
        }
    )

    return {
        "status": True,
        "message": "Chat deleted successfully"
    }