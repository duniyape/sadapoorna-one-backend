import json

def analyze():
    with open('scratch/firebase_database.json', 'r', encoding='utf-8') as f:
        data = json.load(f)

    ledgers = data.get('ledgers', {})
    purchases = data.get('Purchase', {})

    # Calculate from ledgers
    ledger_balances = {}
    for key, l in ledgers.items():
        cid = l.get('customer_id')
        if not cid: continue
        
        def safe_float(val):
            try: return float(val) if val else 0.0
            except ValueError: return 0.0

        # sum dr
        dr_sum = sum(safe_float(d.get('amt', 0)) for d in l.get('dr', []))
        # sum cr
        cr_sum = sum(safe_float(c.get('amt', 0)) for c in l.get('cr', []))
        
        ledger_balances[cid] = ledger_balances.get(cid, 0) + (dr_sum - cr_sum)

    # Calculate from purchases
    purchase_balances = {}
    for key, p in purchases.items():
        cid = p.get('contact')
        if not cid: continue
        
        bal = float(p.get('balance', 0))
        purchase_balances[cid] = purchase_balances.get(cid, 0) + bal

    print(f"Ledger balances calculated for {len(ledger_balances)} customers")
    print(f"Purchase balances calculated for {len(purchase_balances)} customers")
    
    # Let's print a few
    count = 0
    for cid in list(ledger_balances.keys())[:5]:
        print(f"Customer {cid}: Ledger Bal = {ledger_balances.get(cid, 0)}, Purchase Bal = {purchase_balances.get(cid, 0)}")

if __name__ == "__main__":
    analyze()
