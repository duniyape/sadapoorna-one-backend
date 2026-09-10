import os
import io
import requests
from typing import Dict, Any, Optional, Tuple
from fastapi import HTTPException


def get_whatsapp_config() -> Tuple[str, str, str]:
    token = os.getenv("WHATSAPP_ACCESS_TOKEN", "EAA6rtuUkSgIBOw1ZBKc0daGfX8SSbt86QetCckUtCodtMy2ZA44d9e0nrEUhZAsxaroHpX1217ROdLpkDRD1RwKa0VWMzgy5eMfIBv4WN1CYhXnAfXx7psCzgZB2xJkEZABscWDYYsKRwBHXMnfBdT905ZCLklGOnXS8tCaqsDGpoK7s5XlkOxgh4udFz67qw5aQZDZD")
    phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "670517682822062")
    api_ver = os.getenv("WHATSAPP_API_VERSION", "v22.0")
    return token, phone_id, api_ver


def clean_phone_number(raw_mobile: Optional[str]) -> str:
    """Format mobile number to E.164 without leading plus, default with 91 prefix."""
    digits = "".join(filter(str.isdigit, str(raw_mobile or "919131037870")))
    if not digits.startswith("91"):
        digits = f"91{digits}"
    return digits


def send_invoice_template_whatsapp(order: Dict[str, Any]) -> Dict[str, Any]:
    """
    Send the approved WhatsApp template notification (bill_ready_reply) when an invoice is created.
    """
    token, phone_id, api_ver = get_whatsapp_config()
    if not token or not phone_id:
        raise HTTPException(
            status_code=500,
            detail="WhatsApp configuration (token/phone_number_id) is missing."
        )

    customer = order.get("customer") or {}
    raw_mobile = customer.get("mobile")
    clean_number = clean_phone_number(raw_mobile)

    customer_name = str(
        customer.get("shop_name")
        or customer.get("owner")
        or customer.get("name")
        or "Valued Customer"
    ).strip()
    order_id = str(order.get("order_no") or order.get("id") or "")
    invoice_no = str(order.get("invoice_no") or "")
    grand_total = str(order.get("grand_total") or "0")
    order_obj_id = str(order.get("id") or order.get("_id") or "")

    payload = {
        "messaging_product": "whatsapp",
        "to": clean_number,
        "type": "template",
        "template": {
            "name": "bill_ready_reply",
            "language": {"code": "en"},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": customer_name},  # {{1}}
                        {"type": "text", "text": order_id},        # {{2}}
                        {"type": "text", "text": invoice_no},      # {{3}}
                        {"type": "text", "text": grand_total},     # {{4}}
                        {"type": "text", "text": " "}              # {{5}}
                    ]
                },
                {
                    "type": "button",
                    "sub_type": "quick_reply",
                    "index": "0",
                    "parameters": [
                        {
                            "type": "payload",
                            "payload": f"download_invoice_{order_obj_id}"
                        }
                    ]
                }
            ]
        }
    }

    url = f"https://graph.facebook.com/{api_ver}/{phone_id}/messages"
    try:
        response = requests.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        error_details = e.response.json() if e.response else str(e)
        raise HTTPException(
            status_code=400,
            detail=f"WhatsApp template sending failed: {error_details}"
        )


def send_invoice_pdf_whatsapp(
    pdf_bytes: bytes,
    invoice_no: str,
    recipient_mobile: str
) -> Dict[str, Any]:
    """
    Upload and send the generated PDF document through WhatsApp Cloud API.
    """
    token, phone_id, api_ver = get_whatsapp_config()
    if not token or not phone_id:
        raise HTTPException(
            status_code=500,
            detail="WhatsApp configuration is missing on the backend."
        )

    clean_number = clean_phone_number(recipient_mobile)

    # 1. Upload media
    upload_url = f"https://graph.facebook.com/{api_ver}/{phone_id}/media"
    upload_files = {
        "file": (f"Invoice_{invoice_no}.pdf", io.BytesIO(pdf_bytes), "application/pdf")
    }
    upload_data = {
        "messaging_product": "whatsapp",
        "type": "application/pdf",
    }

    upload_resp = requests.post(
        upload_url,
        headers={"Authorization": f"Bearer {token}"},
        files=upload_files,
        data=upload_data,
        timeout=30,
    )
    upload_resp.raise_for_status()
    media_id = upload_resp.json()["id"]

    # 2. Send document message
    send_url = f"https://graph.facebook.com/{api_ver}/{phone_id}/messages"
    send_resp = requests.post(
        send_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "messaging_product": "whatsapp",
            "to": clean_number,
            "type": "document",
            "document": {
                "id": media_id,
                "filename": f"Invoice_{invoice_no}.pdf",
            },
        },
        timeout=30,
    )
    send_resp.raise_for_status()
    return send_resp.json()
