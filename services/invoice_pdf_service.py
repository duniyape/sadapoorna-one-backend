import os
import io
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Tuple
from num2words import num2words
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle

IST = timezone(timedelta(hours=5, minutes=30))


def _pdf_money(val: Any) -> float:
    try:
        return round(float(val or 0), 2)
    except (TypeError, ValueError):
        return 0.00


def _pdf_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, dict):
        return ", ".join(
            str(v) for v in value.values() if v not in (None, "")
        )
    return str(value)


def _pdf_customer_address(customer: Dict[str, Any]) -> str:
    if not customer:
        return ""
    addr = customer.get("billing_address") or customer.get("shipping_address") or {}
    if isinstance(addr, dict):
        parts = [
            addr.get("address") or addr.get("line1"),
            addr.get("city"),
            addr.get("state"),
            addr.get("pincode") or addr.get("postal_code")
        ]
        return ", ".join([str(p) for p in parts if p])
    return _pdf_text(addr or customer.get("location"), "")


def _pdf_customer_state(customer: Dict[str, Any]) -> Tuple[str, str]:
    if not customer:
        return "", ""
    addr = customer.get("billing_address") or customer.get("shipping_address") or customer.get("location") or {}
    if isinstance(addr, dict):
        state = addr.get("state") or addr.get("state_name") or ""
        code = addr.get("state_code") or addr.get("gst_state_code") or ""
        if not code and state and "madhya" in str(state).lower():
            code = "23"
        return str(state), str(code)
    return "", ""


def _build_gst_rows(order: Dict[str, Any]) -> List[Dict[str, float]]:
    grouped = {}
    for item in order.get("items", []) or []:
        rate = _pdf_money(item.get("gst_percent"))
        taxable = _pdf_money(item.get("taxable_amount"))
        gst_amount = _pdf_money(item.get("gst_amount"))
        if taxable > 0:
            grouped.setdefault(rate, {"taxable": 0.0, "gst": 0.0})
            grouped[rate]["taxable"] += taxable
            grouped[rate]["gst"] += gst_amount

    rows = []
    for rate in sorted(grouped.keys()):
        rows.append({
            "rate": rate,
            "taxable": round(grouped[rate]["taxable"], 2),
            "gst": round(grouped[rate]["gst"], 2),
        })
    return rows


def generate_invoice_pdf(order: Dict[str, Any]) -> bytes:
    """
    Generate an in-memory A4 invoice PDF for the given order and return raw bytes.
    """
    customer = order.get("customer") or {}
    supplier_gst = os.getenv("SUPPLIER_GSTIN", "")
    supplier_state = os.getenv("SUPPLIER_STATE", "Madhya Pradesh")
    supplier_code = os.getenv("SUPPLIER_STATE_CODE", "23")

    customer_name = (
        customer.get("shop_name")
        or customer.get("company_name")
        or customer.get("owner")
        or customer.get("name")
        or "Walk-in Customer"
    )
    customer_address = _pdf_customer_address(customer)
    customer_gst = customer.get("gst_number") or customer.get("gstin") or ""
    customer_state, customer_state_code = _pdf_customer_state(customer)

    is_inter_state = bool(
        customer_state_code
        and supplier_code
        and str(customer_state_code) != str(supplier_code)
    )

    invoice_no = order.get("invoice_no") or order.get("order_no") or "NA"
    invoice_date = order.get("billed_at") or order.get("created_at")

    if isinstance(invoice_date, datetime):
        invoice_date = invoice_date.astimezone(timezone.utc).astimezone(IST).strftime("%d-%m-%Y")
    elif isinstance(invoice_date, str) and invoice_date:
        try:
            dt = datetime.fromisoformat(invoice_date.replace("Z", "+00:00"))
            invoice_date = dt.astimezone(IST).strftime("%d-%m-%Y")
        except ValueError:
            invoice_date = invoice_date[:10]
    else:
        invoice_date = str(invoice_date or "")

    subtotal = _pdf_money(order.get("subtotal"))
    total_gst = _pdf_money(order.get("total_gst"))
    other_charges = _pdf_money(order.get("other_charges"))
    discount = _pdf_money(order.get("discount"))
    previous_balance = _pdf_money(
        order.get("previous_balance", order.get("previousBalance", 0))
    )
    gross_bill = round(subtotal + total_gst + other_charges, 2)
    bill_amount = _pdf_money(order.get("grand_total", gross_bill - discount))
    total_due = round(bill_amount + previous_balance, 2)

    try:
        words = num2words(int(round(total_due)), lang="en_IN").title()
    except Exception:
        words = f"{total_due:.2f}"

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    margin = 40

    # -----------------------------------------------------
    # HEADER & SUPPLIER
    # -----------------------------------------------------
    c.setFont("Helvetica-Bold", 14)
    c.drawString(margin, height - 50, "SADAPOORNA TRADERS")
    c.setFont("Helvetica", 8)
    c.setFillGray(0.3)
    c.drawString(margin, height - 62, "LIG B-301, E-7, Arera Colony, Bhopal (MP) 462016")
    c.drawString(
        margin,
        height - 72,
        f"GSTIN: {supplier_gst} | State: {supplier_state} ({supplier_code})"
    )

    logo_path = os.getenv("INVOICE_LOGO_PATH", "logo.png")
    if os.path.exists(logo_path):
        try:
            c.drawImage(
                logo_path,
                width - margin - 100,
                height - 65,
                width=80,
                height=50,
                preserveAspectRatio=True,
                mask="auto",
            )
        except Exception:
            pass

    c.drawRightString(width - margin, height - 62, "Mob: 9977233055, 7553524977")
    c.line(margin, height - 80, width - margin, height - 80)

    # -----------------------------------------------------
    # CUSTOMER / PLACE OF SUPPLY
    # -----------------------------------------------------
    y_meta = height - 105
    c.setFillGray(0)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(margin, y_meta, "BILL TO / PLACE OF SUPPLY:")
    c.setFont("Helvetica", 9)
    c.drawString(margin, y_meta - 12, str(customer_name))
    c.setFont("Helvetica", 8)
    c.drawString(margin, y_meta - 22, str(customer_address)[:90])
    c.drawString(margin, y_meta - 32, f"GSTIN: {customer_gst}")
    c.drawString(margin, y_meta - 42, f"State: {customer_state}")

    c.drawRightString(width - margin, y_meta, f"Invoice No: #{invoice_no}")
    c.drawRightString(width - margin, y_meta - 12, f"Date: {invoice_date}")

    # -----------------------------------------------------
    # ITEMS TABLE
    # -----------------------------------------------------
    y_table = y_meta - 65
    table_rows = [["#", "Description", "HSN", "Units", "Qty", "Rate", "Amount"]]

    for i, item in enumerate(order.get("items", []) or [], 1):
        product_name = item.get("product_name") or (item.get("product", {}).get("name") if isinstance(item.get("product"), dict) else None)
        variant_name = item.get("variant_name") or (item.get("variant", {}).get("name") if isinstance(item.get("variant"), dict) else None)
        description = product_name or variant_name or item.get("description") or "Item"
        if product_name and variant_name and variant_name != product_name:
            description = f"{product_name} - {variant_name}"

        raw_unit = item.get("billingUnit") or item.get("unit") or item.get("unit_name") or ""
        if isinstance(raw_unit, dict):
            unit = raw_unit.get("symbol") or raw_unit.get("name") or ""
        else:
            unit = str(raw_unit)

        billing_qty = item.get("billingQty", item.get("quantity", 0))
        qty = item.get("quantity", 0)
        rate = _pdf_money(item.get("rate"))
        amount = _pdf_money(item.get("total_amount", item.get("amount", item.get("taxable_amount", 0))))
        hsn = item.get("hsn") or item.get("hsn_code") or "-"
        weight_type = item.get("weightagetype") or item.get("weightage_type") or unit or ""
        rate_unit = item.get("rateUnit") or unit or "unit"

        table_rows.append([
            i,
            str(description),
            str(hsn),
            f"{billing_qty} {unit}".strip(),
            f"{qty} {weight_type}".strip(),
            f"{rate:.2f}/- per {rate_unit}",
            f"{amount:.2f}",
        ])

    table_rows.append(["", "", "", "", "", "Total:", f"{gross_bill:.2f}"])

    table = Table(
        table_rows,
        colWidths=[20, 170, 60, 55, 60, 60, 90]
    )
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#333333")),
        ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.1, colors.lightgrey),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    tw, th = table.wrapOn(c, width, height)
    table.drawOn(c, margin, y_table - th)

    # -----------------------------------------------------
    # GST SUMMARY
    # -----------------------------------------------------
    y_gst = y_table - th - 25
    c.setFont("Helvetica-Bold", 8)
    c.drawString(margin, y_gst, "GST TAX SUMMARY")

    gst_rows = _build_gst_rows(order)
    gst_table_rows = []
    if is_inter_state:
        gst_table_rows.append(["Tax Rate", "Taxable Value", "IGST", "Total Tax"])
        for row in gst_rows:
            gst_table_rows.append([
                f"{row['rate']:.2f}%",
                f"{row['taxable']:.2f}",
                f"{row['gst']:.2f}",
                f"{row['gst']:.2f}",
            ])
        if not gst_rows:
            gst_table_rows.append(["0%", f"{subtotal:.2f}", "0.00", "0.00"])
        col_w = [100, 110, 100, 110]
    else:
        gst_table_rows.append(["Tax Rate", "Taxable Value", "CGST", "SGST", "Total Tax"])
        for row in gst_rows:
            half = round(row["gst"] / 2, 2)
            gst_table_rows.append([
                f"{row['rate']:.2f}%",
                f"{row['taxable']:.2f}",
                f"{half:.2f}",
                f"{half:.2f}",
                f"{row['gst']:.2f}",
            ])
        if not gst_rows:
            gst_table_rows.append(["0%", f"{subtotal:.2f}", "0.00", "0.00", "0.00"])
        col_w = [70, 95, 75, 75, 105]

    gst_table = Table(gst_table_rows, colWidths=col_w)
    gst_table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("GRID", (0, 0), (-1, -1), 0.1, colors.grey),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
    ]))
    gw, gh = gst_table.wrapOn(c, width, height)
    gst_table.drawOn(c, margin, y_gst - gh - 5)

    # -----------------------------------------------------
    # TOTALS & BALANCE
    # -----------------------------------------------------
    y_fin = y_gst - gh - 35
    c.setFont("Helvetica", 9)
    c.drawRightString(width - 140, y_fin, "Current Bill Amount:")
    c.drawRightString(width - margin, y_fin, f"₹{gross_bill:.2f}")

    offset = 0
    if discount > 0:
        offset += 15
        c.setFillGray(0.4)
        c.drawRightString(width - 140, y_fin - offset, "Discount (-):")
        c.drawRightString(width - margin, y_fin - offset, f"₹{discount:.2f}")
        c.setFillGray(0)

    if other_charges > 0:
        offset += 15
        c.drawRightString(width - 140, y_fin - offset, "Other Charges (+):")
        c.drawRightString(width - margin, y_fin - offset, f"₹{other_charges:.2f}")

    offset += 15
    c.drawRightString(width - 140, y_fin - offset, "Previous Balance:")
    c.drawRightString(width - margin, y_fin - offset, f"₹{previous_balance:.2f}")
    c.line(width - 160, y_fin - offset - 7, width - margin, y_fin - offset - 7)

    offset += 20
    c.setFont("Helvetica-Bold", 11)
    c.drawRightString(width - 140, y_fin - offset, "Total Balance Due:")
    c.drawRightString(width - margin, y_fin - offset, f"₹{total_due:.2f}")
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(margin, y_fin - offset, f"Amount in words: INR {words} Only")

    # -----------------------------------------------------
    # BANK DETAILS
    # -----------------------------------------------------
    y_bank = y_fin - offset - 45
    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin, y_bank, "Bank Details:")
    c.setFont("Helvetica", 8)
    c.drawString(
        margin,
        y_bank - 10,
        "Indian Overseas Bank | Arera Colony, Bhopal | A/C: 372802000000555 | IFSC: IOBA0003728"
    )

    # -----------------------------------------------------
    # TERMS & SIGNATURE
    # -----------------------------------------------------
    y_terms = y_bank - 40
    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin, y_terms, "Terms & Conditions:")
    c.setFont("Helvetica", 8)
    terms = [
        "1. Not responsible after goods despatched.",
        "2. Interest @24% P.A. after 7 days.",
        "3. Payment on demand.",
        "4. (L) is for Loose Packing.",
    ]
    for i, line in enumerate(terms):
        c.drawString(margin, y_terms - 10 - (i * 9), line)

    sign_path = os.getenv("INVOICE_SIGN_PATH", "sign.png")
    if os.path.exists(sign_path):
        try:
            c.drawImage(
                sign_path,
                width - margin - 80,
                margin + 40,
                width=80,
                height=50,
                preserveAspectRatio=True,
                mask="auto",
            )
        except Exception:
            pass

    c.drawRightString(width - margin, margin + 35, "For SADAPOORNA TRADERS")
    c.setFont("Helvetica", 7)
    c.drawRightString(width - margin, margin + 25, "Authorized Signatory")

    c.line(margin, margin + 15, width - margin, margin + 15)
    c.setFont("Helvetica-Oblique", 6.5)
    c.setFillGray(0.4)
    c.drawCentredString(
        width / 2,
        margin + 5,
        "* This is a computer-generated Bill and managed by Duniyape Technologies"
    )

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.getvalue()
