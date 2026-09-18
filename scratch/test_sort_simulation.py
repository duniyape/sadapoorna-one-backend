import os
import sys
sys.path.insert(0, os.path.abspath("."))
from database import vouchers_collection
from datetime import datetime

q = {'entries.ledger_id': '6aa2a18e1ddfc61c761006b3'}
v_list = list(vouchers_collection.find(q))

def eff_dt(v):
    d = v.get('date')
    c = v.get('created_at') or d
    if isinstance(d, datetime):
        if d.hour == 0 and d.minute == 0 and d.second == 0 and d.microsecond == 0:
            if isinstance(c, datetime) and c.date() == d.date():
                return c
            # For backdated entries without time, place receipts at end of day
            if v.get("voucher_type") in ["Receipt", "Payment"]:
                return d.replace(hour=23, minute=59, second=59)
        return d
    return c

# Type priority as a tie-breaker when timestamps are identical:
# 10: Sales (Billing)
# 20: Receipts
# 30: Reversals
def tie_priority(v):
    if v.get("voucher_mode") == "Reversal":
        return 30
    v_t = (v.get("voucher_type") or "").upper()
    if v_t in ["SALES", "PURCHASE_RETURN", "DEBIT_NOTE"]:
        return 10
    if v_t in ["RECEIPT", "PAYMENT"]:
        return 20
    return 25

v_list.sort(key=lambda v: (eff_dt(v), tie_priority(v), v.get('created_at') or eff_dt(v)))

bal = 0.0
for v in v_list:
    deb = sum(e.get('debit', 0.0) for e in v.get('entries', []) if e.get('ledger_id') == '6aa2a18e1ddfc61c761006b3')
    cred = sum(e.get('credit', 0.0) for e in v.get('entries', []) if e.get('ledger_id') == '6aa2a18e1ddfc61c761006b3')
    bal += (deb - cred)
    b_type = 'Dr' if bal >= 0 else 'Cr'
    print(f"{v.get('voucher_number'):<20} {v.get('voucher_type'):<10} {str(v.get('date')):<28} Dr:{deb:<8} Cr:{cred:<8} Bal:{abs(bal):<8} {b_type}")
