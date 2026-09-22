import os
import re
from typing import Optional, List, Dict, Any, Tuple, Union
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from bson import ObjectId

from database import (
    groups_collection,
    subgroups_collection,
    ledgers_collection,
    vouchers_collection,
    orders_collection,
    customers_collection,
    users_collection,
)
from routes.voucher import generate_voucher_number


# =========================================================
# SYSTEM ACCOUNT NAMES
# =========================================================
SYS_SALES_ACCOUNT = "Sales Account"
SYS_SALES_RETURN = "Sales Return"
SYS_DISCOUNT_ALLOWED = "Discount Allowed"
SYS_OUTPUT_CGST = "Output CGST"
SYS_OUTPUT_SGST = "Output SGST"
SYS_OUTPUT_IGST = "Output IGST"
SYS_CASH_IN_HAND = "Cash in Hand"
SYS_DEFAULT_BANK = "Bank Account (Main)"
SYS_CHEQUES_IN_HAND = "Cheques in Hand"
SYS_FINANCE_CLEARING = "Finance Clearing Account"
SYS_SUNDRY_DEBTORS = "Sundry Debtors"
SYS_BOUNCE_CHARGES_INCOME = "Cheque Bounce Charges Income"
SYS_FINANCE_SUBVENTION_CHARGES = "Finance & Subvention Charges"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_oid(val) -> Optional[ObjectId]:
    if not val:
        return None
    if isinstance(val, ObjectId):
        return val
    try:
        return ObjectId(str(val))
    except Exception:
        return None


# =========================================================
# 1. ENSURE SYSTEM GROUPS & LEDGERS
# =========================================================

def ensure_system_group(group_name: str, group_type: str) -> ObjectId:
    """Finds or creates standard account group."""
    grp = groups_collection.find_one({"group_name": group_name, "group_type": group_type})
    if grp:
        return grp["_id"]
    
    now = utc_now()
    res = groups_collection.insert_one({
        "group_name": group_name,
        "group_type": group_type,
        "is_system": True,
        "created_at": now,
        "updated_at": now,
    })
    return res.inserted_id


def ensure_system_ledger(ledger_name: str, group_name: str, group_type: str) -> Tuple[str, str]:
    """Finds or creates a standard system ledger under a given group."""
    ledger = ledgers_collection.find_one({"ledger_name": ledger_name})
    if ledger:
        return str(ledger["_id"]), ledger["ledger_name"]
    
    grp_id = ensure_system_group(group_name, group_type)
    now = utc_now()
    res = ledgers_collection.insert_one({
        "ledger_name": ledger_name,
        "group_id": grp_id,
        "group_name": group_name,
        "group_type": group_type,
        "subgroup_id": None,
        "subgroup_name": None,
        "opening_balance": 0.0,
        "opening_balance_type": "Dr" if group_type == "ASSETS" else "Cr",
        "is_system": True,
        "status": "ACTIVE",
        "created_at": now,
        "updated_at": now,
    })
    return str(res.inserted_id), ledger_name


def ensure_system_ledgers() -> Dict[str, Tuple[str, str]]:
    """Guarantees all essential core accounts exist."""
    return {
        "sales": ensure_system_ledger(SYS_SALES_ACCOUNT, "Sales Accounts", "INCOME"),
        "sales_return": ensure_system_ledger(SYS_SALES_RETURN, "Sales Accounts", "INCOME"),
        "discount_allowed": ensure_system_ledger(SYS_DISCOUNT_ALLOWED, "Indirect Expenses", "EXPENSES"),
        "cgst": ensure_system_ledger(SYS_OUTPUT_CGST, "Duties & Taxes", "LIABILITIES"),
        "sgst": ensure_system_ledger(SYS_OUTPUT_SGST, "Duties & Taxes", "LIABILITIES"),
        "igst": ensure_system_ledger(SYS_OUTPUT_IGST, "Duties & Taxes", "LIABILITIES"),
        "cash": ensure_system_ledger(SYS_CASH_IN_HAND, "Cash-in-Hand", "ASSETS"),
        "bank": ensure_system_ledger(SYS_DEFAULT_BANK, "Bank Accounts", "ASSETS"),
        "cheque": ensure_system_ledger(SYS_CHEQUES_IN_HAND, "Current Assets", "ASSETS"),
        "finance": ensure_system_ledger(SYS_FINANCE_CLEARING, "Current Assets", "ASSETS"),
        "bounce_charges": ensure_system_ledger(SYS_BOUNCE_CHARGES_INCOME, "Indirect Incomes", "INCOME"),
        "subvention_charges": ensure_system_ledger(SYS_FINANCE_SUBVENTION_CHARGES, "Indirect Expenses", "EXPENSES"),
    }


# =========================================================
# 2. CUSTOMER RESOLUTION & LEDGER PROVISIONING
# =========================================================

def resolve_customer(customer_id: Any, session=None) -> Tuple[ObjectId, dict]:
    """
    Resolves a customer document by:
    1. MongoDB ObjectId (or 24-hex string) matching '_id'
    2. Custom business ID (e.g., 'CUST1001', 'CUST1002') matching 'id'
    3. Case-insensitive fallback on 'id'
    Returns (cust["_id"], cust_doc).
    Raises ValueError(f"Customer not found: {customer_id}") if missing or non-existent.
    """
    if not customer_id:
        raise ValueError("Customer ID is required")

    cust = None
    if isinstance(customer_id, ObjectId):
        cust = customers_collection.find_one({"_id": customer_id}, session=session)
    else:
        cid_str = str(customer_id).strip()
        if not cid_str:
            raise ValueError("Customer ID is required")

        # 1. If valid ObjectId hex, try finding by _id first
        if ObjectId.is_valid(cid_str):
            cust = customers_collection.find_one({"_id": ObjectId(cid_str)}, session=session)

        # 2. Try by custom "id" field (exact match)
        if not cust:
            cust = customers_collection.find_one({"id": cid_str}, session=session)

        # 3. Case-insensitive fallback for "id" if e.g. "cust1002" passed
        if not cust and cid_str.upper().startswith("CUST"):
            cust = customers_collection.find_one({"id": cid_str.upper()}, session=session)

        # 4. If still not found and not a valid ObjectId hex, try case-insensitive regex on id
        if not cust and not ObjectId.is_valid(cid_str):
            cust = customers_collection.find_one(
                {"id": {"$regex": f"^{re.escape(cid_str)}$", "$options": "i"}},
                session=session
            )

    if not cust:
        raise ValueError(f"Customer not found: {customer_id}")

    return cust["_id"], cust


def ensure_customer_ledger(customer_id: str, customer_name: Optional[str] = None) -> Tuple[str, str]:
    """
    Finds or creates a personal ledger for a customer under 'Sundry Debtors'.
    Stores ledger_id back onto the customer document.
    """
    try:
        cust_oid, cust = resolve_customer(customer_id)
    except ValueError:
        cust_oid = to_oid(customer_id)
        cust = customers_collection.find_one({"_id": cust_oid}) if cust_oid else None
    
    if cust and cust.get("ledger_id"):
        led_oid = to_oid(cust["ledger_id"])
        ledger = ledgers_collection.find_one({"_id": led_oid})
        if ledger:
            return str(ledger["_id"]), ledger["ledger_name"]

    name = customer_name or (cust.get("name") if cust else None) or (cust.get("company_name") if cust else None) or f"Customer {customer_id}"
    ledger_name = f"Customer - {name}"

    # Check if a ledger already exists with this name
    existing = ledgers_collection.find_one({"ledger_name": ledger_name})
    if existing:
        ledger_id = str(existing["_id"])
        if cust and cust.get("_id"):
            customers_collection.update_one({"_id": cust["_id"]}, {"$set": {"ledger_id": ledger_id}})
        return ledger_id, existing["ledger_name"]

    # Provision new ledger
    grp_id = ensure_system_group("Sundry Debtors", "ASSETS")
    now = utc_now()
    res = ledgers_collection.insert_one({
        "ledger_name": ledger_name,
        "customer_id": cust_oid,
        "group_id": grp_id,
        "group_name": "Sundry Debtors",
        "group_type": "ASSETS",
        "subgroup_id": None,
        "subgroup_name": None,
        "opening_balance": 0.0,
        "opening_balance_type": "Dr",
        "description": f"Ledger for customer {name}",
        "status": "ACTIVE",
        "created_at": now,
        "updated_at": now,
    })
    ledger_id = str(res.inserted_id)

    if cust and cust.get("_id"):
        customers_collection.update_one({"_id": cust["_id"]}, {"$set": {"ledger_id": ledger_id}})

    return ledger_id, ledger_name


# =========================================================
# 2B. EMPLOYEE CASH CUSTODY LEDGER AUTO-PROVISIONING
# =========================================================

def ensure_employee_cash_ledger(user_id: str, employee_name: Optional[str] = None) -> Tuple[str, str]:
    """
    Finds or creates a personal cash-custody ledger for any employee/associate
    under 'Cash-in-Hand' (Group: ASSETS).
    Stores cash_ledger_id onto the user document.
    """
    u_oid = to_oid(user_id)
    user = users_collection.find_one({"_id": u_oid}) if u_oid else None

    if user and user.get("cash_ledger_id"):
        led_oid = to_oid(user["cash_ledger_id"])
        ledger = ledgers_collection.find_one({"_id": led_oid})
        if ledger:
            return str(ledger["_id"]), ledger["ledger_name"]

    name = employee_name or (user.get("name") if user else None) or f"Staff {user_id}"
    ledger_name = f"Employee Cash - {name}"

    existing = ledgers_collection.find_one({"ledger_name": ledger_name})
    if existing:
        ledger_id = str(existing["_id"])
        if user and user.get("_id"):
            users_collection.update_one({"_id": user["_id"]}, {"$set": {"cash_ledger_id": ledger_id}})
            ledgers_collection.update_one({"_id": existing["_id"]}, {"$set": {"employee_id": user["_id"]}})
        return ledger_id, existing["ledger_name"]

    grp_id = ensure_system_group("Cash-in-Hand", "ASSETS")
    now = utc_now()
    res = ledgers_collection.insert_one({
        "ledger_name": ledger_name,
        "employee_id": u_oid,
        "group_id": grp_id,
        "group_name": "Cash-in-Hand",
        "group_type": "ASSETS",
        "subgroup_id": None,
        "subgroup_name": None,
        "opening_balance": 0.0,
        "opening_balance_type": "Dr",
        "description": f"Cash custody ledger for staff {name}",
        "status": "ACTIVE",
        "created_at": now,
        "updated_at": now,
    })
    ledger_id = str(res.inserted_id)

    if user and user.get("_id"):
        users_collection.update_one({"_id": user["_id"]}, {"$set": {"cash_ledger_id": ledger_id}})

    return ledger_id, ledger_name


# =========================================================
# 3. SALES INVOICE VOUCHER CREATION (ON BILLING)
# =========================================================

def create_sales_invoice_voucher(
    order: dict,
    invoice_no: str,
    user_id: str,
    session=None,
) -> dict:
    """
    Creates the Sales Invoice Voucher upon order billing.
    Accrual principle: Dr Customer Ledger (Grand Total)
                       Cr Sales Account (Taxable Subtotal)
                       Cr Output GST (CGST/SGST or IGST)
    """
    grand_total = round(float(order.get("grand_total", 0.0)), 2)
    total_gst = round(float(order.get("total_gst", 0.0)), 2)
    subtotal = round(float(order.get("subtotal", grand_total - total_gst)), 2)
    discount = round(float(order.get("discount", 0.0)), 2)

    # Net taxable revenue
    taxable_revenue = round(subtotal, 2)

    cust_id = str(order.get("customer_id", ""))
    cust_ledger_id, cust_ledger_name = ensure_customer_ledger(cust_id)
    sys_ledgers = ensure_system_ledgers()

    sales_ledger_id, sales_ledger_name = sys_ledgers["sales"]
    discount_ledger_id, discount_ledger_name = sys_ledgers["discount_allowed"]
    now = utc_now()
    voucher_date = order.get("billed_at") or now

    entries = []

    # 1. DEBIT CUSTOMER (Full Invoice Value)
    entries.append({
        "ledger_id": cust_ledger_id,
        "ledger_name": cust_ledger_name,
        "narration": f"Invoice {invoice_no} billed for Order {order.get('order_no', '')}",
        "debit": grand_total,
        "credit": 0.0,
        "user_id": user_id,
    })

    if discount > 0:
        entries.append({
            "ledger_id": discount_ledger_id,
            "ledger_name": discount_ledger_name,
            "narration": f"Discount allowed on Invoice {invoice_no}",
            "debit": discount,
            "credit": 0.0,
            "user_id": user_id,
        })

    # 2. CREDIT SALES REVENUE
    entries.append({
        "ledger_id": sales_ledger_id,
        "ledger_name": sales_ledger_name,
        "narration": f"Sales revenue against Invoice {invoice_no}",
        "debit": 0.0,
        "credit": taxable_revenue,
        "user_id": user_id,
    })

    # 3. CREDIT GST ACCOUNTS
    if total_gst > 0:
        half_gst = round(total_gst / 2.0, 2)
        other_half = round(total_gst - half_gst, 2)

        cgst_id, cgst_name = sys_ledgers["cgst"]
        sgst_id, sgst_name = sys_ledgers["sgst"]

        entries.append({
            "ledger_id": cgst_id,
            "ledger_name": cgst_name,
            "narration": f"Output CGST for Invoice {invoice_no}",
            "debit": 0.0,
            "credit": half_gst,
            "user_id": user_id,
        })
        entries.append({
            "ledger_id": sgst_id,
            "ledger_name": sgst_name,
            "narration": f"Output SGST for Invoice {invoice_no}",
            "debit": 0.0,
            "credit": other_half,
            "user_id": user_id,
        })

    # Verify double-entry balance
    total_debit = round(sum(e["debit"] for e in entries), 2)
    total_credit = round(sum(e["credit"] for e in entries), 2)

    # Adjust rounding cents if any discrepancy between total_debit & total_credit
    diff = round(total_debit - total_credit, 2)
    if diff != 0:
        entries[1]["credit"] = round(entries[1]["credit"] + diff, 2)

    voucher_number, txn = generate_voucher_number(
        voucher_type="Sales",
        voucher_mode="Credit",
        voucher_date=voucher_date,
    )

    voucher_doc = {
        "voucher_number": voucher_number,
        "voucher_type": "Sales",
        "voucher_mode": "Credit",
        "txn": txn,
        "order_id": order["_id"],
        "invoice_no": invoice_no,
        "customer_id": order.get("customer_id"),
        "date": voucher_date,
        "date_key": voucher_date.strftime("%Y-%m-%d"),
        "narration": f"Sales Invoice {invoice_no} for Order {order.get('order_no', '')}",
        "amount": grand_total,
        "entries": entries,
        "created_by": user_id,
        "created_at": now,
    }

    insert_kwargs = {"session": session} if session else {}
    vouchers_collection.insert_one(voucher_doc, **insert_kwargs)
    return voucher_doc


def create_sales_return_voucher(
    order: dict,
    invoice_no: str,
    user_id: str,
    session=None,
) -> dict:
    """
    Creates the Sales Return Voucher (SRV) upon sale_return order billing.
    Accrual double-entry:
      Dr Sales Return (Taxable Revenue Reversal)
      Dr Output GST (CGST/SGST or IGST Liability Reversal)
      Cr Customer Ledger (Full Return Value - Reduces Customer's Receivable)

    Also updates the original referenced sale order's pending amount and marks
    the return order as SETTLED.
    """
    grand_total = round(float(order.get("grand_total", 0.0)), 2)
    total_gst = round(float(order.get("total_gst", 0.0)), 2)
    subtotal = round(float(order.get("subtotal", grand_total - total_gst)), 2)
    discount = round(float(order.get("discount", 0.0)), 2)
    
    taxable_revenue = round(subtotal, 2)

    cust_id = str(order.get("customer_id", ""))
    cust_ledger_id, cust_ledger_name = ensure_customer_ledger(cust_id)
    sys_ledgers = ensure_system_ledgers()

    sales_ret_id, sales_ret_name = sys_ledgers["sales_return"]
    discount_ledger_id, discount_ledger_name = sys_ledgers["discount_allowed"]
    now = utc_now()
    voucher_date = order.get("billed_at") or now
    if voucher_date.tzinfo is None:
        voucher_date = voucher_date.replace(tzinfo=timezone.utc)

    # Reference to original sales invoice if available
    ref_inv_id = order.get("ref_invoice_id")
    ref_inv_no = None
    insert_kwargs = {"session": session} if session else {}
    if ref_inv_id:
        parent_order = orders_collection.find_one({"_id": ref_inv_id}, **insert_kwargs)
        if parent_order:
            ref_inv_no = parent_order.get("invoice_no")

    ref_txt = f" against Invoice {ref_inv_no}" if ref_inv_no else ""

    entries = []

    # 1. DEBIT SALES RETURN (Taxable Revenue Reversal)
    entries.append({
        "ledger_id": sales_ret_id,
        "ledger_name": sales_ret_name,
        "narration": f"Sales return reversal against {invoice_no}{ref_txt}",
        "debit": taxable_revenue,
        "credit": 0.0,
        "user_id": user_id,
    })

    # 2. DEBIT OUTPUT GST ACCOUNTS (GST Liability Reversal)
    if total_gst > 0:
        half_gst = round(total_gst / 2.0, 2)
        other_half = round(total_gst - half_gst, 2)

        cgst_id, cgst_name = sys_ledgers["cgst"]
        sgst_id, sgst_name = sys_ledgers["sgst"]

        entries.append({
            "ledger_id": cgst_id,
            "ledger_name": cgst_name,
            "narration": f"Output CGST reversal for Return {invoice_no}",
            "debit": half_gst,
            "credit": 0.0,
            "user_id": user_id,
        })
        entries.append({
            "ledger_id": sgst_id,
            "ledger_name": sgst_name,
            "narration": f"Output SGST reversal for Return {invoice_no}",
            "debit": other_half,
            "credit": 0.0,
            "user_id": user_id,
        })

    # 3. CREDIT CUSTOMER (Full Return Value - Reduces Customer's Debt)
    entries.append({
        "ledger_id": cust_ledger_id,
        "ledger_name": cust_ledger_name,
        "narration": f"Sales Return {invoice_no}{ref_txt} for Order {order.get('order_no', '')}",
        "debit": 0.0,
        "credit": grand_total,
        "user_id": user_id,
    })
    
    if discount > 0:
        entries.append({
            "ledger_id": discount_ledger_id,
            "ledger_name": discount_ledger_name,
            "narration": f"Discount reversed on Sales Return {invoice_no}",
            "debit": 0.0,
            "credit": discount,
            "user_id": user_id,
        })

    # Verify double-entry balance
    total_debit = round(sum(e["debit"] for e in entries), 2)
    total_credit = round(sum(e["credit"] for e in entries), 2)

    diff = round(total_debit - total_credit, 2)
    if diff != 0:
        entries[0]["debit"] = round(entries[0]["debit"] - diff, 2)

    voucher_number, txn = generate_voucher_number(
        voucher_type="Sales Return",
        voucher_mode="Credit",
        voucher_date=voucher_date,
    )

    voucher_doc = {
        "voucher_number": voucher_number,
        "voucher_type": "Sales Return",
        "voucher_mode": "Credit",
        "txn": txn,
        "order_id": order["_id"],
        "invoice_no": invoice_no,
        "ref_invoice_id": ref_inv_id,
        "ref_invoice_no": ref_inv_no,
        "customer_id": order.get("customer_id"),
        "date": voucher_date,
        "date_key": voucher_date.strftime("%Y-%m-%d"),
        "narration": f"Sales Return {invoice_no}{ref_txt} for Order {order.get('order_no', '')}",
        "amount": grand_total,
        "entries": entries,
        "created_by": user_id,
        "created_at": now,
    }

    vouchers_collection.insert_one(voucher_doc, **insert_kwargs)

    # Update the sale_return order financial status
    orders_collection.update_one(
        {"_id": order["_id"]},
        {
            "$set": {
                "payment_status": "SETTLED",
                "paid_amount": grand_total,
                "pending_amount": 0.0,
                "voucher_number": voucher_number,
                "updated_at": now,
            }
        },
        **insert_kwargs
    )

    # Adjust the pending balance on the referenced original sale order
    if ref_inv_id:
        parent_order = orders_collection.find_one({"_id": ref_inv_id}, **insert_kwargs)
        if parent_order:
            orig_pending = float(parent_order.get("pending_amount", 0.0))
            new_pending = max(0.0, round(orig_pending - grand_total, 2))
            orig_return_amt = round(float(parent_order.get("return_amount", 0.0)) + grand_total, 2)

            orig_paid = float(parent_order.get("paid_amount", 0.0))
            if new_pending <= 0.01:
                new_pay_status = "PAID"
            elif orig_paid > 0.01 or orig_return_amt > 0.01:
                new_pay_status = "PARTIALLY_PAID"
            else:
                new_pay_status = parent_order.get("payment_status", "UNPAID")

            u_oid = to_oid(user_id) or user_id
            orders_collection.update_one(
                {"_id": ref_inv_id},
                {
                    "$set": {
                        "pending_amount": new_pending,
                        "return_amount": orig_return_amt,
                        "payment_status": new_pay_status,
                        "updated_at": now,
                    },
                    "$push": {
                        "tracking": {
                            "status": "Return Adjusted",
                            "timestamp": now,
                            "updated_by": u_oid,
                            "note": f"Adjusted return of ₹{grand_total:.2f} via {invoice_no} ({voucher_number}). Remaining pending: ₹{new_pending:.2f}",
                        }
                    }
                },
                **insert_kwargs
            )

    return voucher_doc


# =========================================================
# 4. SINGLE-MODE RECEIPT VOUCHER CREATION (SETTLEMENT)
# =========================================================

def record_order_receipt_voucher(
    order_id: str,
    payment_mode: str,
    amount: float,
    user_id: str,
    bank_account_name: Optional[str] = None,
    transaction_ref: Optional[str] = None,
    cheque_no: Optional[str] = None,
    cheque_date: Optional[str] = None,
    cheque_bank: Optional[str] = None,
    financier_name: Optional[str] = None,
    receipt_date: Optional[datetime] = None,
    bank_clearance_date: Optional[datetime] = None,
    notes: Optional[str] = None,
    collected_by_id: Optional[str] = None,
    session=None,
) -> Tuple[dict, dict]:
    """
    Creates a single-mode Receipt Voucher settling an order's invoice.
    Updates the order's paid_amount, pending_amount, and payment_status.
    """
    ord_oid = to_oid(order_id)
    order = orders_collection.find_one({"_id": ord_oid}, session=session)
    if not order:
        raise ValueError(f"Order not found: {order_id}")

    amount = round(float(amount), 2)
    if amount <= 0:
        raise ValueError("Receipt amount must be greater than 0")

    bill_amount = round(float(order.get("bill_amount", order.get("grand_total", 0.0))), 2)
    current_paid = round(float(order.get("paid_amount", 0.0)), 2)
    current_pending = round(float(order.get("pending_amount", bill_amount - current_paid)), 2)

    if current_pending <= 0.01:
        raise ValueError("This order is already fully paid. No pending dues to collect.")

    if amount > current_pending + 0.05:
        raise ValueError(f"Receipt amount (Rs.{amount:.2f}) exceeds pending balance (Rs.{current_pending:.2f}). Excess collection is not allowed.")

    cust_id = str(order.get("customer_id", ""))
    cust_ledger_id, cust_ledger_name = ensure_customer_ledger(cust_id)
    sys_ledgers = ensure_system_ledgers()

    # Determine asset ledger based on single payment mode
    collector_id = collected_by_id or user_id
    pm = payment_mode.upper()
    if pm in ["CASH", "COD"]:
        if collector_id and to_oid(collector_id):
            asset_ledger_id, asset_ledger_name = ensure_employee_cash_ledger(collector_id)
            voucher_mode = "Cash"
            narration_mode = f"Cash collected by {asset_ledger_name}"
        else:
            asset_ledger_id, asset_ledger_name = sys_ledgers["cash"]
            voucher_mode = "Cash"
            narration_mode = "Cash collected"
    elif pm in ["UPI", "BANK", "BANK_TRANSFER", "ONLINE", "NEFT", "RTGS"]:
        asset_ledger_id, asset_ledger_name = sys_ledgers["bank"]
        if bank_account_name:
            asset_ledger_name = bank_account_name
        voucher_mode = "Bank"
        ref_text = f" (Ref: {transaction_ref})" if transaction_ref else ""
        narration_mode = f"Bank/UPI receipt{ref_text}"
    elif pm in ["CHEQUE", "DD"]:
        asset_ledger_id, asset_ledger_name = sys_ledgers["cheque"]
        voucher_mode = "Bank"
        chk_text = f" (Cheque #{cheque_no} {cheque_bank or ''})" if cheque_no else ""
        narration_mode = f"Cheque in hand{chk_text}"
    elif pm in ["FINANCE", "LOAN", "NBFC"]:
        asset_ledger_id, asset_ledger_name = sys_ledgers["finance"]
        voucher_mode = "Bank"
        fn_text = f" ({financier_name})" if financier_name else ""
        ref_text = f" (Ref: {transaction_ref})" if transaction_ref else ""
        narration_mode = f"Finance clearance{fn_text}{ref_text}"
    else:
        asset_ledger_id, asset_ledger_name = sys_ledgers["cash"]
        voucher_mode = "Cash"
        narration_mode = f"Payment collected ({payment_mode})"

    now = utc_now()
    if receipt_date:
        if receipt_date.tzinfo is None:
            receipt_date = receipt_date.replace(tzinfo=timezone.utc)
        if receipt_date.hour == 0 and receipt_date.minute == 0 and receipt_date.second == 0 and receipt_date.microsecond == 0:
            if receipt_date.date() == now.date():
                v_date = receipt_date.replace(hour=now.hour, minute=now.minute, second=now.second, microsecond=now.microsecond)
            else:
                v_date = receipt_date
        else:
            v_date = receipt_date
    else:
        v_date = now
    invoice_no = order.get("invoice_no") or order.get("order_no", "")

    entries = [
        {
            "ledger_id": asset_ledger_id,
            "ledger_name": asset_ledger_name,
            "narration": f"{narration_mode} for Invoice {invoice_no}",
            "debit": amount,
            "credit": 0.0,
            "user_id": user_id,
        },
        {
            "ledger_id": cust_ledger_id,
            "ledger_name": cust_ledger_name,
            "narration": f"Payment received against Invoice {invoice_no}",
            "debit": 0.0,
            "credit": amount,
            "user_id": user_id,
        }
    ]

    voucher_number, txn = generate_voucher_number(
        voucher_type="Receipt",
        voucher_mode=voucher_mode,
        voucher_date=v_date,
    )

    voucher_doc = {
        "voucher_number": voucher_number,
        "voucher_type": "Receipt",
        "voucher_mode": voucher_mode,
        "txn": txn,
        "order_id": order["_id"],
        "invoice_no": invoice_no,
        "customer_id": order.get("customer_id"),
        "date": v_date,
        "date_key": v_date.strftime("%Y-%m-%d"),
        "narration": f"Receipt against Invoice {invoice_no} via {pm}",
        "amount": amount,
        "payment_mode": pm,
        "financier_name": financier_name,
        "disbursement_status": "PENDING" if pm in ["FINANCE", "LOAN", "NBFC"] else None,
        "disbursement_bank_name": None,
        "subvention_charges": 0.0,
        "net_disbursed_amount": 0.0,
        "transaction_ref": transaction_ref,
        "cheque_no": cheque_no,
        "cheque_date": cheque_date,
        "cheque_bank": cheque_bank,
        "entries": entries,
        "notes": notes,
        "collected_by": collector_id,
        "verification_status": "PENDING" if voucher_mode == "Bank" else "VERIFIED",
        "bank_clearance_date": bank_clearance_date or (receipt_date if voucher_mode == "Bank" else v_date),
        "verified_by": None if voucher_mode == "Bank" else user_id,
        "verified_at": None if voucher_mode == "Bank" else now,
        "bounce_reason": None,
        "bounced_by": None,
        "bounced_at": None,
        "reversal_voucher_id": None,
        "reversal_voucher_number": None,
        "created_by": user_id,
        "created_at": now,
    }

    insert_kwargs = {"session": session} if session else {}
    vouchers_collection.insert_one(voucher_doc, **insert_kwargs)

    # Update order settlement state
    new_paid = round(current_paid + amount, 2)
    new_pending = round(max(0.0, bill_amount - new_paid), 2)
    new_status = "PAID" if new_pending <= 0.01 else "PARTIALLY_PAID"

    update_kwargs = {"session": session} if session else {}
    orders_collection.update_one(
        {"_id": ord_oid},
        {
            "$set": {
                "bill_amount": bill_amount,
                "paid_amount": new_paid,
                "pending_amount": new_pending,
                "payment_status": new_status,
                "last_payment_at": now,
                "updated_at": now,
            }
        },
        **update_kwargs
    )

    updated_order = orders_collection.find_one({"_id": ord_oid}, session=session)
    return voucher_doc, updated_order


# =========================================================
# 4B. CUSTOMER-LEVEL FIFO RECEIPT VOUCHER (LUMP-SUM SETTLEMENT)
# =========================================================

def record_customer_fifo_receipt_voucher(
    customer_id: str,
    payment_mode: str,
    amount: float,
    user_id: str,
    bank_account_name: Optional[str] = None,
    transaction_ref: Optional[str] = None,
    cheque_no: Optional[str] = None,
    cheque_date: Optional[str] = None,
    cheque_bank: Optional[str] = None,
    financier_name: Optional[str] = None,
    receipt_date: Optional[datetime] = None,
    bank_clearance_date: Optional[datetime] = None,
    notes: Optional[str] = None,
    collected_by_id: Optional[str] = None,
    session=None,
) -> Tuple[dict, Dict[str, Any]]:
    """
    Creates ONE single Receipt Voucher for a lump-sum customer payment,
    and automatically allocates it across open bills using FIFO (oldest first).
    Any excess amount over total pending is captured as an Advance (On Account).
    """
    cust_oid, cust = resolve_customer(customer_id, session=session)

    amount = round(float(amount), 2)
    if amount <= 0:
        raise ValueError("Receipt amount must be greater than 0")

    cust_ledger_id, cust_ledger_name = ensure_customer_ledger(str(cust_oid))
    sys_ledgers = ensure_system_ledgers()

    # Determine asset ledger based on single payment mode
    collector_id = collected_by_id or user_id
    pm = payment_mode.upper()
    if pm in ["CASH", "COD"]:
        if collector_id and to_oid(collector_id):
            asset_ledger_id, asset_ledger_name = ensure_employee_cash_ledger(collector_id)
            voucher_mode = "Cash"
            narration_mode = f"Cash collected by {asset_ledger_name}"
        else:
            asset_ledger_id, asset_ledger_name = sys_ledgers["cash"]
            voucher_mode = "Cash"
            narration_mode = "Cash collection"
    elif pm in ["UPI", "BANK", "BANK_TRANSFER", "ONLINE", "NEFT", "RTGS"]:
        asset_ledger_id, asset_ledger_name = sys_ledgers["bank"]
        if bank_account_name:
            asset_ledger_name = bank_account_name
        voucher_mode = "Bank"
        ref_text = f" (Ref: {transaction_ref})" if transaction_ref else ""
        narration_mode = f"Bank/UPI receipt{ref_text}"
    elif pm in ["CHEQUE", "DD"]:
        asset_ledger_id, asset_ledger_name = sys_ledgers["cheque"]
        voucher_mode = "Bank"
        chk_text = f" (Cheque #{cheque_no} {cheque_bank or ''})" if cheque_no else ""
        narration_mode = f"Cheque in hand{chk_text}"
    elif pm in ["FINANCE", "LOAN", "NBFC"]:
        asset_ledger_id, asset_ledger_name = sys_ledgers["finance"]
        voucher_mode = "Bank"
        fn_text = f" ({financier_name})" if financier_name else ""
        ref_text = f" (Ref: {transaction_ref})" if transaction_ref else ""
        narration_mode = f"Finance clearance{fn_text}{ref_text}"
    else:
        asset_ledger_id, asset_ledger_name = sys_ledgers["cash"]
        voucher_mode = "Cash"
        narration_mode = f"Payment collected ({payment_mode})"

    now = utc_now()
    if receipt_date:
        if receipt_date.tzinfo is None:
            receipt_date = receipt_date.replace(tzinfo=timezone.utc)
        if receipt_date.hour == 0 and receipt_date.minute == 0 and receipt_date.second == 0 and receipt_date.microsecond == 0:
            if receipt_date.date() == now.date():
                v_date = receipt_date.replace(hour=now.hour, minute=now.minute, second=now.second, microsecond=now.microsecond)
            else:
                v_date = receipt_date
        else:
            v_date = receipt_date
    else:
        v_date = now

    # Query all active open billed orders for this customer, sorted FIFO (oldest first)
    cust_identifiers = [cust_oid, str(cust_oid)]
    if cust.get("id"):
        cust_identifiers.append(cust["id"])

    query = {
        "customer_id": {"$in": cust_identifiers},
        "record_status": "active",
        "pending_amount": {"$gt": 0.01},
        "invoice_no": {"$nin": [None, ""]},
    }
    open_orders = list(orders_collection.find(query, session=session).sort([("billed_at", 1), ("created_at", 1)]))

    # Calculate customer's total outstanding due across all open bills
    total_due = round(sum(
        round(float(o.get("pending_amount", float(o.get("bill_amount", o.get("grand_total", 0.0))) - float(o.get("paid_amount", 0.0)))), 2)
        for o in open_orders
    ), 2)

    if total_due <= 0.01:
        raise ValueError(f"Customer '{cust.get('name', customer_id)}' has no outstanding dues to collect. Excess payment is not allowed.")

    if amount > total_due + 0.05:
        raise ValueError(
            f"Receipt amount (Rs.{amount:.2f}) exceeds customer's total outstanding due (Rs.{total_due:.2f}). Excess payment is not allowed."
        )

    remaining_to_allocate = amount
    allocations = []
    affected_order_ids = []

    voucher_number, txn = generate_voucher_number(
        voucher_type="Receipt",
        voucher_mode=voucher_mode,
        voucher_date=v_date,
    )

    for ord_doc in open_orders:
        if remaining_to_allocate <= 0.009:
            break

        ord_id = ord_doc["_id"]
        bill_amount = round(float(ord_doc.get("bill_amount", ord_doc.get("grand_total", 0.0))), 2)
        current_paid = round(float(ord_doc.get("paid_amount", 0.0)), 2)
        current_pending = round(float(ord_doc.get("pending_amount", bill_amount - current_paid)), 2)

        if current_pending <= 0.01:
            continue

        allocated_for_bill = min(remaining_to_allocate, current_pending)
        allocated_for_bill = round(allocated_for_bill, 2)
        remaining_to_allocate = round(remaining_to_allocate - allocated_for_bill, 2)

        new_paid = round(current_paid + allocated_for_bill, 2)
        new_pending = round(max(0.0, bill_amount - new_paid), 2)
        new_status = "PAID" if new_pending <= 0.01 else "PARTIALLY_PAID"

        # Atomically update order document
        update_kwargs = {"session": session} if session else {}
        orders_collection.update_one(
            {"_id": ord_id},
            {
                "$set": {
                    "bill_amount": bill_amount,
                    "paid_amount": new_paid,
                    "pending_amount": new_pending,
                    "payment_status": new_status,
                    "last_payment_at": now,
                    "updated_at": now,
                }
            },
            **update_kwargs
        )

        allocations.append({
            "order_id": str(ord_id),
            "order_no": ord_doc.get("order_no"),
            "invoice_no": ord_doc.get("invoice_no"),
            "billed_at": ord_doc.get("billed_at"),
            "allocated_amount": allocated_for_bill,
            "new_pending_amount": new_pending,
            "payment_status": new_status,
        })
        affected_order_ids.append(ord_id)

    advance_amount = 0.0

    # Narration details
    allocated_count = len(allocations)
    inv_list = ", ".join([a["invoice_no"] for a in allocations[:3]])
    if allocated_count > 3:
        inv_list += f" + {allocated_count - 3} more"
    narration_detail = f"FIFO settlement for {allocated_count} bill(s) [{inv_list}]"

    if notes:
        narration_detail += f" - {notes}"

    entries = [
        {
            "ledger_id": asset_ledger_id,
            "ledger_name": asset_ledger_name,
            "narration": f"{narration_mode} from {cust.get('name') or cust_ledger_name}",
            "debit": amount,
            "credit": 0.0,
            "user_id": user_id,
        },
        {
            "ledger_id": cust_ledger_id,
            "ledger_name": cust_ledger_name,
            "narration": narration_detail,
            "debit": 0.0,
            "credit": amount,
            "user_id": user_id,
        }
    ]

    voucher_doc = {
        "voucher_number": voucher_number,
        "voucher_type": "Receipt",
        "voucher_mode": voucher_mode,
        "txn": txn,
        "customer_id": cust_oid,
        "customer_custom_id": cust.get("id"),
        "affected_order_ids": affected_order_ids,
        "date": v_date,
        "date_key": v_date.strftime("%Y-%m-%d"),
        "narration": f"Receipt from {cust.get('name') or cust_ledger_name} via {pm}: {narration_detail}",
        "amount": amount,
        "payment_mode": pm,
        "financier_name": financier_name,
        "disbursement_status": "PENDING" if pm in ["FINANCE", "LOAN", "NBFC"] else None,
        "disbursement_bank_name": None,
        "subvention_charges": 0.0,
        "net_disbursed_amount": 0.0,
        "transaction_ref": transaction_ref,
        "cheque_no": cheque_no,
        "cheque_date": cheque_date,
        "cheque_bank": cheque_bank,
        "bill_allocations": allocations,
        "advance_amount": 0.0,
        "entries": entries,
        "notes": notes,
        "collected_by": collector_id,
        "verification_status": "PENDING" if voucher_mode == "Bank" else "VERIFIED",
        "bank_clearance_date": bank_clearance_date or (receipt_date if voucher_mode == "Bank" else v_date),
        "verified_by": None if voucher_mode == "Bank" else user_id,
        "verified_at": None if voucher_mode == "Bank" else now,
        "bounce_reason": None,
        "bounced_by": None,
        "bounced_at": None,
        "reversal_voucher_id": None,
        "reversal_voucher_number": None,
        "created_by": user_id,
        "created_at": now,
    }

    insert_kwargs = {"session": session} if session else {}
    vouchers_collection.insert_one(voucher_doc, **insert_kwargs)

    summary = {
        "customer_id": str(cust_oid),
        "custom_id": cust.get("id"),
        "customer_name": cust.get("name") or cust.get("company_name") or cust_ledger_name,
        "voucher_number": voucher_number,
        "total_received": amount,
        "allocated_to_bills": amount,
        "advance_amount": 0.0,
        "remaining_customer_due": round(max(0.0, total_due - amount), 2),
        "bills_affected_count": len(allocations),
        "bills_fully_paid": sum(1 for a in allocations if a["payment_status"] == "PAID"),
        "bills_partially_paid": sum(1 for a in allocations if a["payment_status"] == "PARTIALLY_PAID"),
        "allocations": allocations,
    }

    return voucher_doc, summary


# =========================================================
# 5. CUSTOMER DPD & DEBTORS AGING ENGINE
# =========================================================

def calculate_customer_aging(customer_id: str) -> Dict[str, Any]:
    """
    Calculates Bill-Wise Days Past Due (DPD) and Debtors Aging Buckets
    for all open/partially-paid invoices of a customer.
    Raises ValueError(f"Customer not found: {customer_id}") if missing.
    """
    cust_oid, cust = resolve_customer(customer_id)
    
    # Query all active billed orders with pending amount > 0 for this customer
    cust_identifiers = [cust_oid, str(cust_oid)]
    if cust.get("id"):
        cust_identifiers.append(cust["id"])

    query = {
        "customer_id": {"$in": cust_identifiers},
        "record_status": "active",
        "pending_amount": {"$gt": 0.01},
        "invoice_no": {"$nin": [None, ""]},
    }

    open_orders = list(orders_collection.find(query).sort("billed_at", 1))
    now = utc_now()

    total_outstanding = 0.0
    total_overdue = 0.0
    max_dpd = 0
    overdue_weight_sum = 0.0

    buckets = {
        "current": 0.0,      # DPD = 0 (Not Due)
        "days_1_30": 0.0,    # 1 - 30 DPD
        "days_31_60": 0.0,   # 31 - 60 DPD
        "days_61_90": 0.0,   # 61 - 90 DPD
        "days_90_plus": 0.0, # 90+ DPD
    }

    open_bills = []

    for ord_doc in open_orders:
        bill_amount = round(float(ord_doc.get("bill_amount", ord_doc.get("grand_total", 0.0))), 2)
        paid_amount = round(float(ord_doc.get("paid_amount", 0.0)), 2)
        pending = round(float(ord_doc.get("pending_amount", bill_amount - paid_amount)), 2)

        if pending <= 0.01:
            continue

        total_outstanding += pending

        # Determine due date
        due_date = ord_doc.get("due_date")
        if not due_date:
            billed_at = ord_doc.get("billed_at") or ord_doc.get("created_at") or now
            credit_days = int(ord_doc.get("credit_days") or (cust.get("credit_days") if cust else 7) or 7)
            due_date = billed_at + timedelta(days=credit_days)

        # Make sure due_date is timezone-aware
        if due_date.tzinfo is None:
            due_date = due_date.replace(tzinfo=timezone.utc)

        # Calculate DPD
        diff_days = (now.date() - due_date.date()).days
        dpd = max(0, diff_days)

        if dpd > 0:
            total_overdue += pending
            overdue_weight_sum += (pending * dpd)
            if dpd > max_dpd:
                max_dpd = dpd

        # Classify into aging bucket
        if dpd == 0:
            buckets["current"] += pending
        elif 1 <= dpd <= 30:
            buckets["days_1_30"] += pending
        elif 31 <= dpd <= 60:
            buckets["days_31_60"] += pending
        elif 61 <= dpd <= 90:
            buckets["days_61_90"] += pending
        else:
            buckets["days_90_plus"] += pending

        open_bills.append({
            "order_id": str(ord_doc["_id"]),
            "order_no": ord_doc.get("order_no"),
            "invoice_no": ord_doc.get("invoice_no"),
            "billed_at": ord_doc.get("billed_at"),
            "due_date": due_date,
            "bill_amount": bill_amount,
            "paid_amount": paid_amount,
            "pending_amount": pending,
            "dpd": dpd,
            "is_overdue": dpd > 0,
        })

    weighted_dpd = round(overdue_weight_sum / total_overdue, 1) if total_overdue > 0 else 0.0

    return {
        "customer_id": str(cust["_id"]),
        "custom_id": cust.get("id"),
        "customer_name": cust.get("name") or cust.get("company_name"),
        "phone": cust.get("mobile") or cust.get("phone"),
        "credit_limit": float(cust.get("credit_limit", 0.0)) if cust else 0.0,
        "credit_days": int(cust.get("credit_days", 7)) if cust else 7,
        "total_outstanding": round(total_outstanding, 2),
        "total_overdue": round(total_overdue, 2),
        "max_dpd": max_dpd,
        "weighted_dpd": weighted_dpd,
        "open_invoices_count": len(open_bills),
        "aging_buckets": {k: round(v, 2) for k, v in buckets.items()},
        "open_bills": open_bills,
    }


def get_due_customers_aging_list(
    search: Optional[str] = None,
    branch_id: Optional[str] = None,
    assigned_employee_id: Optional[str] = None,
    aging_bucket: Optional[str] = None,
    is_overdue: Optional[bool] = None,
    min_due: Optional[float] = None,
    max_due: Optional[float] = None,
    sort_by: str = "total_outstanding",
    sort_order: str = "desc",
    page: int = 1,
    limit: int = 20,
    include_bills: bool = False,
) -> Dict[str, Any]:
    """
    Computes bill-wise aging and aggregate overdue metrics for all customers
    who currently have pending dues on sales orders (pending_amount > 0.01).
    Supports comprehensive filtering (search, branch, sales agent, aging bucket, overdue status),
    customizable sorting, and pagination with portfolio summary KPIs.
    """
    now = utc_now()

    # 1. Fetch active open billed sale orders with pending amount > 0.01
    order_query: Dict[str, Any] = {
        "type": "sale",
        "record_status": "active",
        "pending_amount": {"$gt": 0.01},
        "invoice_no": {"$nin": [None, ""]},
    }

    open_orders = list(orders_collection.find(order_query).sort("billed_at", 1))

    # 2. Group orders by raw customer identifier
    customer_orders_map = defaultdict(list)
    unique_cust_keys = set()
    for ord_doc in open_orders:
        cid = ord_doc.get("customer_id")
        if cid:
            customer_orders_map[str(cid)].append(ord_doc)
            unique_cust_keys.add(str(cid))

    if not unique_cust_keys:
        return {
            "summary": {
                "total_customers_with_dues": 0,
                "total_receivables": 0.0,
                "total_overdue": 0.0,
                "total_open_invoices": 0,
                "portfolio_aging_buckets": {
                    "current": 0.0,
                    "days_1_30": 0.0,
                    "days_31_60": 0.0,
                    "days_61_90": 0.0,
                    "days_90_plus": 0.0,
                },
            },
            "pagination": {
                "page": page,
                "limit": limit,
                "total_customers": 0,
                "total_pages": 1,
                "has_next": False,
                "has_previous": False,
            },
            "data": [],
        }

    # 3. Batch fetch customer profiles for fast lookup
    oids = [ObjectId(k) for k in unique_cust_keys if ObjectId.is_valid(k)]
    custom_ids = [k for k in unique_cust_keys if not ObjectId.is_valid(k)]

    query_parts = []
    if oids:
        query_parts.append({"_id": {"$in": oids}})
    if custom_ids:
        query_parts.append({"id": {"$in": custom_ids}})

    customer_lookup = {}
    if query_parts:
        cust_docs = list(customers_collection.find({"$or": query_parts}))
        for c in cust_docs:
            customer_lookup[str(c["_id"])] = c
            if c.get("id"):
                customer_lookup[c["id"]] = c

    # 4. Consolidate orders under canonical customer _id (or raw key if unregistered)
    canonical_customer_orders = defaultdict(list)
    for cid_str, orders in customer_orders_map.items():
        cust = customer_lookup.get(cid_str)
        canonical_id = str(cust["_id"]) if cust else cid_str
        canonical_customer_orders[canonical_id].extend(orders)

    # 5. Compute aging buckets and DPD metrics for each customer
    computed_customers = []

    for can_id, orders in canonical_customer_orders.items():
        cust = customer_lookup.get(can_id)

        total_outstanding = 0.0
        total_overdue = 0.0
        max_dpd = 0
        overdue_weight_sum = 0.0

        buckets = {
            "current": 0.0,
            "days_1_30": 0.0,
            "days_31_60": 0.0,
            "days_61_90": 0.0,
            "days_90_plus": 0.0,
        }

        bills_list = []
        oldest_bill_date = None
        oldest_due_date = None

        for ord_doc in orders:
            bill_amount = round(float(ord_doc.get("bill_amount", ord_doc.get("grand_total", 0.0))), 2)
            paid_amount = round(float(ord_doc.get("paid_amount", 0.0)), 2)
            pending = round(float(ord_doc.get("pending_amount", bill_amount - paid_amount)), 2)

            if pending <= 0.01:
                continue

            total_outstanding += pending

            billed_at = ord_doc.get("billed_at") or ord_doc.get("created_at") or now
            if billed_at.tzinfo is None:
                billed_at = billed_at.replace(tzinfo=timezone.utc)

            if oldest_bill_date is None or billed_at < oldest_bill_date:
                oldest_bill_date = billed_at

            due_date = ord_doc.get("due_date")
            if not due_date:
                credit_days = int(ord_doc.get("credit_days") or (cust.get("credit_days") if cust else 7) or 7)
                due_date = billed_at + timedelta(days=credit_days)

            if due_date.tzinfo is None:
                due_date = due_date.replace(tzinfo=timezone.utc)

            if oldest_due_date is None or due_date < oldest_due_date:
                oldest_due_date = due_date

            diff_days = (now.date() - due_date.date()).days
            dpd = max(0, diff_days)

            if dpd > 0:
                total_overdue += pending
                overdue_weight_sum += (pending * dpd)
                if dpd > max_dpd:
                    max_dpd = dpd

            if dpd == 0:
                buckets["current"] += pending
            elif 1 <= dpd <= 30:
                buckets["days_1_30"] += pending
            elif 31 <= dpd <= 60:
                buckets["days_31_60"] += pending
            elif 61 <= dpd <= 90:
                buckets["days_61_90"] += pending
            else:
                buckets["days_90_plus"] += pending

            bills_list.append({
                "order_id": str(ord_doc["_id"]),
                "order_no": ord_doc.get("order_no"),
                "invoice_no": ord_doc.get("invoice_no"),
                "billed_at": billed_at,
                "due_date": due_date,
                "bill_amount": bill_amount,
                "paid_amount": paid_amount,
                "pending_amount": pending,
                "dpd": dpd,
                "is_overdue": dpd > 0,
            })

        if total_outstanding <= 0.01:
            continue

        weighted_dpd = round(overdue_weight_sum / total_overdue, 1) if total_overdue > 0 else 0.0

        cust_rec = {
            "customer_id": can_id,
            "custom_id": cust.get("id") if cust else None,
            "customer_name": (cust.get("name") if cust else None) or (cust.get("company_name") if cust else None) or f"Customer {can_id[:8]}",
            "company_name": cust.get("company_name") if cust else None,
            "phone": (cust.get("mobile") if cust else None) or (cust.get("phone") if cust else None),
            "branch_id": str(cust.get("branch_id")) if (cust and cust.get("branch_id")) else None,
            "assigned_employee_id": str(cust.get("assigned_employee_id")) if (cust and cust.get("assigned_employee_id")) else None,
            "credit_limit": float(cust.get("credit_limit", 0.0)) if cust else 0.0,
            "credit_days": int(cust.get("credit_days", 7)) if cust else 7,
            "total_outstanding": round(total_outstanding, 2),
            "total_overdue": round(total_overdue, 2),
            "max_dpd": max_dpd,
            "weighted_dpd": weighted_dpd,
            "open_invoices_count": len(bills_list),
            "oldest_bill_date": oldest_bill_date,
            "oldest_due_date": oldest_due_date,
            "aging_buckets": {k: round(v, 2) for k, v in buckets.items()},
        }
        if include_bills:
            cust_rec["open_bills"] = bills_list

        computed_customers.append(cust_rec)

    # 6. Apply Filters
    filtered_customers = computed_customers

    if branch_id:
        b_str = str(branch_id).strip()
        filtered_customers = [c for c in filtered_customers if c.get("branch_id") == b_str]

    if assigned_employee_id:
        e_str = str(assigned_employee_id).strip()
        filtered_customers = [c for c in filtered_customers if c.get("assigned_employee_id") == e_str]

    if search:
        s = search.strip().lower()
        filtered_customers = [
            c for c in filtered_customers
            if (
                s in (c.get("customer_name") or "").lower()
                or s in (c.get("custom_id") or "").lower()
                or s in (c.get("company_name") or "").lower()
                or s in (c.get("phone") or "").lower()
            )
        ]

    if is_overdue is not None:
        if is_overdue:
            filtered_customers = [c for c in filtered_customers if c["total_overdue"] > 0.01]
        else:
            filtered_customers = [c for c in filtered_customers if c["total_overdue"] <= 0.01]

    if min_due is not None:
        filtered_customers = [c for c in filtered_customers if c["total_outstanding"] >= float(min_due)]

    if max_due is not None:
        filtered_customers = [c for c in filtered_customers if c["total_outstanding"] <= float(max_due)]

    if aging_bucket:
        bucket_key = aging_bucket.strip().lower()
        if bucket_key == "overdue":
            filtered_customers = [c for c in filtered_customers if c["total_overdue"] > 0.01]
        elif bucket_key in ["current", "days_1_30", "days_31_60", "days_61_90", "days_90_plus"]:
            filtered_customers = [c for c in filtered_customers if c["aging_buckets"].get(bucket_key, 0.0) > 0.01]

    # 7. Calculate Portfolio Summary for the filtered cohort
    total_customers_count = len(filtered_customers)
    total_receivables = round(sum(c["total_outstanding"] for c in filtered_customers), 2)
    total_overdue = round(sum(c["total_overdue"] for c in filtered_customers), 2)
    total_open_invoices = sum(c["open_invoices_count"] for c in filtered_customers)

    portfolio_buckets = {
        "current": round(sum(c["aging_buckets"]["current"] for c in filtered_customers), 2),
        "days_1_30": round(sum(c["aging_buckets"]["days_1_30"] for c in filtered_customers), 2),
        "days_31_60": round(sum(c["aging_buckets"]["days_31_60"] for c in filtered_customers), 2),
        "days_61_90": round(sum(c["aging_buckets"]["days_61_90"] for c in filtered_customers), 2),
        "days_90_plus": round(sum(c["aging_buckets"]["days_90_plus"] for c in filtered_customers), 2),
    }

    # 8. Sort
    reverse_sort = (sort_order.lower() != "asc")
    if sort_by == "total_overdue":
        filtered_customers.sort(key=lambda x: x["total_overdue"], reverse=reverse_sort)
    elif sort_by == "max_dpd":
        filtered_customers.sort(key=lambda x: x["max_dpd"], reverse=reverse_sort)
    elif sort_by == "customer_name":
        filtered_customers.sort(key=lambda x: (x["customer_name"] or "").lower(), reverse=reverse_sort)
    elif sort_by == "oldest_due_date":
        filtered_customers.sort(
            key=lambda x: x["oldest_due_date"] or (datetime.min.replace(tzinfo=timezone.utc) if reverse_sort else datetime.max.replace(tzinfo=timezone.utc)),
            reverse=reverse_sort
        )
    else:  # default "total_outstanding"
        filtered_customers.sort(key=lambda x: x["total_outstanding"], reverse=reverse_sort)

    # 9. Paginate
    limit = max(1, min(int(limit), 100))
    page = max(1, int(page))
    total_pages = (total_customers_count + limit - 1) // limit if total_customers_count > 0 else 1
    start_idx = (page - 1) * limit
    paginated_data = filtered_customers[start_idx: start_idx + limit]

    return {
        "summary": {
            "total_customers_with_dues": total_customers_count,
            "total_receivables": total_receivables,
            "total_overdue": total_overdue,
            "total_open_invoices": total_open_invoices,
            "portfolio_aging_buckets": portfolio_buckets,
        },
        "pagination": {
            "page": page,
            "limit": limit,
            "total_customers": total_customers_count,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_previous": page > 1,
        },
        "data": paginated_data,
    }


# =========================================================
# 6. CUSTOMER STATEMENT (PARTY LEDGER) ENGINE
# =========================================================

def get_customer_statement(
    customer_id: str,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Generates a full running-balance Customer Statement (Khata Ledger).
    Returns opening balance, chronological transactions, and closing balance.
    Raises ValueError(f"Customer not found: {customer_id}") if missing.
    """
    cust_oid, cust = resolve_customer(customer_id)
    cust_ledger_id, cust_ledger_name = ensure_customer_ledger(str(cust_oid))

    # 1. Compute Opening Balance prior to from_date
    opening_balance = 0.0
    if from_date:
        if from_date.tzinfo is None:
            from_date = from_date.replace(tzinfo=timezone.utc)

        pre_vouchers = vouchers_collection.find({
            "entries.ledger_id": cust_ledger_id,
            "date": {"$lt": from_date}
        })
        for v in pre_vouchers:
            for entry in v.get("entries", []):
                if str(entry.get("ledger_id")) == cust_ledger_id:
                    opening_balance += (float(entry.get("debit", 0.0)) - float(entry.get("credit", 0.0)))

    # 2. Query period transactions
    query = {"entries.ledger_id": cust_ledger_id}
    date_filter = {}
    if from_date:
        date_filter["$gte"] = from_date
    if to_date:
        if to_date.tzinfo is None:
            to_date = to_date.replace(tzinfo=timezone.utc)
        date_filter["$lte"] = to_date
    if date_filter:
        query["date"] = date_filter

    period_vouchers = list(vouchers_collection.find(query).sort([("date", 1), ("created_at", 1)]))

    def get_effective_datetime(v):
        v_date = v.get("date")
        if isinstance(v_date, str):
            try:
                v_date = datetime.fromisoformat(v_date.replace("Z", "+00:00"))
            except Exception:
                v_date = None

        c_at = v.get("created_at")
        if isinstance(c_at, str):
            try:
                c_at = datetime.fromisoformat(c_at.replace("Z", "+00:00"))
            except Exception:
                c_at = None

        if not isinstance(v_date, datetime):
            return c_at if isinstance(c_at, datetime) else datetime.min

        # If v_date is date-only (00:00:00) without explicit time
        if v_date.hour == 0 and v_date.minute == 0 and v_date.second == 0 and v_date.microsecond == 0:
            if isinstance(c_at, datetime) and c_at.date() == v_date.date():
                # Created on the same day: use created_at time
                return v_date.replace(hour=c_at.hour, minute=c_at.minute, second=c_at.second, microsecond=c_at.microsecond)
            elif v.get("voucher_type") in ["Receipt", "Payment"]:
                # Backdated receipt without time: place at end of day so it settles bills of that day
                return v_date.replace(hour=23, minute=59, second=59)

        return v_date

    def statement_sort_key(v):
        eff_dt = get_effective_datetime(v)

        # Tie-breaker when timestamps are identical on the same date:
        # 10: Sales / Billing (Invoices create the receivable)
        # 20: Receipts / Payments (Settlements credit the receivable)
        # 30: Reversals / Dishonors (Undo a receipt, so must come after the receipt)
        if v.get("voucher_mode") == "Reversal" or v.get("original_voucher_id"):
            tie_prio = 30
        else:
            v_t = (v.get("voucher_type") or "").upper()
            if v_t in ["SALES", "PURCHASE_RETURN", "DEBIT_NOTE"]:
                tie_prio = 10
            elif v_t in ["SALES RETURN", "SALES_RETURN", "SALE RETURN", "CREDIT NOTE", "CREDIT_NOTE"]:
                tie_prio = 15
            elif v_t in ["RECEIPT", "PAYMENT"]:
                tie_prio = 20
            else:
                tie_prio = 25

        c_at = v.get("created_at")
        if isinstance(c_at, str):
            try:
                c_at = datetime.fromisoformat(c_at.replace("Z", "+00:00"))
            except Exception:
                c_at = None
        if not isinstance(c_at, datetime):
            c_at = eff_dt

        return (eff_dt, tie_prio, c_at)

    period_vouchers.sort(key=statement_sort_key)

    running_balance = round(opening_balance, 2)
    transactions = []
    total_debit = 0.0
    total_credit = 0.0

    for v in period_vouchers:
        for entry in v.get("entries", []):
            if str(entry.get("ledger_id")) == cust_ledger_id:
                deb = round(float(entry.get("debit", 0.0)), 2)
                cred = round(float(entry.get("credit", 0.0)), 2)
                running_balance = round(running_balance + deb - cred, 2)
                total_debit += deb
                total_credit += cred

                transactions.append({
                    "voucher_id": str(v["_id"]),
                    "voucher_number": v.get("voucher_number"),
                    "voucher_type": v.get("voucher_type"),
                    "voucher_mode": v.get("voucher_mode"),
                    "payment_mode": v.get("payment_mode"),
                    "date": v.get("date"),
                    "invoice_no": v.get("invoice_no"),
                    "particulars": entry.get("narration") or v.get("narration"),
                    "debit": deb,
                    "credit": cred,
                    "running_balance": abs(running_balance),
                    "balance_type": "Dr" if running_balance >= 0 else "Cr",
                })

    closing_balance = running_balance

    return {
        "customer": {
            "id": str(cust["_id"]),
            "custom_id": cust.get("id"),
            "name": cust.get("name") or cust.get("company_name") or cust_ledger_name,
            "phone": cust.get("mobile") or cust.get("phone"),
            "ledger_id": cust_ledger_id,
            "credit_limit": float(cust.get("credit_limit", 0.0)) if cust else 0.0,
            "credit_days": int(cust.get("credit_days", 7)) if cust else 7,
        },
        "from_date": from_date,
        "to_date": to_date,
        "opening_balance": abs(round(opening_balance, 2)),
        "opening_balance_type": "Dr" if opening_balance >= 0 else "Cr",
        "transactions": transactions,
        "total_debit": round(total_debit, 2),
        "total_credit": round(total_credit, 2),
        "closing_balance": abs(round(closing_balance, 2)),
        "closing_balance_type": "Dr" if closing_balance >= 0 else "Cr",
    }


# =========================================================
# 6. UNIVERSAL EMPLOYEE CASH CUSTODY, TRACKING & HANDOVERS
# =========================================================

def get_employee_cash_balance(user_id: str) -> Dict[str, Any]:
    """
    Calculates the real-time cash balance held in physical custody by an employee.
    Cash custody ledger is under 'Cash-in-Hand' (ASSETS).
    Net cash in hand = Opening Balance (Dr) + Total Debits (Receipts) - Total Credits (Handovers/Deposits).
    """
    u_oid = to_oid(user_id)
    user = users_collection.find_one({"_id": u_oid}) if u_oid else None

    emp_ledger_id, emp_ledger_name = ensure_employee_cash_ledger(user_id)
    led_oid = to_oid(emp_ledger_id)
    ledger = ledgers_collection.find_one({"_id": led_oid})
    opening_bal = float(ledger.get("opening_balance", 0.0)) if ledger else 0.0

    # Query vouchers where this employee cash ledger was touched
    query = {"entries.ledger_id": emp_ledger_id}
    vouchers = list(vouchers_collection.find(query).sort([("date", -1), ("created_at", -1)]))

    total_debit = 0.0   # Cash collected into hand
    total_credit = 0.0  # Cash handed over / deposited
    recent_transactions = []

    for v in vouchers:
        for entry in v.get("entries", []):
            if str(entry.get("ledger_id")) == emp_ledger_id:
                deb = round(float(entry.get("debit", 0.0)), 2)
                cred = round(float(entry.get("credit", 0.0)), 2)
                total_debit += deb
                total_credit += cred

                if len(recent_transactions) < 30:
                    recent_transactions.append({
                        "voucher_id": str(v["_id"]),
                        "voucher_number": v.get("voucher_number"),
                        "voucher_type": v.get("voucher_type"),
                        "voucher_mode": v.get("voucher_mode"),
                        "date": v.get("date"),
                        "narration": entry.get("narration") or v.get("narration"),
                        "cash_received": deb,
                        "cash_handed_over": cred,
                    })

    current_balance = round(opening_bal + total_debit - total_credit, 2)

    return {
        "employee_id": user_id,
        "employee_name": (user.get("name") if user else None) or emp_ledger_name.replace("Employee Cash - ", ""),
        "role": user.get("role") if user else None,
        "phone": user.get("phone") if user else None,
        "ledger_id": emp_ledger_id,
        "ledger_name": emp_ledger_name,
        "opening_balance": opening_bal,
        "total_cash_collected": round(total_debit, 2),
        "total_cash_handed_over": round(total_credit, 2),
        "current_cash_in_hand": current_balance,
        "recent_transactions": recent_transactions,
    }


def get_all_employees_cash_balances() -> List[Dict[str, Any]]:
    """
    Returns an admin dashboard view of physical cash held across ALL staff members
    (delivery drivers, collection associates, sales executives, counter cashiers).
    """
    query = {
        "group_name": "Cash-in-Hand",
        "$or": [
            {"ledger_name": {"$regex": "^Employee Cash -"}},
            {"employee_id": {"$exists": True, "$ne": None}},
        ]
    }
    emp_ledgers = list(ledgers_collection.find(query))
    if not emp_ledgers:
        return []

    ledger_ids = [str(l["_id"]) for l in emp_ledgers]
    ledger_map = {str(l["_id"]): l for l in emp_ledgers}

    pipeline = [
        {"$unwind": "$entries"},
        {"$match": {"entries.ledger_id": {"$in": ledger_ids}}},
        {
            "$group": {
                "_id": "$entries.ledger_id",
                "total_debit": {"$sum": "$entries.debit"},
                "total_credit": {"$sum": "$entries.credit"},
                "last_txn_date": {"$max": "$date"},
            }
        }
    ]
    agg_results = list(vouchers_collection.aggregate(pipeline))
    agg_map = {res["_id"]: res for res in agg_results}

    emp_user_ids = [l.get("employee_id") for l in emp_ledgers if l.get("employee_id")]
    user_map = {str(u["_id"]): u for u in users_collection.find({"_id": {"$in": emp_user_ids}})} if emp_user_ids else {}

    results = []
    for led_id, ledger in ledger_map.items():
        emp_oid = ledger.get("employee_id")
        user_doc = user_map.get(str(emp_oid)) if emp_oid else None

        agg = agg_map.get(led_id, {})
        tot_deb = round(float(agg.get("total_debit", 0.0)), 2)
        tot_cred = round(float(agg.get("total_credit", 0.0)), 2)
        opening = round(float(ledger.get("opening_balance", 0.0)), 2)
        balance = round(opening + tot_deb - tot_cred, 2)

        name = (user_doc.get("name") if user_doc else None) or ledger.get("ledger_name", "").replace("Employee Cash - ", "")

        results.append({
            "employee_id": str(emp_oid) if emp_oid else None,
            "employee_name": name,
            "role": user_doc.get("role") if user_doc else None,
            "phone": user_doc.get("phone") if user_doc else None,
            "ledger_id": led_id,
            "ledger_name": ledger.get("ledger_name"),
            "opening_balance": opening,
            "total_collected": tot_deb,
            "total_handed_over": tot_cred,
            "current_cash_in_hand": balance,
            "last_activity_date": agg.get("last_txn_date"),
        })

    results.sort(key=lambda x: x["current_cash_in_hand"], reverse=True)
    return results


def record_employee_cash_handover(
    employee_id: str,
    amount: float,
    handover_to: str,  # "SAFE" or "BANK"
    handled_by_user_id: str,
    bank_account_name: Optional[str] = None,
    transaction_ref: Optional[str] = None,
    handover_date: Optional[datetime] = None,
    notes: Optional[str] = None,
    session=None,
) -> Tuple[dict, Dict[str, Any]]:
    """
    Creates a Contra Voucher (CTV) recording physical cash handover by an associate.
    Destination options:
      1. "SAFE": Cash deposited into company main cash box / safe ("Cash in Hand").
      2. "BANK": Cash directly deposited by employee into company bank account (Bank CDM / branch).
    Validation:
      Ensures amount does not exceed the employee's current cash in hand custody.
    Double-Entry:
      Dr Destination Account (Safe Cash / Bank Account)
      Cr Employee Cash Custody Account
    """
    amount = round(float(amount), 2)
    if amount <= 0:
        raise ValueError("Handover amount must be greater than 0")

    u_oid = to_oid(employee_id)
    user = users_collection.find_one({"_id": u_oid}, session=session) if u_oid else None
    if not user and not u_oid:
        raise ValueError(f"Invalid employee ID: {employee_id}")

    emp_name = (user.get("name") if user else None) or f"Staff {employee_id}"
    emp_ledger_id, emp_ledger_name = ensure_employee_cash_ledger(employee_id, emp_name)

    # Check employee's current cash custody balance
    cash_summary = get_employee_cash_balance(employee_id)
    current_cash = cash_summary["current_cash_in_hand"]

    if amount > current_cash + 0.05:
        raise ValueError(
            f"Handover amount (Rs.{amount:.2f}) exceeds employee's current cash in hand (Rs.{current_cash:.2f})."
        )

    sys_ledgers = ensure_system_ledgers()
    target_type = handover_to.upper()

    if target_type == "SAFE":
        dest_ledger_id, dest_ledger_name = sys_ledgers["cash"]
        voucher_mode = "Cash"
        dest_label = "Company Safe (Cash in Hand)"
        action_note = "Safe handover"
    elif target_type in ["BANK", "BANK_DEPOSIT", "CDM"]:
        dest_ledger_id, dest_ledger_name = sys_ledgers["bank"]
        if bank_account_name:
            dest_ledger_name = bank_account_name
        voucher_mode = "Bank"
        ref_txt = f" (Ref/Slip: {transaction_ref})" if transaction_ref else ""
        dest_label = f"Bank Deposit: {dest_ledger_name}{ref_txt}"
        action_note = f"Bank CDM deposit{ref_txt}"
    else:
        raise ValueError(f"Invalid handover_to destination: '{handover_to}'. Must be 'SAFE' or 'BANK'.")

    now = utc_now()
    v_date = handover_date or now

    voucher_number, txn = generate_voucher_number(
        voucher_type="Contra",
        voucher_mode=voucher_mode,
        voucher_date=v_date,
    )

    narration = f"Cash handover: {emp_ledger_name} -> {dest_label}"
    if notes:
        narration += f" - {notes}"

    entries = [
        {
            "ledger_id": dest_ledger_id,
            "ledger_name": dest_ledger_name,
            "narration": f"Cash received from {emp_name} ({notes or action_note})",
            "debit": amount,
            "credit": 0.0,
            "user_id": handled_by_user_id,
        },
        {
            "ledger_id": emp_ledger_id,
            "ledger_name": emp_ledger_name,
            "narration": f"Cash handed over to {dest_label} ({notes or action_note})",
            "debit": 0.0,
            "credit": amount,
            "user_id": handled_by_user_id,
        },
    ]

    voucher_doc = {
        "voucher_number": voucher_number,
        "voucher_type": "Contra",
        "voucher_mode": voucher_mode,
        "txn": txn,
        "employee_id": u_oid,
        "employee_name": emp_name,
        "handover_to": target_type,
        "transaction_ref": transaction_ref,
        "date": v_date,
        "date_key": v_date.strftime("%Y-%m-%d"),
        "narration": narration,
        "amount": amount,
        "entries": entries,
        "created_by": handled_by_user_id,
        "created_at": now,
    }

    insert_kwargs = {"session": session} if session else {}
    vouchers_collection.insert_one(voucher_doc, **insert_kwargs)

    remaining_cash = round(current_cash - amount, 2)

    summary = {
        "voucher_number": voucher_number,
        "employee_id": employee_id,
        "employee_name": emp_name,
        "amount_handed_over": amount,
        "handover_destination": target_type,
        "destination_ledger": dest_ledger_name,
        "previous_cash_in_hand": current_cash,
        "remaining_cash_in_hand": remaining_cash,
        "handover_date": v_date,
    }

    return voucher_doc, summary


# =========================================================
# 7. BANK VOUCHER RECONCILIATION & DISHONOR / BOUNCE ENGINE
# =========================================================

def receive_cheque_voucher(
    voucher_id: str,
    user_id: str,
    notes: Optional[str] = None,
    session=None,
) -> dict:
    """
    Transitions a Cheque receipt voucher from PENDING -> RECEIVED.
    Executed by office cashier/receptionist upon taking physical custody of the cheque leaf from the driver.
    """
    v_oid = to_oid(voucher_id)
    voucher = vouchers_collection.find_one({"_id": v_oid}, session=session)
    if not voucher:
        raise ValueError(f"Voucher not found: {voucher_id}")

    if voucher.get("voucher_type") != "Receipt":
        raise ValueError("Only Receipt Vouchers can be processed as cheques")

    pm = (voucher.get("payment_mode") or "").upper()
    if pm not in ["CHEQUE", "DD"]:
        raise ValueError(f"Voucher payment mode is '{pm}'. Only CHEQUE / DD can be received in office.")

    cur_status = voucher.get("verification_status")
    if cur_status == "RECEIVED":
        raise ValueError("Cheque has already been received in office safe")
    if cur_status in ["DEPOSITED", "VERIFIED", "BOUNCED"]:
        raise ValueError(f"Cannot mark as received: Cheque is already in '{cur_status}' status")

    now = utc_now()
    update_data = {
        "verification_status": "RECEIVED",
        "received_by": user_id,
        "received_at": now,
        "updated_at": now,
    }
    if notes:
        existing_notes = voucher.get("notes") or ""
        update_data["notes"] = f"{existing_notes} | Office Received: {notes}".strip(" | ")

    update_kwargs = {"session": session} if session else {}
    vouchers_collection.update_one({"_id": v_oid}, {"$set": update_data}, **update_kwargs)
    return vouchers_collection.find_one({"_id": v_oid}, session=session)


def deposit_cheque_voucher(
    voucher_id: str,
    user_id: str,
    deposit_bank_account_name: Optional[str] = None,
    deposit_date: Optional[datetime] = None,
    deposit_slip_ref: Optional[str] = None,
    notes: Optional[str] = None,
    session=None,
) -> dict:
    """
    Transitions a Cheque receipt voucher from RECEIVED (or PENDING) -> DEPOSITED.
    Executed by accountant when preparing bank deposit slip and depositing cheque in bank.
    """
    v_oid = to_oid(voucher_id)
    voucher = vouchers_collection.find_one({"_id": v_oid}, session=session)
    if not voucher:
        raise ValueError(f"Voucher not found: {voucher_id}")

    if voucher.get("voucher_type") != "Receipt":
        raise ValueError("Only Receipt Vouchers can be processed as cheques")

    pm = (voucher.get("payment_mode") or "").upper()
    if pm not in ["CHEQUE", "DD"]:
        raise ValueError(f"Voucher payment mode is '{pm}'. Only CHEQUE / DD can be deposited into bank.")

    cur_status = voucher.get("verification_status")
    if cur_status == "DEPOSITED":
        raise ValueError("Cheque is already marked as deposited in bank")
    if cur_status in ["VERIFIED", "BOUNCED"]:
        raise ValueError(f"Cannot deposit: Cheque is already in '{cur_status}' status")

    now = utc_now()
    dep_date = deposit_date or now
    if isinstance(dep_date, str):
        try:
            dep_date = datetime.fromisoformat(dep_date)
        except Exception:
            pass

    bank_name = deposit_bank_account_name or SYS_DEFAULT_BANK
    update_data = {
        "verification_status": "DEPOSITED",
        "deposit_bank_name": bank_name,
        "deposit_date": dep_date,
        "deposit_slip_ref": deposit_slip_ref,
        "deposited_by": user_id,
        "deposited_at": now,
        "updated_at": now,
    }
    if notes:
        existing_notes = voucher.get("notes") or ""
        update_data["notes"] = f"{existing_notes} | Deposited: {notes}".strip(" | ")

    update_kwargs = {"session": session} if session else {}
    vouchers_collection.update_one({"_id": v_oid}, {"$set": update_data}, **update_kwargs)
    return vouchers_collection.find_one({"_id": v_oid}, session=session)


def verify_receipt_voucher(
    voucher_id: str,
    user_id: str,
    bank_clearance_date: Optional[datetime] = None,
    notes: Optional[str] = None,
    session=None,
) -> dict:
    """
    Marks a pending Receipt Voucher as VERIFIED after the accountant
    matches the UTR / transaction against the bank statement.
    Optionally records bank_clearance_date (if payment cleared on a past date).
    """
    v_oid = to_oid(voucher_id)
    voucher = vouchers_collection.find_one({"_id": v_oid}, session=session)
    if not voucher:
        raise ValueError(f"Voucher not found: {voucher_id}")

    if voucher.get("voucher_type") != "Receipt":
        raise ValueError("Only Receipt Vouchers can be verified against bank statements")

    current_status = voucher.get("verification_status", "VERIFIED")
    if current_status == "BOUNCED":
        raise ValueError("Cannot verify a bounced/dishonored voucher")
    if current_status == "VERIFIED" and not bank_clearance_date and not notes:
        raise ValueError("Voucher is already verified. Pass a new bank_clearance_date to update clearance date.")

    now = utc_now()
    clearance_dt = bank_clearance_date or voucher.get("bank_clearance_date") or voucher.get("date") or now
    if isinstance(clearance_dt, str):
        try:
            clearance_dt = datetime.fromisoformat(clearance_dt)
        except Exception:
            pass

    update_data = {
        "verification_status": "VERIFIED",
        "bank_clearance_date": clearance_dt,
        "verified_by": user_id,
        "verified_at": now,
        "updated_at": now,
    }
    if notes:
        existing_notes = voucher.get("notes") or ""
        update_data["notes"] = f"{existing_notes} | Verified: {notes}".strip(" | ")

    # For Cheque / DD receipts initially sitting in Cheques in Hand:
    # Automatically generate a Contra Voucher (CTV) transferring funds: Cheques in Hand -> Bank Account
    pm = (voucher.get("payment_mode") or "").upper()
    if pm in ["CHEQUE", "DD"] and not voucher.get("clearance_contra_voucher_id"):
        dep_bank_name = voucher.get("deposit_bank_name") or SYS_DEFAULT_BANK
        sys_ledgers = ensure_system_ledgers()
        bank_led_id, bank_led_name = ensure_system_ledger(dep_bank_name, "Bank Accounts", "ASSETS")
        cheque_led_id, cheque_led_name = sys_ledgers["cheque"]

        c_v_number, c_txn = generate_voucher_number(
            voucher_type="Contra",
            voucher_mode="Bank",
            voucher_date=clearance_dt,
        )
        amt = float(voucher.get("amount", 0.0))
        contra_doc = {
            "voucher_number": c_v_number,
            "voucher_type": "Contra",
            "voucher_mode": "Bank",
            "txn": c_txn,
            "cheque_receipt_voucher_id": v_oid,
            "cheque_receipt_voucher_number": voucher.get("voucher_number"),
            "cheque_no": voucher.get("cheque_no"),
            "cheque_bank": voucher.get("cheque_bank"),
            "customer_id": voucher.get("customer_id"),
            "date": clearance_dt,
            "date_key": clearance_dt.strftime("%Y-%m-%d"),
            "narration": f"Cheque clearance #{voucher.get('cheque_no', '')} ({voucher.get('cheque_bank', '')}) into {bank_led_name}",
            "amount": amt,
            "entries": [
                {
                    "ledger_id": bank_led_id,
                    "ledger_name": bank_led_name,
                    "narration": f"Cheque cleared into {bank_led_name}",
                    "debit": amt,
                    "credit": 0.0,
                    "user_id": user_id,
                },
                {
                    "ledger_id": cheque_led_id,
                    "ledger_name": cheque_led_name,
                    "narration": f"Cheque cleared out of {cheque_led_name}",
                    "debit": 0.0,
                    "credit": amt,
                    "user_id": user_id,
                },
            ],
            "created_by": user_id,
            "created_at": now,
        }
        insert_kwargs = {"session": session} if session else {}
        vouchers_collection.insert_one(contra_doc, **insert_kwargs)
        update_data["clearance_contra_voucher_id"] = contra_doc["_id"]
        update_data["clearance_contra_voucher_number"] = c_v_number

    # For Finance / Loan receipts sitting in Finance Clearing Account:
    # Automatically disburse into Bank Account via disburse_finance_voucher
    if pm in ["FINANCE", "LOAN", "NBFC"] and not voucher.get("clearance_contra_voucher_id"):
        updated_v, contra_v = disburse_finance_voucher(
            voucher_id=voucher_id,
            user_id=user_id,
            bank_clearance_date=clearance_dt,
            notes=notes,
            session=session,
        )
        return updated_v

    update_kwargs = {"session": session} if session else {}
    vouchers_collection.update_one({"_id": v_oid}, {"$set": update_data}, **update_kwargs)
    return vouchers_collection.find_one({"_id": v_oid}, session=session)


def disburse_finance_voucher(
    voucher_id: str,
    user_id: str,
    disbursement_bank_name: Optional[str] = None,
    bank_clearance_date: Optional[datetime] = None,
    subvention_charges: float = 0.0,
    disbursement_utr: Optional[str] = None,
    notes: Optional[str] = None,
    session=None,
) -> Tuple[dict, dict]:
    """
    Records the final bank disbursement for a Finance / NBFC payment (e.g. Bajaj, TVS, HDB).
    Generates a Contra / Clearance Voucher:
      Dr Bank Account (Main): net_bank_amount (amount - subvention_charges)
      Dr Finance & Subvention Charges: subvention_charges (if > 0)
      Cr Finance Clearing Account: full financed amount
    Marks the receipt voucher as VERIFIED.
    """
    v_oid = to_oid(voucher_id)
    voucher = vouchers_collection.find_one({"_id": v_oid}, session=session)
    if not voucher:
        raise ValueError(f"Voucher not found: {voucher_id}")

    if voucher.get("voucher_type") != "Receipt":
        raise ValueError("Only Receipt Vouchers can be disbursed as finance")

    pm = (voucher.get("payment_mode") or "").upper()
    if pm not in ["FINANCE", "LOAN", "NBFC"]:
        raise ValueError(f"Voucher payment mode is '{pm}'. Only FINANCE / LOAN receipts can be disbursed.")

    cur_status = voucher.get("verification_status")
    if cur_status == "VERIFIED":
        raise ValueError("Finance payment is already disbursed and verified")
    if cur_status == "BOUNCED":
        raise ValueError("Cannot disburse a bounced / cancelled finance voucher")

    amount = float(voucher.get("amount", 0.0))
    subvention = round(max(0.0, float(subvention_charges or 0.0)), 2)
    if subvention >= amount:
        raise ValueError(f"Subvention charges (Rs.{subvention:.2f}) cannot equal or exceed the financed amount (Rs.{amount:.2f})")

    net_bank_amount = round(amount - subvention, 2)
    now = utc_now()
    clearance_dt = bank_clearance_date or now
    if isinstance(clearance_dt, str):
        try:
            clearance_dt = datetime.fromisoformat(clearance_dt)
        except Exception:
            pass

    sys_ledgers = ensure_system_ledgers()
    target_bank = disbursement_bank_name or SYS_DEFAULT_BANK
    bank_led_id, bank_led_name = ensure_system_ledger(target_bank, "Bank Accounts", "ASSETS")
    fin_clear_id, fin_clear_name = sys_ledgers["finance"]

    entries = [
        {
            "ledger_id": bank_led_id,
            "ledger_name": bank_led_name,
            "narration": f"Net finance disbursement into {bank_led_name}" + (f" (Ref: {disbursement_utr})" if disbursement_utr else ""),
            "debit": net_bank_amount,
            "credit": 0.0,
            "user_id": user_id,
        }
    ]

    if subvention > 0:
        subv_led_id, subv_led_name = sys_ledgers["subvention_charges"]
        entries.append({
            "ledger_id": subv_led_id,
            "ledger_name": subv_led_name,
            "narration": f"Subvention / MDR charges deducted by {voucher.get('financier_name') or 'Financier'} on {voucher.get('voucher_number')}",
            "debit": subvention,
            "credit": 0.0,
            "user_id": user_id,
        })

    entries.append({
        "ledger_id": fin_clear_id,
        "ledger_name": fin_clear_name,
        "narration": f"Clearing of financed receipt {voucher.get('voucher_number')} ({voucher.get('financier_name') or 'Financier'})",
        "debit": 0.0,
        "credit": amount,
        "user_id": user_id,
    })

    c_v_number, c_txn = generate_voucher_number(
        voucher_type="Contra",
        voucher_mode="Bank",
        voucher_date=clearance_dt,
    )

    fn_label = voucher.get("financier_name") or "Financier"
    ref_txt = f" Ref: {disbursement_utr}" if disbursement_utr else ""
    contra_doc = {
        "voucher_number": c_v_number,
        "voucher_type": "Contra",
        "voucher_mode": "Bank",
        "txn": c_txn,
        "finance_receipt_voucher_id": v_oid,
        "finance_receipt_voucher_number": voucher.get("voucher_number"),
        "financier_name": voucher.get("financier_name"),
        "customer_id": voucher.get("customer_id"),
        "date": clearance_dt,
        "date_key": clearance_dt.strftime("%Y-%m-%d"),
        "narration": f"Disbursement from {fn_label} into {bank_led_name}{ref_txt} (Net: Rs.{net_bank_amount}, Subvention: Rs.{subvention})",
        "amount": amount,
        "net_bank_amount": net_bank_amount,
        "subvention_charges": subvention,
        "disbursement_utr": disbursement_utr,
        "entries": entries,
        "created_by": user_id,
        "created_at": now,
    }

    insert_kwargs = {"session": session} if session else {}
    vouchers_collection.insert_one(contra_doc, **insert_kwargs)

    update_data = {
        "verification_status": "VERIFIED",
        "disbursement_status": "DISBURSED",
        "bank_clearance_date": clearance_dt,
        "subvention_charges": subvention,
        "net_disbursed_amount": net_bank_amount,
        "disbursement_utr": disbursement_utr,
        "disbursement_bank_name": bank_led_name,
        "clearance_contra_voucher_id": contra_doc["_id"],
        "clearance_contra_voucher_number": c_v_number,
        "verified_by": user_id,
        "verified_at": now,
        "updated_at": now,
    }
    if notes:
        existing_notes = voucher.get("notes") or ""
        update_data["notes"] = f"{existing_notes} | Disbursed: {notes}".strip(" | ")

    vouchers_collection.update_one({"_id": v_oid}, {"$set": update_data}, **insert_kwargs)
    updated_voucher = vouchers_collection.find_one({"_id": v_oid}, session=session)

    return updated_voucher, contra_doc


def batch_disburse_finance_vouchers(
    voucher_ids: List[str],
    user_id: str,
    disbursement_bank_name: Optional[str] = None,
    bank_clearance_date: Optional[datetime] = None,
    total_subvention_charges: float = 0.0,
    disbursement_utr: Optional[str] = None,
    notes: Optional[str] = None,
    session=None,
) -> Tuple[List[dict], dict, dict]:
    """
    Settles MULTIPLE financed order receipt vouchers in ONE single lump-sum batch payout.
    Creates ONE master Contra Voucher matching the single bank statement line item:
      Dr Bank Account (Main): total net amount received (Gross - Total Subvention)
      Dr Finance & Subvention Charges: total subvention/MDR deducted (if > 0)
      Cr Finance Clearing Account: total gross amount across all vouchers
    Calculates pro-rata subvention and net payout per voucher.
    Marks all selected receipt vouchers as VERIFIED & DISBURSED, linked to the Master Contra.
    """
    clean_ids = list(dict.fromkeys([str(vid).strip() for vid in voucher_ids if str(vid).strip()]))
    if not clean_ids:
        raise ValueError("At least one voucher ID is required for batch disbursement")

    oids = [to_oid(vid) for vid in clean_ids if to_oid(vid)]
    vouchers = list(vouchers_collection.find({"_id": {"$in": oids}}, session=session))
    if len(vouchers) != len(clean_ids):
        raise ValueError(f"Found {len(vouchers)} of {len(clean_ids)} vouchers. Some vouchers do not exist.")

    # Validate each voucher
    for v in vouchers:
        v_num = v.get("voucher_number", str(v.get("_id")))
        if v.get("voucher_type") != "Receipt":
            raise ValueError(f"Voucher {v_num} is not a Receipt voucher")
        pm = (v.get("payment_mode") or "").upper()
        if pm not in ["FINANCE", "LOAN", "NBFC"]:
            raise ValueError(f"Voucher {v_num} has payment mode '{pm}'. Only FINANCE vouchers can be batch disbursed.")
        if v.get("verification_status") == "VERIFIED":
            raise ValueError(f"Voucher {v_num} is already disbursed and verified")
        if v.get("verification_status") == "BOUNCED":
            raise ValueError(f"Voucher {v_num} is marked as BOUNCED / cancelled")

    total_gross = round(sum(float(v.get("amount", 0.0)) for v in vouchers), 2)
    total_subv = round(max(0.0, float(total_subvention_charges or 0.0)), 2)
    if total_subv >= total_gross:
        raise ValueError(f"Total subvention charges (Rs.{total_subv:.2f}) cannot equal or exceed total gross amount (Rs.{total_gross:.2f})")

    net_bank_amount = round(total_gross - total_subv, 2)
    now = utc_now()
    clearance_dt = bank_clearance_date or now
    if isinstance(clearance_dt, str):
        try:
            clearance_dt = datetime.fromisoformat(clearance_dt.replace("Z", "+00:00"))
        except Exception:
            for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y"):
                try:
                    clearance_dt = datetime.strptime(clearance_dt, fmt)
                    break
                except Exception:
                    pass

    # Pro-rata subvention breakdown per voucher
    allocated_subv_sum = 0.0
    voucher_breakdowns = []
    for idx, v in enumerate(vouchers):
        amt = float(v.get("amount", 0.0))
        if total_subv > 0 and total_gross > 0:
            if idx == len(vouchers) - 1:
                v_subv = round(total_subv - allocated_subv_sum, 2)
            else:
                v_subv = round((amt / total_gross) * total_subv, 2)
                allocated_subv_sum = round(allocated_subv_sum + v_subv, 2)
        else:
            v_subv = 0.0
        v_net = round(amt - v_subv, 2)
        voucher_breakdowns.append({
            "voucher": v,
            "gross": amt,
            "subvention": v_subv,
            "net": v_net,
        })

    target_bank = disbursement_bank_name or SYS_DEFAULT_BANK
    sys_ledgers = ensure_system_ledgers()
    bank_led_id, bank_led_name = ensure_system_ledger(target_bank, "Bank Accounts", "ASSETS")
    fin_clear_id, fin_clear_name = sys_ledgers["finance"]

    ref_str = f" (UTR: {disbursement_utr})" if disbursement_utr else ""
    entries = [
        {
            "ledger_id": bank_led_id,
            "ledger_name": bank_led_name,
            "narration": f"Lump-sum batch finance payout for {len(vouchers)} vouchers into {bank_led_name}{ref_str}",
            "debit": net_bank_amount,
            "credit": 0.0,
            "user_id": user_id,
        }
    ]

    if total_subv > 0:
        subv_led_id, subv_led_name = sys_ledgers["subvention_charges"]
        entries.append({
            "ledger_id": subv_led_id,
            "ledger_name": subv_led_name,
            "narration": f"Total MDR/Subvention charges deducted on batch payout of {len(vouchers)} vouchers",
            "debit": total_subv,
            "credit": 0.0,
            "user_id": user_id,
        })

    entries.append({
        "ledger_id": fin_clear_id,
        "ledger_name": fin_clear_name,
        "narration": f"Batch clearing of {len(vouchers)} financed receipts into {bank_led_name}",
        "debit": 0.0,
        "credit": total_gross,
        "user_id": user_id,
    })

    c_v_number, c_txn = generate_voucher_number(
        voucher_type="Contra",
        voucher_mode="Bank",
        voucher_date=clearance_dt,
    )

    financiers = sorted(list(set([v.get("financier_name") for v in vouchers if v.get("financier_name")])))
    fn_label = ", ".join(financiers) if financiers else "Financier"

    contra_doc = {
        "voucher_number": c_v_number,
        "voucher_type": "Contra",
        "voucher_mode": "Bank",
        "txn": c_txn,
        "batch_settlement": True,
        "settled_vouchers_count": len(vouchers),
        "settled_receipt_voucher_ids": [v["_id"] for v in vouchers],
        "settled_receipt_voucher_numbers": [v.get("voucher_number") for v in vouchers],
        "financier_names": financiers,
        "date": clearance_dt,
        "date_key": clearance_dt.strftime("%Y-%m-%d"),
        "narration": f"Lump-sum batch payout from {fn_label} for {len(vouchers)} orders into {bank_led_name}{ref_str} (Gross: Rs.{total_gross:.2f}, Subvention: Rs.{total_subv:.2f}, Net: Rs.{net_bank_amount:.2f})",
        "amount": total_gross,
        "net_bank_amount": net_bank_amount,
        "subvention_charges": total_subv,
        "disbursement_utr": disbursement_utr,
        "entries": entries,
        "created_by": user_id,
        "created_at": now,
    }

    insert_kwargs = {"session": session} if session else {}
    vouchers_collection.insert_one(contra_doc, **insert_kwargs)

    updated_vouchers = []
    for item in voucher_breakdowns:
        v = item["voucher"]
        v_oid = v["_id"]
        upd = {
            "verification_status": "VERIFIED",
            "disbursement_status": "DISBURSED",
            "bank_clearance_date": clearance_dt,
            "subvention_charges": item["subvention"],
            "net_disbursed_amount": item["net"],
            "disbursement_utr": disbursement_utr,
            "disbursement_bank_name": bank_led_name,
            "clearance_contra_voucher_id": contra_doc["_id"],
            "clearance_contra_voucher_number": c_v_number,
            "batch_settlement": True,
            "verified_by": user_id,
            "verified_at": now,
            "updated_at": now,
        }
        if notes:
            ex_notes = v.get("notes") or ""
            upd["notes"] = f"{ex_notes} | Batch Payout: {notes}".strip(" | ")
        vouchers_collection.update_one({"_id": v_oid}, {"$set": upd}, **insert_kwargs)
        upd_v = vouchers_collection.find_one({"_id": v_oid}, session=session)
        updated_vouchers.append(upd_v)

    summary = {
        "batch_settled_count": len(vouchers),
        "total_gross_amount": total_gross,
        "total_subvention_charges": total_subv,
        "net_bank_amount": net_bank_amount,
        "disbursement_bank_name": bank_led_name,
        "disbursement_utr": disbursement_utr,
        "clearance_contra_voucher_number": c_v_number,
        "clearance_contra_voucher_id": contra_doc["_id"],
        "breakdown": [
            {
                "voucher_number": item["voucher"].get("voucher_number"),
                "gross_amount": item["gross"],
                "subvention_share": item["subvention"],
                "net_disbursed": item["net"],
            }
            for item in voucher_breakdowns
        ]
    }

    return updated_vouchers, contra_doc, summary


def bounce_receipt_voucher(
    voucher_id: str,
    user_id: str,
    bounce_reason: str,
    penalty_amount: float = 0.0,
    session=None,
) -> Tuple[dict, dict]:
    """
    Handles a Dishonored / Bounced UPI / Cheque / Bank receipt.
    1. Creates a Reversal Journal Voucher:
       Dr Customer Ledger (re-creates customer debt + penalty)
       Cr Bank / Asset Ledger (removes uncredited receipt funds)
       Cr Cheque Bounce Charges Income (if penalty_amount > 0)
    2. Restores Order(s) pending_amount, applies penalty, and updates status back to PARTIALLY_PAID / UNPAID.
    3. Tags original voucher as BOUNCED with reason and penalty for full audit trail.
    """
    v_oid = to_oid(voucher_id)
    voucher = vouchers_collection.find_one({"_id": v_oid}, session=session)
    if not voucher:
        raise ValueError(f"Voucher not found: {voucher_id}")

    if voucher.get("voucher_type") != "Receipt":
        raise ValueError("Only Receipt Vouchers can be marked as bounced / dishonored")

    if voucher.get("verification_status") == "BOUNCED":
        raise ValueError("This voucher is already marked as bounced / dishonored")

    if not bounce_reason or len(bounce_reason.strip()) < 3:
        raise ValueError("A clear reason for bounce/dishonor is required (minimum 3 characters)")

    now = utc_now()
    amount = float(voucher.get("amount", 0.0))
    penalty = round(max(0.0, float(penalty_amount or 0.0)), 2)
    voucher_number = voucher.get("voucher_number", "")
    sys_ledgers = ensure_system_ledgers()

    # Identify Customer and Bank/Asset ledgers from original entries
    entries = voucher.get("entries", [])
    asset_entry = next((e for e in entries if float(e.get("debit", 0.0)) > 0), None)
    cust_entry = next((e for e in entries if float(e.get("credit", 0.0)) > 0), None)

    if not asset_entry or not cust_entry:
        raise ValueError("Could not parse debit/credit ledgers from the receipt voucher to reverse")

    total_cust_debit = round(amount + penalty, 2)
    penalty_text = f" (Includes bounce penalty Rs.{penalty:.2f})" if penalty > 0 else ""

    # Determine asset ledger to credit on reversal:
    # If the payment was already cleared into bank via contra, credit the bank account.
    # Otherwise, credit the original asset ledger (Cheques in Hand / Finance Clearing Account).
    if voucher.get("clearance_contra_voucher_id"):
        dep_bank_name = voucher.get("deposit_bank_name") or voucher.get("disbursement_bank_name") or SYS_DEFAULT_BANK
        bank_led_id, bank_led_name = ensure_system_ledger(dep_bank_name, "Bank Accounts", "ASSETS")
        rev_asset_ledger_id = bank_led_id
        rev_asset_ledger_name = bank_led_name
    else:
        rev_asset_ledger_id = asset_entry["ledger_id"]
        rev_asset_ledger_name = asset_entry["ledger_name"]

    rev_entries = [
        {
            "ledger_id": cust_entry["ledger_id"],
            "ledger_name": cust_entry["ledger_name"],
            "narration": f"Reversal of {voucher_number} (Dishonored/Bounced: {bounce_reason}){penalty_text}",
            "debit": total_cust_debit,
            "credit": 0.0,
            "user_id": user_id,
        },
        {
            "ledger_id": rev_asset_ledger_id,
            "ledger_name": rev_asset_ledger_name,
            "narration": f"Reversal of {voucher_number} (Funds not credited: {bounce_reason})",
            "debit": 0.0,
            "credit": amount,
            "user_id": user_id,
        }
    ]

    if penalty > 0:
        bounce_ledger_id, bounce_ledger_name = sys_ledgers["bounce_charges"]
        rev_entries.append({
            "ledger_id": bounce_ledger_id,
            "ledger_name": bounce_ledger_name,
            "narration": f"Bounce penalty levied on {cust_entry['ledger_name']} for {voucher_number}",
            "debit": 0.0,
            "credit": penalty,
            "user_id": user_id,
        })

    rev_v_number, rev_txn = generate_voucher_number(
        voucher_type="Journal",
        voucher_mode="Reversal",
        voucher_date=now,
    )

    reversal_voucher_doc = {
        "voucher_number": rev_v_number,
        "voucher_type": "Journal",
        "voucher_mode": "Reversal",
        "txn": rev_txn,
        "original_voucher_id": v_oid,
        "original_voucher_number": voucher_number,
        "customer_id": voucher.get("customer_id"),
        "order_id": voucher.get("order_id"),
        "affected_order_ids": voucher.get("affected_order_ids", []),
        "date": now,
        "date_key": now.strftime("%Y-%m-%d"),
        "narration": f"Dishonor/Bounce Reversal of Receipt {voucher_number}: {bounce_reason}{penalty_text}",
        "amount": total_cust_debit,
        "reversal_amount": amount,
        "penalty_amount": penalty,
        "entries": rev_entries,
        "bounce_reason": bounce_reason,
        "created_by": user_id,
        "created_at": now,
    }

    insert_kwargs = {"session": session} if session else {}
    vouchers_collection.insert_one(reversal_voucher_doc, **insert_kwargs)

    # 2. Restore Order(s) pending amounts
    # Case A: Single-order receipt
    if voucher.get("order_id"):
        ord_oid = to_oid(voucher["order_id"])
        ord_doc = orders_collection.find_one({"_id": ord_oid}, session=session)
        if ord_doc:
            cur_bill = round(float(ord_doc.get("bill_amount", ord_doc.get("grand_total", 0.0))), 2)
            cur_paid = round(float(ord_doc.get("paid_amount", 0.0)), 2)
            new_bill = round(cur_bill + penalty, 2)
            new_paid = round(max(0.0, cur_paid - amount), 2)
            new_pending = round(max(0.0, new_bill - new_paid), 2)
            new_status = "PAID" if new_pending <= 0.01 else ("PARTIALLY_PAID" if new_paid > 0.01 else "UNPAID")

            update_kwargs = {"session": session} if session else {}
            orders_collection.update_one(
                {"_id": ord_oid},
                {
                    "$set": {
                        "bill_amount": new_bill,
                        "bounce_penalty_amount": round(float(ord_doc.get("bounce_penalty_amount", 0.0)) + penalty, 2),
                        "paid_amount": new_paid,
                        "pending_amount": new_pending,
                        "payment_status": new_status,
                        "updated_at": now,
                    }
                },
                **update_kwargs
            )

    # Case B: Multi-order FIFO lump-sum receipt
    elif voucher.get("bill_allocations"):
        penalty_assigned = False
        for alloc in voucher["bill_allocations"]:
            alloc_ord_id = to_oid(alloc.get("order_id"))
            alloc_amount = round(float(alloc.get("allocated_amount", 0.0)), 2)
            if not alloc_ord_id or alloc_amount <= 0:
                continue

            ord_doc = orders_collection.find_one({"_id": alloc_ord_id}, session=session)
            if ord_doc:
                cur_bill = round(float(ord_doc.get("bill_amount", ord_doc.get("grand_total", 0.0))), 2)
                cur_paid = round(float(ord_doc.get("paid_amount", 0.0)), 2)
                ord_penalty = penalty if (not penalty_assigned and penalty > 0) else 0.0
                penalty_assigned = True

                new_bill = round(cur_bill + ord_penalty, 2)
                new_paid = round(max(0.0, cur_paid - alloc_amount), 2)
                new_pending = round(max(0.0, new_bill - new_paid), 2)
                new_status = "PAID" if new_pending <= 0.01 else ("PARTIALLY_PAID" if new_paid > 0.01 else "UNPAID")

                update_fields = {
                    "bill_amount": new_bill,
                    "paid_amount": new_paid,
                    "pending_amount": new_pending,
                    "payment_status": new_status,
                    "updated_at": now,
                }
                if ord_penalty > 0:
                    update_fields["bounce_penalty_amount"] = round(float(ord_doc.get("bounce_penalty_amount", 0.0)) + ord_penalty, 2)

                update_kwargs = {"session": session} if session else {}
                orders_collection.update_one(
                    {"_id": alloc_ord_id},
                    {"$set": update_fields},
                    **update_kwargs
                )

    # 3. Update original voucher as BOUNCED
    update_kwargs = {"session": session} if session else {}
    vouchers_collection.update_one(
        {"_id": v_oid},
        {
            "$set": {
                "verification_status": "BOUNCED",
                "bounce_reason": bounce_reason,
                "penalty_amount": penalty,
                "bounced_by": user_id,
                "bounced_at": now,
                "reversal_voucher_id": reversal_voucher_doc["_id"],
                "reversal_voucher_number": rev_v_number,
                "updated_at": now,
            }
        },
        **update_kwargs
    )

    updated_voucher = vouchers_collection.find_one({"_id": v_oid}, session=session)
    return updated_voucher, reversal_voucher_doc

